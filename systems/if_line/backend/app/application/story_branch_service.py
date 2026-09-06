"""StoryPath-scoped, versioned branch candidate generation."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.story_branch_contract import (
    BRANCH_CANDIDATE_CREDIT_COST,
    BRANCH_GENERATION_EVENT,
    BRANCH_GENERATION_TASK_KIND,
    BranchGenerationOutputError,
)
from app.application.candidate_set_review_service import (
    list_candidate_set_candidates,
)
from app.application.hashing import content_hash
from app.application.story_context_resolver import (
    StoryContextResolutionError,
    StoryContextResolver,
)
from app.application.task_service import create_generation_task
from app.core.errors import AppError
from app.integrations.llm.branch_adapter import (
    BRANCH_PROMPT_VERSION,
    BranchCandidateLLMRequest,
)
from app.models_v2 import (
    BranchCandidate,
    CandidateSetHead,
    CandidateSetRevision,
    ChapterRevision,
    GenerationTask,
    OutlineChapter,
    OutlineRevision,
    StateSnapshot,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    new_uuid,
)
from app.schemas_branch_generation import GeneratedBranchCandidateBatch


CANDIDATE_SET_SOURCE_SCHEMA_VERSION = "story-path-candidate-set-source-v1"
MAX_CANDIDATE_SET_INSTRUCTIONS = 12_000
MAX_BRANCH_PATH_DEPTH = 8
MAX_CONTEXT_BYTES = 64 * 1024
MAX_CHECKPOINT_BYTES = 16 * 1024
MAX_CONTEXT_DEPTH = 16
MAX_CONTEXT_NODES = 4_000
CHAPTER_TAIL_CHARS = 8_000
_SELECTABLE_CHAPTER_STATUSES = frozenset({"ready", "complete"})


@dataclass(frozen=True)
class CandidateSetGenerationContext:
    project_id: int
    story_path_id: str
    path_chapter_id: str
    checkpoint_node_id: str
    chapter_revision_id: str
    state_snapshot_id: str
    parent_revision_id: str | None
    candidate_count: int
    instructions: str
    source_manifest: dict[str, Any]
    source_hash: str
    request: BranchCandidateLLMRequest


@dataclass(frozen=True)
class CandidateSetPersistence:
    revision: CandidateSetRevision
    candidates: tuple[BranchCandidate, ...]
    created: bool


def _validation_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _normalize_request(
    *,
    story_path_id: object,
    checkpoint_node_id: object,
    chapter_revision_id: object,
    state_snapshot_id: object,
    candidate_count: object,
    instructions: object,
) -> dict[str, Any]:
    ids = {
        "story_path_id": story_path_id,
        "checkpoint_node_id": checkpoint_node_id,
        "chapter_revision_id": chapter_revision_id,
        "state_snapshot_id": state_snapshot_id,
    }
    for field, value in ids.items():
        if not isinstance(value, str) or not value.strip():
            raise _validation_error(f"{field} must be a non-empty UUID string")
        ids[field] = value.strip()
    if (
        isinstance(candidate_count, bool)
        or not isinstance(candidate_count, int)
        or candidate_count < 2
        or candidate_count > 4
    ):
        raise _validation_error("candidate_count must be between 2 and 4")
    if instructions is None:
        instruction_text = ""
    elif isinstance(instructions, str):
        instruction_text = instructions.strip()
    else:
        raise _validation_error("instructions must be a string or null")
    if (
        "\x00" in instruction_text
        or len(instruction_text) > MAX_CANDIDATE_SET_INSTRUCTIONS
    ):
        raise _validation_error(
            f"instructions cannot exceed {MAX_CANDIDATE_SET_INSTRUCTIONS} characters"
        )
    return {
        **ids,
        "candidate_count": candidate_count,
        "instructions": instruction_text,
    }


def _bounded_copy(value: Any, *, label: str, max_bytes: int) -> Any:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_CONTEXT_NODES:
            raise HTTPException(status_code=413, detail=f"{label} has too many nodes")
        if depth > MAX_CONTEXT_DEPTH:
            raise HTTPException(status_code=413, detail=f"{label} is nested too deeply")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise _validation_error(f"{label} must use string JSON object keys")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _validation_error(f"{label} must be valid JSON") from exc
    if len(encoded) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{label} is too large")
    return json.loads(encoded)


def _context_http_error(exc: StoryContextResolutionError) -> HTTPException:
    status_code = 404 if exc.code.endswith("_missing") else 409
    return HTTPException(status_code=status_code, detail=exc.detail)


def _path_depth(db: Session, path: StoryPath) -> int:
    depth = 0
    visited = {path.id}
    parent_id = path.parent_path_id
    while parent_id:
        if parent_id in visited:
            raise HTTPException(status_code=409, detail="StoryPath ancestry contains a cycle")
        visited.add(parent_id)
        parent = (
            db.query(StoryPath)
            .filter(
                StoryPath.id == parent_id,
                StoryPath.project_id == path.project_id,
            )
            .one_or_none()
        )
        if not parent:
            raise HTTPException(status_code=409, detail="StoryPath parent is invalid")
        depth += 1
        if depth >= MAX_BRANCH_PATH_DEPTH:
            raise HTTPException(status_code=409, detail="StoryPath branch depth limit reached")
        parent_id = parent.parent_path_id
    return depth


def _selected_candidate_set_parent(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
) -> str | None:
    head = (
        db.query(CandidateSetHead)
        .filter(
            CandidateSetHead.story_path_id == story_path_id,
            CandidateSetHead.checkpoint_node_id == checkpoint_node_id,
        )
        .one_or_none()
    )
    if not head or not head.current_revision_id:
        # Head 行可能由 GET 端点预创建且从未激活（current_revision_id 为空），
        # 这属于"尚无已选候选集"而非非法状态；否则首个候选集生成会被
        # 自己依赖的 head 永久 409 卡死。
        return None
    revision = (
        db.query(CandidateSetRevision)
        .filter(
            CandidateSetRevision.id == head.current_revision_id,
            CandidateSetRevision.story_path_id == story_path_id,
            CandidateSetRevision.checkpoint_node_id == checkpoint_node_id,
        )
        .one_or_none()
    )
    if not revision:
        raise HTTPException(status_code=409, detail="CandidateSet Head is invalid")
    return revision.id


def _assert_source_payload_integrity(
    db: Session,
    *,
    resolved,
    chapter: ChapterRevision,
    source: dict[str, Any],
) -> tuple[StoryPathChapter, StoryPath, StoryNode, StateSnapshot]:
    project_id = resolved.manifest["project_id"]
    path_chapter_id = resolved.manifest["path_chapter_id"]
    placement = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.story_path_id == resolved.manifest["story_path_id"],
            StoryPathChapter.chapter_slot_id == chapter.chapter_slot_id,
            StoryPathChapter.status == "active",
        )
        .one_or_none()
    )
    if not placement:
        raise BranchGenerationOutputError("PathChapter and ChapterRevision do not share a slot")
    path = db.query(StoryPath).filter(StoryPath.id == placement.story_path_id).one()
    if chapter.project_id != project_id or chapter.status not in _SELECTABLE_CHAPTER_STATUSES:
        raise BranchGenerationOutputError("ChapterRevision is not selectable")
    if content_hash(chapter.content) != chapter.content_hash:
        raise BranchGenerationOutputError("ChapterRevision content hash is invalid")
    if (
        chapter.bible_revision_id != resolved.manifest["bible_revision_id"]
        or chapter.outline_revision_id != resolved.manifest["outline_revision_id"]
    ):
        raise BranchGenerationOutputError("ChapterRevision provenance does not match context")

    bible = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == chapter.bible_revision_id,
            StoryBibleRevision.project_id == project_id,
        )
        .one_or_none()
    )
    if not bible or content_hash(bible.content_json) != bible.content_hash:
        raise BranchGenerationOutputError("Story Bible content hash is invalid")
    outline = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == chapter.outline_revision_id,
            OutlineRevision.project_id == project_id,
            OutlineRevision.story_path_id == path.id,
        )
        .one_or_none()
    )
    outline_chapter = (
        db.query(OutlineChapter)
        .filter(
            OutlineChapter.outline_revision_id == chapter.outline_revision_id,
            OutlineChapter.story_path_chapter_id == placement.id,
        )
        .one_or_none()
    )
    outline_payload = deepcopy(resolved.outline_chapter)
    generated_outline_payload = {
        **outline_payload,
        "story_path_chapter_id": None,
    }
    if (
        not outline
        or not outline_chapter
        or outline_chapter.content_hash
        not in {
            content_hash(outline_payload),
            content_hash(generated_outline_payload),
        }
    ):
        raise BranchGenerationOutputError("Outline chapter content hash is invalid")

    state_snapshot_id = source.get("state_snapshot_id")
    snapshot = (
        db.query(StateSnapshot)
        .filter(
            StateSnapshot.id == state_snapshot_id,
            StateSnapshot.project_id == project_id,
        )
        .one_or_none()
    )
    if not snapshot or content_hash(snapshot.state_json) != snapshot.state_hash:
        raise BranchGenerationOutputError("StateSnapshot content hash is invalid")

    checkpoint = (
        db.query(StoryNode)
        .filter(
            StoryNode.id == source.get("checkpoint_node_id"),
            StoryNode.project_id == project_id,
            StoryNode.node_type == "checkpoint",
            StoryNode.content_revision_id == chapter.id,
        )
        .one_or_none()
    )
    if not checkpoint:
        raise BranchGenerationOutputError("Checkpoint does not belong to ChapterRevision")
    return placement, path, checkpoint, snapshot


def build_candidate_set_generation_source(
    db: Session,
    *,
    story_path_id: str,
    checkpoint_node_id: str,
    chapter_revision_id: str,
    state_snapshot_id: str,
    candidate_count: int,
    instructions: str | None = None,
) -> CandidateSetGenerationContext:
    request_identity = _normalize_request(
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        chapter_revision_id=chapter_revision_id,
        state_snapshot_id=state_snapshot_id,
        candidate_count=candidate_count,
        instructions=instructions,
    )
    path = (
        db.query(StoryPath)
        .filter(StoryPath.id == request_identity["story_path_id"])
        .one_or_none()
    )
    if not path:
        raise HTTPException(status_code=404, detail="StoryPath does not exist")
    if path.status != "active":
        raise HTTPException(status_code=409, detail="Archived StoryPath cannot generate candidates")
    chapter = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.id == request_identity["chapter_revision_id"],
            ChapterRevision.project_id == path.project_id,
        )
        .one_or_none()
    )
    if not chapter or not chapter.chapter_slot_id:
        raise HTTPException(status_code=404, detail="ChapterRevision does not exist")
    placement = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == path.id,
            StoryPathChapter.chapter_slot_id == chapter.chapter_slot_id,
            StoryPathChapter.status == "active",
        )
        .one_or_none()
    )
    if not placement or placement.current_revision_id != chapter.id:
        raise HTTPException(
            status_code=409,
            detail="ChapterRevision is not the selected revision on this StoryPath",
        )
    try:
        resolved = StoryContextResolver(db).resolve(
            placement.id,
            state_snapshot_id=request_identity["state_snapshot_id"],
        )
    except StoryContextResolutionError as exc:
        raise _context_http_error(exc) from exc
    try:
        _placement, _path, checkpoint, snapshot = _assert_source_payload_integrity(
            db,
            resolved=resolved,
            chapter=chapter,
            source={
                "checkpoint_node_id": request_identity["checkpoint_node_id"],
                "state_snapshot_id": request_identity["state_snapshot_id"],
            },
        )
    except BranchGenerationOutputError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    branch_depth = _path_depth(db, path)
    outline_context = {
        **resolved.outline_chapter,
        "chapter_index": resolved.outline_chapter["display_index"],
    }
    checkpoint_payload = _bounded_copy(
        checkpoint.payload or {},
        label="Checkpoint payload",
        max_bytes=MAX_CHECKPOINT_BYTES,
    )
    provider_input = {
        "candidate_count": request_identity["candidate_count"],
        "bible": _bounded_copy(
            resolved.story_bible,
            label="Story Bible",
            max_bytes=MAX_CONTEXT_BYTES,
        ),
        "outline_chapter": outline_context,
        "chapter_tail": chapter.content[-CHAPTER_TAIL_CHARS:],
        "checkpoint": {
            "id": checkpoint.id,
            "checkpoint_key": checkpoint.checkpoint_key,
            "payload": checkpoint_payload,
        },
        "state": _bounded_copy(
            resolved.state or {},
            label="StateSnapshot",
            max_bytes=MAX_CONTEXT_BYTES,
        ),
        "instructions": request_identity["instructions"],
        "branch_depth": branch_depth,
    }
    outline_content_hash = (
        db.query(OutlineRevision.content_hash)
        .filter(OutlineRevision.id == chapter.outline_revision_id)
        .scalar()
    )
    source = {
        "schema_version": CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
        "prompt_version": BRANCH_PROMPT_VERSION,
        "request_identity": request_identity,
        "project_id": path.project_id,
        "story_path_id": path.id,
        "path_chapter_id": placement.id,
        "checkpoint_node_id": checkpoint.id,
        "chapter_revision_id": chapter.id,
        "state_snapshot_id": snapshot.id,
        "parent_revision_id": _selected_candidate_set_parent(
            db,
            story_path_id=path.id,
            checkpoint_node_id=checkpoint.id,
        ),
        "context_manifest": resolved.manifest,
        "context_hash": resolved.context_hash,
        "bible_content_hash": content_hash(resolved.story_bible),
        "outline_content_hash": outline_content_hash,
        "chapter_content_hash": chapter.content_hash,
        "state_hash": snapshot.state_hash,
        "checkpoint_payload_hash": content_hash(checkpoint_payload),
        "provider_input": provider_input,
    }
    return _context_from_source(source, content_hash(source))


def _context_from_source(
    source: dict[str, Any],
    source_hash: str,
) -> CandidateSetGenerationContext:
    provider = source.get("provider_input")
    if not isinstance(provider, dict):
        raise BranchGenerationOutputError("CandidateSet provider input is missing")
    try:
        request = BranchCandidateLLMRequest(
            source_hash=source_hash,
            candidate_count=int(provider["candidate_count"]),
            bible=deepcopy(provider["bible"]),
            outline_chapter=deepcopy(provider["outline_chapter"]),
            chapter_tail=str(provider["chapter_tail"]),
            checkpoint=deepcopy(provider["checkpoint"]),
            state=deepcopy(provider["state"]),
            instructions=str(provider["instructions"]),
            branch_depth=int(provider["branch_depth"]),
        )
        return CandidateSetGenerationContext(
            project_id=int(source["project_id"]),
            story_path_id=str(source["story_path_id"]),
            path_chapter_id=str(source["path_chapter_id"]),
            checkpoint_node_id=str(source["checkpoint_node_id"]),
            chapter_revision_id=str(source["chapter_revision_id"]),
            state_snapshot_id=str(source["state_snapshot_id"]),
            parent_revision_id=(
                str(source["parent_revision_id"])
                if source.get("parent_revision_id")
                else None
            ),
            candidate_count=int(provider["candidate_count"]),
            instructions=str(provider["instructions"]),
            source_manifest=deepcopy(source),
            source_hash=source_hash,
            request=request,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BranchGenerationOutputError("CandidateSet source is incomplete") from exc


def _source_envelope(source_refs: dict[str, Any]) -> tuple[dict[str, Any], str]:
    source = source_refs.get("candidate_set_source")
    digest = source_refs.get("candidate_set_source_hash")
    if not isinstance(source, dict) or not isinstance(digest, str):
        raise BranchGenerationOutputError("CandidateSet frozen source is missing")
    if content_hash(source) != digest:
        raise BranchGenerationOutputError("CandidateSet source hash does not match")
    if source.get("schema_version") != CANDIDATE_SET_SOURCE_SCHEMA_VERSION:
        raise BranchGenerationOutputError("CandidateSet source schema is unsupported")
    return source, digest


def create_candidate_set_generation_task(
    db: Session,
    *,
    user_id: int,
    story_path_id: str,
    checkpoint_node_id: str,
    chapter_revision_id: str,
    state_snapshot_id: str,
    candidate_count: int,
    instructions: str | None,
    idempotency_key: str,
) -> tuple[GenerationTask, bool, CandidateSetGenerationContext]:
    request_identity = _normalize_request(
        story_path_id=story_path_id,
        checkpoint_node_id=checkpoint_node_id,
        chapter_revision_id=chapter_revision_id,
        state_snapshot_id=state_snapshot_id,
        candidate_count=candidate_count,
        instructions=instructions,
    )
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="A valid Idempotency-Key is required")
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        try:
            source, digest = _source_envelope(existing.source_refs or {})
        except BranchGenerationOutputError as exc:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key belongs to a different request",
            ) from exc
        if (
            existing.kind != BRANCH_GENERATION_TASK_KIND
            or source.get("request_identity") != request_identity
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key belongs to a different request",
            )
        return existing, False, _context_from_source(source, digest)

    context = build_candidate_set_generation_source(
        db,
        story_path_id=request_identity["story_path_id"],
        checkpoint_node_id=request_identity["checkpoint_node_id"],
        chapter_revision_id=request_identity["chapter_revision_id"],
        state_snapshot_id=request_identity["state_snapshot_id"],
        candidate_count=request_identity["candidate_count"],
        instructions=request_identity["instructions"],
    )
    task, created = create_generation_task(
        db,
        user_id=user_id,
        project_id=context.project_id,
        kind=BRANCH_GENERATION_TASK_KIND,
        idempotency_key=idempotency_key,
        source_refs={
            "candidate_set_source": context.source_manifest,
            "candidate_set_source_hash": context.source_hash,
            "source_hash": context.source_hash,
            "source_schema_version": CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
            "story_path_id": context.story_path_id,
            "checkpoint_node_id": context.checkpoint_node_id,
            "chapter_revision_id": context.chapter_revision_id,
            "state_snapshot_id": context.state_snapshot_id,
        },
        parameters={
            "candidate_count": context.candidate_count,
            "instructions": context.instructions,
            "prompt_version": BRANCH_PROMPT_VERSION,
            "source_schema_version": CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
        },
        estimated_cost=(
            BRANCH_CANDIDATE_CREDIT_COST * Decimal(context.candidate_count)
        ),
        enqueue_event_type=BRANCH_GENERATION_EVENT,
    )
    return task, created, context


def resolve_candidate_set_generation_source(
    db: Session,
    *,
    task: GenerationTask,
) -> CandidateSetGenerationContext:
    source, digest = _source_envelope(task.source_refs or {})
    context = _context_from_source(source, digest)
    if task.kind != BRANCH_GENERATION_TASK_KIND or task.project_id != context.project_id:
        raise BranchGenerationOutputError("CandidateSet task ownership does not match source")
    if (task.parameters or {}) != {
        "candidate_count": context.candidate_count,
        "instructions": context.instructions,
        "prompt_version": BRANCH_PROMPT_VERSION,
        "source_schema_version": CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
    }:
        raise BranchGenerationOutputError("CandidateSet task parameters do not match source")
    expected_identity = {
        "story_path_id": context.story_path_id,
        "checkpoint_node_id": context.checkpoint_node_id,
        "chapter_revision_id": context.chapter_revision_id,
        "state_snapshot_id": context.state_snapshot_id,
        "candidate_count": context.candidate_count,
        "instructions": context.instructions,
    }
    if source.get("request_identity") != expected_identity:
        raise BranchGenerationOutputError(
            "CandidateSet request identity does not match frozen source"
        )
    try:
        resolved = StoryContextResolver(db).resolve_frozen(
            source.get("context_manifest"),
            expected_context_hash=str(source.get("context_hash") or ""),
        )
    except StoryContextResolutionError as exc:
        raise BranchGenerationOutputError(exc.detail) from exc
    chapter = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id == context.chapter_revision_id)
        .one_or_none()
    )
    if not chapter:
        raise BranchGenerationOutputError("ChapterRevision no longer exists")
    placement, path, checkpoint, snapshot = _assert_source_payload_integrity(
        db,
        resolved=resolved,
        chapter=chapter,
        source=source,
    )
    parent_revision_id = source.get("parent_revision_id")
    if parent_revision_id:
        parent = (
            db.query(CandidateSetRevision)
            .filter(
                CandidateSetRevision.id == parent_revision_id,
                CandidateSetRevision.story_path_id == path.id,
                CandidateSetRevision.checkpoint_node_id == checkpoint.id,
            )
            .one_or_none()
        )
        if not parent:
            raise BranchGenerationOutputError("CandidateSet parent revision is invalid")

    expected_provider = {
        "candidate_count": context.candidate_count,
        "bible": deepcopy(resolved.story_bible),
        "outline_chapter": {
            **resolved.outline_chapter,
            "chapter_index": resolved.outline_chapter["display_index"],
        },
        "chapter_tail": chapter.content[-CHAPTER_TAIL_CHARS:],
        "checkpoint": {
            "id": checkpoint.id,
            "checkpoint_key": checkpoint.checkpoint_key,
            "payload": deepcopy(checkpoint.payload or {}),
        },
        "state": deepcopy(snapshot.state_json or {}),
        "instructions": context.instructions,
        "branch_depth": _path_depth(db, path),
    }
    if source.get("provider_input") != expected_provider:
        raise BranchGenerationOutputError("CandidateSet frozen provider input changed")
    if (
        source.get("project_id") != path.project_id
        or source.get("story_path_id") != path.id
        or source.get("path_chapter_id") != placement.id
        or source.get("chapter_content_hash") != chapter.content_hash
        or source.get("state_hash") != snapshot.state_hash
        or source.get("checkpoint_payload_hash") != content_hash(checkpoint.payload or {})
        or source.get("bible_content_hash") != content_hash(resolved.story_bible)
    ):
        raise BranchGenerationOutputError("CandidateSet frozen source metadata changed")
    outline_hash = (
        db.query(OutlineRevision.content_hash)
        .filter(OutlineRevision.id == chapter.outline_revision_id)
        .scalar()
    )
    if source.get("outline_content_hash") != outline_hash:
        raise BranchGenerationOutputError("CandidateSet Outline source changed")
    return context


def _validated_existing_persistence(
    db: Session,
    revision: CandidateSetRevision,
) -> CandidateSetPersistence:
    try:
        values = list_candidate_set_candidates(
            db,
            candidate_set_revision_id=revision.id,
            project_id=revision.project_id,
        )
    except AppError as exc:
        raise BranchGenerationOutputError(exc.message) from exc
    rows = (
        db.query(BranchCandidate)
        .filter(BranchCandidate.candidate_set_revision_id == revision.id)
        .all()
    )
    by_id = {row.id: row for row in rows}
    return CandidateSetPersistence(
        revision,
        tuple(by_id[value.id] for value in values),
        False,
    )


def persist_candidate_set_revision(
    db: Session,
    *,
    task: GenerationTask,
    batch: GeneratedBranchCandidateBatch,
) -> CandidateSetPersistence:
    existing = (
        db.query(CandidateSetRevision)
        .filter(CandidateSetRevision.generation_task_id == task.id)
        .one_or_none()
    )
    if existing:
        return _validated_existing_persistence(db, existing)

    context = resolve_candidate_set_generation_source(db, task=task)
    if len(batch.candidates) != context.candidate_count:
        raise BranchGenerationOutputError(
            "CandidateSet output count does not match frozen request"
        )
    checkpoint = (
        db.query(StoryNode)
        .filter(StoryNode.id == context.checkpoint_node_id)
        .with_for_update()
        .one()
    )
    revision_no = int(
        db.query(func.max(CandidateSetRevision.revision_no))
        .filter(
            CandidateSetRevision.story_path_id == context.story_path_id,
            CandidateSetRevision.checkpoint_node_id == checkpoint.id,
        )
        .scalar()
        or 0
    ) + 1
    revision_id = new_uuid()
    candidate_specs: list[dict[str, Any]] = []
    for ordinal, generated in enumerate(batch.candidates):
        candidate_specs.append(
            {
                "id": new_uuid(),
                "preview_node_id": new_uuid(),
                "ordinal": ordinal,
                "option_key": generated.option_key,
                "preview_text": generated.preview_text,
                "state_delta": deepcopy(generated.state_delta),
            }
        )
    revision = CandidateSetRevision(
        id=revision_id,
        project_id=context.project_id,
        story_path_id=context.story_path_id,
        checkpoint_node_id=context.checkpoint_node_id,
        chapter_revision_id=context.chapter_revision_id,
        state_snapshot_id=context.state_snapshot_id,
        parent_revision_id=context.parent_revision_id,
        revision_no=revision_no,
        source_hash=context.source_hash,
        candidate_count=context.candidate_count,
        instructions=context.instructions,
        candidates_json=deepcopy(candidate_specs),
        content_hash=content_hash(candidate_specs),
        generation_task_id=task.id,
        created_by=task.user_id,
    )
    db.add(revision)
    db.flush()
    probability = round(1.0 / context.candidate_count, 6)
    previews: list[StoryNode] = []
    for spec in candidate_specs:
        preview = StoryNode(
            id=spec["preview_node_id"],
            project_id=context.project_id,
            node_type="paragraph",
            content_revision_id=context.chapter_revision_id,
            payload={
                "text": spec["preview_text"],
                "preview_text": spec["preview_text"],
                "state_delta_hash": content_hash(spec["state_delta"]),
                "branch_depth": context.request.branch_depth + 1,
                "branch_generation": {
                    "source_hash": context.source_hash,
                    "candidate_set_revision_id": revision.id,
                    "option_key": spec["option_key"],
                    "ordinal": spec["ordinal"],
                    "task_id": task.id,
                    "prompt_version": BRANCH_PROMPT_VERSION,
                    "candidate_hash": content_hash(
                        {
                            "option_key": spec["option_key"],
                            "preview_text": spec["preview_text"],
                            "state_delta": spec["state_delta"],
                        }
                    ),
                },
            },
        )
        db.add(preview)
        previews.append(preview)
    db.flush()

    candidates: list[BranchCandidate] = []
    for spec, preview in zip(candidate_specs, previews):
        candidate = BranchCandidate(
            id=spec["id"],
            project_id=context.project_id,
            candidate_set_revision_id=revision.id,
            checkpoint_node_id=context.checkpoint_node_id,
            option_key=spec["option_key"],
            preview_node_id=preview.id,
            preview_revision_id=preview.id,
            state_delta=deepcopy(spec["state_delta"]),
            candidate_status="preview_ready",
            predicted_probability=probability,
            generation_task_id=task.id,
        )
        db.add(candidate)
        candidates.append(candidate)
    db.flush()
    return CandidateSetPersistence(revision, tuple(candidates), True)
