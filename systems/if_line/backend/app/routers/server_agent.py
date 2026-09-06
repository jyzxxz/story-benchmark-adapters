"""
服务端最小 Agent 路由。
"""
import asyncio
import json
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from app.agent.server_agent import server_agent_manager
from app.agent.server_agent_store import list_event_page, list_thread_records
from app.agent.server_agent_protocol import EEvent_error
from app.auth import get_current_user
from app.models import User


router = APIRouter()


class ServerAgentTurnAcceptedResponse(BaseModel):
    thread_id: str
    turn_id: str
    accepted: bool
    status: str
    message: str


class ServerAgentTurnCancelResponse(BaseModel):
    thread_id: str
    turn_id: str
    cancelled: bool
    status: str
    stop_reason: str | None = None
    message: str


class ServerAgentCreateRequest(BaseModel):
    agent_kind: str
    mode: str | None = None


class ServerAgentSnapshotResponse(BaseModel):
    thread_id: str
    agent_kind: str = "server_agent"
    current_turn_id: str | None = None
    last_turn_id: str | None = None
    mode: str
    status: str
    stop_reason: str | None
    message_count: int
    persisted_message_count: int | None = None
    event_count: int
    subscriber_count: int = 0
    created_at: str | None
    updated_at: str | None
    messages: List[Dict[str, Any]]
    events: List[Dict[str, Any]]


class ServerAgentThreadSummaryResponse(BaseModel):
    thread_id: str
    agent_kind: str
    current_turn_id: str | None = None
    last_turn_id: str | None = None
    mode: str
    status: str
    stop_reason: str | None = None
    model: str | None = None
    persisted_message_count: int
    event_count: int
    created_at: str | None = None
    updated_at: str | None = None


class ServerAgentThreadListResponse(BaseModel):
    items: List[ServerAgentThreadSummaryResponse]
    total: int
    limit: int
    offset: int
    has_more: bool


class ServerAgentEventPageResponse(BaseModel):
    thread_id: str
    turn_id: str | None = None
    events: List[Dict[str, Any]]
    after_seq: int
    next_after_seq: int
    limit: int
    has_more: bool


class ServerAgentInputRequest(BaseModel):
    content: str = ""
    prompt: str = ""
    mode: str | None = None


class ServerAgentToolCallResultRequest(BaseModel):
    result: Dict[str, Any]


class ServerAgentToolCallResultAcceptedResponse(BaseModel):
    thread_id: str
    turn_id: str | None = None
    tool_call_id: str
    accepted: bool
    status: str
    message: str


# POST /api/server-agent/threads
@router.post("/threads", response_model=ServerAgentSnapshotResponse)
async def create_server_agent(
    req: ServerAgentCreateRequest,
    current_user: User = Depends(get_current_user),
):
    """创建一个驻留内存的 Agent 对象。"""
    try:
        agent = await server_agent_manager.create_agent(
            owner_id=current_user.id,
            agent_kind=req.agent_kind,
            mode=req.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ServerAgentSnapshotResponse(**agent.snapshot())


# GET /api/server-agent/threads
@router.get("/threads", response_model=ServerAgentThreadListResponse)
async def list_server_agent_threads(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    agent_kind: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """读取当前用户自己的 Agent 会话列表，用于恢复入口。"""
    return ServerAgentThreadListResponse(
        **list_thread_records(
            owner_id=current_user.id,
            limit=limit,
            offset=offset,
            status=status,
            agent_kind=agent_kind,
        )
    )


# GET /api/server-agent/threads/{thread_id}
@router.get("/threads/{thread_id}", response_model=ServerAgentSnapshotResponse)
async def get_server_agent(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """查看 Agent 当前内存状态。"""
    agent = server_agent_manager.get_thread(thread_id, owner_id=current_user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return ServerAgentSnapshotResponse(**agent.snapshot())


# GET /api/server-agent/threads/{thread_id}/events/page
@router.get("/threads/{thread_id}/events/page", response_model=ServerAgentEventPageResponse)
async def list_server_agent_event_page(
    thread_id: str,
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    turn_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """按 seq 游标读取历史事件页。恢复 UI 时用它，实时增量仍用 SSE。"""
    agent = server_agent_manager.get_thread(thread_id, owner_id=current_user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    page = list_event_page(
        thread_id=thread_id,
        owner_id=current_user.id,
        after_seq=after_seq,
        limit=limit,
        turn_id=turn_id,
    )
    if not page:
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return ServerAgentEventPageResponse(**page)


# GET /api/server-agent/threads/{thread_id}/events
@router.get("/threads/{thread_id}/events")
async def stream_server_agent_events(
    thread_id: str,
    request: Request,
    after_seq: int = Query(default=0, ge=0),
    turn_id: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
):
    """订阅 Agent thread 事件，使用 text/event-stream 推送。"""
    agent = server_agent_manager.get_thread(thread_id, owner_id=current_user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    subscriber_id, queue = agent.subscribe_events(after_seq=after_seq, turn_id=turn_id)

    async def event_stream():
        yield ": subscribed\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _format_sse_event(event)
        finally:
            agent.unsubscribe_events(subscriber_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# POST /api/server-agent/threads/{thread_id}/turns/resume
@router.post("/threads/{thread_id}/turns/resume", response_model=ServerAgentTurnAcceptedResponse)
async def resume_server_agent_turn(
    thread_id: str,
    req: ServerAgentInputRequest,
    current_user: User = Depends(get_current_user),
):
    """向指定 Agent 投递一次输入；实际执行在后台，结果通过 thread SSE 推送。"""
    agent = server_agent_manager.get_thread(thread_id, owner_id=current_user.id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent 不存在")

    if not req.content.strip() and not req.prompt.strip():
        raise HTTPException(status_code=400, detail="输入内容不能为空")

    turn_id = uuid.uuid4().hex

    try:
        server_agent_manager.submit_resume(
            agent,
            run_id=turn_id,
            content=req.content,
            prompt=req.prompt,
            mode=req.mode,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ServerAgentTurnAcceptedResponse(
        thread_id=agent.thread_id,
        turn_id=turn_id,
        accepted=True,
        status=agent.status,
        message="Agent turn 已提交，后续事件请通过 SSE 订阅读取。",
    )


# POST /api/server-agent/threads/{thread_id}/tool-calls/{tool_call_id}/result
@router.post(
    "/threads/{thread_id}/tool-calls/{tool_call_id}/result",
    response_model=ServerAgentToolCallResultAcceptedResponse,
)
async def submit_server_agent_tool_call_result(
    thread_id: str,
    tool_call_id: str,
    req: ServerAgentToolCallResultRequest,
    current_user: User = Depends(get_current_user),
):
    """回传前端工具调用结果；普通用户输入不要走这个接口。"""
    try:
        result = server_agent_manager.submit_frontend_tool_result(
            thread_id,
            tool_call_id,
            req.result,
            owner_id=current_user.id,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Agent 不存在" else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return ServerAgentToolCallResultAcceptedResponse(**result)


# POST /api/server-agent/threads/{thread_id}/turns/{turn_id}/cancel
@router.post("/threads/{thread_id}/turns/{turn_id}/cancel", response_model=ServerAgentTurnCancelResponse)
async def cancel_server_agent_turn(
    thread_id: str,
    turn_id: str,
    current_user: User = Depends(get_current_user),
):
    """显式打断当前 turn；SSE 断开不会自动打断。"""
    try:
        result = server_agent_manager.cancel_turn(thread_id, turn_id, owner_id=current_user.id)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if message == "Agent 不存在" else 409
        raise HTTPException(status_code=status_code, detail=message) from exc
    return ServerAgentTurnCancelResponse(**result)


# DELETE /api/server-agent/threads/{thread_id}
@router.delete("/threads/{thread_id}")
async def delete_server_agent(
    thread_id: str,
    current_user: User = Depends(get_current_user),
):
    """删除内存中的 Agent 对象。"""
    if not server_agent_manager.delete_thread(thread_id, owner_id=current_user.id):
        raise HTTPException(status_code=404, detail="Agent 不存在")
    return {"deleted": True, "thread_id": thread_id}


def _format_sse_event(event: Dict[str, Any]) -> str:
    event_type = str(event.get("type") or EEvent_error)
    seq = event.get("seq")
    event_id = "" if seq is None else str(seq)
    data = json.dumps(event, ensure_ascii=False)
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    return "\n".join(lines) + "\n\n"
