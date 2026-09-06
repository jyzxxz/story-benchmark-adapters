from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.project_state_projection_service import (
    calculate_project_state_projection,
    derive_authoring_stage,
)
from app.application.publication_readiness_service import (
    PublicationBlockingItem,
    calculate_publication_readiness,
)
from app.application.publication_service import publish_project, unpublish_project
from app.core.errors import AppError
from app.database import Base
from app.models import Project, User
from app.models_v2 import (
    GenerationTask,
    ProjectContentHead,
    ProjectPublication,
    VNGraphHead,
)
from test_publication_readiness import _seed_ready_project


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _publish(db, seeded: dict, key: str = "projection-v1"):
    readiness = calculate_publication_readiness(
        db,
        project_id=seeded["project"].id,
    )
    return publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key=key,
    ).release


def test_unpublished_projection_ignores_legacy_project_lifecycle_fields(db):
    user = User(
        email=f"projection-{uuid4()}@example.com",
        password_hash="hash",
        display_name="Projection owner",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Projection only",
        characters=[],
        story_start="Start",
        story_end="End",
        style="cinematic",
        pace="medium",
        status="completed",
        visibility="public",
        is_draft=False,
        published_at=datetime.now(timezone.utc),
    )
    db.add(project)
    db.flush()

    projection = calculate_project_state_projection(db, project_id=project.id)
    assert projection.authoring.stage == "bible"
    assert projection.authoring.running_task_count == 0
    assert projection.authoring.has_unpublished_changes is True
    assert projection.authoring.blocking_items
    assert projection.publication.state == "unpublished"
    assert projection.publication.active_release_id is None
    assert projection.publication.published_at is None
    assert projection.publication.lock_version == 1
    assert db.query(ProjectPublication).count() == 0
    assert projection.as_dict()["publication"]["state"] == "unpublished"


def test_projection_tracks_publish_edit_revert_and_unpublish(db):
    seeded = _seed_ready_project(db)
    project_id = seeded["project"].id

    initial = calculate_project_state_projection(db, project_id=project_id)
    assert initial.authoring.stage == "ready"
    assert initial.authoring.has_unpublished_changes is True
    assert initial.publication.state == "unpublished"

    release = _publish(db, seeded)
    published = calculate_project_state_projection(db, project_id=project_id)
    assert published.authoring.stage == "ready"
    assert published.authoring.has_unpublished_changes is False
    assert published.publication.state == "published"
    assert published.publication.active_release_id == release.id
    assert published.publication.lock_version == 2
    assert db.query(ProjectContentHead).filter_by(
        project_id=project_id
    ).one().lifecycle_status == "draft"

    seeded["project"].title = "Unpublished title"
    db.flush()
    changed = calculate_project_state_projection(db, project_id=project_id)
    assert changed.authoring.has_unpublished_changes is True
    assert changed.publication.state == "changes_pending"
    assert changed.publication.active_release_id == release.id

    seeded["project"].title = "Frozen routes"
    seeded["project"].visibility = "public"
    seeded["project"].is_draft = False
    seeded["project"].published_at = datetime.now(timezone.utc)
    db.flush()
    reverted = calculate_project_state_projection(db, project_id=project_id)
    assert reverted.authoring.has_unpublished_changes is False
    assert reverted.publication.state == "published"

    withdrawn = unpublish_project(
        db,
        project_id=project_id,
        expected_lock_version=reverted.publication.lock_version,
    )
    unpublished = calculate_project_state_projection(db, project_id=project_id)
    assert withdrawn.id == release.id
    assert unpublished.authoring.has_unpublished_changes is True
    assert unpublished.publication.state == "unpublished"
    assert unpublished.publication.active_release_id is None
    assert unpublished.publication.published_at is None
    assert unpublished.publication.lock_version == 3


def test_projection_counts_only_nonterminal_project_tasks(db):
    seeded = _seed_ready_project(db)
    statuses = ["queued", "running", "partial", "succeeded", "failed", "cancelled"]
    for index, status in enumerate(statuses):
        db.add(
            GenerationTask(
                user_id=seeded["user"].id,
                project_id=seeded["project"].id,
                kind="projection.test",
                status=status,
                idempotency_key=f"projection-task-{index}",
                parameters_hash=f"{index:064x}",
            )
        )
    db.add(
        GenerationTask(
            user_id=seeded["user"].id,
            project_id=None,
            kind="projection.test",
            status="running",
            idempotency_key="projection-other-project",
            parameters_hash="f" * 64,
        )
    )
    db.flush()

    projection = calculate_project_state_projection(
        db,
        project_id=seeded["project"].id,
    )
    assert projection.authoring.running_task_count == 2


def test_blocked_reviewed_state_is_changes_pending_after_publish(db):
    seeded = _seed_ready_project(db)
    release = _publish(db, seeded, "projection-blocked")
    graph_head = db.query(VNGraphHead).filter_by(
        script_revision_id=seeded["script"].id
    ).one()
    graph_head.current_revision_id = None
    db.flush()

    projection = calculate_project_state_projection(
        db,
        project_id=seeded["project"].id,
    )
    assert projection.authoring.stage == "vn_graphs"
    assert projection.authoring.has_unpublished_changes is True
    assert {item.code for item in projection.authoring.blocking_items} == {
        "vngraph.head_missing"
    }
    assert projection.publication.state == "changes_pending"
    assert projection.publication.active_release_id == release.id


def test_projection_rejects_a_corrupt_active_release(db):
    seeded = _seed_ready_project(db)
    release = _publish(db, seeded, "projection-corrupt")
    release.manifest_json = {**release.manifest_json, "corrupt": True}
    db.flush()

    with pytest.raises(AppError) as captured:
        calculate_project_state_projection(
            db,
            project_id=seeded["project"].id,
        )
    assert captured.value.code == "release.active_invalid"
    assert captured.value.status_code == 409


@pytest.mark.parametrize(
    ("codes", "expected"),
    [
        ([], "ready"),
        (["bible.head_missing"], "bible"),
        (["story_path.root_invalid"], "story_paths"),
        (["outline.head_missing"], "outlines"),
        (["chapter.head_missing"], "chapters"),
        (["script.head_missing"], "scripts"),
        (["vngraph.head_missing"], "vn_graphs"),
        (["future_artifact.missing"], "blocked"),
        (["vngraph.head_missing", "outline.head_missing"], "outlines"),
    ],
)
def test_authoring_stage_uses_the_earliest_blocking_layer(codes, expected):
    blockers = tuple(
        PublicationBlockingItem(code=code, detail="test") for code in codes
    )
    assert derive_authoring_stage(blockers) == expected


def test_projection_has_a_stable_not_found_error(db):
    with pytest.raises(AppError) as captured:
        calculate_project_state_projection(db, project_id=999_999)
    assert captured.value.code == "project.not_found"
    assert captured.value.status_code == 404
