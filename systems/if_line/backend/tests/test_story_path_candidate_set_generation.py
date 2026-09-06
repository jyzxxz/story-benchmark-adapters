from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.application.hashing import content_hash
from app.application.revision_head_service import activate_path_chapter_revision_head
from app.application.revision_service import create_bible_revision
from app.application.story_branch_contract import BranchGenerationOutputError
from app.application.candidate_set_review_service import (
    activate_candidate_set_head,
    get_candidate_set_head_value,
    list_candidate_set_candidates,
    list_candidate_set_revisions,
)
from app.application.story_branch_service import (
    create_candidate_set_generation_task,
    persist_candidate_set_revision,
)
from app.application.task_service import create_generation_task
from app.application.story_outline_service import (
    activate_story_path_outline_head,
    create_story_path_outline_revision,
)
from app.core.errors import AppError
from app.integrations.llm import ModelCallResult
from app.integrations.llm.branch_adapter import BRANCH_PROMPT_VERSION
from app.models import Project, User
from app.models_v2 import (
    BranchCandidate,
    BranchEdge,
    CandidateSetHead,
    CandidateSetRevision,
    ChapterRevision,
    GenerationTask,
    StateSnapshot,
    StoryNode,
    StoryPath,
    StoryPathChapter,
)
from app.orm_base import Base
from app.schemas_branch_generation import GeneratedBranchCandidateBatch
from app.schemas_v2 import BranchCandidateRead, CandidateSetRevisionRead
from app.workers.branch_tasks import execute_branch_generation_task


class _Provider:
    def __init__(self, marker: str = "a"):
        self.marker = marker
        self.calls = 0
        self.requests = []

    async def generate_candidates(self, request):
        self.calls += 1
        self.requests.append(request)
        return ModelCallResult(
            data={
                "candidates": [
                    {
                        "option_key": f"{self.marker}_left",
                        "preview_text": f"{self.marker} chooses the left corridor.",
                        "state_delta": {"route": f"{self.marker}-left"},
                    },
                    {
                        "option_key": f"{self.marker}_right",
                        "preview_text": f"{self.marker} chooses the right corridor.",
                        "state_delta": {"route": f"{self.marker}-right"},
                    },
                ]
            },
            raw_text=None,
            provider="test",
            model="candidate-test",
            provider_request_id=f"request-{self.marker}-{self.calls}",
            input_tokens=10,
            output_tokens=20,
            latency_ms=1,
            prompt_version=BRANCH_PROMPT_VERSION,
        )


@pytest.fixture()
def seeded(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'candidate-sets.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    user = User(
        email="story-candidates@example.com",
        password_hash="hash",
        display_name="Story Candidates",
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Versioned candidates",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Main")
    db.add(path)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "A city with two sealed corridors."},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": 1,
                "title": "The corridor",
                "summary": "The protagonist must choose a corridor.",
                "conflict": "Left or right",
                "characters": ["Lin"],
                "scene": "Underground station",
                "emotion": "Tense",
                "visual_keywords": ["corridor"],
            }
        ],
        user_id=user.id,
    )
    outline_result = activate_story_path_outline_head(
        db,
        story_path_id=path.id,
        revision_id=outline.id,
        expected_lock_version=2,
    )
    placement = outline_result.path_chapters[0]
    state_json = {"route": "main", "keys": ["station"]}
    snapshot = StateSnapshot(
        project_id=project.id,
        state_hash=content_hash(state_json),
        state_json=state_json,
    )
    db.add(snapshot)
    db.flush()
    chapter_text = "Lin reaches a checkpoint beneath the station."
    chapter = ChapterRevision(
        project_id=project.id,
        chapter_slot_id=placement.chapter_slot_id,
        created_for_story_path_id=path.id,
        chapter_index=1,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        state_snapshot_id=snapshot.id,
        revision_no=1,
        source_hash="a" * 64,
        context_manifest={"story_path_id": path.id},
        context_hash="b" * 64,
        content_hash=content_hash(chapter_text),
        content=chapter_text,
        status="ready",
        created_by=user.id,
    )
    db.add(chapter)
    db.flush()
    activate_path_chapter_revision_head(
        db,
        path_chapter_id=placement.id,
        revision_id=chapter.id,
        expected_lock_version=placement.lock_version,
    )
    checkpoint = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key="corridor-choice",
        content_revision_id=chapter.id,
        payload={"question": "Which corridor?"},
    )
    db.add(checkpoint)
    db.commit()
    try:
        yield {
            "db": db,
            "factory": factory,
            "user": user,
            "project": project,
            "path": path,
            "placement": placement,
            "bible": bible,
            "outline": outline,
            "chapter": chapter,
            "snapshot": snapshot,
            "checkpoint": checkpoint,
        }
    finally:
        db.close()
        engine.dispose()


def _queue(seeded, *, key: str, instructions: str = ""):
    return create_candidate_set_generation_task(
        seeded["db"],
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        chapter_revision_id=seeded["chapter"].id,
        state_snapshot_id=seeded["snapshot"].id,
        candidate_count=2,
        instructions=instructions,
        idempotency_key=key,
    )


def _run(seeded, task: GenerationTask, provider: _Provider):
    seeded["db"].commit()
    return asyncio.run(
        execute_branch_generation_task(
            task.id,
            provider=provider,
            worker_id=f"worker-{task.id}",
            session_factory=seeded["factory"],
        )
    )


def _generate_revision(seeded, *, key: str, marker: str) -> CandidateSetRevision:
    task, _created, _context = _queue(seeded, key=key)
    _run(seeded, task, _Provider(marker))
    seeded["db"].expire_all()
    return (
        seeded["db"]
        .query(CandidateSetRevision)
        .filter_by(generation_task_id=task.id)
        .one()
    )


def test_different_keys_create_multiple_candidate_set_revisions_at_one_checkpoint(seeded):
    first_task, first_created, first_source = _queue(seeded, key="candidate-set-1")
    second_task, second_created, second_source = _queue(
        seeded,
        key="candidate-set-2",
        instructions="Explore a different tone",
    )
    assert first_created is second_created is True
    assert first_task.id != second_task.id
    assert first_source.story_path_id == second_source.story_path_id

    first_ids = _run(seeded, first_task, _Provider("first"))
    second_ids = _run(seeded, second_task, _Provider("second"))
    seeded["db"].expire_all()

    revisions = (
        seeded["db"].query(CandidateSetRevision)
        .order_by(CandidateSetRevision.revision_no)
        .all()
    )
    assert [revision.revision_no for revision in revisions] == [1, 2]
    assert [revision.generation_task_id for revision in revisions] == [
        first_task.id,
        second_task.id,
    ]
    assert [item["id"] for item in revisions[0].candidates_json] == first_ids
    assert [item["id"] for item in revisions[1].candidates_json] == second_ids
    assert all(
        content_hash(revision.candidates_json) == revision.content_hash
        for revision in revisions
    )
    assert seeded["db"].query(BranchCandidate).count() == 4
    assert seeded["db"].query(CandidateSetHead).count() == 0
    assert (
        seeded["db"].query(BranchEdge)
        .filter_by(from_node_id=seeded["checkpoint"].id)
        .count()
        == 0
    )
    assert seeded["db"].query(StoryPath).count() == 1


def test_same_key_replays_frozen_task_after_chapter_head_moves(seeded):
    task, created, context = _queue(seeded, key="frozen-candidate-source")
    assert created is True
    placement = seeded["placement"]
    replacement_text = "Lin turns back before choosing."
    replacement = ChapterRevision(
        project_id=seeded["project"].id,
        chapter_slot_id=placement.chapter_slot_id,
        created_for_story_path_id=seeded["path"].id,
        chapter_index=1,
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        state_snapshot_id=seeded["snapshot"].id,
        revision_no=2,
        source_hash="c" * 64,
        context_manifest={"story_path_id": seeded["path"].id},
        context_hash="d" * 64,
        content_hash=content_hash(replacement_text),
        content=replacement_text,
        status="ready",
    )
    seeded["db"].add(replacement)
    seeded["db"].flush()
    current_version = (
        seeded["db"].query(StoryPathChapter)
        .filter_by(id=placement.id)
        .one()
        .lock_version
    )
    activate_path_chapter_revision_head(
        seeded["db"],
        path_chapter_id=placement.id,
        revision_id=replacement.id,
        expected_lock_version=current_version,
    )
    seeded["db"].commit()

    replay, replay_created, replay_context = _queue(
        seeded,
        key="frozen-candidate-source",
    )
    assert replay_created is False
    assert replay.id == task.id
    assert replay_context.source_hash == context.source_hash

    provider = _Provider("frozen")
    _run(seeded, replay, provider)
    assert provider.requests[0].chapter_tail.endswith("beneath the station.")
    revision = (
        seeded["db"].query(CandidateSetRevision)
        .filter_by(generation_task_id=task.id)
        .one()
    )
    assert revision.chapter_revision_id == seeded["chapter"].id


def test_target_generation_is_review_only_and_redelivery_recovers_same_set(seeded):
    task, _created, _context = _queue(seeded, key="review-only")
    provider = _Provider("review")
    first_ids = _run(seeded, task, provider)
    second_ids = _run(seeded, task, provider)
    seeded["db"].expire_all()

    persisted_task = seeded["db"].query(GenerationTask).filter_by(id=task.id).one()
    revision = seeded["db"].query(CandidateSetRevision).one()
    assert first_ids == second_ids
    assert provider.calls == 1
    assert persisted_task.result_refs["candidate_set_revision_id"] == revision.id
    assert persisted_task.result_refs["review_required"] is True
    assert seeded["db"].query(CandidateSetHead).count() == 0


def test_tampered_frozen_source_fails_before_provider_call(seeded):
    task, _created, _context = _queue(seeded, key="tampered-source")
    source_refs = dict(task.source_refs)
    source = dict(source_refs["candidate_set_source"])
    provider_input = dict(source["provider_input"])
    provider_input["chapter_tail"] = "tampered"
    source["provider_input"] = provider_input
    source_refs["candidate_set_source"] = source
    task.source_refs = source_refs
    seeded["db"].commit()

    provider = _Provider("unused")
    result = _run(seeded, task, provider)
    seeded["db"].expire_all()
    failed = seeded["db"].query(GenerationTask).filter_by(id=task.id).one()
    assert result == []
    assert provider.calls == 0
    assert failed.status == "failed"
    assert failed.error_code == "branch.invalid_source"
    assert seeded["db"].query(CandidateSetRevision).count() == 0


def test_worker_rejects_legacy_candidate_source_before_provider_call(seeded):
    task, created = create_generation_task(
        seeded["db"],
        user_id=seeded["user"].id,
        project_id=seeded["project"].id,
        kind="branch.candidates.generate",
        idempotency_key="legacy-candidate-source",
        source_refs={
            "checkpoint_node_id": seeded["checkpoint"].id,
            "chapter_revision_id": seeded["chapter"].id,
        },
        parameters={"candidate_count": 2},
        estimated_cost=0,
    )
    provider = _Provider("unused")

    result = _run(seeded, task, provider)
    seeded["db"].expire_all()
    failed = seeded["db"].query(GenerationTask).filter_by(id=task.id).one()

    assert created is True
    assert result == []
    assert provider.calls == 0
    assert failed.status == "failed"
    assert failed.error_code == "branch.invalid_source"


def test_rehashed_request_identity_tampering_fails_before_provider_call(seeded):
    task, _created, _context = _queue(seeded, key="tampered-request-identity")
    source_refs = dict(task.source_refs)
    source = dict(source_refs["candidate_set_source"])
    identity = dict(source["request_identity"])
    identity["checkpoint_node_id"] = "another-checkpoint"
    source["request_identity"] = identity
    digest = content_hash(source)
    source_refs["candidate_set_source"] = source
    source_refs["candidate_set_source_hash"] = digest
    source_refs["source_hash"] = digest
    task.source_refs = source_refs
    seeded["db"].commit()

    provider = _Provider("unused")
    result = _run(seeded, task, provider)
    seeded["db"].expire_all()
    failed = seeded["db"].query(GenerationTask).filter_by(id=task.id).one()
    assert result == []
    assert provider.calls == 0
    assert failed.status == "failed"
    assert failed.error_code == "branch.invalid_source"


def test_generation_rejects_excessively_nested_checkpoint_context(seeded):
    payload = {}
    for _index in range(17):
        payload = {"nested": payload}
    seeded["checkpoint"].payload = payload
    seeded["db"].commit()

    with pytest.raises(HTTPException) as exc_info:
        _queue(seeded, key="nested-context")

    assert exc_info.value.status_code == 413


def test_persistence_rejects_output_count_that_differs_from_frozen_request(seeded):
    task, _created, _context = _queue(seeded, key="wrong-output-count")
    batch = GeneratedBranchCandidateBatch.model_validate(
        {
            "candidates": [
                {
                    "option_key": f"option_{index}",
                    "preview_text": f"Preview {index}",
                    "state_delta": {"route": index},
                }
                for index in range(3)
            ]
        }
    )

    with pytest.raises(BranchGenerationOutputError, match="output count"):
        persist_candidate_set_revision(seeded["db"], task=task, batch=batch)

    assert seeded["db"].query(CandidateSetRevision).count() == 0


def test_versioned_candidate_payload_cannot_be_mutated(seeded):
    task, _created, _context = _queue(seeded, key="immutable-candidate")
    candidate_id = _run(seeded, task, _Provider("immutable"))[0]
    candidate = seeded["db"].query(BranchCandidate).filter_by(id=candidate_id).one()
    candidate.state_delta = {"route": "tampered"}

    with pytest.raises(RuntimeError, match="immutable"):
        seeded["db"].commit()
    seeded["db"].rollback()


def test_candidate_set_review_history_ordered_candidates_and_head_cas(seeded):
    first = _generate_revision(seeded, key="review-first", marker="first")
    second = _generate_revision(seeded, key="review-second", marker="second")

    history = list_candidate_set_revisions(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
    )
    assert [revision.id for revision in history] == [second.id, first.id]

    empty = get_candidate_set_head_value(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
    )
    assert empty.revision_id is None
    assert empty.lock_version == 1

    candidates = list_candidate_set_candidates(
        seeded["db"],
        candidate_set_revision_id=first.id,
        project_id=seeded["project"].id,
    )
    assert [candidate.option_key for candidate in candidates] == [
        "first_left",
        "first_right",
    ]
    assert [candidate.preview_text for candidate in candidates] == [
        item["preview_text"] for item in first.candidates_json
    ]
    assert CandidateSetRevisionRead.model_validate(first).content_hash == first.content_hash
    assert BranchCandidateRead.model_validate(candidates[0]).preview_text == (
        first.candidates_json[0]["preview_text"]
    )

    selected = activate_candidate_set_head(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        revision_id=first.id,
        expected_lock_version=1,
    )
    assert selected.head.revision_id == first.id
    assert selected.head.lock_version == 2

    repeated = activate_candidate_set_head(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        revision_id=first.id,
        expected_lock_version=2,
    )
    assert repeated.head.lock_version == 2
    assert repeated.head.updated_at == selected.head.updated_at

    with pytest.raises(AppError) as stale:
        activate_candidate_set_head(
            seeded["db"],
            story_path_id=seeded["path"].id,
            checkpoint_node_id=seeded["checkpoint"].id,
            revision_id=second.id,
            expected_lock_version=1,
        )
    assert stale.value.code == "head.version_conflict"
    assert stale.value.details["current_head"]["revision_id"] == first.id

    switched = activate_candidate_set_head(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        revision_id=second.id,
        expected_lock_version=2,
    )
    assert switched.head.revision_id == second.id
    assert switched.head.lock_version == 3


def test_candidate_query_uses_immutable_revision_payload_as_text_source(seeded):
    revision = _generate_revision(seeded, key="review-manifest", marker="manifest")
    expected_text = revision.candidates_json[0]["preview_text"]
    preview = (
        seeded["db"]
        .query(StoryNode)
        .filter_by(id=revision.candidates_json[0]["preview_node_id"])
        .one()
    )
    preview.payload = {"text": "mutable preview changed"}
    seeded["db"].flush()

    candidates = list_candidate_set_candidates(
        seeded["db"],
        candidate_set_revision_id=revision.id,
    )
    assert candidates[0].preview_text == expected_text


def test_candidate_set_activation_is_scoped_to_path_checkpoint(seeded):
    revision = _generate_revision(seeded, key="review-scope", marker="scope")
    other_checkpoint = StoryNode(
        project_id=seeded["project"].id,
        node_type="checkpoint",
        checkpoint_key="other-choice",
        content_revision_id=seeded["chapter"].id,
        payload={"question": "Another choice?"},
    )
    seeded["db"].add(other_checkpoint)
    seeded["db"].flush()

    empty = get_candidate_set_head_value(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=other_checkpoint.id,
    )
    assert empty.revision_id is None
    assert list_candidate_set_revisions(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=other_checkpoint.id,
    ) == []

    with pytest.raises(AppError) as wrong_checkpoint:
        activate_candidate_set_head(
            seeded["db"],
            story_path_id=seeded["path"].id,
            checkpoint_node_id=other_checkpoint.id,
            revision_id=revision.id,
            expected_lock_version=1,
        )
    assert wrong_checkpoint.value.code == "candidate_set_revision.not_found"
    with pytest.raises(AppError) as wrong_project:
        list_candidate_set_candidates(
            seeded["db"],
            candidate_set_revision_id=revision.id,
            project_id=seeded["project"].id + 1,
        )
    assert wrong_project.value.code == "candidate_set_revision.not_found"


def test_two_reviewers_cannot_spend_one_candidate_head_version(seeded):
    first = _generate_revision(seeded, key="review-race-first", marker="race_first")
    second = _generate_revision(seeded, key="review-race-second", marker="race_second")
    empty = get_candidate_set_head_value(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
    )
    seeded["db"].commit()

    other = seeded["factory"]()
    try:
        winner = activate_candidate_set_head(
            seeded["db"],
            story_path_id=seeded["path"].id,
            checkpoint_node_id=seeded["checkpoint"].id,
            revision_id=first.id,
            expected_lock_version=empty.lock_version,
        )
        seeded["db"].commit()

        with pytest.raises(AppError) as loser:
            activate_candidate_set_head(
                other,
                story_path_id=seeded["path"].id,
                checkpoint_node_id=seeded["checkpoint"].id,
                revision_id=second.id,
                expected_lock_version=empty.lock_version,
            )
        assert loser.value.code == "head.version_conflict"
        assert loser.value.details["current_head"]["revision_id"] == first.id
        assert winner.head.lock_version == empty.lock_version + 1
    finally:
        other.rollback()
        other.close()


def test_candidate_set_from_replaced_chapter_cannot_be_activated(seeded):
    revision = _generate_revision(seeded, key="review-stale-source", marker="stale")
    replacement_text = "Lin leaves before reaching the old checkpoint."
    replacement = ChapterRevision(
        project_id=seeded["project"].id,
        chapter_slot_id=seeded["placement"].chapter_slot_id,
        created_for_story_path_id=seeded["path"].id,
        chapter_index=1,
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        state_snapshot_id=seeded["snapshot"].id,
        revision_no=2,
        source_hash="e" * 64,
        context_manifest={"story_path_id": seeded["path"].id},
        context_hash="f" * 64,
        content_hash=content_hash(replacement_text),
        content=replacement_text,
        status="ready",
    )
    seeded["db"].add(replacement)
    seeded["db"].flush()
    activate_path_chapter_revision_head(
        seeded["db"],
        path_chapter_id=seeded["placement"].id,
        revision_id=replacement.id,
        expected_lock_version=seeded["placement"].lock_version,
    )

    with pytest.raises(AppError) as stale_source:
        activate_candidate_set_head(
            seeded["db"],
            story_path_id=seeded["path"].id,
            checkpoint_node_id=seeded["checkpoint"].id,
            revision_id=revision.id,
            expected_lock_version=1,
        )
    assert stale_source.value.code == "candidate_set_revision.stale_source"
    assert seeded["db"].query(CandidateSetHead).count() == 0


def test_legacy_candidate_set_bridge_is_visible_but_not_selectable(seeded):
    bridge = CandidateSetRevision(
        project_id=seeded["project"].id,
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        chapter_revision_id=seeded["chapter"].id,
        state_snapshot_id=seeded["snapshot"].id,
        revision_no=1,
        source_hash="9" * 64,
        candidate_count=2,
        instructions="legacy bridge",
        candidates_json=None,
        content_hash=None,
    )
    seeded["db"].add(bridge)
    seeded["db"].flush()
    assert list_candidate_set_revisions(
        seeded["db"],
        story_path_id=seeded["path"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
    ) == [bridge]

    with pytest.raises(AppError) as invalid:
        activate_candidate_set_head(
            seeded["db"],
            story_path_id=seeded["path"].id,
            checkpoint_node_id=seeded["checkpoint"].id,
            revision_id=bridge.id,
            expected_lock_version=1,
        )
    assert invalid.value.code == "candidate_set_revision.payload_invalid"
