"""Transactional promotion of one reviewed branch candidate into a child path."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.candidate_set_review_service import (
    CandidateSetCandidateValue,
    resolve_selectable_candidate_set,
)
from app.application.hashing import content_hash
from app.core.errors import AppError
from app.models import Project, User
from app.models_v2 import (
    BranchCandidate,
    CandidateSetHead,
    CandidateSetRevision,
    ChapterRevision,
    StateSnapshot,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    StoryPathPromotionRecord,
)
from app.schemas_branch_generation import (
    MAX_STATE_DELTA_BYTES,
    MAX_STATE_DELTA_DEPTH,
    MAX_STATE_DELTA_NODES,
    validate_json_object,
)


MAX_STORY_STATE_BYTES = 64 * 1024
MAX_STORY_STATE_DEPTH = 16
MAX_STORY_STATE_NODES = 4_000
MAX_STORY_PATH_TITLE = 200


@dataclass(frozen=True)
class StoryPathPromotionResult:
    path: StoryPath
    created: bool


def _normalize_request(
    *,
    candidate_id: object,
    title: object,
    idempotency_key: object,
) -> tuple[dict[str, str | None], str, str]:
    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise AppError(
            code="candidate.invalid",
            message="candidate_id must be a non-empty UUID string",
            status_code=422,
        )
    if title is None:
        normalized_title = None
    elif isinstance(title, str):
        normalized_title = title.strip() or None
        if normalized_title and (
            "\x00" in normalized_title or len(normalized_title) > MAX_STORY_PATH_TITLE
        ):
            raise AppError(
                code="story_path.title_invalid",
                message=f"StoryPath title cannot exceed {MAX_STORY_PATH_TITLE} characters",
                status_code=422,
            )
    else:
        raise AppError(
            code="story_path.title_invalid",
            message="StoryPath title must be a string or null",
            status_code=422,
        )
    if not isinstance(idempotency_key, str):
        key = ""
    else:
        key = idempotency_key.strip()
    if not key or len(key) > 255:
        raise AppError(
            code="idempotency_key.invalid",
            message="A valid Idempotency-Key is required",
            status_code=400,
        )
    request = {
        "candidate_id": candidate_id.strip(),
        "title": normalized_title,
    }
    return request, content_hash(request), key


def _replay_promotion(
    db: Session,
    *,
    record: StoryPathPromotionRecord,
    request: dict[str, str | None],
    request_hash: str,
    expected_project_id: int | None,
) -> StoryPathPromotionResult:
    if record.request_hash != request_hash or record.request_json != request:
        raise AppError(
            code="idempotency_key.conflict",
            message="Idempotency-Key belongs to a different promotion request",
            status_code=409,
        )
    path = (
        db.query(StoryPath)
        .join(Project, Project.id == StoryPath.project_id)
        .filter(
            StoryPath.id == record.story_path_id,
            StoryPath.project_id == record.project_id,
            StoryPath.fork_candidate_id == record.candidate_id,
            Project.owner_id == record.user_id,
        )
        .one_or_none()
    )
    if (
        not path
        or record.candidate_id != request["candidate_id"]
        or (expected_project_id is not None and path.project_id != expected_project_id)
    ):
        raise AppError(
            code="story_path_promotion.invalid",
            message="Stored StoryPath promotion is inconsistent",
            status_code=409,
        )
    return StoryPathPromotionResult(path=path, created=False)


def _validate_state(state: dict[str, Any]) -> dict[str, Any]:
    stack: list[tuple[Any, int]] = [(state, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_STORY_STATE_NODES or depth > MAX_STORY_STATE_DEPTH:
            raise AppError(
                code="story_state.too_large",
                message="Merged story state is too large or nested too deeply",
                status_code=413,
            )
        if isinstance(value, dict):
            if any(not isinstance(key, str) for key in value):
                raise AppError(
                    code="story_state.invalid",
                    message="Story state object keys must be strings",
                    status_code=422,
                )
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AppError(
            code="story_state.invalid",
            message="Story state must be valid JSON",
            status_code=422,
        ) from exc
    if len(encoded) > MAX_STORY_STATE_BYTES:
        raise AppError(
            code="story_state.too_large",
            message=f"Merged story state cannot exceed {MAX_STORY_STATE_BYTES} bytes",
            status_code=413,
        )
    return json.loads(encoded)


def _merge_state(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in delta.items():
        if value is None:
            merged.pop(key, None)
        elif isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_state(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _apply_candidate_state(
    db: Session,
    *,
    project_id: int,
    base_snapshot_id: str,
    candidate: CandidateSetCandidateValue,
) -> StateSnapshot:
    base = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == base_snapshot_id,
            StateSnapshot.project_id == project_id,
        )
        .one_or_none()
    )
    if not base or content_hash(base.state_json) != base.state_hash:
        raise AppError(
            code="story_state.source_invalid",
            message="Candidate base StateSnapshot is missing or corrupted",
            status_code=409,
        )
    try:
        delta = validate_json_object(
            deepcopy(candidate.state_delta),
            max_bytes=MAX_STATE_DELTA_BYTES,
            max_depth=MAX_STATE_DELTA_DEPTH,
            max_nodes=MAX_STATE_DELTA_NODES,
        )
    except ValueError as exc:
        raise AppError(
            code="candidate.state_delta_invalid",
            message=str(exc),
            status_code=409,
        ) from exc
    merged = _validate_state(_merge_state(_validate_state(base.state_json), delta))
    digest = content_hash(merged)
    existing = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.project_id == project_id,
            StateSnapshot.state_hash == digest,
        )
        .one_or_none()
    )
    if existing:
        return existing
    snapshot = StateSnapshot(
        project_id=project_id,
        parent_snapshot_id=base.id,
        state_hash=digest,
        state_json=merged,
    )
    try:
        with db.begin_nested():
            db.add(snapshot)
            db.flush()
        return snapshot
    except IntegrityError:
        existing = (
            db.query(StateSnapshot)
            .filter(
                StateSnapshot.project_id == project_id,
                StateSnapshot.state_hash == digest,
            )
            .one_or_none()
        )
        if existing:
            return existing
        raise


def _frozen_prefix(
    db: Session,
    *,
    parent_path: StoryPath,
    fork_chapter: StoryPathChapter,
) -> list[StoryPathChapter]:
    rows = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == parent_path.id,
            StoryPathChapter.status == "active",
        )
        .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
        .with_for_update()
        .all()
    )
    by_id = {row.id: row for row in rows}
    if fork_chapter.id not in by_id:
        raise AppError(
            code="story_path.topology_invalid",
            message="Fork PathChapter is not part of the parent path",
            status_code=409,
        )
    reverse_chain: list[StoryPathChapter] = []
    visited: set[str] = set()
    cursor: StoryPathChapter | None = fork_chapter
    while cursor:
        if cursor.id in visited:
            raise AppError(
                code="story_path.topology_invalid",
                message="Parent StoryPath predecessor chain contains a cycle",
                status_code=409,
            )
        visited.add(cursor.id)
        reverse_chain.append(cursor)
        predecessor_id = cursor.predecessor_path_chapter_id
        cursor = by_id.get(predecessor_id) if predecessor_id else None
        if predecessor_id and cursor is None:
            raise AppError(
                code="story_path.topology_invalid",
                message="Parent StoryPath predecessor leaves the path",
                status_code=409,
            )
    prefix = list(reversed(reverse_chain))
    fork_position = rows.index(by_id[fork_chapter.id])
    if [row.id for row in rows[: fork_position + 1]] != [row.id for row in prefix]:
        raise AppError(
            code="story_path.topology_invalid",
            message="Parent StoryPath ordering does not match its predecessor chain",
            status_code=409,
        )
    revision_ids = [row.current_revision_id for row in prefix]
    if any(not revision_id for revision_id in revision_ids):
        raise AppError(
            code="story_path.prefix_unreviewed",
            message="Every shared-prefix chapter must have a reviewed revision",
            status_code=409,
        )
    revisions = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id.in_(revision_ids))
        .all()
    )
    by_revision_id = {revision.id: revision for revision in revisions}
    for row in prefix:
        revision = by_revision_id.get(row.current_revision_id)
        if (
            not revision
            or revision.project_id != parent_path.project_id
            or revision.chapter_slot_id != row.chapter_slot_id
            or content_hash(revision.content) != revision.content_hash
        ):
            raise AppError(
                code="story_path.prefix_invalid",
                message="Shared-prefix Chapter Head is missing or corrupted",
                status_code=409,
            )
    return prefix


def _create_child_prefix(
    db: Session,
    *,
    child: StoryPath,
    parent_prefix: list[StoryPathChapter],
) -> list[StoryPathChapter]:
    child_rows: list[StoryPathChapter] = []
    predecessor_id: str | None = None
    for source in parent_prefix:
        inherited = StoryPathChapter(
            story_path_id=child.id,
            chapter_slot_id=source.chapter_slot_id,
            display_index=source.display_index,
            predecessor_path_chapter_id=predecessor_id,
            inherited_from_path_chapter_id=source.id,
            current_revision_id=source.current_revision_id,
            lock_version=1,
        )
        db.add(inherited)
        db.flush()
        child_rows.append(inherited)
        predecessor_id = inherited.id
    return child_rows


def _default_title(parent: StoryPath, candidate: CandidateSetCandidateValue) -> str:
    return f"{parent.title} / {candidate.option_key}"[:MAX_STORY_PATH_TITLE].strip()


def _record_promotion(
    db: Session,
    *,
    user_id: int,
    path: StoryPath,
    candidate_id: str,
    idempotency_key: str,
    request: dict[str, str | None],
    request_hash: str,
) -> StoryPathPromotionRecord:
    record = StoryPathPromotionRecord(
        user_id=user_id,
        project_id=path.project_id,
        candidate_id=candidate_id,
        story_path_id=path.id,
        idempotency_key=idempotency_key,
        request_json=deepcopy(request),
        request_hash=request_hash,
    )
    db.add(record)
    db.flush()
    return record


def promote_candidate_to_story_path(
    db: Session,
    *,
    user_id: int,
    candidate_id: str,
    idempotency_key: str,
    title: str | None = None,
    expected_project_id: int | None = None,
) -> StoryPathPromotionResult:
    """Create one child path, prefix, and resulting state in one transaction."""

    request, request_hash, key = _normalize_request(
        candidate_id=candidate_id,
        title=title,
        idempotency_key=idempotency_key,
    )
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if not user:
        raise AppError(code="user.not_found", message="User does not exist", status_code=404)
    replay = (
        db.query(StoryPathPromotionRecord)
        .filter(
            StoryPathPromotionRecord.user_id == user_id,
            StoryPathPromotionRecord.idempotency_key == key,
        )
        .one_or_none()
    )
    if replay:
        return _replay_promotion(
            db,
            record=replay,
            request=request,
            request_hash=request_hash,
            expected_project_id=expected_project_id,
        )

    candidate = (
        db.query(BranchCandidate)
        .filter(
            BranchCandidate.id == request["candidate_id"],
            BranchCandidate.candidate_set_revision_id.is_not(None),
        )
        .with_for_update()
        .one_or_none()
    )
    revision = (
        db.query(CandidateSetRevision)
        .filter(CandidateSetRevision.id == candidate.candidate_set_revision_id)
        .one_or_none()
        if candidate
        else None
    )
    parent = (
        db.query(StoryPath)
        .filter(StoryPath.id == revision.story_path_id)
        .one_or_none()
        if revision
        else None
    )
    project = (
        db.query(Project)
        .filter(
            Project.id == parent.project_id,
            Project.owner_id == user_id,
        )
        .one_or_none()
        if parent
        else None
    )
    if (
        not candidate
        or not revision
        or not parent
        or not project
        or (expected_project_id is not None and project.id != expected_project_id)
    ):
        raise AppError(
            code="branch_candidate.not_found",
            message="Branch candidate does not exist",
            status_code=404,
        )

    existing_path = (
        db.query(StoryPath)
        .filter(StoryPath.fork_candidate_id == candidate.id)
        .one_or_none()
    )
    if existing_path:
        if (
            existing_path.project_id != project.id
            or existing_path.parent_path_id != parent.id
            or existing_path.fork_checkpoint_node_id != revision.checkpoint_node_id
        ):
            raise AppError(
                code="story_path_promotion.invalid",
                message="Existing candidate promotion has inconsistent provenance",
                status_code=409,
            )
        requested_title = request["title"]
        if requested_title is not None and requested_title != existing_path.title:
            raise AppError(
                code="branch_candidate.already_promoted",
                message="Candidate was already promoted with a different title",
                status_code=409,
            )
        with db.begin_nested():
            _record_promotion(
                db,
                user_id=user_id,
                path=existing_path,
                candidate_id=candidate.id,
                idempotency_key=key,
                request=request,
                request_hash=request_hash,
            )
        return StoryPathPromotionResult(path=existing_path, created=False)

    selection = resolve_selectable_candidate_set(
        db,
        story_path_id=revision.story_path_id,
        checkpoint_node_id=revision.checkpoint_node_id,
        revision_id=revision.id,
        for_update=True,
    )
    head = (
        db.query(CandidateSetHead)
        .filter(
            CandidateSetHead.story_path_id == selection.path.id,
            CandidateSetHead.checkpoint_node_id == selection.checkpoint.id,
        )
        .with_for_update()
        .one_or_none()
    )
    if not head or head.current_revision_id != revision.id:
        raise AppError(
            code="branch_candidate.not_active",
            message="Branch candidate is not in the active reviewed CandidateSet",
            status_code=409,
        )
    selected_candidate = next(
        (value for value in selection.candidates if value.id == candidate.id),
        None,
    )
    if not selected_candidate:
        raise AppError(
            code="candidate_set_revision.payload_invalid",
            message="Candidate is missing from its immutable CandidateSet payload",
            status_code=409,
        )
    parent_prefix = _frozen_prefix(
        db,
        parent_path=selection.path,
        fork_chapter=selection.placement,
    )
    if parent_prefix[-1].current_revision_id != revision.chapter_revision_id:
        raise AppError(
            code="candidate_set_revision.stale_source",
            message="Fork chapter no longer matches the selected CandidateSet source",
            status_code=409,
        )
    with db.begin_nested():
        snapshot = _apply_candidate_state(
            db,
            project_id=selection.path.project_id,
            base_snapshot_id=revision.state_snapshot_id,
            candidate=selected_candidate,
        )
        path = StoryPath(
            project_id=selection.path.project_id,
            parent_path_id=selection.path.id,
            fork_path_chapter_id=selection.placement.id,
            fork_checkpoint_node_id=selection.checkpoint.id,
            fork_candidate_id=selected_candidate.id,
            base_state_snapshot_id=snapshot.id,
            title=request["title"] or _default_title(selection.path, selected_candidate),
            status="active",
            lock_version=1,
        )
        db.add(path)
        db.flush()
        _create_child_prefix(db, child=path, parent_prefix=parent_prefix)
        db.add(
            StoryPathOutlineHead(
                story_path_id=path.id,
                current_revision_id=None,
                lock_version=1,
            )
        )
        _record_promotion(
            db,
            user_id=user_id,
            path=path,
            candidate_id=selected_candidate.id,
            idempotency_key=key,
            request=request,
            request_hash=request_hash,
        )
    return StoryPathPromotionResult(path=path, created=True)


__all__ = [
    "StoryPathPromotionResult",
    "promote_candidate_to_story_path",
]
