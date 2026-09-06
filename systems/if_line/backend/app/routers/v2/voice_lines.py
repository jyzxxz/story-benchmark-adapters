from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.application.voice_line_service import (
    build_voice_manifest,
    create_line_retry_task,
    create_slice_task,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import ChapterRevision, VoiceLine
from app.project_permissions import require_project_owner
from app.schemas_voice import (
    VoiceGenerationRequest,
    VoiceLineRead,
    VoiceManifest,
    VoiceTaskAccepted,
)


router = APIRouter(
    prefix="/projects/{project_id}/chapter-revisions/{chapter_revision_id}/voice-lines",
    tags=["voice-lines"],
)


def _owned_chapter(
    db: Session,
    *,
    project_id: int,
    chapter_revision_id: str,
    user: User,
) -> ChapterRevision:
    require_project_owner(db, project_id, user)
    chapter = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.id == chapter_revision_id,
            ChapterRevision.project_id == project_id,
        )
        .first()
    )
    if not chapter:
        raise HTTPException(status_code=404, detail="章节版本不存在")
    return chapter


# POST /api/projects/{project_id}/chapter-revisions/{chapter_revision_id}/voice-lines/generations
@router.post("/generations", response_model=VoiceTaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def generate_voice_lines(
    project_id: int,
    chapter_revision_id: str,
    body: VoiceGenerationRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chapter = _owned_chapter(
        db,
        project_id=project_id,
        chapter_revision_id=chapter_revision_id,
        user=user,
    )
    task, created = create_slice_task(
        db,
        user_id=user.id,
        chapter=chapter,
        idempotency_key=(idempotency_key or "").strip(),
        render_audio=body.render_audio,
    )
    db.commit()
    db.refresh(task)
    return VoiceTaskAccepted(
        task_id=task.id,
        status=task.status,
        created=created,
        events_url=f"/api/tasks/{task.id}/events",
    )


# GET /api/projects/{project_id}/chapter-revisions/{chapter_revision_id}/voice-lines
@router.get("", response_model=VoiceManifest)
def voice_manifest(
    project_id: int,
    chapter_revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_chapter(
        db,
        project_id=project_id,
        chapter_revision_id=chapter_revision_id,
        user=user,
    )
    items = build_voice_manifest(db, chapter_revision_id)
    return VoiceManifest(
        chapter_revision_id=chapter_revision_id,
        count=len(items),
        items=[VoiceLineRead.model_validate(item) for item in items],
    )


# GET /api/projects/{project_id}/chapter-revisions/{chapter_revision_id}/voice-lines/{line_id}
@router.get("/{line_id}", response_model=VoiceLineRead)
def read_voice_line(
    project_id: int,
    chapter_revision_id: str,
    line_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _owned_chapter(
        db,
        project_id=project_id,
        chapter_revision_id=chapter_revision_id,
        user=user,
    )
    line = (
        db.query(VoiceLine)
        .filter(VoiceLine.id == line_id, VoiceLine.chapter_revision_id == chapter_revision_id)
        .first()
    )
    if not line:
        raise HTTPException(status_code=404, detail="配音行不存在")
    return line


# POST /api/projects/{project_id}/chapter-revisions/{chapter_revision_id}/voice-lines/{line_id}/retry
@router.post("/{line_id}/retry", response_model=VoiceTaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def retry_voice_line(
    project_id: int,
    chapter_revision_id: str,
    line_id: str,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    chapter = _owned_chapter(
        db,
        project_id=project_id,
        chapter_revision_id=chapter_revision_id,
        user=user,
    )
    line = (
        db.query(VoiceLine)
        .filter(VoiceLine.id == line_id, VoiceLine.chapter_revision_id == chapter_revision_id)
        .first()
    )
    if not line:
        raise HTTPException(status_code=404, detail="配音行不存在")
    task, created = create_line_retry_task(
        db,
        user_id=user.id,
        chapter=chapter,
        line=line,
        idempotency_key=(idempotency_key or "").strip(),
    )
    db.commit()
    db.refresh(task)
    return VoiceTaskAccepted(
        task_id=task.id,
        status=task.status,
        created=created,
        events_url=f"/api/tasks/{task.id}/events",
    )

