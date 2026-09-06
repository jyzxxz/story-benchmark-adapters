"""
章节正文生成服务。

HTTP 路由和服务端 Agent 都从这里进入，避免两边各自拼 prompt、写 ChapterContent。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.models import ChapterContent, ChapterOutline, GenerationStat, Project, StoryBible
from app.services.chapter_outline_workflow_service import effective_story_bible
from app.services.generation_stat_service import GenerationStatService
from app.services.llm_service import llm_service
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_events import (
    EEvent_content_done,
    EEvent_content_skip,
    EEvent_content_start,
)
from app.utils import logging as xlog


STALE_CHAPTER_GENERATION_AFTER = timedelta(minutes=30)

ChapterGenerationEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class ChapterGenerationError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ChapterGenerationResult:
    content: ChapterContent
    skipped: bool = False


class ChapterGenerationService:
    def __init__(
        self,
        db: Session,
        emit: Optional[ChapterGenerationEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def generate_chapter_content(
        self,
        project_id: int,
        chapter_index: int,
        word_count_min: int = 3000,
        word_count_max: int = 4500,
    ) -> ChapterGenerationResult:
        xlog.info(
            project_id,
            "[chapter] generate chapter start project_id=%d chapter_index=%d",
            project_id,
            chapter_index,
        )
        project = self._get_project(project_id)

        self._mark_stale_chapter_generations_failed(project_id)

        # 检查是否已生成
        existing = self._get_latest_chapter_content(project_id, chapter_index)
        if existing and not is_empty_chapter_content(existing):
            xlog.info(
                project_id,
                "[chapter] generate chapter skipped existing project_id=%d chapter_index=%d content_id=%d",
                project_id,
                chapter_index,
                existing.id,
            )
            await self._emit(
                {
                    "type": EEvent_content_skip,
                    "project_id": project_id,
                    "chapter_index": chapter_index,
                    "content_id": existing.id,
                    "status": existing.status,
                    "content_length": len(existing.content or ""),
                }
            )
            return ChapterGenerationResult(content=existing, skipped=True)

        active_stats = self._get_active_chapter_generation_stats(project_id, chapter_index)
        if active_stats:
            xlog.warn(
                project_id,
                "[chapter] generate chapter rejected active run project_id=%d chapter_index=%d",
                project_id,
                chapter_index,
            )
            raise ChapterGenerationError("该章节正在生成中，请稍后刷新查看结果", status_code=409)

        # 双存储回落：generate_story_bible 可能只写 v2 修订（不落 legacy 表）。
        bible = effective_story_bible(self.db, project_id)
        if not bible:
            raise ChapterGenerationError("Story Bible 不存在", status_code=404)

        # 获取章节大纲
        outline = (
            self.db.query(ChapterOutline)
            .filter(
                ChapterOutline.project_id == project_id,
                ChapterOutline.chapter_index == chapter_index,
            )
            .first()
        )
        if not outline:
            raise ChapterGenerationError("章节大纲不存在", status_code=404)

        # 开始记录生成时间
        stat_service = GenerationStatService(self.db)
        stat = stat_service.start_generation(project_id, "chapter", chapter_index)
        await self._emit(
            {
                "type": EEvent_content_start,
                "project_id": project_id,
                "chapter_index": chapter_index,
                "title": outline.title,
                "word_count_min": word_count_min,
                "word_count_max": word_count_max,
            }
        )

        try:
            # 调用 LLM 生成正文
            content_output = await llm_service.generate_chapter_content(
                story_bible=bible.raw_json,
                chapter_outline={
                    "chapter_index": outline.chapter_index,
                    "title": outline.title,
                    "summary": outline.summary,
                    "conflict": outline.conflict,
                    "characters": outline.characters,
                    "scene": outline.scene,
                    "emotion": outline.emotion,
                },
                # The legacy table has no StoryPath identity. Supplying no
                # predecessor context is safer than leaking a sibling path.
                previous_chapters=[],
                word_count_min=word_count_min,
                word_count_max=word_count_max,
                uuid=project_id,
            )

            existing_after_generation = self._get_latest_chapter_content(project_id, chapter_index)
            if existing_after_generation and not is_empty_chapter_content(existing_after_generation):
                stat_service.end_generation(stat.id, success=True)
                xlog.info(
                    project_id,
                    "[chapter] generate chapter finished but existing created project_id=%d chapter_index=%d content_id=%d",
                    project_id,
                    chapter_index,
                    existing_after_generation.id,
                )
                await self._emit_content_done(project_id, chapter_index, existing_after_generation, skipped=True)
                return ChapterGenerationResult(content=existing_after_generation, skipped=True)

            if existing_after_generation:
                chapter_content = existing_after_generation
                chapter_content.content = content_output.content
                chapter_content.version = (chapter_content.version or 1) + 1
                chapter_content.status = "completed"
                chapter_content.updated_at = datetime.utcnow()
            else:
                chapter_content = ChapterContent(
                    project_id=project_id,
                    chapter_index=chapter_index,
                    content=content_output.content,
                    version=1,
                    status="completed",
                )
                self.db.add(chapter_content)

            # 更新项目状态
            if project.status == "outline_approved":
                engine = WorkflowEngine(self.db)
                engine.transition_to(project, "chapter_generating")

            # 保存到数据库
            self.db.commit()

            # 结束记录生成时间
            stat_service.end_generation(stat.id, success=True)
            self.db.refresh(chapter_content)
            xlog.info(
                project_id,
                "[chapter] generate chapter ok project_id=%d chapter_index=%d content_id=%d",
                project_id,
                chapter_index,
                chapter_content.id,
            )
            await self._emit_content_done(project_id, chapter_index, chapter_content)
            return ChapterGenerationResult(content=chapter_content)

        except ChapterGenerationError:
            raise
        except Exception as exc:
            # 记录失败
            stat_service.end_generation(stat.id, success=False, error_message=str(exc))
            self.db.commit()
            xlog.error(
                project_id,
                exc,
                "[chapter] generate chapter failed project_id=%d chapter_index=%d",
                project_id,
                chapter_index,
            )
            raise ChapterGenerationError(f"生成章节正文失败: {exc}", status_code=500)

    def _get_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ChapterGenerationError("项目不存在", status_code=404)
        return project

    def _get_latest_chapter_content(
        self,
        project_id: int,
        chapter_index: int,
    ) -> Optional[ChapterContent]:
        return (
            self.db.query(ChapterContent)
            .filter(
                ChapterContent.project_id == project_id,
                ChapterContent.chapter_index == chapter_index,
            )
            .order_by(ChapterContent.id.desc())
            .first()
        )

    def _mark_stale_chapter_generations_failed(self, project_id: int) -> int:
        now = datetime.utcnow()
        cutoff = now - STALE_CHAPTER_GENERATION_AFTER
        stale_stats = (
            self.db.query(GenerationStat)
            .filter(
                GenerationStat.project_id == project_id,
                GenerationStat.generation_type == "chapter",
                GenerationStat.status == "running",
                GenerationStat.start_time < cutoff,
            )
            .all()
        )

        for stat in stale_stats:
            stat.status = "failed"
            stat.end_time = now
            stat.duration_seconds = (now - stat.start_time).total_seconds()
            stat.error_message = "章节生成任务超时未完成，可能由请求超时、重复触发或服务重启遗留"

        if stale_stats:
            self.db.commit()
        return len(stale_stats)

    def _get_active_chapter_generation_stats(
        self,
        project_id: int,
        chapter_index: Optional[int] = None,
    ) -> list[GenerationStat]:
        cutoff = datetime.utcnow() - STALE_CHAPTER_GENERATION_AFTER
        query = self.db.query(GenerationStat).filter(
            GenerationStat.project_id == project_id,
            GenerationStat.generation_type == "chapter",
            GenerationStat.status == "running",
            GenerationStat.start_time >= cutoff,
        )
        if chapter_index is not None:
            query = query.filter(GenerationStat.chapter_index == chapter_index)
        return query.order_by(GenerationStat.chapter_index.asc()).all()

    async def _emit_content_done(
        self,
        project_id: int,
        chapter_index: int,
        content: ChapterContent,
        skipped: bool = False,
    ) -> None:
        await self._emit(
            {
                "type": EEvent_content_done,
                "project_id": project_id,
                "chapter_index": chapter_index,
                "content_id": content.id,
                "status": content.status,
                "skipped": skipped,
                "content_length": len(content.content or ""),
                "content_preview": (content.content or "")[:300],
            }
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result


def is_empty_chapter_content(chapter: Optional[ChapterContent]) -> bool:
    if chapter is None:
        return True
    if (chapter.status or "") == "empty":
        return True
    return not (chapter.content or "").strip()
