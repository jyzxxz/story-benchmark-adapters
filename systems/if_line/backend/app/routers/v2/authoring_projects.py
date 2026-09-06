from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    create_authoring_project,
    delete_authoring_project,
    get_project_artifact_metrics,
    get_project_metrics,
    get_root_story_path,
    require_owned_project,
    update_authoring_project,
)
from app.application.project_state_projection_service import (
    calculate_project_state_projection,
)
from app.application.publication_readiness_service import (
    calculate_publication_readiness,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import Project, User
from app.schemas_authoring import (
    ProjectCreate,
    ProjectPatch,
    ProjectRead,
    PublicationReadiness,
)


router = APIRouter(prefix="/projects", tags=["Projects"])


def _characters(project: Project) -> list[dict]:
    result: list[dict] = []
    for value in project.characters or []:
        result.append(dict(value) if isinstance(value, dict) else {"name": str(value)})
    return result


def _project_read(db: Session, project: Project) -> ProjectRead:
    root = get_root_story_path(db, project_id=project.id)
    projection = calculate_project_state_projection(db, project_id=project.id)
    return ProjectRead(
        id=project.id,
        root_story_path_id=root.id if root else None,
        title=project.title,
        characters=_characters(project),
        story_start=project.story_start or "",
        story_end=project.story_end or "",
        style=project.style or "",
        source_work=project.source_work,
        pace=project.pace,
        extra_requirements=project.extra_requirements,
        **projection.as_dict(),
    )


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project, _root = create_authoring_project(
        db,
        user_id=user.id,
        values=body.model_dump(),
    )
    db.commit()
    return _project_read(db, project)


@router.get("", response_model=list[ProjectRead], operation_id="listProjects")
def list_projects(
    publication_state: Literal[
        "all", "unpublished", "published", "changes_pending"
    ] = Query(default="all"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    projects = (
        db.query(Project)
        .filter(Project.owner_id == user.id, Project.deleted_at.is_(None))
        .order_by(Project.updated_at.desc(), Project.id.desc())
        .all()
    )
    result = [_project_read(db, project) for project in projects]
    if publication_state == "all":
        return result
    return [item for item in result if item.publication.state == publication_state]


@router.get(
    "/{project_id}",
    response_model=ProjectRead,
    operation_id="getProject",
)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = require_owned_project(db, project_id=project_id, user_id=user.id)
    return _project_read(db, project)


@router.patch(
    "/{project_id}",
    response_model=ProjectRead,
    operation_id="updateProject",
)
def patch_project(
    project_id: int,
    body: ProjectPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = update_authoring_project(
        db,
        project_id=project_id,
        user_id=user.id,
        changes=body.model_dump(exclude_unset=True),
    )
    db.commit()
    return _project_read(db, project)


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteProject",
)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    delete_authoring_project(db, project_id=project_id, user_id=user.id)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{project_id}/publication-readiness",
    response_model=PublicationReadiness,
    operation_id="getPublicationReadiness",
)
def get_publication_readiness(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return calculate_publication_readiness(db, project_id=project_id).as_dict()


@router.get("/{project_id}/metrics", operation_id="getProjectMetrics")
def project_metrics(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return get_project_metrics(db, project_id=project_id)


@router.get(
    "/{project_id}/metrics/artifacts",
    operation_id="getProjectArtifactMetrics",
)
def project_artifact_metrics(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return get_project_artifact_metrics(db, project_id=project_id)
