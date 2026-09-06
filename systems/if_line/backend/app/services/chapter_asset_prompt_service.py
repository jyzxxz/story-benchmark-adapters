"""
章节素材 Prompt 生成服务。

调用 LLM 生成角色/背景/关键帧素材 Prompt，并写入
Asset(status="prompt_generated")。不负责真实图片生成。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.models import Asset, ChapterContent, ChapterOutline, Project, StoryBible
from app.services.chapter_outline_workflow_service import effective_story_bible
from app.services.generation_stat_service import GenerationStatService
from app.services.llm_service import llm_service
from app.services.workflow_engine import WorkflowEngine
from app.services.workflow_events import EEvent_assets_done, EEvent_assets_start
from app.utils import logging as xlog


VALID_ASSET_PROMPT_GENRES = [
    "historical",
    "modern",
    "sci-fi",
    "fantasy",
    "anime",
    "realistic",
    "oil_painting",
    "watercolor",
    "sketch",
]

ChapterAssetPromptEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class ChapterAssetPromptError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ChapterAssetPromptResult:
    character_prompts: list[Asset]
    background_prompts: list[Asset]
    keyframe_prompts: list[Asset]


class ChapterAssetPromptService:
    def __init__(
        self,
        db: Session,
        emit: Optional[ChapterAssetPromptEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def generate_chapter_asset_prompts(
        self,
        project_id: int,
        chapter_index: int,
        genre: Optional[str] = None,
    ) -> ChapterAssetPromptResult:
        xlog.info(
            project_id,
            "[asset] generate prompts start project_id=%d chapter_index=%d",
            project_id,
            chapter_index,
        )
        # 验证 genre 参数
        if genre and genre not in VALID_ASSET_PROMPT_GENRES:
            raise ChapterAssetPromptError(
                f"无效的画风类型，可选值: {', '.join(VALID_ASSET_PROMPT_GENRES)}",
                status_code=400,
            )

        project = self._get_project(project_id)
        # 双存储回落：generate_story_bible 可能只写 v2 修订（不落 legacy 表）。
        bible = effective_story_bible(self.db, project_id)
        if not bible:
            raise ChapterAssetPromptError("Story Bible 不存在", status_code=404)

        # 获取章节内容
        content = (
            self.db.query(ChapterContent)
            .filter(
                ChapterContent.project_id == project_id,
                ChapterContent.chapter_index == chapter_index,
            )
            .first()
        )
        if not content:
            raise ChapterAssetPromptError("章节正文不存在", status_code=404)
        if not (content.content or "").strip():
            raise ChapterAssetPromptError(
                "章节正文为空，无法生成素材 Prompt（请先生成正文）", status_code=409
            )

        # 幂等：同章已有素材 Prompt 时直接返回既有结果。此前无查重，重复
        # run_chapter_workflow / 资源失败重跑会整套追加重复 Asset 行。
        existing_prompts = self.get_chapter_asset_prompts(project_id, chapter_index)
        if (
            existing_prompts.character_prompts
            or existing_prompts.background_prompts
            or existing_prompts.keyframe_prompts
        ):
            xlog.info(
                project_id,
                "[asset] generate prompts skip(existing) project_id=%d chapter_index=%d",
                project_id,
                chapter_index,
            )
            return existing_prompts

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
            raise ChapterAssetPromptError("章节大纲不存在", status_code=404)

        # 开始记录生成时间
        stat_service = GenerationStatService(self.db)
        stat = stat_service.start_generation(project_id, "asset", chapter_index)
        await self._emit(
            {
                "type": EEvent_assets_start,
                "project_id": project_id,
                "chapter_index": chapter_index,
                "genre": genre,
                "content_length": len(content.content or ""),
            }
        )

        try:
            # 调用 LLM 生成视觉 Prompt
            assets_output = await llm_service.generate_asset_prompts(
                story_bible=bible.raw_json,
                chapter_content=content.content,
                chapter_outline={
                    "chapter_index": outline.chapter_index,
                    "title": outline.title,
                    "scene": outline.scene,
                },
                genre=genre,
                uuid=project_id,
            )

            # 获取 LLM 检测/使用的画风
            detected_genre = getattr(assets_output, "detected_genre", genre or "auto")

            # 保存到数据库
            # 角色立绘
            for char_prompt in assets_output.character_prompts:
                self.db.add(
                    Asset(
                        project_id=project_id,
                        chapter_index=chapter_index,
                        asset_type="character",
                        target_name=char_prompt.get("name", ""),
                        prompt=char_prompt.get("sd_prompt", char_prompt.get("prompt", "")),
                        description_cn=char_prompt.get("description_cn", ""),
                        emotion=char_prompt.get("emotion"),
                        outfit=char_prompt.get("outfit"),
                        pose=char_prompt.get("pose"),
                        genre=detected_genre,
                        status="prompt_generated",
                    )
                )

            # 背景图
            for bg_prompt in assets_output.background_prompts:
                self.db.add(
                    Asset(
                        project_id=project_id,
                        chapter_index=chapter_index,
                        asset_type="background",
                        target_name=bg_prompt.get("scene", ""),
                        prompt=bg_prompt.get("sd_prompt", bg_prompt.get("prompt", "")),
                        description_cn=bg_prompt.get("description_cn", ""),
                        mood=bg_prompt.get("mood"),
                        genre=detected_genre,
                        status="prompt_generated",
                    )
                )

            # 关键帧
            for kf_prompt in assets_output.keyframe_prompts:
                self.db.add(
                    Asset(
                        project_id=project_id,
                        chapter_index=chapter_index,
                        asset_type="keyframe",
                        target_name=kf_prompt.get("event_name", kf_prompt.get("name", "")),
                        prompt=kf_prompt.get("sd_prompt", kf_prompt.get("prompt", "")),
                        description_cn=kf_prompt.get("description_cn", ""),
                        genre=detected_genre,
                        status="prompt_generated",
                    )
                )

            # 更新项目状态
            if project.status == "chapter_generating":
                engine = WorkflowEngine(self.db)
                engine.transition_to(project, "asset_generating")

            self.db.commit()

            # 结束记录生成时间
            stat_service.end_generation(stat.id, success=True)
            result = self.get_chapter_asset_prompts(project_id, chapter_index)
            await self._emit(
                {
                    "type": EEvent_assets_done,
                    "project_id": project_id,
                    "chapter_index": chapter_index,
                    "genre": detected_genre,
                    "character_prompt_count": len(result.character_prompts),
                    "background_prompt_count": len(result.background_prompts),
                    "keyframe_prompt_count": len(result.keyframe_prompts),
                }
            )
            xlog.info(
                project_id,
                "[asset] generate prompts ok project_id=%d chapter_index=%d asset_count=%d",
                project_id,
                chapter_index,
                len(result.character_prompts) + len(result.background_prompts) + len(result.keyframe_prompts),
            )
            return result
        except ChapterAssetPromptError:
            raise
        except Exception as exc:
            # 记录失败
            stat_service.end_generation(stat.id, success=False, error_message=str(exc))
            self.db.commit()
            xlog.error(
                project_id,
                exc,
                "[asset] generate prompts failed project_id=%d chapter_index=%d",
                project_id,
                chapter_index,
            )
            raise ChapterAssetPromptError(f"生成视觉素材失败: {exc}", status_code=500)

    def get_chapter_asset_prompts(self, project_id: int, chapter_index: int) -> ChapterAssetPromptResult:
        assets = (
            self.db.query(Asset)
            .filter(Asset.project_id == project_id, Asset.chapter_index == chapter_index)
            .all()
        )
        return ChapterAssetPromptResult(
            character_prompts=[asset for asset in assets if asset.asset_type == "character"],
            background_prompts=[asset for asset in assets if asset.asset_type == "background"],
            keyframe_prompts=[asset for asset in assets if asset.asset_type == "keyframe"],
        )

    def _get_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ChapterAssetPromptError("项目不存在", status_code=404)
        return project

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result
