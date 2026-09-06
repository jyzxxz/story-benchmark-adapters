from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import require_owned_story_path
from app.application.revision_head_service import (
    get_or_create_story_path_outline_head,
    head_value,
    parse_if_match,
)
from app.application.story_outline_service import (
    activate_story_path_outline_head,
    build_outline_generation_source,
    create_story_path_outline_revision,
)
from app.application.task_service import create_generation_task
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import (
    GenerationTask,
    OutlineChapter,
    OutlineRevision as OutlineRevisionModel,
)
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import (
    HeadUpdate,
    OutlineChapterInput,
    OutlineGenerationRequest,
    OutlineRevision as OutlineRevisionRead,
    OutlineRevisionCreate,
    RevisionHead,
)
from app.schemas_v2 import TaskAccepted


router = APIRouter(prefix="/story-paths/{story_path_id}", tags=["Outlines"])


def _outline_revision_read(
    db: Session,
    revision: OutlineRevisionModel,
) -> OutlineRevisionRead:
    chapters = (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == revision.id)
        .order_by(OutlineChapter.display_index)
        .all()
    )
    return OutlineRevisionRead(
        id=revision.id,
        story_path_id=revision.story_path_id,
        bible_revision_id=revision.bible_revision_id,
        parent_revision_id=revision.parent_revision_id,
        revision_no=revision.revision_no,
        source_hash=revision.source_hash,
        content_hash=revision.content_hash,
        status=revision.status,
        chapters=[
            OutlineChapterInput(
                story_path_chapter_id=chapter.story_path_chapter_id,
                display_index=chapter.display_index,
                title=chapter.title or "",
                summary=chapter.summary or "",
                conflict=chapter.conflict,
                characters=chapter.characters or [],
                scene=chapter.scene,
                emotion=chapter.emotion,
                visual_keywords=chapter.visual_keywords or [],
            )
            for chapter in chapters
        ],
    )


@router.post(
    "/outline-generations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateOutline",
)
def generate_outline(
    story_path_id: str,
    body: OutlineGenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    path = require_owned_story_path(
        db,
        story_path_id=story_path_id,
        user_id=user.id,
    )
    parameters = {
        "chapter_count": body.chapter_count,
        "instructions": body.instructions,
    }
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user.id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        frozen_source = (existing.source_refs or {}).get("outline_source")
        if (
            existing.kind != "outline.generate"
            or existing.project_id != path.project_id
            or (existing.parameters or {}) != parameters
            or not isinstance(frozen_source, dict)
            or frozen_source.get("story_path_id") != path.id
            or (
                body.bible_revision_id is not None
                and frozen_source.get("bible_revision_id") != body.bible_revision_id
            )
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key 已用于不同请求",
            )
        return accepted_task(existing, created=False)

    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=body.chapter_count,
        instructions=body.instructions,
        bible_revision_id=body.bible_revision_id,
    )
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=path.project_id,
        kind="outline.generate",
        idempotency_key=idempotency_key,
        source_refs=source_refs,
        parameters=parameters,
        estimated_cost=Decimal("4"),
        idempotency_request={
            "story_path_id": path.id,
            "parameters": parameters,
        },
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/outline-revisions",
    response_model=list[OutlineRevisionRead],
    operation_id="listOutlineRevisions",
)
def list_outline_revisions(
    story_path_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    revisions = (
        db.query(OutlineRevisionModel)
        .filter(OutlineRevisionModel.story_path_id == story_path_id)
        .order_by(OutlineRevisionModel.revision_no.desc())
        .all()
    )
    return [_outline_revision_read(db, revision) for revision in revisions]


@router.post(
    "/outline-revisions",
    response_model=OutlineRevisionRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="createOutlineRevision",
)
def add_outline_revision(
    story_path_id: str,
    body: OutlineRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    revision = create_story_path_outline_revision(
        db,
        story_path_id=story_path_id,
        bible_revision_id=body.bible_revision_id,
        chapters=[chapter.model_dump() for chapter in body.chapters],
        user_id=user.id,
        parent_revision_id=body.parent_revision_id,
    )
    db.commit()
    return _outline_revision_read(db, revision)


@router.get(
    "/outline-head",
    response_model=RevisionHead,
    operation_id="getOutlineHead",
)
def get_outline_head(
    story_path_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    head = get_or_create_story_path_outline_head(db, story_path_id)
    db.commit()
    value = head_value(head)
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/outline-head",
    response_model=RevisionHead,
    operation_id="updateOutlineHead",
)
def update_outline_head(
    story_path_id: str,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    result = activate_story_path_outline_head(
        db,
        story_path_id=story_path_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    value = head_value(result.head)
    set_lock_etag(response, value.lock_version)
    return value
