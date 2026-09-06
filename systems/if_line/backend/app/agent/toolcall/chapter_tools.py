"""
章节生成工具。

Agent 侧只保留单章规划和章节产物流水线入口，不暴露“生成/修改/确认整本书章节规划”
这类全局 outline 工具。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent.agent_spec import AgentToolHandle
from app.core.errors import AppError
from app.models import Asset, ChapterContent, ChapterOutline, Project, StoryBible
from app.models_v2 import (
    AssetVersion,
    ChapterHead,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    ProjectContentHead,
    ScriptResourceSlot,
    StorageObject,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)
from app.application.chapter_script_service import (
    bind_script_resource_slot,
    create_chapter_script_generation_task,
    list_script_resource_slots,
    queue_vn_graph_when_resources_ready,
)
from app.application.revision_head_service import activate_path_chapter_revision_head
from app.application.revision_service import (
    build_bible_generation_source,
    create_bible_revision,
)
from app.application.story_chapter_service import create_manual_story_path_chapter_revision
from app.application.story_outline_service import (
    activate_story_path_outline_revision,
    create_story_path_outline_revision,
)
from app.services.chapter_asset_prompt_service import (
    ChapterAssetPromptService,
    VALID_ASSET_PROMPT_GENRES,
)
from app.services.chapter_outline_workflow_service import (
    ChapterOutlineWorkflowError,
    ChapterOutlineWorkflowService,
    effective_story_bible,
)
from app.services.chapter_resource_orchestrator import DEFAULT_CHAPTER_RESOURCE_TYPES
from app.services.chapter_resource_orchestrator import (
    ChapterResourceOrchestrator,
    ChapterResourceOrchestratorError,
    ChapterResourceRequestData,
)
from app.services.chapter_workflow_service import (
    ChapterWorkflowError,
    ChapterWorkflowRequestData,
    ChapterWorkflowService,
    EWorkflowStatus_completed,
    EWorkflowStatus_partial_failed,
)


CHAPTER_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_single_chapter_outline",
            "description": "读取某个章节的一条 ChapterOutline。不是整本书章节规划列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_single_chapter",
            "description": "基于 StoryBible、已有章节规划摘要和用户要求，生成或覆盖一个章节的一条 ChapterOutline。用于单章/分叉章节规划，不会重写整本书章节规划。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {
                        "type": "integer",
                        "description": "可选。目标章节号；不传时使用当前项目下一章。",
                    },
                    "user_instruction": {
                        "type": "string",
                        "description": "用户对目标章节的自然语言要求。",
                    },
                    "reference_context": {
                        "type": "object",
                        "description": "可选。参考上下文，例如分叉来源章节、选中 VNGraph 节点摘要、前情摘要等。",
                    },
                    "overwrite": {
                        "type": "boolean",
                        "default": False,
                        "description": "目标章节已有 outline 时是否覆盖。",
                    },
                },
                "required": ["project_id", "user_instruction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_single_chapter_outline",
            "description": "确认一个已经生成的单章 ChapterOutline，把它从 pending 推进到 approved。通常在用户确认 plan_single_chapter 的规划后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_content_status",
            "description": "检查某个章节正文是否存在，以及正文长度和状态。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_empty_chapter_workspace",
            "description": "创建一个后端空章节工作区：最小 ChapterOutline 和空 ChapterContent。当前通过正式章节流水线创建，只开工作区，不生成正文和资源。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {
                        "type": "integer",
                        "description": "可选。目标章节号；不传或小于 1 时自动使用当前项目下一章。",
                    },
                    "title": {"type": "string", "description": "章节标题。"},
                    "summary": {"type": "string", "description": "可选。章节概要。"},
                    "conflict": {"type": "string", "description": "可选。章节冲突。"},
                    "characters": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。出场人物。",
                    },
                    "scene": {"type": "string", "description": "可选。主要场景。"},
                    "emotion": {"type": "string", "description": "可选。情绪基调。"},
                    "visual_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选。视觉关键词。",
                    },
                },
                "required": ["project_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_asset_prompts",
            "description": "读取目标章节已经生成的角色/背景/关键帧素材 Prompt。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_chapter_workflow",
            "description": "对已确认的单章规划执行章节生成流水线：生成正文、素材 Prompt 和章节级真实资源。通常在 approve_single_chapter_outline 后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer", "description": "目标章节号。该章节应已经有 ChapterOutline。"},
                    "asset_prompt_genre": {
                        "type": "string",
                        "enum": VALID_ASSET_PROMPT_GENRES,
                        "description": "素材 Prompt 画风；不传则自动检测。",
                    },
                    "resource_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": DEFAULT_CHAPTER_RESOURCE_TYPES},
                        "description": "要生成的资源类型；不传默认 background/portrait/keyframe/voice 全部执行。",
                    },
                    "background_moods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选背景氛围列表，例如 day/night/rain。",
                    },
                    "portrait_batch_size": {"type": "integer", "default": 5},
                    "portrait_auto_demand": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否从章节正文自动分析立绘变体需求。",
                    },
                    "word_count_min": {"type": "integer", "default": 3000},
                    "word_count_max": {"type": "integer", "default": 4500},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "retry_chapter_resources",
            "description": "只重试目标章节的外部资源生成，不会重跑章节规划或正文生成。需要章节已有章节脚本（chapter script）；一次只传一种 resource_type。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                    "script_revision_id": {
                        "type": "string",
                        "description": "可选。目标章节脚本修订 id；不传时自动使用该章节最新可用脚本修订。",
                    },
                    "resource_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": DEFAULT_CHAPTER_RESOURCE_TYPES},
                        "description": "要重试的资源类型；不传则默认重试 background/portrait/keyframe/voice。",
                    },
                    "only_failed": {
                        "type": "boolean",
                        "default": True,
                        "description": "语义上表示重试失败资源。当前底层按资源类型重跑，不重跑正文和章节规划。",
                    },
                    "background_moods": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选背景氛围列表，例如 day/night/rain。",
                    },
                    "portrait_batch_size": {"type": "integer", "default": 5},
                    "portrait_auto_demand": {
                        "type": "boolean",
                        "default": True,
                        "description": "是否从章节正文自动分析立绘变体需求。",
                    },
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_chapter_script",
            "description": "为目标章节创建章节脚本（chapter script）生成任务。脚本是配音、绘图资源渲染和 VNGraph 编译的前置。只创建任务并返回 task_id，需要用 wait_for_task 观察任务；任务 succeeded 前不要声称脚本已生成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                    "instructions": {
                        "type": "string",
                        "description": "可选。对脚本切分/标注的附加要求。",
                    },
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_script",
            "description": "读取某个章节当前最新的章节脚本（角色、场景、段落切分与标注摘要）。不写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                    "script_revision_id": {
                        "type": "string",
                        "description": "可选。指定脚本修订 id；不传时自动使用该章节最新可用脚本修订。",
                    },
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_resource_slots",
            "description": "读取某个章节脚本已规划的资源槽位及其状态（planned/generating/bound/failed）和已绑定图片地址。不写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "chapter_index": {"type": "integer"},
                    "script_revision_id": {
                        "type": "string",
                        "description": "可选。指定脚本修订 id；不传时自动使用该章节最新可用脚本修订。",
                    },
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_assets",
            "description": "列出项目里可绑定的素材及其版本（含图片地址），供给资源槽换绑素材时选择。不写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "asset_type": {
                        "type": "string",
                        "description": "可选。按素材类型过滤，例如 background / portrait / keyframe。",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "可选。最多返回的素材数量，默认 20。",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bind_chapter_resource_slot",
            "description": "把一个素材版本绑定（或换绑）到章节脚本的某个资源槽位。expected_lock_version 必须来自最近一次 get_chapter_resource_slots 的返回；绑定后若所有必需槽位就绪会自动排队 VNGraph 编译。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "script_revision_id": {"type": "string"},
                    "slot_id": {"type": "string"},
                    "asset_version_id": {"type": "string"},
                    "expected_lock_version": {
                        "type": "integer",
                        "description": "乐观锁版本号，来自最近一次 get_chapter_resource_slots。",
                    },
                },
                "required": [
                    "project_id",
                    "script_revision_id",
                    "slot_id",
                    "asset_version_id",
                    "expected_lock_version",
                ],
            },
        },
    },
]


def execute_chapter_tool(db: Session, name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if name == "get_single_chapter_outline":
        return get_single_chapter_outline_tool(db, arguments)
    if name == "get_chapter_content_status":
        return get_chapter_content_status(db, arguments)
    if name == "get_chapter_asset_prompts":
        return get_chapter_asset_prompts_tool(db, arguments)
    if name == "get_chapter_script":
        return get_chapter_script_tool(db, arguments)
    if name == "get_chapter_resource_slots":
        return get_chapter_resource_slots_tool(db, arguments)
    if name == "get_chapter_assets":
        return get_chapter_assets_tool(db, arguments)
    return None


async def execute_chapter_tool_async(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    sync_result = execute_chapter_tool(db, name, arguments)
    if sync_result is not None:
        return sync_result

    if name == "plan_single_chapter":
        return await plan_single_chapter_tool(db, arguments)
    if name == "approve_single_chapter_outline":
        return await approve_single_chapter_outline_tool(db, arguments)
    if name == "create_empty_chapter_workspace":
        return await create_empty_chapter_workspace_tool(db, arguments)
    if name == "run_chapter_workflow":
        return await run_chapter_workflow_tool(db, arguments)
    if name == "retry_chapter_resources":
        return await retry_chapter_resources_tool(db, arguments)
    if name == "generate_chapter_script":
        return generate_chapter_script_tool(db, arguments, tool_call_id=tool_call_id, agent=agent)
    if name == "bind_chapter_resource_slot":
        return await bind_chapter_resource_slot_tool(db, arguments, agent)
    return None


def get_single_chapter_outline_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error

    outline = ChapterOutlineWorkflowService(db).get_single_chapter_outline(project_id, chapter_index)
    if not outline:
        return {
            "ok": True,
            "exists": False,
            "project_id": project_id,
            "chapter_index": chapter_index,
            "committed": False,
        }
    return {
        "ok": True,
        "exists": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "outline": _outline_to_dict(outline),
        "committed": False,
    }


async def plan_single_chapter_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error

    events: List[Dict[str, Any]] = []

    async def emit(event: Dict[str, Any]) -> None:
        events.append(event)

    try:
        result = await ChapterOutlineWorkflowService(db, emit=emit).plan_single_chapter_outline(
            project_id=project_id,
            user_instruction=str(arguments.get("user_instruction") or ""),
            chapter_index=_read_optional_int(arguments.get("chapter_index")),
            reference_context=_read_optional_dict(arguments.get("reference_context")),
            overwrite=_parse_bool(arguments.get("overwrite", False)),
        )
    except ChapterOutlineWorkflowError as exc:
        return _error_result(exc, project_id=project_id, events=events)

    outline = result.outline
    return {
        "ok": True,
        "project_id": project_id,
        "chapter_index": outline.chapter_index,
        "outline": _outline_to_dict(outline),
        "created": result.created,
        "planning_notes": result.planning_notes,
        "continuity_check": result.continuity_check,
        "committed": True,
        "message": "已生成并写入待确认单章规划。",
        "_events": events,
    }


async def approve_single_chapter_outline_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error

    events: List[Dict[str, Any]] = []

    async def emit(event: Dict[str, Any]) -> None:
        events.append(event)

    try:
        outline = await ChapterOutlineWorkflowService(db, emit=emit).approve_single_chapter_outline(
            project_id=project_id,
            chapter_index=chapter_index,
        )
    except ChapterOutlineWorkflowError as exc:
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index, events=events)

    return {
        "ok": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "outline": _outline_to_dict(outline),
        "committed": True,
        "message": "单章规划已确认。",
        "_events": events,
    }


def get_chapter_content_status(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error

    head = (
        db.query(ChapterHead)
        .filter(ChapterHead.project_id == project_id, ChapterHead.chapter_index == chapter_index)
        .first()
    )
    revision = db.query(ChapterRevision).filter(ChapterRevision.id == head.current_revision_id).first() if head else None
    if not revision:
        return {
            "ok": True,
            "exists": False,
            "project_id": project_id,
            "chapter_index": chapter_index,
            "committed": False,
        }

    text = revision.content or ""
    return {
        "ok": True,
        "exists": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "chapter_revision_id": revision.id,
        "revision_no": revision.revision_no,
        "status": revision.status,
        "content_length": len(text),
        "content_preview": text[:300],
        "committed": True,
        "created_at": _datetime_to_text(revision.created_at),
        "updated_at": _datetime_to_text(head.updated_at) if head else None,
    }


async def create_empty_chapter_workspace_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error

    workflow_arguments = dict(arguments)
    workflow_arguments["create_workspace"] = True
    workflow_arguments["generate_content"] = False
    workflow_arguments["generate_asset_prompts"] = False
    workflow_arguments["generate_resources"] = False

    result = await run_chapter_workflow_tool(db, workflow_arguments)
    if not result.get("ok"):
        return result

    workspace = result.get("workspace") or {}
    return {
        "ok": True,
        "project_id": project_id,
        "chapter_index": workspace.get("chapter_index") or result.get("chapter_index"),
        "title": workspace.get("title") or str(arguments.get("title") or ""),
        "created": {
            "outline_id": workspace.get("outline_id"),
            "content_id": workspace.get("content_id"),
        },
        "content_status": workspace.get("content_status"),
        "steps": result.get("steps") or [],
        "committed": True,
        "message": "已通过正式章节流水线创建后端空章节工作区。",
        "_events": result.get("_events") or [],
    }


def get_chapter_asset_prompts_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error

    result = ChapterAssetPromptService(db).get_chapter_asset_prompts(project_id, chapter_index)
    return {
        "ok": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "character_prompt_count": len(result.character_prompts),
        "background_prompt_count": len(result.background_prompts),
        "keyframe_prompt_count": len(result.keyframe_prompts),
        "character_prompts": [_asset_to_dict(asset) for asset in result.character_prompts],
        "background_prompts": [_asset_to_dict(asset) for asset in result.background_prompts],
        "keyframe_prompts": [_asset_to_dict(asset) for asset in result.keyframe_prompts],
        "committed": False,
    }


async def run_chapter_workflow_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error

    create_workspace = _parse_bool(arguments.get("create_workspace", False))
    requested_chapter_index = _read_optional_int(arguments.get("chapter_index"))
    if create_workspace and requested_chapter_index is not None:
        # 幂等重试：目标章节工作区已存在时自动降级为继续生成，避免命中
        # "章节 N 已存在" 409（此前 Agent 需自行猜到改参数）。
        existing_outline = ChapterOutlineWorkflowService(db).get_single_chapter_outline(
            project_id, requested_chapter_index
        )
        if existing_outline is not None:
            create_workspace = False
    workflow_defaults_enabled = not create_workspace
    events: List[Dict[str, Any]] = []

    async def emit(event: Dict[str, Any]) -> None:
        events.append(event)

    try:
        result = await ChapterWorkflowService(db, emit=emit).run_chapter_workflow(
            ChapterWorkflowRequestData(
                project_id=project_id,
                chapter_index=_read_optional_int(arguments.get("chapter_index")),
                title=str(arguments.get("title") or ""),
                summary=str(arguments.get("summary") or ""),
                conflict=str(arguments.get("conflict") or ""),
                characters=_read_string_list(arguments.get("characters")),
                scene=str(arguments.get("scene") or ""),
                emotion=str(arguments.get("emotion") or ""),
                visual_keywords=_read_string_list(arguments.get("visual_keywords")),
                create_workspace=create_workspace,
                generate_content=_parse_bool(arguments.get("generate_content", workflow_defaults_enabled)),
                generate_asset_prompts=_parse_bool(arguments.get("generate_asset_prompts", workflow_defaults_enabled)),
                asset_prompt_genre=_read_optional_text(arguments.get("asset_prompt_genre")),
                generate_resources=_parse_bool(arguments.get("generate_resources", False)),
                resource_types=_read_string_list(arguments.get("resource_types")),
                background_moods=_read_optional_string_list(arguments.get("background_moods")),
                portrait_batch_size=_read_int(arguments.get("portrait_batch_size"), 5),
                portrait_auto_demand=_parse_bool(arguments.get("portrait_auto_demand", True)),
                word_count_min=_read_int(arguments.get("word_count_min"), 3000),
                word_count_max=_read_int(arguments.get("word_count_max"), 4500),
            )
        )
    except ChapterWorkflowError as exc:
        # 部分失败不丢已完成步骤：正文先提交、后续步骤失败时，把已落库的
        # 正文状态附给错误视图，避免 Agent 误以为正文也没生成而重复重跑。
        db.rollback()
        error_view = _error_result(exc, project_id=project_id, events=events)
        if requested_chapter_index is not None:
            db.expire_all()
            partial_content = (
                db.query(ChapterContent)
                .filter(
                    ChapterContent.project_id == project_id,
                    ChapterContent.chapter_index == requested_chapter_index,
                )
                .first()
            )
            if partial_content and (partial_content.content or "").strip():
                error_view["partial_progress"] = {
                    "chapter_content_exists": True,
                    "content_length": len(partial_content.content or ""),
                    "note": "正文已生成并落库，失败发生在后续步骤（素材 Prompt/资源）；重试时无需重复生成正文。",
                }
        return error_view

    result["committed"] = True
    result["message"] = "章节流水线已执行完成。"
    result["_events"] = events
    return _compact_chapter_workflow_result(result)


async def retry_chapter_resources_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error

    events: List[Dict[str, Any]] = []

    async def emit(event: Dict[str, Any]) -> None:
        events.append(event)

    try:
        resource_result = await ChapterResourceOrchestrator(db, emit=emit).generate_chapter_resources(
            ChapterResourceRequestData(
                project_id=project_id,
                chapter_index=chapter_index,
                script_revision_id=_read_optional_text(arguments.get("script_revision_id")),
                resource_types=_read_string_list(arguments.get("resource_types")),
                background_moods=_read_optional_string_list(arguments.get("background_moods")),
                portrait_batch_size=_read_int(arguments.get("portrait_batch_size"), 5),
                portrait_auto_demand=_parse_bool(arguments.get("portrait_auto_demand", True)),
            )
        )
    except ChapterResourceOrchestratorError as exc:
        db.rollback()
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index, events=events)
    except HTTPException as exc:
        db.rollback()
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index, events=events)

    # 渲染任务/槽位状态只在事务提交后才会被 outbox 派发与 worker 可见；
    # 不提交会导致任务永远停在 queued 并在会话结束时回滚。
    db.commit()
    failures = _build_resource_failure_summary(resource_result, project_id, chapter_index)
    response: Dict[str, Any] = {
        "ok": True,
        "status": EWorkflowStatus_partial_failed if failures else EWorkflowStatus_completed,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "only_failed": _parse_bool(arguments.get("only_failed", True)),
        "resources": _compact_resource_result(resource_result),
        "recoverable_failures": failures,
        "next_actions": [],
        "committed": True,
        "message": (
            "资源重试完成，但仍有可恢复资源失败。"
            if failures
            else "资源重试完成。"
        ),
        "_events": events,
    }
    if failures:
        response["next_actions"].append(
            _build_retry_resources_action(
                project_id,
                chapter_index,
                [item["type"] for item in failures],
            )
        )

    return response


def _read_project_chapter(arguments: Dict[str, Any]):
    try:
        project_id = int(arguments.get("project_id"))
        chapter_index = int(arguments.get("chapter_index"))
    except (TypeError, ValueError):
        return 0, 0, {"ok": False, "error": "project_id 和 chapter_index 必须是整数", "committed": False}
    return project_id, chapter_index, None


_SCRIPT_VIEW_PARAGRAPH_LIMIT = 12
_SCRIPT_VIEW_TEXT_CHARS = 120
_SLOT_VIEW_MAX = 60


def _read_agent_owner(agent: AgentToolHandle | None):
    owner = getattr(agent, "owner_id", None) if agent is not None else None
    owner_id = int(owner) if owner else 0
    if owner_id <= 0:
        return 0, {"ok": False, "error": "当前工具缺少 Agent 句柄", "committed": False}
    return owner_id, None


def generate_chapter_script_tool(
    db: Session,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error
    owner_id, error = _read_agent_owner(agent)
    if error:
        return error

    try:
        chapter_revision, error = _ensure_v2_chapter_ready(db, project_id, chapter_index, owner_id)
    except HTTPException as exc:
        # 桥接内部的激活/校验失败不能裸穿到 Agent 循环（会变成空错误信息）。
        db.rollback()
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index)
    except AppError as exc:
        # activate_path_chapter_revision_head 抛 AppError（非 HTTPException 子类），
        # 同样需要转成带 code/message 的工具错误视图。
        db.rollback()
        return {
            "ok": False,
            "error": str(getattr(exc, "message", "") or exc),
            "code": getattr(exc, "code", None),
            "status_code": getattr(exc, "status_code", 400),
            "project_id": project_id,
            "chapter_index": chapter_index,
            "committed": False,
        }
    if error:
        return error

    idempotency_key = f"agent:{getattr(agent, 'thread_id', '')}:{tool_call_id}:chapter_script:{chapter_index}"
    try:
        task, created = create_chapter_script_generation_task(
            db,
            user_id=owner_id,
            chapter_revision_id=chapter_revision.id,
            idempotency_key=idempotency_key,
            parameters={"instructions": _read_optional_text(arguments.get("instructions"))},
        )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index)

    return {
        "ok": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "chapter_revision_id": chapter_revision.id,
        "task_id": task.id,
        "task_status": task.status,
        "task_created": created,
        "committed": True,
        "message": (
            "章节脚本生成任务已创建。用 wait_for_task 观察任务；任务 succeeded 前不要声称脚本已生成。"
            if created
            else "已存在同内容的脚本生成任务，返回既有 task_id。"
        ),
    }


def get_chapter_script_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error
    script_revision_id = _read_optional_text(arguments.get("script_revision_id"))

    revision = _resolve_script_revision(db, project_id, chapter_index, script_revision_id)
    if revision is None:
        return {
            "ok": True,
            "exists": False,
            "project_id": project_id,
            "chapter_index": chapter_index,
            "hint": "该章节还没有章节脚本，可调用 generate_chapter_script 生成。",
            "committed": False,
        }
    return {
        "ok": True,
        "exists": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "script": _script_view(db, revision),
        "committed": False,
    }


def get_chapter_resource_slots_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, chapter_index, error = _read_project_chapter(arguments)
    if error:
        return error
    script_revision_id = _read_optional_text(arguments.get("script_revision_id"))

    revision = _resolve_script_revision(db, project_id, chapter_index, script_revision_id)
    if revision is None:
        return {
            "ok": True,
            "exists": False,
            "project_id": project_id,
            "chapter_index": chapter_index,
            "hint": "该章节还没有章节脚本，资源槽位随脚本生成时规划。",
            "committed": False,
        }
    try:
        slots = list_script_resource_slots(db, project_id=project_id, script_revision_id=revision.id)
    except HTTPException as exc:
        return _error_result(exc, project_id=project_id, chapter_index=chapter_index)

    media_urls = _media_url_map(db, [slot.asset_version_id for slot in slots if slot.asset_version_id])
    slot_views = [
        _slot_view(slot, media_urls.get(slot.asset_version_id))
        for slot in slots[:_SLOT_VIEW_MAX]
    ]
    counts: Dict[str, int] = {}
    for slot in slots:
        counts[slot.status] = counts.get(slot.status, 0) + 1
    return {
        "ok": True,
        "exists": True,
        "project_id": project_id,
        "chapter_index": chapter_index,
        "script_revision_id": revision.id,
        "slot_count": len(slots),
        "status_counts": counts,
        "slots": slot_views,
        "committed": False,
    }


def get_chapter_assets_tool(db: Session, arguments: Dict[str, Any]) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error
    asset_type = _read_optional_text(arguments.get("asset_type"))
    limit = min(_read_int(arguments.get("limit"), 20), 50)

    asset_query = db.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.archived_at.is_(None),
    )
    if asset_type:
        asset_query = asset_query.filter(Asset.asset_type == asset_type)
    assets = asset_query.order_by(Asset.id.desc()).limit(limit).all()

    asset_views: List[Dict[str, Any]] = []
    for asset in assets:
        versions = (
            db.query(AssetVersion, StorageObject)
            .outerjoin(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
            .filter(AssetVersion.asset_id == asset.id)
            .order_by(AssetVersion.version_no.desc())
            .limit(5)
            .all()
        )
        asset_views.append(
            {
                "asset_id": asset.id,
                "asset_type": asset.asset_type,
                "logical_key": asset.logical_key,
                "target_name": asset.target_name,
                "versions": [
                    {
                        "asset_version_id": version.id,
                        "version_no": version.version_no,
                        "cache_key": version.cache_key,
                        "media_url": (
                            f"/api/media/{storage.id}" if storage and storage.status == "active" else None
                        ),
                        "created_at": _datetime_to_text(version.created_at),
                    }
                    for version, storage in versions
                ],
            }
        )
    return {
        "ok": True,
        "project_id": project_id,
        "asset_count": len(asset_views),
        "assets": asset_views,
        "committed": False,
    }


async def bind_chapter_resource_slot_tool(
    db: Session,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    owner_id, error = _read_agent_owner(agent)
    if error:
        return error
    project_id, error = _read_project(arguments)
    if error:
        return error
    script_revision_id = _read_optional_text(arguments.get("script_revision_id"))
    slot_id = _read_optional_text(arguments.get("slot_id"))
    asset_version_id = _read_optional_text(arguments.get("asset_version_id"))
    expected_lock_version = _read_optional_int(arguments.get("expected_lock_version"))
    if not script_revision_id or not slot_id or not asset_version_id:
        return {
            "ok": False,
            "error": "script_revision_id、slot_id、asset_version_id 必填（可先用 get_chapter_resource_slots / get_chapter_assets 查询）",
            "committed": False,
        }
    if not expected_lock_version:
        return {
            "ok": False,
            "error": "expected_lock_version 必填，取自最近一次 get_chapter_resource_slots 的返回",
            "committed": False,
        }

    try:
        slot = bind_script_resource_slot(
            db,
            project_id=project_id,
            slot_id=slot_id,
            asset_version_id=asset_version_id,
            script_revision_id=script_revision_id,
            expected_lock_version=expected_lock_version,
        )
        db.commit()
    except HTTPException as exc:
        db.rollback()
        return _error_result(exc, project_id=project_id)

    vngraph_task = queue_vn_graph_when_resources_ready(
        db,
        user_id=owner_id,
        project_id=project_id,
        script_revision_id=script_revision_id,
    )
    db.commit()

    media_urls = _media_url_map(db, [slot.asset_version_id] if slot.asset_version_id else [])
    return {
        "ok": True,
        "project_id": project_id,
        "script_revision_id": script_revision_id,
        "slot": _slot_view(slot, media_urls.get(slot.asset_version_id)),
        "vngraph_task_id": vngraph_task.id if vngraph_task is not None else None,
        "committed": True,
        "message": "素材已绑定到槽位。",
    }


def _ensure_v2_chapter_ready(db: Session, project_id: int, chapter_index: int, user_id: int):
    """把 legacy 章节正文接入 v2 修订链，返回可生成脚本的 ChapterRevision。

    复用 AutoCreator `_step_bridge_v2` 的既有服务序列（bible_revision →
    StoryPath/outline_revision → chapter_revision），并做幂等复用：已存在的
    同 hash 修订直接沿用，不重复创建。不写任何正文，只做修订链桥接。
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None, {"ok": False, "error": "项目不存在", "status_code": 404, "committed": False}
    # 双存储回落：generate_story_bible 可能只写 v2 修订（不落 legacy 表），
    # 下游仅消费 raw_json。
    bible = effective_story_bible(db, project_id)
    if not bible:
        return None, {
            "ok": False,
            "error": "项目还没有故事设定，请先生成故事设定（generate_story_bible）",
            "status_code": 409,
            "committed": False,
        }
    content_row = (
        db.query(ChapterContent)
        .filter(
            ChapterContent.project_id == project_id,
            ChapterContent.chapter_index == chapter_index,
        )
        .order_by(ChapterContent.id.desc())
        .first()
    )
    if not content_row or not (content_row.content or "").strip():
        return None, {
            "ok": False,
            "error": "章节正文不存在，请先执行 run_chapter_workflow 生成正文",
            "status_code": 409,
            "committed": False,
        }

    # 复用检查：该章节已有 v2 正文修订且已有可用脚本修订时无需再桥接。
    existing_chapter_revision = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.project_id == project_id,
            ChapterRevision.chapter_index == chapter_index,
        )
        .order_by(ChapterRevision.revision_no.desc())
        .first()
    )
    existing_script_revision = _resolve_script_revision(db, project_id, chapter_index, None)
    if existing_chapter_revision is not None and existing_script_revision is not None:
        return existing_chapter_revision, None

    # 圣经修订优先取内容 head 当前指向的版本：激活大纲时要求
    # head.current_bible_revision_id 与大纲引用一致，只取"最新"会与
    # head 错位（真实踩坑：发布流程切换 head 后桥接 409）。
    content_head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .first()
    )
    head_bible_revision_id = (
        content_head.current_bible_revision_id if content_head is not None else None
    )
    bible_revision = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == head_bible_revision_id,
            StoryBibleRevision.project_id == project_id,
        )
        .first()
        if head_bible_revision_id
        else None
    )
    if bible_revision is None:
        bible_revision = create_bible_revision(
            db,
            project_id=project_id,
            content=bible.raw_json,
            source=build_bible_generation_source(project, parent_revision_id=None),
            user_id=user_id,
        )
        db.commit()

    path = (
        db.query(StoryPath)
        .filter(StoryPath.project_id == project_id, StoryPath.parent_path_id.is_(None))
        .one_or_none()
    )
    if path is None:
        path = StoryPath(project_id=project_id, title="Main", status="active", lock_version=1)
        db.add(path)
        db.flush()
        db.add(StoryPathOutlineHead(story_path_id=path.id, lock_version=1))
        db.commit()

    placement = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == path.id,
            StoryPathChapter.display_index == chapter_index,
            StoryPathChapter.status == "active",
        )
        .first()
    )
    if placement is None:
        outline_rows = (
            db.query(ChapterOutline)
            .filter(ChapterOutline.project_id == project_id)
            .order_by(ChapterOutline.chapter_index)
            .all()
        )
        # 复用既有 active 挂载点：payload 带上 story_path_chapter_id，激活时
        # 原挂载保持绑定（不脱离、保留 current_revision_id），只为新章节增量
        # 建挂载点。不带 id 的话激活会把全部旧挂载判为 removed 整套重建，
        # 造成 display_index 重复的孤儿挂载二次方累积（实测已发生）。
        active_placement_ids: Dict[int, str] = {
            item.display_index: item.id
            for item in db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.status == "active",
            )
            .all()
        }
        outline_payload: List[Dict[str, Any]] = []
        for row in outline_rows:
            item_payload: Dict[str, Any] = {
                "title": row.title,
                "summary": row.summary,
                "conflict": row.conflict,
                "characters": row.characters,
                "scene": row.scene,
                "emotion": row.emotion,
                "visual_keywords": row.visual_keywords,
                "display_index": row.chapter_index,
            }
            bound_id = active_placement_ids.get(row.chapter_index)
            if bound_id:
                item_payload["story_path_chapter_id"] = bound_id
            outline_payload.append(item_payload)
        if not any(item["display_index"] == chapter_index for item in outline_payload):
            return None, {
                "ok": False,
                "error": f"章节 {chapter_index} 缺少单章规划，请先 plan_single_chapter",
                "status_code": 409,
                "committed": False,
            }
        outline_revision = create_story_path_outline_revision(
            db,
            story_path_id=path.id,
            bible_revision_id=bible_revision.id,
            chapters=outline_payload,
            user_id=user_id,
        )
        db.commit()
        activate_story_path_outline_revision(
            db, story_path_id=path.id, revision_id=outline_revision.id
        )
        db.commit()

    # 逐章补建前驱链：context resolver 要求 ≤目标章节的每个挂载点都有
    # current_revision_id，缺任何一个，脚本前置校验都会 409
    # "predecessor 尚未选择 Chapter revision"。只遍历 active 挂载，
    # 避免历史孤儿（detached）上的旧修订干扰锚点选择。
    placements = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == path.id,
            StoryPathChapter.status == "active",
            StoryPathChapter.display_index <= chapter_index,
        )
        .order_by(StoryPathChapter.display_index, StoryPathChapter.created_at)
        .all()
    )
    chapter_revision = None
    for p in placements:
        if p.current_revision_id:
            revision = (
                db.query(ChapterRevision)
                .filter(ChapterRevision.id == p.current_revision_id)
                .first()
            )
        else:
            p_content = (
                db.query(ChapterContent)
                .filter(
                    ChapterContent.project_id == project_id,
                    ChapterContent.chapter_index == p.display_index,
                )
                .order_by(ChapterContent.id.desc())
                .first()
            )
            if not p_content or not (p_content.content or "").strip():
                hint = (
                    "请先执行 run_chapter_workflow 生成正文"
                    if p.display_index == chapter_index
                    else "请先生成该前驱章节的正文，否则无法生成后续章节脚本"
                )
                return None, {
                    "ok": False,
                    "error": f"章节 {p.display_index} 正文不存在：{hint}",
                    "status_code": 409,
                    "committed": False,
                }
            revision = create_manual_story_path_chapter_revision(
                db,
                path_chapter_id=p.id,
                parent_revision_id=None,
                content=p_content.content,
                user_id=user_id,
            )
            db.commit()
            db.refresh(p)
            activate_path_chapter_revision_head(
                db,
                path_chapter_id=p.id,
                revision_id=revision.id,
                expected_lock_version=p.lock_version,
            )
            db.commit()
        if p.display_index == chapter_index:
            chapter_revision = revision
    if chapter_revision is None:
        return None, {
            "ok": False,
            "error": f"未找到章节 {chapter_index} 的剧情路径挂载点",
            "status_code": 409,
            "committed": False,
        }
    return chapter_revision, None


def _resolve_script_revision(
    db: Session,
    project_id: int,
    chapter_index: int,
    script_revision_id: Optional[str],
) -> Optional[ChapterScriptRevision]:
    """脚本修订三级解析：显式 id → head 当前修订 → 最新 ready 修订。"""
    if script_revision_id:
        return (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == script_revision_id,
                ChapterScriptRevision.project_id == project_id,
            )
            .first()
        )
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
    return (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.project_id == project_id,
            ChapterScriptRevision.chapter_index == chapter_index,
        )
        .order_by(ChapterScriptRevision.revision_no.desc(), ChapterScriptRevision.created_at.desc())
        .first()
    )


def _script_view(db: Session, revision: ChapterScriptRevision) -> Dict[str, Any]:
    script = revision.script_json if isinstance(revision.script_json, dict) else {}
    characters = [
        {"character_id": item.get("character_id"), "name": item.get("name")}
        for item in (script.get("characters") or [])
        if isinstance(item, dict)
    ]
    scenes = [
        {"scene_id": item.get("scene_id"), "title": item.get("title"), "location": item.get("location")}
        for item in (script.get("scenes") or [])
        if isinstance(item, dict)
    ]
    paragraphs = [
        item
        for item in (script.get("paragraphs") or [])
        if isinstance(item, dict)
    ]
    paragraph_views: List[Dict[str, Any]] = []
    for item in paragraphs[:_SCRIPT_VIEW_PARAGRAPH_LIMIT]:
        text = str(item.get("text") or "")
        paragraph_views.append(
            {
                "paragraph_id": item.get("paragraph_id"),
                "kind": item.get("kind"),
                "scene_id": item.get("scene_id"),
                "speaker_name": item.get("speaker_display_name") or item.get("speaker_name"),
                "emotion": item.get("emotion"),
                "keyframe_required": item.get("keyframe_required"),
                "text": text[:_SCRIPT_VIEW_TEXT_CHARS] + ("...[已截断]" if len(text) > _SCRIPT_VIEW_TEXT_CHARS else ""),
            }
        )
    slot_counts: Dict[str, int] = {}
    slot_rows = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == revision.id)
        .all()
    )
    for slot in slot_rows:
        slot_counts[slot.status] = slot_counts.get(slot.status, 0) + 1
    coverage = revision.coverage_json if isinstance(revision.coverage_json, dict) else {}
    return {
        "script_revision_id": revision.id,
        "chapter_revision_id": revision.chapter_revision_id,
        "revision_no": revision.revision_no,
        "schema_version": revision.schema_version,
        "generator_version": revision.generator_version,
        "status": revision.status,
        "created_at": _datetime_to_text(revision.created_at),
        "character_count": len(characters),
        "characters": characters,
        "scene_count": len(scenes),
        "scenes": scenes,
        "paragraph_count": len(paragraphs),
        "keyframe_required_count": sum(1 for item in paragraphs if item.get("keyframe_required")),
        "paragraphs_preview": paragraph_views,
        "resource_slot_counts": slot_counts,
        "coverage": {
            key: coverage.get(key)
            for key in ("mode", "coverage_ratio", "paragraph_count", "span_count")
            if key in coverage
        },
    }


def _media_url_map(db: Session, asset_version_ids: List[str]) -> Dict[str, Optional[str]]:
    if not asset_version_ids:
        return {}
    rows = (
        db.query(AssetVersion.id, StorageObject.id, StorageObject.status)
        .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(AssetVersion.id.in_(asset_version_ids))
        .all()
    )
    return {
        version_id: (f"/api/media/{storage_id}" if storage_status == "active" else None)
        for version_id, storage_id, storage_status in rows
    }


def _slot_view(slot: ScriptResourceSlot, media_url: Optional[str]) -> Dict[str, Any]:
    return {
        "slot_id": slot.id,
        "slot_key": slot.slot_key,
        "role": slot.role,
        "scene_id": slot.scene_id,
        "paragraph_id": slot.paragraph_id,
        "character_id": slot.character_id,
        "status": slot.status,
        "required": slot.required,
        "asset_id": slot.asset_id,
        "asset_version_id": slot.asset_version_id,
        "media_url": media_url,
        "generation_task_id": slot.generation_task_id,
        "lock_version": slot.lock_version,
    }


def _read_project(arguments: Dict[str, Any]):
    try:
        project_id = int(arguments.get("project_id"))
    except (TypeError, ValueError):
        return 0, {"ok": False, "error": "project_id 必须是整数", "committed": False}
    return project_id, None


def _read_optional_int(value: Any) -> Optional[int]:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _read_optional_dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _read_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _read_optional_string_list(value: Any) -> Optional[List[str]]:
    if value is None:
        return None
    result = _read_string_list(value)
    return result or None


def _read_int(value: Any, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _read_optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _compact_chapter_workflow_result(result: Dict[str, Any]) -> Dict[str, Any]:
    compact = dict(result)
    if isinstance(compact.get("resources"), dict):
        compact["resources"] = _compact_resource_result(compact["resources"])
    if "recoverable_failures" not in compact:
        compact["recoverable_failures"] = []
    if "next_actions" not in compact:
        compact["next_actions"] = []
    if "status" not in compact:
        compact["status"] = (
            EWorkflowStatus_partial_failed
            if compact.get("recoverable_failures")
            else EWorkflowStatus_completed
        )
    return compact


def _compact_resource_result(resource_result: Dict[str, Any]) -> Dict[str, Any]:
    compact_results: Dict[str, Any] = {}
    for resource_type, detail in (resource_result.get("results") or {}).items():
        if not isinstance(detail, dict):
            continue
        compact_results[resource_type] = {
            "total": int(detail.get("total") or 0),
            "generated": int(detail.get("generated") or 0),
            "failed": int(detail.get("failed") or 0),
            "error": detail.get("error"),
            "first_error": _first_resource_error(detail),
        }
    return {
        "resource_types": resource_result.get("resource_types") or [],
        "total": int(resource_result.get("total") or 0),
        "generated": int(resource_result.get("generated") or 0),
        "failed": int(resource_result.get("failed") or 0),
        "results": compact_results,
    }


def _build_resource_failure_summary(
    resource_result: Dict[str, Any],
    project_id: int,
    chapter_index: int,
) -> List[Dict[str, Any]]:
    failures: List[Dict[str, Any]] = []
    for resource_type, detail in (resource_result.get("results") or {}).items():
        if not isinstance(detail, dict):
            continue
        failed = int(detail.get("failed") or 0)
        if failed <= 0:
            continue
        failures.append(
            {
                "type": resource_type,
                "project_id": project_id,
                "chapter_index": chapter_index,
                "total": int(detail.get("total") or 0),
                "generated": int(detail.get("generated") or 0),
                "failed": failed,
                "reason": _first_resource_error(detail),
                "retryable": True,
            }
        )
    return failures


def _first_resource_error(detail: Dict[str, Any]) -> str:
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
    project_id: int,
    chapter_index: int,
    resource_types: List[str],
) -> Dict[str, Any]:
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


def _outline_to_dict(outline) -> Dict[str, Any]:
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
        "created_at": _datetime_to_text(outline.created_at),
        "updated_at": _datetime_to_text(outline.updated_at),
    }


def _asset_to_dict(asset) -> Dict[str, Any]:
    return {
        "id": asset.id,
        "asset_type": asset.asset_type,
        "target_name": asset.target_name,
        "prompt": asset.prompt,
        "image_url": asset.image_url,
        "audio_url": getattr(asset, "audio_url", None),
        "status": asset.status,
        "character_id": asset.character_id,
        "emotion": asset.emotion,
        "chapter_index": asset.chapter_index,
        "created_at": _datetime_to_text(asset.created_at),
        "updated_at": _datetime_to_text(asset.updated_at),
    }


def _datetime_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _error_result(
    exc,
    project_id: int,
    chapter_index: Optional[int] = None,
    events: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    # HTTPException 的 str() 是空串，必须取 detail；业务异常优先用 .message。
    message = getattr(exc, "message", None)
    detail = getattr(exc, "detail", None)
    if message:
        error_text = str(message)
    elif detail is not None:
        error_text = str(detail)
    else:
        error_text = str(exc) or "工具执行失败"
    status_code = getattr(exc, "status_code", None) or 400
    result = {
        "ok": False,
        "error": error_text,
        "status_code": status_code,
        "project_id": project_id,
        "committed": False,
    }
    if chapter_index is not None:
        result["chapter_index"] = chapter_index
    if events is not None:
        result["_events"] = events
    return result
