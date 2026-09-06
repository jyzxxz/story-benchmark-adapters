"""Sanitization regression for ``voice_tasks._record_render_failure``.

The legacy code replaced every provider error with a fixed Chinese string,
hiding real auth/quota/network causes from the user. The new path runs the
raw provider exception through :func:`sanitize_detail` so the actual class +
message (stripped of ``sk-*``, ``Bearer``, IPv4, etc.) lands in
``GenerationTask.error_detail``.

This file is intentionally self-contained: no project-wide conftest, no
FastAPI app — just sqlite in-memory + the minimum models needed to call
``_record_render_failure``.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base  # noqa: E402
from app.application.task_service import claim_task, lease_from_task  # noqa: E402
from app.models import Project, User  # noqa: E402
from app.models_v2 import GenerationTask  # noqa: E402
from app.workers.voice_tasks import _record_render_failure  # noqa: E402


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory


def _seed_render_task(db):
    """Seed a minimal ``tts.render`` task without VoiceLine/Chapter.

    Passing ``line_id=None`` exercises the same code path the worker uses when
    ``task.source_refs`` carries no ``voice_line_id`` — ``mark_line_failed`` is
    skipped, ``fail_task`` still persists ``error_detail``.
    """
    user = User(
        email="voice-sanitize@example.com",
        password_hash="hash",
        display_name="sanitize owner",
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="sanitize project",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()

    task_id = str(uuid.uuid4())
    task = GenerationTask(
        id=task_id,
        user_id=user.id,
        project_id=project.id,
        kind="tts.render",
        status="running",
        stage="tts",
        progress=0.0,
        idempotency_key=f"sanitize-{task_id}",
        source_refs={},  # no voice_line_id → mark_line_failed skipped
        result_refs={},
        parameters={},
        parameters_hash="0" * 64,
        attempt=1,
        max_attempts=1,
    )
    db.add(task)
    db.flush()
    return task_id


def test_record_render_failure_sanitizes_provider_error(session_factory):
    """Provider error containing token + IP + email → masked in error_detail.

    Regression guard: the previous implementation wrote a fixed Chinese string
    for ``tts.render_failed`` so the user couldn't see what actually broke.
    The new ``sanitize_detail`` strips credentials while preserving the
    exception class + message for diagnosis.
    """
    db = session_factory()
    try:
        task_id = _seed_render_task(db)
        claimed = claim_task(db, task_id, "voice-sanitization-test")
        assert claimed is not None
        lease = lease_from_task(claimed)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    secret_payload = (
        "RuntimeError: aliyun auth failed Bearer sk-abc123456789012345 "
        "from 10.0.0.5 / LTAI4GhExampleKey12 / admin@example.com"
    )
    # Mirror the live worker's failure path: compute safe_detail via
    # sanitize_detail, then hand it to _record_render_failure.
    from app.services.generation_report import sanitize_detail

    safe = sanitize_detail(secret_payload)
    assert safe, "sanitize_detail should return non-empty for non-empty input"
    _record_render_failure(
        task_id,
        session_factory=session_factory,
        error_code="tts.render_failed",
        safe_detail=safe,
        lease=lease,
        line_id=None,
    )

    verify = session_factory()
    try:
        task = verify.query(GenerationTask).filter(GenerationTask.id == task_id).one()
        detail = task.error_detail or ""
        # The structure of the exception class+message is preserved (sanitized)…
        assert "RuntimeError" in detail
        assert "aliyun auth failed" in detail
        # …but every secret shape is masked out.
        assert "sk-abc123456789012345" not in detail
        # "Bearer" prefix survives but the token itself is replaced with ***
        assert "Bearer sk-abc" not in detail
        assert "sk-***" in detail
        assert "10.0.0.5" not in detail
        assert "LTAI4GhExampleKey12" not in detail
        assert "admin@example.com" not in detail
        # The fixed Chinese string is gone (regression: legacy wrote that one).
        assert "单条语音合成失败" not in detail
    finally:
        verify.close()
