from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Project, User
from app.models_v2 import (
    BranchCandidate,
    ChapterRevision,
    CandidateSetHead,
    CandidateSetRevision,
    OutlineRevision,
    StateSnapshot,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
)
from app.orm_base import Base


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(email="candidate-set@example.com", password_hash="hash", display_name="Sets")
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Candidate sets",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Main")
    bible = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash="a" * 64,
        content_hash="b" * 64,
        content_json={},
    )
    db.add_all([path, bible])
    db.flush()
    outline = OutlineRevision(
        project_id=project.id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash="c" * 64,
        content_hash="d" * 64,
    )
    state = StateSnapshot(
        project_id=project.id,
        state_hash="e" * 64,
        state_json={"route": "main"},
    )
    db.add_all([outline, state])
    db.flush()
    chapter = ChapterRevision(
        project_id=project.id,
        created_for_story_path_id=path.id,
        chapter_index=1,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        state_snapshot_id=state.id,
        revision_no=1,
        source_hash="f" * 64,
        context_manifest={"story_path_id": path.id, "ancestors": []},
        context_hash="1" * 64,
        content_hash="2" * 64,
        content="Choose.",
        status="complete",
    )
    db.add(chapter)
    db.flush()
    checkpoint = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key="choice",
        content_revision_id=chapter.id,
        payload={"question": "Choose"},
    )
    db.add(checkpoint)
    db.commit()
    try:
        yield db, project, user, path, chapter, state, checkpoint
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _candidate_set(db, project, user, path, chapter, state, checkpoint, revision_no):
    candidate_set = CandidateSetRevision(
        project_id=project.id,
        story_path_id=path.id,
        checkpoint_node_id=checkpoint.id,
        chapter_revision_id=chapter.id,
        state_snapshot_id=state.id,
        revision_no=revision_no,
        source_hash=str(revision_no) * 64,
        candidate_count=2,
        instructions=f"set {revision_no}",
        created_by=user.id,
    )
    db.add(candidate_set)
    db.flush()
    return candidate_set


def test_same_checkpoint_can_keep_multiple_candidate_set_revisions(seeded):
    db, project, user, path, chapter, state, checkpoint = seeded
    first_set = _candidate_set(db, project, user, path, chapter, state, checkpoint, 1)
    second_set = _candidate_set(db, project, user, path, chapter, state, checkpoint, 2)
    first_preview = StoryNode(project_id=project.id, node_type="paragraph", payload={"text": "A1"})
    second_preview = StoryNode(project_id=project.id, node_type="paragraph", payload={"text": "A2"})
    db.add_all([first_preview, second_preview])
    db.flush()
    db.add_all(
        [
            BranchCandidate(
                project_id=project.id,
                candidate_set_revision_id=first_set.id,
                checkpoint_node_id=checkpoint.id,
                option_key="same-option",
                preview_node_id=first_preview.id,
            ),
            BranchCandidate(
                project_id=project.id,
                candidate_set_revision_id=second_set.id,
                checkpoint_node_id=checkpoint.id,
                option_key="same-option",
                preview_node_id=second_preview.id,
            ),
            CandidateSetHead(
                story_path_id=path.id,
                checkpoint_node_id=checkpoint.id,
                current_revision_id=second_set.id,
            ),
        ]
    )
    db.commit()

    assert db.query(CandidateSetRevision).count() == 2
    assert db.query(BranchCandidate).filter_by(option_key="same-option").count() == 2
    assert db.query(CandidateSetHead).one().current_revision_id == second_set.id


def test_option_key_is_unique_inside_one_candidate_set(seeded):
    db, project, user, path, chapter, state, checkpoint = seeded
    candidate_set = _candidate_set(db, project, user, path, chapter, state, checkpoint, 1)
    previews = [
        StoryNode(project_id=project.id, node_type="paragraph", payload={"text": str(index)})
        for index in range(2)
    ]
    db.add_all(previews)
    db.flush()
    db.add_all(
        [
            BranchCandidate(
                project_id=project.id,
                candidate_set_revision_id=candidate_set.id,
                checkpoint_node_id=checkpoint.id,
                option_key="duplicate",
                preview_node_id=preview.id,
            )
            for preview in previews
        ]
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_candidate_set_revision_is_immutable(seeded):
    db, project, user, path, chapter, state, checkpoint = seeded
    candidate_set = _candidate_set(db, project, user, path, chapter, state, checkpoint, 1)
    db.commit()

    candidate_set.instructions = "changed"
    with pytest.raises(RuntimeError, match="immutable"):
        db.commit()
