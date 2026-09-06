from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models import Project, User
from app.models_v2 import (
    ChapterRevision,
    ChapterSlot,
    OutlineChapter,
    OutlineRevision,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)
from app.orm_base import Base


def test_outline_and_chapter_revisions_bind_to_stable_path_identity():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        user = User(
            email="path-revision@example.com",
            password_hash="hash",
            display_name="Path Revision",
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            title="Path revision",
            story_start="Start",
            story_end="End",
        )
        db.add(project)
        db.flush()
        path = StoryPath(project_id=project.id, title="Main")
        db.add(path)
        db.flush()
        slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
        db.add(slot)
        db.flush()
        path_chapter = StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=slot.id,
            display_index=1,
        )
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash="a" * 64,
            content_hash="b" * 64,
            content_json={"world": "test"},
            created_by=user.id,
        )
        db.add_all([path_chapter, bible])
        db.flush()
        outline = OutlineRevision(
            project_id=project.id,
            story_path_id=path.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash="c" * 64,
            content_hash="d" * 64,
            created_by=user.id,
        )
        db.add(outline)
        db.flush()
        outline_chapter = OutlineChapter(
            outline_revision_id=outline.id,
            chapter_index=1,
            story_path_chapter_id=None,
            title="One",
            summary="Summary",
            content_hash="e" * 64,
        )
        outline_head = StoryPathOutlineHead(
            story_path_id=path.id,
            current_revision_id=outline.id,
        )
        db.add_all([outline_chapter, outline_head])
        db.flush()
        outline_chapter.story_path_chapter_id = path_chapter.id
        db.flush()
        chapter = ChapterRevision(
            project_id=project.id,
            chapter_slot_id=slot.id,
            created_for_story_path_id=path.id,
            chapter_index=1,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            revision_no=1,
            source_hash="f" * 64,
            context_manifest={
                "story_path_id": path.id,
                "path_chapter_id": path_chapter.id,
                "ancestors": [],
            },
            context_hash="1" * 64,
            content_hash="2" * 64,
            content="Chapter content",
            status="complete",
            created_by=user.id,
        )
        db.add(chapter)
        db.flush()
        path_chapter.current_revision_id = chapter.id
        db.commit()

        assert outline.story_path_id == path.id
        assert outline_chapter.story_path_chapter_id == path_chapter.id
        assert outline_chapter.display_index == 1
        assert outline_head.current_revision_id == outline.id
        assert chapter.chapter_slot_id == slot.id
        assert chapter.created_for_story_path_id == path.id
        assert chapter.context_manifest["path_chapter_id"] == path_chapter.id
        assert path_chapter.current_revision_id == chapter.id

        second_slot = ChapterSlot(
            project_id=project.id,
            created_for_story_path_id=path.id,
        )
        db.add(second_slot)
        db.flush()
        second_placement = StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=second_slot.id,
            display_index=2,
        )
        db.add(second_placement)
        db.flush()
        outline_chapter.story_path_chapter_id = second_placement.id
        with pytest.raises(RuntimeError, match="write-once"):
            db.flush()
        db.rollback()
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_legacy_chapter_writer_gets_deterministic_context_bridge():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        user = User(email="bridge@example.com", password_hash="hash", display_name="Bridge")
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            title="Bridge",
            story_start="Start",
            story_end="End",
        )
        db.add(project)
        db.flush()
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash="a" * 64,
            content_hash="b" * 64,
            content_json={},
        )
        db.add(bible)
        db.flush()
        outline = OutlineRevision(
            project_id=project.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash="c" * 64,
            content_hash="d" * 64,
        )
        db.add(outline)
        db.flush()
        chapter = ChapterRevision(
            project_id=project.id,
            chapter_index=1,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            revision_no=1,
            source_hash="e" * 64,
            content_hash="f" * 64,
            content="Legacy writer",
        )
        db.add(chapter)
        db.commit()

        assert chapter.context_manifest == {}
        assert chapter.context_hash == chapter.source_hash
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
