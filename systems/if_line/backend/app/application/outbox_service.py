from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models_v2 import OutboxEvent, utcnow


def claim_outbox_events(
    db: Session,
    *,
    dispatcher_id: str,
    limit: int,
    lease_seconds: int = 60,
) -> list[OutboxEvent]:
    stale_before = utcnow() - timedelta(seconds=lease_seconds)
    query = (
        db.query(OutboxEvent)
        .filter(
            OutboxEvent.available_at <= utcnow(),
            (
                (OutboxEvent.status == "pending")
                | ((OutboxEvent.status == "publishing") & (OutboxEvent.leased_at < stale_before))
            ),
        )
        .order_by(OutboxEvent.created_at)
        .limit(limit)
    )
    try:
        query = query.with_for_update(skip_locked=True)
    except TypeError:
        query = query.with_for_update()
    events = query.all()
    for event in events:
        event.status = "publishing"
        event.lease_owner = dispatcher_id
        event.leased_at = utcnow()
        event.attempt += 1
    return events


def mark_published(db: Session, event_id: str, dispatcher_id: str) -> None:
    event = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.id == event_id, OutboxEvent.lease_owner == dispatcher_id)
        .with_for_update()
        .first()
    )
    if event:
        event.status = "published"
        event.published_at = utcnow()
        event.lease_owner = None
        event.leased_at = None


def mark_publish_failed(db: Session, event_id: str, dispatcher_id: str, safe_error: str) -> None:
    event = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.id == event_id, OutboxEvent.lease_owner == dispatcher_id)
        .with_for_update()
        .first()
    )
    if not event:
        return
    event.last_error = safe_error[:1000]
    event.lease_owner = None
    event.leased_at = None
    if event.attempt >= 20:
        event.status = "failed"
    else:
        event.status = "pending"
        event.available_at = utcnow() + timedelta(seconds=min(300, 2 ** min(event.attempt, 8)))

