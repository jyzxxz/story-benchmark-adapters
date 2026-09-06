from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.hashing import content_hash
from app.application.public_release_service import (
    get_active_project_release,
    get_active_public_release,
    get_public_path_chapter_manifest,
    get_public_path_chapter_vn_graph,
    get_public_release_manifest,
    list_active_public_releases,
)
from app.application.publication_readiness_service import (
    calculate_publication_readiness,
)
from app.application.publication_service import publish_project, unpublish_project
from app.application.storage_service import (
    can_read_storage_object,
    is_referenced_by_published_release,
)
from app.core.errors import AppError
from app.database import Base
from app.models_v2 import (
    ProjectPublication,
    ProjectRelease,
    StorageObject,
    VNGraphRevision,
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


def _publish(db, seeded: dict, key: str) -> ProjectRelease:
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


def _assert_public_not_found(call) -> None:
    with pytest.raises(AppError) as captured:
        call()
    assert captured.value.code == "public.release_not_found"
    assert captured.value.status_code == 404


def test_public_reads_resolve_the_active_release_and_stable_path_chapter(db):
    seeded = _seed_ready_project(db, with_resource=True)
    release = _publish(db, seeded, "public-v1")

    assert list_active_public_releases(db) == [release]
    assert get_active_project_release(
        db,
        project_id=seeded["project"].id,
    ).id == release.id
    assert get_active_public_release(db, release_id=release.id).id == release.id

    manifest = get_public_release_manifest(db, release_id=release.id)
    assert manifest.release_id == release.id
    assert manifest.version == 1
    assert manifest.manifest_hash == release.manifest_hash
    assert manifest.manifest == release.manifest_json
    assert manifest.manifest is not release.manifest_json

    seeded["project"].title = "Unpublished authoring title"
    db.flush()
    still_public = get_public_release_manifest(db, release_id=release.id)
    assert still_public.manifest["project"]["title"] == "Frozen routes"

    chapter = get_public_path_chapter_manifest(
        db,
        release_id=release.id,
        path_chapter_id=seeded["placement"].id,
    )
    assert chapter["story_path_id"] == seeded["path"].id
    assert chapter["path_chapter_id"] == seeded["placement"].id
    assert chapter["chapter_revision"]["id"] == seeded["chapter"].id

    graph = get_public_path_chapter_vn_graph(
        db,
        release_id=release.id,
        path_chapter_id=seeded["placement"].id,
    )
    assert graph.release_id == release.id
    assert graph.revision_id == seeded["graph"].id
    assert graph.graph_hash == seeded["graph"].graph_hash
    assert graph.graph_json == seeded["graph"].graph_json
    graph.graph_json["Nodes"].append({"id": "caller-local"})
    assert seeded["graph"].graph_json["Nodes"] == []

    _assert_public_not_found(
        lambda: get_public_path_chapter_manifest(
            db,
            release_id=release.id,
            path_chapter_id=str(uuid4()),
        )
    )

    db.execute(
        update(VNGraphRevision)
        .where(VNGraphRevision.id == seeded["graph"].id)
        .values(graph_json={"Nodes": [{"id": "tampered"}]})
    )
    db.expire(seeded["graph"])
    _assert_public_not_found(
        lambda: get_public_path_chapter_vn_graph(
            db,
            release_id=release.id,
            path_chapter_id=seeded["placement"].id,
        )
    )


def test_public_reads_reject_a_nonactive_published_release(db):
    seeded = _seed_ready_project(db)
    first = _publish(db, seeded, "active-v1")
    db.commit()

    seeded["project"].title = "Frozen routes v2"
    db.commit()
    second = _publish(db, seeded, "active-v2")

    first.status = "published"
    db.flush()
    assert list_active_public_releases(db) == [second]
    _assert_public_not_found(
        lambda: get_active_public_release(db, release_id=first.id)
    )
    _assert_public_not_found(
        lambda: get_public_release_manifest(db, release_id=first.id)
    )
    _assert_public_not_found(
        lambda: get_public_path_chapter_vn_graph(
            db,
            release_id=first.id,
            path_chapter_id=seeded["placement"].id,
        )
    )
    assert get_active_public_release(db, release_id=second.id).id == second.id


def test_unpublish_withdraws_and_revokes_all_public_access(db):
    seeded = _seed_ready_project(db, with_resource=True)
    release = _publish(db, seeded, "unpublish-v1")
    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    legacy_state = (
        seeded["project"].visibility,
        seeded["project"].is_draft,
        seeded["project"].published_at,
    )
    assert publication.lock_version == 2
    stale_version = publication.lock_version
    assert can_read_storage_object(db, seeded["storage"], None) is True

    withdrawn = unpublish_project(
        db,
        project_id=seeded["project"].id,
        expected_lock_version=publication.lock_version,
    )
    assert withdrawn.id == release.id
    assert withdrawn.status == "withdrawn"
    assert withdrawn.withdrawn_at is not None
    assert publication.active_release_id is None
    assert publication.published_at is None
    assert publication.lock_version == 3
    assert (
        seeded["project"].visibility,
        seeded["project"].is_draft,
        seeded["project"].published_at,
    ) == legacy_state
    assert list_active_public_releases(db) == []
    assert is_referenced_by_published_release(db, seeded["storage"]) is False
    assert can_read_storage_object(db, seeded["storage"], None) is False
    _assert_public_not_found(
        lambda: get_active_project_release(
            db,
            project_id=seeded["project"].id,
        )
    )
    _assert_public_not_found(
        lambda: get_active_public_release(db, release_id=release.id)
    )

    with pytest.raises(AppError) as stale:
        unpublish_project(
            db,
            project_id=seeded["project"].id,
            expected_lock_version=stale_version,
        )
    assert stale.value.code == "publication.version_conflict"
    assert stale.value.status_code == 409

    with pytest.raises(AppError) as absent:
        unpublish_project(
            db,
            project_id=seeded["project"].id,
            expected_lock_version=publication.lock_version,
        )
    assert absent.value.code == "release.active_not_found"


def test_unpublish_rejects_stale_version_without_mutation(db):
    seeded = _seed_ready_project(db)
    release = _publish(db, seeded, "stale-unpublish")
    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()

    with pytest.raises(AppError) as stale:
        unpublish_project(
            db,
            project_id=seeded["project"].id,
            expected_lock_version=publication.lock_version - 1,
        )
    assert stale.value.code == "publication.version_conflict"
    assert stale.value.details["current_lock_version"] == publication.lock_version
    assert release.status == "published"
    assert release.withdrawn_at is None
    assert publication.active_release_id == release.id

    with pytest.raises(AppError) as invalid:
        unpublish_project(
            db,
            project_id=seeded["project"].id,
            expected_lock_version=True,
        )
    assert invalid.value.code == "precondition.invalid"


def test_unpublish_failure_rolls_back_release_pointer_and_lock(db, monkeypatch):
    seeded = _seed_ready_project(db)
    release = _publish(db, seeded, "rollback-unpublish")
    db.commit()
    publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    expected_version = publication.lock_version
    original_flush = db.flush
    flush_count = 0

    def fail_after_flush(*args, **kwargs):
        nonlocal flush_count
        original_flush(*args, **kwargs)
        flush_count += 1
        if flush_count == 2:
            raise RuntimeError("simulated unpublish failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(db, "flush", fail_after_flush)
        with pytest.raises(RuntimeError, match="simulated unpublish failure"):
            unpublish_project(
                db,
                project_id=seeded["project"].id,
                expected_lock_version=expected_version,
            )

    db.expire_all()
    persisted_release = db.query(ProjectRelease).filter_by(id=release.id).one()
    persisted_publication = db.query(ProjectPublication).filter_by(
        project_id=seeded["project"].id
    ).one()
    assert persisted_release.status == "published"
    assert persisted_release.withdrawn_at is None
    assert persisted_publication.active_release_id == release.id
    assert persisted_publication.published_at is not None
    assert persisted_publication.lock_version == expected_version


def test_public_media_requires_an_intact_active_storage_manifest(db):
    seeded = _seed_ready_project(db)
    project = seeded["project"]
    user = seeded["user"]
    shared = StorageObject(
        owner_id=user.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"library/{uuid4()}.png",
        media_type="image/png",
        byte_size=12,
        sha256="a" * 64,
        visibility="private",
        status="active",
    )
    sensitive = StorageObject(
        owner_id=user.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"voice-reference/{uuid4()}.wav",
        media_type="audio/wav",
        byte_size=12,
        sha256="b" * 64,
        visibility="private",
        status="active",
    )
    incidental = StorageObject(
        owner_id=user.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"library/{uuid4()}.png",
        media_type="image/png",
        byte_size=12,
        sha256="c" * 64,
        visibility="release",
        status="active",
    )
    db.add_all([shared, sensitive, incidental])
    db.flush()
    manifest = {
        "schema_version": "story-path-release-v1",
        "storage_object_ids": [shared.id, sensitive.id],
        "description": f"This is not a resource reference: {incidental.id}",
    }
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        manifest_json=manifest,
        manifest_hash=content_hash(manifest),
        authoring_fingerprint="d" * 64,
        created_by=user.id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    db.add(
        ProjectPublication(
            project_id=project.id,
            active_release_id=release.id,
            published_at=release.published_at,
            lock_version=1,
        )
    )
    db.flush()

    assert is_referenced_by_published_release(db, shared) is True
    assert can_read_storage_object(db, shared, None) is True
    assert is_referenced_by_published_release(db, sensitive) is True
    assert can_read_storage_object(db, sensitive, None) is False
    assert is_referenced_by_published_release(db, incidental) is False
    assert can_read_storage_object(db, incidental, None) is False

    publication = db.query(ProjectPublication).filter_by(project_id=project.id).one()
    publication.active_release_id = None
    publication.published_at = None
    publication.lock_version += 1
    db.flush()
    assert is_referenced_by_published_release(db, shared) is False
    assert can_read_storage_object(db, shared, None) is False
    publication.active_release_id = release.id
    publication.published_at = release.published_at
    publication.lock_version += 1
    db.flush()
    assert is_referenced_by_published_release(db, shared) is True

    release.manifest_json = {**manifest, "corrupt": True}
    db.flush()
    assert list_active_public_releases(db) == []
    assert is_referenced_by_published_release(db, shared) is False
    assert can_read_storage_object(db, shared, None) is False
    _assert_public_not_found(
        lambda: get_active_public_release(db, release_id=release.id)
    )


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (101, 0), (20, -1)],
)
def test_public_catalog_rejects_invalid_pagination(db, limit, offset):
    with pytest.raises(AppError) as captured:
        list_active_public_releases(db, limit=limit, offset=offset)
    assert captured.value.code == "pagination.invalid"
    assert captured.value.status_code == 422


def test_public_catalog_applies_offset_after_manifest_integrity_filter(db):
    oldest = _seed_ready_project(db)
    oldest_release = _publish(db, oldest, "catalog-oldest")
    db.commit()
    corrupt = _seed_ready_project(db)
    corrupt_release = _publish(db, corrupt, "catalog-corrupt")
    db.commit()
    newest = _seed_ready_project(db)
    newest_release = _publish(db, newest, "catalog-newest")
    db.commit()

    corrupt_release.manifest_json = {
        **corrupt_release.manifest_json,
        "corrupt": True,
    }
    db.flush()

    assert list_active_public_releases(db, limit=1, offset=0) == [newest_release]
    assert list_active_public_releases(db, limit=1, offset=1) == [oldest_release]
