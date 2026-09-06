from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import require_owned_project
from app.application.revision_head_service import head_value, parse_if_match
from app.application.story_bible_service import (
    activate_bible_revision_head,
    build_bible_generation_source,
    create_bible_revision,
    get_or_create_content_head,
)
from app.application.task_service import create_generation_task
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import GenerationTask, StoryBibleRevision
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import (
    BibleRevision,
    BibleRevisionCreate,
    HeadUpdate,
    RevisionHead,
)
from app.schemas_v2 import GenerationRequest, TaskAccepted


router = APIRouter(prefix="/projects/{project_id}", tags=["Bible"])


@router.post(
    "/bible-generations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateBible",
)
def generate_bible(
    project_id: int,
    body: GenerationRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    request = body or GenerationRequest()
    project = require_owned_project(db, project_id=project_id, user_id=user.id)
    parameters = deepcopy(request.parameters)
    if request.instructions is not None:
        parameters["instructions"] = request.instructions
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user.id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        if (
            existing.kind != "bible.generate"
            or existing.project_id != project.id
            or (existing.parameters or {}) != parameters
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key 已用于不同请求",
            )
        return accepted_task(existing, created=False)

    head = get_or_create_content_head(db, project_id)
    source_refs = build_bible_generation_source(
        project,
        parent_revision_id=head.current_bible_revision_id,
    )
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key=idempotency_key,
        source_refs=source_refs,
        parameters=parameters,
        estimated_cost=Decimal("3"),
        idempotency_request={"parameters": parameters},
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/bible-revisions",
    response_model=list[BibleRevision],
    operation_id="listBibleRevisions",
)
def list_bible_revisions(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.project_id == project_id)
        .order_by(StoryBibleRevision.revision_no.desc())
        .all()
    )


@router.post(
    "/bible-revisions",
    response_model=BibleRevision,
    status_code=status.HTTP_201_CREATED,
    operation_id="createBibleRevision",
)
def add_bible_revision(
    project_id: int,
    body: BibleRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    revision = create_bible_revision(
        db,
        project_id=project_id,
        content=deepcopy(body.content_json),
        source={
            "origin": "manual_edit",
            "parent_revision_id": body.parent_revision_id,
        },
        user_id=user.id,
        activate=False,
        parent_revision_id=body.parent_revision_id,
    )
    db.commit()
    return revision


@router.get(
    "/bible-head",
    response_model=RevisionHead,
    operation_id="getBibleHead",
)
def get_bible_head(
    project_id: int,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    head = get_or_create_content_head(db, project_id)
    db.commit()
    value = head_value(head)
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/bible-head",
    response_model=RevisionHead,
    operation_id="updateBibleHead",
)
def update_bible_head(
    project_id: int,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    result = activate_bible_revision_head(
        db,
        project_id=project_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    set_lock_etag(response, result.head.lock_version)
    return result.head
