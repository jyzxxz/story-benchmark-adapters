from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from fastapi import HTTPException
from sqlalchemy import case, update
from sqlalchemy.orm import Session

from app.models import User
from app.models_v2 import GenerationTask, UsageLedgerEntry, UsageReservation, utcnow
from app.quota import reset_daily_quota_if_needed


ZERO = Decimal("0")


def _credits(value: Decimal | int | str) -> Decimal:
    amount = Decimal(str(value))
    if amount < ZERO:
        raise ValueError("usage amount cannot be negative")
    return amount.quantize(Decimal("0.000001"))


def _legacy_units(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def reserve_usage(db: Session, task: GenerationTask, amount: Decimal | int | str) -> UsageReservation:
    """Reserve credits atomically using the legacy counters as the balance gate.

    The append-only reservation and ledger are the v2 source of truth.  Updating
    the existing counters keeps the current account UI compatible during the
    migration and prevents two concurrent reservations from overspending.
    """

    reserved = _credits(amount)
    units = _legacy_units(reserved)
    user = db.query(User).filter(User.id == task.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    reset_daily_quota_if_needed(db, user)
    if units:
        statement = (
            update(User)
            .where(
                User.id == task.user_id,
                User.quota_used_total + units <= User.quota_total,
                User.quota_used_daily + units <= User.quota_daily,
            )
            .values(
                quota_used_total=User.quota_used_total + units,
                quota_used_daily=User.quota_used_daily + units,
            )
        )
        if db.execute(statement).rowcount != 1:
            raise HTTPException(status_code=402, detail="额度不足，任务未入队")

    reservation = UsageReservation(
        user_id=task.user_id,
        project_id=task.project_id,
        task_id=task.id,
        estimated_amount=reserved,
        reserved_amount=reserved,
    )
    db.add(reservation)
    db.flush()
    db.add(
        UsageLedgerEntry(
            reservation_id=reservation.id,
            user_id=task.user_id,
            project_id=task.project_id,
            task_id=task.id,
            activity_type=task.kind,
            entry_type="reserve",
            amount=reserved,
            event_metadata={"kind": task.kind},
            created_at=utcnow(),
        )
    )
    task.reserved_cost = reserved
    return reservation


def settle_usage(db: Session, task: GenerationTask, actual_amount: Decimal | int | str) -> UsageReservation | None:
    reservation = (
        db.query(UsageReservation)
        .filter(UsageReservation.task_id == task.id)
        .with_for_update()
        .first()
    )
    if not reservation or reservation.status != "reserved":
        return reservation

    actual = _credits(actual_amount)
    reserved = Decimal(reservation.reserved_amount or ZERO)
    delta = actual - reserved
    legacy_delta = _legacy_units(actual) - _legacy_units(reserved)

    if legacy_delta > 0:
        statement = (
            update(User)
            .where(
                User.id == task.user_id,
                User.quota_used_total + legacy_delta <= User.quota_total,
                User.quota_used_daily + legacy_delta <= User.quota_daily,
            )
            .values(
                quota_used_total=User.quota_used_total + legacy_delta,
                quota_used_daily=User.quota_used_daily + legacy_delta,
            )
        )
        if db.execute(statement).rowcount != 1:
            # The provider call already happened.  Preserve accounting and flag
            # the account for follow-up instead of losing the actual usage.
            user = db.query(User).filter(User.id == task.user_id).with_for_update().one()
            user.quota_used_total += legacy_delta
            user.quota_used_daily += legacy_delta
    elif legacy_delta < 0:
        total_after = User.quota_used_total + legacy_delta
        daily_after = User.quota_used_daily + legacy_delta
        db.execute(
            update(User)
            .where(User.id == task.user_id)
            .values(
                # A daily reset may have happened while the provider task was
                # running. Refunds must never make compatibility counters
                # negative; the append-only ledger remains the exact record.
                quota_used_total=case((total_after < 0, 0), else_=total_after),
                quota_used_daily=case((daily_after < 0, 0), else_=daily_after),
            )
        )

    reservation.status = "settled"
    reservation.settled_amount = actual
    reservation.refunded_amount = max(ZERO, -delta)
    reservation.settled_at = utcnow()
    task.actual_cost = actual
    db.add(
        UsageLedgerEntry(
            reservation_id=reservation.id,
            user_id=task.user_id,
            project_id=task.project_id,
            task_id=task.id,
            activity_type=task.kind,
            entry_type="settle" if delta >= ZERO else "refund",
            amount=delta,
            event_metadata={"actual": str(actual), "reserved": str(reserved)},
            created_at=utcnow(),
        )
    )
    return reservation


def release_usage(db: Session, task: GenerationTask, reason: str) -> UsageReservation | None:
    reservation = (
        db.query(UsageReservation)
        .filter(UsageReservation.task_id == task.id)
        .with_for_update()
        .first()
    )
    if not reservation or reservation.status != "reserved":
        return reservation

    reserved = Decimal(reservation.reserved_amount or ZERO)
    units = _legacy_units(reserved)
    if units:
        user = db.query(User).filter(User.id == task.user_id).with_for_update().one()
        user.quota_used_total = max(0, user.quota_used_total - units)
        user.quota_used_daily = max(0, user.quota_used_daily - units)

    reservation.status = "released"
    reservation.refunded_amount = reserved
    reservation.settled_at = utcnow()
    db.add(
        UsageLedgerEntry(
            reservation_id=reservation.id,
            user_id=task.user_id,
            project_id=task.project_id,
            task_id=task.id,
            activity_type=task.kind,
            entry_type="refund",
            amount=-reserved,
            event_metadata={"reason": reason},
            created_at=utcnow(),
        )
    )
    return reservation


def reopen_usage(db: Session, task: GenerationTask) -> UsageReservation:
    """Re-reserve the original amount when a terminal failure is retried.

    Retries reuse the same reservation and append a new ledger entry.  This
    preserves the task-to-reservation one-to-one relationship while ensuring a
    released failed task cannot be run again without passing the balance gate.
    """

    reservation = (
        db.query(UsageReservation)
        .filter(UsageReservation.task_id == task.id)
        .with_for_update()
        .first()
    )
    if not reservation:
        return reserve_usage(db, task, task.estimated_cost or ZERO)
    if reservation.status == "reserved":
        return reservation
    if reservation.status not in {"released", "cancelled"}:
        raise HTTPException(status_code=409, detail="任务费用已经结算，不能重新预留")

    reserved = _credits(reservation.reserved_amount or task.estimated_cost or ZERO)
    units = _legacy_units(reserved)
    user = db.query(User).filter(User.id == task.user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    reset_daily_quota_if_needed(db, user)
    if units:
        statement = (
            update(User)
            .where(
                User.id == task.user_id,
                User.quota_used_total + units <= User.quota_total,
                User.quota_used_daily + units <= User.quota_daily,
            )
            .values(
                quota_used_total=User.quota_used_total + units,
                quota_used_daily=User.quota_used_daily + units,
            )
        )
        if db.execute(statement).rowcount != 1:
            raise HTTPException(status_code=402, detail="额度不足，失败任务未重新入队")

    reservation.status = "reserved"
    reservation.settled_amount = ZERO
    reservation.refunded_amount = ZERO
    reservation.settled_at = None
    task.reserved_cost = reserved
    db.add(
        UsageLedgerEntry(
            reservation_id=reservation.id,
            user_id=task.user_id,
            project_id=task.project_id,
            task_id=task.id,
            activity_type=task.kind,
            entry_type="reserve",
            amount=reserved,
            event_metadata={"kind": task.kind, "reason": "retry"},
            created_at=utcnow(),
        )
    )
    return reservation
