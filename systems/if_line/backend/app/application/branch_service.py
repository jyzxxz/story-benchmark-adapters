from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from fastapi import HTTPException
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.public_release_service import get_active_public_release
from app.core.errors import AppError
from app.models_v2 import (
    BranchCandidate,
    ChoiceDecision,
    OutboxEvent,
    ProjectRelease,
    ReadingBookmark,
    ReadingSession,
    StateSnapshot,
    StoryNode,
    utcnow,
)


MAX_STATE_BYTES = 64 * 1024
MAX_STATE_DEPTH = 16
MAX_STATE_NODES = 4000


def _active_release_or_404(
    db: Session,
    *,
    release_id: str,
    project_id: int | None = None,
) -> ProjectRelease:
    try:
        release = get_active_public_release(db, release_id=release_id)
    except AppError as exc:
        raise HTTPException(status_code=404, detail="发布版本不存在") from exc
    if project_id is not None and release.project_id != project_id:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    return release


def _validate_state(state: dict[str, Any]) -> None:
    stack: list[tuple[Any, int]] = [(state, 1)]
    nodes = 0
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_STATE_NODES or depth > MAX_STATE_DEPTH:
            raise HTTPException(status_code=413, detail="故事状态过大或嵌套过深")
        if isinstance(value, dict):
            stack.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            stack.extend((item, depth + 1) for item in value)
    try:
        encoded = json.dumps(
            state,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="故事状态不是有效 JSON") from exc
    if len(encoded) > MAX_STATE_BYTES:
        raise HTTPException(status_code=413, detail="故事状态不能超过 64 KiB")


def _merge_state(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in delta.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_state(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _snapshot(db: Session, project_id: int, state: dict[str, Any], parent_id: str | None) -> StateSnapshot:
    _validate_state(state)
    digest = content_hash(state)
    existing = (
        db.query(StateSnapshot)
        .filter(StateSnapshot.project_id == project_id, StateSnapshot.state_hash == digest)
        .first()
    )
    if existing:
        return existing
    snapshot = StateSnapshot(
        project_id=project_id,
        parent_snapshot_id=parent_id,
        state_hash=digest,
        state_json=state,
    )
    try:
        with db.begin_nested():
            db.add(snapshot)
            db.flush()
        return snapshot
    except IntegrityError:
        # Content-addressed snapshots are intentionally shared across reading
        # sessions. A concurrent session may have committed the same state
        # between our lookup and insert; recover without aborting the choice.
        existing = (
            db.query(StateSnapshot)
            .filter(StateSnapshot.project_id == project_id, StateSnapshot.state_hash == digest)
            .first()
        )
        if existing:
            return existing
        raise


def create_reading_session(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    release_id: str,
    initial_state: dict[str, Any] | None = None,
) -> ReadingSession:
    release = _active_release_or_404(
        db,
        release_id=release_id,
        project_id=project_id,
    )
    start_node_id = (release.manifest_json.get("story") or {}).get("start_node_id")
    if start_node_id:
        if not _release_has_node(release, start_node_id):
            raise HTTPException(status_code=409, detail="发布版本起始节点不在 manifest 中")
        node = (
            db.query(StoryNode)
            .filter(StoryNode.id == start_node_id, StoryNode.project_id == project_id)
            .first()
        )
        if not node:
            raise HTTPException(status_code=409, detail="发布版本起始节点损坏")
    snapshot = _snapshot(db, project_id, initial_state or {}, None)
    reading = ReadingSession(
        user_id=user_id,
        project_id=project_id,
        release_id=release.id,
        head_node_id=start_node_id,
        state_snapshot_id=snapshot.id,
    )
    db.add(reading)
    db.flush()
    return reading


def get_owned_session(db: Session, session_id: str, user_id: int, *, lock: bool = False) -> ReadingSession:
    query = db.query(ReadingSession).filter(
        ReadingSession.id == session_id,
        ReadingSession.user_id == user_id,
    )
    if lock:
        query = query.with_for_update()
    session = query.first()
    if not session:
        raise HTTPException(status_code=404, detail="阅读会话不存在")
    try:
        release = get_active_public_release(db, release_id=session.release_id)
    except AppError as exc:
        raise HTTPException(status_code=404, detail="阅读会话不存在") from exc
    if release.project_id != session.project_id:
        raise HTTPException(status_code=404, detail="阅读会话不存在")
    return session


def _release_story(release: ProjectRelease) -> dict[str, Any]:
    story = (release.manifest_json or {}).get("story") or {}
    return story if isinstance(story, dict) else {}


def _release_has_node(release: ProjectRelease, node_id: str | None) -> bool:
    if not node_id:
        return False
    return any(
        isinstance(node, dict) and node.get("id") == node_id
        for node in _release_story(release).get("nodes", [])
    )


def _release_candidate(
    release: ProjectRelease,
    *,
    checkpoint_node_id: str,
    option_key: str,
) -> dict[str, Any] | None:
    return next(
        (
            candidate
            for candidate in _release_story(release).get("candidates", [])
            if isinstance(candidate, dict)
            and candidate.get("checkpoint_node_id") == checkpoint_node_id
            and candidate.get("option_key") == option_key
        ),
        None,
    )


def _find_choice_decision(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    idempotency_key: str,
) -> ChoiceDecision | None:
    return (
        db.query(ChoiceDecision)
        .join(ReadingSession, ReadingSession.id == ChoiceDecision.session_id)
        .filter(
            ChoiceDecision.session_id == session_id,
            ChoiceDecision.idempotency_key == idempotency_key,
            ReadingSession.user_id == user_id,
        )
        .first()
    )


def _validate_choice_replay(
    decision: ChoiceDecision,
    *,
    checkpoint_node_id: str,
    option_key: str,
) -> None:
    if decision.checkpoint_node_id != checkpoint_node_id or decision.option_key != option_key:
        raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同选择")


def _advance_session_head(
    db: Session,
    *,
    reading: ReadingSession,
    user_id: int,
    expected_lock_version: int,
    checkpoint_node_id: str,
    preview_node_id: str,
    result_snapshot_id: str,
) -> bool:
    statement = (
        update(ReadingSession)
        .where(
            ReadingSession.id == reading.id,
            ReadingSession.user_id == user_id,
            ReadingSession.lock_version == expected_lock_version,
            ReadingSession.head_node_id == checkpoint_node_id,
        )
        .values(
            head_node_id=preview_node_id,
            state_snapshot_id=result_snapshot_id,
            lock_version=ReadingSession.lock_version + 1,
            updated_at=utcnow(),
        )
    )
    return db.execute(statement).rowcount == 1


def choose(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    checkpoint_node_id: str,
    option_key: str,
    idempotency_key: str,
    expected_lock_version: int,
) -> tuple[ChoiceDecision, bool]:
    idempotency_key = (idempotency_key or "").strip()
    if not idempotency_key or len(idempotency_key) > 255:
        raise HTTPException(status_code=400, detail="缺少有效的 Idempotency-Key")
    reading = get_owned_session(db, session_id, user_id)
    existing = _find_choice_decision(
        db,
        session_id=session_id,
        user_id=user_id,
        idempotency_key=idempotency_key,
    )
    if existing:
        _validate_choice_replay(
            existing,
            checkpoint_node_id=checkpoint_node_id,
            option_key=option_key,
        )
        return existing, False

    if reading.status != "active":
        raise HTTPException(status_code=409, detail="阅读会话当前不可修改")
    if reading.lock_version != expected_lock_version:
        raise HTTPException(status_code=409, detail={"code": "session.version_conflict", "current": reading.lock_version})
    if reading.head_node_id != checkpoint_node_id:
        raise HTTPException(status_code=409, detail="会话不在该检查点")

    release = _active_release_or_404(
        db,
        release_id=reading.release_id,
        project_id=reading.project_id,
    )
    if not _release_has_node(release, checkpoint_node_id):
        raise HTTPException(status_code=409, detail="检查点不属于当前发布版本")
    frozen_candidate = _release_candidate(
        release,
        checkpoint_node_id=checkpoint_node_id,
        option_key=option_key,
    )
    if not frozen_candidate:
        raise HTTPException(status_code=404, detail="分支候选不存在")

    candidate = (
        db.query(BranchCandidate)
        .filter(
            BranchCandidate.id == frozen_candidate.get("id"),
            BranchCandidate.project_id == reading.project_id,
            BranchCandidate.checkpoint_node_id == checkpoint_node_id,
            BranchCandidate.option_key == option_key,
        )
        .first()
    )
    if not candidate:
        raise HTTPException(status_code=409, detail="发布版本的分支候选记录损坏")
    frozen_status = frozen_candidate.get("candidate_status")
    preview_node_id = frozen_candidate.get("preview_node_id")
    if frozen_status not in {"preview_ready", "selected_text_ready", "complete"} or not preview_node_id:
        raise HTTPException(status_code=409, detail={"code": "branch.preview_not_ready", "status": frozen_status})
    if not _release_has_node(release, preview_node_id):
        raise HTTPException(status_code=409, detail="候选预览节点不属于当前发布版本")
    preview = db.query(StoryNode).filter(StoryNode.id == preview_node_id).first()
    if not preview or preview.project_id != reading.project_id:
        raise HTTPException(status_code=409, detail="候选预览节点损坏")

    previous_snapshot = (
        db.query(StateSnapshot).filter(StateSnapshot.id == reading.state_snapshot_id).first()
        if reading.state_snapshot_id
        else None
    )
    frozen_delta = frozen_candidate.get("state_delta") or {}
    if not isinstance(frozen_delta, dict):
        raise HTTPException(status_code=409, detail="发布版本的状态差分损坏")
    next_state = _merge_state(previous_snapshot.state_json if previous_snapshot else {}, frozen_delta)
    result_snapshot = _snapshot(
        db,
        reading.project_id,
        next_state,
        previous_snapshot.id if previous_snapshot else None,
    )

    if not _advance_session_head(
        db,
        reading=reading,
        user_id=user_id,
        expected_lock_version=expected_lock_version,
        checkpoint_node_id=checkpoint_node_id,
        preview_node_id=preview_node_id,
        result_snapshot_id=result_snapshot.id,
    ):
        # A request with the same idempotency key can lose the session CAS to
        # the request that just committed that very decision. End the losing
        # transaction before checking for the winner's durable row. A
        # content-addressed snapshot may already exist and is safe to reuse;
        # no session head or decision from the loser is retained. Different
        # choices still get the normal concurrency conflict.
        db.rollback()
        get_owned_session(db, session_id, user_id)
        replay = _find_choice_decision(
            db,
            session_id=session_id,
            user_id=user_id,
            idempotency_key=idempotency_key,
        )
        if replay:
            _validate_choice_replay(
                replay,
                checkpoint_node_id=checkpoint_node_id,
                option_key=option_key,
            )
            return replay, False
        raise HTTPException(status_code=409, detail={"code": "session.concurrent_choice"})

    decision = ChoiceDecision(
        session_id=reading.id,
        checkpoint_node_id=checkpoint_node_id,
        option_key=option_key,
        candidate_id=candidate.id,
        idempotency_key=idempotency_key,
        previous_node_id=checkpoint_node_id,
        result_node_id=preview_node_id,
        previous_snapshot_id=previous_snapshot.id if previous_snapshot else None,
        result_snapshot_id=result_snapshot.id,
        result_revision_id=frozen_candidate.get("preview_revision_id"),
    )
    db.add(decision)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail={"code": "choice.idempotency_conflict"}) from exc
    db.add(
        OutboxEvent(
            aggregate_type="choice_decision",
            aggregate_id=decision.id,
            event_type="branch.complete_selected",
            payload={
                "decision_id": decision.id,
                "session_id": reading.id,
                "candidate_id": candidate.id,
                "preview_revision_id": frozen_candidate.get("preview_revision_id"),
            },
        )
    )
    return decision, True


def undo_latest_choice(db: Session, *, session_id: str, user_id: int, expected_lock_version: int) -> ChoiceDecision:
    reading = get_owned_session(db, session_id, user_id, lock=True)
    if reading.lock_version != expected_lock_version:
        raise HTTPException(status_code=409, detail={"code": "session.version_conflict", "current": reading.lock_version})
    decision = (
        db.query(ChoiceDecision)
        .filter(ChoiceDecision.session_id == session_id, ChoiceDecision.undone_at.is_(None))
        .order_by(ChoiceDecision.created_at.desc(), ChoiceDecision.id.desc())
        .first()
    )
    if not decision or reading.head_node_id != decision.result_node_id:
        raise HTTPException(status_code=409, detail="当前节点无法直接撤销")
    reading.head_node_id = decision.previous_node_id
    reading.state_snapshot_id = decision.previous_snapshot_id
    reading.lock_version += 1
    decision.undone_at = utcnow()
    return decision


def fork_session(
    db: Session,
    *,
    session_id: str,
    user_id: int,
    decision_id: str | None = None,
) -> ReadingSession:
    source = get_owned_session(db, session_id, user_id)
    node_id = source.head_node_id
    snapshot_id = source.state_snapshot_id
    if decision_id:
        decision = (
            db.query(ChoiceDecision)
            .filter(ChoiceDecision.id == decision_id, ChoiceDecision.session_id == source.id)
            .first()
        )
        if not decision:
            raise HTTPException(status_code=404, detail="选择记录不存在")
        node_id = decision.previous_node_id
        snapshot_id = decision.previous_snapshot_id
    forked = ReadingSession(
        user_id=user_id,
        project_id=source.project_id,
        release_id=source.release_id,
        head_node_id=node_id,
        state_snapshot_id=snapshot_id,
        parent_session_id=source.id,
        forked_from_decision_id=decision_id,
    )
    db.add(forked)
    db.flush()
    return forked


def add_bookmark(db: Session, *, session_id: str, user_id: int, node_id: str, label: str | None) -> ReadingBookmark:
    reading = get_owned_session(db, session_id, user_id)
    release = _active_release_or_404(
        db,
        release_id=reading.release_id,
        project_id=reading.project_id,
    )
    if not _release_has_node(release, node_id):
        raise HTTPException(status_code=404, detail="节点不存在")
    node = db.query(StoryNode).filter(StoryNode.id == node_id, StoryNode.project_id == reading.project_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="节点不存在")
    existing = (
        db.query(ReadingBookmark)
        .filter(ReadingBookmark.session_id == session_id, ReadingBookmark.node_id == node_id)
        .first()
    )
    if existing:
        existing.label = label
        return existing
    bookmark = ReadingBookmark(session_id=session_id, node_id=node_id, label=label)
    db.add(bookmark)
    db.flush()
    return bookmark
