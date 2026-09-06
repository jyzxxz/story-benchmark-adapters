"""项目固化发布工具。

把 finalize-publish 的"锚点收集 + 原子激活 + 发布成书"封装为 Agent 的
用户级业务能力：用户说"发布/成书"时，工具自动从当前修订链收集锚点，
不要求模型提供任何版本编号。
"""
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent.agent_spec import AgentToolHandle
from app.application.draft_publication_service import (
    collect_current_anchors,
    finalize_and_publish,
)
from app.core.errors import AppError


PUBLISH_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "publish_project",
            "description": "把项目当前的全部创作产物（设定、大纲、各章正文、脚本、VN图）原子激活并发布成一个新的公开版本（Release）。发布是公开可见动作，必须在用户明确要求发布/成书后才能调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer"},
                    "release_notes": {
                        "type": "string",
                        "description": "可选。本次发布的版本说明。",
                    },
                },
                "required": ["project_id"],
            },
        },
    },
]

_KNOWN_BLOCKING_HINTS = {
    "script.resource_unbound": "存在未绑定图片资源的章节脚本（先渲染资源）",
    "chapter.bible_mismatch": "有章节与当前设定版本不一致（需要重做该章下游）",
    "chapter_revision.provisional_ancestors_unreviewed": "前驱章节尚未审校发布",
    "release.no_changes": "没有可发布的新变更",
    "release.content_already_released": "当前内容与最近一个发布版本完全相同",
}


async def execute_publish_tool_async(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    if name != "publish_project":
        return None
    if agent is None:
        return {"ok": False, "error": "当前工具缺少 Agent 句柄"}
    return publish_project_tool(db, arguments, tool_call_id=tool_call_id, agent=agent)


def publish_project_tool(
    db: Session,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Dict[str, Any]:
    owner_id = getattr(agent, "owner_id", None) if agent is not None else None
    owner_id = int(owner_id) if owner_id else 0
    if owner_id <= 0:
        return {"ok": False, "error": "当前工具缺少 Agent 句柄", "committed": False}
    try:
        project_id = int(arguments.get("project_id"))
    except (TypeError, ValueError):
        return {"ok": False, "error": "project_id 必须是整数", "committed": False}
    release_notes = str(arguments.get("release_notes") or "").strip() or None

    anchors = _collect_anchors(db, project_id=project_id)

    idempotency_key = f"agent:{getattr(agent, 'thread_id', '')}:{tool_call_id}:finalize_publish"
    try:
        result = finalize_and_publish(
            db,
            project_id=project_id,
            user_id=owner_id,
            idempotency_key=idempotency_key,
            bible_revision_id=anchors["bible_revision_id"],
            outline_revision_ids=anchors["outline_revision_ids"],
            chapter_revision_ids=anchors["chapter_revision_ids"],
            script_revision_ids=anchors["script_revision_ids"],
            graph_revision_ids=anchors["graph_revision_ids"],
            release_notes=release_notes,
        )
        db.commit()
    except AppError as exc:
        db.rollback()
        return _app_error_result(exc, project_id=project_id)
    except HTTPException as exc:
        db.rollback()
        detail = getattr(exc, "detail", None)
        return {
            "ok": False,
            "error": str(detail) if detail is not None else "发布失败",
            "status_code": exc.status_code,
            "project_id": project_id,
            "committed": False,
        }

    release = result.release
    return {
        "ok": True,
        "project_id": project_id,
        "release": {
            "release_id": release.id,
            "version": release.version,
            "status": release.status,
            "published_at": release.published_at.isoformat() if release.published_at else None,
            "release_notes": release.release_notes,
        },
        "anchors": anchors,
        "committed": True,
        "message": (
            f"已发布为公开版本 v{release.version}（Release {release.id}）。"
        ),
    }


def _collect_anchors(db: Session, *, project_id: int) -> Dict[str, Any]:
    """从当前修订链收集固化锚点：与 finalize-publish 空 anchors 回退共用同一实现。"""
    return collect_current_anchors(db, project_id=project_id)


def _app_error_result(exc: AppError, *, project_id: int) -> Dict[str, Any]:
    details = exc.details if isinstance(exc.details, dict) else {}
    result: Dict[str, Any] = {
        "ok": False,
        "error": str(getattr(exc, "message", "") or exc),
        "code": getattr(exc, "code", None),
        "status_code": getattr(exc, "status_code", 422),
        "project_id": project_id,
        "committed": False,
    }
    blocking = details.get("blocking_items")
    if blocking:
        result["blocking_items"] = [
            _translate_blocking_item(item) for item in blocking if isinstance(item, dict)
        ]
        result["hint"] = (
            "发布被阻塞。请把 blocking_items 逐条转述给用户，"
            "并建议先完成对应内容再重新发布。"
        )
    return result


def _translate_blocking_item(item: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(item)
    code = str(view.get("code") or view.get("reason") or "")
    view["hint"] = _KNOWN_BLOCKING_HINTS.get(code, view.get("reason") or code)
    return view
