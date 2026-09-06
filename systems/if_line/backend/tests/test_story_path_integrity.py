from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import BigInteger, create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.application.authoring_resource_service import delete_authoring_project
from app.application.hashing import content_hash
from app.database import engine as application_engine, get_alembic_head_revision
from app.models import Project
from app.models_v2 import (
    AuthoringProjectDeleteScope,
    ChapterRevision,
    ChapterSlot,
    GenerationTask,
    OutlineChapter,
    OutlineRevision,
    ProjectPublication,
    ProjectRelease,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
    StateSnapshot,
    StoryPathChapter,
    UsageLedgerEntry,
    UsageReservation,
)
from test_publication_readiness import _seed_ready_project
from test_runtime_foundation import _run_alembic


@pytest.fixture()
def migrated_integrity(tmp_path):
    database = tmp_path / "story-path-integrity.db"
    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    engine = create_engine(f"sqlite:///{database.as_posix()}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    db = factory()
    first = _seed_ready_project(db)
    second = _seed_ready_project(db)
    try:
        yield engine, factory, db, first, second
    finally:
        db.close()
        engine.dispose()


def _assert_db_rejects(engine, sql: str, parameters: dict, message: str) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        with pytest.raises(IntegrityError, match=message):
            connection.execute(text(sql), parameters)
        transaction.rollback()


def test_migrated_database_freezes_core_rows_and_allows_one_reconciliation(
    migrated_integrity,
):
    engine, _factory, db, first, _second = migrated_integrity
    slot = ChapterSlot(
        project_id=first["project"].id,
        created_for_story_path_id=first["path"].id,
    )
    db.add(slot)
    db.flush()
    placement = StoryPathChapter(
        story_path_id=first["path"].id,
        chapter_slot_id=slot.id,
        display_index=2,
    )
    db.add(placement)
    db.flush()
    outline = OutlineRevision(
        project_id=first["project"].id,
        story_path_id=first["path"].id,
        bible_revision_id=first["bible"].id,
        revision_no=2,
        source_hash="7" * 64,
        content_hash="8" * 64,
    )
    db.add(outline)
    db.flush()
    outline_chapter = OutlineChapter(
        outline_revision_id=outline.id,
        chapter_index=1,
        display_index=1,
        title="Reconciled",
        summary="Bound exactly once",
        content_hash="9" * 64,
    )
    db.add(outline_chapter)
    db.commit()

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE outline_revision_chapters "
                "SET story_path_chapter_id = :placement_id WHERE id = :id"
            ),
            {"placement_id": placement.id, "id": outline_chapter.id},
        )

    _assert_db_rejects(
        engine,
        "UPDATE outline_revision_chapters SET story_path_chapter_id = :placement_id "
        "WHERE id = :id",
        {"placement_id": first["placement"].id, "id": outline_chapter.id},
        "immutable except for one PathChapter reconciliation",
    )
    _assert_db_rejects(
        engine,
        "DELETE FROM outline_revision_chapters WHERE id = :id",
        {"id": outline_chapter.id},
        "OutlineChapter is immutable",
    )

    frozen_rows = (
        (
            "story_bible_revisions",
            first["bible"].id,
            "content_hash",
            "0" * 64,
            "StoryBibleRevision is immutable",
        ),
        (
            "outline_revisions",
            outline.id,
            "content_hash",
            "0" * 64,
            "OutlineRevision payload and provenance are immutable",
        ),
        (
            "chapter_revisions",
            first["chapter"].id,
            "content_hash",
            "0" * 64,
            "ChapterRevision is immutable",
        ),
        (
            "state_snapshots",
            first["state"].id,
            "state_hash",
            "0" * 64,
            "StateSnapshot is immutable",
        ),
    )
    for table, row_id, column, value, message in frozen_rows:
        _assert_db_rejects(
            engine,
            f"UPDATE {table} SET {column} = :value WHERE id = :id",
            {"value": value, "id": row_id},
            message,
        )
        _assert_db_rejects(
            engine,
            f"DELETE FROM {table} WHERE id = :id",
            {"id": row_id},
            message.split(" payload")[0],
        )


def test_migrated_database_rejects_cross_project_ownership_and_head_families(
    migrated_integrity,
):
    engine, _factory, db, first, second = migrated_integrity
    now = datetime.now(timezone.utc)
    checkpoint = StoryNode(
        project_id=second["project"].id,
        node_type="checkpoint",
        checkpoint_key="cross-project",
        payload={},
    )
    db.add(checkpoint)
    db.commit()

    _assert_db_rejects(
        engine,
        "INSERT INTO chapter_slots "
        "(id, project_id, created_for_story_path_id, created_at) "
        "VALUES (:id, :project_id, :path_id, :created_at)",
        {
            "id": str(uuid4()),
            "project_id": first["project"].id,
            "path_id": second["path"].id,
            "created_at": now,
        },
        "belongs to another Project",
    )
    _assert_db_rejects(
        engine,
        "INSERT INTO story_path_chapters "
        "(id, story_path_id, chapter_slot_id, display_index, lock_version, "
        "created_at, updated_at) VALUES "
        "(:id, :path_id, :slot_id, 3, 1, :created_at, :created_at)",
        {
            "id": str(uuid4()),
            "path_id": first["path"].id,
            "slot_id": second["slot"].id,
            "created_at": now,
        },
        "ownership or Head family is invalid",
    )
    _assert_db_rejects(
        engine,
        "UPDATE chapter_script_heads SET current_revision_id = :revision_id "
        "WHERE chapter_revision_id = :source_id",
        {
            "revision_id": second["script"].id,
            "source_id": first["chapter"].id,
        },
        "another chapter family",
    )
    _assert_db_rejects(
        engine,
        "UPDATE vn_graph_heads SET current_revision_id = :revision_id "
        "WHERE script_revision_id = :source_id",
        {
            "revision_id": second["graph"].id,
            "source_id": first["script"].id,
        },
        "another Script family",
    )
    _assert_db_rejects(
        engine,
        "INSERT INTO state_snapshots "
        "(id, project_id, parent_snapshot_id, state_hash, state_json, created_at) "
        "VALUES (:id, :project_id, :parent_id, :state_hash, '{}', :created_at)",
        {
            "id": str(uuid4()),
            "project_id": first["project"].id,
            "parent_id": second["state"].id,
            "state_hash": "3" * 64,
            "created_at": now,
        },
        "parent belongs to another Project",
    )
    _assert_db_rejects(
        engine,
        "INSERT INTO story_paths "
        "(id, project_id, parent_path_id, fork_path_chapter_id, "
        "fork_checkpoint_node_id, fork_candidate_id, base_state_snapshot_id, "
        "title, status, lock_version, created_at, updated_at) VALUES "
        "(:id, :project_id, :parent_id, :placement_id, :checkpoint_id, "
        ":candidate_id, :state_id, 'Invalid child', 'active', 1, :created_at, :created_at)",
        {
            "id": str(uuid4()),
            "project_id": first["project"].id,
            "parent_id": second["path"].id,
            "placement_id": second["placement"].id,
            "checkpoint_id": checkpoint.id,
            "candidate_id": str(uuid4()),
            "state_id": second["state"].id,
            "created_at": now,
        },
        "fork provenance belongs to another Project",
    )
    _assert_db_rejects(
        engine,
        "INSERT INTO project_releases "
        "(id, project_id, version, status, bible_revision_id, "
        "outline_revision_id, manifest_json, manifest_hash, created_at) VALUES "
        "(:id, :project_id, 99, 'withdrawn', :bible_id, :outline_id, '{}', "
        ":manifest_hash, :created_at)",
        {
            "id": str(uuid4()),
            "project_id": first["project"].id,
            "bible_id": second["bible"].id,
            "outline_id": second["outline"].id,
            "manifest_hash": "4" * 64,
            "created_at": now,
        },
        "sources belong to another Project",
    )


def test_bible_content_can_repeat_but_generation_task_cannot(migrated_integrity):
    _engine, _factory, db, first, _second = migrated_integrity
    repeated = StoryBibleRevision(
        project_id=first["project"].id,
        revision_no=2,
        source_hash="5" * 64,
        content_hash=first["bible"].content_hash,
        content_json=first["bible"].content_json,
    )
    task = GenerationTask(
        user_id=first["user"].id,
        project_id=first["project"].id,
        kind="bible_generate",
        idempotency_key="integrity-task",
        parameters_hash="6" * 64,
    )
    db.add_all([repeated, task])
    db.commit()
    first_task_revision = StoryBibleRevision(
        project_id=first["project"].id,
        revision_no=3,
        source_hash="7" * 64,
        content_hash="8" * 64,
        content_json={"task": 1},
        generation_task_id=task.id,
    )
    db.add(first_task_revision)
    db.commit()
    db.add(
        StoryBibleRevision(
            project_id=first["project"].id,
            revision_no=4,
            source_hash="9" * 64,
            content_hash="a" * 64,
            content_json={"task": 2},
            generation_task_id=task.id,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_active_release_lifecycle_is_enforced_by_database(migrated_integrity):
    engine, _factory, db, first, _second = migrated_integrity
    published_at = datetime.now(timezone.utc)
    active = ProjectRelease(
        project_id=first["project"].id,
        version=1,
        status="published",
        bible_revision_id=first["bible"].id,
        outline_revision_id=first["outline"].id,
        manifest_json={"active": True},
        manifest_hash="b" * 64,
        authoring_fingerprint="c" * 64,
        published_at=published_at,
    )
    db.add(active)
    db.flush()
    db.add(
        ProjectPublication(
            project_id=first["project"].id,
            active_release_id=active.id,
            published_at=published_at,
        )
    )
    db.commit()

    _assert_db_rejects(
        engine,
        "UPDATE project_releases SET status = 'withdrawn', withdrawn_at = :now "
        "WHERE id = :id",
        {"id": active.id, "now": published_at},
        "must be unpublished",
    )


def test_migrated_populated_project_delete_uses_and_clears_scope(migrated_integrity):
    _engine, factory, db, first, _second = migrated_integrity
    project_id = first["project"].id
    checkpoint = StoryNode(
        project_id=project_id,
        node_type="checkpoint",
        checkpoint_key="delete-child",
        payload={},
    )
    db.add(checkpoint)
    db.flush()
    child_path = StoryPath(
        project_id=project_id,
        parent_path_id=first["path"].id,
        fork_path_chapter_id=first["placement"].id,
        fork_checkpoint_node_id=checkpoint.id,
        fork_candidate_id=str(uuid4()),
        base_state_snapshot_id=first["state"].id,
        title="Child",
    )
    db.add(child_path)
    db.flush()
    child_slot = ChapterSlot(
        project_id=project_id,
        created_for_story_path_id=child_path.id,
    )
    db.add(child_slot)
    db.flush()
    db.add(
        StoryPathChapter(
            story_path_id=child_path.id,
            chapter_slot_id=child_slot.id,
            display_index=1,
            inherited_from_path_chapter_id=first["placement"].id,
        )
    )
    db.commit()

    delete_authoring_project(
        db,
        project_id=project_id,
        user_id=first["user"].id,
    )
    db.commit()

    verification = factory()
    try:
        assert verification.query(Project).filter_by(id=project_id).count() == 0
        assert (
            verification.query(AuthoringProjectDeleteScope)
            .filter_by(project_id=project_id)
            .count()
            == 0
        )
    finally:
        verification.close()


def test_orm_rejects_direct_core_revision_deletes(migrated_integrity):
    _engine, factory, _db, first, _second = migrated_integrity
    targets = (
        (StoryBibleRevision, first["bible"].id, "StoryBibleRevision"),
        (OutlineRevision, first["outline"].id, "OutlineRevision"),
        (ChapterRevision, first["chapter"].id, "ChapterRevision"),
        (StateSnapshot, first["state"].id, "StateSnapshot"),
        (
            OutlineChapter,
            first["outline"].id,
            "OutlineChapter",
        ),
    )
    for model, identifier, message in targets:
        session = factory()
        try:
            if model is OutlineChapter:
                target = session.query(model).filter_by(
                    outline_revision_id=identifier
                ).one()
            else:
                target = session.get(model, identifier)
            session.delete(target)
            with pytest.raises(RuntimeError, match=message):
                session.flush()
            session.rollback()
        finally:
            session.close()


def test_postgresql_online_migration_enforces_integrity_and_aggregate_delete():
    if application_engine.dialect.name != "postgresql":
        pytest.skip("requires explicit dedicated PostgreSQL TEST_DATABASE_URL")

    with application_engine.connect() as connection:
        outer = connection.begin()
        db = sessionmaker(
            bind=connection,
            autoflush=False,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )()
        try:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            assert revision == get_alembic_head_revision()
            asset_seed = next(
                column
                for column in inspect(connection).get_columns("asset_versions")
                if column["name"] == "seed"
            )
            assert isinstance(asset_seed["type"], BigInteger)
            continuation_indexes = {
                index["name"]
                for index in inspect(connection).get_indexes(
                    "reading_continuations"
                )
            }
            assert "ix_public_continuation_chapter" in continuation_indexes
            first = _seed_ready_project(db)
            second = _seed_ready_project(db)

            slot = ChapterSlot(
                project_id=first["project"].id,
                created_for_story_path_id=first["path"].id,
            )
            db.add(slot)
            db.flush()
            placement = StoryPathChapter(
                story_path_id=first["path"].id,
                chapter_slot_id=slot.id,
                display_index=2,
            )
            db.add(placement)
            db.flush()
            outline = OutlineRevision(
                project_id=first["project"].id,
                story_path_id=first["path"].id,
                bible_revision_id=first["bible"].id,
                revision_no=2,
                source_hash="7" * 64,
                content_hash="8" * 64,
            )
            db.add(outline)
            db.flush()
            outline_chapter = OutlineChapter(
                outline_revision_id=outline.id,
                chapter_index=2,
                display_index=2,
                title="PostgreSQL reconciliation",
                summary="Bind this generated outline exactly once",
                characters=["A"],
                visual_keywords=["rain"],
                content_hash="9" * 64,
            )
            db.add(outline_chapter)
            db.flush()

            connection.execute(
                text(
                    "UPDATE outline_revision_chapters "
                    "SET story_path_chapter_id = :placement_id WHERE id = :id"
                ),
                {"placement_id": placement.id, "id": outline_chapter.id},
            )
            assert connection.execute(
                text(
                    "SELECT story_path_chapter_id FROM outline_revision_chapters "
                    "WHERE id = :id"
                ),
                {"id": outline_chapter.id},
            ).scalar_one() == placement.id

            def reject(sql: str, parameters: dict, message: str) -> None:
                savepoint = connection.begin_nested()
                try:
                    with pytest.raises(IntegrityError, match=message):
                        connection.execute(text(sql), parameters)
                finally:
                    if savepoint.is_active:
                        savepoint.rollback()

            reject(
                "UPDATE story_bible_revisions SET content_hash = :value "
                "WHERE id = :id",
                {"value": "0" * 64, "id": first["bible"].id},
                "StoryBibleRevision is immutable",
            )
            reject(
                "DELETE FROM chapter_revisions WHERE id = :id",
                {"id": first["chapter"].id},
                "ChapterRevision is immutable",
            )
            reject(
                "INSERT INTO chapter_slots "
                "(id, project_id, created_for_story_path_id, created_at) "
                "VALUES (:id, :project_id, :path_id, :created_at)",
                {
                    "id": str(uuid4()),
                    "project_id": first["project"].id,
                    "path_id": second["path"].id,
                    "created_at": datetime.now(timezone.utc),
                },
                "belongs to another Project",
            )

            task = GenerationTask(
                user_id=first["user"].id,
                project_id=first["project"].id,
                kind="chapter.generate",
                idempotency_key=f"postgres-ledger-{uuid4()}",
                parameters_hash="5" * 64,
            )
            db.add(task)
            db.flush()
            reservation = UsageReservation(
                user_id=first["user"].id,
                project_id=first["project"].id,
                task_id=task.id,
                estimated_amount=Decimal("2"),
                reserved_amount=Decimal("2"),
                settled_amount=Decimal("0"),
                refunded_amount=Decimal("0"),
            )
            db.add(reservation)
            db.flush()
            ledger = UsageLedgerEntry(
                reservation_id=reservation.id,
                user_id=first["user"].id,
                project_id=first["project"].id,
                task_id=task.id,
                activity_type=task.kind,
                entry_type="reserve",
                amount=Decimal("2"),
                event_metadata={"kind": task.kind},
            )
            db.add(ledger)
            db.flush()
            connection.execute(
                text("DELETE FROM generation_tasks WHERE id = :id"),
                {"id": task.id},
            )
            ledger_after_task_delete = connection.execute(
                text(
                    "SELECT reservation_id, task_id, project_id, activity_type "
                    "FROM usage_ledger_entries WHERE id = :id"
                ),
                {"id": ledger.id},
            ).one()
            assert ledger_after_task_delete == (
                None,
                None,
                first["project"].id,
                "chapter.generate",
            )

            delete_authoring_project(
                db,
                project_id=first["project"].id,
                user_id=first["user"].id,
            )
            db.flush()
            assert connection.execute(
                text("SELECT COUNT(*) FROM projects WHERE id = :id"),
                {"id": first["project"].id},
            ).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT COUNT(*) FROM authoring_project_delete_scopes "
                    "WHERE project_id = :id"
                ),
                {"id": first["project"].id},
            ).scalar_one() == 0
        finally:
            db.close()
            if outer.is_active:
                outer.rollback()
