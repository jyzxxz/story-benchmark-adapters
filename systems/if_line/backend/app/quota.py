"""
User quota helpers.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any, Optional

from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.models import User


def _conditional_reset_daily_quota(db: Session, user: User, now: datetime) -> bool:
    day_start = datetime.combine(now.date(), time.min)
    reset_stmt = (
        update(User)
        .where(
            User.id == user.id,
            or_(User.quota_reset_at.is_(None), User.quota_reset_at < day_start),
        )
        .values(quota_used_daily=0, quota_reset_at=now)
        .execution_options(synchronize_session=False)
    )
    result = db.execute(reset_stmt)
    return result.rowcount == 1


def reset_daily_quota_if_needed(db: Session, user: User, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    changed = _conditional_reset_daily_quota(db, user, now)
    db.refresh(user)
    return changed


def quota_snapshot(user: User) -> dict[str, Any]:
    return {
        "user_id": user.id,
        "quota_total": user.quota_total,
        "quota_daily": user.quota_daily,
        "quota_used_total": user.quota_used_total,
        "quota_used_daily": user.quota_used_daily,
        "quota_remaining_total": max(0, user.quota_total - user.quota_used_total),
        "quota_remaining_daily": max(0, user.quota_daily - user.quota_used_daily),
        "quota_reset_at": user.quota_reset_at,
    }
