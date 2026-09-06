from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.chapter_script_service import (
    CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION,
)
from app.application.story_branch_service import (
    CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
)
from app.application.story_chapter_batch_service import (
    STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION,
)
from app.application.story_chapter_service import CHAPTER_SOURCE_SCHEMA_VERSION
from app.application.story_outline_service import OUTLINE_SOURCE_SCHEMA_VERSION
from app.application.story_path_task_compatibility import (
    STORY_PATH_TASK_SOURCE_CONTRACTS,
)
from app.application.task_service import (
    claim_task,
    create_generation_task,
    fail_task,
    lease_from_task,
    retry_task,
)
from app.models import Project, User
from app.models_v2 import GenerationTask, OutboxEvent, UsageReservation
from app.orm_base import Base
from app.services.vn_graph_compiler import VNGRAPH_BINDING_MANIFEST_VERSION
from scripts.check_story_path_cutover import (
    EXPECTED_ALEMBIC_REVISION,
    audit_story_path_cutover,
)


@pytest.fixture()
def cutover_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": EXPECTED_ALEMBIC_REVISION},
        )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield engine, session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _owner_and_project(db):
    user = User(
        email="cutover@example.com",
        password_hash="hash",
        display_name="Cutover",
        quota_total=100,
        quota_daily=100,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Cutover",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    return user, project


def _chapter_source_refs():
    return {
        "chapter_source": {"schema_version": CHAPTER_SOURCE_SCHEMA_VERSION},
    }


def test_cutover_contract_versions_match_authoring_services():
    expected = {
        "outline.generate": OUTLINE_SOURCE_SCHEMA_VERSION,
        "chapter.generate": CHAPTER_SOURCE_SCHEMA_VERSION,
        "chapter.batch": STORY_PATH_CHAPTER_BATCH_SOURCE_VERSION,
        "branch.candidates.generate": CANDIDATE_SET_SOURCE_SCHEMA_VERSION,
        "chapter_script.generate": CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION,
        "vngraph.compile": VNGRAPH_BINDING_MANIFEST_VERSION,
    }
    assert {
        kind: contract.expected_version
        for kind, contract in STORY_PATH_TASK_SOURCE_CONTRACTS.items()
    } == expected


def test_cutover_audit_passes_at_head_with_an_empty_queue(cutover_db):
    engine, _db = cutover_db
    report = audit_story_path_cutover(engine)

    assert report["ok"] is True
    assert report["database_revision"]["actual"] == EXPECTED_ALEMBIC_REVISION
    assert report["active_story_path_tasks"]["count"] == 0
    assert report["open_story_path_outbox_events"]["count"] == 0


def test_cutover_audit_blocks_wrong_database_revision(cutover_db):
    engine, _db = cutover_db
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = '0037_story_path_backfill'")
        )

    report = audit_story_path_cutover(engine)

    assert report["ok"] is False
    assert {item["code"] for item in report["blockers"]} == {
        "database.revision_mismatch"
    }


def test_cutover_audit_blocks_active_tasks_and_open_outbox(cutover_db):
    engine, db = cutover_db
    user, project = _owner_and_project(db)
    legacy, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="legacy-active",
    )
    current, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="current-active",
        source_refs=_chapter_source_refs(),
    )
    current.status = "running"
    db.commit()

    report = audit_story_path_cutover(engine)

    assert report["ok"] is False
    assert report["active_story_path_tasks"]["count"] == 2
    assert report["active_story_path_tasks"]["by_kind"] == {
        "chapter.generate": 2
    }
    assert report["open_story_path_outbox_events"]["count"] == 2
    assert {legacy.id, current.id} == set(
        report["active_story_path_tasks"]["sample_task_ids"]
    )


def test_cutover_audit_blocks_open_outbox_for_terminal_task(cutover_db):
    engine, db = cutover_db
    user, project = _owner_and_project(db)
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="open-outbox",
        source_refs=_chapter_source_refs(),
    )
    task.status = "succeeded"
    event_row = db.query(OutboxEvent).filter_by(aggregate_id=task.id).one()
    event_row.status = "failed"
    db.commit()

    report = audit_story_path_cutover(engine)

    assert report["ok"] is False
    assert report["active_story_path_tasks"]["count"] == 0
    assert report["open_story_path_outbox_events"]["by_status"] == {"failed": 1}
    assert {item["code"] for item in report["blockers"]} == {
        "outbox.open_story_path"
    }


def test_terminal_legacy_task_is_reported_and_cannot_be_retried(cutover_db):
    engine, db = cutover_db
    user, project = _owner_and_project(db)
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="chapter.generate",
        idempotency_key="legacy-terminal",
        estimated_cost=Decimal("2"),
    )
    claimed = claim_task(db, task.id, "worker-before-cutover")
    assert claimed is not None
    lease = lease_from_task(claimed)
    fail_task(
        db,
        task,
        error_code="legacy_failed",
        safe_detail="legacy task failed",
        retryable=False,
        lease=lease,
    )
    db.query(OutboxEvent).filter_by(aggregate_id=task.id).update(
        {"status": "published"}
    )
    db.commit()

    report = audit_story_path_cutover(engine)

    assert report["ok"] is True
    assert report["terminal_legacy_tasks"]["count"] == 1
    assert report["warnings"][0]["code"] == "tasks.terminal_legacy_source"
    reservation = db.query(UsageReservation).filter_by(task_id=task.id).one()
    outbox_count = db.query(OutboxEvent).count()
    with pytest.raises(HTTPException) as exc:
        retry_task(db, db.query(GenerationTask).filter_by(id=task.id).one())
    assert exc.value.status_code == 409
    assert "旧版创作接口" in exc.value.detail
    assert task.status == "failed"
    assert reservation.status == "released"
    assert db.query(OutboxEvent).count() == outbox_count
    assert user.quota_used_total == 0
