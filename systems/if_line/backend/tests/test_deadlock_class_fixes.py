"""死锁类修复的回归测试。

D1: CandidateSetHead 行存在但 current_revision_id 为空（GET 端点预创建）
曾是硬 409，导致候选集生成永久卡死；应视为"尚无已选候选集"。
D3: finalize-publish 空 anchors 回退到服务端锚点收集
（collect_current_anchors），无草稿会话也能发布。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.draft_publication_service import (
    collect_current_anchors,
    finalize_and_publish,
)
from app.application.hashing import content_hash
from app.application.revision_head_service import (
    activate_path_chapter_revision_head,
)
from app.application.revision_service import create_bible_revision
from app.application.story_branch_service import _selected_candidate_set_parent
from app.application.story_outline_service import (
    create_story_path_outline_revision,
)
from app.core.errors import AppError
from app.models import Project, User
from app.models_v2 import (
    Base,
    CandidateSetHead,
    CandidateSetRevision,
    ChapterRevision,
    ProjectContentHead,
    StateSnapshot,
    StoryNode,
    StoryPath,
    StoryPathChapter,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_project(db):
    user = User(
        email="deadlock-fix@example.com",
        password_hash="hash",
        display_name="Deadlock Fix",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Deadlock fixes",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    return user, project


def _outline_chapters(count: int):
    return [
        {
            "story_path_chapter_id": None,
            "display_index": index,
            "title": f"Chapter {index}",
            "summary": f"Summary {index}",
            "characters": ["Lin"],
            "visual_keywords": ["signal"],
        }
        for index in range(1, count + 1)
    ]


def _chapter_revision(
    db,
    *,
    project_id: int,
    path: StoryPath,
    placement: StoryPathChapter,
    outline_revision_id: str,
    bible_revision_id: str,
    revision_no: int,
) -> ChapterRevision:
    text = f"Revision {revision_no} for slot {placement.chapter_slot_id}"
    revision = ChapterRevision(
        project_id=project_id,
        chapter_slot_id=placement.chapter_slot_id,
        created_for_story_path_id=path.id,
        chapter_index=placement.display_index,
        bible_revision_id=bible_revision_id,
        outline_revision_id=outline_revision_id,
        revision_no=revision_no,
        source_hash=str(revision_no) * 64,
        context_manifest={"path_chapter_id": placement.id},
        context_hash=str(revision_no) * 64,
        content_hash=content_hash(text),
        content=text,
        status="ready",
    )
    db.add(revision)
    db.flush()
    return revision


# ------------------------------ D1 ------------------------------


def test_candidate_set_parent_treats_unselected_head_as_missing(db):
    user, project = _seed_project(db)
    path = StoryPath(project_id=project.id, title="Main")
    db.add(path)
    db.flush()
    node = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key="corridor",
    )
    db.add(node)
    db.flush()
    # GET 端点会预创建 NULL head 行——此前这一步之后生成永久 409。
    db.add(
        CandidateSetHead(
            story_path_id=path.id,
            checkpoint_node_id=node.id,
            current_revision_id=None,
            lock_version=1,
        )
    )
    db.commit()

    assert (
        _selected_candidate_set_parent(
            db, story_path_id=path.id, checkpoint_node_id=node.id
        )
        is None
    )


def test_candidate_set_parent_returns_selected_revision(authored):
    seeded = authored
    db = seeded["db"]
    chapter = _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][0],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=1,
    )
    state_json = {"route": "main"}
    snapshot = StateSnapshot(
        project_id=seeded["project"].id,
        state_hash=content_hash(state_json),
        state_json=state_json,
    )
    db.add(snapshot)
    db.flush()
    node = StoryNode(
        project_id=seeded["project"].id,
        node_type="checkpoint",
        checkpoint_key="corridor",
        content_revision_id=chapter.id,
        payload={"question": "Which corridor?"},
    )
    db.add(node)
    db.flush()
    revision = CandidateSetRevision(
        project_id=seeded["project"].id,
        story_path_id=seeded["path"].id,
        checkpoint_node_id=node.id,
        chapter_revision_id=chapter.id,
        state_snapshot_id=snapshot.id,
        revision_no=1,
        source_hash="a" * 64,
        candidate_count=2,
        instructions="",
        candidates_json={"candidates": []},
        content_hash="b" * 64,
        created_by=seeded["user"].id,
    )
    db.add(revision)
    db.flush()
    db.add(
        CandidateSetHead(
            story_path_id=seeded["path"].id,
            checkpoint_node_id=node.id,
            current_revision_id=revision.id,
            lock_version=1,
        )
    )
    db.commit()

    assert (
        _selected_candidate_set_parent(
            db, story_path_id=seeded["path"].id, checkpoint_node_id=node.id
        )
        == revision.id
    )


# ------------------------------ D3 ------------------------------


@pytest.fixture()
def authored(db):
    user, project = _seed_project(db)
    path = StoryPath(project_id=project.id, title="Main")
    db.add(path)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Frozen"},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_outline_chapters(2),
        user_id=user.id,
    )
    db.commit()
    placements = (
        db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id)
        .order_by(StoryPathChapter.display_index)
        .all()
    )
    return {
        "db": db,
        "user": user,
        "project": project,
        "path": path,
        "bible": bible,
        "outline": outline,
        "placements": placements,
    }


def test_collect_anchors_prefers_heads_and_falls_back_to_latest(authored):
    seeded = authored
    db = seeded["db"]
    first = _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][0],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=1,
    )
    second = _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][0],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=2,
    )
    _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][1],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=1,
    )
    db.commit()

    anchors = collect_current_anchors(db, project_id=seeded["project"].id)
    # head 未设 → 回退到该章节位最新可选修订。
    assert anchors["bible_revision_id"] == seeded["bible"].id
    assert anchors["outline_revision_ids"] == {seeded["path"].id: seeded["outline"].id}
    assert (
        anchors["chapter_revision_ids"][seeded["placements"][0].id] == second.id
    )
    assert set(anchors["chapter_revision_ids"]) == {
        placement.id for placement in seeded["placements"]
    }

    # head 已设 → 优先 head（哪怕不是最新）。
    activate_path_chapter_revision_head(
        db,
        path_chapter_id=seeded["placements"][0].id,
        revision_id=first.id,
        expected_lock_version=seeded["placements"][0].lock_version,
    )
    db.commit()
    anchors = collect_current_anchors(db, project_id=seeded["project"].id)
    assert anchors["chapter_revision_ids"][seeded["placements"][0].id] == first.id


def test_collect_anchors_ignores_detached_placements(authored):
    seeded = authored
    db = seeded["db"]
    _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][0],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=1,
    )
    detached_revision = _chapter_revision(
        db,
        project_id=seeded["project"].id,
        path=seeded["path"],
        placement=seeded["placements"][1],
        outline_revision_id=seeded["outline"].id,
        bible_revision_id=seeded["bible"].id,
        revision_no=1,
    )
    db.commit()
    seeded["placements"][1].status = "detached"
    seeded["placements"][1].detached_at = seeded["placements"][1].updated_at
    seeded["placements"][1].detached_by_outline_revision_id = seeded["outline"].id
    db.commit()

    anchors = collect_current_anchors(db, project_id=seeded["project"].id)
    assert set(anchors["chapter_revision_ids"]) == {seeded["placements"][0].id}
    assert seeded["placements"][1].id not in anchors["chapter_revision_ids"]


def test_collect_anchors_falls_back_to_latest_bible_when_head_missing(db):
    user, project = _seed_project(db)
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Frozen"},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    # 模拟存量被毒化的状态：修订在、head 指针为空。
    head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    head.current_bible_revision_id = None
    db.commit()

    anchors = collect_current_anchors(db, project_id=project.id)
    assert anchors["bible_revision_id"] == bible.id


def test_finalize_empty_anchors_reports_readiness_blockers(db):
    user, project = _seed_project(db)
    db.commit()
    with pytest.raises(AppError) as captured:
        finalize_and_publish(
            db,
            project_id=project.id,
            user_id=user.id,
            idempotency_key="finalize-empty",
        )
    assert captured.value.status_code == 422
    assert captured.value.code == "release.not_ready"
    blocking_codes = {
        item["code"]
        for item in captured.value.details.get("blocking_items", [])
    }
    assert "bible.head_missing" in blocking_codes
