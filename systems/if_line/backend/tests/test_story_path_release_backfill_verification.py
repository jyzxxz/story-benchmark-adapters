from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from sqlalchemy import create_engine

from scripts.verify_story_path_release_backfill import (
    POST_BACKFILL_REVISION,
    VerificationInputError,
    verify_snapshot,
)


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _environment(database: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "test",
            "DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "PYTHONPATH": str(BACKEND_DIR),
        }
    )
    return environment


def _run_alembic(
    database: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=BACKEND_DIR,
        env=_environment(database),
        capture_output=True,
        text=True,
        check=False,
    )


def _run_verifier(
    database: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(BACKEND_DIR / "scripts" / "verify_story_path_release_backfill.py"),
            *arguments,
        ],
        cwd=BACKEND_DIR,
        env=_environment(database),
        capture_output=True,
        text=True,
        check=False,
    )


def _seed_pre_backfill_database(database: Path) -> None:
    now = "2026-08-08T12:00:00+00:00"
    later = "2026-08-08T13:00:00+00:00"
    empty_hash = hashlib.sha256(b"{}").hexdigest()
    active_manifest = '{"story_paths":[]}'
    active_manifest_hash = hashlib.sha256(active_manifest.encode()).hexdigest()
    newer_manifest = '{"story_paths":[],"version":2}'
    newer_manifest_hash = hashlib.sha256(newer_manifest.encode()).hexdigest()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO users (
                id, email, password_hash, display_name, is_active,
                quota_total, quota_daily, quota_used_total, quota_used_daily,
                created_at, updated_at
            ) VALUES (1, 'backfill-audit@example.com', 'hash', 'Backfill Audit', 1,
                      1000, 1000, 0, 0, ?, ?)
            """,
            (now, now),
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, owner_id, title, story_start, story_end, visibility,
                is_draft, published_at, created_at, updated_at
            ) VALUES (1, 1, '迁移校验', '开始', '结束', 'public', 0, ?, ?, ?)
            """,
            (now, now, later),
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
                id, project_id, bible_revision_id, revision_no,
                source_hash, content_hash, status, created_by, created_at
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
            ) VALUES (1, 'bible-1', 'outline-1', 'release-active',
                      'published', 9, ?)
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
        connection.execute(
            """
            INSERT INTO chapter_script_revisions (
                id, project_id, chapter_index, chapter_revision_id,
                bible_revision_id, outline_revision_id, revision_no,
                source_hash, script_hash, script_json, coverage_json,
                schema_version, generator_version, status,
                created_by, created_at
            ) VALUES ('script-1', 1, 1, 'chapter-1', 'bible-1', 'outline-1', 1,
                      ?, ?, '{}', '{}', 'script-v1', 'legacy-v1', 'complete', 1, ?)
            """,
            ("7" * 64, "8" * 64, now),
        )
        connection.execute(
            """
            INSERT INTO chapter_script_heads (
                id, project_id, chapter_index, chapter_revision_id,
                current_revision_id, lock_version, updated_at
            ) VALUES ('script-head-1', 1, 1, 'chapter-1', 'script-1', 7, ?)
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
            (empty_hash, empty_hash, empty_hash, now),
        )
        connection.execute(
            """
            INSERT INTO vn_graph_heads (
                id, project_id, chapter_index, script_revision_id,
                current_revision_id, lock_version, updated_at
            ) VALUES ('graph-head-1', 1, 1, 'script-1', 'graph-1', 8, ?)
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
        connection.executemany(
            """
            INSERT INTO project_releases (
                id, project_id, version, status, bible_revision_id,
                outline_revision_id, manifest_json, manifest_hash,
                created_by, created_at, published_at, withdrawn_at
            ) VALUES (?, 1, ?, 'published', 'bible-1', 'outline-1',
                      ?, ?, 1, ?, ?, NULL)
            """,
            [
                (
                    "release-active",
                    1,
                    active_manifest,
                    active_manifest_hash,
                    now,
                    now,
                ),
                (
                    "release-newer",
                    2,
                    newer_manifest,
                    newer_manifest_hash,
                    later,
                    later,
                ),
            ],
        )
        connection.commit()


def test_story_path_release_backfill_verifier_detects_exact_result_and_damage(
    tmp_path: Path,
):
    pytest.importorskip("alembic")
    database = tmp_path / "story-path-release-audit.db"
    snapshot_path = tmp_path / "backfill-snapshot.json"

    upgraded = _run_alembic(database, "upgrade", "0035_script_resource_slot_lock")
    assert upgraded.returncode == 0, upgraded.stderr
    _seed_pre_backfill_database(database)

    captured = _run_verifier(
        database,
        "capture",
        "--output",
        str(snapshot_path),
    )
    assert captured.returncode == 0, captured.stderr
    capture_report = json.loads(captured.stdout)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert capture_report["ok"] is True
    assert capture_report["database_revision"] == "0035_script_resource_slot_lock"
    assert "chapter-1" not in snapshot_path.read_text(encoding="utf-8")
    assert "release-active" not in snapshot_path.read_text(encoding="utf-8")

    migrated = _run_alembic(database, "upgrade", "head")
    assert migrated.returncode == 0, migrated.stderr

    verified = _run_verifier(
        database,
        "verify",
        "--snapshot",
        str(snapshot_path),
    )
    assert verified.returncode == 0, verified.stderr
    report = json.loads(verified.stdout)
    assert report["ok"] is True
    assert report["database_revision"] == POST_BACKFILL_REVISION
    assert report["checks"]["chapter.legacy_head"]["ok"] is True
    assert report["checks"]["release.manifest_integrity"]["ok"] is True
    assert report["checks"]["release.public_readability"] == {
        "actual_readable": 1,
        "expected_readable": 1,
        "ok": True,
        "violations": 0,
    }
    assert report["after_counts"]["story_paths"] == 1
    assert report["after_counts"]["chapter_slots"] == 2
    assert report["after_counts"]["story_path_chapters"] == 2
    assert report["after_counts"]["project_publications"] == 1

    rolled_back = _run_alembic(
        database,
        "downgrade",
        "0035_script_resource_slot_lock",
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    rollback_snapshot_path = tmp_path / "rollback-snapshot.json"
    rollback_captured = _run_verifier(
        database,
        "capture",
        "--output",
        str(rollback_snapshot_path),
    )
    assert rollback_captured.returncode == 0, rollback_captured.stderr
    rollback_snapshot = json.loads(
        rollback_snapshot_path.read_text(encoding="utf-8")
    )
    assert rollback_snapshot["snapshot_hash"] == snapshot["snapshot_hash"]

    reupgraded = _run_alembic(database, "upgrade", "head")
    assert reupgraded.returncode == 0, reupgraded.stderr
    reverified = _run_verifier(
        database,
        "verify",
        "--snapshot",
        str(snapshot_path),
    )
    assert reverified.returncode == 0, reverified.stderr
    assert json.loads(reverified.stdout)["ok"] is True

    with sqlite3.connect(database) as connection:
        releases = connection.execute(
            "SELECT id, status FROM project_releases ORDER BY version"
        ).fetchall()
        connection.execute(
            """
            UPDATE story_path_chapters
               SET current_revision_id = NULL
             WHERE display_index = 1
            """
        )
        connection.execute(
            """
            UPDATE project_releases
               SET manifest_json = '{}'
             WHERE id = 'release-active'
            """
        )
        connection.commit()
    assert releases == [
        ("release-active", "published"),
        ("release-newer", "superseded"),
    ]

    audit_engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        damaged = verify_snapshot(snapshot, audit_engine)
    finally:
        audit_engine.dispose()
    assert damaged["ok"] is False
    error_codes = {error["code"] for error in damaged["errors"]}
    assert "chapter.legacy_head" in error_codes
    assert "rows.exact.immutable_release_manifests" in error_codes
    assert "release.manifest_integrity" in error_codes
    assert "release.public_readability" in error_codes

    snapshot["table_counts"]["projects"] += 1
    tampered_snapshot_engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with pytest.raises(VerificationInputError, match="integrity"):
            verify_snapshot(snapshot, tampered_snapshot_engine)
    finally:
        tampered_snapshot_engine.dispose()
