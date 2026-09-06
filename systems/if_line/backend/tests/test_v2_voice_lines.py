from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.application.storage_service import LocalStorageBackend
from app.application.voice_line_service import (
    TTSRenderOutput,
    build_tts_cache_key,
    prepare_render_request,
)
from app.database import Base, get_db
from app.models import User
from app.models_v2 import (
    AssetVersion,
    ChapterRevision,
    GenerationTask,
    OutlineRevision,
    ProviderUsageRecord,
    StorageObject,
    StoryBibleRevision,
    VoiceLine,
)
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers.v2 import voice_lines as voice_router
from app.workers import voice_tasks as voice_tasks_module
from app.workers.voice_tasks import execute_tts_render_task, execute_voice_slice_task


@pytest.fixture
def voice_app(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_tasks_module.tts_module, "TTS_ENABLED", True)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionMaker = sessionmaker(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = SessionMaker()
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(projects_router.router, prefix="/api/projects")
    app.include_router(voice_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield app, db, SessionMaker, LocalStorageBackend(tmp_path / "voice-objects")
    finally:
        db.close()
        engine.dispose()


def _client(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert response.status_code == 201
    return client


def _seed_chapter(app: FastAPI, db):
    email = "voice-v2-owner@example.com"
    owner = _client(app, email)
    other = _client(app, "voice-v2-other@example.com")
    project = owner.post(
        "/api/projects/",
        json={
            "title": "voice v2",
            "characters": ["林夜"],
            "story_start": "开端",
            "story_end": "结局",
            "style": "现代",
            "pace": "medium",
        },
    ).json()
    owner_id = db.query(User).filter(User.email == email).one().id
    bible = StoryBibleRevision(
        project_id=project["id"],
        revision_no=1,
        source_hash="a" * 64,
        content_hash="b" * 64,
        content_json={"characters": [{"name": "林夜", "gender": "male"}]},
        status="complete",
        created_by=owner_id,
    )
    db.add(bible)
    db.flush()
    outline = OutlineRevision(
        project_id=project["id"],
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash="c" * 64,
        content_hash="d" * 64,
        status="approved",
        created_by=owner_id,
    )
    db.add(outline)
    db.flush()
    chapter = ChapterRevision(
        project_id=project["id"],
        chapter_index=1,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=1,
        source_hash="e" * 64,
        content_hash="f" * 64,
        content='林夜说：“好。”\n林夜又说：“好。”\n夜色渐深。',
        status="complete",
        created_by=owner_id,
    )
    db.add(chapter)
    db.commit()
    return owner, other, project["id"], chapter


def _generate(owner: TestClient, project_id: int, chapter_id: str):
    response = owner.post(
        f"/api/projects/{project_id}/chapter-revisions/{chapter_id}/voice-lines/generations",
        json={"render_audio": True},
        headers={"Idempotency-Key": "voice-slice-1"},
    )
    assert response.status_code == 202
    return response.json()


def test_slice_creates_distinct_occurrences_child_tasks_and_ordered_manifest(voice_app):
    app, db, SessionMaker, _ = voice_app
    owner, other, project_id, chapter = _seed_chapter(app, db)
    accepted = _generate(owner, project_id, chapter.id)

    replay = _generate(owner, project_id, chapter.id)
    assert replay["task_id"] == accepted["task_id"]
    assert replay["created"] is False
    execute_voice_slice_task(accepted["task_id"], session_factory=SessionMaker)
    db.expire_all()

    parent = db.query(GenerationTask).filter(GenerationTask.id == accepted["task_id"]).one()
    lines = db.query(VoiceLine).filter(VoiceLine.chapter_revision_id == chapter.id).order_by(VoiceLine.order_index).all()
    children = db.query(GenerationTask).filter(GenerationTask.parent_task_id == parent.id).all()
    assert parent.status == "succeeded"
    assert len(lines) >= 3
    assert len(children) == len(lines)
    assert {task.kind for task in children} == {"tts.render"}
    assert all(task.root_task_id == parent.id for task in children)
    assert all(task.estimated_cost > 0 for task in children)

    duplicate_lines = [line for line in lines if line.text == "好。"]
    assert len(duplicate_lines) == 2
    assert duplicate_lines[0].occurrence_id != duplicate_lines[1].occurrence_id
    assert duplicate_lines[0].order_index != duplicate_lines[1].order_index

    manifest = owner.get(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines"
    )
    assert manifest.status_code == 200
    items = manifest.json()["items"]
    assert [item["order_index"] for item in items] == list(range(len(items)))
    assert other.get(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines"
    ).status_code == 403
    assert other.post(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines/generations",
        json={"render_audio": True},
        headers={"Idempotency-Key": "cross-user-generation"},
    ).status_code == 403
    assert owner.post(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines/generations",
        json={"render_audio": True, "estimated_cost": 0},
        headers={"Idempotency-Key": "client-cost-override"},
    ).status_code == 422


@pytest.mark.asyncio
async def test_per_line_render_failure_and_manual_retry_do_not_block_other_lines(voice_app):
    app, db, SessionMaker, backend = voice_app
    owner, other, project_id, chapter = _seed_chapter(app, db)
    accepted = _generate(owner, project_id, chapter.id)
    execute_voice_slice_task(accepted["task_id"], session_factory=SessionMaker)
    db.expire_all()
    lines = db.query(VoiceLine).filter(VoiceLine.chapter_revision_id == chapter.id).order_by(VoiceLine.order_index).all()
    tasks = {
        task.source_refs["voice_line_id"]: task
        for task in db.query(GenerationTask).filter(GenerationTask.parent_task_id == accepted["task_id"]).all()
    }

    class SuccessfulAdapter:
        def __init__(self):
            self.calls = []

        async def render(self, request):
            self.calls.append(request)
            return TTSRenderOutput(
                audio_bytes=b"fake-mp3-audio",
                media_type="audio/mpeg",
                provider="mock-tts",
                model=request.model,
                actual_cost=Decimal("0.10"),
                provider_request_id=f"request-{request.voice_line_id}",
                audio_seconds=Decimal("1.25"),
            )

    class FailedAdapter:
        async def render(self, request):
            raise RuntimeError("raw provider secret must not escape")

    success_adapter = SuccessfulAdapter()
    first, second = lines[0], lines[1]
    await execute_tts_render_task(
        tasks[first.id].id,
        adapter=success_adapter,
        session_factory=SessionMaker,
        backend=backend,
    )
    await execute_tts_render_task(
        tasks[second.id].id,
        adapter=FailedAdapter(),
        session_factory=SessionMaker,
        backend=backend,
    )
    db.expire_all()

    first = db.query(VoiceLine).filter(VoiceLine.id == first.id).one()
    second = db.query(VoiceLine).filter(VoiceLine.id == second.id).one()
    assert first.status == "ready"
    assert first.audio_asset_version_id is not None
    assert second.status == "failed"
    assert db.query(GenerationTask).filter(GenerationTask.id == tasks[first.id].id).one().status == "succeeded"
    failed_task = db.query(GenerationTask).filter(GenerationTask.id == tasks[second.id].id).one()
    assert failed_task.status == "failed"
    # P3.2 修复后：provider 的真实 class+message 经过 sanitize_detail 后落进
    # error_detail（之前被替换成固定中文串，用户看不到失败原因）。
    assert "RuntimeError" in failed_task.error_detail
    assert "raw provider" in failed_task.error_detail
    # 但任何 token-shaped / Bearer / IP / 邮箱形状都不该原样落库 —— sanitize_detail
    # 已经把 sk-***、Bearer ***、<ip>、<email> 都掩盖掉。
    from app.services.generation_report import sanitize_detail
    sanitized = sanitize_detail("Bearer sk-abc123456789012345 from 10.0.0.5")
    assert "sk-abc123456789012345" not in sanitized  # 证明 sanitizer 生效，token 被掩盖
    assert "sk-***" in sanitized
    assert failed_task.error_detail != "单条语音合成失败，可单独重试"  # 旧固定串已废弃
    assert db.query(AssetVersion).count() == 1
    assert db.query(StorageObject).count() == 1
    assert db.query(ProviderUsageRecord).count() == 1

    assert other.post(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines/{second.id}/retry",
        headers={"Idempotency-Key": "cross-user-retry"},
    ).status_code == 403

    retry = owner.post(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines/{second.id}/retry",
        headers={"Idempotency-Key": "retry-one-line"},
    )
    assert retry.status_code == 202
    assert retry.json()["created"] is True
    await execute_tts_render_task(
        retry.json()["task_id"],
        adapter=success_adapter,
        session_factory=SessionMaker,
        backend=backend,
    )
    db.expire_all()
    assert db.query(VoiceLine).filter(VoiceLine.id == second.id).one().status == "ready"
    assert db.query(AssetVersion).count() == 2
    assert db.query(StorageObject).count() == 2

    manifest = owner.get(
        f"/api/projects/{project_id}/chapter-revisions/{chapter.id}/voice-lines"
    ).json()
    assert manifest["count"] == len(lines)
    assert [item["order_index"] for item in manifest["items"]] == list(range(len(lines)))


@pytest.mark.asyncio
async def test_disabled_tts_worker_fails_task_without_calling_adapter(voice_app, monkeypatch):
    app, db, SessionMaker, backend = voice_app
    owner, _, project_id, chapter = _seed_chapter(app, db)
    accepted = _generate(owner, project_id, chapter.id)
    execute_voice_slice_task(accepted["task_id"], session_factory=SessionMaker)
    db.expire_all()
    task = (
        db.query(GenerationTask)
        .filter(GenerationTask.parent_task_id == accepted["task_id"])
        .order_by(GenerationTask.created_at)
        .first()
    )
    line_id = task.source_refs["voice_line_id"]

    class RecordingAdapter:
        def __init__(self):
            self.calls = 0

        async def render(self, request):
            self.calls += 1
            raise AssertionError("disabled TTS must not invoke a provider adapter")

    adapter = RecordingAdapter()
    monkeypatch.setattr(voice_tasks_module.tts_module, "TTS_ENABLED", False)
    await execute_tts_render_task(
        task.id,
        adapter=adapter,
        session_factory=SessionMaker,
        backend=backend,
    )
    db.expire_all()

    failed_task = db.query(GenerationTask).filter(GenerationTask.id == task.id).one()
    failed_line = db.query(VoiceLine).filter(VoiceLine.id == line_id).one()
    assert adapter.calls == 0
    assert failed_task.status == "failed"
    assert failed_task.error_code == "tts.feature_disabled"
    assert failed_line.status == "failed"


def test_tts_cache_key_covers_occurrence_and_every_render_parameter(voice_app):
    app, db, SessionMaker, _ = voice_app
    owner, _, project_id, chapter = _seed_chapter(app, db)
    accepted = _generate(owner, project_id, chapter.id)
    execute_voice_slice_task(accepted["task_id"], session_factory=SessionMaker)
    db.expire_all()
    tasks = db.query(GenerationTask).filter(GenerationTask.parent_task_id == accepted["task_id"]).all()
    requests = [prepare_render_request(db, task)[2] for task in tasks]
    assert len({request.cache_key for request in requests}) == len(requests)

    baseline = requests[0]
    for changed in (
        replace(baseline, speed=baseline.speed + 1),
        replace(baseline, pitch=baseline.pitch + 1),
        replace(baseline, volume=baseline.volume + 1),
        replace(baseline, emotion="angry"),
        replace(baseline, model=baseline.model + "-v2"),
        replace(baseline, audio_format="wav"),
        replace(baseline, sample_rate=16000),
        replace(baseline, language_type="English"),
        replace(baseline, prompt_version="voice-line-v2"),
    ):
        assert build_tts_cache_key(changed) != build_tts_cache_key(baseline)
