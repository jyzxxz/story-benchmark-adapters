from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from app.application.task_service import (
    TaskLease,
    append_task_event,
    claim_task,
    complete_task,
    fail_task,
    heartbeat_task,
    lease_from_task,
    revoke_task_lease,
    task_lease_matches,
)
from app.application.usage_service import release_usage
from app.application.visual_asset_service import (
    CONTINUATION_TASK_KIND,
    persist_generated_continuation,
    prepare_continuation_generation_request,
)
from app.database import SessionLocal
from app.integrations.llm.continuation_adapter import (
    CONTINUATION_PROMPT_VERSION,
    ContinuationProvider,
    LegacyContinuationLLMAdapter,
)
from app.models_v2 import GenerationTask, ProviderUsageRecord, ReadingContinuation
from app.schemas_continuation_generation import GeneratedContinuation
from app.workers.celery_app import celery_app
from app.workers.task_lease import TaskLeaseHeartbeat


CONTINUATION_CELERY_TASK_NAME = "if_line.generate_reading_continuation"
CONTINUATION_TASK_QUEUE = "text"
logger = logging.getLogger(__name__)


def _fail(
    task_id: str,
    *,
    session_factory: sessionmaker,
    lease: TaskLease,
    code: str,
    detail: str,
    retryable: bool,
) -> None:
    session = session_factory()
    try:
        task = (
            session.query(GenerationTask)
            .filter(GenerationTask.id == task_id)
            .with_for_update()
            .first()
        )
        if task and task_lease_matches(task, lease):
            fail_task(
                session,
                task,
                error_code=code,
                safe_detail=detail,
                retryable=retryable,
                lease=lease,
            )
            if task.status == "failed":
                continuation_id = (task.source_refs or {}).get("continuation_id")
                continuation = (
                    session.query(ReadingContinuation)
                    .filter(
                        ReadingContinuation.id == continuation_id,
                        ReadingContinuation.generation_task_id == task.id,
                        ReadingContinuation.status == "processing",
                    )
                    .first()
                )
                if continuation:
                    continuation.status = "failed"
                    continuation.error = {
                        "code": code,
                        "message": detail,
                    }
            session.commit()
    finally:
        session.close()


async def execute_continuation_task(
    task_id: str,
    *,
    provider: ContinuationProvider,
    worker_id: str,
    session_factory: sessionmaker = SessionLocal,
) -> str | None:
    lease: TaskLease | None = None
    session = session_factory()
    try:
        existing = session.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        if existing and existing.status == "succeeded":
            return (existing.result_refs or {}).get("continuation_id")
        task = claim_task(session, task_id, worker_id)
        if not task or task.kind != CONTINUATION_TASK_KIND:
            session.rollback()
            return None
        # Production sessions disable autoflush. Persist the queued and
        # started events before heartbeat_task allocates the next sequence,
        # otherwise both pending events can receive the same seq value.
        lease = lease_from_task(task)
        session.flush()
        request = prepare_continuation_generation_request(session, task)
        heartbeat_task(session, task, lease, stage="calling_llm", progress=15)
        session.commit()
    except Exception:
        logger.exception("Reading continuation source preparation failed task_id=%s", task_id)
        session.rollback()
        session.close()
        if lease is not None:
            _fail(
                task_id,
                session_factory=session_factory,
                lease=lease,
                code="continuation.invalid_source",
                detail="续写来源快照无效",
                retryable=False,
            )
        return None
    finally:
        if session.is_active:
            session.close()

    try:
        with TaskLeaseHeartbeat(
            task_id,
            lease,
            session_factory=session_factory,
        ):
            provider_result = await provider.generate_continuation(request)
    except Exception as exc:
        logger.exception("Reading continuation provider failed task_id=%s", task_id)
        timed_out = isinstance(exc, (TimeoutError, asyncio.TimeoutError))
        _fail(
            task_id,
            session_factory=session_factory,
            lease=lease,
            code="continuation.provider_timeout" if timed_out else "continuation.provider_failed",
            detail="续写模型响应超时，请重新生成" if timed_out else "续写服务暂时不可用，请重新生成",
            retryable=True,
        )
        return None
    try:
        if provider_result.prompt_version != CONTINUATION_PROMPT_VERSION:
            raise ValueError("prompt version mismatch")
        generated = GeneratedContinuation.model_validate(provider_result.data)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.warning(
            "Reading continuation provider output rejected task_id=%s error=%s",
            task_id,
            exc,
        )
        _fail(
            task_id,
            session_factory=session_factory,
            lease=lease,
            code="continuation.invalid_provider_output",
            detail="续写服务返回格式无效",
            retryable=False,
        )
        return None

    session = session_factory()
    try:
        task = (
            session.query(GenerationTask)
            .filter(GenerationTask.id == task_id)
            .with_for_update()
            .first()
        )
        if not task or not task_lease_matches(task, lease):
            session.rollback()
            return None
        if task.cancel_requested_at:
            task.status = "cancelled"
            task.stage = "cancelled"
            revoke_task_lease(task)
            release_usage(session, task, "cancelled_before_persist")
            append_task_event(session, task, "cancelled", {"reason": "cancelled_before_persist"})
            session.commit()
            return None
        heartbeat_task(session, task, lease, stage="matching_visuals", progress=75)
        # complete_task appends another task event in this transaction. Flush
        # the progress event first so it receives a distinct sequence number
        # when SessionLocal has autoflush disabled.
        session.flush()
        continuation = persist_generated_continuation(session, task=task, generated=generated)
        session.add(
            ProviderUsageRecord(
                task_id=task.id,
                provider=provider_result.provider,
                model=provider_result.model,
                provider_request_id=provider_result.provider_request_id,
                input_tokens=max(0, int(provider_result.input_tokens or 0)),
                output_tokens=max(0, int(provider_result.output_tokens or 0)),
                latency_ms=max(0, int(provider_result.latency_ms or 0)),
                cost_amount=Decimal("0"),
                cost_currency="CNY",
                cache_hit=False,
            )
        )
        complete_task(
            session,
            task,
            result_refs={
                "continuation_id": continuation.id,
                "scene_manifest_id": continuation.scene_manifest_id,
                "asset_action_id": continuation.asset_action_id,
                "asset_version_ids": continuation.frozen_asset_version_ids,
            },
            actual_cost=Decimal(task.reserved_cost or task.estimated_cost or 0),
            lease=lease,
        )
        session.commit()
        return continuation.id
    except Exception:
        logger.exception("Reading continuation persistence failed task_id=%s", task_id)
        session.rollback()
        session.close()
        _fail(
            task_id,
            session_factory=session_factory,
            lease=lease,
            code="continuation.persist_failed",
            detail="续写预览保存失败",
            retryable=True,
        )
        return None
    finally:
        if session.is_active:
            session.close()


@celery_app.task(name=CONTINUATION_CELERY_TASK_NAME)
def run_continuation_task(task_id: str) -> str | None:
    return asyncio.run(
        execute_continuation_task(
            task_id,
            provider=LegacyContinuationLLMAdapter(),
            worker_id=f"continuation-worker:{task_id}:{uuid4()}",
        )
    )
