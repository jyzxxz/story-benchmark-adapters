from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.story_path_task_compatibility import (
    inspect_story_path_task_source,
)
from app.application.usage_service import reopen_usage, release_usage, reserve_usage, settle_usage
from app.models_v2 import (
    GenerationTask,
    GenerationTaskDependency,
    OutboxEvent,
    ScriptResourceSlot,
    TaskEvent,
    utcnow,
)


TERMINAL_TASK_STATUSES = {"partial", "succeeded", "failed", "cancelled"}

TASK_ENQUEUE_EVENTS = {
    "asset.render": "task.asset.render.queued",
    "voice.slice": "task.voice.slice.queued",
    "tts.render": "task.tts.render.queued",
    "chapter_script.generate": "task.chapter_script.generate.queued",
    "vngraph.compile": "vngraph.compile.requested",
    "branch.candidates.generate": "task.branch.candidates.generate.queued",
    "reading.continuation.generate": "reading.continuation.generate.requested",
}


@dataclass(frozen=True)
class TaskLease:
    owner: str
    token: str


class TaskLeaseLostError(RuntimeError):
    """Raised when a worker attempts to mutate a task after losing its lease."""


def enqueue_event_for_kind(kind: str) -> str:
    return TASK_ENQUEUE_EVENTS.get(kind, f"task.{kind}.queued")


def _as_utc(value: datetime) -> datetime:
    """Normalize database datetimes for portable lease comparisons.

    PostgreSQL preserves ``timezone=True`` while SQLite returns a naive value.
    Treat a naive persisted timestamp as UTC instead of mixing aware and naive
    values or changing lease semantics between development and production.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def append_task_event(
    db: Session,
    task: GenerationTask,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> TaskEvent:
    last_seq = (
        db.query(func.max(TaskEvent.seq))
        .filter(TaskEvent.task_id == task.id)
        .scalar()
        or 0
    )
    event = TaskEvent(
        task_id=task.id,
        seq=last_seq + 1,
        event_type=event_type,
        payload=payload or {},
    )
    db.add(event)
    # Production sessions disable autoflush. Persist each event before the
    # next sequence allocation so consecutive events in one transaction
    # cannot all observe the same database max(seq).
    db.flush([event])
    return event


def create_generation_task(
    db: Session,
    *,
    user_id: int,
    project_id: int | None,
    kind: str,
    idempotency_key: str,
    source_refs: dict[str, Any] | None = None,
    parameters: dict[str, Any] | None = None,
    estimated_cost: Decimal | int | str = 0,
    parent_task_id: str | None = None,
    root_task_id: str | None = None,
    enqueue_event_type: str | None = None,
    enqueue: bool = True,
    idempotency_request: dict[str, Any] | None = None,
) -> tuple[GenerationTask, bool]:
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="缺少有效的 Idempotency-Key")

    parameters = parameters or {}
    source_refs = source_refs or {}
    normalized_cost = Decimal(str(estimated_cost)).quantize(Decimal("0.000001"))
    if normalized_cost < 0:
        raise HTTPException(status_code=422, detail="estimated_cost 不能为负数")
    # HTTP idempotency identifies the client's request. Frozen source refs may
    # legitimately differ when a mutable Head moves before a replay arrives.
    hash_payload: dict[str, Any] = {
        "kind": kind,
        "project_id": project_id,
        "estimated_cost": str(normalized_cost),
        "parent_task_id": parent_task_id,
        "root_task_id": root_task_id,
        "enqueue_event_type": enqueue_event_type,
        "enqueue": enqueue,
    }
    if idempotency_request is None:
        hash_payload.update(
            {
                "source_refs": source_refs,
                "parameters": parameters,
            }
        )
    else:
        hash_payload["idempotency_request"] = idempotency_request
    parameter_hash = content_hash(hash_payload)

    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        if existing.user_id != user_id or existing.kind != kind or existing.parameters_hash != parameter_hash:
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        if enqueue and existing.status in {"failed", "partial"} and not existing.cancel_requested_at:
            # Returning a terminal failed/partial task as a no-op leaves the
            # caller permanently unable to re-run the same generation (every
            # retry path is also blocked once attempt == max_attempts).  A
            # re-request with identical parameters means the user wants a
            # fresh execution, so revive the task and re-enqueue it.
            revive_generation_task(db, existing, enqueue_event_type=enqueue_event_type)
        return existing, False

    task = GenerationTask(
        user_id=user_id,
        project_id=project_id,
        kind=kind,
        idempotency_key=idempotency_key,
        source_refs=source_refs,
        parameters=parameters,
        parameters_hash=parameter_hash,
        estimated_cost=normalized_cost,
        parent_task_id=parent_task_id,
        root_task_id=root_task_id,
    )
    try:
        # Keep task creation, quota reservation and outbox creation in one
        # savepoint.  A 402 must not leave an orphan task behind, and a
        # uniqueness race must not roll back unrelated caller changes.
        with db.begin_nested():
            db.add(task)
            db.flush()
            reserve_usage(db, task, normalized_cost)
            append_task_event(db, task, "queued", {"kind": kind})
            if enqueue:
                db.add(
                    OutboxEvent(
                        aggregate_type="generation_task",
                        aggregate_id=task.id,
                        event_type=enqueue_event_type or enqueue_event_for_kind(kind),
                        payload={"task_id": task.id, "kind": kind},
                    )
                )
    except IntegrityError:
        existing = (
            db.query(GenerationTask)
            .filter(
                GenerationTask.user_id == user_id,
                GenerationTask.idempotency_key == idempotency_key,
            )
            .first()
        )
        if (
            existing
            and existing.user_id == user_id
            and existing.kind == kind
            and existing.parameters_hash == parameter_hash
        ):
            return existing, False
        raise HTTPException(status_code=409, detail="任务幂等冲突")
    return task, True


def revive_generation_task(
    db: Session,
    task: GenerationTask,
    *,
    enqueue_event_type: str | None = None,
) -> None:
    """Reset a terminal failed/partial task so a re-request can re-run it.

    Failed workers had their usage reservation released, so a fresh reservation
    is taken here.  ``partial`` tasks (aggregate batches) are only flipped back
    to ``running`` — their children are revived individually and the aggregate
    recomputes as children finish.
    """
    previous_status = task.status
    task.status = "queued" if previous_status == "failed" else "running"
    task.stage = "retrying"
    task.progress = 0.0
    task.attempt = 0
    task.error_code = None
    task.error_detail = None
    task.finished_at = None
    task.cancel_requested_at = None
    task.lease_owner = None
    task.lease_token = None
    append_task_event(db, task, "revived", {"previous_status": previous_status})
    if previous_status == "failed":
        reopen_usage(db, task)
        db.add(
            OutboxEvent(
                aggregate_type="generation_task",
                aggregate_id=task.id,
                event_type=enqueue_event_type or enqueue_event_for_kind(task.kind),
                payload={"task_id": task.id, "kind": task.kind, "retry": True},
            )
        )


def get_owned_task(db: Session, task_id: str, user_id: int) -> GenerationTask:
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.user_id != user_id:
        # Avoid leaking another user's task existence.
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def claim_task(db: Session, task_id: str, worker_id: str, *, lease_seconds: int = 120) -> GenerationTask | None:
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().first()
    if not task or task.status in TERMINAL_TASK_STATUSES or task.cancel_requested_at:
        return None
    stale_before = utcnow() - timedelta(seconds=lease_seconds)
    if task.status == "running" and task.heartbeat_at and _as_utc(task.heartbeat_at) >= stale_before:
        return None
    task.status = "running"
    task.stage = task.stage or "starting"
    task.lease_owner = worker_id
    task.lease_token = str(uuid4())
    task.heartbeat_at = utcnow()
    task.started_at = task.started_at or utcnow()
    task.attempt += 1
    append_task_event(db, task, "started", {"attempt": task.attempt})
    return task


def lease_from_task(task: GenerationTask) -> TaskLease:
    if not task.lease_owner or not task.lease_token:
        raise TaskLeaseLostError("task has no active fenced lease")
    return TaskLease(owner=task.lease_owner, token=task.lease_token)


def task_lease_matches(task: GenerationTask, lease: TaskLease) -> bool:
    return bool(
        task.status == "running"
        and task.lease_owner == lease.owner
        and task.lease_token == lease.token
    )


def require_task_lease(task: GenerationTask, lease: TaskLease) -> None:
    if not task_lease_matches(task, lease):
        raise TaskLeaseLostError("task lease is no longer owned by this worker")


def revoke_task_lease(task: GenerationTask) -> None:
    task.lease_owner = None
    task.lease_token = None
    task.heartbeat_at = None


def renew_task_lease(db: Session, task_id: str, lease: TaskLease) -> bool:
    task = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.id == task_id,
            GenerationTask.status == "running",
            GenerationTask.lease_owner == lease.owner,
            GenerationTask.lease_token == lease.token,
        )
        .with_for_update()
        .one_or_none()
    )
    if not task:
        return False
    task.heartbeat_at = utcnow()
    return True


def heartbeat_task(
    db: Session,
    task: GenerationTask,
    lease: TaskLease,
    *,
    stage: str,
    progress: float,
) -> None:
    require_task_lease(task, lease)
    task.heartbeat_at = utcnow()
    task.stage = stage
    task.progress = max(0.0, min(100.0, progress))
    append_task_event(db, task, "progress", {"stage": stage, "progress": task.progress})


def update_parent_aggregate(db: Session, child: GenerationTask) -> None:
    if not child.parent_task_id:
        return
    parent = (
        db.query(GenerationTask)
        .filter(GenerationTask.id == child.parent_task_id)
        .with_for_update()
        .first()
    )
    if not parent or parent.status in TERMINAL_TASK_STATUSES:
        return
    from app.application.story_chapter_batch_service import (
        is_sequential_story_path_chapter_batch,
        refresh_story_path_chapter_batch,
    )

    if is_sequential_story_path_chapter_batch(parent):
        refresh_story_path_chapter_batch(db, parent)
        return
    children = db.query(GenerationTask).filter(GenerationTask.parent_task_id == parent.id).all()
    if not children:
        return
    terminal = [item for item in children if item.status in TERMINAL_TASK_STATUSES]
    parent.progress = round(len(terminal) * 100.0 / len(children), 2)
    parent.stage = f"children:{len(terminal)}/{len(children)}"
    append_task_event(
        db,
        parent,
        "progress",
        {"completed_children": len(terminal), "total_children": len(children), "progress": parent.progress},
    )
    if len(terminal) != len(children):
        parent.status = "running"
        return
    failed = [item for item in children if item.status in {"failed", "cancelled"}]
    parent.status = "partial" if failed else "succeeded"
    parent.stage = "completed"
    parent.progress = 100.0
    parent.finished_at = utcnow()
    parent.result_refs = {
        "children": [
            {"task_id": item.id, "status": item.status, "result_refs": item.result_refs}
            for item in children
        ]
    }
    settle_usage(db, parent, 0)
    append_task_event(
        db,
        parent,
        "partial" if failed else "completed",
        {"failed_children": len(failed), "total_children": len(children)},
    )


def complete_task(
    db: Session,
    task: GenerationTask,
    *,
    result_refs: dict[str, Any],
    actual_cost: Decimal | int | str,
    partial: bool = False,
    lease: TaskLease | None = None,
) -> None:
    has_active_lease = task.lease_owner is not None or task.lease_token is not None
    if has_active_lease:
        if lease is None:
            raise TaskLeaseLostError("active task completion requires its fenced lease")
        require_task_lease(task, lease)
    elif lease is not None:
        raise TaskLeaseLostError("task no longer owns the supplied lease")
    task.status = "partial" if partial else "succeeded"
    task.stage = "completed"
    task.progress = 100.0
    task.result_refs = result_refs
    task.finished_at = utcnow()
    revoke_task_lease(task)
    settle_usage(db, task, actual_cost)
    append_task_event(db, task, "partial" if partial else "completed", {"result_refs": result_refs})


def fail_task(
    db: Session,
    task: GenerationTask,
    *,
    error_code: str,
    safe_detail: str,
    retryable: bool,
    diagnostic: str | None = None,
    lease: TaskLease | None = None,
) -> None:
    has_active_lease = task.lease_owner is not None or task.lease_token is not None
    if has_active_lease:
        if lease is None:
            raise TaskLeaseLostError("active task failure requires its fenced lease")
        require_task_lease(task, lease)
    elif lease is not None:
        raise TaskLeaseLostError("task no longer owns the supplied lease")
    task.error_code = error_code
    task.error_detail = safe_detail[:2000]
    revoke_task_lease(task)
    # Persist the raw exception class + message on every failure (retry or
    # terminal) so engineers can diagnose without scraping the worker journal.
    # This is intentionally a separate task_event so the user-facing
    # error_detail stays safely masked.
    if diagnostic:
        append_task_event(
            db,
            task,
            "attempt.failed",
            {"error_code": error_code, "diagnostic": diagnostic[:1000]},
        )
    if retryable and task.attempt < task.max_attempts and not task.cancel_requested_at:
        task.status = "queued"
        task.stage = "retrying"
        append_task_event(db, task, "retrying", {"error_code": error_code, "attempt": task.attempt})
        # Exponential backoff: transient provider rejections (e.g. image API
        # rate limiting) burn all max_attempts within seconds when retried
        # immediately, turning recoverable failures into terminal ones.
        retry_countdown = min(300, 15 * (2 ** max(0, task.attempt - 1)))
        db.add(
            OutboxEvent(
                aggregate_type="generation_task",
                aggregate_id=task.id,
                event_type=enqueue_event_for_kind(task.kind),
                payload={
                    "task_id": task.id,
                    "kind": task.kind,
                    "retry": True,
                    "countdown": retry_countdown,
                },
            )
        )
        return
    task.status = "failed"
    task.finished_at = utcnow()
    release_usage(db, task, f"failed:{error_code}")
    append_task_event(db, task, "failed", {"error_code": error_code, "detail": safe_detail[:500]})


def request_cancel(db: Session, task: GenerationTask) -> None:
    if task.status in TERMINAL_TASK_STATUSES:
        return
    # Provider calls are intentionally performed outside a database
    # transaction and cannot be recalled reliably once a worker owns the
    # lease.  Rejecting a late cancellation is clearer than acknowledging it
    # and subsequently charging for a task that still completes.
    if task.status != "queued":
        raise HTTPException(status_code=409, detail="任务已经开始，只能取消尚未执行的任务")
    task.cancel_requested_at = utcnow()
    task.status = "cancelled"
    task.finished_at = utcnow()
    release_usage(db, task, "cancelled_before_start")
    append_task_event(db, task, "cancelled", {})
    update_parent_aggregate(db, task)


def retry_task(db: Session, task: GenerationTask) -> None:
    if task.kind == "asset.render.batch" and task.status == "partial":
        children = (
            db.query(GenerationTask)
            .join(
                GenerationTaskDependency,
                GenerationTaskDependency.depends_on_task_id == GenerationTask.id,
            )
            .filter(
                GenerationTaskDependency.task_id == task.id,
                GenerationTask.status == "failed",
            )
            .all()
        )
        retryable_children = [child for child in children if child.attempt < child.max_attempts]
        if not retryable_children:
            raise HTTPException(status_code=409, detail="批任务没有可重试的失败子任务")
        task.status = "running"
        task.stage = "retrying_failed_children"
        task.finished_at = None
        task.progress = 0.0
        append_task_event(
            db,
            task,
            "retrying",
            {"failed_child_task_ids": [child.id for child in retryable_children]},
        )
        for child in retryable_children:
            retry_task(db, child)
        return
    if task.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败任务可以重试")
    if task.attempt >= task.max_attempts:
        raise HTTPException(status_code=409, detail="任务已达到最大重试次数")
    compatibility = inspect_story_path_task_source(
        task.kind,
        source_refs=task.source_refs,
        parameters=task.parameters,
    )
    if compatibility.managed and not compatibility.compatible:
        raise HTTPException(
            status_code=409,
            detail="任务来源属于已删除的旧版创作接口，不能重试",
        )
    # A terminal failure has already released its reservation.  Re-open the
    # same reservation before mutating task status so a 402 leaves it failed.
    reopen_usage(db, task)
    task.status = "queued"
    task.error_code = None
    task.error_detail = None
    task.finished_at = None
    task.cancel_requested_at = None
    append_task_event(db, task, "queued", {"retry": True})
    db.add(
        OutboxEvent(
            aggregate_type="generation_task",
            aggregate_id=task.id,
            event_type=enqueue_event_for_kind(task.kind),
            payload={"task_id": task.id, "kind": task.kind, "retry": True},
        )
    )
    if task.kind == "asset.render":
        slots = db.query(ScriptResourceSlot).filter(
            ScriptResourceSlot.generation_task_id == task.id,
            ScriptResourceSlot.status == "failed",
        )
        for slot in slots:
            slot.status = "generating"
