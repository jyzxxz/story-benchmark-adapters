"""
Likes, comments, notifications, and user usage endpoints.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.auth import get_current_user, get_optional_user
from app.database import get_db
from app.models import ProjectComment, ProjectLike, Project, User, UserNotification
from app.models_v2 import GenerationTask, UsageLedgerEntry, UsageReservation
from app.project_permissions import require_project_read_access
from app.quota import quota_snapshot, reset_daily_quota_if_needed
from app.schemas import (
    NotificationListResponse,
    NotificationResponse,
    ProjectCommentCreate,
    ProjectCommentResponse,
    ProjectLikeResponse,
    ProjectSocialSummary,
    UserQuotaResponse,
    UserUsageLedgerEntryResponse,
)

router = APIRouter()


def _like_count(db: Session, project_id: int) -> int:
    return db.query(ProjectLike).filter(ProjectLike.project_id == project_id).count()


def _comment_count(db: Session, project_id: int) -> int:
    return db.query(ProjectComment).filter(ProjectComment.project_id == project_id).count()


def _liked_by_user(db: Session, project_id: int, user: Optional[User]) -> bool:
    if not user:
        return False
    return db.query(ProjectLike).filter(
        ProjectLike.project_id == project_id,
        ProjectLike.user_id == user.id,
    ).first() is not None


def _comment_response(comment: ProjectComment) -> ProjectCommentResponse:
    user = comment.user
    return ProjectCommentResponse(
        id=comment.id,
        project_id=comment.project_id,
        user_id=comment.user_id,
        display_name=user.display_name if user else "用户",
        avatar_url=user.avatar_url if user else None,
        body=comment.body,
        created_at=comment.created_at,
        updated_at=comment.updated_at,
    )


def _notification_response(notification: UserNotification) -> NotificationResponse:
    actor = notification.actor
    return NotificationResponse(
        id=notification.id,
        actor_user_id=notification.actor_user_id,
        actor_display_name=actor.display_name if actor else None,
        actor_avatar_url=actor.avatar_url if actor else None,
        project_id=notification.project_id,
        comment_id=notification.comment_id,
        notification_type=notification.notification_type,
        title=notification.title,
        body=notification.body,
        payload=notification.payload,
        read_at=notification.read_at,
        created_at=notification.created_at,
    )


def _unread_count(db: Session, user_id: int) -> int:
    return db.query(UserNotification).filter(
        UserNotification.user_id == user_id,
        UserNotification.read_at.is_(None),
    ).count()


def _usage_ledger_response(
    entry: UsageLedgerEntry,
    task: GenerationTask | None,
    reservation: UsageReservation | None,
) -> UserUsageLedgerEntryResponse:
    metadata = entry.event_metadata or {}
    return UserUsageLedgerEntryResponse(
        id=entry.id,
        reservation_id=entry.reservation_id,
        task_id=entry.task_id,
        task_kind=task.kind if task else entry.activity_type,
        task_status=task.status if task else None,
        project_id=entry.project_id,
        entry_type=entry.entry_type,
        amount=float(entry.amount or 0),
        currency=entry.currency,
        reservation_status=reservation.status if reservation else None,
        event_metadata=metadata,
        created_at=entry.created_at,
    )


def _notify_project_owner(
    db: Session,
    *,
    project: Project,
    actor: User,
    notification_type: str,
    title: str,
    body: str,
    comment_id: Optional[int] = None,
) -> None:
    if not project.owner_id or project.owner_id == actor.id:
        return
    db.add(UserNotification(
        user_id=project.owner_id,
        actor_user_id=actor.id,
        project_id=project.id,
        comment_id=comment_id,
        notification_type=notification_type,
        title=title,
        body=body,
        payload={"project_title": project.title},
    ))


# GET /api/projects/{project_id}/social
@router.get("/projects/{project_id}/social", response_model=ProjectSocialSummary)
async def get_project_social_summary(
    project_id: int,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    require_project_read_access(db, project_id, current_user)
    return ProjectSocialSummary(
        project_id=project_id,
        like_count=_like_count(db, project_id),
        comment_count=_comment_count(db, project_id),
        liked_by_me=_liked_by_user(db, project_id, current_user),
    )


# POST /api/projects/{project_id}/likes
@router.post("/projects/{project_id}/likes", response_model=ProjectLikeResponse)
async def like_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_read_access(db, project_id, current_user)
    existing = db.query(ProjectLike).filter(
        ProjectLike.project_id == project_id,
        ProjectLike.user_id == current_user.id,
    ).first()
    if not existing:
        db.add(ProjectLike(project_id=project_id, user_id=current_user.id))
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
        else:
            _notify_project_owner(
                db,
                project=project,
                actor=current_user,
                notification_type="project_like",
                title="收到新点赞",
                body=f"{current_user.display_name} 点赞了《{project.title}》",
            )
            db.commit()

    return ProjectLikeResponse(
        project_id=project_id,
        liked=True,
        like_count=_like_count(db, project_id),
    )


# DELETE /api/projects/{project_id}/likes
@router.delete("/projects/{project_id}/likes", response_model=ProjectLikeResponse)
async def unlike_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_project_read_access(db, project_id, current_user)
    like = db.query(ProjectLike).filter(
        ProjectLike.project_id == project_id,
        ProjectLike.user_id == current_user.id,
    ).first()
    if like:
        db.delete(like)
        db.commit()
    return ProjectLikeResponse(
        project_id=project_id,
        liked=False,
        like_count=_like_count(db, project_id),
    )


# GET /api/projects/{project_id}/comments
@router.get("/projects/{project_id}/comments", response_model=List[ProjectCommentResponse])
async def list_project_comments(
    project_id: int,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    require_project_read_access(db, project_id, current_user)
    comments = (
        db.query(ProjectComment)
        .options(joinedload(ProjectComment.user))
        .filter(ProjectComment.project_id == project_id)
        .order_by(ProjectComment.created_at.asc(), ProjectComment.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_comment_response(comment) for comment in comments]


# POST /api/projects/{project_id}/comments
@router.post("/projects/{project_id}/comments", response_model=ProjectCommentResponse, status_code=201)
async def create_project_comment(
    project_id: int,
    payload: ProjectCommentCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    project = require_project_read_access(db, project_id, current_user)
    comment = ProjectComment(
        project_id=project_id,
        user_id=current_user.id,
        body=payload.body,
    )
    db.add(comment)
    db.flush()
    _notify_project_owner(
        db,
        project=project,
        actor=current_user,
        notification_type="project_comment",
        title="收到新评论",
        body=f"{current_user.display_name} 评论了《{project.title}》",
        comment_id=comment.id,
    )
    db.commit()
    db.refresh(comment)
    return _comment_response(comment)


# GET /api/notifications
@router.get("/notifications", response_model=NotificationListResponse)
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    unread_only: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
):
    query = (
        db.query(UserNotification)
        .options(joinedload(UserNotification.actor))
        .filter(UserNotification.user_id == current_user.id)
    )
    if unread_only:
        query = query.filter(UserNotification.read_at.is_(None))
    notifications = query.order_by(UserNotification.created_at.desc(), UserNotification.id.desc()).limit(limit).all()
    return NotificationListResponse(
        items=[_notification_response(notification) for notification in notifications],
        unread_count=_unread_count(db, current_user.id),
    )


# PATCH /api/notifications/{notification_id}/read
@router.patch("/notifications/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notification = db.query(UserNotification).filter(
        UserNotification.id == notification_id,
        UserNotification.user_id == current_user.id,
    ).first()
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
    if not notification.read_at:
        notification.read_at = datetime.utcnow()
        db.commit()
        db.refresh(notification)
    return _notification_response(notification)


# POST /api/notifications/read-all
@router.post("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifications = db.query(UserNotification).filter(
        UserNotification.user_id == current_user.id,
        UserNotification.read_at.is_(None),
    ).all()
    now = datetime.utcnow()
    for notification in notifications:
        notification.read_at = now
    db.commit()
    return {"updated": len(notifications), "unread_count": 0}


# GET /api/users/me/quota
@router.get("/users/me/quota", response_model=UserQuotaResponse)
async def get_my_quota(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if reset_daily_quota_if_needed(db, current_user):
        db.commit()
        db.refresh(current_user)
    return UserQuotaResponse(**quota_snapshot(current_user))


# GET /api/users/me/usage
@router.get("/users/me/usage", response_model=List[UserUsageLedgerEntryResponse])
async def list_my_usage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    rows = (
        db.query(UsageLedgerEntry, GenerationTask, UsageReservation)
        .outerjoin(GenerationTask, GenerationTask.id == UsageLedgerEntry.task_id)
        .outerjoin(UsageReservation, UsageReservation.id == UsageLedgerEntry.reservation_id)
        .filter(UsageLedgerEntry.user_id == current_user.id)
        .order_by(UsageLedgerEntry.created_at.desc(), UsageLedgerEntry.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_usage_ledger_response(entry, task, reservation) for entry, task, reservation in rows]
