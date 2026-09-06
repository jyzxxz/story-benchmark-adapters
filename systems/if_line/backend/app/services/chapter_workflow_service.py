"""
章节生成流水线编排服务。

这里不重新实现生成逻辑，只按顺序调用已经抽离的步骤服务。Agent 可以继续逐步调用
单个工具；HTTP 也可以直接 POST 触发同样的流水线。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.models import Project
from app.services.chapter_generation_service import (
    ChapterGenerationError,
    ChapterGenerationService,
)
from app.services.chapter_asset_prompt_service import (
    ChapterAssetPromptError,
    ChapterAssetPromptService,
)
from app.services.chapter_resource_orchestrator import (
    ChapterResourceOrchestrator,
    ChapterResourceOrchestratorError,
    ChapterResourceRequestData,
)
from app.services.chapter_workspace_service import (
    ChapterWorkspaceExists,
    create_empty_chapter_workspace,
)
from app.services.workflow_events import (
    EEvent_workflow_done,
    EEvent_workflow_start,
    EEvent_workspace_done,
)


ChapterWorkflowEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]

EWorkflowStatus_completed = "completed"
EWorkflowStatus_partial_failed = "partial_failed"


class ChapterWorkflowError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ChapterWorkflowRequestData:
    project_id: int
    chapter_index: Optional[int] = None
    title: str = ""
    summary: str = ""
    conflict: str = ""
    characters: Optional[list[str]] = None
    scene: str = ""
    emotion: str = ""
    visual_keywords: Optional[list[str]] = None
    create_workspace: bool = False
    generate_content: bool = True
    generate_asset_prompts: bool = False
    asset_prompt_genre: Optional[str] = None
    generate_resources: bool = False
    resource_types: Optional[list[str]] = None
    background_moods: Optional[list[str]] = None
    portrait_batch_size: int = 5
    portrait_auto_demand: bool = True
    word_count_min: int = 3000
    word_count_max: int = 4500


class ChapterWorkflowService:
    def __init__(
        self,
        db: Session,
        emit: Optional[ChapterWorkflowEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def run_chapter_workflow(self, request: ChapterWorkflowRequestData) -> dict[str, Any]:
        self._ensure_project(request.project_id)
        result: dict[str, Any] = {
            "ok": True,
            "status": EWorkflowStatus_completed,
            "project_id": request.project_id,
            "chapter_index": request.chapter_index,
            "steps": [],
            "recoverable_failures": [],
            "next_actions": [],
        }

        await self._emit(
            {
                "type": EEvent_workflow_start,
                "project_id": request.project_id,
                "chapter_index": request.chapter_index,
                "create_workspace": request.create_workspace,
                "generate_content": request.generate_content,
                "generate_asset_prompts": request.generate_asset_prompts,
                "generate_resources": request.generate_resources,
            }
        )

        if request.create_workspace:
            # 创建空章节工作区：最小 ChapterOutline 和空 ChapterContent。
            workspace = self._create_workspace(request)
            request.chapter_index = workspace["chapter_index"]
            result["chapter_index"] = request.chapter_index
            result["workspace"] = workspace
            result["steps"].append({"name": "create_empty_chapter_workspace", "ok": True})
            await self._emit(
                {
                    "type": EEvent_workspace_done,
                    "project_id": request.project_id,
                    "chapter_index": request.chapter_index,
                    "title": workspace.get("title"),
                    "outline_id": workspace.get("outline_id"),
                    "content_id": workspace.get("content_id"),
                }
            )

        chapter_index = self._require_chapter_index(request.chapter_index)

        if request.generate_content:
            # 调用正式章节正文生成流程，为目标章节生成 ChapterContent。
            try:
                content_result = await ChapterGenerationService(
                    self.db,
                    emit=self._emit,
                ).generate_chapter_content(
                    project_id=request.project_id,
                    chapter_index=chapter_index,
                    word_count_min=request.word_count_min,
                    word_count_max=request.word_count_max,
                )
            except ChapterGenerationError as exc:
                raise ChapterWorkflowError(exc.message, exc.status_code)

            content = content_result.content
            result["content"] = {
                "content_id": content.id,
                "status": content.status,
                "skipped": content_result.skipped,
                "content_length": len(content.content or ""),
                "content_preview": (content.content or "")[:300],
            }
            result["steps"].append({"name": "generate_chapter_content", "ok": True})

        if request.generate_asset_prompts:
            # 调用章节素材 Prompt 生成流程；这里只写 Asset Prompt，不生成真实图片。
            try:
                asset_result = await ChapterAssetPromptService(
                    self.db,
                    emit=self._emit,
                ).generate_chapter_asset_prompts(
                    project_id=request.project_id,
                    chapter_index=chapter_index,
                    genre=request.asset_prompt_genre,
                )
            except ChapterAssetPromptError as exc:
                raise ChapterWorkflowError(exc.message, exc.status_code)

            result["asset_prompts"] = {
                "character_prompt_count": len(asset_result.character_prompts),
                "background_prompt_count": len(asset_result.background_prompts),
                "keyframe_prompt_count": len(asset_result.keyframe_prompts),
            }
            result["steps"].append({"name": "generate_chapter_asset_prompts", "ok": True})

        if request.generate_resources:
            # 统一生成章节级真实资源：背景、立绘、关键帧、配音。
            try:
                resource_result = await ChapterResourceOrchestrator(
                    self.db,
                    emit=self._emit,
                ).generate_chapter_resources(
                    ChapterResourceRequestData(
                        project_id=request.project_id,
                        chapter_index=chapter_index,
                        resource_types=request.resource_types or [],
                        background_moods=request.background_moods,
                        portrait_batch_size=request.portrait_batch_size,
                        portrait_auto_demand=request.portrait_auto_demand,
                    )
                )
            except ChapterResourceOrchestratorError as exc:
                raise ChapterWorkflowError(exc.message, exc.status_code)

            result["resources"] = {
                "resource_types": resource_result.get("resource_types") or [],
                "total": resource_result.get("total") or 0,
                "generated": resource_result.get("generated") or 0,
                "failed": resource_result.get("failed") or 0,
                "results": resource_result.get("results") or {},
            }
            resource_failures = self._build_resource_failures(
                resource_result,
                request.project_id,
                chapter_index,
            )
            if resource_failures:
                result["status"] = EWorkflowStatus_partial_failed
                result["recoverable_failures"].extend(resource_failures)
                result["next_actions"].append(
                    self._build_retry_resources_action(
                        request.project_id,
                        chapter_index,
                        [item["type"] for item in resource_failures],
                    )
                )
            result["steps"].append(
                {
                    "name": "generate_chapter_resources",
                    "ok": not resource_failures,
                    "status": (
                        EWorkflowStatus_partial_failed
                        if resource_failures
                        else EWorkflowStatus_completed
                    ),
                    "total": result["resources"]["total"],
                    "generated": result["resources"]["generated"],
                    "failed": result["resources"]["failed"],
                    "retryable": bool(resource_failures),
                }
            )

        await self._emit(
            {
                "type": EEvent_workflow_done,
                "project_id": request.project_id,
                "chapter_index": chapter_index,
                "steps": [step["name"] for step in result["steps"]],
                "status": result["status"],
            }
        )
        return result

    def _build_resource_failures(
        self,
        resource_result: dict[str, Any],
        project_id: int,
        chapter_index: int,
    ) -> list[dict[str, Any]]:
        failures: list[dict[str, Any]] = []
        for resource_type, detail in (resource_result.get("results") or {}).items():
            failed = int((detail or {}).get("failed") or 0)
            if failed <= 0:
                continue
            failures.append(
                {
                    "type": resource_type,
                    "project_id": project_id,
                    "chapter_index": chapter_index,
                    "total": int((detail or {}).get("total") or 0),
                    "generated": int((detail or {}).get("generated") or 0),
                    "failed": failed,
                    "reason": self._first_resource_error(detail),
                    "retryable": True,
                }
            )
        return failures

    def _first_resource_error(self, detail: Any) -> str:
        if not isinstance(detail, dict):
            return "未知资源生成失败"
        if detail.get("error"):
            return str(detail["error"])
        result = detail.get("result")
        if isinstance(result, dict):
            errors = result.get("errors")
            if isinstance(errors, list) and errors:
                first_error = errors[0]
                if isinstance(first_error, dict):
                    return str(first_error.get("message") or first_error.get("code") or first_error)
                return str(first_error)
            results = result.get("results")
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    reason = item.get("reason") or item.get("error")
                    if reason:
                        return str(reason)
            voice_lines = result.get("voice_lines")
            if isinstance(voice_lines, list):
                for item in voice_lines:
                    if isinstance(item, dict) and item.get("error"):
                        return str(item["error"])
        return "资源生成失败"

    def _build_retry_resources_action(
        self,
        project_id: int,
        chapter_index: int,
        resource_types: list[str],
    ) -> dict[str, Any]:
        return {
            "label": "重试失败资源",
            "tool": "retry_chapter_resources",
            "arguments": {
                "project_id": project_id,
                "chapter_index": chapter_index,
                "resource_types": resource_types,
                "only_failed": True,
            },
        }

    def _create_workspace(self, request: ChapterWorkflowRequestData) -> dict[str, Any]:
        try:
            outline, content = create_empty_chapter_workspace(
                db=self.db,
                project_id=request.project_id,
                chapter_index=request.chapter_index,
                title=request.title,
                summary=request.summary,
                conflict=request.conflict,
                characters=request.characters or [],
                scene=request.scene,
                emotion=request.emotion,
                visual_keywords=request.visual_keywords or [],
            )
        except ChapterWorkspaceExists as exc:
            raise ChapterWorkflowError(str(exc), status_code=409)

        self.db.commit()
        self.db.refresh(outline)
        self.db.refresh(content)
        return {
            "chapter_index": outline.chapter_index,
            "title": outline.title,
            "outline_id": outline.id,
            "content_id": content.id,
            "content_status": content.status,
        }

    def _ensure_project(self, project_id: int) -> Project:
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            raise ChapterWorkflowError("项目不存在", status_code=404)
        return project

    def _require_chapter_index(self, chapter_index: Optional[int]) -> int:
        try:
            value = int(chapter_index or 0)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            raise ChapterWorkflowError("chapter_index 必须是正整数", status_code=400)
        return value

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result
