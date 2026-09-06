"""
章节大纲工作流服务。

HTTP 路由和服务端 Agent 共用这里的大纲生成、修改和确认逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.models import ChapterContent, ChapterOutline, Project, StoryBible
from app.models_v2 import ProjectContentHead, StoryBibleRevision
from app.core.story_outline import DEFAULT_OUTLINE_CHAPTER_COUNT
from app.services.generation_stat_service import GenerationStatService
from app.services.llm_service import llm_service
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_events import (
    EEvent_outline_approved,
    EEvent_outline_done,
    EEvent_outline_revise,
    EEvent_outline_revised,
    EEvent_outline_start,
    EEvent_single_outline_approved,
    EEvent_single_outline_done,
    EEvent_single_outline_start,
)
from app.utils import logging as xlog


ChapterOutlineEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


def effective_story_bible(db: Session, project_id: int) -> Any:
    """读取项目生效的 Story Bible：优先 legacy 行，回落 v2 当前修订。

    agent-toolcall 分支的 generate_story_bible 只写 story_bible_revisions
    并激活内容 head，不落 legacy story_bibles；消费方（规划/正文生成）
    只读 raw_json，因此回落时用修订的 content_json 等价提供。
    找不到任何 Bible 时返回 None，由调用方决定报错文案。
    """
    bible = db.query(StoryBible).filter(StoryBible.project_id == project_id).first()
    if bible:
        return bible
    head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .first()
    )
    revision = None
    if head and head.current_bible_revision_id:
        revision = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == head.current_bible_revision_id,
                StoryBibleRevision.project_id == project_id,
            )
            .first()
        )
    if revision is None:
        revision = (
            db.query(StoryBibleRevision)
            .filter(StoryBibleRevision.project_id == project_id)
            .order_by(
                StoryBibleRevision.revision_no.desc(),
                StoryBibleRevision.created_at.desc(),
            )
            .first()
        )
    if revision is None:
        return None
    return SimpleNamespace(
        id=revision.id, project_id=project_id, raw_json=revision.content_json
    )


class ChapterOutlineWorkflowError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ChapterOutlineWorkflowResult:
    outlines: list[ChapterOutline]


@dataclass
class SingleChapterOutlineWorkflowResult:
    outline: ChapterOutline
    planning_notes: str = ""
    continuity_check: str = ""
    created: bool = False


class ChapterOutlineWorkflowService:
    def __init__(
        self,
        db: Session,
        emit: Optional[ChapterOutlineEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def generate_all_chapter_outlines(
        self,
        project_id: int,
        chapter_count: int = DEFAULT_OUTLINE_CHAPTER_COUNT,
    ) -> ChapterOutlineWorkflowResult:
        xlog.info(project_id, "[workflow] generate outline start project_id=%d", project_id)
        project = self._get_project(project_id)
        bible = self._get_story_bible(project_id, missing_message="请先生成 Story Bible")

        await self._emit(
            {
                "type": EEvent_outline_start,
                "project_id": project_id,
                "pace": project.pace or "medium",
                "chapter_count": chapter_count,
            }
        )

        try:
            # 调用 LLM 生成章节大纲
            outline_output = await llm_service.generate_chapter_outline(
                story_bible=bible.raw_json,
                chapter_count=chapter_count,
                pace=project.pace or "medium",
                uuid=project_id,
            )

            # 保存到数据库
            for chapter_data in outline_output.chapters:
                outline = ChapterOutline(
                    project_id=project_id,
                    chapter_index=chapter_data.chapter_index,
                    title=chapter_data.title,
                    summary=chapter_data.summary,
                    conflict=chapter_data.conflict,
                    characters=chapter_data.characters,
                    scene=chapter_data.scene,
                    emotion=chapter_data.emotion,
                    visual_keywords=chapter_data.visual_keywords,
                    status="pending",
                )
                self.db.add(outline)

            # 更新项目状态 - 进入审核阶段
            engine = WorkflowEngine(self.db)
            engine.transition_to(project, "outline_reviewing")
            self.db.commit()

            # 返回所有章节大纲
            outlines = self.get_outlines(project_id)
            xlog.info(
                project_id,
                "[workflow] generate outline ok project_id=%d chapter_count=%d",
                project_id,
                len(outlines),
            )
            await self._emit_outline_ready(
                stage=EEvent_outline_done,
                project_id=project_id,
                outlines=outlines,
            )
            return ChapterOutlineWorkflowResult(outlines=outlines)
        except ChapterOutlineWorkflowError:
            raise
        except Exception as exc:
            xlog.error(project_id, exc, "[workflow] generate outline failed project_id=%d", project_id)
            raise ChapterOutlineWorkflowError(f"生成章节大纲失败: {exc}", status_code=500)

    async def generate_outline(
        self,
        project_id: int,
        chapter_count: int = DEFAULT_OUTLINE_CHAPTER_COUNT,
    ) -> ChapterOutlineWorkflowResult:
        """兼容旧命名：生成整部故事的章节规划列表。"""
        return await self.generate_all_chapter_outlines(project_id, chapter_count)

    async def revise_all_chapter_outlines(
        self,
        project_id: int,
        feedback: str,
    ) -> ChapterOutlineWorkflowResult:
        xlog.info(
            project_id,
            "[workflow] revise outline start project_id=%d feedback_chars=%d",
            project_id,
            len(feedback or ""),
        )
        self._get_project(project_id)
        bible = self._get_story_bible(project_id, missing_message="Story Bible 不存在")
        current_outlines = self.get_outlines(project_id)
        current_outline_list = [self._outline_to_llm_dict(outline) for outline in current_outlines]

        await self._emit(
            {
                "type": EEvent_outline_revise,
                "project_id": project_id,
                "feedback_chars": len(feedback or ""),
                "chapter_count": len(current_outlines),
            }
        )

        try:
            # 调用 LLM 修改大纲
            outline_output = await llm_service.revise_chapter_outline(
                current_outline=current_outline_list,
                feedback=feedback,
                story_bible=bible.raw_json,
                uuid=project_id,
            )

            # 删除旧大纲
            self.db.query(ChapterOutline).filter(ChapterOutline.project_id == project_id).delete()

            # 保存新大纲
            for chapter_data in outline_output.chapters:
                outline = ChapterOutline(
                    project_id=project_id,
                    chapter_index=chapter_data.chapter_index,
                    title=chapter_data.title,
                    summary=chapter_data.summary,
                    conflict=chapter_data.conflict,
                    characters=chapter_data.characters,
                    scene=chapter_data.scene,
                    emotion=chapter_data.emotion,
                    visual_keywords=chapter_data.visual_keywords,
                    status="pending",
                )
                self.db.add(outline)

            self.db.commit()

            # 返回新大纲
            outlines = self.get_outlines(project_id)
            xlog.info(
                project_id,
                "[workflow] revise outline ok project_id=%d chapter_count=%d",
                project_id,
                len(outlines),
            )
            await self._emit_outline_ready(
                stage=EEvent_outline_revised,
                project_id=project_id,
                outlines=outlines,
            )
            return ChapterOutlineWorkflowResult(outlines=outlines)
        except ChapterOutlineWorkflowError:
            raise
        except Exception as exc:
            xlog.error(project_id, exc, "[workflow] revise outline failed project_id=%d", project_id)
            raise ChapterOutlineWorkflowError(f"修改章节大纲失败: {exc}", status_code=500)

    async def revise_outline(
        self,
        project_id: int,
        feedback: str,
    ) -> ChapterOutlineWorkflowResult:
        """兼容旧命名：修改整部故事的章节规划列表。"""
        return await self.revise_all_chapter_outlines(project_id, feedback)

    async def approve_all_chapter_outlines(self, project_id: int) -> dict[str, Any]:
        xlog.info(project_id, "[workflow] approve outline start project_id=%d", project_id)
        project = self._get_project(project_id)

        # 更新所有大纲状态为 approved
        self.db.query(ChapterOutline).filter(ChapterOutline.project_id == project_id).update(
            {"status": "approved"}
        )

        # 更新项目状态
        engine = WorkflowEngine(self.db)
        engine.transition_to(project, "outline_approved")

        # 记录大纲确认时间
        stat_service = GenerationStatService(self.db)
        stat_service.record_outline_approved(project_id)
        self.db.commit()

        outlines = self.get_outlines(project_id)
        xlog.info(project_id, "[workflow] approve outline ok project_id=%d", project_id)
        await self._emit_outline_ready(
            stage=EEvent_outline_approved,
            project_id=project_id,
            outlines=outlines,
        )
        return {"message": "章节大纲已确认"}

    async def approve_outline(self, project_id: int) -> dict[str, Any]:
        """兼容旧命名：确认整部故事的章节规划列表。"""
        return await self.approve_all_chapter_outlines(project_id)

    async def approve_single_chapter_outline(self, project_id: int, chapter_index: int) -> ChapterOutline:
        xlog.info(
            project_id,
            "[workflow] approve single outline start project_id=%d chapter_index=%d",
            project_id,
            chapter_index,
        )
        self._get_project(project_id)
        outline = self.get_single_chapter_outline(project_id, chapter_index)
        if not outline:
            raise ChapterOutlineWorkflowError(f"章节 {chapter_index} 单章规划不存在", status_code=404)

        # 一致性守卫：大纲在正文生成后被重写（overwrite）时，批准一份与
        # 现有正文不一致的大纲会静默污染后续脚本/资源链。
        content = (
            self.db.query(ChapterContent)
            .filter(
                ChapterContent.project_id == project_id,
                ChapterContent.chapter_index == chapter_index,
            )
            .first()
        )
        if (
            content
            and (content.content or "").strip()
            and content.updated_at
            and outline.updated_at
            and outline.updated_at > content.updated_at
            and outline.status == "pending"
        ):
            raise ChapterOutlineWorkflowError(
                f"章节 {chapter_index} 大纲在正文生成后被重写过，批准会与现有正文不一致；"
                "请先重新生成正文，或明确放弃当前正文",
                status_code=409,
            )

        outline.status = "approved"
        self.db.commit()
        self.db.refresh(outline)
        await self._emit(
            {
                "type": EEvent_single_outline_approved,
                "project_id": project_id,
                "chapter_index": outline.chapter_index,
                "outline": self._outline_to_event_dict(outline),
            }
        )
        xlog.info(
            project_id,
            "[workflow] approve single outline ok project_id=%d chapter_index=%d outline_id=%d",
            project_id,
            outline.chapter_index,
            outline.id,
        )
        return outline

    def get_outlines(self, project_id: int) -> list[ChapterOutline]:
        return (
            self.db.query(ChapterOutline)
            .filter(ChapterOutline.project_id == project_id)
            .order_by(ChapterOutline.chapter_index)
            .all()
        )

    def get_single_chapter_outline(self, project_id: int, chapter_index: int) -> Optional[ChapterOutline]:
        return (
            self.db.query(ChapterOutline)
            .filter(
                ChapterOutline.project_id == project_id,
                ChapterOutline.chapter_index == chapter_index,
            )
            .first()
        )

    async def plan_single_chapter_outline(
        self,
        project_id: int,
        user_instruction: str,
        chapter_index: Optional[int] = None,
        reference_context: Optional[dict[str, Any]] = None,
        overwrite: bool = False,
    ) -> SingleChapterOutlineWorkflowResult:
        xlog.info(
            project_id,
            "[workflow] plan single outline start project_id=%d chapter_index=%s instruction_chars=%d overwrite=%s",
            project_id,
            chapter_index,
            len(user_instruction or ""),
            overwrite,
        )
        self._get_project(project_id)
        bible = self._get_story_bible(project_id, missing_message="请先生成 Story Bible")
        target_index = self._resolve_target_chapter_index(project_id, chapter_index)
        existing = self.get_single_chapter_outline(project_id, target_index)
        if existing and not overwrite:
            raise ChapterOutlineWorkflowError(f"章节 {target_index} 已有单章规划，请确认 overwrite=true 后再覆盖", status_code=409)

        all_outlines = self.get_outlines(project_id)
        await self._emit(
            {
                "type": EEvent_single_outline_start,
                "project_id": project_id,
                "chapter_index": target_index,
                "outline_count": len(all_outlines),
            }
        )

        try:
            output = await llm_service.generate_single_chapter_outline(
                story_bible=bible.raw_json,
                existing_outlines=[self._outline_to_llm_dict(outline) for outline in all_outlines],
                user_instruction=user_instruction,
                chapter_index=target_index,
                reference_context=reference_context or {},
                uuid=project_id,
            )
            chapter_data = output.chapter
            if existing:
                outline = existing
                created = False
            else:
                outline = ChapterOutline(project_id=project_id, chapter_index=target_index)
                self.db.add(outline)
                created = True

            outline.chapter_index = target_index
            outline.title = chapter_data.title
            outline.summary = chapter_data.summary
            outline.conflict = chapter_data.conflict
            outline.characters = chapter_data.characters
            outline.scene = chapter_data.scene
            outline.emotion = chapter_data.emotion
            outline.visual_keywords = chapter_data.visual_keywords
            outline.status = "pending"

            self.db.commit()
            self.db.refresh(outline)
            await self._emit(
                {
                    "type": EEvent_single_outline_done,
                    "project_id": project_id,
                    "chapter_index": outline.chapter_index,
                    "outline": self._outline_to_event_dict(outline),
                    "created": created,
                    "planning_notes": output.planning_notes,
                    "continuity_check": output.continuity_check,
                }
            )
            xlog.info(
                project_id,
                "[workflow] plan single outline ok project_id=%d chapter_index=%d outline_id=%d created=%s",
                project_id,
                outline.chapter_index,
                outline.id,
                created,
            )
            return SingleChapterOutlineWorkflowResult(
                outline=outline,
                planning_notes=output.planning_notes or "",
                continuity_check=output.continuity_check or "",
                created=created,
            )
        except ChapterOutlineWorkflowError:
            raise
        except Exception as exc:
            self.db.rollback()
            xlog.error(
                project_id,
                exc,
                "[workflow] plan single outline failed project_id=%d chapter_index=%d",
                project_id,
                target_index,
            )
            raise ChapterOutlineWorkflowError(f"生成单章规划失败: {exc}", status_code=500)

    def _resolve_target_chapter_index(self, project_id: int, chapter_index: Optional[int]) -> int:
        try:
            value = int(chapter_index or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return value
        outlines = self.get_outlines(project_id)
        return max((int(outline.chapter_index or 0) for outline in outlines), default=0) + 1

    def _get_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ChapterOutlineWorkflowError("项目不存在", status_code=404)
        return project

    def _get_story_bible(self, project_id: int, missing_message: str) -> Any:
        bible = effective_story_bible(self.db, project_id)
        if not bible:
            xlog.warn(project_id, "[workflow] outline missing bible project_id=%d", project_id)
            raise ChapterOutlineWorkflowError(missing_message, status_code=404)
        return bible

    def _outline_to_llm_dict(self, outline: ChapterOutline) -> dict[str, Any]:
        return {
            "chapter_index": outline.chapter_index,
            "title": outline.title,
            "summary": outline.summary,
            "conflict": outline.conflict,
            "characters": outline.characters,
            "scene": outline.scene,
            "emotion": outline.emotion,
            "visual_keywords": outline.visual_keywords,
        }

    async def _emit_outline_ready(
        self,
        stage: str,
        project_id: int,
        outlines: list[ChapterOutline],
    ) -> None:
        await self._emit(
            {
                "type": stage,
                "project_id": project_id,
                "chapter_count": len(outlines),
                "chapters": [self._outline_to_event_dict(outline) for outline in outlines],
            }
        )

    def _outline_to_event_dict(self, outline: ChapterOutline) -> dict[str, Any]:
        return {
            "id": outline.id,
            "project_id": outline.project_id,
            "chapter_index": outline.chapter_index,
            "title": outline.title,
            "summary": outline.summary,
            "conflict": outline.conflict,
            "characters": outline.characters,
            "scene": outline.scene,
            "emotion": outline.emotion,
            "visual_keywords": outline.visual_keywords,
            "status": outline.status,
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result
