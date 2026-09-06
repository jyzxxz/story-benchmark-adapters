"""
章节工作区服务。

后端没有单独的 Chapter 表，章节由同一组 project_id + chapter_index 下的
outline/content 等记录共同构成。这里集中维护“空章节”的最小创建语义。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import ChapterContent, ChapterOutline, VNGraph


class ChapterWorkspaceExists(Exception):
    """目标章节已经有任意工作区记录。"""


def get_next_chapter_index(db: Session, project_id: int) -> int:
    max_indices = [
        db.query(func.max(ChapterOutline.chapter_index))
        .filter(ChapterOutline.project_id == project_id)
        .scalar(),
        db.query(func.max(ChapterContent.chapter_index))
        .filter(ChapterContent.project_id == project_id)
        .scalar(),
        db.query(func.max(VNGraph.chapter_index))
        .filter(VNGraph.project_id == project_id)
        .scalar(),
    ]
    max_index = max((int(value) for value in max_indices if value is not None), default=0)
    return max_index + 1


def chapter_workspace_exists(db: Session, project_id: int, chapter_index: int) -> bool:
    outline_exists = (
        db.query(ChapterOutline.id)
        .filter(
            ChapterOutline.project_id == project_id,
            ChapterOutline.chapter_index == chapter_index,
        )
        .first()
        is not None
    )
    if outline_exists:
        return True

    content_exists = (
        db.query(ChapterContent.id)
        .filter(
            ChapterContent.project_id == project_id,
            ChapterContent.chapter_index == chapter_index,
        )
        .first()
        is not None
    )
    if content_exists:
        return True

    return (
        db.query(VNGraph.id)
        .filter(
            VNGraph.project_id == project_id,
            VNGraph.chapter_index == chapter_index,
        )
        .first()
        is not None
    )


def create_empty_chapter_workspace(
    db: Session,
    project_id: int,
    chapter_index: Optional[int],
    title: str,
    summary: str = "",
    conflict: str = "",
    characters: Optional[list[str]] = None,
    scene: str = "",
    emotion: str = "",
    visual_keywords: Optional[list[str]] = None,
) -> tuple[ChapterOutline, ChapterContent]:
    target_index = int(chapter_index or 0)
    if target_index <= 0:
        target_index = get_next_chapter_index(db, project_id)

    if chapter_workspace_exists(db, project_id, target_index):
        raise ChapterWorkspaceExists(f"章节 {target_index} 已存在")

    now = datetime.utcnow()
    normalized_title = (title or "").strip() or f"第 {target_index} 章"
    normalized_summary = (summary or "").strip()

    outline = ChapterOutline(
        project_id=project_id,
        chapter_index=target_index,
        title=normalized_title,
        summary=normalized_summary,
        conflict=(conflict or "").strip(),
        characters=characters or [],
        scene=(scene or "").strip(),
        emotion=(emotion or "").strip(),
        visual_keywords=visual_keywords or [],
        status="pending",
        created_at=now,
        updated_at=now,
    )
    content = ChapterContent(
        project_id=project_id,
        chapter_index=target_index,
        content="",
        version=1,
        status="empty",
        created_at=now,
        updated_at=now,
    )
    db.add(outline)
    db.add(content)
    return outline, content
