from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application import publication_service
from app.application.hashing import content_hash
from app.application.publication_readiness_service import (
    calculate_publication_readiness,
)
from app.application.publication_service import (
    RELEASE_MANIFEST_VERSION,
    publish_project,
)
from app.application.storage_service import is_referenced_by_published_release
from app.core.errors import AppError
from app.database import Base
from app.models_v2 import ProjectPublication, ProjectRelease, VNGraphHead
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


def test_first_publish_is_atomic_and_replay_returns_the_same_release(db):
    seeded = _seed_ready_project(db, with_resource=True)
    readiness = calculate_publication_readiness(db, project_id=seeded["project"].id)

    result = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key="publish-first",
        release_notes="First public version",
    )
    assert result.created is True
    release = result.release
    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    assert release.status == "published"
    assert release.version == 1
    assert release.authoring_fingerprint == readiness.authoring_fingerprint
    assert release.publication_idempotency_key == "publish-first"
    assert release.publication_request_hash
    assert release.published_at.replace(tzinfo=None) == publication.published_at.replace(
        tzinfo=None
    )
    assert publication.active_release_id == release.id
    assert publication.lock_version == 2
    assert release.manifest_json["schema_version"] == RELEASE_MANIFEST_VERSION
    assert release.manifest_json["authoring_fingerprint"] == readiness.authoring_fingerprint
    assert release.manifest_hash == content_hash(release.manifest_json)
    assert "extra_requirements" not in release.manifest_json["project"]
    assert release.manifest_json["authoring_metadata_hash"] == content_hash(
        readiness.authoring_snapshot["project"]
    )
    chapter = release.manifest_json["story_paths"][0]["chapters"][0]
    assert chapter["path_chapter_id"] == seeded["placement"].id
    assert chapter["vn_graph_revision"]["id"] == seeded["graph"].id
    assert chapter["vn_graph_revision"]["binding_manifest"] == seeded[
        "graph"
    ].binding_manifest
    assert release.manifest_json["storage_object_ids"] == [seeded["storage"].id]
    assert is_referenced_by_published_release(db, seeded["storage"]) is True
    assert seeded["project"].visibility == "private"
    assert seeded["project"].is_draft is True

    db.commit()
    replay = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key=" publish-first ",
        release_notes="First public version",
    )
    assert replay.created is False
    assert replay.release.id == release.id
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 1

    with pytest.raises(AppError) as conflict:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=readiness.authoring_fingerprint,
            idempotency_key="publish-first",
            release_notes="Different request",
        )
    assert conflict.value.code == "idempotency_key.conflict"

    with pytest.raises(AppError) as no_changes:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=readiness.authoring_fingerprint,
            idempotency_key="publish-second",
        )
    assert no_changes.value.code == "release.no_changes"


def test_new_release_supersedes_the_previous_active_release_atomically(db):
    seeded = _seed_ready_project(db)
    first_readiness = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    first = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=first_readiness.authoring_fingerprint,
        idempotency_key="publish-v1",
    ).release
    db.commit()

    seeded["project"].title = "Frozen routes v2"
    db.commit()
    second_readiness = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    second = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=second_readiness.authoring_fingerprint,
        idempotency_key="publish-v2",
        release_notes="Superseding version",
    ).release

    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    assert first.status == "superseded"
    assert first.withdrawn_at is None
    assert second.status == "published"
    assert second.version == 2
    assert second.authoring_fingerprint == second_readiness.authoring_fingerprint
    assert second.manifest_hash != first.manifest_hash
    assert publication.active_release_id == second.id
    assert publication.lock_version == 3
    assert db.query(ProjectRelease).filter_by(status="published").all() == [second]

    replay = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=first_readiness.authoring_fingerprint,
        idempotency_key="publish-v1",
    )
    assert replay.created is False
    assert replay.release.id == first.id
    assert replay.release.status == "superseded"
    assert publication.active_release_id == second.id


def test_supersede_failure_rolls_back_old_status_new_release_and_pointer(db, monkeypatch):
    seeded = _seed_ready_project(db)
    first_readiness = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    first = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=first_readiness.authoring_fingerprint,
        idempotency_key="rollback-v1",
    ).release
    db.commit()
    seeded["project"].title = "A change that must roll back"
    db.commit()
    second_readiness = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )

    def fail_after_supersede(*, publication, release, previous_release):
        previous_release.status = "superseded"
        publication.active_release_id = release.id
        db.flush()
        raise RuntimeError("simulated pointer failure")

    monkeypatch.setattr(publication_service, "_activate_release", fail_after_supersede)
    with pytest.raises(RuntimeError, match="simulated pointer failure"):
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=second_readiness.authoring_fingerprint,
            idempotency_key="rollback-v2",
        )

    db.expire_all()
    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    persisted_first = db.query(ProjectRelease).filter_by(id=first.id).one()
    assert persisted_first.status == "published"
    assert publication.active_release_id == first.id
    assert publication.lock_version == 2
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 1


def test_active_release_cannot_be_superseded_before_pointer_moves(db):
    seeded = _seed_ready_project(db)
    readiness = calculate_publication_readiness(db, project_id=seeded["project"].id)
    active = publish_project(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key="invalid-active-v1",
    ).release
    db.commit()
    active.status = "superseded"
    with pytest.raises(RuntimeError, match="must be unpublished"):
        db.commit()
    db.rollback()
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 1


def test_stale_or_blocked_source_creates_no_release_or_publication(db):
    seeded = _seed_ready_project(db)

    with pytest.raises(AppError) as stale:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint="0" * 64,
            idempotency_key="stale-source",
        )
    assert stale.value.code == "release.source_changed"
    assert stale.value.details["current_authoring_fingerprint"]
    assert db.query(ProjectRelease).count() == 0
    assert db.query(ProjectPublication).count() == 0

    graph_head = db.query(VNGraphHead).filter_by(
        script_revision_id=seeded["script"].id
    ).one()
    graph_head.current_revision_id = None
    db.flush()
    blocked = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert blocked.ready is False
    with pytest.raises(AppError) as not_ready:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=blocked.authoring_fingerprint,
            idempotency_key="blocked-source",
        )
    assert not_ready.value.code == "release.not_ready"
    assert not_ready.value.status_code == 422
    assert not_ready.value.details["blocking_items"][0]["code"] == "vngraph.head_missing"
    assert db.query(ProjectRelease).count() == 0
    assert db.query(ProjectPublication).count() == 0


def test_source_change_between_read_and_lock_rolls_back_the_publish(db, monkeypatch):
    seeded = _seed_ready_project(db)
    readiness = calculate_publication_readiness(db, project_id=seeded["project"].id)
    original = publication_service.calculate_publication_readiness
    calls = 0

    def changing_readiness(session, *, project_id):
        nonlocal calls
        calls += 1
        result = original(session, project_id=project_id)
        if calls == 1:
            seeded["project"].title = "Changed between readiness reads"
            session.flush()
        return result

    monkeypatch.setattr(
        publication_service,
        "calculate_publication_readiness",
        changing_readiness,
    )
    with pytest.raises(AppError) as changed:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=readiness.authoring_fingerprint,
            idempotency_key="racing-source",
        )
    assert changed.value.code == "release.source_changed"
    assert calls == 2
    assert db.query(ProjectRelease).count() == 0
    assert db.query(ProjectPublication).count() == 0
    db.refresh(seeded["project"])
    assert seeded["project"].title == "Frozen routes"


@pytest.mark.parametrize(
    ("fingerprint", "key", "notes", "expected_code"),
    [
        ("ABC", "publish", None, "release.fingerprint_invalid"),
        ("0" * 64, "", None, "idempotency_key.invalid"),
        ("0" * 64, "publish", "x" * 2001, "release.notes_invalid"),
    ],
)
def test_publish_command_validates_its_direct_service_inputs(
    db,
    fingerprint,
    key,
    notes,
    expected_code,
):
    seeded = _seed_ready_project(db)
    with pytest.raises(AppError) as captured:
        publish_project(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=fingerprint,
            idempotency_key=key,
            release_notes=notes,
        )
    assert captured.value.code == expected_code
    assert db.query(ProjectRelease).count() == 0
