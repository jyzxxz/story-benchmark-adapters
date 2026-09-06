from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Project, User
from app.models_v2 import ChapterSlot, StoryPath, StoryPathChapter
from app.orm_base import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    user = User(
        email="story-path@example.com",
        password_hash="hash",
        display_name="Story Path",
    )
    session.add(user)
    session.flush()
    project = Project(
        owner_id=user.id,
        title="Branching story",
        story_start="Start",
        story_end="End",
    )
    session.add(project)
    session.commit()
    try:
        yield session, project
    finally:
        session.close()
        engine.dispose()


def _root_path(db, project, *, title: str = "Main") -> StoryPath:
    path = StoryPath(project_id=project.id, title=title)
    db.add(path)
    db.flush()
    return path


def test_path_chapter_identity_is_stable_and_ordered_per_path(db):
    session, project = db
    path = _root_path(session, project)
    first_slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    second_slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    session.add_all([first_slot, second_slot])
    session.flush()

    first = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=first_slot.id,
        display_index=1,
    )
    session.add(first)
    session.flush()
    second = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=second_slot.id,
        display_index=2,
        predecessor_path_chapter_id=first.id,
    )
    session.add(second)
    session.commit()

    assert first.id != second.id
    assert first.chapter_slot_id != second.chapter_slot_id
    assert second.predecessor_path_chapter_id == first.id


def test_project_allows_exactly_one_root_story_path(db):
    session, project = db
    _root_path(session, project)
    session.commit()

    session.add(StoryPath(project_id=project.id, title="Another root"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_non_root_path_requires_complete_fork_provenance(db):
    session, project = db
    root = _root_path(session, project)
    session.commit()

    session.add(
        StoryPath(
            project_id=project.id,
            parent_path_id=root.id,
            title="Incomplete branch",
        )
    )
    with pytest.raises(ValueError, match="fork PathChapter"):
        session.commit()


@pytest.mark.parametrize("display_index", [0, -1])
def test_path_chapter_display_index_must_be_positive(db, display_index):
    session, project = db
    path = _root_path(session, project)
    slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    session.add(slot)
    session.flush()
    session.add(
        StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=slot.id,
            display_index=display_index,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_path_rejects_duplicate_display_index_and_slot(db):
    session, project = db
    path = _root_path(session, project)
    first_slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    second_slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    session.add_all([first_slot, second_slot])
    session.flush()
    session.add(
        StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=first_slot.id,
            display_index=1,
        )
    )
    session.commit()

    session.add(
        StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=second_slot.id,
            display_index=1,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
