"""Review queries and optimistic activation for immutable candidate sets."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.revision_head_service import (
    RevisionHeadValue,
    head_value,
    raise_head_version_conflict,
)
from app.core.errors import AppError
from app.models_v2 import (
    BranchCandidate,
    CandidateSetHead,
    CandidateSetRevision,
    ChapterRevision,
    StateSnapshot,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    utcnow,
)


_SELECTABLE_CHAPTER_STATUSES = frozenset({"ready", "complete"})


@dataclass(frozen=True)
class CandidateSetCandidateValue:
    id: str
    candidate_set_revision_id: str
    option_key: str
    preview_text: str
    state_delta: dict[str, object]


@dataclass(frozen=True)
class CandidateSetHeadActivation:
    revision: CandidateSetRevision
    head: RevisionHeadValue


@dataclass(frozen=True)
class SelectableCandidateSet:
    path: StoryPath
    checkpoint: StoryNode
    placement: StoryPathChapter
    chapter: ChapterRevision
    revision: CandidateSetRevision
    candidates: tuple[CandidateSetCandidateValue, ...]


def _path_checkpoint(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
    for_update: bool = False,
) -> tuple[StoryPath, StoryNode, StoryPathChapter, ChapterRevision]:
    path_query = db.query(StoryPath).filter(StoryPath.id == story_path_id)
    if for_update:
        path_query = path_query.with_for_update()
    path = path_query.one_or_none()
    if not path:
        raise AppError(
            code="story_path.not_found",
            message="StoryPath does not exist",
            status_code=404,
        )
    checkpoint_query = db.query(StoryNode).filter(
        StoryNode.id == checkpoint_node_id,
        StoryNode.project_id == path.project_id,
        StoryNode.node_type == "checkpoint",
    )
    if for_update:
        checkpoint_query = checkpoint_query.with_for_update()
    checkpoint = checkpoint_query.one_or_none()
    chapter = None
    if checkpoint and checkpoint.content_revision_id:
        chapter_query = db.query(ChapterRevision).filter(
            ChapterRevision.id == checkpoint.content_revision_id,
            ChapterRevision.project_id == path.project_id,
        )
        if for_update:
            chapter_query = chapter_query.with_for_update()
        chapter = chapter_query.one_or_none()
    placement = None
    if chapter and chapter.chapter_slot_id:
        placement_query = db.query(StoryPathChapter).filter(
            StoryPathChapter.story_path_id == path.id,
            StoryPathChapter.chapter_slot_id == chapter.chapter_slot_id,
            StoryPathChapter.status == "active",
        )
        if for_update:
            placement_query = placement_query.with_for_update()
        placement = placement_query.one_or_none()
    if not checkpoint or not chapter or not placement:
        raise AppError(
            code="checkpoint.not_found",
            message="Checkpoint does not belong to this StoryPath",
            status_code=404,
        )
    return path, checkpoint, placement, chapter


def _assert_head_integrity(db: Session, head: CandidateSetHead) -> None:
    if not head.current_revision_id:
        return
    revision = (
        db.query(CandidateSetRevision.id)
        .filter(
            CandidateSetRevision.id == head.current_revision_id,
            CandidateSetRevision.story_path_id == head.story_path_id,
            CandidateSetRevision.checkpoint_node_id == head.checkpoint_node_id,
        )
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="candidate_set_head.invalid",
            message="CandidateSet Head points outside its checkpoint",
            status_code=409,
        )


def get_or_create_candidate_set_head(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
    for_update: bool = False,
) -> CandidateSetHead:
    """Return a discoverable Head, including before the first selection."""

    _path_checkpoint(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        for_update=for_update,
    )
    query = db.query(CandidateSetHead).filter(
        CandidateSetHead.story_path_id == story_path_id,
        CandidateSetHead.checkpoint_node_id == checkpoint_node_id,
    )
    if for_update:
        query = query.with_for_update()
    head = query.one_or_none()
    if head:
        _assert_head_integrity(db, head)
        return head
    if not for_update:
        return get_or_create_candidate_set_head(
            db,
            story_path_id=story_path_id,
            checkpoint_node_id=checkpoint_node_id,
            for_update=True,
        )

    head = CandidateSetHead(
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        current_revision_id=None,
        lock_version=1,
    )
    db.add(head)
    db.flush()
    return head


def get_candidate_set_head_value(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
) -> RevisionHeadValue:
    return head_value(
        get_or_create_candidate_set_head(
            db,
            story_path_id=story_path_id,
            checkpoint_node_id=checkpoint_node_id,
        )
    )


def list_candidate_set_revisions(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
) -> list[CandidateSetRevision]:
    _path_checkpoint(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
    )
    return (
        db.query(CandidateSetRevision)
        .filter(
            CandidateSetRevision.story_path_id == story_path_id,
            CandidateSetRevision.checkpoint_node_id == checkpoint_node_id,
        )
        .order_by(CandidateSetRevision.revision_no.desc())
        .all()
    )


def get_candidate_set_revision(
    db: Session,
    *,
    candidate_set_revision_id: str,
    project_id: int | None = None,
) -> CandidateSetRevision:
    query = db.query(CandidateSetRevision).filter(
        CandidateSetRevision.id == candidate_set_revision_id
    )
    if project_id is not None:
        query = query.filter(CandidateSetRevision.project_id == project_id)
    revision = query.one_or_none()
    if not revision:
        raise AppError(
            code="candidate_set_revision.not_found",
            message="CandidateSet revision does not exist",
            status_code=404,
        )
    return revision


def _invalid_payload() -> AppError:
    return AppError(
        code="candidate_set_revision.payload_invalid",
        message="CandidateSet revision payload is incomplete or corrupted",
        status_code=409,
    )


def list_candidate_set_candidates(
    db: Session,
    *,
    candidate_set_revision_id: str,
    project_id: int | None = None,
) -> list[CandidateSetCandidateValue]:
    revision = get_candidate_set_revision(
        db,
        candidate_set_revision_id=candidate_set_revision_id,
        project_id=project_id,
    )
    manifest = revision.candidates_json
    if (
        not isinstance(manifest, list)
        or len(manifest) != revision.candidate_count
        or content_hash(manifest) != revision.content_hash
    ):
        raise _invalid_payload()
    rows = (
        db.query(BranchCandidate)
        .filter(BranchCandidate.candidate_set_revision_id == revision.id)
        .all()
    )
    by_id = {row.id: row for row in rows}
    ordered: list[CandidateSetCandidateValue] = []
    for ordinal, item in enumerate(manifest):
        if not isinstance(item, dict):
            raise _invalid_payload()
        candidate = by_id.get(item.get("id"))
        state_delta = item.get("state_delta")
        if (
            item.get("ordinal") != ordinal
            or not isinstance(item.get("option_key"), str)
            or not isinstance(item.get("preview_text"), str)
            or not isinstance(state_delta, dict)
            or not candidate
            or candidate.project_id != revision.project_id
            or candidate.checkpoint_node_id != revision.checkpoint_node_id
            or candidate.option_key != item.get("option_key")
            or candidate.preview_node_id != item.get("preview_node_id")
            or (candidate.state_delta or {}) != state_delta
        ):
            raise _invalid_payload()
        ordered.append(
            CandidateSetCandidateValue(
                id=candidate.id,
                candidate_set_revision_id=revision.id,
                option_key=candidate.option_key,
                preview_text=item["preview_text"],
                state_delta=deepcopy(state_delta),
            )
        )
    if len(rows) != len(ordered):
        raise _invalid_payload()
    return ordered


def _assert_revision_is_selectable(
    db: Session,
    *,
    path: StoryPath,
    checkpoint: StoryNode,
    placement: StoryPathChapter,
    checkpoint_chapter: ChapterRevision,
    revision: CandidateSetRevision,
) -> tuple[CandidateSetCandidateValue, ...]:
    candidates = tuple(
        list_candidate_set_candidates(
            db,
            candidate_set_revision_id=revision.id,
            project_id=path.project_id,
        )
    )
    if (
        revision.chapter_revision_id != checkpoint_chapter.id
        or checkpoint.content_revision_id != revision.chapter_revision_id
        or placement.current_revision_id != revision.chapter_revision_id
        or checkpoint_chapter.status not in _SELECTABLE_CHAPTER_STATUSES
        or content_hash(checkpoint_chapter.content) != checkpoint_chapter.content_hash
    ):
        raise AppError(
            code="candidate_set_revision.stale_source",
            message="CandidateSet source chapter is no longer selected on this StoryPath",
            status_code=409,
        )
    snapshot = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == revision.state_snapshot_id,
            StateSnapshot.project_id == path.project_id,
        )
        .one_or_none()
    )
    if not snapshot or content_hash(snapshot.state_json) != snapshot.state_hash:
        raise AppError(
            code="candidate_set_revision.stale_source",
            message="CandidateSet source state is unavailable or corrupted",
            status_code=409,
        )
    if revision.parent_revision_id:
        parent = (
            db.query(CandidateSetRevision.id)
            .filter(
                CandidateSetRevision.id == revision.parent_revision_id,
                CandidateSetRevision.story_path_id == path.id,
                CandidateSetRevision.checkpoint_node_id == checkpoint.id,
            )
            .one_or_none()
        )
        if not parent:
            raise AppError(
                code="candidate_set_revision.parent_invalid",
                message="CandidateSet editorial parent belongs to another checkpoint",
                status_code=409,
            )
    return candidates


def resolve_selectable_candidate_set(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
    revision_id: str,
    for_update: bool = False,
) -> SelectableCandidateSet:
    path, checkpoint, placement, checkpoint_chapter = _path_checkpoint(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        for_update=for_update,
    )
    if path.status != "active":
        raise AppError(
            code="story_path.archived",
            message="Archived StoryPath cannot be edited",
            status_code=409,
        )
    revision = (
        db.query(CandidateSetRevision)
        .filter(
            CandidateSetRevision.id == revision_id,
            CandidateSetRevision.project_id == path.project_id,
            CandidateSetRevision.story_path_id == path.id,
            CandidateSetRevision.checkpoint_node_id == checkpoint.id,
        )
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="candidate_set_revision.not_found",
            message="CandidateSet revision does not belong to this checkpoint",
            status_code=404,
        )
    candidates = _assert_revision_is_selectable(
        db,
        path=path,
        checkpoint=checkpoint,
        placement=placement,
        checkpoint_chapter=checkpoint_chapter,
        revision=revision,
    )
    return SelectableCandidateSet(
        path=path,
        checkpoint=checkpoint,
        placement=placement,
        chapter=checkpoint_chapter,
        revision=revision,
        candidates=candidates,
    )


def activate_candidate_set_head(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
    revision_id: str,
    expected_lock_version: int,
) -> CandidateSetHeadActivation:
    if (
        isinstance(expected_lock_version, bool)
        or not isinstance(expected_lock_version, int)
        or expected_lock_version < 1
    ):
        raise AppError(
            code="precondition.invalid",
            message="Expected Head lock version must be a positive integer",
            status_code=400,
        )
    selection = resolve_selectable_candidate_set(
        db,
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        revision_id=revision_id,
        for_update=True,
    )
    head = get_or_create_candidate_set_head(
        db,
        story_path_id=selection.path.id,
        checkpoint_node_id=selection.checkpoint.id,
        for_update=True,
    )
    same_revision = head.current_revision_id == selection.revision.id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": CandidateSetHead.lock_version,
            "updated_at": CandidateSetHead.updated_at,
        }
    else:
        values = {
            "current_revision_id": selection.revision.id,
            "lock_version": CandidateSetHead.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(CandidateSetHead)
        .where(
            CandidateSetHead.story_path_id == head.story_path_id,
            CandidateSetHead.checkpoint_node_id == head.checkpoint_node_id,
            CandidateSetHead.lock_version == expected_lock_version,
            *(
                (CandidateSetHead.current_revision_id == selection.revision.id,)
                if same_revision
                else ()
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.expire(head)
    db.refresh(head)
    if result.rowcount != 1:
        raise_head_version_conflict(head)
    return CandidateSetHeadActivation(
        revision=selection.revision,
        head=head_value(head),
    )
