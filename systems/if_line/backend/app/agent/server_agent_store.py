"""
服务端 Agent 会话持久化。

这里保存可恢复的业务状态：thread、turn、model messages 和 SSE events。
Phoenix / agent_trace_events 只做观测，不作为恢复来源。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.agent.server_agent_model_message import filter_model_message_fields
from app.database import SessionLocal
from app.models_v2 import AgentEvent, AgentMessage, AgentThread, AgentTurn, utcnow
from app.utils import logging as xlog


def create_thread_record(
    *,
    thread_id: str,
    owner_id: int | None,
    agent_kind: str,
    mode: str,
    status: str,
    stop_reason: str | None,
    model: str | None,
    current_turn_id: str | None,
    last_turn_id: str | None,
    messages: list[dict[str, Any]],
    events: list[dict[str, Any]],
    created_at: datetime,
    updated_at: datetime,
) -> None:
    db = SessionLocal()
    try:
        if db.get(AgentThread, thread_id):
            return
        db.add(
            AgentThread(
                thread_id=thread_id,
                owner_id=owner_id,
                agent_kind=agent_kind,
                mode=mode,
                status=status,
                stop_reason=stop_reason,
                model=model,
                current_turn_id=current_turn_id,
                last_turn_id=last_turn_id,
                persisted_message_count=len(messages),
                event_count=len(events),
                created_at=created_at,
                updated_at=updated_at,
            )
        )
        db.flush()
        for index, message in enumerate(messages):
            db.add(
                AgentMessage(
                    thread_id=thread_id,
                    turn_id=None,
                    message_index=index,
                    role=str(message.get("role") or ""),
                    message=_jsonable(message),
                    created_at=updated_at,
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# 崩溃恢复边界：
# - thread 级历史必须可恢复：messages 用于恢复模型上下文，events 用于恢复前端 UI。
# - turn 内半截执行不恢复：进程崩在 LLM stream、toolcall 或 SSE delta 中间时，
#   不重放模型请求或工具调用，只把最近一个 running turn 标记为 interrupted。
# - agent_message.delta 不落 DB；如果崩在流式输出中间，已推给前端但未完成的半截文本允许丢失。
# - interrupted 会补一条 turn.failed 事件，前端刷新后可以提示用户重新提交本轮请求。
# - 如果同一个 thread 出现多个 running turn，只处理中最近一条，其余保留现场并打 error 日志。
def load_thread_record(thread_id: str, owner_id: int | None = None) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        thread = db.get(AgentThread, thread_id)
        if not thread or thread.deleted_at is not None:
            return None
        if owner_id is not None and thread.owner_id != owner_id:
            return None
        if thread.status == "running":
            now = utcnow()
            running_turns = (
                db.query(AgentTurn)
                .filter(AgentTurn.thread_id == thread_id, AgentTurn.status == "running")
                .order_by(AgentTurn.created_at.desc())
                .all()
            )
            if len(running_turns) > 1:
                xlog.error(
                    thread_id,
                    RuntimeError("multiple running agent turns"),
                    "[server-agent-store] 多个 running turn，恢复时只中断最近一条 count=%d turn_ids=%s",
                    len(running_turns),
                    [turn.turn_id for turn in running_turns],
                )
            last_seq = (
                db.query(AgentEvent.seq)
                .filter(AgentEvent.thread_id == thread_id)
                .order_by(AgentEvent.seq.desc())
                .first()
            )
            next_seq = int(last_seq[0]) + 1 if last_seq else 1
            thread.status = "waiting_user"
            thread.stop_reason = "interrupted"
            thread.current_turn_id = None
            thread.updated_at = now
            turn = running_turns[0] if running_turns else None
            if turn:
                turn.status = "interrupted"
                turn.stop_reason = "interrupted"
                turn.event_end_seq = next_seq
                turn.updated_at = now
                turn.completed_at = now
                event = {
                    "type": "turn.failed",
                    "thread_id": thread_id,
                    "turn_id": turn.turn_id,
                    "seq": next_seq,
                    "stop_reason": "interrupted",
                    "error": "Agent 运行被中断，请重新提交本轮请求。",
                    "created_at": now.isoformat(),
                }
                db.add(
                    AgentEvent(
                        thread_id=thread_id,
                        turn_id=turn.turn_id,
                        seq=next_seq,
                        event_type="turn.failed",
                        payload=event,
                        created_at=now,
                    )
                )
            thread.event_count = next_seq - 1
            db.commit()
        message_rows = (
            db.query(AgentMessage)
            .filter(AgentMessage.thread_id == thread_id)
            .order_by(AgentMessage.message_index)
            .all()
        )
        active_messages, active_message_db_indexes = _active_model_messages(message_rows)
        next_persisted_message_index = 0
        if message_rows:
            next_persisted_message_index = max(int(row.message_index or 0) for row in message_rows) + 1
        event_rows = (
            db.query(AgentEvent)
            .filter(AgentEvent.thread_id == thread_id)
            .order_by(AgentEvent.seq)
            .all()
        )
        events = [dict(row.payload or {}) for row in event_rows]
        max_event_seq = max((int(row.seq or 0) for row in event_rows), default=int(thread.event_count or 0))
        return {
            "thread_id": thread.thread_id,
            "owner_id": thread.owner_id,
            "agent_kind": thread.agent_kind,
            "mode": thread.mode,
            "status": thread.status,
            "stop_reason": thread.stop_reason,
            "model": thread.model,
            "current_turn_id": thread.current_turn_id,
            "last_turn_id": thread.last_turn_id,
            "created_at": thread.created_at,
            "updated_at": thread.updated_at,
            "active_messages": active_messages,
            "active_message_db_indexes": active_message_db_indexes,
            "next_persisted_message_index": next_persisted_message_index,
            "next_event_seq": max_event_seq + 1,
            "events": events,
        }
    finally:
        db.close()


def list_thread_records(
    *,
    owner_id: int,
    limit: int,
    offset: int,
    status: str | None = None,
    agent_kind: str | None = None,
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        query = db.query(AgentThread).filter(
            AgentThread.owner_id == owner_id,
            AgentThread.deleted_at.is_(None),
        )
        if status:
            query = query.filter(AgentThread.status == status)
        if agent_kind:
            query = query.filter(AgentThread.agent_kind == agent_kind)
        total = query.count()
        rows = (
            query.order_by(AgentThread.updated_at.desc(), AgentThread.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {
            "items": [_thread_summary(row) for row in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + len(rows) < total,
        }
    finally:
        db.close()


def list_event_page(
    *,
    thread_id: str,
    owner_id: int,
    after_seq: int,
    limit: int,
    turn_id: str | None = None,
) -> dict[str, Any] | None:
    db = SessionLocal()
    try:
        thread = db.get(AgentThread, thread_id)
        if not thread or thread.deleted_at is not None or thread.owner_id != owner_id:
            return None
        query = db.query(AgentEvent).filter(
            AgentEvent.thread_id == thread_id,
            AgentEvent.seq > after_seq,
        )
        if turn_id:
            query = query.filter(AgentEvent.turn_id == turn_id)
        rows = query.order_by(AgentEvent.seq.asc()).limit(limit + 1).all()
        page_rows = rows[:limit]
        events = [dict(row.payload or {}) for row in page_rows]
        next_after_seq = after_seq
        for event in events:
            next_after_seq = max(next_after_seq, int(event.get("seq") or 0))
        return {
            "thread_id": thread.thread_id,
            "turn_id": turn_id,
            "events": events,
            "after_seq": after_seq,
            "next_after_seq": next_after_seq,
            "limit": limit,
            "has_more": len(rows) > limit,
        }
    finally:
        db.close()


def persist_thread_snapshot(
    *,
    thread_id: str,
    mode: str,
    status: str,
    stop_reason: str | None,
    current_turn_id: str | None,
    last_turn_id: str | None,
    persisted_message_count: int,
    event_count: int,
    updated_at: datetime,
) -> None:
    db = SessionLocal()
    try:
        thread = db.get(AgentThread, thread_id)
        if not thread:
            return
        thread.mode = mode
        thread.status = status
        thread.stop_reason = stop_reason
        thread.current_turn_id = current_turn_id
        thread.last_turn_id = last_turn_id
        thread.persisted_message_count = persisted_message_count
        thread.event_count = event_count
        thread.updated_at = updated_at
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def persist_message(
    *,
    thread_id: str,
    turn_id: str | None,
    message_index: int,
    message: dict[str, Any],
    created_at: datetime,
) -> None:
    db = SessionLocal()
    try:
        db.add(
            AgentMessage(
                thread_id=thread_id,
                turn_id=turn_id,
                message_index=message_index,
                role=str(message.get("role") or ""),
                message=_jsonable(message),
                created_at=created_at,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def begin_turn_record(
    *,
    thread_id: str,
    turn_id: str,
    mode: str,
    content: str,
    prompt: str,
    message_start_index: int,
    event_start_seq: int,
    created_at: datetime,
) -> None:
    db = SessionLocal()
    try:
        existing = db.get(AgentTurn, turn_id)
        if existing:
            return
        db.add(
            AgentTurn(
                turn_id=turn_id,
                thread_id=thread_id,
                mode=mode,
                status="running",
                input={
                    "content": content,
                    "prompt": prompt,
                },
                message_start_index=message_start_index,
                event_start_seq=event_start_seq,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def finish_turn_record(
    *,
    thread_id: str,
    turn_id: str | None,
    event: dict[str, Any],
    message_end_index: int,
    updated_at: datetime,
) -> None:
    if not turn_id:
        return
    db = SessionLocal()
    try:
        turn = db.get(AgentTurn, turn_id)
        if not turn or turn.thread_id != thread_id:
            return
        failed = str(event.get("type") or "") == "turn.failed"
        stop_reason = event.get("stop_reason")
        if failed and stop_reason == "interrupted":
            status = "interrupted"
        elif failed:
            status = "failed"
        else:
            status = "completed"
        turn.status = status
        turn.stop_reason = str(stop_reason or "")
        turn.message_end_index = message_end_index
        turn.event_end_seq = int(event.get("seq") or 0)
        turn.sampling_steps = _optional_int(event.get("sampling_steps"))
        turn.tool_call_count = _optional_int(event.get("tool_call_count"))
        if failed:
            turn.error = _jsonable({"message": event.get("error")})
        turn.updated_at = updated_at
        turn.completed_at = updated_at
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def persist_event(
    *,
    thread_id: str,
    event: dict[str, Any],
    created_at: datetime,
) -> None:
    if event.get("type") == "agent_message.delta":
        return
    db = SessionLocal()
    try:
        db.add(
            AgentEvent(
                thread_id=thread_id,
                turn_id=event.get("turn_id"),
                seq=int(event.get("seq") or 0),
                event_type=str(event.get("type") or ""),
                payload=_jsonable(event),
                created_at=created_at,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def delete_thread_record(thread_id: str, owner_id: int | None = None) -> bool:
    db = SessionLocal()
    try:
        thread = db.get(AgentThread, thread_id)
        if not thread or thread.deleted_at is not None:
            return False
        if owner_id is not None and thread.owner_id != owner_id:
            return False
        thread.deleted_at = utcnow()
        thread.status = "deleted"
        thread.updated_at = thread.deleted_at
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# 数据库里的 agent_messages 是审计账本：历史 message 永远追加，不因为压缩而删除。
# 但恢复 Agent 时不能把全量历史重新塞回模型，否则上下文压缩就失效了。
# 这里负责从数据库全量 rows 重建“模型活跃窗口”：
#   - 没有 context_compaction 摘要：直接按数据库顺序恢复全部 message；
#   - 有 context_compaction 摘要：只保留 system、最新摘要，以及摘要声明的最近原文窗口。
#
# 摘要消息里的 first_kept_message_index 是关键边界：
#   system + latest_summary + message_index >= first_kept_message_index 的普通消息
#
# 注意：summary message 落库可能早于 thread snapshot 更新。恢复时按 messages 本身重建窗口，
# 不依赖 snapshot 里的内存状态，这样即使进程在压缩应用阶段崩溃，也不会丢掉原始历史。
# message_index 与内存窗口的详细关系见 ServerAgentMessageWindow。
def _active_model_messages(rows: list[AgentMessage]) -> tuple[list[dict[str, Any]], list[int]]:
    if not rows:
        return [], []

    # 找最后一条 context_compaction 摘要。旧摘要仍保留在数据库里，但不再进入模型窗口。
    summary_pos = -1
    summary_payload: dict[str, Any] | None = None
    for pos, row in enumerate(rows):
        payload = dict(row.message or {})
        if payload.get("ifline_kind") == "context_compaction":
            summary_pos = pos
            summary_payload = payload

    if summary_pos < 0 or not summary_payload:
        return [filter_model_message_fields(row.message or {}) for row in rows], [
            int(row.message_index) for row in rows
        ]

    first_kept_index = _optional_int(summary_payload.get("first_kept_message_index"))
    summary_index = int(rows[summary_pos].message_index)
    active_rows: list[AgentMessage] = []
    if rows:
        # rows[0] 是 system prompt。它通常不会被摘要替代，恢复时固定放回窗口开头。
        active_rows.append(rows[0])
    # summary 在数据库里可能排在最近 user 后面；这里按“模型阅读顺序”放到最近窗口前面。
    active_rows.append(rows[summary_pos])
    for row in rows:
        row_index = int(row.message_index)
        if row_index == 0 or row_index == summary_index:
            continue
        if first_kept_index is not None and row_index >= first_kept_index:
            active_rows.append(row)

    return [filter_model_message_fields(row.message or {}) for row in active_rows], [
        int(row.message_index) for row in active_rows
    ]


def _thread_summary(thread: AgentThread) -> dict[str, Any]:
    return {
        "thread_id": thread.thread_id,
        "agent_kind": thread.agent_kind,
        "mode": thread.mode,
        "status": thread.status,
        "stop_reason": thread.stop_reason,
        "model": thread.model,
        "current_turn_id": thread.current_turn_id,
        "last_turn_id": thread.last_turn_id,
        "persisted_message_count": thread.persisted_message_count,
        "event_count": thread.event_count,
        "created_at": _datetime_to_text(thread.created_at),
        "updated_at": _datetime_to_text(thread.updated_at),
    }


def _datetime_to_text(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
