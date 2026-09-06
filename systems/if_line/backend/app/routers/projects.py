"""
项目路由
"""
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session
from app.auth import get_current_user, get_optional_user
from app.database import get_db
from app.models import Project, User
from app.models_v2 import ProjectContentHead, ProjectRelease
from app.models_v2 import StorageObject
from app.application.storage_service import soft_delete_storage_object
from app.project_permissions import is_project_owner, require_project_owner, require_project_read_access
from app.schemas import (
    ProjectCreate,
    ProjectResponse,
    ProjectStatusResponse,
    PublicProjectResponse,
)
from app.utils import logging as xlog

router = APIRouter()


def _has_valid_current_release(db: Session, project_id: int) -> bool:
    return (
        db.query(ProjectRelease.id)
        .join(
            ProjectContentHead,
            ProjectContentHead.published_release_id == ProjectRelease.id,
        )
        .filter(
            ProjectContentHead.project_id == project_id,
            ProjectRelease.project_id == project_id,
            ProjectRelease.status == "published",
            ProjectRelease.withdrawn_at.is_(None),
        )
        .first()
        is not None
    )


# POST /api/projects/
@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project_data: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """创建小说项目"""
    xlog.info(0, "[project] create start title=%s character_count=%d", project_data.title, len(project_data.characters or []))
    project = Project(
        owner_id=current_user.id,
        title=project_data.title,
        characters=project_data.characters,
        story_start=project_data.story_start,
        story_end=project_data.story_end,
        style=project_data.style,
        source_work=(project_data.source_work or "").strip() or None,
        pace=project_data.pace,
        extra_requirements=project_data.extra_requirements,
        visibility="private",
        published_at=None,
        status="draft_input",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    xlog.info(project.id, "[project] create ok project_id=%d status=%s", project.id, project.status)
    return project



def _published_project_ids(db: Session):
    return (
        db.query(ProjectContentHead.project_id)
        .join(
            ProjectRelease,
            ProjectRelease.id == ProjectContentHead.published_release_id,
        )
        .filter(
            ProjectRelease.project_id == ProjectContentHead.project_id,
            ProjectRelease.status == "published",
            ProjectRelease.withdrawn_at.is_(None),
        )
    )


# GET /api/projects/
@router.get("/", response_model=List[ProjectResponse])
async def list_projects(
    publication: str = Query("all", pattern="^(all|draft|published)$"),
    visibility: Optional[str] = Query(None, pattern="^(private|public)$"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取当前用户项目列表"""
    query = db.query(Project).filter(Project.owner_id == current_user.id)
    if publication == "draft":
        query = query.filter(~Project.id.in_(_published_project_ids(db)))
    elif publication == "published":
        query = query.filter(Project.id.in_(_published_project_ids(db)))
    if visibility:
        query = query.filter(Project.visibility == visibility)
    projects = query.order_by(Project.updated_at.desc()).all()
    xlog.info(0, "[project] list ok count=%d", len(projects))
    return projects


LEGACY_PROJECT_CLAIM_TOKEN = os.getenv("LEGACY_PROJECT_CLAIM_TOKEN", "")


# GET /api/projects/{project_id}
@router.get("/{project_id}")
async def get_project(
    project_id: int,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db)
):
    """获取项目详情"""
    project = require_project_read_access(db, project_id, current_user)
    if (
        not is_project_owner(project, current_user)
        and not _has_valid_current_release(db, project_id)
    ):
        raise HTTPException(status_code=404, detail="项目不存在")
    xlog.info(project_id, "[project] get ok project_id=%d status=%s", project_id, project.status)
    if is_project_owner(project, current_user):
        return ProjectResponse.model_validate(project)
    return PublicProjectResponse.model_validate(project)


# POST /api/projects/{project_id}/claim
@router.post("/{project_id}/claim", response_model=ProjectResponse, include_in_schema=False)
async def claim_legacy_project(
    project_id: int,
    x_legacy_project_claim: Optional[str] = Header(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """认领 owner_id 为空的旧项目；必须配置服务端 claim token。"""
    if not LEGACY_PROJECT_CLAIM_TOKEN:
        raise HTTPException(status_code=403, detail="旧项目认领未启用")
    if x_legacy_project_claim != LEGACY_PROJECT_CLAIM_TOKEN:
        raise HTTPException(status_code=403, detail="旧项目认领令牌无效")

    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    if project.owner_id is not None:
        raise HTTPException(status_code=409, detail="项目已归属用户")

    project.owner_id = current_user.id
    project.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(project)
    return project


# DELETE /api/projects/{project_id}
@router.delete("/{project_id}")
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """删除项目及其所有相关数据"""
    project = require_project_owner(db, project_id, current_user)

    active_release = (
        db.query(ProjectRelease)
        .filter(
            ProjectRelease.project_id == project_id,
            ProjectRelease.status == "published",
            ProjectRelease.withdrawn_at.is_(None),
        )
        .first()
    )
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if (
        active_release
        or (head and head.published_release_id)
    ):
        raise HTTPException(status_code=409, detail="已发布项目不能删除，请先撤回所有有效 Release")

    # Keep media tombstones through the project cascade; deferred GC removes
    # physical files only after all version and release references are gone.
    for storage in (
        db.query(StorageObject)
        .filter(StorageObject.project_id == project_id)
        .with_for_update()
        .all()
    ):
        soft_delete_storage_object(db, storage)
    db.delete(project)
    db.commit()
    xlog.info(project_id, "[project] delete ok project_id=%d", project_id)
    return {"message": "项目已删除"}


# GET /api/projects/{project_id}/status
@router.get("/{project_id}/status", response_model=ProjectStatusResponse)
async def get_project_status(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """获取项目状态"""
    project = require_project_owner(db, project_id, current_user)

    from app.services.workflow_engine import WorkflowEngine
    engine = WorkflowEngine(db)
    progress = engine.get_progress(project)
    xlog.info(project_id, "[project] status ok project_id=%d status=%s progress=%.2f", project_id, project.status, progress["progress_percentage"])

    return ProjectStatusResponse(
        id=project.id,
        title=project.title,
        status=project.status,
        current_step=progress["current_step"],
        completed_steps=progress["completed_steps"],
        pending_steps=progress["pending_steps"]
    )
