from __future__ import annotations

import logging
import socket
import threading
import uuid

from fastapi import HTTPException

from app.application.outbox_service import (
    claim_outbox_events,
    mark_publish_failed,
    mark_published,
)
from app.application.maintenance_service import (
    purge_unreferenced_deleted_media,
    recover_stale_tasks as recover_stale_tasks_service,
)
from app.application.story_generation_service import execute_generation_task
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import (
    TaskLease,
    TaskLeaseLostError,
    claim_task,
    fail_task,
    lease_from_task,
    renew_task_lease,
    task_lease_matches,
    update_parent_aggregate,
)
from app.application.vn_graph_service import execute_vn_graph_compile_task
from app.core.config import get_settings
from app.database import SessionLocal
from app.models_v2 import (
    BranchCandidate,
    ChoiceDecision,
    GenerationTask,
    GenerationTaskDependency,
)
from app.workers.celery_app import celery_app


def _worker_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4().hex[:12]}"


logger = logging.getLogger(__name__)


DEPENDENCY_WAIT_MAX_RETRIES_HARD_LIMIT = 720
_DEPENDENCY_SUCCESS_STATUSES = frozenset({"succeeded", "partial"})
_DEPENDENCY_FAILURE_STATUSES = frozenset({"failed", "cancelled"})


def _load_generation_task_dependencies(session, task_id: str) -> list[GenerationTask]:
    return (
        session.query(GenerationTask)
        .join(
            GenerationTaskDependency,
            GenerationTaskDependency.depends_on_task_id == GenerationTask.id,
        )
        .filter(GenerationTaskDependency.task_id == task_id)
        .all()
    )


def _fail_task_for_dependency_state(
    session,
    task_id: str,
    *,
    error_code: str,
    safe_detail: str,
    diagnostic: str | None = None,
) -> None:
    task = (
        session.query(GenerationTask)
        .filter(GenerationTask.id == task_id)
        .with_for_update()
        .first()
    )
    if task and task.status not in {"failed", "cancelled", "succeeded", "partial"}:
        fail_task(
            session,
            task,
            error_code=error_code,
            safe_detail=safe_detail,
            retryable=False,
            diagnostic=diagnostic,
        )
        update_parent_aggregate(session, task)
        session.commit()


def _retry_or_fail_pending_dependencies(self, session, task_id: str) -> bool:
    """Retry a bounded number of times; return True after terminal handling."""
    settings = get_settings()
    poll_seconds = settings.task_dependency_poll_seconds
    max_retries = min(
        settings.task_dependency_max_retries,
        DEPENDENCY_WAIT_MAX_RETRIES_HARD_LIMIT,
    )
    retry_count = max(0, int(getattr(self.request, "retries", 0) or 0))
    if retry_count < max_retries:
        session.rollback()
        raise self.retry(countdown=poll_seconds, max_retries=max_retries)

    # Close the old read transaction before the final check so a dependency that
    # completed on the timeout boundary is observed instead of being failed.
    session.rollback()
    dependencies = _load_generation_task_dependencies(session, task_id)
    failed_dependencies = [
        item for item in dependencies if item.status in _DEPENDENCY_FAILURE_STATUSES
    ]
    if failed_dependencies:
        _fail_task_for_dependency_state(
            session,
            task_id,
            error_code="dependency.failed",
            safe_detail="前置章节生成失败",
        )
        return True

    pending_dependencies = [
        item for item in dependencies if item.status not in _DEPENDENCY_SUCCESS_STATUSES
    ]
    if not pending_dependencies:
        return False

    pending_summary = ",".join(
        f"{item.id}:{item.status}" for item in pending_dependencies[:20]
    )
    diagnostic = (
        f"dependency wait exhausted retries={retry_count}/{max_retries}; "
        f"poll_seconds={poll_seconds}; pending={pending_summary}"
    )
    logger.error(
        "generation task dependency wait exhausted task_id=%s retries=%d/%d pending=%s",
        task_id,
        retry_count,
        max_retries,
        pending_summary,
    )
    _fail_task_for_dependency_state(
        session,
        task_id,
        error_code="dependency.timeout",
        safe_detail="等待前置章节生成超时，请检查前置任务状态后重试",
        diagnostic=diagnostic,
    )
    return True


def _heartbeat_loop(task_id: str, lease: TaskLease, stop: threading.Event) -> None:
    interval = max(10, get_settings().task_lease_seconds // 3)
    while not stop.wait(interval):
        session = SessionLocal()
        try:
            if not renew_task_lease(session, task_id, lease):
                session.rollback()
                return
            session.commit()
        except Exception:
            session.rollback()
            logger.exception(
                "task heartbeat failed; will retry task_id=%s lease_owner=%s",
                task_id,
                lease.owner,
            )
        finally:
            session.close()


@celery_app.task(
    name="if_line.run_generation_task",
    bind=True,
    max_retries=DEPENDENCY_WAIT_MAX_RETRIES_HARD_LIMIT,
)
def run_generation_task(self, task_id: str) -> None:
    worker_id = f"{self.request.hostname or socket.gethostname()}:{self.request.id}"
    session = SessionLocal()
    try:
        dependencies = _load_generation_task_dependencies(session, task_id)
        failed_dependencies = [
            item for item in dependencies if item.status in _DEPENDENCY_FAILURE_STATUSES
        ]
        pending_dependencies = [
            item for item in dependencies if item.status not in _DEPENDENCY_SUCCESS_STATUSES
        ]
        if failed_dependencies:
            _fail_task_for_dependency_state(
                session,
                task_id,
                error_code="dependency.failed",
                safe_detail="前置章节生成失败",
            )
            return
        if pending_dependencies:
            if _retry_or_fail_pending_dependencies(self, session, task_id):
                return
        task = claim_task(
            session,
            task_id,
            worker_id,
            lease_seconds=get_settings().task_lease_seconds,
        )
        if not task:
            session.rollback()
            return
        lease = lease_from_task(task)
        session.commit()
    finally:
        session.close()

    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(task_id, lease, heartbeat_stop),
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        execute_generation_task(task_id, lease)
    except TaskLeaseLostError:
        logger.warning(
            "discarding generation result after lease loss task_id=%s lease_owner=%s",
            task_id,
            lease.owner,
        )
        return
    except Exception as exc:
        # Log the full traceback to the worker journal. Without this every
        # chapter/outline failure was a silent retry — the user saw
        # "生成服务暂时不可用" three times and we had no way to diagnose.
        logger.exception(
            "run_generation_task failed task_id=%s kind=%s attempt=%d",
            task_id,
            getattr(exc, "kind", None) or "<unknown>",
            -1,
        )
        # 业务语义错误不是第三方服务抖动，不能自动重试。
        if isinstance(exc, NonRetryableTaskError):
            error_code = exc.code
            safe_detail = exc.safe_detail
            retryable = False
        elif isinstance(exc, HTTPException) and 400 <= int(exc.status_code) < 500:
            error_code = "task.validation_failed"
            safe_detail = str(exc.detail or "任务参数或前置条件不满足")
            retryable = False
        # Classify auth/quota errors as non-retryable so users see a real
        # diagnostic instead of three silent retries ending in "生成服务暂时
        # 不可用". 401/403 from the upstream provider will not heal on retry.
        else:
            msg = str(exc).lower()
            auth_failed = any(
                token in msg
                for token in (
                    "401", "unauthorized", "invalid api key", "invalid_api_key",
                    "authentication", "403", "forbidden", "permission",
                )
            )
            if auth_failed:
                error_code = "llm.auth_failed"
                safe_detail = "API Key 无效或已失效，请检查 .env 的 OPENAI_API_KEY"
                retryable = False
            else:
                error_code = "generation.failed"
                safe_detail = "生成服务暂时不可用，请稍后重试"
                retryable = True
        session = SessionLocal()
        try:
            task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().first()
            if task and task_lease_matches(task, lease):
                # Persist the raw exception class + first 500 chars so future
                # debugging doesn't require scraping the worker journal.
                # error_detail is also displayed to the user via the safe
                # message, so we keep both: safe prefix + diagnostic suffix.
                diagnostic = f"{type(exc).__name__}: {str(exc)[:500]}"
                fail_task(
                    session,
                    task,
                    error_code=error_code,
                    safe_detail=safe_detail,
                    retryable=retryable,
                    diagnostic=diagnostic,
                    lease=lease,
                )
                update_parent_aggregate(session, task)
                session.commit()
        finally:
            session.close()
        return
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=2)

@celery_app.task(
    name="if_line.complete_selected_branch",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def complete_selected_branch(decision_id: str) -> None:
    """Record that selected text is ready without claiming media completion.

    The current implementation deliberately does not create image, voice or
    VNGraph tasks. ``selected_text_ready`` is observable through the candidate
    API, is safe to write repeatedly, and remains distinct from a future
    media-complete state. Celery retries transient database failures.
    """

    session = SessionLocal()
    try:
        decision = session.query(ChoiceDecision).filter(ChoiceDecision.id == decision_id).first()
        if not decision:
            return
        candidate = (
            session.query(BranchCandidate)
            .filter(BranchCandidate.id == decision.candidate_id)
            .with_for_update()
            .first()
        )
        if candidate and candidate.candidate_status in {
            "preview_ready",
            "selected_text_ready",
            # Repair rows produced by the earlier placeholder worker, which
            # incorrectly called a text-only preview complete.
            "complete",
        }:
            if candidate.candidate_set_revision_id is None:
                candidate.candidate_status = "selected_text_ready"
        session.commit()
    finally:
        session.close()


@celery_app.task(name="if_line.vngraph_compile", bind=True)
def compile_vngraph(self, task_id: str) -> None:
    worker_id = f"vngraph-worker:{self.request.id}"
    session = SessionLocal()
    try:
        execute_vn_graph_compile_task(
            session,
            task_id=task_id,
            worker_id=worker_id,
        )
        session.commit()
    except Exception:
        # A failed flush leaves SQLAlchemy's transaction unusable. Roll it
        # back before loading the task again, then record the failed attempt
        # in a fresh transaction so the task never remains stuck in running.
        session.rollback()
        task = claim_task(
            session,
            task_id,
            f"{worker_id}:failure",
            lease_seconds=get_settings().task_lease_seconds,
        )
        if task:
            lease = lease_from_task(task)
            fail_task(
                session,
                task,
                error_code="vngraph_compile_failed",
                safe_detail="VNGraph 编译失败",
                retryable=True,
                lease=lease,
            )
            session.commit()
        else:
            session.rollback()
    finally:
        session.close()


@celery_app.task(name="if_line.dispatch_outbox")
def dispatch_outbox() -> int:
    dispatcher_id = _worker_id()
    session = SessionLocal()
    try:
        events = claim_outbox_events(
            session,
            dispatcher_id=dispatcher_id,
            limit=get_settings().outbox_batch_size,
        )
        event_payloads = [(event.id, event.event_type, dict(event.payload or {})) for event in events]
        session.commit()
    finally:
        session.close()

    published = 0
    for event_id, event_type, payload in event_payloads:
        try:
            handled = False
            if event_type in {
                "branch.candidates.generate.requested",
                "task.branch.candidates.generate.queued",
            }:
                celery_app.send_task(
                    "if_line.generate_branch_candidates",
                    args=[payload["task_id"]],
                )
                handled = True
            elif event_type == "reading.continuation.generate.requested":
                celery_app.send_task(
                    "if_line.generate_reading_continuation",
                    args=[payload["task_id"]],
                )
                handled = True
            elif event_type.startswith("task.") and event_type.endswith(".queued"):
                countdown = payload.get("countdown")
                countdown = countdown if isinstance(countdown, int) and countdown > 0 else None
                if event_type == "task.voice.slice.queued":
                    celery_app.send_task("if_line.voice_slice", args=[payload["task_id"]], countdown=countdown)
                elif event_type == "task.tts.render.queued":
                    celery_app.send_task("if_line.tts_render", args=[payload["task_id"]], countdown=countdown)
                elif event_type == "task.asset.render.queued":
                    celery_app.send_task("if_line.run_asset_task", args=[payload["task_id"]], countdown=countdown)
                else:
                    celery_app.send_task("if_line.run_generation_task", args=[payload["task_id"]], countdown=countdown)
                handled = True
            elif event_type == "vngraph.compile.requested":
                celery_app.send_task("if_line.vngraph_compile", args=[payload["task_id"]])
                handled = True
            elif event_type == "branch.complete_selected":
                celery_app.send_task(
                    "if_line.complete_selected_branch",
                    args=[payload["decision_id"]],
                )
                handled = True
            if not handled:
                raise ValueError("unsupported outbox event type")
            session = SessionLocal()
            try:
                mark_published(session, event_id, dispatcher_id)
                session.commit()
            finally:
                session.close()
            published += 1
        except Exception:
            session = SessionLocal()
            try:
                mark_publish_failed(session, event_id, dispatcher_id, "broker publish failed")
                session.commit()
            finally:
                session.close()
    return published


@celery_app.task(name="if_line.recover_stale_tasks")
def recover_stale_tasks_task() -> dict[str, int]:
    session = SessionLocal()
    try:
        result = recover_stale_tasks_service(
            session,
            lease_seconds=get_settings().task_lease_seconds,
        )
        session.commit()
        return result
    finally:
        session.close()


@celery_app.task(name="if_line.purge_deleted_media")
def purge_deleted_media_task() -> int:
    session = SessionLocal()
    try:
        result = purge_unreferenced_deleted_media(session)
        session.commit()
        return result
    finally:
        session.close()


# Celery autodiscovery loads this module. Import sibling task modules here so
# their named tasks are registered without making the API process execute them.
from app.workers import voice_tasks as _voice_tasks  # noqa: E402,F401
from app.workers import asset_tasks as _asset_tasks  # noqa: E402,F401
from app.workers import branch_tasks as _branch_tasks  # noqa: E402,F401
from app.workers import continuation_tasks as _continuation_tasks  # noqa: E402,F401
