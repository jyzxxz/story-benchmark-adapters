from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.application.revision_head_service import (
    activate_path_chapter_revision_head,
    get_or_create_story_path_outline_head,
    parse_if_match,
)
from app.application.revision_service import (
    activate_bible_revision_head,
    create_bible_revision,
)
from app.application.story_outline_service import (
    activate_story_path_outline_head,
    create_story_path_outline_revision,
)
from app.core.errors import AppError
from app.models import Project, User
from app.models_v2 import (
    ChapterRevision,
    OutlineRevision,
    ProjectContentHead,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)
from app.orm_base import Base


@pytest.fixture()
def seeded(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'revision-heads.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    user = User(
        email="revision-heads@example.com",
        password_hash="hash",
        display_name="Revision Heads",
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Optimistic Heads",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Main")
    db.add(path)
    db.flush()
    first_bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "first"},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    second_bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "second"},
        source={"origin": "test"},
        user_id=user.id,
        activate=False,
    )
    db.commit()
    try:
        yield db, factory, user, project, path, first_bible, second_bible
    finally:
        db.close()
        engine.dispose()


def _chapters(count: int, path_chapter_ids: list[str | None] | None = None):
    ids = path_chapter_ids or [None] * count
    return [
        {
            "story_path_chapter_id": ids[index - 1],
            "display_index": index,
            "title": f"Chapter {index}",
            "summary": f"Summary {index}",
            "characters": [],
            "visual_keywords": [],
        }
        for index in range(1, count + 1)
    ]


def _chapter_revision(
    db,
    *,
    project_id: int,
    path: StoryPath,
    placement: StoryPathChapter,
    outline: OutlineRevision,
    bible_revision_id: str,
    revision_no: int,
) -> ChapterRevision:
    marker = str(revision_no)
    revision = ChapterRevision(
        project_id=project_id,
        chapter_slot_id=placement.chapter_slot_id,
        created_for_story_path_id=path.id,
        chapter_index=placement.display_index,
        bible_revision_id=bible_revision_id,
        outline_revision_id=outline.id,
        revision_no=revision_no,
        source_hash=marker * 64,
        context_manifest={"path_chapter_id": placement.id, "revision": revision_no},
        context_hash=marker * 64,
        content_hash=marker * 64,
        content=f"Revision {revision_no}",
        status="ready",
    )
    db.add(revision)
    db.flush()
    return revision


@pytest.mark.parametrize("value, expected", [("1", 1), ('"2"', 2), (" 37 ", 37)])
def test_if_match_parser_accepts_only_contract_forms(value, expected):
    assert parse_if_match(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, "", "0", "-1", 'W/"1"', "1,2", '"1', '1"', "abc"],
)
def test_if_match_parser_rejects_missing_weak_or_malformed_tags(value):
    with pytest.raises(AppError) as captured:
        parse_if_match(value)
    assert captured.value.status_code in {400, 428}


def test_bible_head_uses_cas_and_matching_reselection_is_a_noop(seeded):
    db, _factory, _user, project, _path, first, second = seeded
    head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    initial_version = head.lock_version

    selected = activate_bible_revision_head(
        db,
        project_id=project.id,
        revision_id=second.id,
        expected_lock_version=initial_version,
    )
    assert selected.head.revision_id == second.id
    assert selected.head.lock_version == initial_version + 1
    assert second.status == "complete"

    repeated = activate_bible_revision_head(
        db,
        project_id=project.id,
        revision_id=second.id,
        expected_lock_version=selected.head.lock_version,
    )
    assert repeated.head.lock_version == selected.head.lock_version

    with pytest.raises(AppError) as captured:
        activate_bible_revision_head(
            db,
            project_id=project.id,
            revision_id=first.id,
            expected_lock_version=initial_version,
        )
    assert captured.value.code == "head.version_conflict"
    assert captured.value.details["current_head"]["revision_id"] == second.id


def test_outline_creation_auto_adopts_first_revision_and_put_uses_cas(seeded):
    db, _factory, user, project, path, bible, _second = seeded
    head = get_or_create_story_path_outline_head(db, path.id)
    assert head.current_revision_id is None
    assert head.lock_version == 1

    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2),
        user_id=user.id,
    )
    # head 为空且锚定 Bible head → 创建即自动采用（首个自动采用规则）。
    db.expire_all()
    head = db.query(StoryPathOutlineHead).filter_by(story_path_id=path.id).one()
    assert head.current_revision_id == outline.id
    assert head.lock_version == 2
    assert db.query(StoryPathChapter).filter_by(story_path_id=path.id).count() == 2

    repeated = activate_story_path_outline_head(
        db,
        story_path_id=path.id,
        revision_id=outline.id,
        expected_lock_version=2,
    )
    assert repeated.head.current_revision_id == outline.id
    assert repeated.head.lock_version == 2
    assert len(repeated.path_chapters) == 2
    assert outline.status == "ready"

    with pytest.raises(AppError) as captured:
        activate_story_path_outline_head(
            db,
            story_path_id=path.id,
            revision_id=outline.id,
            expected_lock_version=1,
        )
    assert captured.value.code == "head.version_conflict"


@pytest.mark.parametrize("revision_status", ["draft", "approved"])
def test_outline_head_rejects_legacy_non_ready_status_without_side_effects(
    seeded,
    revision_status,
):
    db, _factory, user, project, path, bible, second_bible = seeded
    get_or_create_story_path_outline_head(db, path.id)
    # 锚定非 head 的 Bible 修订 → 创建时自动采用被跳过（bible 不一致），
    # 保持 head 为空以便验证激活端对非法状态自身的拒绝语义。
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=second_bible.id,
        chapters=_chapters(2),
        user_id=user.id,
    )
    outline.status = revision_status
    db.flush()

    with pytest.raises(HTTPException) as captured:
        activate_story_path_outline_head(
            db,
            story_path_id=path.id,
            revision_id=outline.id,
            expected_lock_version=1,
        )

    assert captured.value.status_code == 409
    db.expire_all()
    head = db.query(StoryPathOutlineHead).filter_by(story_path_id=path.id).one()
    assert head.current_revision_id is None
    assert head.lock_version == 1
    assert db.query(StoryPathChapter).filter_by(story_path_id=path.id).count() == 0
    content_head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    assert content_head.current_outline_revision_id is None


def test_stale_outline_cas_has_no_topology_side_effects(seeded):
    db, _factory, user, _project, path, bible, _second = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2),
        user_id=user.id,
    )
    first_result = activate_story_path_outline_head(
        db,
        story_path_id=path.id,
        revision_id=first.id,
        expected_lock_version=2,
    )
    original = [
        (item.id, item.display_index, item.predecessor_path_chapter_id)
        for item in first_result.path_chapters
    ]
    ids = [item.id for item in first_result.path_chapters]
    second = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2, [ids[1], ids[0]]),
        user_id=user.id,
    )

    with pytest.raises(AppError) as captured:
        activate_story_path_outline_head(
            db,
            story_path_id=path.id,
            revision_id=second.id,
            expected_lock_version=1,
        )
    assert captured.value.code == "head.version_conflict"

    db.expire_all()
    head = db.query(StoryPathOutlineHead).filter_by(story_path_id=path.id).one()
    topology = [
        (item.id, item.display_index, item.predecessor_path_chapter_id)
        for item in db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id)
        .order_by(StoryPathChapter.display_index)
        .all()
    ]
    assert head.current_revision_id == first.id
    assert head.lock_version == 2
    assert topology == original


def test_two_sessions_cannot_spend_the_same_head_version_twice(seeded):
    db, factory, _user, project, _path, first, second = seeded
    expected = db.query(ProjectContentHead).filter_by(project_id=project.id).one().lock_version
    other = factory()
    try:
        winner = activate_bible_revision_head(
            db,
            project_id=project.id,
            revision_id=second.id,
            expected_lock_version=expected,
        )
        db.commit()

        with pytest.raises(AppError) as captured:
            activate_bible_revision_head(
                other,
                project_id=project.id,
                revision_id=first.id,
                expected_lock_version=expected,
            )
        assert captured.value.code == "head.version_conflict"
        assert captured.value.details["current_head"]["lock_version"] == expected + 1
        assert winner.head.revision_id == second.id
    finally:
        other.rollback()
        other.close()


def test_path_chapter_head_is_scoped_by_slot_and_uses_cas(seeded):
    db, _factory, user, project, path, bible, _second = seeded
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2),
        user_id=user.id,
    )
    outline_result = activate_story_path_outline_head(
        db,
        story_path_id=path.id,
        revision_id=outline.id,
        expected_lock_version=2,
    )
    first_placement, second_placement = outline_result.path_chapters
    revision_one = _chapter_revision(
        db,
        project_id=project.id,
        path=path,
        placement=first_placement,
        outline=outline,
        bible_revision_id=bible.id,
        revision_no=1,
    )
    revision_two = _chapter_revision(
        db,
        project_id=project.id,
        path=path,
        placement=first_placement,
        outline=outline,
        bible_revision_id=bible.id,
        revision_no=2,
    )
    foreign_slot_revision = _chapter_revision(
        db,
        project_id=project.id,
        path=path,
        placement=second_placement,
        outline=outline,
        bible_revision_id=bible.id,
        revision_no=1,
    )

    selected = activate_path_chapter_revision_head(
        db,
        path_chapter_id=first_placement.id,
        revision_id=revision_one.id,
        expected_lock_version=1,
    )
    assert selected.head.revision_id == revision_one.id
    assert selected.head.lock_version == 2

    repeated = activate_path_chapter_revision_head(
        db,
        path_chapter_id=first_placement.id,
        revision_id=revision_one.id,
        expected_lock_version=2,
    )
    assert repeated.head.lock_version == 2

    with pytest.raises(AppError) as stale:
        activate_path_chapter_revision_head(
            db,
            path_chapter_id=first_placement.id,
            revision_id=revision_two.id,
            expected_lock_version=1,
        )
    assert stale.value.code == "head.version_conflict"

    with pytest.raises(AppError) as wrong_slot:
        activate_path_chapter_revision_head(
            db,
            path_chapter_id=first_placement.id,
            revision_id=foreign_slot_revision.id,
            expected_lock_version=2,
        )
    assert wrong_slot.value.code == "chapter_revision.not_found"

    switched = activate_path_chapter_revision_head(
        db,
        path_chapter_id=first_placement.id,
        revision_id=revision_two.id,
        expected_lock_version=2,
    )
    assert switched.head.revision_id == revision_two.id
    assert switched.head.lock_version == 3
    assert db.query(StoryPath).filter_by(project_id=project.id).count() == 1
