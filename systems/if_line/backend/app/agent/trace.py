"""
服务端 Agent 轨迹采集。

这层只记录“Agent 语义事件”，不参与业务事务。写入失败只打日志，
避免观测链路影响真实对话和工具调用。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models_v2 import AgentTraceEvent
from app.utils import logging as xlog


TRACE_VISIBILITIES = {"model", "ui", "admin", "debug"}
TRACE_SOURCES = {"agent", "model", "tool", "task", "user", "frontend", "system"}
_TEXT_PREVIEW_CHARS = 1200
_LIST_PREVIEW_COUNT = 20


def new_span_id() -> str:
    return uuid.uuid4().hex


def record_agent_trace_event(
    *,
    thread_id: str,
    event_type: str,
    run_id: str | None = None,
    run_seq: int | None = None,
    source: str = "agent",
    visibility: str = "ui",
    span_id: str | None = None,
    parent_span_id: str | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
    task_id: str | None = None,
    model: str | None = None,
    payload: dict[str, Any] | None = None,
    model_view: dict[str, Any] | None = None,
    full_ref: dict[str, Any] | None = None,
) -> None:
    source = source if source in TRACE_SOURCES else "agent"
    visibility = visibility if visibility in TRACE_VISIBILITIES else "ui"
    safe_payload = _jsonable_dict(payload or {})
    safe_model_view = _jsonable_dict(model_view or {})
    safe_full_ref = _jsonable_dict(full_ref or {})
    extracted_task_id = task_id or _extract_task_id(safe_payload, safe_model_view)

    db = SessionLocal()
    try:
        db.add(
            AgentTraceEvent(
                thread_id=thread_id,
                run_id=run_id,
                run_seq=run_seq,
                event_type=event_type,
                source=source,
                visibility=visibility,
                span_id=span_id or new_span_id(),
                parent_span_id=parent_span_id,
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                task_id=extracted_task_id,
                model=model,
                payload=safe_payload,
                model_view=safe_model_view,
                full_ref=safe_full_ref,
                created_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        xlog.warn(thread_id, "[server-agent-trace] 写入失败 event_type=%s error=%s", event_type, exc)
    finally:
        db.close()


def list_agent_trace_events(
    db: Session,
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
    event_type: str | None = None,
    after_id: int = 0,
    limit: int = 200,
) -> list[AgentTraceEvent]:
    query = db.query(AgentTraceEvent)
    if after_id > 0:
        query = query.filter(AgentTraceEvent.id > after_id)
    if thread_id:
        query = query.filter(AgentTraceEvent.thread_id == thread_id)
    if run_id:
        query = query.filter(AgentTraceEvent.run_id == run_id)
    if event_type:
        query = query.filter(AgentTraceEvent.event_type == event_type)
    return query.order_by(AgentTraceEvent.id).limit(limit).all()


def build_model_view(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return _compact_mapping(value, _LIST_PREVIEW_COUNT)
    return {"value": _compact_value(value)}


def _extract_task_id(*values: Any) -> str | None:
    for value in values:
        found = _find_key(value, "task_id")
        if isinstance(found, str) and found:
            return found
    return None


def _find_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for item in value.values():
            found = _find_key(item, key)
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _find_key(item, key)
            if found is not None:
                return found
    return None


def _jsonable_dict(value: dict[str, Any]) -> dict[str, Any]:
    try:
        json.dumps(value, ensure_ascii=False, default=str)
        return value
    except TypeError:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _compact_mapping(value: dict[str, Any], limit: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= limit:
            result["_omitted_keys"] = max(0, len(value) - limit)
            break
        result[str(key)] = _compact_value(item)
    return result


def _compact_list(values: Iterable[Any]) -> list[Any]:
    items = list(values)
    result = [_compact_value(item) for item in items[:_LIST_PREVIEW_COUNT]]
    if len(items) > _LIST_PREVIEW_COUNT:
        result.append({"_omitted_items": len(items) - _LIST_PREVIEW_COUNT})
    return result


def _compact_value(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= _TEXT_PREVIEW_CHARS:
            return value
        return value[:_TEXT_PREVIEW_CHARS] + "...[已截断]"
    if isinstance(value, dict):
        return _compact_mapping(value, _LIST_PREVIEW_COUNT)
    if isinstance(value, list):
        return _compact_list(value)
    return value
