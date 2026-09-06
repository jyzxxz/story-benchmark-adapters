"""VNGraph 前端协作工具。

这组工具不直接修改 VNGraph。工具先调用后端素材动作服务创建 patch，
再把 patch 交给前端预览；前端回传完整 graph_json 后，后端重新重放
patch 并校验 VNGraph 派生产物，最后才写入新的 VNGraph revision。
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent.agent_spec import AgentToolHandle
from app.application.visual_asset_service import (
    MATCHER_VERSION,
    asset_action_dict,
    confirm_asset_action,
    create_asset_action,
    get_owned_asset_action,
)
from app.core.config import get_settings


VNGRAPH_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "request_frontend_vngraph_draft",
            "description": "请求前端上报当前本地 VNGraph 草稿。正式参数协议后续重新设计。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID。"},
                    "chapter_index": {"type": "integer", "description": "章节序号。"},
                    "payload": {"type": "object", "description": "临时透传给前端的参数。"},
                },
                "required": ["project_id", "chapter_index"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_vngraph_patch",
            "description": "创建视觉素材动作并生成 VNGraph patch，然后请求前端预览应用。调用后必须用同一个 tool_call_id 调用 get_frontend_tool_result 读取前端结果；如果结果是 pending，只能告诉用户前端仍在处理。不会直接写入 VNGraph，只有用户明确确认保存后才能调用 confirm_vngraph_patch。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID。"},
                    "mode": {
                        "type": "string",
                        "enum": ["library_select", "upload", "direct_generate"],
                        "description": "素材来源。library_select 选择公共素材，upload 使用项目已有素材版本，direct_generate 发起生图任务。",
                        "default": "library_select",
                    },
                    "target": {
                        "type": "object",
                        "description": "目标 VNGraph 节点。最简写法是 vn_graph_revision_id + node_index 或 node_key，后端会推断素材槽位。",
                    },
                    "input": {
                        "type": "object",
                        "description": "素材来源参数。library_select 可传 library_asset_id 或 instruction；upload 传 asset_version_id；direct_generate 传 instruction。",
                    },
                    "catalog_version": {"type": "string", "description": "公共素材库版本，可不传。"},
                    "matcher_version": {"type": "string", "description": "公共素材匹配版本，可不传。"},
                    "idempotency_key": {"type": "string", "description": "幂等 key，可不传，工具会基于 tool_call_id 生成。"},
                    "frontend_message": {"type": "string", "description": "展示给前端的补充说明。"},
                },
                "required": ["project_id", "target", "input"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_vngraph_patch",
            "description": "确认前端已经预览应用且用户明确同意保存的 VNGraph patch。调用本工具前，必须先在当前保存流程里调用 get_frontend_tool_result 并确认 status=completed。后端会重放 patch、校验 graph_json/hash，并写入新的 VNGraph revision。通常不要手填 graph_json；如果前端已经上报结果，传 apply_vngraph_patch 的 tool_call_id 到 frontend_tool_call_id。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "integer", "description": "项目 ID。"},
                    "action_id": {"type": "string", "description": "apply_vngraph_patch 创建的素材动作 ID。"},
                    "graph_json": {"type": "object", "description": "前端应用 patch 后的完整 VNGraph JSON。"},
                    "result_graph_hash": {"type": "string", "description": "graph_json 的内容 hash。"},
                    "frontend_tool_call_id": {
                        "type": "string",
                        "description": "如果 graph_json 已经通过前端工具结果接口提交，可以传该 tool_call_id 自动读取结果。",
                    },
                },
                "required": ["project_id", "action_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_frontend_tool_result",
            "description": "读取前端对某次 tool_call 的上报结果。apply_vngraph_patch 或 request_frontend_vngraph_draft 返回后必须主动调用本工具；默认会短暂等待前端上报。status=pending 表示前端还没上报，此时最多再重试 2 次；status=completed 表示可以继续分析结果或在用户确认后调用确认工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_call_id": {"type": "string", "description": "前一次前端工具调用返回的 tool_call_id。"},
                    "timeout_seconds": {
                        "type": "number",
                        "description": "等待前端上报的秒数。默认 5 秒，最大 30 秒；如果只想立即查询可传 0。",
                        "default": 5,
                    },
                },
                "required": ["tool_call_id"],
            },
        },
    },
]


async def execute_vngraph_tool(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    tool_call_id: str = "",
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    if name not in {
        "request_frontend_vngraph_draft",
        "apply_vngraph_patch",
        "confirm_vngraph_patch",
        "get_frontend_tool_result",
    }:
        return None
    if agent is None:
        return {"ok": False, "error": "当前工具缺少 Agent 句柄"}
    if name == "get_frontend_tool_result":
        return await agent.wait_frontend_tool_result(
            str(arguments.get("tool_call_id") or ""),
            timeout_seconds=_parse_wait_timeout(arguments.get("timeout_seconds")),
        )
    try:
        if name == "apply_vngraph_patch":
            return apply_vngraph_patch_tool(db, arguments, tool_call_id, agent)
        if name == "confirm_vngraph_patch":
            return confirm_vngraph_patch_tool(db, arguments, agent)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    request = build_vngraph_tool_input_request(name, arguments)
    if request is not None:
        return agent.request_frontend_tool_input(tool_call_id, name, arguments, request)
    return None


def build_vngraph_tool_input_request(name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if name == "request_frontend_vngraph_draft":
        return {
            "type": "vngraph_draft.requested",
            "name": name,
            "arguments": arguments,
            "message": "等待前端上报当前 VNGraph 本地草稿",
        }
    if name == "apply_vngraph_patch":
        return {
            "type": "vngraph_apply.requested",
            "name": name,
            "arguments": arguments,
            "message": "等待前端处理 VNGraph apply 请求",
        }
    return None


def apply_vngraph_patch_tool(
    db: Session,
    arguments: Dict[str, Any],
    tool_call_id: str,
    agent: AgentToolHandle,
) -> Dict[str, Any]:
    user_id = _require_agent_owner(agent)
    project_id = _parse_int(arguments.get("project_id"), "project_id")
    mode = str(arguments.get("mode") or "library_select").strip()
    if mode not in {"library_select", "upload", "direct_generate"}:
        return {"ok": False, "error": "mode 必须是 library_select / upload / direct_generate"}
    target = _require_dict(arguments.get("target"), "target")
    input_data = _require_dict(arguments.get("input"), "input")
    settings = get_settings()
    idempotency_key = str(arguments.get("idempotency_key") or f"agent:{agent.thread_id}:{tool_call_id}")
    try:
        action = create_asset_action(
            db,
            user_id=user_id,
            project_id=project_id,
            mode=mode,
            target=target,
            input_data={**input_data, "generate_on_low_confidence": False},
            idempotency_key=idempotency_key,
            catalog_version=str(arguments.get("catalog_version") or settings.visual_asset_catalog_version),
            matcher_version=str(arguments.get("matcher_version") or MATCHER_VERSION),
            agent_enabled=False,
        )
        db.commit()
        action_view = _json_safe(asset_action_dict(db, action))
    except HTTPException as exc:
        db.rollback()
        return {"ok": False, "error": _http_detail_text(exc.detail), "detail": exc.detail}
    except Exception:
        db.rollback()
        raise

    request = {
        "type": "vngraph_apply.requested",
        "name": "apply_vngraph_patch",
        "message": str(arguments.get("frontend_message") or "请前端预览应用 VNGraph patch，并回传 graph_json/result_graph_hash。"),
        "project_id": project_id,
        "action_id": action_view["action_id"],
        "asset_action": action_view,
        "vngraph_patch": action_view.get("vngraph_patch") or [],
        "base_graph_hash": action_view.get("base_graph_hash"),
        "submit_result_hint": {
            "endpoint": f"/api/server-agent/threads/{agent.thread_id}/tool-calls/{tool_call_id}/result",
            "body": {
                "result": {
                    "action_id": action_view["action_id"],
                    "graph_json": "前端应用 patch 后的完整 VNGraph JSON",
                    "result_graph_hash": "graph_json 的内容 hash",
                }
            },
        },
    }
    frontend_result = agent.request_frontend_tool_input(tool_call_id, "apply_vngraph_patch", arguments, request)
    return {
        **frontend_result,
        "action_id": action_view["action_id"],
        "asset_action": action_view,
        "vngraph_patch": action_view.get("vngraph_patch") or [],
        "confirm_tool": "confirm_vngraph_patch",
    }


def confirm_vngraph_patch_tool(
    db: Session,
    arguments: Dict[str, Any],
    agent: AgentToolHandle,
) -> Dict[str, Any]:
    user_id = _require_agent_owner(agent)
    project_id = _parse_int(arguments.get("project_id"), "project_id")
    action_id = str(arguments.get("action_id") or "").strip()
    if not action_id:
        return {"ok": False, "error": "action_id 不能为空"}
    graph_json = arguments.get("graph_json")
    result_graph_hash = arguments.get("result_graph_hash")
    frontend_tool_call_id = str(arguments.get("frontend_tool_call_id") or "").strip()
    if frontend_tool_call_id and graph_json is None:
        frontend_result = agent.read_frontend_tool_result(frontend_tool_call_id)
        if not frontend_result.get("ok") or frontend_result.get("status") != "completed":
            return frontend_result
        payload = frontend_result.get("result") or {}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "前端工具结果必须是对象"}
        graph_json = payload.get("graph_json")
        result_graph_hash = result_graph_hash or payload.get("result_graph_hash")
        action_id = str(payload.get("action_id") or action_id)
    if not isinstance(graph_json, dict):
        return {"ok": False, "error": "确认 VNGraph patch 必须提供 graph_json"}
    try:
        action = get_owned_asset_action(db, action_id, user_id, project_id)
        confirm_asset_action(
            db,
            action=action,
            graph_json=graph_json,
            result_graph_hash=str(result_graph_hash or "") or None,
        )
        db.commit()
        action_view = _json_safe(asset_action_dict(db, action))
    except HTTPException as exc:
        db.rollback()
        return {"ok": False, "error": _http_detail_text(exc.detail), "detail": exc.detail}
    except Exception:
        db.rollback()
        raise
    return {
        "ok": True,
        "status": "confirmed",
        "action_id": action_view["action_id"],
        "asset_action": action_view,
        "result_graph_hash": action_view.get("result_graph_hash"),
    }


def _require_agent_owner(agent: AgentToolHandle) -> int:
    if agent.owner_id is None:
        raise ValueError("当前 Agent 缺少用户身份，不能执行项目写入工具")
    return int(agent.owner_id)


def _parse_int(value: Any, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 必须是整数") from exc


def _require_dict(value: Any, field_name: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是对象")
    return dict(value)


def _parse_wait_timeout(value: Any) -> float:
    if value is None:
        return 5.0
    try:
        return max(0.0, min(float(value), 30.0))
    except (TypeError, ValueError):
        return 5.0


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _http_detail_text(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    return json.dumps(detail, ensure_ascii=False, default=str)
