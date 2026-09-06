from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Column, Integer, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app import main as main_module
from app.core.config import AppSettings
from app.database import (
    UnitOfWork,
    check_database_schema,
    get_alembic_head_revision,
)
from app.main import create_app


BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_HEAD = get_alembic_head_revision()

V2_TABLE_GROUPS = {
    "0003_tasks_usage": (
        "generation_tasks",
        "generation_task_dependencies",
        "task_events",
        "outbox_events",
        "usage_reservations",
        "usage_ledger_entries",
        "provider_usage_records",
    ),
    "0004_content_revisions": (
        "project_content_heads",
        "story_bible_revisions",
        "outline_revisions",
        "outline_revision_chapters",
        "state_snapshots",
        "chapter_revisions",
        "chapter_heads",
        "chapter_segments",
        "vn_graph_revisions",
        "vn_graph_heads",
    ),
    "0005_artifact_domain": (
        "storage_objects",
        "voice_profiles",
        "asset_versions",
        "asset_bindings",
        "voice_lines",
    ),
    "0006_reading_branching": (
        "story_nodes",
        "branch_candidates",
        "branch_edges",
    ),
    "0007_releases": (
        "project_releases",
        "reading_sessions",
        "choice_decisions",
        "reading_bookmarks",
    ),
    "0008_visual_asset_workflows": (
        "library_assets",
        "library_asset_embeddings",
        "scene_manifests",
        "asset_actions",
        "reading_continuations",
    ),
    "0020_chapter_script_semantic_pipeline": (
        "chapter_script_revisions",
        "chapter_script_heads",
        "script_resource_slots",
    ),
    "0021_project_asset_history": (
        "asset_plans",
        "asset_plan_items",
    ),
    "0022_story_path_identity": (
        "story_paths",
        "chapter_slots",
        "story_path_chapters",
    ),
    "0023_path_revision_binding": (
        "story_path_outline_heads",
    ),
    "0024_candidate_set_versioning": (
        "candidate_set_revisions",
        "candidate_set_heads",
    ),
    "0025_revision_keyed_artifact_heads": (),
    "0026_single_source_publication": ("project_publications",),
    "0027_nullable_outline_head": (),
    "0028_candidate_set_payload": (),
    "0029_nullable_candidate_set_head": (),
    "0030_story_path_promotions": ("story_path_promotion_records",),
    "0031_chapter_script_revision_heads": (),
    "0032_vn_graph_script_revision_skeleton": (),
    "0033_immutable_vn_graph_manifest": ("voice_line_versions",),
    "0034_release_publication_idempotency": (),
    "0035_script_resource_slot_lock": (),
    "0036_backfill_root_story_paths": (),
    "0037_backfill_immutable_artifact_relations": (),
    "0038_backfill_project_publications": (),
    "0039_story_path_integrity": ("authoring_project_delete_scopes",),
    "0040_unified_usage_ledger": (),
    "0041_fix_outline_chapter_json_guard": (),
    "0042_align_postgresql_schema": (),
    "0043_repair_legacy_outline_contract": (),
    "0044_story_path_chapter_lifecycle": (),
    "0045_generation_task_lease_fencing": (),
    "0046_detach_legacy_ghost_chapters": (),
    "0047_library_asset_tags": ("library_tags", "library_asset_tags"),
}
V2_TABLES = frozenset(table for tables in V2_TABLE_GROUPS.values() for table in tables)
LEGACY_FK_TARGETS = {"users", "projects", "assets"}

# SHA-256 of normalized SQLite CREATE TABLE/INDEX statements for V2_TABLES.
# A mismatch means models_v2 changed an existing table without a new migration.
V2_SQLITE_SCHEMA_FINGERPRINT = "897e05385b8cfcae664f16c6b131fa308a4756ef59659cd4d73436f58857061b"


def _alembic_environment(database: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    return env


def _run_alembic(database: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=BACKEND_DIR,
        env=_alembic_environment(database),
        capture_output=True,
        text=True,
        timeout=90,
    )


def _normalize_sqlite_create_sql(sql: str) -> str:
    normalized = re.sub(r"\s+", " ", sql).strip()
    open_index = normalized.find("(")
    close_index = normalized.rfind(")")
    if open_index < 0 or close_index <= open_index:
        return normalized
    body = normalized[open_index + 1 : close_index]
    clauses: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    for index, character in enumerate(body):
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            clauses.append(body[start:index].strip())
            start = index + 1
    clauses.append(body[start:].strip())
    constraint_prefixes = ("CONSTRAINT ", "PRIMARY KEY", "FOREIGN KEY", "UNIQUE ", "CHECK ")
    columns = [clause for clause in clauses if not clause.upper().startswith(constraint_prefixes)]
    constraints = sorted(
        clause for clause in clauses if clause.upper().startswith(constraint_prefixes)
    )
    return f"{normalized[:open_index + 1]}{', '.join([*columns, *constraints])}{normalized[close_index:]}"


def _sqlite_schema_fingerprint(connection: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in V2_TABLES)
    rows = connection.execute(
        f"""
        SELECT type, name, tbl_name, sql
          FROM sqlite_master
         WHERE tbl_name IN ({placeholders})
           AND type IN ('table', 'index')
           AND sql IS NOT NULL
         ORDER BY type, name
        """,
        tuple(sorted(V2_TABLES)),
    ).fetchall()
    normalized = [
        [kind, name, table, _normalize_sqlite_create_sql(sql)]
        for kind, name, table, sql in rows
    ]
    payload = json.dumps(normalized, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_migration(revision: str):
    path = BACKEND_DIR / "alembic" / "versions" / f"{revision}.py"
    spec = importlib.util.spec_from_file_location(f"test_migration_{revision}", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_development_settings_keep_sqlite_compatibility(tmp_path: Path):
    settings = AppSettings(
        _env_file=None,
        app_env="development",
        database_url=f"sqlite:///{(tmp_path / 'dev.db').as_posix()}",
        cors_allow_origins="http://localhost:5173",
    )

    assert settings.is_production is False
    assert settings.cors_origins == ["http://localhost:5173"]


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"database_url": "sqlite:///unsafe.db"}, "PostgreSQL"),
        ({"cors_allow_origins": "*"}, "CORS_ALLOW_ORIGINS"),
        ({"auth_cookie_secure": False}, "AUTH_COOKIE_SECURE"),
        ({"openai_api_key": "your-api-key-here"}, "OPENAI_API_KEY"),
    ],
)
def test_production_settings_fail_closed(override: dict, expected: str):
    safe = {
        "_env_file": None,
        "app_env": "production",
        "database_url": "postgresql+psycopg://user:pass@localhost/if_line",
        "cors_allow_origins": "https://if-line.example",
        "auth_cookie_secure": True,
        "debug": False,
        "openai_api_key": "sk-real-value-for-validation",
        "tts_enabled": False,
        "image_generation_enabled": False,
    }
    safe.update(override)

    with pytest.raises(ValidationError, match=expected):
        AppSettings(**safe)


def test_importing_main_does_not_create_or_migrate_database(tmp_path: Path):
    database = tmp_path / "must-not-exist.db"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "STATIC_DIR": str(tmp_path / "static"),
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert not database.exists()


def test_health_metrics_request_id_and_security_headers(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(main_module, "check_database_schema", lambda: None)
    monkeypatch.setattr(
        main_module,
        "render_metrics",
        lambda *, include_database: (
            b"# TYPE if_line_http_requests_total counter\n",
            "text/plain; version=0.0.4",
        ),
    )
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        static_dir=tmp_path / "static",
        cors_allow_origins="http://localhost:5173",
        metrics_enabled=True,
        check_dependencies_on_startup=False,
        auth_ratelimit_backend="memory",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/live", headers={"X-Request-ID": "test-request-123"})
        metrics = client.get("/metrics")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"] == "test-request-123"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert metrics.status_code == 200
    assert "if_line_http_requests_total" in metrics.text


def test_readiness_checks_redis_when_durable_tasks_are_enabled(tmp_path: Path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(main_module, "check_database_connection", lambda: calls.append("database"))
    monkeypatch.setattr(main_module, "check_database_schema", lambda: calls.append("schema"))
    monkeypatch.setattr(
        main_module,
        "_check_redis_connection",
        lambda settings: calls.append("redis"),
    )
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        static_dir=tmp_path / "static",
        cors_allow_origins="http://localhost:5173",
        task_system_enabled=True,
        check_dependencies_on_startup=True,
        auth_ratelimit_backend="redis",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"database": "ok", "schema": "ok", "redis": "ok"},
    }
    assert calls == ["database", "redis", "schema", "database", "schema", "redis"]


def test_unit_of_work_requires_explicit_commit():
    base = declarative_base()

    class Record(base):
        __tablename__ = "records"
        id = Column(Integer, primary_key=True)

    test_engine = create_engine("sqlite:///:memory:")
    base.metadata.create_all(test_engine)
    factory = sessionmaker(bind=test_engine)

    with UnitOfWork(factory) as uow:
        uow.session.add(Record(id=1))
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM records")).scalar_one() == 0

    with UnitOfWork(factory) as uow:
        uow.session.add(Record(id=2))
        uow.commit()
    with test_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM records")).scalar_one() == 1


def test_alembic_upgrades_empty_sqlite_database(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "alembic.db"
    result = _run_alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    legacy_tables = {
        "users",
        "projects",
        "story_bibles",
        "chapter_outlines",
        "chapter_contents",
        "assets",
        "vn_graphs",
        "workflow_runs",
        "generation_stats",
        "project_likes",
        "project_comments",
        "user_notifications",
        "character_voice_bindings",
        "user_sessions",
        "alembic_version",
    }
    assert legacy_tables.issubset(tables)
    assert V2_TABLES.issubset(tables)
    assert "api_usage_events" not in tables
    assert version == ALEMBIC_HEAD


def test_path_chapter_lifecycle_downgrade_refuses_detached_history(tmp_path: Path):
    pytest.importorskip("alembic")
    from app.models import Project, User
    from app.models_v2 import (
        ChapterSlot,
        OutlineRevision,
        StoryBibleRevision,
        StoryPath,
        StoryPathChapter,
    )

    database = tmp_path / "path-chapter-lifecycle.db"
    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    try:
        user = User(
            email="path-chapter-lifecycle@example.com",
            password_hash="hash",
            display_name="PathChapter Lifecycle",
        )
        project = Project(
            owner=user,
            title="Lifecycle",
            story_start="Start",
            story_end="End",
        )
        db.add_all([user, project])
        db.flush()
        path = StoryPath(project_id=project.id, title="Main")
        db.add(path)
        db.flush()
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash="a" * 64,
            content_hash="b" * 64,
            content_json={},
            status="ready",
            created_by=user.id,
        )
        db.add(bible)
        db.flush()
        outline = OutlineRevision(
            project_id=project.id,
            story_path_id=path.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash="c" * 64,
            content_hash="d" * 64,
            status="ready",
            created_by=user.id,
        )
        db.add(outline)
        db.flush()
        slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
        db.add(slot)
        db.flush()
        db.add(
            StoryPathChapter(
                story_path_id=path.id,
                chapter_slot_id=slot.id,
                display_index=1,
                status="detached",
                detached_at=datetime.now(timezone.utc),
                detached_by_outline_revision_id=outline.id,
            )
        )
        db.commit()
    finally:
        db.close()
        engine.dispose()

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0043_repair_legacy_outline_contract",
    )
    assert downgraded.returncode != 0
    assert "cannot be downgraded after PathChapters have been detached" in (
        downgraded.stderr
    )
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("story_path_chapters")')
        }
    assert version == "0044_story_path_chapter_lifecycle"
    assert {"status", "detached_at", "detached_by_outline_revision_id"} <= columns


def test_0045_invalidates_legacy_leases_and_guards_active_token_downgrade(
    tmp_path: Path,
):
    database = tmp_path / "lease-fencing.db"
    upgraded = _run_alembic(
        database,
        "upgrade",
        "0044_story_path_chapter_lifecycle",
    )
    assert upgraded.returncode == 0, upgraded.stderr

    task_id = "legacy-running-task"
    with sqlite3.connect(database) as connection:
        # Model-backed early migrations reflect current metadata, so remove
        # the future column to accurately emulate a database created by 0044.
        connection.execute("ALTER TABLE generation_tasks DROP COLUMN lease_token")
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily
            ) VALUES (1, 'lease@example.com', 'hash', 'Lease', 1, 20, 20, 0, 0)
            """
        )
        connection.execute(
            """
            INSERT INTO generation_tasks (
                id, user_id, kind, status, progress, idempotency_key,
                source_refs, result_refs, parameters, parameters_hash,
                attempt, max_attempts, lease_owner, heartbeat_at,
                estimated_cost, reserved_cost, actual_cost,
                queued_at, created_at, updated_at
            ) VALUES (
                ?, 1, 'bible.generate', 'running', 10, 'legacy-lease',
                '{}', '{}', '{}', ?,
                1, 3, 'legacy-worker', '2026-08-10 00:00:00',
                0, 0, 0,
                '2026-08-10 00:00:00', '2026-08-10 00:00:00',
                '2026-08-10 00:00:00'
            )
            """,
            (task_id, "a" * 64),
        )

    fenced = _run_alembic(database, "upgrade", "head")
    assert fenced.returncode == 0, fenced.stderr
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            """
            SELECT status, lease_owner, lease_token, heartbeat_at
              FROM generation_tasks
             WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        assert row == ("running", None, None, None)
        connection.execute(
            """
            UPDATE generation_tasks
               SET lease_owner = 'new-worker', lease_token = ?
             WHERE id = ?
            """,
            ("b" * 36, task_id),
        )

    blocked = _run_alembic(
        database,
        "downgrade",
        "0044_story_path_chapter_lifecycle",
    )
    assert blocked.returncode != 0
    assert "cannot be downgraded while generation task leases are active" in (
        blocked.stderr
    )
    with sqlite3.connect(database) as connection:
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("generation_tasks")')
        }
        connection.execute(
            "UPDATE generation_tasks SET lease_owner = NULL, lease_token = NULL"
        )
    assert version == "0045_generation_task_lease_fencing"
    assert "lease_token" in columns

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0044_story_path_chapter_lifecycle",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("generation_tasks")')
        }
    assert "lease_token" not in columns


def test_0046_repairs_legacy_ghost_chapters_and_roundtrips(tmp_path: Path):
    pytest.importorskip("alembic")
    from app.models import Project, User
    from app.models_v2 import (
        ChapterSlot,
        OutlineChapter,
        OutlineRevision,
        StoryBibleRevision,
        StoryPath,
        StoryPathChapter,
        StoryPathOutlineHead,
    )

    database = tmp_path / "legacy-ghost-chapters.db"
    upgraded = _run_alembic(
        database,
        "upgrade",
        "0045_generation_task_lease_fencing",
    )
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = factory()
    try:
        user = User(
            email="legacy-ghosts@example.com",
            password_hash="hash",
            display_name="Legacy Ghosts",
        )
        project = Project(
            owner=user,
            title="Legacy Ghosts",
            story_start="Start",
            story_end="End",
        )
        db.add_all([user, project])
        db.flush()
        path = StoryPath(project_id=project.id, title="Main")
        db.add(path)
        db.flush()
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash="a" * 64,
            content_hash="b" * 64,
            content_json={},
            status="ready",
            created_by=user.id,
        )
        db.add(bible)
        db.flush()

        placements: list[StoryPathChapter] = []
        previous: StoryPathChapter | None = None
        for display_index in range(1, 5):
            slot = ChapterSlot(
                project_id=project.id,
                created_for_story_path_id=path.id,
            )
            db.add(slot)
            db.flush()
            placement = StoryPathChapter(
                story_path_id=path.id,
                chapter_slot_id=slot.id,
                display_index=display_index,
                predecessor_path_chapter_id=previous.id if previous else None,
            )
            db.add(placement)
            db.flush()
            placements.append(placement)
            previous = placement

        outline = OutlineRevision(
            project_id=project.id,
            story_path_id=path.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash="c" * 64,
            content_hash="d" * 64,
            status="ready",
            created_by=user.id,
        )
        db.add(outline)
        db.flush()
        selected = (placements[0], placements[2])
        for display_index, placement in enumerate(selected, start=1):
            db.add(
                OutlineChapter(
                    outline_revision_id=outline.id,
                    chapter_index=display_index,
                    story_path_chapter_id=placement.id,
                    display_index=display_index,
                    title=f"Chapter {display_index}",
                    summary="Summary",
                    characters=[],
                    visual_keywords=[],
                    content_hash=str(display_index) * 64,
                )
            )
        db.add(
            StoryPathOutlineHead(
                story_path_id=path.id,
                current_revision_id=outline.id,
            )
        )
        db.commit()
        path_id = path.id
        outline_id = outline.id
        placement_ids = [placement.id for placement in placements]
    finally:
        db.close()
        engine.dispose()

    with sqlite3.connect(database) as connection:
        original_path = connection.execute(
            "SELECT lock_version, updated_at FROM story_paths WHERE id = ?",
            (path_id,),
        ).fetchone()
        original_chapters = connection.execute(
            """
            SELECT id, display_index, predecessor_path_chapter_id, status,
                   detached_at, detached_by_outline_revision_id,
                   lock_version, updated_at
              FROM story_path_chapters
             WHERE story_path_id = ?
             ORDER BY display_index, id
            """,
            (path_id,),
        ).fetchall()

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr
    with sqlite3.connect(database) as connection:
        repaired = {
            row[0]: row[1:]
            for row in connection.execute(
                """
                SELECT id, display_index, predecessor_path_chapter_id, status,
                       detached_at, detached_by_outline_revision_id, lock_version
                  FROM story_path_chapters
                 WHERE story_path_id = ?
                """,
                (path_id,),
            )
        }
        repaired_path_lock = connection.execute(
            "SELECT lock_version FROM story_paths WHERE id = ?",
            (path_id,),
        ).fetchone()[0]
        chapter_repairs = connection.execute(
            "SELECT COUNT(*) FROM _alembic_0046_path_chapter_repair"
        ).fetchone()[0]
        path_repairs = connection.execute(
            "SELECT COUNT(*) FROM _alembic_0046_story_path_repair"
        ).fetchone()[0]
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    first_id, second_id, third_id, fourth_id = placement_ids
    assert repaired[first_id] == (1, None, "active", None, None, 1)
    assert repaired[third_id] == (2, first_id, "active", None, None, 2)
    assert repaired[second_id][0:3] == (2, None, "detached")
    assert repaired[second_id][3] is not None
    assert repaired[second_id][4:] == (outline_id, 2)
    assert repaired[fourth_id][0:3] == (4, None, "detached")
    assert repaired[fourth_id][3] is not None
    assert repaired[fourth_id][4:] == (outline_id, 2)
    assert repaired_path_lock == original_path[0] + 1
    assert chapter_repairs == 3
    assert path_repairs == 1
    assert version == ALEMBIC_HEAD

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0045_generation_task_lease_fencing",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        restored_path = connection.execute(
            "SELECT lock_version, updated_at FROM story_paths WHERE id = ?",
            (path_id,),
        ).fetchone()
        restored_chapters = connection.execute(
            """
            SELECT id, display_index, predecessor_path_chapter_id, status,
                   detached_at, detached_by_outline_revision_id,
                   lock_version, updated_at
              FROM story_path_chapters
             WHERE story_path_id = ?
             ORDER BY display_index, id
            """,
            (path_id,),
        ).fetchall()
        scratch_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_0046_%'"
            )
        }
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert restored_path == original_path
    assert restored_chapters == original_chapters
    assert not scratch_tables
    assert version == "0045_generation_task_lease_fencing"

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        active_ids = connection.execute(
            """
            SELECT id FROM story_path_chapters
             WHERE story_path_id = ? AND status = 'active'
             ORDER BY display_index
            """,
            (path_id,),
        ).fetchall()
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    assert active_ids == [(first_id,), (third_id,)]
    assert version == ALEMBIC_HEAD


def test_schema_guard_requires_the_exact_single_revision(tmp_path: Path):
    database = tmp_path / "schema-guard.db"
    guard_engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with guard_engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE alembic_version (version_num VARCHAR(64) NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": ALEMBIC_HEAD},
            )
        check_database_schema(guard_engine)

        with guard_engine.begin() as connection:
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES ('unexpected_branch')")
            )
        with pytest.raises(RuntimeError, match="schema revision mismatch"):
            check_database_schema(guard_engine)

        with guard_engine.begin() as connection:
            connection.execute(text("DELETE FROM alembic_version"))
            connection.execute(
                text("INSERT INTO alembic_version(version_num) VALUES (:revision)"),
                {"revision": "0038_backfill_project_publications"},
            )
        with pytest.raises(RuntimeError, match="0038_backfill_project_publications"):
            check_database_schema(guard_engine)
    finally:
        guard_engine.dispose()


def test_upgrade_head_enters_real_application_lifespan(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "lifespan-head.db"
    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    environment = _alembic_environment(database)
    environment.update(
        {
            "CHECK_DEPENDENCIES_ON_STARTUP": "false",
            "TTS_ENABLED": "false",
        }
    )
    script = """
from fastapi.testclient import TestClient
from app.core.config import AppSettings
from app.main import create_app

application = create_app(AppSettings(_env_file=None))
with TestClient(application) as client:
    response = client.get('/health/live')
    assert response.status_code == 200, response.text
print('LIFESPAN_OK')
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "LIFESPAN_OK" in result.stdout


def test_unified_usage_ledger_migrates_legacy_rows_and_roundtrips(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "unified-usage-ledger.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (
                version_num VARCHAR(64) NOT NULL PRIMARY KEY
            );
            INSERT INTO alembic_version(version_num)
            VALUES ('0039_story_path_integrity');

            CREATE TABLE users (id INTEGER NOT NULL PRIMARY KEY);
            CREATE TABLE projects (id INTEGER NOT NULL PRIMARY KEY);
            CREATE TABLE generation_tasks (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id INTEGER,
                kind VARCHAR(64) NOT NULL
            );
            CREATE TABLE usage_reservations (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                project_id INTEGER,
                task_id VARCHAR(36) NOT NULL UNIQUE,
                FOREIGN KEY(task_id) REFERENCES generation_tasks(id)
                    ON DELETE CASCADE
            );
            CREATE TABLE usage_ledger_entries (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                reservation_id VARCHAR(36),
                user_id INTEGER NOT NULL,
                task_id VARCHAR(36),
                entry_type VARCHAR(32) NOT NULL,
                amount NUMERIC(18, 6) NOT NULL,
                currency VARCHAR(16) NOT NULL,
                event_metadata JSON NOT NULL,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(reservation_id) REFERENCES usage_reservations(id)
                    ON DELETE CASCADE,
                FOREIGN KEY(task_id) REFERENCES generation_tasks(id)
                    ON DELETE SET NULL
            );
            CREATE TABLE api_usage_events (
                id INTEGER NOT NULL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                event_type VARCHAR(100) NOT NULL,
                amount INTEGER NOT NULL,
                project_id INTEGER,
                event_metadata JSON,
                created_at DATETIME
            );

            INSERT INTO users(id) VALUES (1);
            INSERT INTO projects(id) VALUES (7);
            INSERT INTO generation_tasks(id, project_id, kind)
            VALUES ('task-current', 7, 'chapter_generate');
            INSERT INTO generation_tasks(id, project_id, kind)
            VALUES ('task-direct-reservation', 7, 'outline_generate');
            INSERT INTO usage_reservations(id, project_id, task_id)
            VALUES ('reservation-current', 7, 'task-current');
            INSERT INTO usage_reservations(id, project_id, task_id)
            VALUES (
                'reservation-direct', 7, 'task-direct-reservation'
            );
            INSERT INTO usage_ledger_entries(
                id, reservation_id, user_id, task_id, entry_type,
                amount, currency, event_metadata, created_at
            ) VALUES (
                'ledger-current', 'reservation-current', 1, 'task-current',
                'reserve', 2, 'credit', '{"kind":"chapter_generate"}',
                '2026-01-01 00:00:00'
            );
            INSERT INTO usage_ledger_entries(
                id, reservation_id, user_id, task_id, entry_type,
                amount, currency, event_metadata, created_at
            ) VALUES (
                'ledger-direct', 'reservation-direct', 1,
                'task-direct-reservation', 'reserve', 4, 'credit',
                '{"kind":"outline_generate"}', '2026-01-01 00:01:00'
            );
            INSERT INTO api_usage_events(
                id, user_id, event_type, amount, project_id,
                event_metadata, created_at
            ) VALUES (
                41, 1, 'legacy_outline_generate', 3, 7,
                '{"chapter_count":12}', '2025-12-31 23:59:00'
            );
            """
        )

    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        columns = {
            row[1]: bool(row[3])
            for row in connection.execute(
                'PRAGMA table_info("usage_ledger_entries")'
            )
        }
        current = connection.execute(
            "SELECT project_id, activity_type FROM usage_ledger_entries "
            "WHERE id = 'ledger-current'"
        ).fetchone()
        legacy = connection.execute(
            "SELECT project_id, activity_type, entry_type, amount, "
            "event_metadata, created_at FROM usage_ledger_entries "
            "WHERE id = 'legacy-api-usage-41'"
        ).fetchone()
    assert "api_usage_events" not in tables
    assert columns["project_id"] is False
    assert columns["activity_type"] is True
    assert current == (7, "chapter_generate")
    assert legacy[:4] == (7, "legacy_outline_generate", "adjustment", 3)
    assert json.loads(legacy[4]) == {"chapter_count": 12}
    assert legacy[5] == "2025-12-31 23:59:00"

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        ledger_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                'PRAGMA foreign_key_list("usage_ledger_entries")'
            )
        }
        connection.execute(
            "DELETE FROM usage_reservations WHERE id = 'reservation-direct'"
        )
        direct_reservation_deleted = connection.execute(
            "SELECT reservation_id, task_id, project_id, activity_type "
            "FROM usage_ledger_entries WHERE id = 'ledger-direct'"
        ).fetchone()
        connection.execute(
            "DELETE FROM generation_tasks WHERE id = 'task-current'"
        )
        task_deleted = connection.execute(
            "SELECT reservation_id, task_id, project_id, activity_type "
            "FROM usage_ledger_entries WHERE id = 'ledger-current'"
        ).fetchone()
        connection.commit()

    assert (
        "usage_reservations",
        "reservation_id",
        "id",
        "SET NULL",
    ) in ledger_fks
    assert ("generation_tasks", "task_id", "id", "SET NULL") in ledger_fks
    assert direct_reservation_deleted == (
        None,
        "task-direct-reservation",
        7,
        "outline_generate",
    )
    assert task_deleted == (None, None, 7, "chapter_generate")

    downgraded = _run_alembic(
        database, "downgrade", "0039_story_path_integrity"
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        columns_after_downgrade = {
            row[1]
            for row in connection.execute(
                'PRAGMA table_info("usage_ledger_entries")'
            )
        }
        restored = connection.execute(
            "SELECT user_id, event_type, amount, project_id, event_metadata "
            "FROM api_usage_events WHERE id = 41"
        ).fetchone()
        migrated_row_count = connection.execute(
            "SELECT COUNT(*) FROM usage_ledger_entries "
            "WHERE id = 'legacy-api-usage-41'"
        ).fetchone()[0]
    assert "project_id" not in columns_after_downgrade
    assert "activity_type" not in columns_after_downgrade
    assert restored[:4] == (1, "legacy_outline_generate", 3, 7)
    assert json.loads(restored[4]) == {"chapter_count": 12}
    assert migrated_row_count == 0

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        stable = connection.execute(
            "SELECT project_id, activity_type, amount "
            "FROM usage_ledger_entries WHERE id = 'legacy-api-usage-41'"
        ).fetchone()
    assert stable == (7, "legacy_outline_generate", 3)


def test_script_resource_slot_lock_migration_backfills_historical_rows(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "historical-resource-slot.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE alembic_version (
                version_num VARCHAR(64) NOT NULL PRIMARY KEY
            );
            INSERT INTO alembic_version(version_num)
            VALUES ('0034_release_publication_idempotency');
            CREATE TABLE script_resource_slots (
                id VARCHAR(36) NOT NULL PRIMARY KEY,
                status VARCHAR(16) NOT NULL
            );
            INSERT INTO script_resource_slots(id, status)
            VALUES ('slot-before-r62', 'bound');
            """
        )

    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT id, status, lock_version FROM script_resource_slots"
        ).fetchone() == ("slot-before-r62", "bound", 1)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO script_resource_slots(id, status, lock_version) "
                "VALUES ('slot-invalid-lock', 'bound', 0)"
            )

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0034_release_publication_idempotency",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("script_resource_slots")')
        }
        row = connection.execute(
            "SELECT id, status FROM script_resource_slots"
        ).fetchone()
    assert "lock_version" not in columns
    assert row == ("slot-before-r62", "bound")

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT lock_version FROM script_resource_slots WHERE id = 'slot-before-r62'"
        ).fetchone() == (1,)


def test_chapter_script_migration_backfills_exact_source_and_vngraph_link(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "script-backfill.db"
    upgraded = _run_alembic(database, "upgrade", "0019_change_assets_seed_to_bigint")
    assert upgraded.returncode == 0, upgraded.stderr

    chapter_content = "  雨落在站台。\r\n\r\n阿宁说：\"走吧。\"  \n灯光熄灭。"
    chapter_hash = hashlib.sha256(chapter_content.encode("utf-8")).hexdigest()
    bible_content = {
        "worldview": "现代城市",
        "characters": [{"character_id": "character-an", "name": "阿宁"}],
    }
    now = "2026-07-30T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'backfill@example.com', 'hash', 'Backfill', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '迁移项目', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, ?, 'complete', 1, ?)
            """,
            (
                "1" * 64,
                hashlib.sha256(
                    json.dumps(bible_content, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                json.dumps(bible_content, ensure_ascii=False),
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("2" * 64, "3" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_revisions (
                id, project_id, chapter_index, bible_revision_id,
                outline_revision_id, revision_no, source_hash, content_hash,
                content, status, created_by, created_at
            ) VALUES ('chapter-1', 1, 1, 'bible-1', 'outline-1', 1,
                      ?, ?, ?, 'complete', 1, ?)
            """,
            ("4" * 64, chapter_hash, chapter_content, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_heads (
                project_id, chapter_index, current_revision_id, lock_version, updated_at
            ) VALUES (1, 1, 'chapter-1', 1, ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO voice_lines (
                id, chapter_revision_id, occurrence_id, order_index, kind,
                text, speaker_name, status, created_at
            ) VALUES ('voice-1', 'chapter-1', 'occurrence-1', 0, 'narration',
                      '雨落在站台。', '旁白', 'planned', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_revisions (
                id, project_id, chapter_index, chapter_revision_id, revision_no,
                source_manifest_hash, graph_hash, graph_json, schema_version,
                compiler_version, tachi_policy_version, status, created_at
            ) VALUES ('graph-1', 1, 1, 'chapter-1', 1, ?, ?, ?,
                      'legacy-v1', 'legacy-v1', 'legacy-v1', 'complete', ?)
            """,
            ("5" * 64, "6" * 64, json.dumps({"nodes": []}), now),
        )

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        script_row = connection.execute(
            """
            SELECT id, generator_version, script_json, coverage_json
              FROM chapter_script_revisions
             WHERE chapter_revision_id = 'chapter-1'
            """
        ).fetchone()
        script_head = connection.execute(
            """
            SELECT current_revision_id FROM chapter_script_heads
             WHERE project_id = 1 AND chapter_index = 1
            """
        ).fetchone()
        graph_script_id = connection.execute(
            "SELECT script_revision_id FROM vn_graph_revisions WHERE id = 'graph-1'"
        ).fetchone()[0]
        voice_version_row = connection.execute(
            """
            SELECT voice_line_id, version_no, content_hash, content_json
              FROM voice_line_versions WHERE voice_line_id = 'voice-1'
            """
        ).fetchone()
        graph_manifest_row = connection.execute(
            """
            SELECT binding_manifest, binding_manifest_hash
              FROM vn_graph_revisions WHERE id = 'graph-1'
            """
        ).fetchone()

    assert script_row is not None
    script_id, generator_version, script_raw, coverage_raw = script_row
    script = json.loads(script_raw)
    coverage = json.loads(coverage_raw)
    reconstructed = "".join(span["text"] for span in script["spans"])
    assert generator_version == "deterministic-backfill-v1"
    assert reconstructed == chapter_content
    assert coverage["coverage_ratio"] == 1.0
    assert coverage["covered_characters"] == len(chapter_content)
    assert script_head == (script_id,)
    assert graph_script_id == script_id
    voice_payload = json.loads(voice_version_row[3])
    assert voice_version_row[:2] == ("voice-1", 1)
    assert voice_payload["text"] == "雨落在站台。"
    assert voice_version_row[2] == hashlib.sha256(
        json.dumps(
            voice_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    graph_manifest = json.loads(graph_manifest_row[0])
    assert graph_manifest["manifest_version"] == "legacy-vngraph-v1"
    assert graph_manifest["script_revision"]["id"] == script_id
    assert graph_manifest_row[1] == hashlib.sha256(
        json.dumps(
            graph_manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def test_project_asset_history_migration_backfills_without_unsafe_merges(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "asset-history-backfill.db"
    upgraded = _run_alembic(database, "upgrade", "0020_chapter_script_semantic_pipeline")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-01T12:00:00+00:00"
    plan_key = "a" * 64
    generation_params = json.dumps(
        {
            "v2": {
                "logical_key": "audio:character-an:line-1",
                "taxonomy": {"character_id": "character-an"},
                "plans": {
                    plan_key: {
                        "source_kind": "legacy",
                        "source_revision_id": "legacy-source",
                        "source_hash": "b" * 64,
                        "asset_spec": {"asset_type": "audio"},
                        "render_spec": {"prompt": "line one"},
                    }
                },
            }
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'asset-backfill@example.com', 'hash', 'Asset Backfill', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '素材迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.executemany(
            """
            INSERT INTO assets (
                id, project_id, asset_type, target_name, prompt, status,
                generation_params, created_at, updated_at
            ) VALUES (?, 1, ?, ?, ?, 'completed', ?, ?, ?)
            """,
            [
                (1, "voice_line_v2", "line one", "line one", generation_params, now, now),
                (2, "audio", "line two", "line two", generation_params, now, now),
            ],
        )
        connection.executemany(
            """
            INSERT INTO asset_versions (
                id, asset_id, source_revision_id, version_no, cache_key,
                prompt, prompt_hash, prompt_version, safety_status,
                rights_metadata, created_at
            ) VALUES (?, 1, NULL, ?, 'duplicate-cache', ?, ?, 'legacy-v1',
                      'passed', '{}', ?)
            """,
            [
                ("version-1", 1, "line one", "1" * 64, now),
                ("version-2", 2, "line two", "2" * 64, now),
            ],
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        assets = connection.execute(
            "SELECT id, asset_type, logical_key, taxonomy_json FROM assets ORDER BY id"
        ).fetchall()
        versions = connection.execute(
            "SELECT source_kind, cache_key, asset_spec_json, render_spec_hash "
            "FROM asset_versions ORDER BY version_no"
        ).fetchall()
        plan = connection.execute(
            "SELECT source_kind, source_revision_id, source_hash FROM asset_plans"
        ).fetchone()
        item_count = connection.execute("SELECT COUNT(*) FROM asset_plan_items").fetchone()[0]

    assert [(row[0], row[1], row[2]) for row in assets] == [
        (1, "audio", "audio:character-an:line-1"),
        (2, "audio", "legacy:2"),
    ]
    assert json.loads(assets[0][3]) == {"character_id": "character-an"}
    assert [row[0] for row in versions] == ["legacy", "legacy"]
    assert versions[0][1] == "duplicate-cache"
    assert versions[1][1] == "duplicate-cache:legacy-duplicate:version-2"
    assert all(len(row[1]) <= 128 for row in versions)
    assert all(json.loads(row[2])["asset_type"] == "audio" for row in versions)
    assert all(len(row[3]) == 64 for row in versions)
    assert plan == ("legacy", "legacy-source", "b" * 64)
    assert item_count == 2


def test_v2_migration_schema_and_constraints_are_fingerprint_locked(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "fingerprint.db"
    result = _run_alembic(database, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    with sqlite3.connect(database) as connection:
        fingerprint = _sqlite_schema_fingerprint(connection)
        schema_sql = "\n".join(
            row[0]
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"
            )
        )
        storage_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("storage_objects")')
        }
        choice_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("choice_decisions")')
        }
        chapter_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("chapter_revisions")')
        }
        script_head_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("chapter_script_heads")')
        }
        graph_head_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("vn_graph_heads")')
        }
        publication_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute('PRAGMA foreign_key_list("project_publications")')
        }
        promotion_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                'PRAGMA foreign_key_list("story_path_promotion_records")'
            )
        }
        voice_version_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                'PRAGMA foreign_key_list("voice_line_versions")'
            )
        }
        usage_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("usage_reservations")')
        }
        generation_task_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("generation_tasks")')
        }
        ledger_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute(
                'PRAGMA table_info("usage_ledger_entries")'
            )
        }
        ledger_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                'PRAGMA foreign_key_list("usage_ledger_entries")'
            )
        }
        outline_head_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute(
                'PRAGMA table_info("story_path_outline_heads")'
            )
        }
        candidate_set_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute(
                'PRAGMA table_info("candidate_set_revisions")'
            )
        }
        candidate_set_head_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("candidate_set_heads")')
        }
        chapter_script_head_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("chapter_script_heads")')
        }
        script_resource_slot_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("script_resource_slots")')
        }
        vn_graph_revision_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("vn_graph_revisions")')
        }
        vn_graph_head_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
            for row in connection.execute('PRAGMA table_info("vn_graph_heads")')
        }
        project_release_columns = {
            row[1]: {"not_null": bool(row[3]), "primary_key": bool(row[5])}
                for row in connection.execute('PRAGMA table_info("project_releases")')
        }
        path_chapter_columns = {
            row[1]: {
                "not_null": bool(row[3]),
                "default": row[4],
                "primary_key": bool(row[5]),
            }
            for row in connection.execute('PRAGMA table_info("story_path_chapters")')
        }
        path_chapter_fks = {
            (row[2], row[3], row[4], row[6])
            for row in connection.execute(
                'PRAGMA foreign_key_list("story_path_chapters")'
            )
        }
        active_chapter_index_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='uq_story_path_chapter_display_index'"
        ).fetchone()[0]
        index_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            )
        }

    assert fingerprint == V2_SQLITE_SCHEMA_FINGERPRINT
    assert generation_task_columns["lease_token"] == {
        "not_null": False,
        "primary_key": False,
    }
    assert outline_head_columns["current_revision_id"]["not_null"] is False
    assert candidate_set_columns["candidates_json"]["not_null"] is False
    assert candidate_set_columns["content_hash"]["not_null"] is False
    assert candidate_set_head_columns["current_revision_id"]["not_null"] is False
    assert chapter_script_head_columns["chapter_revision_id"]["not_null"] is True
    assert chapter_script_head_columns["current_revision_id"]["not_null"] is False
    assert script_resource_slot_columns["lock_version"]["not_null"] is True
    assert vn_graph_revision_columns["script_revision_id"]["not_null"] is True
    assert vn_graph_revision_columns["binding_manifest"]["not_null"] is True
    assert vn_graph_revision_columns["binding_manifest_hash"]["not_null"] is True
    assert vn_graph_head_columns["script_revision_id"]["not_null"] is True
    assert vn_graph_head_columns["current_revision_id"]["not_null"] is False
    assert project_release_columns["publication_idempotency_key"]["not_null"] is False
    assert project_release_columns["publication_request_hash"]["not_null"] is False
    assert path_chapter_columns["status"] == {
        "not_null": True,
        "default": "'active'",
        "primary_key": False,
    }
    assert path_chapter_columns["detached_at"]["not_null"] is False
    assert path_chapter_columns["detached_by_outline_revision_id"]["not_null"] is False
    assert (
        "outline_revisions",
        "detached_by_outline_revision_id",
        "id",
        "RESTRICT",
    ) in path_chapter_fks
    assert "WHERE status = 'active'" in active_chapter_index_sql
    for constraint_name in (
        "ck_generation_task_status",
        "ck_generation_task_progress",
        "uq_generation_task_user_idempotency",
        "uq_task_event_seq",
        "ck_outbox_event_status",
        "ck_usage_reservation_status",
        "uq_bible_revision_no",
        "uq_bible_revision_generation_task",
        "uq_outline_revision_chapter",
        "uq_chapter_script_chapter_revision_no",
        "uq_script_resource_slot_key",
        "ck_script_resource_slot_role",
        "ck_script_resource_slot_status",
        "ck_script_resource_slot_lock_version",
        "uq_project_asset_logical_key",
        "uq_asset_version_cache_key",
        "ck_asset_plan_item_type",
        "uq_vngraph_script_revision_no",
        "ck_storage_object_visibility",
        "uq_voice_profile_version",
        "uq_asset_binding_slot",
        "uq_voice_line_occurrence",
        "uq_voice_line_version_no",
        "uq_voice_line_version_content",
        "uq_vngraph_binding_compile",
        "uq_state_snapshot_hash",
        "uq_branch_candidate_set_option",
        "ck_project_release_status",
        "ck_reading_session_status",
        "uq_choice_decision_idempotency",
        "uq_reading_bookmark_node",
        "ck_story_path_status",
        "ck_story_path_fork_provenance",
        "uq_story_path_fork_candidate",
        "uq_story_path_chapter_display_index",
        "uq_story_path_chapter_slot",
        "ck_story_path_chapter_display_index",
        "ck_story_path_chapter_status",
        "ck_story_path_chapter_detachment",
        "uq_outline_path_revision_no",
        "uq_outline_path_source_content",
        "uq_outline_revision_path_chapter",
        "uq_outline_revision_display_index",
        "ck_outline_revision_display_index",
        "uq_chapter_slot_revision_no",
        "uq_chapter_slot_context_content",
        "ck_story_path_outline_head_lock_version",
        "uq_candidate_set_path_checkpoint_revision",
        "uq_candidate_set_generation_task",
        "ck_candidate_set_candidate_count",
        "ck_candidate_set_head_lock_version",
        "uq_chapter_script_head_chapter_revision",
        "ck_chapter_script_head_lock_version",
        "uq_vn_graph_head_script_revision",
        "ck_vn_graph_head_lock_version",
        "uq_project_release_project_id_id",
        "uq_project_release_publication_idempotency",
        "uq_project_publication_active_release",
        "fk_project_publication_active_release",
        "ck_project_publication_activation",
        "ck_project_publication_lock_version",
        "uq_story_path_promotion_user_idempotency",
    ):
        assert constraint_name in schema_sql

    assert ("users", "owner_id", "id", "CASCADE") in storage_fks
    assert ("projects", "project_id", "id", "SET NULL") in storage_fks
    assert ("projects", "project_id", "id", "CASCADE") not in storage_fks
    assert ("reading_sessions", "session_id", "id", "CASCADE") in choice_fks
    assert ("branch_candidates", "candidate_id", "id", "RESTRICT") in choice_fks
    assert ("state_snapshots", "state_snapshot_id", "id", "SET NULL") in chapter_fks
    assert ("chapter_revisions", "chapter_revision_id", "id", "CASCADE") in script_head_fks
    assert (
        "chapter_script_revisions",
        "script_revision_id",
        "id",
        "CASCADE",
    ) in graph_head_fks
    assert ("projects", "project_id", "id", "CASCADE") in publication_fks
    assert (
        "project_releases",
        "project_id",
        "project_id",
        "RESTRICT",
    ) in publication_fks
    assert (
        "project_releases",
        "active_release_id",
        "id",
        "RESTRICT",
    ) in publication_fks
    assert ("users", "user_id", "id", "CASCADE") in promotion_fks
    assert ("projects", "project_id", "id", "CASCADE") in promotion_fks
    assert ("branch_candidates", "candidate_id", "id", "RESTRICT") in promotion_fks
    assert ("story_paths", "story_path_id", "id", "CASCADE") in promotion_fks
    assert ("voice_lines", "voice_line_id", "id", "RESTRICT") in voice_version_fks
    assert (
        "chapter_revisions",
        "chapter_revision_id",
        "id",
        "RESTRICT",
    ) in voice_version_fks
    assert (
        "asset_versions",
        "audio_asset_version_id",
        "id",
        "RESTRICT",
    ) in voice_version_fks
    assert usage_columns["task_id"]["not_null"] is True
    assert ledger_columns["project_id"]["not_null"] is False
    assert ledger_columns["activity_type"]["not_null"] is True
    assert ("projects", "project_id", "id", "SET NULL") in ledger_fks
    assert (
        "usage_reservations",
        "reservation_id",
        "id",
        "SET NULL",
    ) in ledger_fks
    assert ("generation_tasks", "task_id", "id", "SET NULL") in ledger_fks
    assert "UNIQUE (task_id)" in schema_sql
    assert {
        "ix_generation_task_active_source",
        "ix_usage_ledger_entries_project_id",
        "ix_task_event_replay",
        "ix_outbox_dispatch",
        "ix_chapter_revision_lookup",
        "ix_chapter_script_revision_chapter_created",
        "ix_script_resource_slot_semantic",
        "ix_vngraph_revision_script_created",
        "ix_asset_plan_source",
        "ix_asset_plan_item_asset",
        "ix_asset_binding_source",
        "ix_choice_decision_timeline",
        "uq_story_path_root_project",
        "ix_story_path_project_status",
        "ix_chapter_slot_project_path",
        "ix_story_path_chapter_order",
        "ix_outline_revision_path_created",
        "ix_chapter_revision_slot_created",
        "ix_story_path_chapters_current_revision_id",
        "ix_story_path_chapters_detached_by_outline_revision_id",
        "ix_candidate_set_path_checkpoint_created",
        "ix_candidate_set_source",
        "ix_candidate_set_revisions_content_hash",
        "ix_branch_candidates_candidate_set_revision_id",
        "ix_chapter_script_head_legacy_lookup",
        "ix_vn_graph_head_legacy_lookup",
        "ix_voice_line_version_chapter_created",
        "ix_story_path_promotion_records_user_id",
        "ix_story_path_promotion_records_project_id",
        "ix_story_path_promotion_candidate",
    }.issubset(index_names)


def test_v2_migration_table_order_is_postgresql_fk_safe():
    from app.database import Base
    import app.models  # noqa: F401

    created = set(LEGACY_FK_TARGETS)
    for revision, expected_tables in V2_TABLE_GROUPS.items():
        migration = _load_migration(revision)
        assert migration.TABLES == expected_tables
        for table_name in migration.TABLES:
            table = Base.metadata.tables[table_name]
            referenced_tables = {fk.column.table.name for fk in table.foreign_keys}
            referenced_tables -= getattr(migration, "DEFERRED_FOREIGN_KEY_TARGETS", {}).get(
                table_name, set()
            )
            unresolved = referenced_tables - created - {table_name}
            assert not unresolved, f"{table_name} references tables not yet created: {unresolved}"
            created.add(table_name)


def test_postgresql_offline_ddl_renders_complete_fk_safe_chain():
    pytest.importorskip("alembic")
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": "postgresql+psycopg://user:pass@localhost/if_line",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stderr

    ddl = result.stdout
    version_column_upgrade = (
        "ALTER TABLE alembic_version ALTER COLUMN version_num "
        "TYPE VARCHAR(128)"
    )
    assert version_column_upgrade in ddl
    assert ddl.index(version_column_upgrade) < ddl.index(
        "version_num='0017_backfill_asset_versions_and_tasks'"
    )
    for table_name in V2_TABLES:
        assert f"CREATE TABLE {table_name}" in ddl
    assert ddl.index("CREATE TABLE state_snapshots") < ddl.index("CREATE TABLE chapter_revisions")
    assert ddl.index("CREATE TABLE storage_objects") < ddl.index("CREATE TABLE voice_profiles")
    assert ddl.index("CREATE TABLE project_releases") < ddl.index("CREATE TABLE reading_sessions")
    assert ddl.index("CREATE TABLE reading_sessions") < ddl.index("CREATE TABLE choice_decisions")
    assert "ON DELETE CASCADE" in ddl
    assert "ON DELETE RESTRICT" in ddl
    assert "IF TG_OP = 'DELETE' THEN RETURN OLD" in ddl
    final_outline_guard_start = ddl.rindex(
        "CREATE OR REPLACE FUNCTION fn_0039_outline_chapter_update"
    )
    final_outline_guard = ddl[final_outline_guard_start:]
    assert (
        "CAST(NEW.characters AS JSONB) IS NOT DISTINCT FROM "
        "CAST(OLD.characters AS JSONB)"
    ) in final_outline_guard
    assert (
        "CAST(NEW.visual_keywords AS JSONB) IS NOT DISTINCT FROM "
        "CAST(OLD.visual_keywords AS JSONB)"
    ) in final_outline_guard
    assert (
        "NEW.characters IS NOT DISTINCT FROM OLD.characters"
        not in final_outline_guard
    )
    assert "ALTER TABLE asset_versions ALTER COLUMN seed TYPE BIGINT" in ddl
    for column_name in (
        "source_kind",
        "source_hash",
        "asset_spec_json",
        "render_spec_json",
        "render_spec_hash",
    ):
        assert ddl.count(f"ADD COLUMN {column_name}") == 1
    assert ddl.count("CONSTRAINT uq_asset_version_cache_key") == 1
    assert "NULL-duplicate" not in ddl

    incremental = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "alembic.ini",
            "upgrade",
            "0019_change_assets_seed_to_bigint:head",
            "--sql",
        ],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert incremental.returncode == 0, incremental.stderr
    incremental_ddl = incremental.stdout
    for table_name in (
        "chapter_script_revisions",
        "chapter_script_heads",
        "script_resource_slots",
    ):
        assert f"CREATE TABLE {table_name}" in incremental_ddl
    assert "ADD COLUMN script_revision_id" in incremental_ddl
    assert "fk_vn_graph_revisions_script_revision_id" in incremental_ddl
    assert "CREATE INDEX ix_vn_graph_revisions_script_revision_id" in incremental_ddl


def test_legacy_stamp_then_upgrade_preserves_rows_and_adds_v2(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email VARCHAR(255) NOT NULL
            );
            CREATE TABLE projects (
                id INTEGER PRIMARY KEY,
                title VARCHAR(200) NOT NULL,
                story_start TEXT NOT NULL,
                story_end TEXT NOT NULL
            );
            CREATE TABLE assets (
                id INTEGER PRIMARY KEY,
                project_id INTEGER NOT NULL,
                image_url VARCHAR(500),
                prompt TEXT
            );
            CREATE TABLE legacy_sentinel (
                id INTEGER PRIMARY KEY,
                payload TEXT NOT NULL
            );
            INSERT INTO users(id, email) VALUES (7, 'legacy@example.com');
            INSERT INTO projects(id, title, story_start, story_end)
                VALUES (11, '不可修改的旧项目', '旧开端', '旧结局');
            INSERT INTO assets(id, project_id, image_url, prompt)
                VALUES (13, 11, '/static/assets/legacy.png', '旧素材提示');
            INSERT INTO legacy_sentinel(id, payload) VALUES (17, 'keep-me');
            """
        )

    stamped = _run_alembic(database, "stamp", "0001_current_schema")
    assert stamped.returncode == 0, stamped.stderr
    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    with sqlite3.connect(database) as connection:
        project = connection.execute(
            """
            SELECT id, title, story_start, story_end, owner_id, visibility, is_draft, published_at
              FROM projects WHERE id = 11
            """
        ).fetchone()
        asset = connection.execute(
            "SELECT id, project_id, image_url, prompt FROM assets WHERE id = 13"
        ).fetchone()
        sentinel = connection.execute(
            "SELECT id, payload FROM legacy_sentinel WHERE id = 17"
        ).fetchone()
        v2_counts = {
            table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in V2_TABLES
        }
        version = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]

    assert project == (11, "不可修改的旧项目", "旧开端", "旧结局", None, "private", 1, None)
    assert asset == (13, 11, "/static/assets/legacy.png", "旧素材提示")
    assert sentinel == (17, "keep-me")
    assert v2_counts["story_paths"] == 1
    assert v2_counts["story_path_outline_heads"] == 1
    assert all(
        count == 0
        for table, count in v2_counts.items()
        if table not in {"story_paths", "story_path_outline_heads"}
    )
    assert version == ALEMBIC_HEAD


def test_path_revision_binding_backfills_legacy_display_and_context(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "path-revision-binding.db"
    upgraded = _run_alembic(database, "upgrade", "0022_story_path_identity")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'path-binding@example.com', 'hash', 'Path Binding', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '路径迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            ("a" * 64, "b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("c" * 64, "d" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revision_chapters (
                id, outline_revision_id, chapter_index, title, summary,
                characters, visual_keywords, content_hash
            ) VALUES ('outline-chapter-1', 'outline-1', 7, '第七章', '摘要',
                      '[]', '[]', ?)
            """,
            ("e" * 64,),
        )
        connection.execute(
            """
            INSERT INTO chapter_revisions (
                id, project_id, chapter_index, bible_revision_id,
                outline_revision_id, revision_no, source_hash, content_hash,
                content, status, created_by, created_at
            ) VALUES ('chapter-1', 1, 7, 'bible-1', 'outline-1', 1,
                      ?, ?, '正文', 'complete', 1, ?)
            """,
            ("f" * 64, "1" * 64, now),
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "0023_path_revision_binding")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        outline_row = connection.execute(
            "SELECT story_path_chapter_id, display_index "
            "FROM outline_revision_chapters WHERE id = 'outline-chapter-1'"
        ).fetchone()
        chapter_row = connection.execute(
            "SELECT chapter_slot_id, created_for_story_path_id, context_manifest, context_hash "
            "FROM chapter_revisions WHERE id = 'chapter-1'"
        ).fetchone()

    assert outline_row == (None, 7)
    assert chapter_row[:2] == (None, None)
    assert json.loads(chapter_row[2]) == {}
    assert chapter_row[3] == "f" * 64


def test_root_story_path_backfill_preserves_order_heads_and_roundtrips(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "root-story-path-backfill.db"
    upgraded = _run_alembic(database, "upgrade", "0035_script_resource_slot_lock")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    later = "2026-08-08T13:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'root-backfill@example.com', 'hash', 'Root Backfill', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.executemany(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (?, 1, ?, '开始', '结束', 'private', 1, ?, ?)
            """,
            [
                (1, "旧项目", now, later),
                (2, "已有新路径项目", now, later),
            ],
        )
        connection.execute(
            """
            INSERT INTO story_paths (
                id, project_id, title, status, lock_version, created_at, updated_at
            ) VALUES ('existing-root', 2, 'Main', 'active', 3, ?, ?)
            """,
            (now, later),
        )
        connection.execute(
            """
            INSERT INTO story_path_outline_heads (
                story_path_id, current_revision_id, lock_version, updated_at
            ) VALUES ('existing-root', NULL, 4, ?)
            """,
            (later,),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            ("a" * 64, "b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("c" * 64, "d" * 64, now),
        )
        connection.executemany(
            """
            INSERT INTO outline_revision_chapters (
                id, outline_revision_id, chapter_index, display_index,
                title, summary, characters, visual_keywords, content_hash
            ) VALUES (?, 'outline-1', ?, ?, ?, '摘要', '[]', '[]', ?)
            """,
            [
                ("outline-chapter-1", 1, 1, "第一章", "e" * 64),
                ("outline-chapter-3", 3, 3, "第三章", "f" * 64),
            ],
        )
        connection.executemany(
            """
            INSERT INTO chapter_revisions (
                id, project_id, chapter_index, bible_revision_id,
                outline_revision_id, revision_no, source_hash,
                context_manifest, context_hash, content_hash, content,
                status, created_by, created_at
            ) VALUES (?, 1, ?, 'bible-1', 'outline-1', ?, ?, '{}', ?, ?, ?,
                      'complete', 1, ?)
            """,
            [
                ("chapter-1-r1", 1, 1, "1" * 64, "2" * 64, "3" * 64, "正文 1.1", now),
                ("chapter-1-r2", 1, 2, "4" * 64, "5" * 64, "6" * 64, "正文 1.2", later),
                ("chapter-2-r1", 2, 1, "7" * 64, "8" * 64, "9" * 64, "正文 2.1", now),
            ],
        )
        connection.executemany(
            """
            INSERT INTO chapter_heads (
                project_id, chapter_index, current_revision_id,
                lock_version, updated_at
            ) VALUES (1, ?, ?, ?, ?)
            """,
            [
                (1, "chapter-1-r2", 5, later),
                (2, "chapter-2-r1", 2, now),
            ],
        )
        connection.execute(
            """
            INSERT INTO chapter_outlines (
                id, project_id, chapter_index, title, status, created_at, updated_at
            ) VALUES (40, 1, 4, '第四章', 'pending', ?, ?)
            """,
            (now, later),
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        roots = connection.execute(
            """
            SELECT project_id, id
              FROM story_paths
             WHERE parent_path_id IS NULL
             ORDER BY project_id
            """
        ).fetchall()
        root_id = roots[0][1]
        placements = connection.execute(
            """
            SELECT chapter.display_index, chapter.id,
                   chapter.predecessor_path_chapter_id,
                   chapter.current_revision_id, chapter.lock_version,
                   chapter.chapter_slot_id, slot.project_id,
                   slot.created_for_story_path_id
              FROM story_path_chapters AS chapter
              JOIN chapter_slots AS slot ON slot.id = chapter.chapter_slot_id
             WHERE chapter.story_path_id = ?
             ORDER BY chapter.display_index
            """,
            (root_id,),
        ).fetchall()
        bindings = connection.execute(
            """
            SELECT id, chapter_index, chapter_slot_id, created_for_story_path_id
              FROM chapter_revisions
             WHERE project_id = 1
             ORDER BY chapter_index, revision_no
            """
        ).fetchall()
        outline_heads = connection.execute(
            """
            SELECT story_path_id, current_revision_id, lock_version
              FROM story_path_outline_heads
             ORDER BY story_path_id
            """
        ).fetchall()
        scratch_counts = (
            connection.execute("SELECT COUNT(*) FROM _alembic_0036_created").fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM _alembic_0036_revision_binding"
            ).fetchone()[0],
        )

    assert roots[1] == (2, "existing-root")
    assert [row[0] for row in placements] == [1, 2, 3, 4]
    assert [row[2] for row in placements] == [
        None,
        placements[0][1],
        placements[1][1],
        placements[2][1],
    ]
    assert [row[3] for row in placements] == [
        "chapter-1-r2",
        "chapter-2-r1",
        None,
        None,
    ]
    assert [row[4] for row in placements] == [5, 2, 1, 1]
    assert all(row[6:] == (1, root_id) for row in placements)
    assert bindings == [
        ("chapter-1-r1", 1, placements[0][5], root_id),
        ("chapter-1-r2", 1, placements[0][5], root_id),
        ("chapter-2-r1", 2, placements[1][5], root_id),
    ]
    assert (root_id, None, 1) in outline_heads
    assert ("existing-root", None, 4) in outline_heads
    assert scratch_counts == (10, 3)

    first_ids = [root_id, *[row[1] for row in placements], *[row[5] for row in placements]]
    downgraded = _run_alembic(
        database,
        "downgrade",
        "0035_script_resource_slot_lock",
    )
    assert downgraded.returncode == 0, downgraded.stderr

    with sqlite3.connect(database) as connection:
        remaining_roots = connection.execute(
            "SELECT project_id, id FROM story_paths ORDER BY project_id"
        ).fetchall()
        remaining_bindings = connection.execute(
            """
            SELECT chapter_slot_id, created_for_story_path_id
              FROM chapter_revisions
             WHERE project_id = 1
             ORDER BY chapter_index, revision_no
            """
        ).fetchall()
        legacy_heads = connection.execute(
            """
            SELECT chapter_index, current_revision_id, lock_version
              FROM chapter_heads
             WHERE project_id = 1
             ORDER BY chapter_index
            """
        ).fetchall()
        scratch_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_0036_%'"
            )
        }

    assert remaining_roots == [(2, "existing-root")]
    assert remaining_bindings == [(None, None), (None, None), (None, None)]
    assert legacy_heads == [(1, "chapter-1-r2", 5), (2, "chapter-2-r1", 2)]
    assert not scratch_tables

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        root_id_again = connection.execute(
            "SELECT id FROM story_paths WHERE project_id = 1 AND parent_path_id IS NULL"
        ).fetchone()[0]
        placements_again = connection.execute(
            """
            SELECT id, chapter_slot_id
              FROM story_path_chapters
             WHERE story_path_id = ?
             ORDER BY display_index
            """,
            (root_id_again,),
        ).fetchall()
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    second_ids = [
        root_id_again,
        *[row[0] for row in placements_again],
        *[row[1] for row in placements_again],
    ]
    assert second_ids == first_ids
    assert version == ALEMBIC_HEAD


def test_immutable_artifact_relation_backfill_is_exact_and_reversible(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "immutable-artifact-backfill.db"
    upgraded = _run_alembic(database, "upgrade", "0035_script_resource_slot_lock")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    later = "2026-08-08T13:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'artifact-backfill@example.com', 'hash', 'Artifact Backfill', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '精确产物迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, later),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            ("a" * 64, "b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("c" * 64, "d" * 64, now),
        )
        connection.executemany(
            """
            INSERT INTO outline_revision_chapters (
                id, outline_revision_id, chapter_index, display_index,
                title, summary, characters, visual_keywords, content_hash
            ) VALUES (?, 'outline-1', ?, ?, ?, '摘要', '[]', '[]', ?)
            """,
            [
                ("outline-chapter-1", 1, 1, "第一章", "e" * 64),
                ("outline-chapter-2", 2, 2, "第二章", "f" * 64),
            ],
        )
        connection.execute(
            """
            INSERT INTO project_content_heads (
                project_id, current_bible_revision_id,
                current_outline_revision_id, published_release_id,
                lifecycle_status, lock_version, updated_at
            ) VALUES (1, 'bible-1', 'outline-1', NULL, 'draft', 9, ?)
            """,
            (later,),
        )
        connection.executemany(
            """
            INSERT INTO chapter_revisions (
                id, project_id, chapter_index, bible_revision_id,
                outline_revision_id, revision_no, source_hash,
                context_manifest, context_hash, content_hash, content,
                status, created_by, created_at
            ) VALUES (?, 1, ?, 'bible-1', 'outline-1', 1, ?, '{}', ?, ?, ?,
                      'complete', 1, ?)
            """,
            [
                ("chapter-1", 1, "1" * 64, "2" * 64, "3" * 64, "正文 1", now),
                ("chapter-2", 2, "4" * 64, "5" * 64, "6" * 64, "正文 2", later),
            ],
        )
        connection.execute(
            """
            INSERT INTO chapter_heads (
                project_id, chapter_index, current_revision_id,
                lock_version, updated_at
            ) VALUES (1, 1, 'chapter-1', 5, ?)
            """,
            (later,),
        )
        connection.executemany(
            """
            INSERT INTO chapter_script_revisions (
                id, project_id, chapter_index, chapter_revision_id,
                bible_revision_id, outline_revision_id, revision_no,
                source_hash, script_hash, script_json, coverage_json,
                schema_version, generator_version, status,
                created_by, created_at
            ) VALUES (?, 1, ?, ?, 'bible-1', 'outline-1', 1,
                      ?, ?, '{}', '{}', 'script-v1', 'legacy-v1', 'complete', 1, ?)
            """,
            [
                ("script-1", 1, "chapter-1", "7" * 64, "8" * 64, now),
                ("script-2", 2, "chapter-2", "9" * 64, "0" * 64, later),
            ],
        )
        connection.execute(
            """
            INSERT INTO chapter_script_heads (
                id, project_id, chapter_index, chapter_revision_id,
                current_revision_id, lock_version, updated_at
            ) VALUES ('existing-script-head', 1, 1, 'chapter-1',
                      'script-1', 7, ?)
            """,
            (later,),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_revisions (
                id, project_id, chapter_index, chapter_revision_id,
                script_revision_id, revision_no, binding_manifest,
                binding_manifest_hash, source_manifest_hash, graph_hash,
                graph_json, schema_version, compiler_version,
                tachi_policy_version, status, created_at
            ) VALUES ('graph-1', 1, 1, 'chapter-1', 'script-1', 1, '{}',
                      ?, ?, ?, '{}', 'graph-v1', 'legacy-v1',
                      'legacy-v1', 'complete', ?)
            """,
            ("a" * 64, "b" * 64, "c" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_heads (
                id, project_id, chapter_index, script_revision_id,
                current_revision_id, lock_version, updated_at
            ) VALUES ('existing-graph-head', 1, 1, 'script-1',
                      'graph-1', 8, ?)
            """,
            (later,),
        )
        connection.execute(
            """
            INSERT INTO story_nodes (
                id, project_id, node_type, checkpoint_key,
                content_revision_id, payload, created_at
            ) VALUES ('checkpoint-1', 1, 'checkpoint', 'choice-1',
                      'chapter-1', '{}', ?)
            """,
            (now,),
        )
        connection.executemany(
            """
            INSERT INTO branch_candidates (
                id, project_id, checkpoint_node_id, option_key,
                state_delta, candidate_status, predicted_probability, created_at
            ) VALUES (?, 1, 'checkpoint-1', ?, ?, 'preview_ready', ?, ?)
            """,
            [
                ("candidate-a", "option-a", '{"route":"a"}', 0.6, now),
                ("candidate-b", "option-b", '{"route":"b"}', 0.4, later),
            ],
        )
        connection.commit()

    rooted = _run_alembic(database, "upgrade", "0036_backfill_root_story_paths")
    assert rooted.returncode == 0, rooted.stderr
    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        root_id = connection.execute(
            "SELECT id FROM story_paths WHERE project_id = 1 AND parent_path_id IS NULL"
        ).fetchone()[0]
        outline = connection.execute(
            "SELECT story_path_id FROM outline_revisions WHERE id = 'outline-1'"
        ).fetchone()[0]
        outline_chapters = connection.execute(
            """
            SELECT outline.display_index, outline.story_path_chapter_id,
                   path.story_path_id
              FROM outline_revision_chapters AS outline
              JOIN story_path_chapters AS path
                ON path.id = outline.story_path_chapter_id
             WHERE outline.outline_revision_id = 'outline-1'
             ORDER BY outline.display_index
            """
        ).fetchall()
        outline_head = connection.execute(
            """
            SELECT current_revision_id, lock_version
              FROM story_path_outline_heads
             WHERE story_path_id = ?
            """,
            (root_id,),
        ).fetchone()
        repaired_outline = connection.execute(
            """
            SELECT id, parent_revision_id, revision_no, source_hash,
                   content_hash, status, legacy_source_table, legacy_source_id
              FROM outline_revisions
             WHERE parent_revision_id = 'outline-1'
            """
        ).fetchone()
        repaired_chapters = connection.execute(
            """
            SELECT story_path_chapter_id, display_index, title, summary,
                   conflict, characters, scene, emotion, visual_keywords,
                   content_hash, legacy_source_id
              FROM outline_revision_chapters
             WHERE outline_revision_id = ?
             ORDER BY display_index
            """,
            (repaired_outline[0],),
        ).fetchall()
        legacy_outline = connection.execute(
            """
            SELECT source_hash, content_hash, status
              FROM outline_revisions
             WHERE id = 'outline-1'
            """
        ).fetchone()
        project_outline_head = connection.execute(
            """
            SELECT current_outline_revision_id, lock_version
              FROM project_content_heads
             WHERE project_id = 1
            """
        ).fetchone()
        repair_count = connection.execute(
            "SELECT COUNT(*) FROM _alembic_0043_outline_repair"
        ).fetchone()[0]
        candidate_set = connection.execute(
            """
            SELECT id, story_path_id, checkpoint_node_id,
                   chapter_revision_id, state_snapshot_id, revision_no,
                   source_hash, candidate_count, candidates_json, content_hash
              FROM candidate_set_revisions
            """
        ).fetchone()
        candidate_bindings = connection.execute(
            """
            SELECT id, candidate_set_revision_id
              FROM branch_candidates
             ORDER BY id
            """
        ).fetchall()
        candidate_head = connection.execute(
            """
            SELECT current_revision_id, lock_version
              FROM candidate_set_heads
             WHERE story_path_id = ? AND checkpoint_node_id = 'checkpoint-1'
            """,
            (root_id,),
        ).fetchone()
        state_snapshot = connection.execute(
            "SELECT id, project_id, state_hash, state_json FROM state_snapshots"
        ).fetchone()
        script_heads = connection.execute(
            """
            SELECT chapter_revision_id, current_revision_id, lock_version, id
              FROM chapter_script_heads
             ORDER BY chapter_revision_id
            """
        ).fetchall()
        graph_heads = connection.execute(
            """
            SELECT script_revision_id, current_revision_id, lock_version, id
              FROM vn_graph_heads
             ORDER BY script_revision_id
            """
        ).fetchall()
        scratch_counts = {
            "created": connection.execute(
                "SELECT COUNT(*) FROM _alembic_0037_created"
            ).fetchone()[0],
            "outlines": connection.execute(
                "SELECT COUNT(*) FROM _alembic_0037_outline_binding"
            ).fetchone()[0],
            "outline_chapters": connection.execute(
                "SELECT COUNT(*) FROM _alembic_0037_outline_chapter_binding"
            ).fetchone()[0],
            "outline_heads": connection.execute(
                "SELECT COUNT(*) FROM _alembic_0037_outline_head_state"
            ).fetchone()[0],
            "candidates": connection.execute(
                "SELECT COUNT(*) FROM _alembic_0037_candidate_binding"
            ).fetchone()[0],
        }

    assert outline == root_id
    assert [row[0] for row in outline_chapters] == [1, 2]
    assert all(row[1] and row[2] == root_id for row in outline_chapters)
    repaired_id = repaired_outline[0]
    assert outline_head == (repaired_id, 2)
    assert project_outline_head == (repaired_id, 10)
    assert repaired_outline[1:3] == ("outline-1", 2)
    assert len(repaired_outline[3]) == 64
    assert repaired_outline[5:] == (
        "ready",
        "outline_contract_repair_v1",
        "outline-1",
    )
    expected_items = [
        {
            "story_path_chapter_id": row[0],
            "display_index": row[1],
            "title": row[2],
            "summary": row[3],
            "conflict": row[4],
            "characters": json.loads(row[5]),
            "scene": row[6],
            "emotion": row[7],
            "visual_keywords": json.loads(row[8]),
        }
        for row in repaired_chapters
    ]
    expected_hash = lambda value: hashlib.sha256(  # noqa: E731
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert repaired_outline[4] == expected_hash(expected_items)
    assert [row[9] for row in repaired_chapters] == [
        expected_hash(item) for item in expected_items
    ]
    assert [row[10] for row in repaired_chapters] == [
        "outline-chapter-1",
        "outline-chapter-2",
    ]
    assert legacy_outline == ("c" * 64, "d" * 64, "approved")
    assert repair_count == 1
    assert candidate_set[1:4] == (root_id, "checkpoint-1", "chapter-1")
    assert candidate_set[4] == state_snapshot[0]
    assert candidate_set[5] == 1
    assert len(candidate_set[6]) == 64
    assert candidate_set[7:] == (2, None, None)
    assert candidate_bindings == [
        ("candidate-a", candidate_set[0]),
        ("candidate-b", candidate_set[0]),
    ]
    assert candidate_head == (None, 1)
    assert state_snapshot[1:3] == (1, hashlib.sha256(b"{}").hexdigest())
    assert json.loads(state_snapshot[3]) == {}
    assert script_heads[0] == ("chapter-1", "script-1", 7, "existing-script-head")
    assert script_heads[1][:3] == ("chapter-2", None, 1)
    assert graph_heads[0] == ("script-1", "graph-1", 8, "existing-graph-head")
    assert graph_heads[1][:3] == ("script-2", None, 1)
    assert scratch_counts == {
        "created": 5,
        "outlines": 1,
        "outline_chapters": 2,
        "outline_heads": 1,
        "candidates": 2,
    }

    stable_ids = (
        candidate_set[0],
        script_heads[1][3],
        graph_heads[1][3],
        repaired_id,
    )
    downgraded = _run_alembic(
        database,
        "downgrade",
        "0036_backfill_root_story_paths",
    )
    assert downgraded.returncode == 0, downgraded.stderr

    with sqlite3.connect(database) as connection:
        restored_outline = connection.execute(
            "SELECT story_path_id FROM outline_revisions WHERE id = 'outline-1'"
        ).fetchone()[0]
        restored_outline_chapters = connection.execute(
            """
            SELECT story_path_chapter_id
              FROM outline_revision_chapters
             WHERE outline_revision_id = 'outline-1'
             ORDER BY display_index
            """
        ).fetchall()
        restored_outline_head = connection.execute(
            "SELECT current_revision_id, lock_version FROM story_path_outline_heads"
        ).fetchone()
        restored_candidates = connection.execute(
            "SELECT candidate_set_revision_id FROM branch_candidates ORDER BY id"
        ).fetchall()
        remaining_sets = connection.execute(
            "SELECT COUNT(*) FROM candidate_set_revisions"
        ).fetchone()[0]
        remaining_snapshots = connection.execute(
            "SELECT COUNT(*) FROM state_snapshots"
        ).fetchone()[0]
        remaining_script_heads = connection.execute(
            "SELECT id FROM chapter_script_heads ORDER BY id"
        ).fetchall()
        remaining_graph_heads = connection.execute(
            "SELECT id FROM vn_graph_heads ORDER BY id"
        ).fetchall()
        scratch_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_0037_%'"
            )
        }
        remaining_repaired_outlines = connection.execute(
            "SELECT COUNT(*) FROM outline_revisions WHERE parent_revision_id = 'outline-1'"
        ).fetchone()[0]
        restored_project_outline_head = connection.execute(
            """
            SELECT current_outline_revision_id, lock_version
              FROM project_content_heads
             WHERE project_id = 1
            """
        ).fetchone()

    assert restored_outline is None
    assert restored_outline_chapters == [(None,), (None,)]
    assert restored_outline_head == (None, 1)
    assert restored_project_outline_head == ("outline-1", 9)
    assert remaining_repaired_outlines == 0
    assert restored_candidates == [(None,), (None,)]
    assert remaining_sets == 0
    assert remaining_snapshots == 0
    assert remaining_script_heads == [("existing-script-head",)]
    assert remaining_graph_heads == [("existing-graph-head",)]
    assert not scratch_tables

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        stable_ids_again = (
            connection.execute("SELECT id FROM candidate_set_revisions").fetchone()[0],
            connection.execute(
                "SELECT id FROM chapter_script_heads WHERE chapter_revision_id = 'chapter-2'"
            ).fetchone()[0],
            connection.execute(
                "SELECT id FROM vn_graph_heads WHERE script_revision_id = 'script-2'"
            ).fetchone()[0],
            connection.execute(
                "SELECT id FROM outline_revisions WHERE parent_revision_id = 'outline-1'"
            ).fetchone()[0],
        )
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert stable_ids_again == stable_ids
    assert version == ALEMBIC_HEAD


def test_legacy_draft_outline_head_is_cleared_and_downgrade_restores_it(
    tmp_path: Path,
):
    pytest.importorskip("alembic")
    from app.application.hashing import content_hash
    from app.models import Project, User
    from app.models_v2 import (
        ChapterSlot,
        OutlineChapter,
        OutlineRevision,
        ProjectContentHead,
        StoryBibleRevision,
        StoryPath,
        StoryPathOutlineHead,
    )

    database = tmp_path / "legacy-draft-outline.db"
    upgraded = _run_alembic(database, "upgrade", "0042_align_postgresql_schema")
    assert upgraded.returncode == 0, upgraded.stderr

    engine = create_engine(f"sqlite:///{database.as_posix()}")
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = factory()
    try:
        user = User(
            email="legacy-draft@example.com",
            password_hash="hash",
            display_name="Legacy Draft",
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            title="Legacy Draft",
            story_start="Start",
            story_end="End",
        )
        db.add(project)
        db.flush()
        path = StoryPath(project_id=project.id, title="Main")
        db.add(path)
        db.flush()
        bible_content = {"world": "legacy"}
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash="a" * 64,
            content_hash=content_hash(bible_content),
            content_json=bible_content,
            status="complete",
            created_by=user.id,
        )
        db.add(bible)
        db.flush()
        slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
        db.add(slot)
        db.flush()
        placement_id = str(uuid4())
        created_at = datetime.now(timezone.utc)
        db.execute(
            text(
                "INSERT INTO story_path_chapters ("
                "id, story_path_id, chapter_slot_id, display_index, "
                "predecessor_path_chapter_id, inherited_from_path_chapter_id, "
                "lock_version, created_at, updated_at"
                ") VALUES ("
                ":id, :story_path_id, :chapter_slot_id, 1, NULL, NULL, 1, "
                ":created_at, :updated_at)"
            ),
            {
                "id": placement_id,
                "story_path_id": path.id,
                "chapter_slot_id": slot.id,
                "created_at": created_at,
                "updated_at": created_at,
            },
        )
        item = {
            "story_path_chapter_id": placement_id,
            "display_index": 1,
            "title": "Draft chapter",
            "summary": "Not reviewed",
            "conflict": None,
            "characters": [],
            "scene": None,
            "emotion": None,
            "visual_keywords": [],
        }
        outline = OutlineRevision(
            project_id=project.id,
            story_path_id=path.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash="b" * 64,
            content_hash=content_hash([item]),
            status="draft",
            created_by=user.id,
        )
        db.add(outline)
        db.flush()
        db.add(
            OutlineChapter(
                outline_revision_id=outline.id,
                chapter_index=1,
                story_path_chapter_id=placement_id,
                display_index=1,
                title=item["title"],
                summary=item["summary"],
                characters=[],
                visual_keywords=[],
                content_hash=content_hash(item),
            )
        )
        db.add(
            StoryPathOutlineHead(
                story_path_id=path.id,
                current_revision_id=outline.id,
                lock_version=3,
            )
        )
        db.add(
            ProjectContentHead(
                project_id=project.id,
                current_bible_revision_id=bible.id,
                current_outline_revision_id=outline.id,
                lock_version=7,
            )
        )
        db.commit()
        path_id = path.id
        outline_id = outline.id
        project_id = project.id
    finally:
        db.close()
        engine.dispose()

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr
    with sqlite3.connect(database) as connection:
        path_head = connection.execute(
            "SELECT current_revision_id, lock_version FROM story_path_outline_heads "
            "WHERE story_path_id = ?",
            (path_id,),
        ).fetchone()
        project_head = connection.execute(
            "SELECT current_outline_revision_id, lock_version FROM project_content_heads "
            "WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        legacy = connection.execute(
            "SELECT status FROM outline_revisions WHERE id = ?",
            (outline_id,),
        ).fetchone()
        clones = connection.execute(
            "SELECT COUNT(*) FROM outline_revisions WHERE parent_revision_id = ?",
            (outline_id,),
        ).fetchone()[0]
        repair = connection.execute(
            "SELECT old_revision_id, new_revision_id, project_head_matched "
            "FROM _alembic_0043_outline_repair WHERE story_path_id = ?",
            (path_id,),
        ).fetchone()

    assert path_head == (None, 4)
    assert project_head == (None, 8)
    assert legacy == ("draft",)
    assert clones == 0
    assert repair == (outline_id, None, 1)

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0042_align_postgresql_schema",
    )
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        restored_path_head = connection.execute(
            "SELECT current_revision_id, lock_version FROM story_path_outline_heads "
            "WHERE story_path_id = ?",
            (path_id,),
        ).fetchone()
        restored_project_head = connection.execute(
            "SELECT current_outline_revision_id, lock_version FROM project_content_heads "
            "WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        scratch_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = '_alembic_0043_outline_repair'"
        ).fetchone()

    assert restored_path_head == (outline_id, 3)
    assert restored_project_head == (outline_id, 7)
    assert scratch_exists is None

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT current_revision_id FROM story_path_outline_heads "
            "WHERE story_path_id = ?",
            (path_id,),
        ).fetchone() == (None,)


def test_project_publication_backfill_preserves_active_release_and_roundtrips(
    tmp_path: Path,
):
    pytest.importorskip("alembic")
    database = tmp_path / "project-publication-backfill.db"
    upgraded = _run_alembic(
        database,
        "upgrade",
        "0037_backfill_immutable_artifact_relations",
    )
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    later = "2026-08-08T13:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'release-backfill@example.com', 'hash', 'Release Backfill', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.executemany(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, published_at, created_at, updated_at
            ) VALUES (?, 1, ?, '开始', '结束', ?, ?, ?, ?, ?)
            """,
            [
                (1, "旧 Head 指针", "public", 0, now, now, later),
                (2, "唯一公开版本", "private", 1, None, now, later),
                (3, "现有目标指针", "public", 0, later, now, later),
            ],
        )
        connection.executemany(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES (?, ?, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            [
                ("bible-1", 1, "1" * 64, "4" * 64, now),
                ("bible-2", 2, "2" * 64, "5" * 64, now),
                ("bible-3", 3, "3" * 64, "6" * 64, now),
            ],
        )
        connection.executemany(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no,
                source_hash, content_hash, status, created_by, created_at
            ) VALUES (?, ?, ?, 1, ?, ?, 'approved', 1, ?)
            """,
            [
                ("outline-1", 1, "bible-1", "7" * 64, "a" * 64, now),
                ("outline-2", 2, "bible-2", "8" * 64, "b" * 64, now),
                ("outline-3", 3, "bible-3", "9" * 64, "c" * 64, now),
            ],
        )
        connection.executemany(
            """
            INSERT INTO project_releases (
                id, project_id, version, status, bible_revision_id,
                outline_revision_id, manifest_json, manifest_hash,
                created_by, created_at, published_at, withdrawn_at
            ) VALUES (?, ?, ?, 'published', ?, ?, ?, ?, 1, ?, ?, NULL)
            """,
            [
                (
                    "release-1-active",
                    1,
                    1,
                    "bible-1",
                    "outline-1",
                    '{"route":"active-from-legacy-head"}',
                    "d" * 64,
                    now,
                    now,
                ),
                (
                    "release-1-newer",
                    1,
                    2,
                    "bible-1",
                    "outline-1",
                    '{"route":"newer-but-not-active"}',
                    "e" * 64,
                    later,
                    later,
                ),
                (
                    "release-2-active",
                    2,
                    1,
                    "bible-2",
                    "outline-2",
                    '{"route":"only-published"}',
                    "f" * 64,
                    now,
                    now,
                ),
                (
                    "release-3-old",
                    3,
                    1,
                    "bible-3",
                    "outline-3",
                    '{"route":"superseded"}',
                    "0" * 64,
                    now,
                    now,
                ),
                (
                    "release-3-active",
                    3,
                    2,
                    "bible-3",
                    "outline-3",
                    '{"route":"active-from-publication"}',
                    "a" * 64,
                    later,
                    later,
                ),
            ],
        )
        connection.executemany(
            """
            INSERT INTO project_content_heads (
                project_id, current_bible_revision_id,
                current_outline_revision_id, published_release_id,
                lifecycle_status, lock_version, updated_at
            ) VALUES (?, ?, ?, ?, 'published', ?, ?)
            """,
            [
                (1, "bible-1", "outline-1", "release-1-active", 4, later),
                (3, "bible-3", "outline-3", "release-3-active", 8, later),
            ],
        )
        connection.execute(
            """
            INSERT INTO project_publications (
                project_id, active_release_id, published_at,
                lock_version, updated_at
            ) VALUES (3, 'release-3-active', ?, 7, ?)
            """,
            (later, later),
        )
        manifests_before = connection.execute(
            """
            SELECT id, manifest_json, manifest_hash, published_at
              FROM project_releases
             ORDER BY id
            """
        ).fetchall()
        project_state_before = connection.execute(
            "SELECT id, visibility, is_draft, published_at FROM projects ORDER BY id"
        ).fetchall()
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        releases = connection.execute(
            "SELECT id, status FROM project_releases ORDER BY id"
        ).fetchall()
        publications = connection.execute(
            """
            SELECT project_id, active_release_id, published_at, lock_version, updated_at
              FROM project_publications
             ORDER BY project_id
            """
        ).fetchall()
        manifests_after = connection.execute(
            """
            SELECT id, manifest_json, manifest_hash, published_at
              FROM project_releases
             ORDER BY id
            """
        ).fetchall()
        project_state_after = connection.execute(
            "SELECT id, visibility, is_draft, published_at FROM projects ORDER BY id"
        ).fetchall()
        scratch_counts = (
            connection.execute(
                "SELECT COUNT(*) FROM _alembic_0038_release_status"
            ).fetchone()[0],
            connection.execute(
                "SELECT COUNT(*) FROM _alembic_0038_created_publication"
            ).fetchone()[0],
        )

    assert releases == [
        ("release-1-active", "published"),
        ("release-1-newer", "superseded"),
        ("release-2-active", "published"),
        ("release-3-active", "published"),
        ("release-3-old", "superseded"),
    ]
    assert publications == [
        (1, "release-1-active", now, 1, now),
        (2, "release-2-active", now, 1, now),
        (3, "release-3-active", later, 7, later),
    ]
    assert manifests_after == manifests_before
    assert project_state_after == project_state_before
    assert scratch_counts == (2, 2)

    downgraded = _run_alembic(
        database,
        "downgrade",
        "0037_backfill_immutable_artifact_relations",
    )
    assert downgraded.returncode == 0, downgraded.stderr

    with sqlite3.connect(database) as connection:
        restored_releases = connection.execute(
            "SELECT id, status FROM project_releases ORDER BY id"
        ).fetchall()
        restored_publications = connection.execute(
            """
            SELECT project_id, active_release_id, published_at, lock_version, updated_at
              FROM project_publications
             ORDER BY project_id
            """
        ).fetchall()
        restored_manifests = connection.execute(
            """
            SELECT id, manifest_json, manifest_hash, published_at
              FROM project_releases
             ORDER BY id
            """
        ).fetchall()
        scratch_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE '_alembic_0038_%'"
            )
        }

    assert all(status == "published" for _, status in restored_releases)
    assert restored_publications == [
        (3, "release-3-active", later, 7, later),
    ]
    assert restored_manifests == manifests_before
    assert not scratch_tables

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    with sqlite3.connect(database) as connection:
        publications_again = connection.execute(
            "SELECT project_id, active_release_id, lock_version "
            "FROM project_publications ORDER BY project_id"
        ).fetchall()
        version = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]

    assert publications_again == [
        (1, "release-1-active", 1),
        (2, "release-2-active", 1),
        (3, "release-3-active", 7),
    ]
    assert version == ALEMBIC_HEAD


def test_candidate_set_versioning_preserves_legacy_candidates(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "candidate-set-versioning.db"
    upgraded = _run_alembic(database, "upgrade", "0023_path_revision_binding")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'candidate-migration@example.com', 'hash', 'Candidate Migration', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '候选集迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO story_nodes (
                id, project_id, node_type, checkpoint_key, payload, created_at
            ) VALUES ('checkpoint-1', 1, 'checkpoint', 'choice-1', '{}', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO branch_candidates (
                id, project_id, checkpoint_node_id, option_key, state_delta,
                candidate_status, created_at
            ) VALUES ('candidate-1', 1, 'checkpoint-1', 'option-a', '{}',
                      'ready', ?)
            """,
            (now,),
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "0024_candidate_set_versioning")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        candidate = connection.execute(
            """
            SELECT id, checkpoint_node_id, option_key, candidate_set_revision_id
              FROM branch_candidates
             WHERE id = 'candidate-1'
            """
        ).fetchone()
        set_count = connection.execute(
            "SELECT COUNT(*) FROM candidate_set_revisions"
        ).fetchone()[0]

    assert candidate == ("candidate-1", "checkpoint-1", "option-a", None)
    assert set_count == 0


def test_revision_keyed_heads_preserve_legacy_pointers(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "revision-keyed-heads.db"
    upgraded = _run_alembic(database, "upgrade", "0024_candidate_set_versioning")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'artifact-head-migration@example.com', 'hash', 'Head Migration', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, 'Head 迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            ("a" * 64, "b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("c" * 64, "d" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_revisions (
                id, project_id, chapter_index, bible_revision_id,
                outline_revision_id, revision_no, source_hash, context_manifest,
                context_hash, content_hash, content, status, created_by, created_at
            ) VALUES ('chapter-1', 1, 1, 'bible-1', 'outline-1', 1,
                      ?, '{}', ?, ?, '正文', 'complete', 1, ?)
            """,
            ("e" * 64, "f" * 64, "1" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_script_revisions (
                id, project_id, chapter_index, chapter_revision_id,
                bible_revision_id, outline_revision_id, revision_no, source_hash,
                script_hash, script_json, coverage_json, schema_version,
                generator_version, status, created_by, created_at
            ) VALUES ('script-1', 1, 1, 'chapter-1', 'bible-1', 'outline-1', 1,
                      ?, ?, '{}', '{}', 'test-v1', 'test-v1', 'complete', 1, ?)
            """,
            ("2" * 64, "3" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_revisions (
                id, project_id, chapter_index, chapter_revision_id,
                script_revision_id, revision_no, source_manifest_hash, graph_hash,
                graph_json, schema_version, compiler_version, tachi_policy_version,
                status, created_at
            ) VALUES ('graph-1', 1, 1, 'chapter-1', NULL, 1, ?, ?, '{}',
                      'test-v1', 'test-v1', 'test-v1', 'complete', ?)
            """,
            ("4" * 64, "5" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_script_heads (
                project_id, chapter_index, current_revision_id, lock_version, updated_at
            ) VALUES (1, 1, 'script-1', 3, ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_heads (
                project_id, chapter_index, current_revision_id, lock_version, updated_at
            ) VALUES (1, 1, 'graph-1', 4, ?)
            """,
            (now,),
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "0025_revision_keyed_artifact_heads")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        script_head = connection.execute(
            """
            SELECT id, project_id, chapter_index, chapter_revision_id,
                   current_revision_id, lock_version
              FROM chapter_script_heads
            """
        ).fetchone()
        graph_head = connection.execute(
            """
            SELECT id, project_id, chapter_index, script_revision_id,
                   current_revision_id, lock_version
              FROM vn_graph_heads
            """
        ).fetchone()
        script_columns = {
            row[1]: bool(row[5])
            for row in connection.execute('PRAGMA table_info("chapter_script_heads")')
        }
        graph_columns = {
            row[1]: bool(row[5])
            for row in connection.execute('PRAGMA table_info("vn_graph_heads")')
        }

    assert len(script_head[0]) == 36
    assert script_head[1:] == (1, 1, None, "script-1", 3)
    assert len(graph_head[0]) == 36
    assert graph_head[1:] == (1, 1, None, "graph-1", 4)
    assert script_columns["id"] is True
    assert graph_columns["id"] is True
    assert script_columns["project_id"] is False
    assert graph_columns["project_id"] is False

    upgraded_to_review_heads = _run_alembic(database, "upgrade", "head")
    assert upgraded_to_review_heads.returncode == 0, upgraded_to_review_heads.stderr
    with sqlite3.connect(database) as connection:
        exact_script_head = connection.execute(
            """
            SELECT chapter_revision_id, current_revision_id, lock_version
              FROM chapter_script_heads
            """
        ).fetchone()
        exact_script_columns = {
            row[1]: bool(row[3])
            for row in connection.execute('PRAGMA table_info("chapter_script_heads")')
        }
        exact_graph_head = connection.execute(
            """
            SELECT script_revision_id, current_revision_id, lock_version
              FROM vn_graph_heads
            """
        ).fetchone()
        exact_graph_head_columns = {
            row[1]: bool(row[3])
            for row in connection.execute('PRAGMA table_info("vn_graph_heads")')
        }
        exact_graph_revision = connection.execute(
            "SELECT script_revision_id, revision_no FROM vn_graph_revisions"
        ).fetchone()
        exact_graph_revision_columns = {
            row[1]: bool(row[3])
            for row in connection.execute('PRAGMA table_info("vn_graph_revisions")')
        }

    assert exact_script_head == ("chapter-1", "script-1", 3)
    assert exact_script_columns["chapter_revision_id"] is True
    assert exact_script_columns["current_revision_id"] is False
    assert exact_graph_head == ("script-1", "graph-1", 4)
    assert exact_graph_head_columns["script_revision_id"] is True
    assert exact_graph_head_columns["current_revision_id"] is False
    assert exact_graph_revision == ("script-1", 1)
    assert exact_graph_revision_columns["script_revision_id"] is True


def test_single_source_publication_migration_is_data_preserving_and_reversible(
    tmp_path: Path,
):
    pytest.importorskip("alembic")
    database = tmp_path / "single-source-publication.db"
    upgraded = _run_alembic(database, "upgrade", "0025_revision_keyed_artifact_heads")
    assert upgraded.returncode == 0, upgraded.stderr

    now = "2026-08-08T12:00:00+00:00"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'publication-migration@example.com', 'hash', 'Publication', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, created_at, updated_at
            ) VALUES (1, 1, '发布状态迁移', '开始', '结束', 'private', 1, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO story_bible_revisions (
                id, project_id, revision_no, source_hash, content_hash,
                content_json, status, created_by, created_at
            ) VALUES ('bible-1', 1, 1, ?, ?, '{}', 'complete', 1, ?)
            """,
            ("a" * 64, "b" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO outline_revisions (
                id, project_id, bible_revision_id, revision_no, source_hash,
                content_hash, status, created_by, created_at
            ) VALUES ('outline-1', 1, 'bible-1', 1, ?, ?, 'approved', 1, ?)
            """,
            ("c" * 64, "d" * 64, now),
        )
        connection.executemany(
            """
            INSERT INTO project_releases (
                id, project_id, version, status, bible_revision_id,
                outline_revision_id, manifest_json, manifest_hash,
                created_by, created_at, published_at, withdrawn_at
            ) VALUES (?, 1, ?, ?, 'bible-1', 'outline-1', '{}', ?, 1, ?, ?, NULL)
            """,
            [
                ("draft-release", 1, "draft", "e" * 64, now, None),
                ("published-release", 2, "published", "f" * 64, now, now),
            ],
        )
        connection.commit()

    migrated = _run_alembic(database, "upgrade", "0026_single_source_publication")
    assert migrated.returncode == 0, migrated.stderr

    with sqlite3.connect(database) as connection:
        releases = connection.execute(
            """
            SELECT id, status, withdrawn_at, authoring_fingerprint, release_notes
              FROM project_releases
             ORDER BY version
            """
        ).fetchall()
        publication_count = connection.execute(
            "SELECT COUNT(*) FROM project_publications"
        ).fetchone()[0]
        snapshot_count = connection.execute(
            f'SELECT COUNT(*) FROM "_alembic_0026_release_status"'
        ).fetchone()[0]
        connection.execute(
            "UPDATE project_releases SET status = 'superseded' "
            "WHERE id = 'published-release'"
        )
        connection.commit()

    assert releases == [
        ("draft-release", "withdrawn", now, None, None),
        ("published-release", "published", None, None, None),
    ]
    assert publication_count == 0
    assert snapshot_count == 1

    downgraded = _run_alembic(database, "downgrade", "0025_revision_keyed_artifact_heads")
    assert downgraded.returncode == 0, downgraded.stderr

    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("project_releases")')
        }
        restored = connection.execute(
            "SELECT id, status, withdrawn_at FROM project_releases ORDER BY version"
        ).fetchall()
        release_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='project_releases'"
        ).fetchone()[0]

    assert "project_publications" not in tables
    assert "_alembic_0026_release_status" not in tables
    assert "authoring_fingerprint" not in columns
    assert "release_notes" not in columns
    assert restored == [
        ("draft-release", "draft", None),
        ("published-release", "published", None),
    ]
    assert "'draft','published','withdrawn'" in release_sql
    assert "superseded" not in release_sql


def test_v2_migrations_downgrade_to_foundation_and_upgrade_again(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "roundtrip.db"
    upgraded = _run_alembic(database, "upgrade", "head")
    assert upgraded.returncode == 0, upgraded.stderr

    downgraded = _run_alembic(database, "downgrade", "0002_runtime_foundation")
    assert downgraded.returncode == 0, downgraded.stderr
    with sqlite3.connect(database) as connection:
        tables_after_down = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version_after_down = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
    assert not (V2_TABLES & tables_after_down)
    assert {"users", "projects", "assets"}.issubset(tables_after_down)
    assert version_after_down == "0002_runtime_foundation"

    upgraded_again = _run_alembic(database, "upgrade", "head")
    assert upgraded_again.returncode == 0, upgraded_again.stderr
    with sqlite3.connect(database) as connection:
        tables_after_up = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert V2_TABLES.issubset(tables_after_up)


def test_start_script_is_location_independent_and_secret_safe():
    script = (BACKEND_DIR / "start.sh").read_text(encoding="utf-8")

    assert "/home/workspace/xuxie_agent" not in script
    assert "OPENAI_API_KEY" not in script
    assert "--reload" not in script
    assert 'BACKEND_DIR="$(cd --' in script
    assert "venv/bin/python" in script
