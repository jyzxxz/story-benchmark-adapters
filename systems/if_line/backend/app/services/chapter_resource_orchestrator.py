"""
章节资源生成编排服务。

兼容 HTTP / workflow / agent tool 的薄适配层。视觉写入统一委托给
Chapter Script resource render 任务，不直接调用图片 provider。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.orm import Session

from app.application.chapter_script_service import request_script_resource_render
from app.models import Project
from app.models_v2 import ChapterScriptHead, ChapterScriptRevision
from app.services.image_generation_service import IMAGE_GENERATION_ENABLED
from app.services.tts_service import TTS_ENABLED
from app.services.workflow_events import (
    EEvent_resource_done,
    EEvent_resource_start,
    EEvent_resources_done,
    EEvent_resources_start,
)
from app.utils import logging as xlog


RESOURCE_BACKGROUND = "background"
RESOURCE_PORTRAIT = "portrait"
RESOURCE_KEYFRAME = "keyframe"

DEFAULT_CHAPTER_RESOURCE_TYPES = [
    RESOURCE_BACKGROUND,
    RESOURCE_PORTRAIT,
    RESOURCE_KEYFRAME,
]
VALID_CHAPTER_RESOURCE_TYPES = set(DEFAULT_CHAPTER_RESOURCE_TYPES)

ChapterResourceEventHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class ChapterResourceOrchestratorError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class ChapterResourceRequestData:
    project_id: int
    chapter_index: int
    resource_types: list[str] = field(default_factory=list)
    background_moods: Optional[list[str]] = None
    portrait_batch_size: int = 5
    portrait_auto_demand: bool = True
    script_revision_id: Optional[str] = None


class ChapterResourceOrchestrator:
    def __init__(
        self,
        db: Session,
        emit: Optional[ChapterResourceEventHandler] = None,
    ) -> None:
        self.db = db
        self.emit = emit

    async def generate_chapter_resources(self, request: ChapterResourceRequestData) -> dict[str, Any]:
        resource_types = self._normalize_resource_types(request.resource_types)
        self._check_feature_flags(resource_types)
        xlog.info(
            request.project_id,
            "[chapter-resource] generate start project_id=%d chapter_index=%d resource_types=%s",
            request.project_id,
            request.chapter_index,
            ",".join(resource_types),
        )
        await self._emit(
            {
                "type": EEvent_resources_start,
                "project_id": request.project_id,
                "chapter_index": request.chapter_index,
                "resource_types": resource_types,
            }
        )

        results: dict[str, Any] = {}
        total = 0
        generated = 0
        failed = 0

        for resource_type in resource_types:
            await self._emit(
                {
                    "type": EEvent_resource_start,
                    "project_id": request.project_id,
                    "chapter_index": request.chapter_index,
                    "resource_type": resource_type,
                }
            )
            result = await self._run_one(resource_type, request)
            summary = self._summarize_resource_result(resource_type, result)
            results[resource_type] = {
                **summary,
                "result": result,
            }
            total += summary["total"]
            generated += summary["generated"]
            failed += summary["failed"]
            await self._emit(
                {
                    "type": EEvent_resource_done,
                    "project_id": request.project_id,
                    "chapter_index": request.chapter_index,
                    "resource_type": resource_type,
                    **summary,
                }
            )

        response = {
            "ok": True,
            "project_id": request.project_id,
            "chapter_index": request.chapter_index,
            "resource_types": resource_types,
            "total": total,
            "generated": generated,
            "failed": failed,
            "results": results,
        }
        await self._emit(
            {
                "type": EEvent_resources_done,
                "project_id": request.project_id,
                "chapter_index": request.chapter_index,
                "resource_types": resource_types,
                "total": total,
                "generated": generated,
                "failed": failed,
            }
        )
        xlog.info(
            request.project_id,
            "[chapter-resource] generate ok project_id=%d chapter_index=%d total=%d generated=%d failed=%d",
            request.project_id,
            request.chapter_index,
            total,
            generated,
            failed,
        )
        return response

    async def _run_one(self, resource_type: str, request: ChapterResourceRequestData) -> Any:
        project = self.db.query(Project).filter(Project.id == request.project_id).first()
        if not project or project.owner_id is None:
            raise ChapterResourceOrchestratorError("项目不存在或尚未归属用户", status_code=404)
        revision = _resolve_chapter_script_revision(
            self.db,
            project_id=request.project_id,
            chapter_index=request.chapter_index,
            script_revision_id=request.script_revision_id,
        )
        # 该 role 无槽位时直接返回零值摘要，不创建 0 子任务的空批次
        # （空批次会被聚合器标 dependency_inconsistent，污染任务历史）。
        from app.models_v2 import ScriptResourceSlot

        role_slot_count = (
            self.db.query(ScriptResourceSlot)
            .filter(
                ScriptResourceSlot.chapter_script_revision_id == revision.id,
                ScriptResourceSlot.role == resource_type,
            )
            .count()
        )
        if role_slot_count == 0:
            return {
                "total": 0,
                "generated": 0,
                "failed": 0,
                "task_id": None,
                "task_status": "skipped_no_slots",
                "created": False,
            }
        result = request_script_resource_render(
            self.db,
            user_id=project.owner_id,
            project_id=request.project_id,
            script_revision_id=revision.id,
            role=resource_type,
        )
        parent = result["parent"]
        return {
            "total": int((parent.result_refs or {}).get("selected_slot_count") or 0),
            "generated": len((parent.result_refs or {}).get("cached_version_ids") or []),
            "failed": 0,
            "task_id": parent.id,
            "task_status": parent.status,
            "created": bool(result.get("parent_created")),
        }

    def _normalize_resource_types(self, resource_types: list[str]) -> list[str]:
        source = resource_types or []
        result: list[str] = []
        for item in source:
            value = str(item or "").strip()
            if not value:
                continue
            if value not in VALID_CHAPTER_RESOURCE_TYPES:
                raise ChapterResourceOrchestratorError(
                    f"不支持的资源类型: {value}，可选值: {', '.join(DEFAULT_CHAPTER_RESOURCE_TYPES)}",
                    status_code=400,
                )
            if value not in result:
                result.append(value)
        if len(result) != 1:
            raise ChapterResourceOrchestratorError(
                "每次只能生成一种资源，请指定 portrait、background 或 keyframe",
                status_code=422,
            )
        return result

    def _check_feature_flags(self, resource_types: list[str]) -> None:
        image_types = {RESOURCE_BACKGROUND, RESOURCE_PORTRAIT, RESOURCE_KEYFRAME}
        if image_types.intersection(resource_types) and not IMAGE_GENERATION_ENABLED:
            raise ChapterResourceOrchestratorError("图像生成功能未启用", status_code=503)

    def _summarize_resource_result(self, resource_type: str, result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            total = int(result.get("total") or 0)
            generated = int(result.get("generated") or 0)
            failed = int(result.get("failed") or 0)
            error = result.get("error")
            return {
                "total": total,
                "generated": generated,
                "failed": failed,
                "error": error,
            }
        return {
            "total": 0,
            "generated": 0,
            "failed": 0,
            "error": "未知资源生成结果",
        }

    async def _emit(self, event: dict[str, Any]) -> None:
        if self.emit is None:
            return
        result = self.emit(event)
        if hasattr(result, "__await__"):
            await result


def _resolve_chapter_script_revision(
    db: Session,
    *,
    project_id: int,
    chapter_index: int,
    script_revision_id: Optional[str],
) -> ChapterScriptRevision:
    """脚本修订三级解析：显式 id → head 当前修订 → 最新已生成修订。

    脚本生成任务本身不激活 chapter_script_heads.current_revision_id，只查
    head 会在"脚本已生成但未人工激活"时误报 404，因此最后回退到最新修订。
    """
    if script_revision_id:
        revision = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == script_revision_id,
                ChapterScriptRevision.project_id == project_id,
            )
            .first()
        )
        if revision is None:
            raise ChapterResourceOrchestratorError(
                "指定的章节脚本修订不存在", status_code=404
            )
        return revision
    head = (
        db.query(ChapterScriptHead)
        .filter(
            ChapterScriptHead.project_id == project_id,
            ChapterScriptHead.chapter_index == chapter_index,
        )
        .first()
    )
    if head and head.current_revision_id:
        revision = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == head.current_revision_id,
                ChapterScriptRevision.project_id == project_id,
            )
            .first()
        )
        if revision is not None:
            return revision
    revision = (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.project_id == project_id,
            ChapterScriptRevision.chapter_index == chapter_index,
        )
        .order_by(
            ChapterScriptRevision.revision_no.desc(),
            ChapterScriptRevision.created_at.desc(),
        )
        .first()
    )
    if revision is None:
        raise ChapterResourceOrchestratorError(
            "章节脚本修订不存在，请先为该章节生成章节脚本（generate_chapter_script）",
            status_code=404,
        )
    return revision
