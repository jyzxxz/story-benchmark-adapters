"""
Phase 2 v2 stats endpoint tests.

验证 stats_service_v2 从 GenerationTask + parameters.asset_type 正确聚合,
返回结构与 v1 ProjectStatsResponse 兼容.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.stats_service_v2 import (
    get_asset_time_breakdown_v2,
    get_project_stats_v2,
)
from app.database import Base
from app.models import Project, User
from app.models_v2 import GenerationTask, OutlineRevision


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _owner_and_project(db, suffix: str):
    user = User(
        email=f"stats-{suffix}@example.com",
        password_hash="hash",
        display_name="stats owner",
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title=f"stats project {suffix}",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    return user, project


def _make_task(
    db,
    *,
    user_id: int,
    project_id: int,
    kind: str,
    asset_type: str | None = None,
    duration_sec: float = 10.0,
    chapter_index: int | None = None,
    emotion: str | None = None,
    mood: str | None = None,
    seq: int = 0,
):
    """Build a succeeded GenerationTask with the given shape.

    `seq` keeps idempotency_key unique across calls within one test."""
    started = datetime.now(timezone.utc) - timedelta(seconds=duration_sec)
    finished = datetime.now(timezone.utc)
    parameters = {}
    if asset_type:
        parameters["asset_type"] = asset_type
        render_spec = {}
        if emotion:
            render_spec["emotion"] = emotion
        if mood:
            render_spec["mood"] = mood
        if render_spec:
            parameters["render_spec"] = render_spec
    source_refs = {}
    if chapter_index is not None:
        source_refs["chapter_index"] = chapter_index
    task = GenerationTask(
        id=f"task-{seq}-{kind}-{asset_type or 'x'}-{chapter_index or 0}-{emotion or mood or 'none'}",
        user_id=user_id,
        project_id=project_id,
        kind=kind,
        status="succeeded",
        idempotency_key=f"idem-{project_id}-{seq}-{kind}-{asset_type or 'x'}",
        source_refs=source_refs,
        parameters=parameters,
        parameters_hash="hash",
        started_at=started,
        finished_at=finished,
    )
    db.add(task)
    db.flush()
    return task


def test_get_project_stats_v2_empty_project(db):
    """空项目返回零值,不报错(v1 在这种情况会 500,这是 v2 的改善)."""
    user, project = _owner_and_project(db, "empty")
    stats = get_project_stats_v2(db, project.id)
    assert stats["project_id"] == project.id
    assert stats["total_chapters_generated"] == 0
    assert stats["total_portraits_generated"] == 0
    assert stats["total_backgrounds_generated"] == 0
    assert stats["total_keyframes_generated"] == 0
    assert stats["total_assets_generated"] == 0
    assert stats["total_chapter_time"] == 0.0
    assert stats["total_portrait_time"] == 0.0
    assert stats["avg_portrait_time"] == 0.0
    assert stats["outline_approved_at"] is None
    assert stats["first_chapter_generated_at"] is None
    assert stats["first_chapter_duration"] is None
    assert stats["generation_stats"] == []
    assert stats["portrait_stats"] == []
    assert stats["background_stats"] == []
    assert stats["keyframe_stats"] == []


def test_get_project_stats_v2_counts_asset_types(db):
    """不同 asset_type 的 task 被分到对应类别."""
    user, project = _owner_and_project(db, "mixed")
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", duration_sec=5.0, seq=1)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", duration_sec=15.0, seq=2)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="background", duration_sec=20.0, seq=3)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="keyframe", duration_sec=8.0, seq=4)

    stats = get_project_stats_v2(db, project.id)
    assert stats["total_portraits_generated"] == 2
    assert stats["total_backgrounds_generated"] == 1
    assert stats["total_keyframes_generated"] == 1
    assert stats["total_assets_generated"] == 4
    # avg = (5+15)/2 = 10.0
    assert stats["avg_portrait_time"] == pytest.approx(10.0, rel=0.01)
    assert stats["total_portrait_time"] == pytest.approx(20.0, rel=0.01)
    assert stats["total_background_time"] == pytest.approx(20.0, rel=0.01)
    assert stats["total_keyframe_time"] == pytest.approx(8.0, rel=0.01)


def test_get_project_stats_v2_filters_non_succeeded(db):
    """只有 status='succeeded' 的 task 被统计."""
    user, project = _owner_and_project(db, "filter")
    succeeded = _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", seq=1)
    # 同一类型但 status 不同
    failed = GenerationTask(
        id="task-failed-1",
        user_id=user.id,
        project_id=project.id,
        kind="asset.render",
        status="failed",
        idempotency_key="idem-failed-1",
        source_refs={},
        parameters={"asset_type": "portrait"},
        parameters_hash="h",
    )
    db.add(failed)
    db.flush()
    stats = get_project_stats_v2(db, project.id)
    assert stats["total_portraits_generated"] == 1


def test_get_project_stats_v2_chapter_kind_filter(db):
    """chapter.generate + chapter.batch 都计入章节统计, asset.render 不计入."""
    user, project = _owner_and_project(db, "chapter")
    _make_task(db, user_id=user.id, project_id=project.id, kind="chapter.generate", duration_sec=30.0, chapter_index=1, seq=1)
    _make_task(db, user_id=user.id, project_id=project.id, kind="chapter.batch", duration_sec=60.0, chapter_index=2, seq=2)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", duration_sec=5.0, seq=3)
    stats = get_project_stats_v2(db, project.id)
    assert stats["total_chapter_time"] == pytest.approx(90.0, rel=0.01)
    # total_chapters_generated 基于 ChapterHead,这里没有 head,所以是 0
    assert stats["total_chapters_generated"] == 0
    # generation_stats 包含 chapter tasks
    assert len(stats["generation_stats"]) == 2


def test_get_project_stats_v2_first_chapter_duration(db):
    """outline_approved_at + first_chapter_generated_at 计算 first_chapter_duration."""
    from app.models_v2 import StoryBibleRevision
    user, project = _owner_and_project(db, "duration")
    approved = datetime.now(timezone.utc) - timedelta(hours=2)
    # 先建 bible(OutlineRevision 外键依赖它), 再建 outline
    db.add(
        StoryBibleRevision(
            id="b-1",
            project_id=project.id,
            revision_no=1,
            source_hash="bs",
            content_hash="bc",
            content_json={"k": "v"},
            status="complete",
            created_by=user.id,
        )
    )
    db.flush()
    db.add(
        OutlineRevision(
            id="outline-1",
            project_id=project.id,
            bible_revision_id="b-1",
            revision_no=1,
            source_hash="s",
            content_hash="c",
            status="draft",
            approved_at=approved,
            created_by=user.id,
        )
    )
    db.flush()
    _make_task(db, user_id=user.id, project_id=project.id, kind="chapter.generate", duration_sec=10.0, seq=1)
    stats = get_project_stats_v2(db, project.id)
    assert stats["outline_approved_at"] is not None
    assert stats["first_chapter_generated_at"] is not None
    # first_chapter_duration = finished - approved ≈ 2 hours minus 10s
    assert stats["first_chapter_duration"] is not None
    assert stats["first_chapter_duration"] > 7000  # > ~2 小时


def test_get_asset_time_breakdown_v2_by_emotion_mood(db):
    """portrait 按 emotion 分桶, background 按 mood 分桶, background 按章节分桶."""
    user, project = _owner_and_project(db, "breakdown")
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", emotion="happy", duration_sec=10.0, seq=1)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", emotion="happy", duration_sec=20.0, seq=2)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", emotion="sad", duration_sec=30.0, seq=3)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="background", mood="calm", chapter_index=1, duration_sec=5.0, seq=4)
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="background", mood="calm", chapter_index=2, duration_sec=15.0, seq=5)

    breakdown = get_asset_time_breakdown_v2(db, project.id)
    by_emotion = {row["key"]: row for row in breakdown["portrait_by_emotion"]}
    assert "happy" in by_emotion
    assert by_emotion["happy"]["count"] == 2
    assert by_emotion["happy"]["total_time"] == pytest.approx(30.0, rel=0.01)
    assert by_emotion["happy"]["avg_time"] == pytest.approx(15.0, rel=0.01)
    assert "sad" in by_emotion
    assert by_emotion["sad"]["count"] == 1

    by_mood = {row["key"]: row for row in breakdown["background_by_mood"]}
    assert "calm" in by_mood
    assert by_mood["calm"]["count"] == 2

    by_chapter = {row["key"]: row for row in breakdown["background_by_chapter"]}
    assert "1" in by_chapter
    assert "2" in by_chapter


def test_get_project_stats_v2_response_schema_compatible(db):
    """get_project_stats_v2 返回的 dict 能直接喂给 v1 ProjectStatsResponse schema."""
    from app.schemas import ProjectStatsResponse
    user, project = _owner_and_project(db, "schema")
    _make_task(db, user_id=user.id, project_id=project.id, kind="asset.render", asset_type="portrait", duration_sec=5.0, seq=1)
    stats = get_project_stats_v2(db, project.id)
    # 关键:能不报错地构造 schema,说明字段兼容
    response = ProjectStatsResponse(**stats)
    assert response.project_id == project.id
    assert response.total_portraits_generated == 1
