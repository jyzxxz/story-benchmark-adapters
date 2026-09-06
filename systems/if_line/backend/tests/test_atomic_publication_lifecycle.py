from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import os
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.application import publication_service
from app.application.hashing import content_hash
from app.application.project_state_projection_service import (
    calculate_project_state_projection,
)
from app.application.public_release_service import (
    get_active_public_release,
    get_public_path_chapter_vn_graph,
    get_public_release_manifest,
    list_active_public_releases,
)
from app.application.publication_readiness_service import (
    calculate_publication_readiness,
)
from app.application.publication_service import publish_project, unpublish_project
from app.application.storage_service import is_referenced_by_published_release
from app.core.errors import AppError
from app.database import Base
from app.models import Project
from app.models_v2 import (
    ChapterRevision,
    ProjectPublication,
    ProjectRelease,
    StoryPath,
)
from test_publication_readiness import _seed_ready_project


@pytest.fixture()
def lifecycle(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'publication-lifecycle.db'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    seeded = _seed_ready_project(db, with_resource=True)
    try:
        yield db, factory, seeded
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def postgres_lifecycle():
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("requires explicit TEST_DATABASE_URL")

    database_url_value = make_url(database_url)
    if database_url_value.drivername in {"postgres", "postgresql"}:
        database_url_value = database_url_value.set(drivername="postgresql+psycopg")
    admin_engine = create_engine(database_url_value)
    if admin_engine.dialect.name != "postgresql":
        admin_engine.dispose()
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")

    schema = f"test_publication_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_engine = create_engine(
        database_url_value,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    setup = None
    try:
        Base.metadata.create_all(isolated_engine)
        factory = sessionmaker(
            bind=isolated_engine,
            autoflush=False,
            expire_on_commit=False,
        )
        setup = factory()
        seeded = _seed_ready_project(setup)
        setup.commit()
        yield factory, seeded
    finally:
        if setup is not None:
            setup.rollback()
            setup.close()
        isolated_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def _publish(db, seeded: dict, *, key: str) -> ProjectRelease:
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


def test_published_project_remains_frozen_during_continued_authoring(lifecycle):
    db, _factory, seeded = lifecycle
    release = _publish(db, seeded, key="continued-authoring-v1")
    db.commit()
    frozen_manifest = get_public_release_manifest(
        db,
        release_id=release.id,
    ).manifest
    frozen_graph = get_public_path_chapter_vn_graph(
        db,
        release_id=release.id,
        path_chapter_id=seeded["placement"].id,
    ).graph_json

    revised_text = "A turns away from the station and follows another route."
    attempt = ChapterRevision(
        project_id=seeded["project"].id,
        chapter_slot_id=seeded["slot"].id,
        created_for_story_path_id=seeded["path"].id,
        chapter_index=1,
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        state_snapshot_id=seeded["state"].id,
        parent_revision_id=seeded["chapter"].id,
        revision_no=2,
        source_hash=seeded["chapter"].source_hash,
        context_manifest=deepcopy(seeded["chapter"].context_manifest),
        context_hash=seeded["chapter"].context_hash,
        content_hash=content_hash(revised_text),
        content=revised_text,
        status="ready",
        created_by=seeded["user"].id,
    )
    db.add(attempt)
    db.flush()

    unselected = calculate_project_state_projection(
        db,
        project_id=seeded["project"].id,
    )
    assert unselected.publication.state == "published"
    assert unselected.authoring.has_unpublished_changes is False
    assert get_public_release_manifest(db, release_id=release.id).manifest == frozen_manifest

    seeded["placement"].current_revision_id = attempt.id
    seeded["placement"].lock_version += 1
    db.flush()
    selected = calculate_project_state_projection(
        db,
        project_id=seeded["project"].id,
    )

    assert selected.publication.state == "changes_pending"
    assert selected.authoring.has_unpublished_changes is True
    assert selected.authoring.stage == "scripts"
    assert get_active_public_release(db, release_id=release.id).id == release.id
    assert get_public_release_manifest(db, release_id=release.id).manifest == frozen_manifest
    assert get_public_path_chapter_vn_graph(
        db,
        release_id=release.id,
        path_chapter_id=seeded["placement"].id,
    ).graph_json == frozen_graph
    assert is_referenced_by_published_release(db, seeded["storage"]) is True
    assert db.query(StoryPath).filter_by(project_id=seeded["project"].id).count() == 1
    assert release.status == "published"


@pytest.mark.parametrize("reuse_key", [False, True])
def test_two_stale_clients_create_only_one_first_release(lifecycle, reuse_key):
    db, factory, seeded = lifecycle
    project_id = seeded["project"].id
    user_id = seeded["user"].id
    winner_readiness = calculate_publication_readiness(db, project_id=project_id)
    db.commit()
    other = factory()
    try:
        loser_readiness = calculate_publication_readiness(other, project_id=project_id)
        assert loser_readiness.authoring_fingerprint == (
            winner_readiness.authoring_fingerprint
        )
        other.commit()

        winner = publish_project(
            db,
            project_id=project_id,
            user_id=user_id,
            expected_authoring_fingerprint=winner_readiness.authoring_fingerprint,
            idempotency_key="concurrent-first-winner",
        )
        db.commit()

        if reuse_key:
            replay = publish_project(
                other,
                project_id=project_id,
                user_id=user_id,
                expected_authoring_fingerprint=loser_readiness.authoring_fingerprint,
                idempotency_key="concurrent-first-winner",
            )
            assert replay.created is False
            assert replay.release.id == winner.release.id
        else:
            with pytest.raises(AppError) as loser:
                publish_project(
                    other,
                    project_id=project_id,
                    user_id=user_id,
                    expected_authoring_fingerprint=(
                        loser_readiness.authoring_fingerprint
                    ),
                    idempotency_key="concurrent-first-loser",
                )
            assert loser.value.code == "release.no_changes"

        publication = other.query(ProjectPublication).filter_by(
            project_id=project_id
        ).one()
        assert other.query(ProjectRelease).filter_by(project_id=project_id).count() == 1
        assert publication.active_release_id == winner.release.id
        assert publication.lock_version == 2
    finally:
        other.rollback()
        other.close()


def test_two_stale_clients_create_only_one_superseding_release(lifecycle):
    db, factory, seeded = lifecycle
    project_id = seeded["project"].id
    user_id = seeded["user"].id
    first = _publish(db, seeded, key="concurrent-supersede-v1")
    db.commit()
    seeded["project"].title = "Concurrent v2"
    db.commit()

    winner_readiness = calculate_publication_readiness(db, project_id=project_id)
    db.commit()
    other = factory()
    try:
        loser_readiness = calculate_publication_readiness(other, project_id=project_id)
        assert loser_readiness.authoring_fingerprint == (
            winner_readiness.authoring_fingerprint
        )
        other.commit()

        second = publish_project(
            db,
            project_id=project_id,
            user_id=user_id,
            expected_authoring_fingerprint=winner_readiness.authoring_fingerprint,
            idempotency_key="concurrent-supersede-winner",
        ).release
        db.commit()

        with pytest.raises(AppError) as loser:
            publish_project(
                other,
                project_id=project_id,
                user_id=user_id,
                expected_authoring_fingerprint=loser_readiness.authoring_fingerprint,
                idempotency_key="concurrent-supersede-loser",
            )
        assert loser.value.code == "release.no_changes"

        other.expire_all()
        publication = other.query(ProjectPublication).filter_by(
            project_id=project_id
        ).one()
        persisted_first = other.query(ProjectRelease).filter_by(id=first.id).one()
        persisted_second = other.query(ProjectRelease).filter_by(id=second.id).one()
        assert persisted_first.status == "superseded"
        assert persisted_second.status == "published"
        assert publication.active_release_id == second.id
        assert publication.lock_version == 3
        assert other.query(ProjectRelease).filter_by(project_id=project_id).count() == 2
        assert [item.id for item in list_active_public_releases(other)] == [second.id]
    finally:
        other.rollback()
        other.close()


def test_failed_first_activation_rolls_back_every_publication_side_effect(
    lifecycle,
    monkeypatch,
):
    db, _factory, seeded = lifecycle
    project_id = seeded["project"].id
    readiness = calculate_publication_readiness(db, project_id=project_id)
    legacy_state = (
        seeded["project"].visibility,
        seeded["project"].is_draft,
        seeded["project"].published_at,
    )

    def fail_after_activation(*, publication, release, previous_release):
        assert previous_release is None
        publication.active_release_id = release.id
        publication.published_at = release.published_at
        publication.lock_version += 1
        db.flush()
        raise RuntimeError("simulated first activation failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(publication_service, "_activate_release", fail_after_activation)
        with pytest.raises(RuntimeError, match="simulated first activation failure"):
            publish_project(
                db,
                project_id=project_id,
                user_id=seeded["user"].id,
                expected_authoring_fingerprint=readiness.authoring_fingerprint,
                idempotency_key="rollback-first-activation",
            )

    db.expire_all()
    assert db.query(ProjectRelease).filter_by(project_id=project_id).count() == 0
    assert db.query(ProjectPublication).filter_by(project_id=project_id).count() == 0
    persisted_project = db.query(Project).filter_by(id=project_id).one()
    assert (
        persisted_project.visibility,
        persisted_project.is_draft,
        persisted_project.published_at,
    ) == legacy_state

    retry = publish_project(
        db,
        project_id=project_id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key="rollback-first-activation",
    )
    assert retry.created is True
    assert retry.release.status == "published"


def test_failed_supersede_keeps_old_release_public_and_retryable(
    lifecycle,
    monkeypatch,
):
    db, _factory, seeded = lifecycle
    project_id = seeded["project"].id
    first = _publish(db, seeded, key="rollback-supersede-v1")
    db.commit()
    seeded["project"].title = "Retryable v2"
    db.commit()
    readiness = calculate_publication_readiness(db, project_id=project_id)

    def fail_after_supersede(*, publication, release, previous_release):
        previous_release.status = "superseded"
        publication.active_release_id = release.id
        publication.published_at = release.published_at
        publication.lock_version += 1
        db.flush()
        raise RuntimeError("simulated supersede failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(publication_service, "_activate_release", fail_after_supersede)
        with pytest.raises(RuntimeError, match="simulated supersede failure"):
            publish_project(
                db,
                project_id=project_id,
                user_id=seeded["user"].id,
                expected_authoring_fingerprint=readiness.authoring_fingerprint,
                idempotency_key="rollback-supersede-v2",
            )

    db.expire_all()
    publication = db.query(ProjectPublication).filter_by(project_id=project_id).one()
    persisted_first = db.query(ProjectRelease).filter_by(id=first.id).one()
    assert persisted_first.status == "published"
    assert publication.active_release_id == first.id
    assert publication.lock_version == 2
    assert db.query(ProjectRelease).filter_by(project_id=project_id).count() == 1
    assert get_active_public_release(db, release_id=first.id).id == first.id
    assert calculate_project_state_projection(
        db,
        project_id=project_id,
    ).publication.state == "changes_pending"

    retry = publish_project(
        db,
        project_id=project_id,
        user_id=seeded["user"].id,
        expected_authoring_fingerprint=readiness.authoring_fingerprint,
        idempotency_key="rollback-supersede-v2",
    )
    assert retry.created is True
    assert retry.release.version == 2
    assert persisted_first.status == "superseded"
    assert publication.active_release_id == retry.release.id
    assert publication.lock_version == 3


def test_source_lock_refreshes_identity_map_before_second_fingerprint(
    lifecycle,
    monkeypatch,
):
    db, _factory, seeded = lifecycle
    project_id = seeded["project"].id
    _publish(db, seeded, key="identity-map-v1")
    db.commit()
    seeded["project"].title = "Reviewed title"
    db.commit()
    expected = calculate_publication_readiness(
        db,
        project_id=project_id,
    ).authoring_fingerprint
    original_lock = publication_service._lock_publication_source

    def edit_then_lock(*args, **kwargs):
        db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(title="Changed during publish")
            .execution_options(synchronize_session=False)
        )
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(
        publication_service,
        "_lock_publication_source",
        edit_then_lock,
    )
    with pytest.raises(AppError) as captured:
        publish_project(
            db,
            project_id=project_id,
            user_id=seeded["user"].id,
            expected_authoring_fingerprint=expected,
            idempotency_key="identity-map-v2",
        )
    assert captured.value.code == "release.source_changed"
    assert db.query(ProjectRelease).filter_by(project_id=project_id).count() == 1


def test_stale_unpublish_session_refreshes_lock_version_before_absence_check(
    lifecycle,
):
    db, factory, seeded = lifecycle
    project_id = seeded["project"].id
    _publish(db, seeded, key="stale-unpublish-v1")
    db.commit()
    stale = factory()
    try:
        stale_publication = stale.query(ProjectPublication).filter_by(
            project_id=project_id
        ).one()
        stale_version = stale_publication.lock_version

        current = db.query(ProjectPublication).filter_by(project_id=project_id).one()
        unpublish_project(
            db,
            project_id=project_id,
            expected_lock_version=current.lock_version,
        )
        db.commit()

        with pytest.raises(AppError) as captured:
            unpublish_project(
                stale,
                project_id=project_id,
                expected_lock_version=stale_version,
            )
        assert captured.value.code == "publication.version_conflict"
        assert captured.value.status_code == 409
        assert captured.value.details["current_lock_version"] == stale_version + 1
    finally:
        stale.rollback()
        stale.close()


def test_postgresql_concurrent_first_publish_is_serialized(postgres_lifecycle):
    factory, seeded = postgres_lifecycle
    project_id = seeded["project"].id
    user_id = seeded["user"].id
    setup = factory()
    fingerprint = calculate_publication_readiness(
        setup,
        project_id=project_id,
    ).authoring_fingerprint
    setup.commit()
    setup.close()
    barrier = Barrier(2)

    def attempt(key: str) -> tuple[str, str | None]:
        session = factory()
        try:
            barrier.wait(timeout=10)
            try:
                result = publish_project(
                    session,
                    project_id=project_id,
                    user_id=user_id,
                    expected_authoring_fingerprint=fingerprint,
                    idempotency_key=key,
                )
                session.commit()
                return "created" if result.created else "replayed", result.release.id
            except AppError as error:
                session.rollback()
                return error.code, None
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                attempt,
                ("postgres-concurrent-a", "postgres-concurrent-b"),
            )
        )
    assert sorted(outcome for outcome, _release_id in outcomes) == [
        "created",
        "release.no_changes",
    ]
    verify = factory()
    try:
        releases = verify.query(ProjectRelease).filter_by(project_id=project_id).all()
        publication = verify.query(ProjectPublication).filter_by(
            project_id=project_id
        ).one()
        assert len(releases) == 1
        assert publication.active_release_id == releases[0].id
        assert publication.lock_version == 2
    finally:
        verify.close()


def test_postgresql_editor_commit_during_publish_is_detected(
    postgres_lifecycle,
    monkeypatch,
):
    factory, seeded = postgres_lifecycle
    project_id = seeded["project"].id
    user_id = seeded["user"].id
    setup = factory()
    first = _publish(setup, seeded, key="postgres-editor-race-v1")
    setup.commit()
    project = setup.query(Project).filter_by(id=project_id).one()
    project.title = "Reviewed title"
    setup.commit()
    expected = calculate_publication_readiness(
        setup,
        project_id=project_id,
    ).authoring_fingerprint
    setup.commit()
    setup.close()

    before_source_lock = Event()
    editor_committed = Event()
    original_lock = publication_service._lock_publication_source

    def wait_for_editor(*args, **kwargs):
        before_source_lock.set()
        assert editor_committed.wait(timeout=15)
        return original_lock(*args, **kwargs)

    monkeypatch.setattr(
        publication_service,
        "_lock_publication_source",
        wait_for_editor,
    )

    def attempt_publish() -> str:
        session = factory()
        try:
            try:
                publish_project(
                    session,
                    project_id=project_id,
                    user_id=user_id,
                    expected_authoring_fingerprint=expected,
                    idempotency_key="postgres-editor-race-v2",
                )
                session.commit()
                return "created"
            except AppError as error:
                session.rollback()
                return error.code
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(attempt_publish)
        assert before_source_lock.wait(timeout=15)
        editor = factory()
        try:
            edited = editor.query(Project).filter_by(id=project_id).one()
            edited.title = "Changed during publish"
            editor.commit()
        finally:
            editor.close()
            editor_committed.set()
        assert future.result(timeout=15) == "release.source_changed"

    verify = factory()
    try:
        assert verify.query(ProjectRelease).filter_by(project_id=project_id).count() == 1
        assert verify.query(ProjectRelease).filter_by(id=first.id).one().status == "published"
        assert verify.query(Project).filter_by(id=project_id).one().title == (
            "Changed during publish"
        )
    finally:
        verify.close()
