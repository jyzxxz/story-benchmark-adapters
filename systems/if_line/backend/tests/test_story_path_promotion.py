from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.application.candidate_set_review_service import activate_candidate_set_head
from app.application.chapter_script_service import activate_chapter_script_head
from app.application.hashing import content_hash
from app.application.revision_head_service import activate_path_chapter_revision_head
from app.application.revision_service import create_bible_revision
from app.application.story_branch_service import create_candidate_set_generation_task
from app.application.story_chapter_service import (
    build_chapter_generation_source,
    create_story_path_chapter_revision,
    resolve_chapter_generation_source,
)
from app.application.story_outline_service import (
    activate_story_path_outline_head,
    create_story_path_outline_revision,
)
from app.application.story_path_promotion_service import (
    promote_candidate_to_story_path,
)
from app.application.vn_graph_service import activate_vn_graph_head
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
    ChapterScriptRevision,
    StateSnapshot,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    StoryPathPromotionRecord,
    VNGraphRevision,
)
from app.orm_base import Base
from app.schemas_v2 import StoryPathRead
from app.workers.branch_tasks import execute_branch_generation_task


class _PromotionProvider:
    async def generate_candidates(self, request):
        candidates = [
            {
                "option_key": "left_gate",
                "preview_text": "Lin enters the left gate under a red signal.",
                "state_delta": {
                    "route": "left",
                    "inventory": {"coins": 3},
                    "remove_me": None,
                },
            },
            {
                "option_key": "right_gate",
                "preview_text": "Lin enters the right gate beneath a blue signal.",
                "state_delta": {
                    "route": "right",
                    "inventory": {"coins": 1},
                },
            },
            {
                "option_key": "center_gate",
                "preview_text": "Lin enters the center gate beneath a white signal.",
                "state_delta": {
                    "route": "center",
                    "inventory": {"coins": 2},
                },
            },
        ]
        return ModelCallResult(
            data={"candidates": candidates[: request.candidate_count]},
            raw_text=None,
            provider="test",
            model="promotion-test",
            provider_request_id=f"promotion-{request.source_hash[:12]}",
            input_tokens=10,
            output_tokens=20,
            latency_ms=1,
            prompt_version=BRANCH_PROMPT_VERSION,
        )


@pytest.fixture()
def seeded(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'story-path-promotion.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    db = factory()
    user = User(
        email="promotion@example.com",
        password_hash="hash",
        display_name="Promotion",
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Promotion project",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    root = StoryPath(project_id=project.id, title="Main path")
    db.add(root)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "A station with two signal gates."},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=root.id,
        bible_revision_id=bible.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": index,
                "title": f"Chapter {index}",
                "summary": f"Summary {index}",
                "conflict": f"Conflict {index}",
                "characters": ["Lin"],
                "scene": "Station",
                "emotion": "Tense",
                "visual_keywords": ["signal"],
            }
            for index in range(1, 4)
        ],
        user_id=user.id,
    )
    outline_result = activate_story_path_outline_head(
        db,
        story_path_id=root.id,
        revision_id=outline.id,
        expected_lock_version=2,
    )
    base_state = {
        "route": "main",
        "inventory": {"key": True, "coins": 2},
        "remove_me": "delete on branch",
    }
    snapshot = StateSnapshot(
        project_id=project.id,
        state_hash=content_hash(base_state),
        state_json=base_state,
    )
    db.add(snapshot)
    db.flush()
    revisions: list[ChapterRevision] = []
    for placement in outline_result.path_chapters:
        text = f"Reviewed parent chapter {placement.display_index}."
        revision = ChapterRevision(
            project_id=project.id,
            chapter_slot_id=placement.chapter_slot_id,
            created_for_story_path_id=root.id,
            chapter_index=placement.display_index,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            state_snapshot_id=snapshot.id,
            revision_no=1,
            source_hash=str(placement.display_index) * 64,
            context_manifest={"path_chapter_id": placement.id},
            context_hash=str(placement.display_index + 3) * 64,
            content_hash=content_hash(text),
            content=text,
            status="ready",
            created_by=user.id,
        )
        db.add(revision)
        db.flush()
        activate_path_chapter_revision_head(
            db,
            path_chapter_id=placement.id,
            revision_id=revision.id,
            expected_lock_version=placement.lock_version,
        )
        revisions.append(revision)
    checkpoint = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key="signal-gate",
        content_revision_id=revisions[1].id,
        payload={"question": "Which signal gate?"},
    )
    db.add(checkpoint)
    db.commit()
    try:
        yield {
            "db": db,
            "factory": factory,
            "user": user,
            "project": project,
            "root": root,
            "bible": bible,
            "outline": outline,
            "placements": outline_result.path_chapters,
            "revisions": revisions,
            "snapshot": snapshot,
            "checkpoint": checkpoint,
        }
    finally:
        db.close()
        engine.dispose()


def _generate_set_for(
    seeded,
    *,
    story_path: StoryPath,
    checkpoint: StoryNode,
    chapter: ChapterRevision,
    snapshot: StateSnapshot,
    key: str,
    candidate_count: int = 2,
) -> CandidateSetRevision:
    db = seeded["db"]
    task, _created, _context = create_candidate_set_generation_task(
        db,
        user_id=seeded["user"].id,
        story_path_id=story_path.id,
        checkpoint_node_id=checkpoint.id,
        chapter_revision_id=chapter.id,
        state_snapshot_id=snapshot.id,
        candidate_count=candidate_count,
        instructions=None,
        idempotency_key=key,
    )
    db.commit()
    asyncio.run(
        execute_branch_generation_task(
            task.id,
            provider=_PromotionProvider(),
            worker_id=f"worker-{key}",
            session_factory=seeded["factory"],
        )
    )
    db.expire_all()
    return db.query(CandidateSetRevision).filter_by(generation_task_id=task.id).one()


def _generate_set(seeded, *, key: str) -> CandidateSetRevision:
    return _generate_set_for(
        seeded,
        story_path=seeded["root"],
        checkpoint=seeded["checkpoint"],
        chapter=seeded["revisions"][1],
        snapshot=seeded["snapshot"],
        key=key,
    )


def _activate_set(seeded, revision: CandidateSetRevision, *, expected: int = 1):
    return activate_candidate_set_head(
        seeded["db"],
        story_path_id=seeded["root"].id,
        checkpoint_node_id=seeded["checkpoint"].id,
        revision_id=revision.id,
        expected_lock_version=expected,
    )


def _candidate(seeded, revision: CandidateSetRevision, option_key: str) -> BranchCandidate:
    return (
        seeded["db"]
        .query(BranchCandidate)
        .filter_by(candidate_set_revision_id=revision.id, option_key=option_key)
        .one()
    )


def _path_rows(db, path: StoryPath) -> list[StoryPathChapter]:
    return (
        db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id)
        .order_by(StoryPathChapter.display_index)
        .all()
    )


def _activate_outline_through(seeded, path: StoryPath, chapter_count: int):
    existing = _path_rows(seeded["db"], path)
    outline = create_story_path_outline_revision(
        seeded["db"],
        story_path_id=path.id,
        bible_revision_id=seeded["bible"].id,
        chapters=[
            {
                "story_path_chapter_id": (
                    existing[index - 1].id if index <= len(existing) else None
                ),
                "display_index": index,
                "title": f"{path.title} chapter {index}",
                "summary": f"{path.title} summary {index}",
                "characters": ["Lin"],
                "visual_keywords": ["branch"],
            }
            for index in range(1, chapter_count + 1)
        ],
        user_id=seeded["user"].id,
    )
    return activate_story_path_outline_head(
        seeded["db"],
        story_path_id=path.id,
        revision_id=outline.id,
        expected_lock_version=2,
    )


def _write_and_activate_chapter(seeded, target: StoryPathChapter, content: str):
    source_refs = build_chapter_generation_source(
        seeded["db"],
        path_chapter_id=target.id,
    )
    context = resolve_chapter_generation_source(
        seeded["db"],
        task_project_id=seeded["project"].id,
        source_refs=source_refs,
    )
    revision = create_story_path_chapter_revision(
        seeded["db"],
        context=context,
        content=content,
        user_id=seeded["user"].id,
    )
    activation = activate_path_chapter_revision_head(
        seeded["db"],
        path_chapter_id=target.id,
        revision_id=revision.id,
        expected_lock_version=target.lock_version,
    )
    return context, revision, activation


def test_promotion_creates_child_prefix_state_and_provenance_atomically(seeded):
    revision = _generate_set(seeded, key="promotion-source")
    _activate_set(seeded, revision)
    candidate = _candidate(seeded, revision, "left_gate")
    revision_hash = revision.content_hash

    result = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=candidate.id,
        idempotency_key="promote-left",
        title="Left signal route",
        expected_project_id=seeded["project"].id,
    )
    assert result.created is True
    child = result.path
    assert StoryPathRead.model_validate(child).title == "Left signal route"
    assert child.parent_path_id == seeded["root"].id
    assert child.fork_path_chapter_id == seeded["placements"][1].id
    assert child.fork_checkpoint_node_id == seeded["checkpoint"].id
    assert child.fork_candidate_id == candidate.id

    state = seeded["db"].query(StateSnapshot).filter_by(id=child.base_state_snapshot_id).one()
    assert state.parent_snapshot_id == seeded["snapshot"].id
    assert state.state_json == {
        "route": "left",
        "inventory": {"key": True, "coins": 3},
    }
    child_rows = (
        seeded["db"]
        .query(StoryPathChapter)
        .filter_by(story_path_id=child.id)
        .order_by(StoryPathChapter.display_index)
        .all()
    )
    assert len(child_rows) == 2
    assert [row.chapter_slot_id for row in child_rows] == [
        placement.chapter_slot_id for placement in seeded["placements"][:2]
    ]
    assert [row.current_revision_id for row in child_rows] == [
        chapter.id for chapter in seeded["revisions"][:2]
    ]
    assert [row.inherited_from_path_chapter_id for row in child_rows] == [
        placement.id for placement in seeded["placements"][:2]
    ]
    assert child_rows[0].predecessor_path_chapter_id is None
    assert child_rows[1].predecessor_path_chapter_id == child_rows[0].id
    assert (
        seeded["db"]
        .query(StoryPathOutlineHead)
        .filter_by(story_path_id=child.id)
        .one()
        .current_revision_id
        is None
    )
    assert seeded["db"].query(StoryPathPromotionRecord).count() == 1
    assert seeded["db"].query(BranchEdge).count() == 0
    assert revision.content_hash == revision_hash
    assert seeded["db"].query(CandidateSetHead).one().current_revision_id == revision.id


def test_promotion_replay_and_duplicate_candidate_are_idempotent(seeded):
    revision = _generate_set(seeded, key="promotion-idempotency-source")
    _activate_set(seeded, revision)
    left = _candidate(seeded, revision, "left_gate")
    right = _candidate(seeded, revision, "right_gate")

    first = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=left.id,
        idempotency_key="promote-idempotent",
        title="Stable branch title",
    )
    replay = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=left.id,
        idempotency_key="promote-idempotent",
        title="Stable branch title",
    )
    assert first.created is True
    assert replay.created is False
    assert replay.path.id == first.path.id

    with pytest.raises(AppError) as reused_key:
        promote_candidate_to_story_path(
            seeded["db"],
            user_id=seeded["user"].id,
            candidate_id=right.id,
            idempotency_key="promote-idempotent",
            title="Stable branch title",
        )
    assert reused_key.value.code == "idempotency_key.conflict"

    duplicate = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=left.id,
        idempotency_key="promote-same-candidate-new-key",
        title="Stable branch title",
    )
    assert duplicate.created is False
    assert duplicate.path.id == first.path.id
    with pytest.raises(AppError) as retitled:
        promote_candidate_to_story_path(
            seeded["db"],
            user_id=seeded["user"].id,
            candidate_id=left.id,
            idempotency_key="promote-retitle",
            title="Conflicting title",
        )
    assert retitled.value.code == "branch_candidate.already_promoted"
    assert seeded["db"].query(StoryPath).count() == 2
    assert seeded["db"].query(StoryPathPromotionRecord).count() == 2


def test_candidate_must_belong_to_current_reviewed_set(seeded):
    first = _generate_set(seeded, key="promotion-old-set")
    _activate_set(seeded, first)
    old_candidate = _candidate(seeded, first, "left_gate")
    second = _generate_set(seeded, key="promotion-current-set")
    _activate_set(seeded, second, expected=2)

    with pytest.raises(AppError) as inactive:
        promote_candidate_to_story_path(
            seeded["db"],
            user_id=seeded["user"].id,
            candidate_id=old_candidate.id,
            idempotency_key="promote-inactive",
        )
    assert inactive.value.code == "branch_candidate.not_active"
    assert seeded["db"].query(StoryPath).count() == 1
    assert seeded["db"].query(StoryPathPromotionRecord).count() == 0


def test_promotion_savepoint_removes_partial_state_and_path(seeded, monkeypatch):
    revision = _generate_set(seeded, key="promotion-rollback-source")
    _activate_set(seeded, revision)
    candidate = _candidate(seeded, revision, "left_gate")
    original_snapshot_count = seeded["db"].query(StateSnapshot).count()

    def _fail_prefix(*_args, **_kwargs):
        raise RuntimeError("prefix failure")

    monkeypatch.setattr(
        "app.application.story_path_promotion_service._create_child_prefix",
        _fail_prefix,
    )
    with pytest.raises(RuntimeError, match="prefix failure"):
        promote_candidate_to_story_path(
            seeded["db"],
            user_id=seeded["user"].id,
            candidate_id=candidate.id,
            idempotency_key="promote-rollback",
        )
    seeded["db"].commit()
    assert seeded["db"].query(StoryPath).count() == 1
    assert seeded["db"].query(StoryPathPromotionRecord).count() == 0
    assert seeded["db"].query(StateSnapshot).count() == original_snapshot_count


def test_promoted_branch_choice_is_frozen_into_later_chapter_context(seeded):
    revision = _generate_set(seeded, key="promotion-context-source")
    _activate_set(seeded, revision)
    candidate = _candidate(seeded, revision, "left_gate")
    child = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=candidate.id,
        idempotency_key="promote-context",
    ).path
    child_rows = (
        seeded["db"]
        .query(StoryPathChapter)
        .filter_by(story_path_id=child.id)
        .order_by(StoryPathChapter.display_index)
        .all()
    )
    child_outline = create_story_path_outline_revision(
        seeded["db"],
        story_path_id=child.id,
        bible_revision_id=seeded["bible"].id,
        chapters=[
            {
                "story_path_chapter_id": child_rows[index - 1].id if index <= 2 else None,
                "display_index": index,
                "title": f"Child chapter {index}",
                "summary": f"Child summary {index}",
                "characters": ["Lin"],
                "visual_keywords": ["branch"],
            }
            for index in range(1, 4)
        ],
        user_id=seeded["user"].id,
    )
    activated = activate_story_path_outline_head(
        seeded["db"],
        story_path_id=child.id,
        revision_id=child_outline.id,
        expected_lock_version=2,
    )
    target = activated.path_chapters[-1]
    source_refs = build_chapter_generation_source(
        seeded["db"],
        path_chapter_id=target.id,
    )
    context = resolve_chapter_generation_source(
        seeded["db"],
        task_project_id=seeded["project"].id,
        source_refs=source_refs,
    )

    assert context.context_manifest["fork_choice"]["candidate_id"] == candidate.id
    assert context.context_manifest["fork_choice"]["candidate_set_revision_id"] == revision.id
    assert context.previous_chapters[-1] == {
        "kind": "branch_choice",
        "candidate_id": candidate.id,
        "candidate_set_revision_id": revision.id,
        "chapter_index": 2,
        "display_index": 2,
        "content_hash": context.context_manifest["fork_choice"]["candidate_content_hash"],
        "summary": "Lin enters the left gate under a red signal.",
    }
    assert context.state == {
        "route": "left",
        "inventory": {"key": True, "coins": 3},
    }


def test_sibling_paths_isolate_state_placements_and_post_fork_heads(seeded):
    candidate_set = _generate_set(seeded, key="sibling-source")
    _activate_set(seeded, candidate_set)
    left_candidate = _candidate(seeded, candidate_set, "left_gate")
    right_candidate = _candidate(seeded, candidate_set, "right_gate")
    left = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=left_candidate.id,
        idempotency_key="promote-sibling-left",
        title="Left sibling",
    ).path
    right = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=right_candidate.id,
        idempotency_key="promote-sibling-right",
        title="Right sibling",
    ).path

    left_prefix = _path_rows(seeded["db"], left)
    right_prefix = _path_rows(seeded["db"], right)
    assert {row.id for row in left_prefix}.isdisjoint(row.id for row in right_prefix)
    assert [row.chapter_slot_id for row in left_prefix] == [
        row.chapter_slot_id for row in right_prefix
    ]
    assert [row.current_revision_id for row in left_prefix] == [
        row.current_revision_id for row in right_prefix
    ]
    left_state = seeded["db"].get(StateSnapshot, left.base_state_snapshot_id)
    right_state = seeded["db"].get(StateSnapshot, right.base_state_snapshot_id)
    assert left_state.state_json["route"] == "left"
    assert right_state.state_json["route"] == "right"
    assert left_state.id != right_state.id

    left_outline = _activate_outline_through(seeded, left, 3)
    right_outline = _activate_outline_through(seeded, right, 3)
    left_target = left_outline.path_chapters[-1]
    right_target = right_outline.path_chapters[-1]
    assert left_target.chapter_slot_id != right_target.chapter_slot_id

    left_context, left_revision, _left_head = _write_and_activate_chapter(
        seeded,
        left_target,
        "Only the left sibling follows the red signal.",
    )
    right_context, right_revision, _right_head = _write_and_activate_chapter(
        seeded,
        right_target,
        "Only the right sibling follows the blue signal.",
    )
    assert left_context.previous_chapters[-1]["candidate_id"] == left_candidate.id
    assert right_context.previous_chapters[-1]["candidate_id"] == right_candidate.id
    assert left_context.state["route"] == "left"
    assert right_context.state["route"] == "right"
    assert left_target.current_revision_id == left_revision.id
    assert right_target.current_revision_id == right_revision.id

    _left_edit_context, left_edit, _left_edit_head = _write_and_activate_chapter(
        seeded,
        left_target,
        "The left sibling alone receives a revised third chapter.",
    )
    seeded["db"].refresh(right_target)
    assert left_target.current_revision_id == left_edit.id
    assert right_target.current_revision_id == right_revision.id
    assert [row.current_revision_id for row in seeded["placements"]] == [
        revision.id for revision in seeded["revisions"]
    ]


def test_parent_reorder_does_not_change_promoted_child_fork_context(seeded):
    candidate_set = _generate_set(seeded, key="parent-reorder-source")
    _activate_set(seeded, candidate_set)
    candidate = _candidate(seeded, candidate_set, "left_gate")
    child = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=candidate.id,
        idempotency_key="parent-reorder-child",
    ).path
    child_outline = _activate_outline_through(seeded, child, 3)
    child_target = child_outline.path_chapters[-1]
    frozen_source = build_chapter_generation_source(
        seeded["db"],
        path_chapter_id=child_target.id,
    )

    parent_order = [
        seeded["placements"][0],
        seeded["placements"][2],
        seeded["placements"][1],
    ]
    reordered_outline = create_story_path_outline_revision(
        seeded["db"],
        story_path_id=seeded["root"].id,
        bible_revision_id=seeded["bible"].id,
        chapters=[
            {
                "story_path_chapter_id": placement.id,
                "display_index": index,
                "title": f"Reordered parent chapter {index}",
                "summary": f"Reordered parent summary {index}",
                "characters": ["Lin"],
                "visual_keywords": ["parent-reorder"],
            }
            for index, placement in enumerate(parent_order, start=1)
        ],
        user_id=seeded["user"].id,
    )
    parent_head = seeded["db"].query(StoryPathOutlineHead).filter_by(
        story_path_id=seeded["root"].id
    ).one()
    activate_story_path_outline_head(
        seeded["db"],
        story_path_id=seeded["root"].id,
        revision_id=reordered_outline.id,
        expected_lock_version=parent_head.lock_version,
    )
    seeded["db"].commit()

    frozen = resolve_chapter_generation_source(
        seeded["db"],
        task_project_id=seeded["project"].id,
        source_refs=frozen_source,
    )
    fresh = resolve_chapter_generation_source(
        seeded["db"],
        task_project_id=seeded["project"].id,
        source_refs=build_chapter_generation_source(
            seeded["db"],
            path_chapter_id=child_target.id,
        ),
    )

    assert frozen.context_manifest["fork_choice"]["display_index"] == 2
    assert fresh.context_manifest["fork_choice"]["display_index"] == 2
    assert frozen.previous_chapters[-1]["candidate_id"] == candidate.id
    assert fresh.previous_chapters[-1]["candidate_id"] == candidate.id


def test_three_candidates_create_isolated_display_five_artifact_chains(seeded):
    candidate_set = _generate_set_for(
        seeded,
        story_path=seeded["root"],
        checkpoint=seeded["checkpoint"],
        chapter=seeded["revisions"][1],
        snapshot=seeded["snapshot"],
        key="three-path-acceptance",
        candidate_count=3,
    )
    _activate_set(seeded, candidate_set)
    candidates = [
        _candidate(seeded, candidate_set, option_key)
        for option_key in ("left_gate", "right_gate", "center_gate")
    ]
    paths = [
        promote_candidate_to_story_path(
            seeded["db"],
            user_id=seeded["user"].id,
            candidate_id=candidate.id,
            idempotency_key=f"promote-three-{candidate.option_key}",
            title=f"{candidate.option_key} path",
        ).path
        for candidate in candidates
    ]

    assert len({candidate.id for candidate in candidates}) == 3
    assert {candidate.candidate_set_revision_id for candidate in candidates} == {
        candidate_set.id
    }
    assert len({path.id for path in paths}) == 3
    assert {path.parent_path_id for path in paths} == {seeded["root"].id}
    assert {path.fork_candidate_id for path in paths} == {
        candidate.id for candidate in candidates
    }

    artifacts: dict[str, dict[str, object]] = {}
    for path in paths:
        placements = _activate_outline_through(seeded, path, 6).path_chapters
        by_index = {placement.display_index: placement for placement in placements}
        chapter_five_revision = None
        for display_index in range(3, 6):
            _context, chapter_revision, _activation = _write_and_activate_chapter(
                seeded,
                by_index[display_index],
                f"{path.title} owns chapter {display_index}.",
            )
            if display_index == 5:
                chapter_five_revision = chapter_revision
        assert chapter_five_revision is not None

        script_json = {
            "schema_version": "script-ir-v1",
            "path_id": path.id,
            "chapter_revision_id": chapter_five_revision.id,
            "blocks": [],
        }
        script = ChapterScriptRevision(
            project_id=seeded["project"].id,
            chapter_index=5,
            chapter_revision_id=chapter_five_revision.id,
            bible_revision_id=chapter_five_revision.bible_revision_id,
            outline_revision_id=chapter_five_revision.outline_revision_id,
            revision_no=1,
            source_hash=content_hash(
                {"chapter_revision_id": chapter_five_revision.id}
            ),
            script_hash=content_hash(script_json),
            script_json=script_json,
            coverage_json={"coverage_ratio": 1.0},
            schema_version="script-ir-v1",
            generator_version="plan-acceptance-v1",
            status="ready",
            created_by=seeded["user"].id,
        )
        seeded["db"].add(script)
        seeded["db"].flush()
        activate_chapter_script_head(
            seeded["db"],
            chapter_revision_id=chapter_five_revision.id,
            revision_id=script.id,
            expected_lock_version=1,
        )

        binding_manifest = {
            "schema_version": "vngraph-binding-manifest-v1",
            "script_revision_id": script.id,
            "assets": [],
            "voice_lines": [],
        }
        graph_json = {
            "Nodes": [],
            "Meta": {"script_revision_id": script.id, "path_id": path.id},
        }
        graph = VNGraphRevision(
            project_id=seeded["project"].id,
            chapter_index=5,
            chapter_revision_id=chapter_five_revision.id,
            script_revision_id=script.id,
            revision_no=1,
            binding_manifest=binding_manifest,
            binding_manifest_hash=content_hash(binding_manifest),
            source_manifest_hash=content_hash(binding_manifest),
            graph_hash=content_hash(graph_json),
            graph_json=graph_json,
            schema_version="1",
            compiler_version="plan-acceptance-v1",
            tachi_policy_version="speaker-focus-v2",
            status="ready",
        )
        seeded["db"].add(graph)
        seeded["db"].flush()
        activate_vn_graph_head(
            seeded["db"],
            script_revision_id=script.id,
            revision_id=graph.id,
            expected_lock_version=1,
        )
        artifacts[path.id] = {
            "chapter_five": by_index[5],
            "chapter_revision": chapter_five_revision,
            "script": script,
            "graph": graph,
            "chapter_six": by_index[6],
        }

    assert {artifact["chapter_five"].display_index for artifact in artifacts.values()} == {5}
    assert len({artifact["chapter_five"].id for artifact in artifacts.values()}) == 3
    assert len({artifact["chapter_five"].chapter_slot_id for artifact in artifacts.values()}) == 3
    assert len({artifact["chapter_revision"].id for artifact in artifacts.values()}) == 3
    assert len({artifact["script"].id for artifact in artifacts.values()}) == 3
    assert len({artifact["graph"].id for artifact in artifacts.values()}) == 3

    fifth_ids = {
        path_id: artifact["chapter_five"].id
        for path_id, artifact in artifacts.items()
    }
    for path in paths:
        source_refs = build_chapter_generation_source(
            seeded["db"],
            path_chapter_id=artifacts[path.id]["chapter_six"].id,
        )
        context = resolve_chapter_generation_source(
            seeded["db"],
            task_project_id=seeded["project"].id,
            source_refs=source_refs,
        )
        ancestor_ids = {
            item["path_chapter_id"]
            for item in context.context_manifest["ancestors"]
        }
        assert fifth_ids[path.id] in ancestor_ids
        assert ancestor_ids.isdisjoint(
            fifth_id
            for other_path_id, fifth_id in fifth_ids.items()
            if other_path_id != path.id
        )


def test_child_path_can_branch_again_with_exact_multilevel_prefix(seeded):
    root_set = _generate_set(seeded, key="multilevel-root-source")
    _activate_set(seeded, root_set)
    root_candidate = _candidate(seeded, root_set, "left_gate")
    child = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=root_candidate.id,
        idempotency_key="promote-multilevel-child",
        title="First-level child",
    ).path
    child_outline = _activate_outline_through(seeded, child, 3)
    child_context, child_revision, _child_head = _write_and_activate_chapter(
        seeded,
        child_outline.path_chapters[-1],
        "The first-level child reaches a second junction.",
    )
    nested_checkpoint = StoryNode(
        project_id=seeded["project"].id,
        node_type="checkpoint",
        checkpoint_key="second-junction",
        content_revision_id=child_revision.id,
        payload={"question": "Which tunnel?"},
    )
    seeded["db"].add(nested_checkpoint)
    seeded["db"].commit()
    child_state = seeded["db"].get(StateSnapshot, child.base_state_snapshot_id)
    nested_set = _generate_set_for(
        seeded,
        story_path=child,
        checkpoint=nested_checkpoint,
        chapter=child_revision,
        snapshot=child_state,
        key="multilevel-child-source",
    )
    activate_candidate_set_head(
        seeded["db"],
        story_path_id=child.id,
        checkpoint_node_id=nested_checkpoint.id,
        revision_id=nested_set.id,
        expected_lock_version=1,
    )
    nested_candidate = _candidate(seeded, nested_set, "right_gate")
    grandchild = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=nested_candidate.id,
        idempotency_key="promote-multilevel-grandchild",
        title="Second-level child",
    ).path

    child_rows = _path_rows(seeded["db"], child)
    grandchild_rows = _path_rows(seeded["db"], grandchild)
    assert grandchild.parent_path_id == child.id
    assert grandchild.fork_path_chapter_id == child_rows[-1].id
    assert [row.inherited_from_path_chapter_id for row in grandchild_rows] == [
        row.id for row in child_rows
    ]
    assert [row.current_revision_id for row in grandchild_rows] == [
        seeded["revisions"][0].id,
        seeded["revisions"][1].id,
        child_revision.id,
    ]
    grandchild_state = seeded["db"].get(
        StateSnapshot,
        grandchild.base_state_snapshot_id,
    )
    assert grandchild_state.parent_snapshot_id == child_state.id
    assert grandchild_state.state_json == {
        "route": "right",
        "inventory": {"key": True, "coins": 1},
    }

    grandchild_outline = _activate_outline_through(seeded, grandchild, 4)
    grandchild_source = build_chapter_generation_source(
        seeded["db"],
        path_chapter_id=grandchild_outline.path_chapters[-1].id,
    )
    grandchild_context = resolve_chapter_generation_source(
        seeded["db"],
        task_project_id=seeded["project"].id,
        source_refs=grandchild_source,
    )
    assert child_context.context_manifest["fork_choice"]["candidate_id"] == root_candidate.id
    assert grandchild_context.context_manifest["fork"]["parent_path_id"] == child.id
    assert (
        grandchild_context.context_manifest["fork_choice"]["candidate_id"]
        == nested_candidate.id
    )
    assert [item["chapter_revision_id"] for item in grandchild_context.previous_chapters[:-1]] == [
        seeded["revisions"][0].id,
        seeded["revisions"][1].id,
        child_revision.id,
    ]
    assert grandchild_context.previous_chapters[-1]["candidate_id"] == nested_candidate.id
    assert seeded["db"].query(BranchEdge).count() == 0


def test_promotion_idempotency_survives_commit_and_new_session(seeded):
    candidate_set = _generate_set(seeded, key="durable-idempotency-source")
    _activate_set(seeded, candidate_set)
    candidate = _candidate(seeded, candidate_set, "left_gate")
    first = promote_candidate_to_story_path(
        seeded["db"],
        user_id=seeded["user"].id,
        candidate_id=candidate.id,
        idempotency_key="durable-promotion",
    )
    seeded["db"].commit()

    with seeded["factory"]() as other:
        replay = promote_candidate_to_story_path(
            other,
            user_id=seeded["user"].id,
            candidate_id=candidate.id,
            idempotency_key="durable-promotion",
        )
        duplicate = promote_candidate_to_story_path(
            other,
            user_id=seeded["user"].id,
            candidate_id=candidate.id,
            idempotency_key="durable-promotion-second-key",
        )
        other.commit()

    assert replay.created is False
    assert duplicate.created is False
    assert replay.path.id == first.path.id == duplicate.path.id
    seeded["db"].expire_all()
    assert seeded["db"].query(StoryPath).count() == 2
    assert seeded["db"].query(StoryPathPromotionRecord).count() == 2
