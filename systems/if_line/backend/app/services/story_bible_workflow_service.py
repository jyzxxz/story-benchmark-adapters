"""
StoryBible 生成服务。

这是项目生成流的第一段：项目输入 -> StoryBible。HTTP 路由和 Agent tool
共用这里，避免各自拼 prompt 和写库。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.models import Project, StoryBible
from app.services.llm_service import llm_service
from app.services.prompt_builder_service import prompt_builder_service
from app.services.source_visual_profile_service import source_visual_profile_service
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_events import EEvent_bible_done, EEvent_bible_start
from app.utils import logging as xlog


StoryBibleEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class StoryBibleWorkflowError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class StoryBibleWorkflowResult:
    story_bible: StoryBible


class StoryBibleWorkflowService:
    def __init__(
        self,
        db: Session,
        emit: Optional[StoryBibleEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def generate_story_bible(self, project_id: int) -> StoryBibleWorkflowResult:
        xlog.debug(project_id, "[workflow] generate story bible start project_id=%d", project_id)
        project = self._get_project(project_id)
        xlog.debug(project_id, "[workflow] project loaded title=%s", project.title)

        await self._emit(
            {
                "type": EEvent_bible_start,
                "project_id": project_id,
                "title": project.title,
                "character_count": len(project.characters or []),
                "pace": project.pace or "medium",
            }
        )

        try:
            xlog.debug(project_id, "[workflow] call llm service")

            # 调用 LLM 生成 Story Bible
            bible_output = await llm_service.generate_story_bible(
                title=project.title,
                characters=project.characters or [],
                story_start=project.story_start,
                story_end=project.story_end,
                style=project.style or "",
                pace=project.pace or "medium",
                extra_requirements=project.extra_requirements or "",
                uuid=project_id,
                source_work=getattr(project, "source_work", "") or "",
            )
            xlog.debug(
                project_id,
                "[workflow] llm generated worldview_prefix=%s",
                bible_output.worldview[:50],
            )

            bible_raw = prompt_builder_service.enrich_story_bible_genders(
                bible_output.dict(),
                context_text=" ".join(
                    [
                        project.title or "",
                        project.story_start or "",
                        project.story_end or "",
                        project.extra_requirements or "",
                    ]
                ),
            )
            if getattr(project, "source_work", None):
                bible_raw["source_work"] = project.source_work
            bible_raw = source_visual_profile_service.apply_detected_character_identities(
                bible_raw
            )

            # 保存到数据库
            story_bible = StoryBible(
                project_id=project_id,
                worldview=bible_raw.get("worldview"),
                characters=bible_raw.get("characters") or [],
                character_relations=bible_raw.get("character_relations"),
                main_conflict=bible_raw.get("main_conflict"),
                emotional_line=bible_raw.get("emotional_line"),
                style_rules=bible_raw.get("style_rules"),
                ending_constraints=bible_raw.get("ending_constraints"),
                forbidden_points=bible_raw.get("forbidden_points") or [],
                writing_notes=bible_raw.get("writing_notes") or [],
                raw_json=bible_raw,
            )
            self.db.add(story_bible)

            xlog.debug(project_id, "[workflow] transition project status to bible_generated")

            # 更新项目状态
            engine = WorkflowEngine(self.db)
            engine.transition_to(project, "bible_generated")

            self.db.commit()
            self.db.refresh(story_bible)
            xlog.info(
                project_id,
                "[workflow] generate story bible ok project_id=%d bible_id=%d",
                project_id,
                story_bible.id,
            )
            await self._emit(
                {
                    "type": EEvent_bible_done,
                    "project_id": project_id,
                    "story_bible_id": story_bible.id,
                    "worldview_preview": (story_bible.worldview or "")[:300],
                    "character_count": len(story_bible.characters or []),
                    "main_conflict": story_bible.main_conflict,
                }
            )
            return StoryBibleWorkflowResult(story_bible=story_bible)
        except Exception as exc:
            self.db.rollback()
            xlog.error(project_id, exc, "[workflow] generate story bible failed project_id=%d", project_id)
            raise StoryBibleWorkflowError(f"生成 Story Bible 失败: {exc}", status_code=500)

    def get_story_bible(self, project_id: int) -> StoryBible:
        bible = self.db.query(StoryBible).filter(StoryBible.project_id == project_id).first()
        if not bible:
            raise StoryBibleWorkflowError("Story Bible 不存在", status_code=404)
        return bible

    def _get_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise StoryBibleWorkflowError("项目不存在", status_code=404)
        return project

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result
