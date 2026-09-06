"""后台任务观察工具。"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.agent.agent_spec import AgentToolHandle
from app.application.task_service import TERMINAL_TASK_STATUSES, get_owned_task
from app.database import SessionLocal
from app.models_v2 import TaskEvent


TASK_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_task_status",
            "description": "立即读取当前用户某个后台任务的状态、进度、结果和最近事件。只查询一次，不会等待任务完成。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "后台任务编号。"},
                    "event_limit": {
                        "type": "integer",
                        "description": "返回最近多少条任务事件，默认 10，最大 30。",
                        "default": 10,
                        "minimum": 0,
                        "maximum": 30,
                    },
                },
                "required": ["task_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait_for_task",
            "description": "等待当前用户的后台任务结束。任务在等待期间成功、失败、部分完成或取消时会立即唤醒 Agent；超时但仍在运行时返回最新状态，之后可以再次调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "后台任务编号。"},
                    "timeout_seconds": {
                        "type": "number",
                        "description": "本次最多等待多少秒，默认 20 秒，最大 60 秒。",
                        "default": 20,
                        "minimum": 1,
                        "maximum": 60,
                    },
                },
                "required": ["task_id"],
            },
        },
    },
]


async def execute_task_tool(
    db: Session,
    name: str,
    arguments: Dict[str, Any],
    agent: AgentToolHandle | None = None,
) -> Optional[Dict[str, Any]]:
    if name not in {"get_task_status", "wait_for_task"}:
        return None
    owner_id = getattr(agent, "owner_id", None)
    if owner_id is None:
        return {"ok": False, "error": "缺少当前用户上下文，不能读取后台任务"}

    task_id = str(arguments.get("task_id") or "").strip()
    if not task_id:
        return {"ok": False, "error": "task_id 不能为空"}

    if name == "get_task_status":
        try:
            task = get_owned_task(db, task_id, owner_id)
        except HTTPException as exc:
            return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}
        event_limit = _bounded_int(arguments.get("event_limit"), default=10, minimum=0, maximum=30)
        return _task_view(db, task, event_limit=event_limit)

    timeout_seconds = _bounded_float(
        arguments.get("timeout_seconds"),
        default=20.0,
        minimum=1.0,
        maximum=60.0,
    )
    return await _wait_for_task(task_id, owner_id, timeout_seconds)


async def _wait_for_task(task_id: str, owner_id: int, timeout_seconds: float) -> Dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        session = SessionLocal()
        try:
            try:
                task = get_owned_task(session, task_id, owner_id)
            except HTTPException as exc:
                return {"ok": False, "error": str(exc.detail), "status_code": exc.status_code}
            result = _task_view(session, task, event_limit=10)
        finally:
            session.close()

        if result["terminal"]:
            result["wait_timed_out"] = False
            return result

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result["wait_timed_out"] = True
            return result
        await asyncio.sleep(min(1.0, remaining))


def _task_view(db: Session, task, *, event_limit: int) -> Dict[str, Any]:
    events = []
    if event_limit > 0:
        rows = (
            db.query(TaskEvent)
            .filter(TaskEvent.task_id == task.id)
            .order_by(TaskEvent.seq.desc())
            .limit(event_limit)
            .all()
        )
        events = [_event_view(item) for item in reversed(rows)]
    return {
        "ok": True,
        "task_id": task.id,
        "project_id": task.project_id,
        "kind": task.kind,
        "status": task.status,
        "terminal": task.status in TERMINAL_TASK_STATUSES,
        "stage": task.stage,
        "progress": task.progress,
        "attempt": task.attempt,
        "max_attempts": task.max_attempts,
        "result_refs": task.result_refs or {},
        "error_code": task.error_code,
        "error_detail": task.error_detail,
        "queued_at": _datetime_to_text(task.queued_at),
        "started_at": _datetime_to_text(task.started_at),
        "finished_at": _datetime_to_text(task.finished_at),
        "recent_events": events,
    }


def _event_view(event: TaskEvent) -> Dict[str, Any]:
    payload = dict(event.payload or {})
    delta = payload.pop("delta", None)
    if isinstance(delta, str):
        payload["delta_characters"] = len(delta)
    return {
        "seq": event.seq,
        "event_type": event.event_type,
        "payload": payload,
        "created_at": _datetime_to_text(event.created_at),
    }


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
