from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.story_chapter_batch_service import create_story_path_chapter_batch
from app.models_v2 import OutlineChapter, OutlineRevision, ProjectContentHead


def create_chapter_batch(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    idempotency_key: str,
    chapter_indexes: list[int] | None,
    parameters: dict,
):
    """Bridge the legacy index request onto the sequential StoryPath batch."""

    head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .one_or_none()
    )
    if not head or not head.current_bible_revision_id or not head.current_outline_revision_id:
        raise HTTPException(status_code=409, detail="请先激活 Bible 和 Outline revision")
    outline = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == head.current_outline_revision_id,
            OutlineRevision.project_id == project_id,
        )
        .one_or_none()
    )
    if not outline or not outline.story_path_id:
        raise HTTPException(
            status_code=409,
            detail="当前 Outline 尚未迁移到 StoryPath，请重新确认大纲",
        )
    rows = (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == outline.id)
        .order_by(OutlineChapter.chapter_index, OutlineChapter.id)
        .all()
    )
    rows_by_index = {row.chapter_index: row for row in rows}
    selected_indexes = sorted(set(chapter_indexes or rows_by_index))
    if not selected_indexes or any(index not in rows_by_index for index in selected_indexes):
        raise HTTPException(status_code=422, detail="章节批次包含不在当前大纲中的 chapter_index")
    selected_rows = [rows_by_index[index] for index in selected_indexes]
    if any(not row.story_path_chapter_id for row in selected_rows):
        raise HTTPException(
            status_code=409,
            detail="当前 Outline 的 PathChapter 绑定不完整，请重新确认大纲",
        )
    return create_story_path_chapter_batch(
        db,
        user_id=user_id,
        story_path_id=outline.story_path_id,
        idempotency_key=idempotency_key,
        path_chapter_ids=[row.story_path_chapter_id for row in selected_rows],
        parameters=parameters,
    )


__all__ = [
    "create_chapter_batch",
    "create_story_path_chapter_batch",
]
