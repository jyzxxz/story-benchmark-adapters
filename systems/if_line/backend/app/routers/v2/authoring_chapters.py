from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    append_path_chapter,
    require_owned_chapter_revision,
    require_owned_path_chapter,
    require_owned_story_path,
)
from app.application.story_chapter_batch_service import create_story_path_chapter_batch
from app.application.revision_head_service import (
    activate_path_chapter_revision_head,
    head_value,
    parse_if_match,
)
from app.application.story_chapter_service import (
    create_manual_story_path_chapter_revision,
    create_story_path_chapter_generation_task,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import ChapterRevision as ChapterRevisionModel
from app.models_v2 import StoryPathChapter
from app.routers.v2.authoring_common import accepted_task, set_lock_etag
from app.schemas_authoring import (
    ChapterBatchRequest,
    ChapterRevision,
    ChapterRevisionCreate,
    HeadUpdate,
    PathChapter,
    PathChapterCreate,
    RevisionHead,
)
from app.schemas_v2 import ChapterGenerationRequest, TaskAccepted


router = APIRouter(tags=["Chapters"])


@router.get(
    "/story-paths/{story_path_id}/chapters",
    response_model=list[PathChapter],
    operation_id="listPathChapters",
)
def list_path_chapters(
    story_path_id: str,
    include_detached: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    query = db.query(StoryPathChapter).filter(
        StoryPathChapter.story_path_id == story_path_id
    )
    if not include_detached:
        query = query.filter(StoryPathChapter.status == "active")
    return (
        query
        .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
        .all()
    )


@router.post(
    "/story-paths/{story_path_id}/chapters",
    response_model=PathChapter,
    status_code=status.HTTP_201_CREATED,
    operation_id="appendPathChapter",
)
def add_path_chapter(
    story_path_id: str,
    body: PathChapterCreate,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    placement = append_path_chapter(
        db,
        story_path_id=story_path_id,
        user_id=user.id,
        expected_lock_version=parse_if_match(if_match),
        after_path_chapter_id=body.after_path_chapter_id,
    )
    db.commit()
    return placement


@router.post(
    "/story-paths/{story_path_id}/chapter-generation-batches",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateChapterBatch",
)
def generate_chapter_batch(
    story_path_id: str,
    body: ChapterBatchRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_story_path(db, story_path_id=story_path_id, user_id=user.id)
    task, created = create_story_path_chapter_batch(
        db,
        user_id=user.id,
        story_path_id=story_path_id,
        idempotency_key=idempotency_key,
        path_chapter_ids=body.path_chapter_ids,
        parameters={},
        instructions=body.instructions,
        bible_revision_id=body.bible_revision_id,
        outline_revision_id=body.outline_revision_id,
    )
    db.commit()
    return accepted_task(task, created=created)


@router.post(
    "/path-chapters/{path_chapter_id}/generations",
    response_model=TaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateChapter",
)
def generate_chapter(
    path_chapter_id: str,
    body: ChapterGenerationRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    request = body or ChapterGenerationRequest()
    require_owned_path_chapter(
        db,
        path_chapter_id=path_chapter_id,
        user_id=user.id,
    )
    task, created = create_story_path_chapter_generation_task(
        db,
        user_id=user.id,
        path_chapter_id=path_chapter_id,
        idempotency_key=idempotency_key,
        parameters=request.parameters,
        instructions=request.instructions,
        bible_revision_id=request.bible_revision_id,
        outline_revision_id=request.outline_revision_id,
        ancestor_revision_overrides=request.ancestor_revision_overrides,
    )
    db.commit()
    return accepted_task(task, created=created)


@router.get(
    "/path-chapters/{path_chapter_id}/revisions",
    response_model=list[ChapterRevision],
    operation_id="listChapterRevisions",
)
def list_chapter_revisions(
    path_chapter_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    placement = require_owned_path_chapter(
        db,
        path_chapter_id=path_chapter_id,
        user_id=user.id,
    )
    return (
        db.query(ChapterRevisionModel)
        .filter(ChapterRevisionModel.chapter_slot_id == placement.chapter_slot_id)
        .order_by(ChapterRevisionModel.revision_no.desc())
        .all()
    )


@router.post(
    "/path-chapters/{path_chapter_id}/revisions",
    response_model=ChapterRevision,
    status_code=status.HTTP_201_CREATED,
    operation_id="createChapterRevision",
)
def add_chapter_revision(
    path_chapter_id: str,
    body: ChapterRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_path_chapter(
        db,
        path_chapter_id=path_chapter_id,
        user_id=user.id,
    )
    revision = create_manual_story_path_chapter_revision(
        db,
        path_chapter_id=path_chapter_id,
        parent_revision_id=body.parent_revision_id,
        content=body.content,
        user_id=user.id,
    )
    db.commit()
    return revision


@router.get(
    "/path-chapters/{path_chapter_id}/head",
    response_model=RevisionHead,
    operation_id="getChapterHead",
)
def get_chapter_head(
    path_chapter_id: str,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    placement = require_owned_path_chapter(
        db,
        path_chapter_id=path_chapter_id,
        user_id=user.id,
    )
    value = head_value(placement)
    set_lock_etag(response, value.lock_version)
    return value


@router.put(
    "/path-chapters/{path_chapter_id}/head",
    response_model=RevisionHead,
    operation_id="updateChapterHead",
)
def update_chapter_head(
    path_chapter_id: str,
    body: HeadUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_path_chapter(
        db,
        path_chapter_id=path_chapter_id,
        user_id=user.id,
    )
    result = activate_path_chapter_revision_head(
        db,
        path_chapter_id=path_chapter_id,
        revision_id=body.revision_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    set_lock_etag(response, result.head.lock_version)
    return result.head


@router.get(
    "/chapter-revisions/{chapter_revision_id}",
    response_model=ChapterRevision,
    operation_id="getChapterRevision",
)
def get_chapter_revision(
    chapter_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return require_owned_chapter_revision(
        db,
        chapter_revision_id=chapter_revision_id,
        user_id=user.id,
    )
