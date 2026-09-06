from __future__ import annotations

from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.story_chapter_service import (
    CHAPTER_GENERATION_COST,
    build_chapter_generation_source,
    normalize_chapter_generation_parameters,
)
from app.application.task_service import (
    TERMINAL_TASK_STATUSES,
    append_task_event,
    complete_task,
    create_generation_task,
    request_cancel,
)
from app.core.errors import AppError
from app.models_v2 import ChapterRevision, GenerationTask, StoryPath, StoryPathChapter, utcnow


LEGACY_STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION = "story-path-chapter-batch-source-v1"
STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION = "story-path-chapter-batch-source-v2"
_ANCHOR_FIELDS = (
    "bible_revision_id",
    "outline_revision_id",
    "state_snapshot_id",
    "fork",
    "fork_choice",
)


def _batch_child_idempotency_key(
    idempotency_key: str,
    path_chapter_id: str,
) -> str:
    candidate = f"{idempotency_key}:path-chapter:{path_chapter_id}"
    if len(candidate) <= 255:
        return candidate
    return f"chapter-batch-child:{content_hash(candidate)}"


def _batch_source(task: GenerationTask) -> dict[str, Any] | None:
    source = (task.source_refs or {}).get("chapter_batch_source")
    if not isinstance(source, dict):
        return None
    if source.get("schema_version") != STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION:
        return None
    return source


def is_sequential_story_path_chapter_batch(task: GenerationTask) -> bool:
    return task.kind == "chapter.batch" and _batch_source(task) is not None


def _child_path_chapter_id(task: GenerationTask) -> str | None:
    source = (task.source_refs or {}).get("chapter_source")
    manifest = source.get("context_manifest") if isinstance(source, dict) else None
    path_chapter_id = manifest.get("path_chapter_id") if isinstance(manifest, dict) else None
    return path_chapter_id if isinstance(path_chapter_id, str) and path_chapter_id else None


def _ancestor_ids(
    target: StoryPathChapter,
    placements_by_id: Mapping[str, StoryPathChapter],
) -> list[str]:
    reverse: list[str] = []
    visited = {target.id}
    cursor_id = target.predecessor_path_chapter_id
    while cursor_id:
        if cursor_id in visited:
            raise HTTPException(status_code=409, detail="StoryPath predecessor chain contains a cycle")
        visited.add(cursor_id)
        predecessor = placements_by_id.get(cursor_id)
        if not predecessor:
            raise HTTPException(
                status_code=409,
                detail="StoryPath predecessor chain contains an inactive or missing chapter",
            )
        if predecessor.display_index >= target.display_index:
            raise HTTPException(status_code=409, detail="StoryPath predecessor order is invalid")
        reverse.append(predecessor.id)
        cursor_id = predecessor.predecessor_path_chapter_id
    return list(reversed(reverse))


def _source_anchor(source_refs: Mapping[str, Any]) -> dict[str, Any]:
    source = source_refs.get("chapter_source")
    manifest = source.get("context_manifest") if isinstance(source, dict) else None
    if not isinstance(manifest, dict):
        raise HTTPException(status_code=409, detail="Chapter batch source is incomplete")
    return {field: manifest.get(field) for field in _ANCHOR_FIELDS}


def _create_child_task(
    db: Session,
    *,
    parent: GenerationTask,
    path_chapter_id: str,
    source_refs: dict[str, Any],
) -> GenerationTask:
    child, created = create_generation_task(
        db,
        user_id=parent.user_id,
        project_id=parent.project_id,
        kind="chapter.generate",
        idempotency_key=_batch_child_idempotency_key(
            parent.idempotency_key,
            path_chapter_id,
        ),
        source_refs=source_refs,
        parameters=parent.parameters,
        estimated_cost=CHAPTER_GENERATION_COST,
        parent_task_id=parent.id,
        root_task_id=parent.id,
    )
    if child.parent_task_id != parent.id or _child_path_chapter_id(child) != path_chapter_id:
        raise HTTPException(status_code=409, detail="Chapter batch child idempotency conflict")
    if not created and child.status in TERMINAL_TASK_STATUSES:
        return child
    return child


def _load_active_placements(
    db: Session,
    *,
    story_path_id: str,
) -> tuple[list[StoryPathChapter], dict[str, StoryPathChapter]]:
    placements = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == story_path_id,
            StoryPathChapter.status == "active",
        )
        .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
        .all()
    )
    return placements, {placement.id: placement for placement in placements}


def _revision_overrides_for_target(
    target: StoryPathChapter,
    *,
    placements_by_id: Mapping[str, StoryPathChapter],
    baseline_revision_ids: Mapping[str, str],
    provisional_revision_ids: Mapping[str, str],
) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for ancestor_id in _ancestor_ids(target, placements_by_id):
        revision_id = provisional_revision_ids.get(ancestor_id) or baseline_revision_ids.get(
            ancestor_id
        )
        if not revision_id:
            raise HTTPException(
                status_code=409,
                detail="Every predecessor outside the generated prefix must have a selected revision",
            )
        overrides[ancestor_id] = revision_id
    return overrides


def _build_next_source(
    db: Session,
    *,
    parent: GenerationTask,
    source: Mapping[str, Any],
    path_chapter_id: str,
    provisional_revision_ids: Mapping[str, str],
) -> dict[str, Any]:
    story_path_id = source.get("story_path_id")
    baseline_revision_ids = source.get("baseline_revision_ids")
    anchor = source.get("context_anchor")
    topology = source.get("topology")
    if (
        not isinstance(story_path_id, str)
        or not isinstance(baseline_revision_ids, dict)
        or not isinstance(anchor, dict)
        or not isinstance(topology, dict)
    ):
        raise HTTPException(status_code=409, detail="Chapter batch orchestration source is invalid")
    placements, placements_by_id = _load_active_placements(
        db,
        story_path_id=story_path_id,
    )
    current_topology = {
        placement.id: {
            "chapter_slot_id": placement.chapter_slot_id,
            "display_index": placement.display_index,
            "predecessor_path_chapter_id": placement.predecessor_path_chapter_id,
        }
        for placement in placements
    }
    if current_topology != topology:
        raise HTTPException(status_code=409, detail="StoryPath topology changed during chapter batch")
    target = placements_by_id.get(path_chapter_id)
    if not target:
        raise HTTPException(status_code=409, detail="StoryPath topology changed during chapter batch")
    overrides = _revision_overrides_for_target(
        target,
        placements_by_id=placements_by_id,
        baseline_revision_ids=baseline_revision_ids,
        provisional_revision_ids=provisional_revision_ids,
    )
    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=path_chapter_id,
        parameters=parent.parameters,
        state_snapshot_id=anchor.get("state_snapshot_id"),
        bible_revision_id=anchor.get("bible_revision_id"),
        outline_revision_id=anchor.get("outline_revision_id"),
        ancestor_revision_overrides=overrides,
    )
    manifest = source_refs["chapter_source"]["context_manifest"]
    if any(manifest.get(field) != anchor.get(field) for field in _ANCHOR_FIELDS):
        raise HTTPException(status_code=409, detail="Chapter batch context anchor changed")
    return source_refs


def _child_result(
    child: GenerationTask,
    *,
    position: int,
    status: str | None = None,
) -> dict[str, Any]:
    result = dict(child.result_refs or {})
    result.update(
        {
            "batch_task_id": child.parent_task_id,
            "provisional": True,
            "provisional_position": position,
        }
    )
    if status is not None:
        result["provisional_status"] = status
    elif "provisional_status" not in result:
        result["provisional_status"] = (
            "provisional" if child.status == "succeeded" else child.status
        )
    child.result_refs = result
    return result


def _parent_result_snapshot(
    parent: GenerationTask,
    source: Mapping[str, Any],
    children_by_path_chapter_id: Mapping[str, GenerationTask],
) -> dict[str, Any]:
    current = dict(parent.result_refs or {})
    ordered_ids = list(source.get("path_chapter_ids") or [])
    chain: list[dict[str, Any]] = []
    for position, path_chapter_id in enumerate(ordered_ids, start=1):
        child = children_by_path_chapter_id.get(path_chapter_id)
        if not child:
            continue
        refs = dict(child.result_refs or {})
        chain.append(
            {
                "position": position,
                "path_chapter_id": path_chapter_id,
                "task_id": child.id,
                "task_status": child.status,
                "chapter_revision_id": refs.get("chapter_revision_id"),
                "provisional_status": refs.get("provisional_status"),
            }
        )
    result = {
        "requested_child_count": len(ordered_ids),
        "children": chain,
        "provisional_chain": chain,
        "review_required": True,
    }
    if "invalidated_from_position" in current:
        result["invalidated_from_position"] = current["invalidated_from_position"]
        result["invalidated_by_revision_id"] = current.get("invalidated_by_revision_id")
    return result


def _finish_parent(
    db: Session,
    *,
    parent: GenerationTask,
    source: Mapping[str, Any],
    children_by_path_chapter_id: Mapping[str, GenerationTask],
    partial: bool,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> None:
    if parent.status in TERMINAL_TASK_STATUSES:
        parent.result_refs = _parent_result_snapshot(
            parent,
            source,
            children_by_path_chapter_id,
        )
        return
    parent.error_code = error_code
    parent.error_detail = error_detail
    complete_task(
        db,
        parent,
        result_refs=_parent_result_snapshot(
            parent,
            source,
            children_by_path_chapter_id,
        ),
        actual_cost=0,
        partial=partial,
    )


def refresh_story_path_chapter_batch(db: Session, parent: GenerationTask) -> None:
    source = _batch_source(parent)
    if source is None:
        raise RuntimeError("task is not a sequential StoryPath chapter batch")
    ordered_ids = source.get("path_chapter_ids")
    if not isinstance(ordered_ids, list) or not ordered_ids:
        _finish_parent(
            db,
            parent=parent,
            source=source,
            children_by_path_chapter_id={},
            partial=True,
            error_code="chapter.batch_source_invalid",
            error_detail="Chapter batch has no ordered PathChapter source",
        )
        return

    children = (
        db.query(GenerationTask)
        .filter(GenerationTask.parent_task_id == parent.id)
        .order_by(GenerationTask.created_at, GenerationTask.id)
        .all()
    )
    children_by_path_chapter_id: dict[str, GenerationTask] = {}
    for child in children:
        path_chapter_id = _child_path_chapter_id(child)
        if (
            path_chapter_id not in ordered_ids
            or path_chapter_id in children_by_path_chapter_id
        ):
            _finish_parent(
                db,
                parent=parent,
                source=source,
                children_by_path_chapter_id=children_by_path_chapter_id,
                partial=True,
                error_code="chapter.batch_child_inconsistent",
                error_detail="Chapter batch contains an unexpected or duplicate child task",
            )
            return
        children_by_path_chapter_id[path_chapter_id] = child

    provisional_revision_ids: dict[str, str] = {}
    invalidated_from = (parent.result_refs or {}).get("invalidated_from_position")
    for position, path_chapter_id in enumerate(ordered_ids, start=1):
        child = children_by_path_chapter_id.get(path_chapter_id)
        if child is None:
            if isinstance(invalidated_from, int) and position >= invalidated_from:
                _finish_parent(
                    db,
                    parent=parent,
                    source=source,
                    children_by_path_chapter_id=children_by_path_chapter_id,
                    partial=True,
                    error_code="chapter.batch_chain_stale",
                    error_detail="A reviewed upstream chapter invalidated the remaining batch",
                )
                return
            try:
                next_source = _build_next_source(
                    db,
                    parent=parent,
                    source=source,
                    path_chapter_id=path_chapter_id,
                    provisional_revision_ids=provisional_revision_ids,
                )
                child = _create_child_task(
                    db,
                    parent=parent,
                    path_chapter_id=path_chapter_id,
                    source_refs=next_source,
                )
                children_by_path_chapter_id[path_chapter_id] = child
            except HTTPException as exc:
                _finish_parent(
                    db,
                    parent=parent,
                    source=source,
                    children_by_path_chapter_id=children_by_path_chapter_id,
                    partial=True,
                    error_code="chapter.batch_context_changed",
                    error_detail=str(exc.detail),
                )
                return
            parent.status = "running"
            parent.stage = f"children:{position - 1}/{len(ordered_ids)}"
            parent.progress = round((position - 1) * 100.0 / len(ordered_ids), 2)
            parent.result_refs = _parent_result_snapshot(
                parent,
                source,
                children_by_path_chapter_id,
            )
            append_task_event(
                db,
                parent,
                "progress",
                {
                    "completed_children": position - 1,
                    "total_children": len(ordered_ids),
                    "next_child_task_id": child.id,
                    "progress": parent.progress,
                },
            )
            return

        if child.status not in TERMINAL_TASK_STATUSES:
            parent.status = "running"
            parent.stage = f"children:{position - 1}/{len(ordered_ids)}"
            parent.progress = round((position - 1) * 100.0 / len(ordered_ids), 2)
            parent.result_refs = _parent_result_snapshot(
                parent,
                source,
                children_by_path_chapter_id,
            )
            return
        if child.status != "succeeded":
            _child_result(child, position=position, status=child.status)
            _finish_parent(
                db,
                parent=parent,
                source=source,
                children_by_path_chapter_id=children_by_path_chapter_id,
                partial=True,
                error_code="chapter.batch_child_failed",
                error_detail="A chapter child task failed before the provisional chain completed",
            )
            return

        refs = dict(child.result_refs or {})
        revision_id = refs.get("chapter_revision_id")
        revision = (
            db.query(ChapterRevision)
            .filter(
                ChapterRevision.id == revision_id,
                ChapterRevision.generation_task_id == child.id,
            )
            .one_or_none()
            if isinstance(revision_id, str)
            else None
        )
        if not revision or _child_path_chapter_id(child) != path_chapter_id:
            _finish_parent(
                db,
                parent=parent,
                source=source,
                children_by_path_chapter_id=children_by_path_chapter_id,
                partial=True,
                error_code="chapter.batch_result_inconsistent",
                error_detail="A succeeded chapter child has no matching revision",
            )
            return
        stale = isinstance(invalidated_from, int) and position >= invalidated_from
        _child_result(
            child,
            position=position,
            status="stale" if stale else refs.get("provisional_status") or "provisional",
        )
        provisional_revision_ids[path_chapter_id] = revision.id
        if stale:
            _finish_parent(
                db,
                parent=parent,
                source=source,
                children_by_path_chapter_id=children_by_path_chapter_id,
                partial=True,
                error_code="chapter.batch_chain_stale",
                error_detail="A reviewed upstream chapter invalidated the remaining batch",
            )
            return

    _finish_parent(
        db,
        parent=parent,
        source=source,
        children_by_path_chapter_id=children_by_path_chapter_id,
        partial=False,
    )


def assert_provisional_revision_selectable(
    db: Session,
    revision: ChapterRevision,
) -> None:
    if not revision.generation_task_id:
        return
    task = (
        db.query(GenerationTask)
        .filter(GenerationTask.id == revision.generation_task_id)
        .one_or_none()
    )
    if not task or not task.parent_task_id:
        return
    parent = db.query(GenerationTask).filter(GenerationTask.id == task.parent_task_id).one_or_none()
    if not parent or not is_sequential_story_path_chapter_batch(parent):
        return
    provisional_status = (task.result_refs or {}).get("provisional_status")
    if task.status != "succeeded" or provisional_status not in {"provisional", "accepted"}:
        raise AppError(
            code="chapter_revision.provisional_stale",
            message="This provisional chapter is stale and cannot be selected",
            status_code=409,
        )
    ancestors = revision.context_manifest.get("ancestors") if isinstance(
        revision.context_manifest, dict
    ) else None
    if not isinstance(ancestors, list):
        raise AppError(
            code="chapter_revision.provisional_context_invalid",
            message="This provisional chapter has an invalid ancestor chain",
            status_code=409,
        )
    if any(
        not isinstance(item, dict)
        or not isinstance(item.get("path_chapter_id"), str)
        or not isinstance(item.get("chapter_revision_id"), str)
        for item in ancestors
    ):
        raise AppError(
            code="chapter_revision.provisional_context_invalid",
            message="This provisional chapter has an invalid ancestor chain",
            status_code=409,
        )
    ancestor_ids = [item["path_chapter_id"] for item in ancestors]
    placements = (
        db.query(StoryPathChapter)
        .filter(StoryPathChapter.id.in_(ancestor_ids))
        .all()
        if ancestor_ids
        else []
    )
    placements_by_id = {placement.id: placement for placement in placements}
    if any(
        placements_by_id.get(item.get("path_chapter_id")) is None
        or placements_by_id[item["path_chapter_id"]].current_revision_id
        != item.get("chapter_revision_id")
        for item in ancestors
        if isinstance(item, dict)
    ):
        raise AppError(
            code="chapter_revision.provisional_ancestors_unreviewed",
            message="Review the provisional predecessor chapters first",
            status_code=409,
        )


def record_provisional_revision_selection(
    db: Session,
    *,
    placement: StoryPathChapter,
    revision: ChapterRevision,
) -> None:
    parents = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.project_id == revision.project_id,
            GenerationTask.kind == "chapter.batch",
        )
        .with_for_update()
        .all()
    )
    queued_to_cancel: list[GenerationTask] = []
    for parent in parents:
        source = _batch_source(parent)
        ordered_ids = source.get("path_chapter_ids") if source else None
        if not isinstance(ordered_ids, list) or placement.id not in ordered_ids:
            continue
        selected_position = ordered_ids.index(placement.id) + 1
        children = (
            db.query(GenerationTask)
            .filter(GenerationTask.parent_task_id == parent.id)
            .with_for_update()
            .all()
        )
        children_by_path_chapter_id = {
            path_chapter_id: child
            for child in children
            if (path_chapter_id := _child_path_chapter_id(child)) is not None
        }
        selected_child = children_by_path_chapter_id.get(placement.id)
        selected_matches = bool(
            selected_child
            and (selected_child.result_refs or {}).get("chapter_revision_id") == revision.id
        )
        if selected_matches:
            _child_result(selected_child, position=selected_position, status="accepted")
        else:
            current = dict(parent.result_refs or {})
            existing_position = current.get("invalidated_from_position")
            current["invalidated_from_position"] = (
                min(existing_position, selected_position)
                if isinstance(existing_position, int)
                else selected_position
            )
            current["invalidated_by_revision_id"] = revision.id
            parent.result_refs = current
            for position, path_chapter_id in enumerate(ordered_ids, start=1):
                if position < selected_position:
                    continue
                child = children_by_path_chapter_id.get(path_chapter_id)
                if not child:
                    continue
                _child_result(child, position=position, status="stale")
                if child.status == "queued":
                    queued_to_cancel.append(child)
        parent.result_refs = _parent_result_snapshot(
            parent,
            source,
            children_by_path_chapter_id,
        )

    db.flush()
    for child in queued_to_cancel:
        if child.status == "queued":
            request_cancel(db, child)


def create_story_path_chapter_batch(
    db: Session,
    *,
    user_id: int,
    story_path_id: str,
    idempotency_key: str,
    path_chapter_ids: list[str],
    parameters: dict[str, Any] | None = None,
    instructions: str | None = None,
    state_snapshot_id: str | None = None,
    bible_revision_id: str | None = None,
    outline_revision_id: str | None = None,
) -> tuple[GenerationTask, bool]:
    """Create a sequential batch whose next context uses prior provisional output."""

    if not isinstance(path_chapter_ids, list) or not path_chapter_ids:
        raise HTTPException(
            status_code=422,
            detail="path_chapter_ids must contain at least one PathChapter ID",
        )
    if any(
        not isinstance(path_chapter_id, str) or not path_chapter_id.strip()
        for path_chapter_id in path_chapter_ids
    ):
        raise HTTPException(status_code=422, detail="path_chapter_ids contains an invalid ID")
    if len(set(path_chapter_ids)) != len(path_chapter_ids):
        raise HTTPException(status_code=422, detail="path_chapter_ids must be unique")

    path = (
        db.query(StoryPath)
        .filter(StoryPath.id == story_path_id)
        .with_for_update()
        .one_or_none()
    )
    if not path:
        raise HTTPException(status_code=404, detail="StoryPath does not exist")
    if path.status != "active":
        raise HTTPException(status_code=409, detail="Archived StoryPath cannot generate chapters")

    all_placements, placements_by_id = _load_active_placements(
        db,
        story_path_id=story_path_id,
    )
    requested_ids = set(path_chapter_ids)
    if requested_ids - placements_by_id.keys():
        raise HTTPException(
            status_code=422,
            detail="Every path_chapter_id must belong to the requested StoryPath",
        )
    placements = sorted(
        (placements_by_id[path_chapter_id] for path_chapter_id in requested_ids),
        key=lambda placement: (placement.display_index, placement.id),
    )
    ordered_ids = [placement.id for placement in placements]
    normalized_parameters = normalize_chapter_generation_parameters(
        parameters,
        instructions=instructions,
    )

    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        existing_source = (existing.source_refs or {}).get("chapter_batch_source", {})
        existing_anchor = existing_source.get("context_anchor") or {}
        if not (
            existing.kind == "chapter.batch"
            and existing.project_id == path.project_id
            and existing_source.get("schema_version")
            == STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION
            and existing_source.get("story_path_id") == path.id
            and existing_source.get("path_chapter_ids") == ordered_ids
            and existing_source.get("requested_state_snapshot_id") == state_snapshot_id
            # 请求未显式锚定时沿用旧行为（无锚=当时 head，head 会漂移，不参与比较）。
            and (
                bible_revision_id is None
                or existing_anchor.get("bible_revision_id") == bible_revision_id
            )
            and (
                outline_revision_id is None
                or existing_anchor.get("outline_revision_id") == outline_revision_id
            )
            and existing.parameters == normalized_parameters
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key has already been used for a different request",
            )
        return existing, False

    baseline_revision_ids = {
        placement.id: placement.current_revision_id
        for placement in all_placements
        if placement.current_revision_id
    }
    requested_positions = {
        path_chapter_id: position for position, path_chapter_id in enumerate(ordered_ids)
    }
    for position, placement in enumerate(placements):
        for ancestor_id in _ancestor_ids(placement, placements_by_id):
            generated_position = requested_positions.get(ancestor_id)
            if generated_position is not None and generated_position < position:
                continue
            if ancestor_id not in baseline_revision_ids:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Every predecessor outside the generated prefix must have "
                        "a selected revision"
                    ),
                )

    first = placements[0]
    first_overrides = _revision_overrides_for_target(
        first,
        placements_by_id=placements_by_id,
        baseline_revision_ids=baseline_revision_ids,
        provisional_revision_ids={},
    )
    first_source = build_chapter_generation_source(
        db,
        path_chapter_id=first.id,
        parameters=normalized_parameters,
        state_snapshot_id=state_snapshot_id,
        bible_revision_id=bible_revision_id,
        outline_revision_id=outline_revision_id,
        ancestor_revision_overrides=first_overrides,
    )
    batch_source = {
        "schema_version": STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION,
        "orchestration": "sequential_provisional",
        "project_id": path.project_id,
        "story_path_id": path.id,
        "path_chapter_ids": ordered_ids,
        "topology": {
            placement.id: {
                "chapter_slot_id": placement.chapter_slot_id,
                "display_index": placement.display_index,
                "predecessor_path_chapter_id": placement.predecessor_path_chapter_id,
            }
            for placement in all_placements
        },
        "requested_state_snapshot_id": state_snapshot_id,
        "baseline_revision_ids": baseline_revision_ids,
        "context_anchor": _source_anchor(first_source),
    }
    parent, created = create_generation_task(
        db,
        user_id=user_id,
        project_id=path.project_id,
        kind="chapter.batch",
        idempotency_key=idempotency_key,
        source_refs={
            "chapter_batch_source": batch_source,
            "chapter_batch_source_hash": content_hash(batch_source),
        },
        parameters=normalized_parameters,
        estimated_cost=0,
        enqueue=False,
    )
    if not created:
        return parent, False

    parent.status = "running"
    parent.stage = f"children:0/{len(ordered_ids)}"
    parent.started_at = utcnow()
    parent.result_refs = {
        "requested_child_count": len(ordered_ids),
        "children": [],
        "provisional_chain": [],
        "review_required": True,
    }
    append_task_event(db, parent, "started", {"child_count": len(ordered_ids)})
    first_child = _create_child_task(
        db,
        parent=parent,
        path_chapter_id=first.id,
        source_refs=first_source,
    )
    parent.result_refs = _parent_result_snapshot(
        parent,
        batch_source,
        {first.id: first_child},
    )
    return parent, True


__all__ = [
    "LEGACY_STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION",
    "STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION",
    "assert_provisional_revision_selectable",
    "create_story_path_chapter_batch",
    "is_sequential_story_path_chapter_batch",
    "record_provisional_revision_selection",
    "refresh_story_path_chapter_batch",
]
