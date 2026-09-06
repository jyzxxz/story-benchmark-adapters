"""Worker boundary for pre-publication branch preview generation."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.application.story_branch_contract import (
    BRANCH_GENERATION_TASK_KIND,
    BranchGenerationOutputError,
    validate_generated_candidate_batch,
)
from app.application.story_branch_service import (
    persist_candidate_set_revision,
    resolve_candidate_set_generation_source,
)
from app.application.task_service import (
    TaskLease,
    append_task_event,
    claim_task,
    complete_task,
    fail_task,
    heartbeat_task,
    lease_from_task,
    require_task_lease,
    revoke_task_lease,
    task_lease_matches,
    update_parent_aggregate,
)
from app.application.usage_service import release_usage
from app.database import SessionLocal
from app.integrations.llm.branch_adapter import (
    BRANCH_PROMPT_VERSION,
    BranchCandidateProvider,
    LegacyBranchLLMAdapter,
)
from app.models_v2 import GenerationTask, ProviderUsageRecord, utcnow
from app.workers.celery_app import celery_app
from app.workers.task_lease import TaskLeaseHeartbeat


BRANCH_CELERY_TASK_NAME = "if_line.generate_branch_candidates"
BRANCH_TASK_QUEUE = "text"
BRANCH_CELERY_ROUTE = {BRANCH_CELERY_TASK_NAME: {"queue": BRANCH_TASK_QUEUE}}
BRANCH_HEARTBEAT_SECONDS = 30.0


class BranchWorkerLeaseLost(RuntimeError):
    pass


def _finalize_cancelled(
    session: Session,
    task: GenerationTask,
    lease: TaskLease,
    *,
    reason: str,
) -> None:
    require_task_lease(task, lease)
    task.status = "cancelled"
    task.stage = "cancelled"
    task.finished_at = utcnow()
    revoke_task_lease(task)
    release_usage(session, task, reason)
    append_task_event(session, task, "cancelled", {"reason": reason})
    update_parent_aggregate(session, task)


def _mark_failed(
    task_id: str,
    *,
    session_factory: sessionmaker,
    lease: TaskLease,
    error_code: str,
    safe_detail: str,
    retryable: bool,
) -> None:
    session: Session = session_factory()
    try:
        task = (
            session.query(GenerationTask)
            .filter(GenerationTask.id == task_id)
            .with_for_update()
            .first()
        )
        if task and task_lease_matches(task, lease):
            if task.cancel_requested_at:
                _finalize_cancelled(
                    session,
                    task,
                    lease,
                    reason="cancelled_before_failure",
                )
            else:
                fail_task(
                    session,
                    task,
                    error_code=error_code,
                    safe_detail=safe_detail,
                    retryable=retryable,
                    lease=lease,
                )
            session.commit()
    finally:
        session.close()


async def _call_provider_with_heartbeat(
    task_id: str,
    *,
    request,
    provider: BranchCandidateProvider,
    session_factory: sessionmaker,
    lease: TaskLease,
):
    provider_call = asyncio.create_task(provider.generate_candidates(request))
    try:
        with TaskLeaseHeartbeat(
            task_id,
            lease,
            session_factory=session_factory,
            interval_seconds=int(BRANCH_HEARTBEAT_SECONDS),
        ) as heartbeat:
            while True:
                done, _ = await asyncio.wait(
                    {provider_call},
                    timeout=BRANCH_HEARTBEAT_SECONDS,
                )
                if provider_call in done:
                    return provider_call.result()
                if heartbeat.lease_lost:
                    raise BranchWorkerLeaseLost(
                        "分支候选任务租约已丢失或任务已取消"
                    )
    finally:
        if not provider_call.done():
            provider_call.cancel()
            with suppress(BaseException):
                await provider_call


async def execute_branch_generation_task(
    task_id: str,
    *,
    provider: BranchCandidateProvider,
    worker_id: str,
    session_factory: sessionmaker = SessionLocal,
) -> list[str]:
    """Claim/prepare, call the LLM without a DB transaction, then persist."""

    session: Session = session_factory()
    try:
        existing = session.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        if existing and existing.status == "succeeded":
            return list((existing.result_refs or {}).get("candidate_ids") or [])
        task = claim_task(session, task_id, worker_id)
        if not task or task.kind != BRANCH_GENERATION_TASK_KIND:
            session.rollback()
            return []
        lease = lease_from_task(task)
        session.commit()
    finally:
        session.close()

    # Prepare and verify immutable source rows in a short transaction.
    session = session_factory()
    try:
        task = (
            session.query(GenerationTask)
            .filter(
                GenerationTask.id == task_id,
                GenerationTask.status == "running",
                GenerationTask.lease_owner == lease.owner,
                GenerationTask.lease_token == lease.token,
            )
            .with_for_update()
            .first()
        )
        if not task:
            session.rollback()
            return []
        request = resolve_candidate_set_generation_source(session, task=task).request
        expected_count = int((task.parameters or {}).get("candidate_count") or 0)
        heartbeat_task(session, task, lease, stage="calling_llm", progress=15)
        session.commit()
    except Exception:
        session.rollback()
        session.close()
        _mark_failed(
            task_id,
            session_factory=session_factory,
            lease=lease,
            error_code="branch.invalid_source",
            safe_detail="分支候选来源快照无效",
            retryable=False,
        )
        return []
    finally:
        if session.is_active:
            session.close()

    # No Session is open while waiting on the provider.
    try:
        provider_result = await _call_provider_with_heartbeat(
            task_id,
            request=request,
            provider=provider,
            session_factory=session_factory,
            lease=lease,
        )
    except BranchWorkerLeaseLost:
        return []
    except Exception:
        _mark_failed(
            task_id,
            session_factory=session_factory,
            lease=lease,
            error_code="branch.provider_failed",
            safe_detail="分支候选生成服务暂时不可用",
            retryable=True,
        )
        return []

    try:
        if provider_result.prompt_version != BRANCH_PROMPT_VERSION:
            raise BranchGenerationOutputError("LLM prompt_version 与任务不一致")
        batch = validate_generated_candidate_batch(
            provider_result.data,
            expected_count=expected_count,
        )
    except BranchGenerationOutputError:
        _mark_failed(
            task_id,
            session_factory=session_factory,
            lease=lease,
            error_code="branch.invalid_provider_output",
            safe_detail="分支候选输出格式无效",
            retryable=False,
        )
        return []

    # Persist the complete candidate set atomically. No partial set can be
    # observed, and no media task is created by this worker.
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
            return []
        if task.cancel_requested_at:
            _finalize_cancelled(
                session,
                task,
                lease,
                reason="cancelled_before_persist",
            )
            session.commit()
            return []
        heartbeat_task(session, task, lease, stage="persisting_previews", progress=80)
        persistence = persist_candidate_set_revision(
            session,
            task=task,
            batch=batch,
        )
        candidates = list(persistence.candidates)
        created = persistence.created
        candidate_set_revision_id = persistence.revision.id
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
                "source_hash": (task.source_refs or {}).get("source_hash"),
                "checkpoint_node_id": (task.source_refs or {}).get("checkpoint_node_id"),
                "story_path_id": (task.source_refs or {}).get("story_path_id"),
                "candidate_set_revision_id": candidate_set_revision_id,
                "candidate_ids": [candidate.id for candidate in candidates],
                "preview_node_ids": [candidate.preview_node_id for candidate in candidates],
                "candidate_count": len(candidates),
                "created": created,
                "media_generated": False,
                "review_required": True,
            },
            actual_cost=Decimal(task.reserved_cost or task.estimated_cost or 0),
            lease=lease,
        )
        session.commit()
        return [candidate.id for candidate in candidates]
    except BranchGenerationOutputError:
        session.rollback()
        session.close()
        _mark_failed(
            task_id,
            session_factory=session_factory,
            lease=lease,
            error_code="branch.persist_conflict",
            safe_detail="Checkpoint 候选已变化，请创建新的 checkpoint",
            retryable=False,
        )
        return []
    except Exception:
        session.rollback()
        session.close()
        _mark_failed(
            task_id,
            session_factory=session_factory,
            lease=lease,
            error_code="branch.persist_failed",
            safe_detail="分支候选保存失败",
            retryable=True,
        )
        return []
    finally:
        if session.is_active:
            session.close()


@celery_app.task(name=BRANCH_CELERY_TASK_NAME)
def run_branch_generation_task(task_id: str) -> list[str]:
    return asyncio.run(
        execute_branch_generation_task(
            task_id,
            provider=LegacyBranchLLMAdapter(),
            worker_id=f"branch-worker:{task_id}:{uuid4()}",
        )
    )
