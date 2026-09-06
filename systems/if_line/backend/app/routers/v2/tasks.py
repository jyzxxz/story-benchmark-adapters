from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.application.task_service import (
    TERMINAL_TASK_STATUSES,
    get_owned_task,
    request_cancel,
    retry_task,
)
from app.auth import get_current_user
from app.database import SessionLocal, get_db
from app.models import User
from app.models_v2 import GenerationTask, TaskEvent
from app.project_permissions import require_project_owner
from app.schemas_v2 import TaskEventRead, TaskRead


router = APIRouter(prefix="/tasks", tags=["tasks"])


# GET /api/tasks/{task_id}
@router.get("/{task_id}", response_model=TaskRead)
def read_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_owned_task(db, task_id, user.id)


# GET /api/tasks
@router.get("", response_model=list[TaskRead])
def list_tasks(
    project_id: int | None = None,
    task_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = db.query(GenerationTask).filter(GenerationTask.user_id == user.id)
    if project_id is not None:
        require_project_owner(db, project_id, user)
        query = query.filter(GenerationTask.project_id == project_id)
    if task_status:
        query = query.filter(GenerationTask.status == task_status)
    return query.order_by(GenerationTask.created_at.desc()).offset(offset).limit(limit).all()


# GET /api/tasks/{task_id}/event-log
@router.get("/{task_id}/event-log", response_model=list[TaskEventRead])
def read_task_events(
    task_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_task(db, task_id, user.id)
    return (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task_id, TaskEvent.seq > after)
        .order_by(TaskEvent.seq)
        .limit(limit)
        .all()
    )


# GET /api/tasks/{task_id}/events
@router.get("/{task_id}/events")
def stream_task_events(
    request: Request,
    task_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = get_owned_task(db, task_id, user.id)
    owner_id = task.user_id
    try:
        cursor = max(0, int(last_event_id or 0))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Last-Event-ID 必须是整数") from exc

    async def event_source():
        nonlocal cursor
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                break
            session = SessionLocal()
            try:
                current = session.query(GenerationTask).filter(GenerationTask.id == task_id).first()
                if not current or current.user_id != owner_id:
                    break
                events = (
                    session.query(TaskEvent)
                    .filter(TaskEvent.task_id == task_id, TaskEvent.seq > cursor)
                    .order_by(TaskEvent.seq)
                    .limit(100)
                    .all()
                )
                terminal = current.status in TERMINAL_TASK_STATUSES
                rows = [
                    (event.seq, event.event_type, event.payload, event.created_at.isoformat())
                    for event in events
                ]
            finally:
                session.close()

            if rows:
                idle_ticks = 0
                for seq, event_type, payload, created_at in rows:
                    cursor = seq
                    data = json.dumps(
                        {"event_type": event_type, "payload": payload, "created_at": created_at},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield f"id: {seq}\nevent: {event_type}\ndata: {data}\n\n"
            else:
                idle_ticks += 1
                if idle_ticks % 15 == 0:
                    yield ": keep-alive\n\n"
            if terminal and not rows:
                break
            await asyncio.sleep(1)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# POST /api/tasks/{task_id}/cancel
@router.post("/{task_id}/cancel", response_model=TaskRead)
def cancel_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = get_owned_task(db, task_id, user.id)
    request_cancel(db, task)
    db.commit()
    db.refresh(task)
    return task


# POST /api/tasks/{task_id}/retry
@router.post("/{task_id}/retry", response_model=TaskRead)
def retry_failed_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    task = get_owned_task(db, task_id, user.id)
    retry_task(db, task)
    db.commit()
    db.refresh(task)
    return task
