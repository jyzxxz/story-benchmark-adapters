from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.task_service import (
    TERMINAL_TASK_STATUSES,
    TaskLeaseLostError,
    claim_task,
    complete_task,
    create_generation_task,
    enqueue_event_for_kind,
    heartbeat_task,
    fail_task,
    lease_from_task,
    request_cancel,
    retry_task,
)
from app.application.usage_service import release_usage
from app.application.chapter_batch_service import create_chapter_batch
from app.application.revision_service import create_bible_revision, create_outline_revision
from app.database import Base
from app.models import Project, User
from app.models_v2 import (
    GenerationTask,
    GenerationTaskDependency,
    OutboxEvent,
    TaskEvent,
    UsageLedgerEntry,
    UsageReservation,
)
from app.schemas_v2 import GenerationRequest
from app.workers import tasks as worker_tasks


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    # This is also the SQLite schema smoke test for all additive v2 models.
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _owner_and_project(db, *, quota: int = 20, suffix: str = "one"):
    user = User(
        email=f"v2-{suffix}@example.com",
        password_hash="hash",
        display_name="v2 owner",
        quota_total=quota,
        quota_daily=quota,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title=f"v2 project {suffix}",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    return user, project


def test_sqlite_create_all_registers_task_and_revision_tables(db):
    tables = set(inspect(db.get_bind()).get_table_names())
    assert {
        "generation_tasks",
        "task_events",
        "outbox_events",
        "usage_reservations",
        "story_bible_revisions",
        "outline_revisions",
        "chapter_revisions",
        "state_snapshots",
    } <= tables


def test_task_idempotency_reuses_only_the_same_request(db):
    user, project = _owner_and_project(db)
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="same-request",
        source_refs={"chapter_index": 1},
        parameters={"model": "fast"},
        estimated_cost=Decimal("2.25"),
    )
    db.flush()

    replay, replay_created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="same-request",
        source_refs={"chapter_index": 1},
        parameters={"model": "fast"},
        estimated_cost=Decimal("2.25"),
    )
    db.flush()

    assert created is True
    assert replay_created is False
    assert replay.id == task.id
    assert db.query(GenerationTask).count() == 1
    assert db.query(UsageReservation).count() == 1
    assert db.query(OutboxEvent).count() == 1
    assert [(row.seq, row.event_type) for row in db.query(TaskEvent).all()] == [(1, "queued")]
    assert user.quota_used_total == 3
    assert user.quota_used_daily == 3

    different_requests = [
        {"parameters": {"model": "quality"}, "estimated_cost": Decimal("2.25")},
        {"parameters": {"model": "fast"}, "estimated_cost": Decimal("4.00")},
    ]
    for change in different_requests:
        with pytest.raises(HTTPException) as exc:
            create_generation_task(
                db,
                user_id=user.id,
                project_id=project.id,
                kind="chapter.generate",
                idempotency_key="same-request",
                source_refs={"chapter_index": 1},
                parameters=change["parameters"],
                estimated_cost=change["estimated_cost"],
            )
        assert exc.value.status_code == 409

    second_project = Project(
        owner_id=user.id,
        title="second project",
        story_start="start",
        story_end="end",
    )
    db.add(second_project)
    db.flush()
    with pytest.raises(HTTPException) as exc:
        create_generation_task(
            db,
            user_id=user.id,
            project_id=second_project.id,
            kind="chapter.generate",
            idempotency_key="same-request",
            source_refs={"chapter_index": 1},
            parameters={"model": "fast"},
            estimated_cost=Decimal("2.25"),
        )
    assert exc.value.status_code == 409

    # Idempotency keys are scoped to the authenticated user, so one account
    # cannot reserve common keys and deny service to another account.
    other_user, other_project = _owner_and_project(db, suffix="other-user")
    other_task, other_created = create_generation_task(
        db,
        user_id=other_user.id,
        project_id=other_project.id,
        kind="chapter.generate",
        idempotency_key="same-request",
        source_refs={"chapter_index": 1},
        parameters={"model": "fast"},
        estimated_cost=Decimal("2.25"),
    )
    db.flush()
    assert other_created is True
    assert other_task.id != task.id


def test_task_idempotency_can_ignore_mutable_frozen_source_drift(db):
    user, project = _owner_and_project(db, suffix="stable-request")
    request_identity = {
        "story_path_id": "path-1",
        "parameters": {"chapter_count": 12},
    }
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="stable-request",
        source_refs={"outline_source": {"parent_revision_id": "head-1"}},
        parameters={"chapter_count": 12},
        estimated_cost=4,
        idempotency_request=request_identity,
    )
    replay, replay_created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="stable-request",
        source_refs={"outline_source": {"parent_revision_id": "head-2"}},
        parameters={"chapter_count": 12},
        estimated_cost=4,
        idempotency_request=request_identity,
    )

    assert created is True
    assert replay_created is False
    assert replay.id == task.id
    assert replay.source_refs == {
        "outline_source": {"parent_revision_id": "head-1"}
    }

    with pytest.raises(HTTPException) as captured:
        create_generation_task(
            db,
            user_id=user.id,
            project_id=project.id,
            kind="outline.generate",
            idempotency_key="stable-request",
            source_refs={"outline_source": {"parent_revision_id": "head-2"}},
            parameters={"chapter_count": 13},
            estimated_cost=4,
            idempotency_request={
                "story_path_id": "path-1",
                "parameters": {"chapter_count": 13},
            },
        )

    assert captured.value.status_code == 409


def test_public_generation_request_rejects_client_pricing():
    request = GenerationRequest(parameters={"model": "fast"})
    assert request.parameters == {"model": "fast"}
    with pytest.raises(ValidationError):
        GenerationRequest(parameters={}, estimated_cost=0)

    app = FastAPI()

    @app.post("/generate")
    def _generate(body: GenerationRequest):
        return body.model_dump()

    response = TestClient(app).post(
        "/generate",
        json={"parameters": {}, "estimated_cost": 0},
    )
    assert response.status_code == 422


def test_usage_reservation_is_atomic_and_refundable(db):
    user, project = _owner_and_project(db, quota=5, suffix="quota")
    first, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="image.render",
        idempotency_key="quota-first",
        estimated_cost=Decimal("3.2"),
    )
    db.commit()
    assert user.quota_used_total == 4

    with pytest.raises(HTTPException) as exc:
        create_generation_task(
            db,
            user_id=user.id,
            project_id=project.id,
            kind="image.render",
            idempotency_key="quota-overflow",
            estimated_cost=Decimal("2"),
        )
    assert exc.value.status_code == 402
    db.rollback()
    assert db.query(GenerationTask).count() == 1

    release_usage(db, first, "test-release")
    db.commit()
    db.refresh(user)
    assert user.quota_used_total == 0
    assert user.quota_used_daily == 0
    reservation = db.query(UsageReservation).filter_by(task_id=first.id).one()
    assert reservation.status == "released"
    assert Decimal(reservation.refunded_amount) == Decimal("3.200000")

    settled, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="image.render",
        idempotency_key="quota-settle",
        estimated_cost=Decimal("3.2"),
    )
    claim_task(db, settled.id, "worker-1")
    lease = lease_from_task(settled)
    complete_task(
        db,
        settled,
        result_refs={"asset": "ok"},
        actual_cost=Decimal("1.2"),
        lease=lease,
    )
    db.commit()
    db.refresh(user)
    assert user.quota_used_total == 2
    settled_reservation = db.query(UsageReservation).filter_by(task_id=settled.id).one()
    assert settled_reservation.status == "settled"
    assert Decimal(settled_reservation.settled_amount) == Decimal("1.200000")
    assert Decimal(settled_reservation.refunded_amount) == Decimal("2.000000")
    ledger_types = [
        row.entry_type
        for row in db.query(UsageLedgerEntry)
        .filter(UsageLedgerEntry.task_id == settled.id)
        .order_by(UsageLedgerEntry.created_at, UsageLedgerEntry.id)
    ]
    assert ledger_types == ["reserve", "refund"]


def test_task_events_are_monotonic_and_terminal_tasks_are_not_reclaimed(db):
    user, project = _owner_and_project(db, suffix="events")
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="event-order",
        estimated_cost=1,
    )
    assert claim_task(db, task.id, "worker-1") is task
    lease = lease_from_task(task)
    heartbeat_task(db, task, lease, stage="writing", progress=35)
    complete_task(
        db,
        task,
        result_refs={"revision_id": "r1"},
        actual_cost=1,
        lease=lease,
    )
    db.flush()

    events = db.query(TaskEvent).filter_by(task_id=task.id).order_by(TaskEvent.seq).all()
    assert [event.seq for event in events] == [1, 2, 3, 4]
    assert [event.event_type for event in events] == ["queued", "started", "progress", "completed"]
    assert claim_task(db, task.id, "worker-2") is None

    partial, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.batch",
        idempotency_key="partial-terminal",
        estimated_cost=1,
    )
    claim_task(db, partial.id, "worker-1")
    partial_lease = lease_from_task(partial)
    complete_task(
        db,
        partial,
        result_refs={"completed": [1], "failed": [2]},
        actual_cost=1,
        partial=True,
        lease=partial_lease,
    )
    db.flush()
    assert "partial" in TERMINAL_TASK_STATUSES
    assert claim_task(db, partial.id, "worker-2") is None


def test_active_lease_survives_sqlite_timezone_round_trip(db):
    user, project = _owner_and_project(db, suffix="lease")
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="active-lease",
        estimated_cost=0,
    )
    claim_task(db, task.id, "worker-1")
    db.commit()
    db.expunge_all()

    reloaded = db.query(GenerationTask).filter_by(id=task.id).one()
    assert reloaded.heartbeat_at is not None
    assert claim_task(db, reloaded.id, "worker-2", lease_seconds=120) is None


def test_reclaimed_task_rejects_late_completion_and_failure_from_old_token(db):
    user, project = _owner_and_project(db, suffix="lease-fencing")
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="lease-fencing",
        estimated_cost=0,
    )
    assert claim_task(db, task.id, "same-worker-name") is task
    old_lease = lease_from_task(task)
    task.heartbeat_at = datetime.now().astimezone() - timedelta(minutes=10)
    db.flush()

    assert claim_task(db, task.id, "same-worker-name", lease_seconds=1) is task
    new_lease = lease_from_task(task)
    assert new_lease.owner == old_lease.owner
    assert new_lease.token != old_lease.token

    with pytest.raises(TaskLeaseLostError):
        complete_task(
            db,
            task,
            result_refs={"revision_id": "late"},
            actual_cost=0,
            lease=old_lease,
        )
    with pytest.raises(TaskLeaseLostError):
        fail_task(
            db,
            task,
            error_code="late.failure",
            safe_detail="late",
            retryable=False,
            lease=old_lease,
        )
    complete_task(
        db,
        task,
        result_refs={"revision_id": "winner"},
        actual_cost=0,
        lease=new_lease,
    )
    assert task.status == "succeeded"
    assert task.result_refs == {"revision_id": "winner"}


def test_tokenless_legacy_worker_lease_cannot_write_a_terminal_state(db):
    user, project = _owner_and_project(db, suffix="legacy-lease")
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="legacy-tokenless-lease",
        estimated_cost=0,
    )
    task.status = "running"
    task.lease_owner = "pre-fencing-worker"
    task.lease_token = None
    db.flush()

    with pytest.raises(TaskLeaseLostError):
        complete_task(
            db,
            task,
            result_refs={"revision_id": "unsafe"},
            actual_cost=0,
        )
    with pytest.raises(TaskLeaseLostError):
        fail_task(
            db,
            task,
            error_code="unsafe.failure",
            safe_detail="unsafe",
            retryable=False,
        )
    assert task.status == "running"


def test_legacy_chapter_batch_rejects_outline_without_story_path_identity(db):
    user, project = _owner_and_project(db, quota=30, suffix="batch")
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "w"},
        source={"origin": "test"},
        user_id=user.id,
    )
    create_outline_revision(
        db,
        project_id=project.id,
        bible_revision_id=bible.id,
        chapters=[
            {"chapter_index": 1, "title": "one"},
            {"chapter_index": 2, "title": "two"},
            {"chapter_index": 3, "title": "three"},
        ],
        user_id=user.id,
    )
    with pytest.raises(HTTPException) as captured:
        create_chapter_batch(
            db,
            user_id=user.id,
            project_id=project.id,
            idempotency_key="batch-one",
            chapter_indexes=[1, 2, 3],
            parameters={"stream": True},
        )
    assert captured.value.status_code == 409
    assert "StoryPath" in str(captured.value.detail)
    assert db.query(GenerationTask).count() == 0
    assert db.query(OutboxEvent).count() == 0


def test_retry_reopens_the_same_usage_reservation(db):
    user, project = _owner_and_project(db, quota=5, suffix="retry")
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="asset.render",
        idempotency_key="retry-reservation",
        estimated_cost=3,
    )
    claim_task(db, task.id, "worker-1")
    lease = lease_from_task(task)
    fail_task(
        db,
        task,
        error_code="provider_rejected",
        safe_detail="rejected",
        retryable=False,
        lease=lease,
    )
    db.flush()
    assert task.status == "failed"
    assert user.quota_used_total == 0
    reservation_id = db.query(UsageReservation).filter_by(task_id=task.id).one().id

    retry_task(db, task)
    db.flush()
    assert task.status == "queued"
    assert user.quota_used_total == 3
    reservations = db.query(UsageReservation).filter_by(task_id=task.id).all()
    assert len(reservations) == 1
    assert reservations[0].id == reservation_id
    assert reservations[0].status == "reserved"
    assert [
        row.entry_type
        for row in db.query(UsageLedgerEntry)
        .filter_by(task_id=task.id)
        .order_by(UsageLedgerEntry.created_at, UsageLedgerEntry.id)
    ] == ["reserve", "refund", "reserve"]


def test_continuation_retries_route_back_to_the_specialized_worker():
    assert (
        enqueue_event_for_kind("reading.continuation.generate")
        == "reading.continuation.generate.requested"
    )


def _pending_dependency_pair(db, suffix: str):
    user, project = _owner_and_project(db, suffix=suffix)
    dependency, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key=f"{suffix}:dependency",
        estimated_cost=0,
    )
    waiting, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key=f"{suffix}:waiting",
        estimated_cost=0,
    )
    db.flush()
    db.add(
        GenerationTaskDependency(
            task_id=waiting.id,
            depends_on_task_id=dependency.id,
            required_status="succeeded",
        )
    )
    db.commit()
    return dependency, waiting


class _DependencyRetryRequested(Exception):
    pass


class _FakeBoundTask:
    def __init__(self, retries: int):
        self.request = SimpleNamespace(retries=retries)
        self.retry_kwargs = None

    def retry(self, **kwargs):
        self.retry_kwargs = kwargs
        return _DependencyRetryRequested()


def test_generation_worker_dependency_retry_is_finite(db, monkeypatch):
    _dependency, waiting = _pending_dependency_pair(db, "dependency-bounded")
    monkeypatch.setattr(
        worker_tasks,
        "get_settings",
        lambda: SimpleNamespace(
            task_dependency_poll_seconds=7,
            task_dependency_max_retries=2,
        ),
    )
    bound_task = _FakeBoundTask(retries=0)
    worker_session = sessionmaker(
        bind=db.get_bind(),
        expire_on_commit=False,
        autoflush=False,
    )()
    try:
        with pytest.raises(_DependencyRetryRequested):
            worker_tasks._retry_or_fail_pending_dependencies(
                bound_task,
                worker_session,
                waiting.id,
            )
    finally:
        worker_session.close()

    assert worker_tasks.run_generation_task.max_retries == 720
    assert bound_task.retry_kwargs == {"countdown": 7, "max_retries": 2}


def test_generation_worker_marks_dependency_timeout_after_retry_limit(db, monkeypatch):
    dependency, waiting = _pending_dependency_pair(db, "dependency-timeout")
    monkeypatch.setattr(
        worker_tasks,
        "get_settings",
        lambda: SimpleNamespace(
            task_dependency_poll_seconds=5,
            task_dependency_max_retries=2,
        ),
    )
    bound_task = _FakeBoundTask(retries=2)
    worker_session = sessionmaker(
        bind=db.get_bind(),
        expire_on_commit=False,
        autoflush=False,
    )()
    try:
        handled = worker_tasks._retry_or_fail_pending_dependencies(
            bound_task,
            worker_session,
            waiting.id,
        )
    finally:
        worker_session.close()

    db.expire_all()
    timed_out = db.query(GenerationTask).filter_by(id=waiting.id).one()
    events = (
        db.query(TaskEvent)
        .filter_by(task_id=waiting.id)
        .order_by(TaskEvent.seq)
        .all()
    )

    assert handled is True
    assert bound_task.retry_kwargs is None
    assert timed_out.status == "failed"
    assert timed_out.error_code == "dependency.timeout"
    assert timed_out.finished_at is not None
    assert [event.event_type for event in events][-2:] == ["attempt.failed", "failed"]
    assert dependency.id in events[-2].payload["diagnostic"]
    assert "retries=2/2" in events[-2].payload["diagnostic"]


def test_only_queued_tasks_can_be_cancelled(db):
    user, project = _owner_and_project(db, suffix="cancel")
    queued, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="cancel-queued",
        estimated_cost=1,
    )
    request_cancel(db, queued)
    db.flush()
    assert queued.status == "cancelled"
    assert user.quota_used_total == 0

    running, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="cancel-running",
        estimated_cost=1,
    )
    claim_task(db, running.id, "worker-1")
    with pytest.raises(HTTPException) as exc:
        request_cancel(db, running)
    assert exc.value.status_code == 409
    assert running.cancel_requested_at is None


def test_v2_reservation_resets_expired_daily_counter(db):
    user, project = _owner_and_project(db, quota=10, suffix="daily-reset")
    user.quota_used_total = 2
    user.quota_used_daily = 10
    user.quota_reset_at = datetime.utcnow() - timedelta(days=1)
    db.flush()

    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="daily-reset-task",
        estimated_cost=2,
    )
    db.flush()

    assert created is True
    assert task.status == "queued"
    assert user.quota_used_total == 4
    assert user.quota_used_daily == 2


def test_terminal_failed_task_is_revived_on_identical_re_request(db):
    user, project = _owner_and_project(db, quota=30, suffix="revive")
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="asset.render",
        idempotency_key="asset-render-revive",
        estimated_cost=2,
    )
    db.commit()
    assert created is True

    # Simulate a worker exhausting all attempts: fail_task releases the
    # usage reservation and marks the task terminally failed.
    lease = claim_task(db, task.id, "worker-1")
    fail_task(
        db,
        task,
        error_code="asset.render_failed",
        safe_detail="boom",
        retryable=False,
        lease=lease_from_task(lease),
    )
    db.commit()
    assert task.status == "failed"

    outbox_before = db.query(OutboxEvent).count()
    task_events_before = db.query(TaskEvent).count()

    # The user re-requests the exact same generation (same idempotency key
    # and parameters).  It must be revived and re-enqueued, not returned as
    # a frozen no-op.
    revived, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="asset.render",
        idempotency_key="asset-render-revive",
        estimated_cost=2,
    )
    db.commit()

    assert created is False
    assert revived.id == task.id
    assert revived.status == "queued"
    assert revived.attempt == 0
    assert revived.error_code is None
    assert revived.finished_at is None
    assert db.query(OutboxEvent).count() == outbox_before + 1
    assert db.query(TaskEvent).count() == task_events_before + 1  # "revived" event
