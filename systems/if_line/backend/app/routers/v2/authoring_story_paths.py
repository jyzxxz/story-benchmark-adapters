from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    require_owned_project,
    require_owned_story_path,
    update_story_path_metadata,
)
from app.application.revision_head_service import parse_if_match
from app.application.story_path_promotion_service import (
    promote_candidate_to_story_path,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import StoryPath as StoryPathModel
from app.routers.v2.authoring_common import set_lock_etag
from app.schemas_authoring import StoryPath, StoryPathCreate, StoryPathPatch


router = APIRouter(tags=["StoryPaths"])


@router.get(
    "/projects/{project_id}/story-paths",
    response_model=list[StoryPath],
    operation_id="listStoryPaths",
)
def list_story_paths(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return (
        db.query(StoryPathModel)
        .filter(StoryPathModel.project_id == project_id)
        .order_by(StoryPathModel.created_at, StoryPathModel.id)
        .all()
    )


@router.post(
    "/projects/{project_id}/story-paths",
    response_model=StoryPath,
    status_code=status.HTTP_201_CREATED,
    operation_id="createStoryPathFromCandidate",
)
def create_story_path_from_candidate(
    project_id: int,
    body: StoryPathCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    result = promote_candidate_to_story_path(
        db,
        user_id=user.id,
        candidate_id=body.candidate_id,
        idempotency_key=idempotency_key,
        title=body.title,
        expected_project_id=project_id,
    )
    db.commit()
    return result.path


@router.get(
    "/story-paths/{story_path_id}",
    response_model=StoryPath,
    operation_id="getStoryPath",
)
def get_story_path(
    story_path_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    path = require_owned_story_path(
        db,
        story_path_id=story_path_id,
        user_id=user.id,
    )
    set_lock_etag(response, path.lock_version)
    return path


@router.patch(
    "/story-paths/{story_path_id}",
    response_model=StoryPath,
    operation_id="updateStoryPath",
)
def patch_story_path(
    story_path_id: str,
    body: StoryPathPatch,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    path = update_story_path_metadata(
        db,
        story_path_id=story_path_id,
        user_id=user.id,
        expected_lock_version=parse_if_match(if_match),
        changes=body.model_dump(exclude_unset=True),
    )
    db.commit()
    set_lock_etag(response, path.lock_version)
    return path
