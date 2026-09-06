"""
Project ownership and public-read permission helpers.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.public_release_service import get_active_project_release
from app.core.errors import AppError
from app.models import Project, User


def is_project_published(project: Project) -> bool:
    return (project.visibility or "private") == "public"


def is_project_publicly_readable(db: Session, project: Project) -> bool:
    try:
        get_active_project_release(db, project_id=project.id)
    except AppError:
        return False
    return True


def is_project_owner(project: Project, user: Optional[User]) -> bool:
    if not user:
        return False
    return project.owner_id == user.id


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.deleted_at.is_(None))
        .first()
    )
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project


def require_project_owner(db: Session, project_id: int, user: User) -> Project:
    project = get_project_or_404(db, project_id)
    if not is_project_owner(project, user):
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return project


def require_project_read_access(
    db: Session,
    project_id: int,
    user: Optional[User],
) -> Project:
    project = get_project_or_404(db, project_id)
    if is_project_owner(project, user) or is_project_publicly_readable(db, project):
        return project
    raise HTTPException(status_code=404, detail="项目不存在")
