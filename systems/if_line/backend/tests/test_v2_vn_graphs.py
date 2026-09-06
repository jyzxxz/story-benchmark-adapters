from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.hashing import content_hash
from app.application.chapter_script_service import ensure_backfilled_script_revision
from app.application.task_service import claim_task
from app.application.vn_graph_service import (
    activate_vn_graph_head,
    create_vn_graph_compile_task,
    execute_vn_graph_compile_task,
    get_vn_graph_head,
    load_compile_input,
    persist_vn_graph_result,
)
from app.database import Base
from app.models import Asset, Project, User
from app.models_v2 import (
    AssetBinding,
    AssetVersion,
    ChapterRevision,
    ChapterScriptRevision,
    GenerationTask,
    OutboxEvent,
    OutlineRevision,
    StorageObject,
    StoryBibleRevision,
    TaskEvent,
    UsageReservation,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
    VoiceLineVersion,
)
from app.core.errors import AppError
from app.services.vn_graph_compiler import (
    VNGRAPH_COMPILER_VERSION,
    VNGRAPH_SCHEMA_VERSION,
    VNGRAPH_TACHI_POLICY_VERSION,
    VNGraphCompileError,
    VNGraphCompileInput,
    vn_graph_compiler,
)
from app.workers import tasks as worker_tasks


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    # Match SessionLocal: production disables autoflush, which is important
    # for task-event sequence allocation inside one compile transaction.
    Session = sessionmaker(bind=engine, autoflush=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(db):
    user = User(
        email="vngraph@example.com",
        password_hash="hash",
        display_name="VNGraph owner",
        is_active=True,
        quota_total=100,
        quota_daily=100,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="确定性故事",
        story_start="开始",
        story_end="结局",
        visibility="private",
        is_draft=True,
    )
    db.add(project)
    db.flush()

    bible_content = {"characters": [{"name": "阿宁"}]}
    bible = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash=content_hash({"origin": "vngraph-test"}),
        content_hash=content_hash(bible_content),
        content_json=bible_content,
        status="complete",
        created_by=user.id,
    )
    db.add(bible)
    db.flush()
    outline = OutlineRevision(
        project_id=project.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash=content_hash({"bible_revision_id": bible.id}),
        content_hash=content_hash({"chapters": []}),
        status="approved",
        created_by=user.id,
    )
    db.add(outline)
    db.flush()

    chapter_text = "阿宁说了两次同样的话，然后夜色沉了下来。"
    chapter = ChapterRevision(
        project_id=project.id,
        chapter_index=1,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=1,
        source_hash="1" * 64,
        content_hash=content_hash(chapter_text),
        content=chapter_text,
        status="complete",
        created_by=user.id,
    )
    db.add(chapter)
    db.flush()
    script = ensure_backfilled_script_revision(db, chapter=chapter, activate=True)

    character_id = hashlib.md5(f"阿宁|{project.id}".encode("utf-8")).hexdigest()[:12]
    # 单说话人舞台下立绘只在说话人段落入场：把回填的首段标注为阿宁的对白，
    # 否则全 narration 章节不再产出任何 Tachi 节点。
    script_ir = script.script_json if isinstance(script.script_json, dict) else json.loads(script.script_json)
    for para in script_ir["paragraphs"]:
        if para.get("paragraph_id") == "paragraph-0001":
            para["kind"] = "dialogue"
            para["speaker_character_id"] = character_id
            para["speaker_name"] = "阿宁"
    # ChapterScriptRevision 不可变，走 Core update 绕过 ORM 校验；script_hash 需同步
    db.execute(
        update(ChapterScriptRevision)
        .where(ChapterScriptRevision.id == script.id)
        .values(
            script_json=json.loads(json.dumps(script_ir, ensure_ascii=False)),
            script_hash=content_hash(script_ir),
        )
    )
    background_asset = Asset(
        project_id=project.id,
        chapter_index=1,
        asset_type="background",
        target_name="雨夜车站",
        prompt="rain station",
        image_url="/static/assets/legacy-background.png",
        status="completed",
    )
    portrait_asset = Asset(
        project_id=project.id,
        chapter_index=1,
        asset_type="portrait",
        target_name="阿宁",
        character_id=character_id,
        emotion="neutral",
        prompt="portrait",
        status="completed",
    )
    audio_asset = Asset(
        project_id=project.id,
        chapter_index=1,
        asset_type="voice_line",
        target_name="阿宁",
        prompt="走吧",
        status="completed",
    )
    db.add_all([background_asset, portrait_asset, audio_asset])
    db.flush()

    background_storage = StorageObject(
        owner_id=user.id,
        project_id=project.id,
        storage_backend="local",
        storage_key="images/background-v1.png",
        media_type="image/png",
        byte_size=10,
        sha256="2" * 64,
        visibility="private",
        status="active",
    )
    portrait_storage = StorageObject(
        owner_id=user.id,
        project_id=project.id,
        storage_backend="local",
        storage_key="images/portrait-v1.png",
        media_type="image/png",
        byte_size=10,
        sha256="3" * 64,
        visibility="private",
        status="active",
    )
    audio_storage = StorageObject(
        owner_id=user.id,
        project_id=project.id,
        storage_backend="local",
        storage_key="audio/line-v1.mp3",
        media_type="audio/mpeg",
        byte_size=10,
        sha256="4" * 64,
        visibility="private",
        status="active",
    )
    db.add_all([background_storage, portrait_storage, audio_storage])
    db.flush()

    background_version = AssetVersion(
        asset_id=background_asset.id,
        source_revision_id=chapter.id,
        storage_object_id=background_storage.id,
        version_no=1,
        cache_key="background-v1",
        prompt_hash="5" * 64,
        prompt_version="v1",
        safety_status="safe",
    )
    portrait_version = AssetVersion(
        asset_id=portrait_asset.id,
        source_revision_id=chapter.id,
        storage_object_id=portrait_storage.id,
        version_no=1,
        cache_key="portrait-v1",
        prompt_hash="6" * 64,
        prompt_version="v1",
        safety_status="safe",
    )
    audio_version = AssetVersion(
        asset_id=audio_asset.id,
        source_revision_id=chapter.id,
        storage_object_id=audio_storage.id,
        version_no=1,
        cache_key="audio-v1",
        prompt_hash="7" * 64,
        prompt_version="v1",
        safety_status="safe",
    )
    db.add_all([background_version, portrait_version, audio_version])
    db.flush()

    background_binding = AssetBinding(
        project_id=project.id,
        source_kind="chapter_revision",
        source_id=chapter.id,
        role="background",
        asset_version_id=background_version.id,
        order_index=0,
        required=True,
    )
    portrait_binding = AssetBinding(
        project_id=project.id,
        source_kind="chapter_revision",
        source_id=chapter.id,
        role="portrait",
        asset_version_id=portrait_version.id,
        order_index=1,
    )
    db.add_all([background_binding, portrait_binding])
    db.flush()

    first_line = VoiceLine(
        chapter_revision_id=chapter.id,
        occurrence_id="line-0001",
        order_index=0,
        kind="dialogue",
        text="走吧",
        speaker_character_id=character_id,
        speaker_name="阿宁",
        emotion="neutral",
        audio_asset_version_id=audio_version.id,
        status="ready",
    )
    second_line = VoiceLine(
        chapter_revision_id=chapter.id,
        occurrence_id="line-0002",
        order_index=1,
        kind="dialogue",
        text="走吧",
        speaker_character_id=character_id,
        speaker_name="阿宁",
        emotion="neutral",
        status="planned",
    )
    db.add_all([first_line, second_line])
    db.commit()
    return {
        "user": user,
        "project": project,
        "chapter": chapter,
        "script": script,
        "background_asset": background_asset,
        "background_binding": background_binding,
        "background_version": background_version,
        "background_storage": background_storage,
        "first_voice_line": first_line,
        "second_voice_line": second_line,
    }


def _paragraph_lines(graph):
    result = []
    for node in graph["Nodes"]:
        if node.get("NodeType") == 1 and node.get("SubType") == 2:
            result.extend(item["ObjectValue"] for item in node["Data"]["Lines"]["Items"])
    return result


def _string(data, key):
    return data[key].get("StringValue")


def _background_url(graph):
    node = next(
        item for item in graph["Nodes"] if item.get("NodeType") == 2 and item.get("SubType") == 3
    )
    return _string(node["Data"], "BackgroundImage")


def test_compiler_is_canonical_deterministic_and_occurrence_safe(db):
    seeded = _seed(db)
    source = load_compile_input(
        db,
        script_revision_id=seeded["script"].id,
    )

    first = vn_graph_compiler.compile(source)
    second = vn_graph_compiler.compile(source)
    reordered = vn_graph_compiler.compile(
        replace(
            source,
            asset_bindings=list(reversed(source.asset_bindings)),
            voice_line_versions=list(reversed(source.voice_line_versions)),
        )
    )

    assert first.graph == second.graph == reordered.graph
    assert first.graph_hash == second.graph_hash == reordered.graph_hash
    assert (
        first.binding_manifest_hash
        == second.binding_manifest_hash
        == reordered.binding_manifest_hash
    )
    assert len(first.graph_hash) == 64
    assert first.graph["Meta"]["compiler"] == {
        "schema_version": VNGRAPH_SCHEMA_VERSION,
        "compiler_version": VNGRAPH_COMPILER_VERSION,
        "tachi_policy_version": VNGRAPH_TACHI_POLICY_VERSION,
    }
    assert _background_url(first.graph) == f'/api/media/{seeded["background_storage"].id}'

    tachi = next(
        item for item in first.graph["Nodes"] if item.get("NodeType") == 2 and item.get("SubType") == 1
    )
    assert _string(tachi["Data"], "TachiIamge").startswith("/api/media/")

    lines = _paragraph_lines(first.graph)
    # Paragraph structure comes only from the source-covered Script IR. These
    # stale VoiceLines do not match the prose and therefore cannot replace it.
    assert [_string(line, "Text") for line in lines] == [seeded["chapter"].content]
    assert lines[0]["VoiceLineId"]["Kind"] == "Null"
    assert lines[0]["AudioUrl"]["Kind"] == "Null"
    assert first.graph["Meta"]["text_coverage"]["coverage_ratio"] == 1.0


def test_compiler_rejects_content_or_version_drift_and_hashes_manifest_changes(db):
    seeded = _seed(db)
    source = load_compile_input(
        db,
        script_revision_id=seeded["script"].id,
    )
    original = vn_graph_compiler.compile(source)

    changed_assets = [dict(item) for item in source.asset_bindings]
    changed_assets[0]["media_url"] = "/api/media/a-different-object"
    changed = vn_graph_compiler.compile(replace(source, asset_bindings=changed_assets))
    assert changed.binding_manifest_hash != original.binding_manifest_hash
    assert changed.graph_hash != original.graph_hash

    with pytest.raises(VNGraphCompileError, match="content_hash"):
        vn_graph_compiler.compile(replace(source, chapter_content_hash="0" * 64))
    with pytest.raises(VNGraphCompileError, match="编译版本"):
        vn_graph_compiler.compile(replace(source, compiler_version="future-compiler"))

    unavailable = [dict(item) for item in source.asset_bindings]
    unavailable[0].update(
        {
            "required": True,
            "slot_status": "bound",
            "media_url": None,
            "legacy_url": None,
        }
    )
    with pytest.raises(VNGraphCompileError, match="已绑定的必需资源不可用"):
        vn_graph_compiler.compile(replace(source, asset_bindings=unavailable))


def test_versioned_storage_failure_does_not_fall_back_to_public_legacy_url(db):
    seeded = _seed(db)
    seeded["background_storage"].status = "quarantined"
    db.commit()

    source = load_compile_input(
        db,
        script_revision_id=seeded["script"].id,
    )
    background = next(item for item in source.asset_bindings if item["role"] == "background")
    assert background["legacy_url"] == "/static/assets/legacy-background.png"
    assert background["media_url"] is None
    assert background["status"] == "missing_media"


def test_compile_task_freezes_inputs_and_persists_immutable_revision_history(db):
    seeded = _seed(db)
    project = seeded["project"]
    chapter = seeded["chapter"]
    task, created = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-v1",
    )
    assert created is True
    assert db.query(VNGraphRevision).count() == 0
    assert db.query(UsageReservation).filter_by(task_id=task.id).count() == 1
    assert db.query(TaskEvent).filter_by(task_id=task.id, event_type="queued").count() == 1
    assert db.query(OutboxEvent).filter_by(aggregate_id=task.id).one().event_type == "vngraph.compile.requested"
    frozen_manifest = task.parameters["binding_manifest"]
    assert task.parameters["binding_manifest_hash"] == content_hash(frozen_manifest)
    assert len(frozen_manifest["voice_line_versions"]) == 2
    assert db.query(VoiceLineVersion).count() == 2
    frozen_background_url = next(
        item["media_url"]
        for item in frozen_manifest["asset_bindings"]
        if item["role"] == "background"
    )
    frozen_first_voice = frozen_manifest["voice_line_versions"][0]
    assert frozen_first_voice["voice_line_version_id"]
    assert frozen_first_voice["audio_asset_version"]["asset_version_id"]

    replay, replay_created = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-v1",
    )
    assert replay.id == task.id
    assert replay_created is False

    # Change the live binding after the task snapshot. The queued task must
    # still compile the old, exact media URL.
    replacement_storage = StorageObject(
        owner_id=seeded["user"].id,
        project_id=project.id,
        storage_backend="local",
        storage_key="images/background-v2.png",
        media_type="image/png",
        byte_size=10,
        sha256="8" * 64,
        visibility="private",
        status="active",
    )
    db.add(replacement_storage)
    db.flush()
    replacement_version = AssetVersion(
        asset_id=seeded["background_asset"].id,
        source_revision_id=chapter.id,
        storage_object_id=replacement_storage.id,
        version_no=2,
        cache_key="background-v2",
        prompt_hash="9" * 64,
        prompt_version="v1",
        safety_status="safe",
    )
    db.add(replacement_version)
    db.flush()
    seeded["background_binding"].asset_version_id = replacement_version.id
    seeded["first_voice_line"].text = "任务入队后修改的台词"
    seeded["first_voice_line"].status = "planned"
    db.commit()

    executed_task, first_revision, first_created = execute_vn_graph_compile_task(
        db,
        task_id=task.id,
        worker_id="worker-a",
    )
    db.commit()
    assert executed_task.status == "succeeded"
    assert first_created is True
    assert first_revision.status == "ready"
    assert first_revision.generation_task_id == task.id
    assert first_revision.binding_manifest == frozen_manifest
    assert first_revision.binding_manifest_hash == content_hash(frozen_manifest)
    assert first_revision.source_manifest_hash == first_revision.binding_manifest_hash
    assert _background_url(first_revision.graph_json) == frozen_background_url
    assert db.query(VNGraphRevision).count() == 1
    empty_head = get_vn_graph_head(db, script_revision_id=seeded["script"].id)
    assert empty_head.revision_id is None
    assert empty_head.lock_version == 1
    first_events = (
        db.query(TaskEvent)
        .filter_by(task_id=task.id)
        .order_by(TaskEvent.seq)
        .all()
    )
    assert [event.seq for event in first_events] == [1, 2, 3, 4, 5, 6]
    assert [event.event_type for event in first_events] == [
        "queued",
        "started",
        "progress",
        "progress",
        "progress",
        "completed",
    ]

    # Re-delivery is idempotent and cannot create another revision.
    _, same_revision, delivered_created = execute_vn_graph_compile_task(
        db,
        task_id=task.id,
        worker_id="worker-b",
    )
    assert same_revision.id == first_revision.id
    assert delivered_created is False
    assert db.query(VNGraphRevision).count() == 1

    selected_first = activate_vn_graph_head(
        db,
        script_revision_id=seeded["script"].id,
        revision_id=first_revision.id,
        expected_lock_version=1,
    )
    assert selected_first.head.revision_id == first_revision.id
    assert selected_first.head.lock_version == 2

    # A fresh task sees binding v2 and freezes the reviewed graph as its parent.
    second_task, _ = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-v2",
    )
    second_voice = second_task.parameters["binding_manifest"]["voice_line_versions"][0]
    assert second_voice["text"] == "任务入队后修改的台词"
    assert second_voice["voice_line_version_id"] != frozen_first_voice[
        "voice_line_version_id"
    ]
    assert db.query(VoiceLineVersion).count() == 3
    db.commit()
    _, second_revision, second_created = execute_vn_graph_compile_task(
        db,
        task_id=second_task.id,
        worker_id="worker-c",
    )
    db.commit()
    assert second_created is True
    assert second_revision.parent_revision_id == first_revision.id
    assert second_revision.graph_hash != first_revision.graph_hash
    assert _background_url(second_revision.graph_json) == f"/api/media/{replacement_storage.id}"
    assert db.query(VNGraphRevision).count() == 2
    head = db.query(VNGraphHead).filter_by(script_revision_id=seeded["script"].id).one()
    assert head.current_revision_id == first_revision.id

    selected_second = activate_vn_graph_head(
        db,
        script_revision_id=seeded["script"].id,
        revision_id=second_revision.id,
        expected_lock_version=2,
    )
    assert selected_second.head.revision_id == second_revision.id
    assert selected_second.head.lock_version == 3
    same_second = activate_vn_graph_head(
        db,
        script_revision_id=seeded["script"].id,
        revision_id=second_revision.id,
        expected_lock_version=3,
    )
    assert same_second.head.revision_id == second_revision.id
    assert same_second.head.lock_version == 3

    late_replay, late_replay_created = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-v1",
    )
    assert late_replay.id == task.id
    assert late_replay_created is False
    with pytest.raises(AppError) as stale:
        activate_vn_graph_head(
            db,
            script_revision_id=seeded["script"].id,
            revision_id=first_revision.id,
            expected_lock_version=2,
        )
    assert stale.value.code == "head.version_conflict"


def test_compile_worker_rejects_tampered_asset_version_snapshot(db):
    seeded = _seed(db)
    task, _ = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-tampered-asset",
    )
    db.execute(
        update(AssetVersion)
        .where(AssetVersion.id == seeded["background_version"].id)
        .values(prompt_hash="0" * 64)
    )
    db.commit()

    failed_task, revision, created = execute_vn_graph_compile_task(
        db,
        task_id=task.id,
        worker_id="worker-tamper-check",
    )

    assert revision is None
    assert created is False
    assert failed_task.status == "failed"
    assert failed_task.error_code == "invalid_compile_snapshot"
    assert "AssetVersion snapshot mismatch" in failed_task.error_detail


def test_version_rows_reject_orm_mutation(db):
    seeded = _seed(db)
    seeded["background_version"].prompt_hash = "0" * 64
    with pytest.raises(RuntimeError, match="AssetVersion is immutable"):
        db.flush()
    db.rollback()

    load_compile_input(db, script_revision_id=seeded["script"].id)
    voice_version = db.query(VoiceLineVersion).order_by(VoiceLineVersion.version_no).first()
    voice_version.content_hash = "0" * 64

    with pytest.raises(RuntimeError, match="VoiceLineVersion is immutable"):
        db.flush()
    db.rollback()


def test_same_display_index_scripts_own_independent_graph_histories(db):
    seeded = _seed(db)
    first_script = seeded["script"]
    sibling_text = "同一显示位置上的另一条剧情路径。"
    sibling_chapter = ChapterRevision(
        project_id=seeded["project"].id,
        chapter_index=1,
        bible_revision_id=seeded["chapter"].bible_revision_id,
        outline_revision_id=seeded["chapter"].outline_revision_id,
        revision_no=2,
        source_hash="b" * 64,
        content_hash=content_hash(sibling_text),
        content=sibling_text,
        status="complete",
        created_by=seeded["user"].id,
    )
    db.add(sibling_chapter)
    db.flush()
    sibling_script = ensure_backfilled_script_revision(
        db,
        chapter=sibling_chapter,
        activate=True,
    )

    tasks = [
        create_vn_graph_compile_task(
            db,
            user_id=seeded["user"].id,
            script_revision_id=script_id,
            idempotency_key=f"same-index:{script_id}",
        )[0]
        for script_id in (first_script.id, sibling_script.id)
    ]
    db.commit()
    revisions = [
        execute_vn_graph_compile_task(
            db,
            task_id=task.id,
            worker_id=f"worker-{index}",
        )[1]
        for index, task in enumerate(tasks)
    ]
    db.commit()

    assert {revision.script_revision_id for revision in revisions} == {
        first_script.id,
        sibling_script.id,
    }
    assert [revision.revision_no for revision in revisions] == [1, 1]
    assert db.query(VNGraphHead).count() == 2
    assert all(head.current_revision_id is None for head in db.query(VNGraphHead).all())


def test_compile_worker_rolls_back_failed_flush_before_recording_retry(db, monkeypatch):
    seeded = _seed(db)
    task, _ = create_vn_graph_compile_task(
        db,
        user_id=seeded["user"].id,
        script_revision_id=seeded["script"].id,
        idempotency_key="compile-flush-failure",
    )
    db.commit()

    factory = sessionmaker(
        bind=db.get_bind(),
        expire_on_commit=False,
        autoflush=False,
    )

    def fail_during_flush(session, *, task_id, worker_id):
        claimed = claim_task(session, task_id, worker_id)
        assert claimed is not None
        # The started event already owns seq=2. Force the same integrity error
        # that originally left the production session in PendingRollbackError.
        session.add(
            TaskEvent(
                task_id=task_id,
                seq=2,
                event_type="progress",
                payload={"stage": "loading_snapshot", "progress": 10},
            )
        )
        session.flush()

    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    monkeypatch.setattr(
        worker_tasks,
        "execute_vn_graph_compile_task",
        fail_during_flush,
    )

    worker_tasks.compile_vngraph.run(task.id)

    db.expire_all()
    recovered = db.query(GenerationTask).filter_by(id=task.id).one()
    assert recovered.status == "queued"
    assert recovered.stage == "retrying"
    assert recovered.attempt == 1
    assert recovered.error_code == "vngraph_compile_failed"
    events = (
        db.query(TaskEvent)
        .filter_by(task_id=task.id)
        .order_by(TaskEvent.seq)
        .all()
    )
    assert [(event.seq, event.event_type) for event in events] == [
        (1, "queued"),
        (2, "started"),
        (3, "retrying"),
    ]
