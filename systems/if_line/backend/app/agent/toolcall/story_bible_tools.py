"""StoryBible 版本化生成工具。"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent.agent_spec import AgentToolHandle
from app.application.authoring_resource_service import require_owned_project
from app.application.story_bible_service import (
    activate_bible_revision_head,
    build_bible_generation_source,
    create_bible_revision,
    get_or_create_content_head,
)
from app.application.task_service import create_generation_task
from app.core.errors import AppError
from app.models_v2 import ProjectContentHead, StoryBibleRevision


STORY_BIBLE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_story_bible",
            "description": "读取项目当前已经激活的 StoryBible 版本。不会返回仍在审核、尚未激活的生成版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_story_bible",
            "description": "创建异步 StoryBible 生成任务。只返回 task_id，不直接返回生成内容；随后必须调用 get_task_status 或 wait_for_task 观察任务。任务成功后产生待审核版本，不会自动激活当前 StoryBible。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID。"},
                    "instructions": {
                        "type": "string",
                        "description": "可选。本次生成的附加自然语言要求，例如强化人物冲突或保留某种创作约束。",
                    },
                    "parameters": {
                        "type": "object",
                        "description": "可选。透传给生成任务的扩展参数；普通调用不需要填写。",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_story_bible",
            "description": "修改项目当前创作设定。必须先调用 get_story_bible 读取当前完整内容和 revision_id；本工具会创建子版本并立即设为当前版本，不会覆盖或删除历史版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "要修改创作设定的项目 ID。",
                    },
                    "expected_revision_id": {
                        "type": "string",
                        "description": "get_story_bible 返回的当前 revision_id，用于防止基于旧版本覆盖新修改。",
                    },
                    "content": {
                        "type": "object",
                        "description": "修改后的完整 StoryBible 内容。必须保留未修改字段，不能只传本次变化字段。",
                    },
                },
                "required": ["project_id", "expected_revision_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "activate_story_bible",
            "description": "将项目中一个已经存在的 StoryBible revision 设为当前创作设定。适用于审核并采用异步生成结果，或切换到已有版本；不会创建新版本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "integer",
                        "description": "目标项目 ID。",
                    },
                    "revision_id": {
                        "type": "string",
                        "description": "要激活的 StoryBible revision ID，通常来自 StoryBible 生成任务的结果。",
                    },
                },
                "required": ["project_id", "revision_id"],
            },
        },
    },
]


def execute_story_bible_tool(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    if name == "get_story_bible":
        return get_story_bible_tool(db, arguments, agent=agent)
    if name == "update_story_bible":
        return update_story_bible_tool(db, arguments, agent=agent)
    if name == "activate_story_bible":
        return activate_story_bible_tool(db, arguments, agent=agent)
    return None


async def execute_story_bible_tool_async(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    sync_result = execute_story_bible_tool(db, name, arguments, agent=agent)
    if sync_result is not None:
        return sync_result

    if name == "generate_story_bible":
        return generate_story_bible_tool(
            db,
            arguments,
            tool_call_id=tool_call_id,
            agent=agent,
        )
    return None


def get_story_bible_tool(
    db: Session,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error
    try:
        require_owned_project(db, project_id=project_id, user_id=_require_owner_id(agent))
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}

    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    bible = None
    if head and head.current_bible_revision_id:
        bible = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == head.current_bible_revision_id,
                StoryBibleRevision.project_id == project_id,
            )
            .first()
        )
    if bible is None:
        return {
            "ok": True,
            "exists": False,
            "project_id": project_id,
            "committed": False,
        }

    return {
        "ok": True,
        "exists": True,
        "project_id": project_id,
        "story_bible": _story_bible_revision_to_dict(bible),
        "committed": False,
    }


def generate_story_bible_tool(
    db: Session,
    arguments: Dict[str, Any],
    *,
    tool_call_id: str,
    agent: AgentToolHandle | None,
) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error
    try:
        owner_id = _require_owner_id(agent)
        project = require_owned_project(db, project_id=project_id, user_id=owner_id)
        head = get_or_create_content_head(db, project_id)
        raw_parameters = arguments.get("parameters") or {}
        if not isinstance(raw_parameters, dict):
            return {"ok": False, "error": "parameters 必须是对象", "committed": False}
        parameters = deepcopy(raw_parameters)
        instructions = str(arguments.get("instructions") or "").strip()
        if instructions:
            parameters["instructions"] = instructions
        idempotency_key = f"agent:{getattr(agent, 'thread_id', '')}:{tool_call_id}:bible.generate"
        task, created = create_generation_task(
            db,
            user_id=owner_id,
            project_id=project.id,
            kind="bible.generate",
            idempotency_key=idempotency_key,
            source_refs=build_bible_generation_source(
                project,
                parent_revision_id=head.current_bible_revision_id,
            ),
            parameters=parameters,
            estimated_cost=Decimal("3"),
            idempotency_request={"parameters": parameters},
        )
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}

    return {
        "ok": True,
        "project_id": project_id,
        "task_id": task.id,
        "status": task.status,
        "created": created,
        "events_url": f"/api/tasks/{task.id}/events",
        "committed": True,
        "message": "StoryBible 生成任务已创建，请继续观察任务状态。",
    }


def update_story_bible_tool(
    db: Session,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error
    expected_revision_id = str(arguments.get("expected_revision_id") or "").strip()
    if not expected_revision_id:
        return {"ok": False, "error": "expected_revision_id 不能为空", "committed": False}
    content = arguments.get("content")
    if not isinstance(content, dict):
        return {"ok": False, "error": "content 必须是完整的 StoryBible 对象", "committed": False}

    try:
        owner_id = _require_owner_id(agent)
        require_owned_project(db, project_id=project_id, user_id=owner_id)
        head = get_or_create_content_head(db, project_id)
        if not head.current_bible_revision_id:
            return {
                "ok": False,
                "error": "项目尚无当前 StoryBible，请先生成并激活一个版本",
                "committed": False,
            }
        if head.current_bible_revision_id != expected_revision_id:
            return {
                "ok": False,
                "error": "StoryBible 当前版本已变化，请重新读取后再修改",
                "error_code": "story_bible.version_conflict",
                "current_revision_id": head.current_bible_revision_id,
                "committed": False,
            }
        current = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == expected_revision_id,
                StoryBibleRevision.project_id == project_id,
            )
            .one_or_none()
        )
        if current is None:
            return {"ok": False, "error": "当前 StoryBible 版本不存在", "committed": False}
        if current.content_json == content:
            return {
                "ok": True,
                "operation": "unchanged",
                "project_id": project_id,
                "story_bible": _story_bible_revision_to_dict(current),
                "committed": False,
            }

        revision = create_bible_revision(
            db,
            project_id=project_id,
            content=deepcopy(content),
            source={
                "origin": "agent_edit",
                "parent_revision_id": current.id,
                "thread_id": getattr(agent, "thread_id", None),
            },
            user_id=owner_id,
            activate=True,
            parent_revision_id=current.id,
        )
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}

    return {
        "ok": True,
        "operation": "updated",
        "project_id": project_id,
        "previous_revision_id": current.id,
        "story_bible": _story_bible_revision_to_dict(revision),
        "committed": True,
    }


def activate_story_bible_tool(
    db: Session,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    project_id, error = _read_project(arguments)
    if error:
        return error
    revision_id = str(arguments.get("revision_id") or "").strip()
    if not revision_id:
        return {"ok": False, "error": "revision_id 不能为空", "committed": False}

    try:
        owner_id = _require_owner_id(agent)
        require_owned_project(db, project_id=project_id, user_id=owner_id)
        head = get_or_create_content_head(db, project_id)
        previous_revision_id = head.current_bible_revision_id
        activation = activate_bible_revision_head(
            db,
            project_id=project_id,
            revision_id=revision_id,
            expected_lock_version=head.lock_version,
        )
    except HTTPException as exc:
        return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}
    except AppError as exc:
        return {
            "ok": False,
            "error": exc.message,
            "error_code": exc.code,
            "status_code": exc.status_code,
            "details": exc.details,
            "committed": False,
        }

    return {
        "ok": True,
        "operation": "unchanged" if previous_revision_id == revision_id else "activated",
        "project_id": project_id,
        "previous_revision_id": previous_revision_id,
        "story_bible": _story_bible_revision_to_dict(activation.revision),
        "head": activation.head.as_dict(),
        "committed": previous_revision_id != revision_id,
    }


def _read_project(arguments: Dict[str, Any]):
    try:
        project_id = int(arguments.get("project_id"))
    except (TypeError, ValueError):
        return 0, {"ok": False, "error": "project_id 必须是整数", "committed": False}
    return project_id, None


def _story_bible_revision_to_dict(bible: StoryBibleRevision) -> Dict[str, Any]:
    return {
        "revision_id": bible.id,
        "project_id": bible.project_id,
        "parent_revision_id": bible.parent_revision_id,
        "revision_no": bible.revision_no,
        "status": bible.status,
        "content_hash": bible.content_hash,
        "content": bible.content_json,
        "created_at": _datetime_to_text(bible.created_at),
    }


def _require_owner_id(agent: AgentToolHandle | None) -> int:
    owner_id = getattr(agent, "owner_id", None)
    if owner_id is None:
        raise HTTPException(status_code=401, detail="缺少当前用户上下文")
    return int(owner_id)


def _datetime_to_text(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None
