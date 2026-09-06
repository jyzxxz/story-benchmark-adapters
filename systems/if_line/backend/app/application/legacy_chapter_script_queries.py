"""Sequence-based queries retained only by the pre-StoryPath server-agent adapter."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models_v2 import ChapterScriptHead, ChapterScriptRevision


def get_current_script_revision(
    db: Session,
    *,
    project_id: int,
    chapter_index: int,
) -> ChapterScriptRevision:
    head = (
        db.query(ChapterScriptHead)
        .filter(
            ChapterScriptHead.project_id == project_id,
            ChapterScriptHead.chapter_index == chapter_index,
        )
        .first()
    )
    revision = (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.id == head.current_revision_id,
            ChapterScriptRevision.project_id == project_id,
        )
        .first()
        if head
        else None
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Chapter Script revision 不存在")
    return revision


__all__ = ["get_current_script_revision"]
