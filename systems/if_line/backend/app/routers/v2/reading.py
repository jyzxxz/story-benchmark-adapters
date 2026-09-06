from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.application.branch_service import (
    add_bookmark,
    choose,
    create_reading_session,
    fork_session,
    get_owned_session,
    undo_latest_choice,
)
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import ChoiceDecision, ReadingBookmark
from app.schemas_branch import (
    BookmarkCreate,
    BookmarkRead,
    ChoiceCreate,
    ChoiceRead,
    ChoiceResult,
    ForkCreate,
    ReadingSessionCreate,
    ReadingSessionRead,
)


router = APIRouter(prefix="/reading-sessions", tags=["reading"])


def _if_match(value: str | None) -> int:
    if value is None or not value.strip():
        raise HTTPException(status_code=428, detail="必须提供 If-Match 会话版本")
    raw = value.strip().strip('"')
    try:
        result = int(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="If-Match 必须是会话 lock_version") from exc
    if result < 1:
        raise HTTPException(status_code=400, detail="If-Match 必须大于 0")
    return result


# POST /api/reading-sessions
@router.post("", response_model=ReadingSessionRead, status_code=status.HTTP_201_CREATED)
def start_session(
    body: ReadingSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reading = create_reading_session(
        db,
        user_id=user.id,
        project_id=body.project_id,
        release_id=body.release_id,
        initial_state=body.initial_state,
    )
    db.commit()
    db.refresh(reading)
    return reading


# GET /api/reading-sessions/{session_id}
@router.get("/{session_id}", response_model=ReadingSessionRead)
def read_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return get_owned_session(db, session_id, user.id)


# GET /api/reading-sessions/{session_id}/timeline
@router.get("/{session_id}/timeline", response_model=list[ChoiceRead])
def timeline(
    session_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_session(db, session_id, user.id)
    return (
        db.query(ChoiceDecision)
        .filter(ChoiceDecision.session_id == session_id)
        .order_by(ChoiceDecision.created_at)
        .offset(offset)
        .limit(limit)
        .all()
    )


# POST /api/reading-sessions/{session_id}/choices
@router.post("/{session_id}/choices", response_model=ChoiceResult)
def submit_choice(
    session_id: str,
    body: ChoiceCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    decision, created = choose(
        db,
        session_id=session_id,
        user_id=user.id,
        checkpoint_node_id=body.checkpoint_node_id,
        option_key=body.option_key,
        idempotency_key=idempotency_key or "",
        expected_lock_version=_if_match(if_match),
    )
    db.commit()
    reading = get_owned_session(db, session_id, user.id)
    return ChoiceResult(
        decision=ChoiceRead.model_validate(decision),
        session=ReadingSessionRead.model_validate(reading),
        created=created,
    )


# POST /api/reading-sessions/{session_id}/undo
@router.post("/{session_id}/undo", response_model=ChoiceResult)
def undo(
    session_id: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    decision = undo_latest_choice(
        db,
        session_id=session_id,
        user_id=user.id,
        expected_lock_version=_if_match(if_match),
    )
    db.commit()
    reading = get_owned_session(db, session_id, user.id)
    return ChoiceResult(
        decision=ChoiceRead.model_validate(decision),
        session=ReadingSessionRead.model_validate(reading),
        created=False,
    )


# POST /api/reading-sessions/{session_id}/fork
@router.post("/{session_id}/fork", response_model=ReadingSessionRead, status_code=status.HTTP_201_CREATED)
def fork(
    session_id: str,
    body: ForkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    reading = fork_session(db, session_id=session_id, user_id=user.id, decision_id=body.decision_id)
    db.commit()
    return reading


# POST /api/reading-sessions/{session_id}/bookmarks
@router.post("/{session_id}/bookmarks", response_model=BookmarkRead, status_code=status.HTTP_201_CREATED)
def bookmark(
    session_id: str,
    body: BookmarkCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    item = add_bookmark(db, session_id=session_id, user_id=user.id, node_id=body.node_id, label=body.label)
    db.commit()
    return item


# GET /api/reading-sessions/{session_id}/bookmarks
@router.get("/{session_id}/bookmarks", response_model=list[BookmarkRead])
def bookmarks(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    get_owned_session(db, session_id, user.id)
    return (
        db.query(ReadingBookmark)
        .filter(ReadingBookmark.session_id == session_id)
        .order_by(ReadingBookmark.created_at)
        .all()
    )

