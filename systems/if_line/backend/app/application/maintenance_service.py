from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.application.storage_service import (
    get_local_storage_backend,
    has_direct_storage_reference,
    is_referenced_by_any_release,
    is_referenced_by_published_release,
    purge_deleted_storage_object,
)
from app.application.task_service import (
    append_task_event,
    enqueue_event_for_kind,
    fail_task,
    revoke_task_lease,
)
from app.application.usage_service import release_usage
from app.models_v2 import GenerationTask, OutboxEvent, StorageObject, utcnow


NON_EXECUTING_PARENT_KINDS = {"chapter.batch", "asset.render.batch"}


def recover_stale_tasks(
    db: Session,
    *,
    lease_seconds: int,
    republish_after_seconds: int = 120,
    limit: int = 200,
) -> dict[str, int]:
    now = utcnow()
    stale_before = now - timedelta(seconds=lease_seconds)
    republish_before = now - timedelta(seconds=republish_after_seconds)
    recovered = 0
    failed = 0
    cancelled = 0

    running = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.status == "running",
            GenerationTask.kind.notin_(NON_EXECUTING_PARENT_KINDS),
            (GenerationTask.heartbeat_at.is_(None)) | (GenerationTask.heartbeat_at < stale_before),
        )
        .order_by(GenerationTask.updated_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
        .all()
    )
    for task in running:
        if task.cancel_requested_at:
            task.status = "cancelled"
            task.finished_at = now
            revoke_task_lease(task)
            release_usage(db, task, "cancelled_after_stale_lease")
            append_task_event(db, task, "cancelled", {"recovered_stale_lease": True})
            cancelled += 1
            continue
        if task.attempt >= task.max_attempts:
            revoke_task_lease(task)
            fail_task(
                db,
                task,
                error_code="worker.lease_expired",
                safe_detail="任务执行进程中断且已达到最大重试次数",
                retryable=False,
            )
            failed += 1
            continue
        task.status = "queued"
        task.stage = "recovered"
        revoke_task_lease(task)
        append_task_event(db, task, "recovered", {"previous_attempt": task.attempt})
        db.add(
            OutboxEvent(
                aggregate_type="generation_task",
                aggregate_id=task.id,
                event_type=enqueue_event_for_kind(task.kind),
                payload={"task_id": task.id, "kind": task.kind, "recovered": True},
            )
        )
        recovered += 1

    queued = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.status == "queued",
            GenerationTask.kind.notin_(NON_EXECUTING_PARENT_KINDS),
            GenerationTask.updated_at < republish_before,
        )
        .order_by(GenerationTask.updated_at)
        .limit(max(0, limit - recovered))
        .all()
    )
    for task in queued:
        recent_outbox = (
            db.query(OutboxEvent)
            .filter(
                OutboxEvent.aggregate_type == "generation_task",
                OutboxEvent.aggregate_id == task.id,
                OutboxEvent.created_at >= republish_before,
            )
            .first()
        )
        if recent_outbox:
            continue
        db.add(
            OutboxEvent(
                aggregate_type="generation_task",
                aggregate_id=task.id,
                event_type=enqueue_event_for_kind(task.kind),
                payload={"task_id": task.id, "kind": task.kind, "republished": True},
            )
        )
        append_task_event(db, task, "republished", {})
        recovered += 1
    return {"recovered": recovered, "failed": failed, "cancelled": cancelled}


def purge_unreferenced_deleted_media(
    db: Session,
    *,
    grace_hours: int = 24,
    limit: int = 100,
) -> int:
    cutoff = utcnow() - timedelta(hours=grace_hours)
    objects = (
        db.query(StorageObject)
        .filter(
            StorageObject.status == "deleted",
            StorageObject.deleted_at.is_not(None),
            StorageObject.deleted_at < cutoff,
        )
        .order_by(StorageObject.deleted_at)
        .limit(limit)
        .all()
    )
    backend = get_local_storage_backend()
    purged = 0
    for obj in objects:
        if has_direct_storage_reference(db, obj) or is_referenced_by_any_release(db, obj):
            continue
        purge_deleted_storage_object(obj, backend=backend)
        purged += 1
    return purged
