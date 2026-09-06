from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.application.revision_service import (
    activate_bible_revision,
    activate_chapter_revision,
    activate_outline_revision,
    build_bible_generation_source,
    create_bible_revision,
    create_chapter_revision,
    create_outline_revision,
    project_readiness,
)
from app.application.chapter_batch_service import create_chapter_batch
from app.application.task_service import create_generation_task
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
)
from app.project_permissions import require_project_owner
from app.services.prompt_builder_service import prompt_builder_service
from app.schemas_v2 import (
    BibleRevisionCreate,
    BibleRevisionRead,
    ChapterRevisionCreate,
    ChapterRevisionRead,
    ChapterBatchGenerationRequest,
    GenerationRequest,
    OutlineChapterInput,
    OutlineRevisionCreate,
    OutlineRevisionRead,
    TaskAccepted,
)


router = APIRouter(prefix="/projects/{project_id}", tags=["revisions"])


def _outline_read(db: Session, revision: OutlineRevision) -> OutlineRevisionRead:
    rows = (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == revision.id)
        .order_by(OutlineChapter.chapter_index)
        .all()
    )
    result = OutlineRevisionRead.model_validate(revision)
    result.chapters = [
        OutlineChapterInput(
            chapter_index=row.chapter_index,
            title=row.title,
            summary=row.summary,
            conflict=row.conflict,
            characters=row.characters or [],
            scene=row.scene,
            emotion=row.emotion,
            visual_keywords=row.visual_keywords or [],
        )
        for row in rows
    ]
    return result


# GET /api/projects/{project_id}/bible/current
@router.get("/bible/current", response_model=BibleRevisionRead)
def current_bible(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project_owner(db, project_id, user)
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    revision = (
        db.query(StoryBibleRevision).filter(StoryBibleRevision.id == head.current_bible_revision_id).first()
        if head and head.current_bible_revision_id
        else None
    )
    if not revision:
        raise HTTPException(status_code=404, detail="当前 Bible revision 不存在")
    return revision


# GET /api/projects/{project_id}/bible/revisions
@router.get("/bible/revisions", response_model=list[BibleRevisionRead])
def list_bible_revisions(
    project_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.project_id == project_id)
        .order_by(StoryBibleRevision.revision_no.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# POST /api/projects/{project_id}/bible/revisions
@router.post("/bible/revisions", response_model=BibleRevisionRead, status_code=status.HTTP_201_CREATED)
def add_bible_revision(
    project_id: int,
    body: BibleRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    normalized_content = prompt_builder_service.enrich_story_bible_genders(
        body.content,
        context_text=" ".join((
            str(body.content.get("worldview") or body.content.get("world") or ""),
            str(body.content.get("style_rules") or ""),
        )),
    )
    revision = create_bible_revision(
        db,
        project_id=project_id,
        content=normalized_content,
        source={"origin": "manual_edit"},
        user_id=user.id,
        activate=body.activate,
    )
    db.commit()
    db.refresh(revision)
    return revision


# POST /api/projects/{project_id}/bible/revisions/{revision_id}/activate
@router.post("/bible/revisions/{revision_id}/activate", response_model=BibleRevisionRead)
def activate_bible(
    project_id: int,
    revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revision = activate_bible_revision(db, project_id, revision_id)
    db.commit()
    return revision


# POST /api/projects/{project_id}/bible/generations
@router.post("/bible/generations", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def generate_bible(
    project_id: int,
    body: GenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    project = require_project_owner(db, project_id, user)
    head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .one_or_none()
    )
    source_refs = build_bible_generation_source(
        project,
        parent_revision_id=head.current_bible_revision_id if head else None,
    )
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project_id,
        kind="bible.generate",
        idempotency_key=idempotency_key,
        source_refs=source_refs,
        parameters=body.parameters,
        estimated_cost=Decimal("3"),
    )
    db.commit()
    return TaskAccepted(task_id=task.id, status=task.status, events_url=f"/api/tasks/{task.id}/events", created=created)


# GET /api/projects/{project_id}/outline/current
@router.get("/outline/current", response_model=OutlineRevisionRead)
def current_outline(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project_owner(db, project_id, user)
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    revision = (
        db.query(OutlineRevision).filter(OutlineRevision.id == head.current_outline_revision_id).first()
        if head and head.current_outline_revision_id
        else None
    )
    if not revision:
        raise HTTPException(status_code=404, detail="当前 Outline revision 不存在")
    return _outline_read(db, revision)


# GET /api/projects/{project_id}/outline/revisions
@router.get("/outline/revisions", response_model=list[OutlineRevisionRead])
def list_outline_revisions(
    project_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revisions = (
        db.query(OutlineRevision)
        .filter(OutlineRevision.project_id == project_id)
        .order_by(OutlineRevision.revision_no.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_outline_read(db, item) for item in revisions]


# POST /api/projects/{project_id}/outline/revisions
@router.post("/outline/revisions", response_model=OutlineRevisionRead, status_code=status.HTTP_201_CREATED)
def add_outline_revision(
    project_id: int,
    body: OutlineRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revision = create_outline_revision(
        db,
        project_id=project_id,
        chapters=[chapter.model_dump() for chapter in body.chapters],
        user_id=user.id,
        bible_revision_id=body.bible_revision_id,
        activate=body.activate,
    )
    db.commit()
    return _outline_read(db, revision)


# POST /api/projects/{project_id}/outline/revisions/{revision_id}/approve
@router.post("/outline/revisions/{revision_id}/approve", response_model=OutlineRevisionRead)
def approve_outline(
    project_id: int,
    revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revision = activate_outline_revision(db, project_id, revision_id, approve=True)
    db.commit()
    return _outline_read(db, revision)


# POST /api/projects/{project_id}/outline/generations
@router.post("/outline/generations", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def generate_outline(
    project_id: int,
    body: GenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not head or not head.current_bible_revision_id:
        raise HTTPException(status_code=409, detail="请先创建或激活 Story Bible revision")
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project_id,
        kind="outline.generate",
        idempotency_key=idempotency_key,
        source_refs={"bible_revision_id": head.current_bible_revision_id},
        parameters=body.parameters,
        estimated_cost=Decimal("4"),
    )
    db.commit()
    return TaskAccepted(task_id=task.id, status=task.status, events_url=f"/api/tasks/{task.id}/events", created=created)


# GET /api/projects/{project_id}/chapters/{chapter_index}/current
@router.get("/chapters/{chapter_index}/current", response_model=ChapterRevisionRead)
def current_chapter(
    project_id: int,
    chapter_index: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    head = (
        db.query(ChapterHead)
        .filter(ChapterHead.project_id == project_id, ChapterHead.chapter_index == chapter_index)
        .first()
    )
    revision = db.query(ChapterRevision).filter(ChapterRevision.id == head.current_revision_id).first() if head else None
    if not revision:
        raise HTTPException(status_code=404, detail="当前 Chapter revision 不存在")
    return revision


# GET /api/projects/{project_id}/chapters/{chapter_index}/revisions
@router.get("/chapters/{chapter_index}/revisions", response_model=list[ChapterRevisionRead])
def list_chapter_revisions(
    project_id: int,
    chapter_index: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return (
        db.query(ChapterRevision)
        .filter(ChapterRevision.project_id == project_id, ChapterRevision.chapter_index == chapter_index)
        .order_by(ChapterRevision.revision_no.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# POST /api/projects/{project_id}/chapters/{chapter_index}/revisions
@router.post("/chapters/{chapter_index}/revisions", response_model=ChapterRevisionRead, status_code=status.HTTP_201_CREATED)
def add_chapter_revision(
    project_id: int,
    chapter_index: int,
    body: ChapterRevisionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revision = create_chapter_revision(
        db,
        project_id=project_id,
        chapter_index=chapter_index,
        content=body.content,
        user_id=user.id,
        bible_revision_id=body.bible_revision_id,
        outline_revision_id=body.outline_revision_id,
        state_snapshot_id=body.state_snapshot_id,
        activate=body.activate,
    )
    db.commit()
    return revision


# POST /api/projects/{project_id}/chapters/{chapter_index}/revisions/{revision_id}/activate
@router.post("/chapters/{chapter_index}/revisions/{revision_id}/activate", response_model=ChapterRevisionRead)
def activate_chapter(
    project_id: int,
    chapter_index: int,
    revision_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    revision = activate_chapter_revision(db, project_id, chapter_index, revision_id)
    db.commit()
    return revision


# POST /api/projects/{project_id}/chapters/{chapter_index}/generations
@router.post("/chapters/{chapter_index}/generations", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def generate_chapter(
    project_id: int,
    chapter_index: int,
    body: GenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """触发生成章节正文版本。"""
    require_project_owner(db, project_id, user)
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not head or not head.current_bible_revision_id or not head.current_outline_revision_id:
        raise HTTPException(status_code=409, detail="请先激活 Bible 和 Outline revision")
    task, created = create_generation_task(
        db,
        user_id=user.id,
        project_id=project_id,
        kind="chapter.generate",
        idempotency_key=idempotency_key,
        source_refs={
            "bible_revision_id": head.current_bible_revision_id,
            "outline_revision_id": head.current_outline_revision_id,
            "chapter_index": chapter_index,
        },
        parameters=body.parameters,
        estimated_cost=Decimal("8"),
    )
    db.commit()
    return TaskAccepted(task_id=task.id, status=task.status, events_url=f"/api/tasks/{task.id}/events", created=created)


# GET /api/projects/{project_id}/readiness
@router.get("/readiness")
def readiness(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    require_project_owner(db, project_id, user)
    return project_readiness(db, project_id)


# POST /api/projects/{project_id}/chapter-generation-batches
@router.post("/chapter-generation-batches", response_model=TaskAccepted, status_code=status.HTTP_202_ACCEPTED)
def generate_chapter_batch(
    project_id: int,
    body: ChapterBatchGenerationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    task, created = create_chapter_batch(
        db,
        user_id=user.id,
        project_id=project_id,
        idempotency_key=idempotency_key,
        chapter_indexes=body.chapter_indexes,
        parameters=body.parameters,
    )
    db.commit()
    return TaskAccepted(
        task_id=task.id,
        status=task.status,
        events_url=f"/api/tasks/{task.id}/events",
        created=created,
    )


# ===== v2 stats endpoints =====
# 替代 v1 chapters.get_project_stats / get_asset_time_breakdown.
# 数据源从 GenerationStat 单表换成 GenerationTask + task.parameters.asset_type,
# 返回 schema 与 v1 兼容(前端切换无感).
from app.application.stats_service_v2 import (  # noqa: E402
    get_asset_time_breakdown_v2,
    get_project_stats_v2,
)
from app.schemas import (  # noqa: E402
    AssetTimeBreakdownResponse,
    ProjectStatsResponse,
)


# GET /api/projects/{project_id}/stats
@router.get("/stats", response_model=ProjectStatsResponse)
def get_project_stats_v2_endpoint(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取项目生成统计(v2 数据源,兼容 v1 schema)"""
    require_project_owner(db, project_id, user)
    stats = get_project_stats_v2(db, project_id)
    return ProjectStatsResponse(**stats)


# GET /api/projects/{project_id}/stats/breakdown
@router.get("/stats/breakdown", response_model=AssetTimeBreakdownResponse)
def get_asset_time_breakdown_v2_endpoint(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """获取素材生成时间分解(v2 数据源,兼容 v1 schema)"""
    require_project_owner(db, project_id, user)
    breakdown = get_asset_time_breakdown_v2(db, project_id)
    return AssetTimeBreakdownResponse(**breakdown)
    build_bible_generation_source,
