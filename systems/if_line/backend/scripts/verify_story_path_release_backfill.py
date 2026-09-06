"""Capture and verify the StoryPath and Release backfill boundary.

Capture a privacy-preserving baseline at revision 0035, upgrade through the
current Alembic head, then verify counts, immutable row fingerprints, exact
Heads, manifests, and public Release access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.application.hashing import content_hash
from app.application.public_release_service import get_active_public_release
from app.core.errors import AppError
from app.database import engine, get_alembic_head_revision


SNAPSHOT_VERSION = 1
PRE_BACKFILL_REVISION = "0035_script_resource_slot_lock"
POST_BACKFILL_REVISION = get_alembic_head_revision()

PRESERVED_COUNT_TABLES = (
    "projects",
    "project_content_heads",
    "story_bible_revisions",
    "chapter_revisions",
    "chapter_heads",
    "chapter_segments",
    "story_nodes",
    "branch_candidates",
    "chapter_script_revisions",
    "vn_graph_revisions",
    "project_releases",
    "reading_sessions",
)

GROW_ONLY_COUNT_TABLES = (
    "story_paths",
    "chapter_slots",
    "story_path_chapters",
    "story_path_outline_heads",
    "outline_revisions",
    "outline_revision_chapters",
    "state_snapshots",
    "candidate_set_revisions",
    "candidate_set_heads",
    "chapter_script_heads",
    "vn_graph_heads",
    "project_publications",
)

ROW_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "legacy_project_publication": (
        "projects",
        ("id", "visibility", "is_draft", "published_at"),
    ),
    "legacy_project_heads": (
        "project_content_heads",
        (
            "project_id",
            "current_bible_revision_id",
            "published_release_id",
            "lifecycle_status",
        ),
    ),
    "legacy_outline_revisions": (
        "outline_revisions",
        (
            "id",
            "project_id",
            "bible_revision_id",
            "parent_revision_id",
            "revision_no",
            "source_hash",
            "content_hash",
            "status",
            "approved_at",
            "generation_task_id",
            "legacy_source_table",
            "legacy_source_id",
            "created_by",
            "created_at",
        ),
    ),
    "legacy_outline_chapters": (
        "outline_revision_chapters",
        (
            "id",
            "outline_revision_id",
            "chapter_index",
            "display_index",
            "title",
            "summary",
            "conflict",
            "characters",
            "scene",
            "emotion",
            "visual_keywords",
            "content_hash",
            "legacy_source_id",
            "created_at",
        ),
    ),
    "legacy_chapter_heads": (
        "chapter_heads",
        (
            "project_id",
            "chapter_index",
            "current_revision_id",
            "lock_version",
            "updated_at",
        ),
    ),
    "existing_script_heads": (
        "chapter_script_heads",
        (
            "id",
            "project_id",
            "chapter_index",
            "chapter_revision_id",
            "current_revision_id",
            "lock_version",
            "updated_at",
        ),
    ),
    "existing_graph_heads": (
        "vn_graph_heads",
        (
            "id",
            "project_id",
            "chapter_index",
            "script_revision_id",
            "current_revision_id",
            "lock_version",
            "updated_at",
        ),
    ),
    "immutable_release_manifests": (
        "project_releases",
        (
            "id",
            "project_id",
            "version",
            "bible_revision_id",
            "outline_revision_id",
            "manifest_json",
            "manifest_hash",
            "authoring_fingerprint",
            "release_notes",
            "publication_idempotency_key",
            "publication_request_hash",
            "created_by",
            "created_at",
            "published_at",
            "withdrawn_at",
        ),
    ),
}

EXACT_ROW_KEYS = (
    "legacy_project_publication",
    "legacy_project_heads",
    "legacy_chapter_heads",
    "immutable_release_manifests",
)

SUBSET_ROW_KEYS = (
    "legacy_outline_revisions",
    "legacy_outline_chapters",
    "existing_script_heads",
    "existing_graph_heads",
)

JSON_COLUMNS = frozenset({"manifest_json"})
REQUIRED_TABLES = frozenset(
    {
        "alembic_version",
        *PRESERVED_COUNT_TABLES,
        *GROW_ONLY_COUNT_TABLES,
        *(table_name for table_name, _columns in ROW_SPECS.values()),
    }
)


class VerificationInputError(ValueError):
    """The verifier cannot safely interpret the requested database or snapshot."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _decode_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _revision(connection) -> str:
    revisions = sorted(
        str(row[0])
        for row in connection.execute(text("SELECT version_num FROM alembic_version"))
    )
    if len(revisions) != 1:
        raise VerificationInputError(
            f"expected one Alembic revision, found {len(revisions)}"
        )
    return revisions[0]


def _require_tables(target_engine: Engine) -> None:
    tables = set(inspect(target_engine).get_table_names())
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise VerificationInputError(
            f"database is missing {len(missing)} required backfill table(s)"
        )


def _table_counts(connection) -> dict[str, int]:
    return {
        table_name: int(
            connection.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar_one()
        )
        for table_name in (*PRESERVED_COUNT_TABLES, *GROW_ONLY_COUNT_TABLES)
    }


def _row_fingerprints(connection) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for key, (table_name, columns) in ROW_SPECS.items():
        selected = ", ".join(f'"{column}"' for column in columns)
        rows = connection.execute(
            text(f'SELECT {selected} FROM "{table_name}"')
        ).all()
        fingerprints: list[str] = []
        for row in rows:
            values: list[Any] = []
            for column, value in zip(columns, row):
                if column in JSON_COLUMNS:
                    value = _decode_json(value)
                values.append(_normalize(value))
            fingerprints.append(_digest(values))
        result[key] = sorted(fingerprints)
    return result


def _snapshot_payload(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key != "snapshot_hash"
    }


def capture_snapshot(target_engine: Engine = engine) -> dict[str, Any]:
    """Capture the exact pre-backfill baseline without exposing row content."""

    _require_tables(target_engine)
    with target_engine.connect() as connection:
        revision = _revision(connection)
        if revision != PRE_BACKFILL_REVISION:
            raise VerificationInputError(
                "baseline capture requires Alembic revision "
                f"{PRE_BACKFILL_REVISION}, found {revision}"
            )
        snapshot: dict[str, Any] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "database_revision": revision,
            "table_counts": _table_counts(connection),
            "row_fingerprints": _row_fingerprints(connection),
        }
    snapshot["snapshot_hash"] = _digest(snapshot)
    return snapshot


def _validate_snapshot(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise VerificationInputError("unsupported backfill snapshot version")
    if snapshot.get("database_revision") != PRE_BACKFILL_REVISION:
        raise VerificationInputError("snapshot was not captured at the pre-backfill revision")
    expected_hash = snapshot.get("snapshot_hash")
    if not isinstance(expected_hash, str) or expected_hash != _digest(
        _snapshot_payload(snapshot)
    ):
        raise VerificationInputError("snapshot integrity check failed")

    counts = snapshot.get("table_counts")
    fingerprints = snapshot.get("row_fingerprints")
    if not isinstance(counts, dict) or set(counts) != {
        *PRESERVED_COUNT_TABLES,
        *GROW_ONLY_COUNT_TABLES,
    }:
        raise VerificationInputError("snapshot table counts are incomplete")
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(ROW_SPECS):
        raise VerificationInputError("snapshot row fingerprints are incomplete")
    if any(
        not isinstance(rows, list) or any(not isinstance(item, str) for item in rows)
        for rows in fingerprints.values()
    ):
        raise VerificationInputError("snapshot row fingerprints are malformed")


def _violation_count(connection, sql: str) -> int:
    return int(connection.execute(text(sql)).scalar_one())


def _add_check(
    checks: dict[str, dict[str, Any]],
    errors: list[dict[str, Any]],
    code: str,
    violations: int,
) -> None:
    checks[code] = {"ok": violations == 0, "violations": violations}
    if violations:
        errors.append({"code": code, "violations": violations})


INVARIANT_QUERIES = {
    "story_path.root_count": """
        SELECT COUNT(*) FROM (
            SELECT project.id
              FROM projects AS project
              LEFT JOIN story_paths AS path
                ON path.project_id = project.id
               AND path.parent_path_id IS NULL
             GROUP BY project.id
            HAVING COUNT(path.id) <> 1
        ) AS invalid
    """,
    "story_path.root_outline_head": """
        SELECT COUNT(*)
          FROM story_paths AS path
          LEFT JOIN story_path_outline_heads AS head
            ON head.story_path_id = path.id
         WHERE path.parent_path_id IS NULL
           AND head.story_path_id IS NULL
    """,
    "story_path.predecessor_chain": """
        SELECT COUNT(*)
          FROM story_path_chapters AS chapter
          JOIN story_paths AS path ON path.id = chapter.story_path_id
         WHERE path.parent_path_id IS NULL
           AND (
                (
                    chapter.predecessor_path_chapter_id IS NULL
                    AND EXISTS (
                        SELECT 1 FROM story_path_chapters AS previous
                         WHERE previous.story_path_id = chapter.story_path_id
                           AND previous.display_index < chapter.display_index
                    )
                )
                OR (
                    chapter.predecessor_path_chapter_id IS NOT NULL
                    AND (
                        NOT EXISTS (
                            SELECT 1 FROM story_path_chapters AS previous
                             WHERE previous.story_path_id = chapter.story_path_id
                               AND previous.display_index < chapter.display_index
                        )
                        OR chapter.predecessor_path_chapter_id <> (
                            SELECT previous.id
                              FROM story_path_chapters AS previous
                             WHERE previous.story_path_id = chapter.story_path_id
                               AND previous.display_index < chapter.display_index
                             ORDER BY previous.display_index DESC
                             LIMIT 1
                        )
                    )
                )
           )
    """,
    "chapter.revision_binding": """
        SELECT COUNT(*)
          FROM chapter_revisions AS revision
          LEFT JOIN chapter_slots AS slot ON slot.id = revision.chapter_slot_id
          LEFT JOIN story_paths AS path
            ON path.id = revision.created_for_story_path_id
         WHERE revision.chapter_slot_id IS NULL
            OR revision.created_for_story_path_id IS NULL
            OR slot.id IS NULL
            OR path.id IS NULL
            OR slot.project_id <> revision.project_id
            OR path.project_id <> revision.project_id
            OR slot.created_for_story_path_id <> revision.created_for_story_path_id
    """,
    "chapter.legacy_head": """
        SELECT COUNT(*)
          FROM chapter_heads AS legacy
          LEFT JOIN story_paths AS root
            ON root.project_id = legacy.project_id
           AND root.parent_path_id IS NULL
          LEFT JOIN story_path_chapters AS chapter
            ON chapter.story_path_id = root.id
           AND chapter.display_index = legacy.chapter_index
         WHERE chapter.id IS NULL
            OR chapter.current_revision_id IS NULL
            OR chapter.current_revision_id <> legacy.current_revision_id
            OR chapter.lock_version <> legacy.lock_version
    """,
    "outline.revision_binding": """
        SELECT COUNT(*)
          FROM outline_revisions AS revision
          LEFT JOIN story_paths AS path ON path.id = revision.story_path_id
          LEFT JOIN outline_revisions AS parent
            ON parent.id = revision.parent_revision_id
         WHERE revision.story_path_id IS NULL
            OR path.id IS NULL
            OR path.project_id <> revision.project_id
            OR (
                parent.id IS NOT NULL
                AND parent.story_path_id <> revision.story_path_id
            )
    """,
    "outline.chapter_binding": """
        SELECT COUNT(*)
          FROM outline_revision_chapters AS chapter
          JOIN outline_revisions AS revision
            ON revision.id = chapter.outline_revision_id
          LEFT JOIN story_path_chapters AS placement
            ON placement.id = chapter.story_path_chapter_id
         WHERE chapter.story_path_chapter_id IS NULL
            OR placement.id IS NULL
            OR placement.story_path_id <> revision.story_path_id
            OR placement.display_index <> chapter.display_index
    """,
    "outline.legacy_head": """
        SELECT COUNT(*)
          FROM project_content_heads AS legacy
          LEFT JOIN outline_revisions AS revision
            ON revision.id = legacy.current_outline_revision_id
          LEFT JOIN story_paths AS root
            ON root.id = revision.story_path_id
           AND root.project_id = legacy.project_id
           AND root.parent_path_id IS NULL
          LEFT JOIN story_path_outline_heads AS head
            ON head.story_path_id = root.id
         WHERE legacy.current_outline_revision_id IS NOT NULL
           AND (
                revision.id IS NULL
                OR root.id IS NULL
                OR head.current_revision_id IS NULL
                OR head.current_revision_id <> legacy.current_outline_revision_id
           )
    """,
    "script.revision_source": """
        SELECT COUNT(*)
          FROM chapter_script_revisions AS script
          LEFT JOIN chapter_revisions AS chapter
            ON chapter.id = script.chapter_revision_id
         WHERE chapter.id IS NULL
            OR script.project_id <> chapter.project_id
            OR script.chapter_index <> chapter.chapter_index
            OR script.bible_revision_id <> chapter.bible_revision_id
            OR script.outline_revision_id <> chapter.outline_revision_id
    """,
    "script.head_coverage": """
        SELECT COUNT(*)
          FROM chapter_revisions AS chapter
          LEFT JOIN chapter_script_heads AS head
            ON head.chapter_revision_id = chapter.id
         WHERE head.id IS NULL
            OR head.project_id <> chapter.project_id
            OR head.chapter_index <> chapter.chapter_index
    """,
    "script.head_selection": """
        SELECT COUNT(*)
          FROM chapter_script_heads AS head
          LEFT JOIN chapter_script_revisions AS script
            ON script.id = head.current_revision_id
         WHERE head.current_revision_id IS NOT NULL
           AND (
                script.id IS NULL
                OR script.chapter_revision_id <> head.chapter_revision_id
                OR script.project_id <> head.project_id
           )
    """,
    "vngraph.revision_source": """
        SELECT COUNT(*)
          FROM vn_graph_revisions AS graph
          LEFT JOIN chapter_script_revisions AS script
            ON script.id = graph.script_revision_id
         WHERE script.id IS NULL
            OR graph.project_id <> script.project_id
            OR graph.chapter_revision_id <> script.chapter_revision_id
            OR graph.chapter_index <> script.chapter_index
    """,
    "vngraph.head_coverage": """
        SELECT COUNT(*)
          FROM chapter_script_revisions AS script
          LEFT JOIN vn_graph_heads AS head
            ON head.script_revision_id = script.id
         WHERE head.id IS NULL
            OR head.project_id <> script.project_id
            OR head.chapter_index <> script.chapter_index
    """,
    "vngraph.head_selection": """
        SELECT COUNT(*)
          FROM vn_graph_heads AS head
          LEFT JOIN vn_graph_revisions AS graph
            ON graph.id = head.current_revision_id
         WHERE head.current_revision_id IS NOT NULL
           AND (
                graph.id IS NULL
                OR graph.script_revision_id <> head.script_revision_id
                OR graph.project_id <> head.project_id
           )
    """,
    "candidate.exact_binding": """
        SELECT COUNT(*)
          FROM branch_candidates AS candidate
          LEFT JOIN candidate_set_revisions AS candidate_set
            ON candidate_set.id = candidate.candidate_set_revision_id
         WHERE candidate.candidate_set_revision_id IS NULL
            OR candidate_set.id IS NULL
            OR candidate_set.project_id <> candidate.project_id
            OR candidate_set.checkpoint_node_id <> candidate.checkpoint_node_id
    """,
    "candidate.head_selection": """
        SELECT COUNT(*)
          FROM candidate_set_heads AS head
          LEFT JOIN candidate_set_revisions AS revision
            ON revision.id = head.current_revision_id
         WHERE head.current_revision_id IS NOT NULL
           AND (
                revision.id IS NULL
                OR revision.story_path_id <> head.story_path_id
                OR revision.checkpoint_node_id <> head.checkpoint_node_id
           )
    """,
    "release.active_pointer": """
        SELECT COUNT(*)
          FROM project_publications AS publication
          LEFT JOIN project_releases AS release
            ON release.project_id = publication.project_id
           AND release.id = publication.active_release_id
         WHERE publication.active_release_id IS NOT NULL
           AND (
                release.id IS NULL
                OR publication.published_at IS NULL
                OR release.status <> 'published'
                OR release.published_at IS NULL
                OR release.withdrawn_at IS NOT NULL
           )
    """,
    "release.single_public_source": """
        SELECT COUNT(*)
          FROM project_releases AS release
          LEFT JOIN project_publications AS publication
            ON publication.project_id = release.project_id
           AND publication.active_release_id = release.id
         WHERE release.status = 'published'
           AND publication.active_release_id IS NULL
    """,
}


def _manifest_violation_count(connection) -> int:
    violations = 0
    rows = connection.execute(
        text("SELECT manifest_json, manifest_hash FROM project_releases")
    ).all()
    for raw_manifest, expected_hash in rows:
        manifest = _decode_json(raw_manifest)
        if not isinstance(manifest, dict) or content_hash(manifest) != expected_hash:
            violations += 1
    return violations


def _public_access_violation_count(target_engine: Engine) -> tuple[int, int, int]:
    session_factory = sessionmaker(bind=target_engine, autoflush=False)
    db = session_factory()
    violations = 0
    expected_readable = 0
    actual_readable = 0
    try:
        rows = db.execute(
            text(
                """
                SELECT release.id,
                       CASE WHEN publication.active_release_id = release.id
                             THEN 1 ELSE 0 END AS expected_readable
                  FROM project_releases AS release
                  LEFT JOIN project_publications AS publication
                    ON publication.project_id = release.project_id
                   AND publication.active_release_id = release.id
                """
            )
        ).all()
        for release_id, expected in rows:
            expected = bool(expected)
            expected_readable += int(expected)
            try:
                get_active_public_release(db, release_id=release_id)
                readable = True
            except AppError as error:
                readable = False
                if error.status_code != 404:
                    violations += 1
            except Exception:
                readable = False
                violations += 1
            actual_readable += int(readable)
            if readable != expected:
                violations += 1
    finally:
        db.close()
    return violations, expected_readable, actual_readable


def verify_snapshot(
    snapshot: Mapping[str, Any],
    target_engine: Engine = engine,
) -> dict[str, Any]:
    """Verify the current schema head against an integrity-checked 0035 snapshot."""

    _validate_snapshot(snapshot)
    _require_tables(target_engine)
    checks: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []

    with target_engine.connect() as connection:
        revision = _revision(connection)
        if revision != POST_BACKFILL_REVISION:
            raise VerificationInputError(
                "backfill verification requires Alembic revision "
                f"{POST_BACKFILL_REVISION}, found {revision}"
            )
        after_counts = _table_counts(connection)
        after_fingerprints = _row_fingerprints(connection)

        before_counts = snapshot["table_counts"]
        for table_name in PRESERVED_COUNT_TABLES:
            expected = int(before_counts[table_name])
            actual = after_counts[table_name]
            code = f"count.preserved.{table_name}"
            checks[code] = {
                "ok": actual == expected,
                "before": expected,
                "after": actual,
                "delta": actual - expected,
            }
            if actual != expected:
                errors.append(
                    {"code": code, "before": expected, "after": actual}
                )

        for table_name in GROW_ONLY_COUNT_TABLES:
            expected = int(before_counts[table_name])
            actual = after_counts[table_name]
            code = f"count.grow_only.{table_name}"
            checks[code] = {
                "ok": actual >= expected,
                "before": expected,
                "after": actual,
                "delta": actual - expected,
            }
            if actual < expected:
                errors.append(
                    {"code": code, "before": expected, "after": actual}
                )

        before_fingerprints = snapshot["row_fingerprints"]
        for key in EXACT_ROW_KEYS:
            violations = int(
                Counter(before_fingerprints[key])
                != Counter(after_fingerprints[key])
            )
            _add_check(checks, errors, f"rows.exact.{key}", violations)

        for key in SUBSET_ROW_KEYS:
            missing = Counter(before_fingerprints[key]) - Counter(
                after_fingerprints[key]
            )
            _add_check(
                checks,
                errors,
                f"rows.preserved.{key}",
                sum(missing.values()),
            )

        for code, sql in INVARIANT_QUERIES.items():
            _add_check(checks, errors, code, _violation_count(connection, sql))

        _add_check(
            checks,
            errors,
            "release.manifest_integrity",
            _manifest_violation_count(connection),
        )

    public_violations, expected_readable, actual_readable = (
        _public_access_violation_count(target_engine)
    )
    _add_check(checks, errors, "release.public_readability", public_violations)
    checks["release.public_readability"].update(
        {
            "expected_readable": expected_readable,
            "actual_readable": actual_readable,
        }
    )

    return {
        "ok": not errors,
        "snapshot_version": SNAPSHOT_VERSION,
        "snapshot_hash": snapshot["snapshot_hash"],
        "database_revision": revision,
        "before_counts": dict(snapshot["table_counts"]),
        "after_counts": after_counts,
        "checks": checks,
        "errors": errors,
    }


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationInputError("unable to read backfill snapshot") from error
    if not isinstance(value, dict):
        raise VerificationInputError("backfill snapshot must be a JSON object")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture and verify the StoryPath/Release backfill",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture", help="capture the 0035 baseline")
    capture.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify the current-head result")
    verify.add_argument("--snapshot", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "capture":
            snapshot = capture_snapshot()
            _write_json(arguments.output, snapshot)
            report: Mapping[str, Any] = {
                "ok": True,
                "output": str(arguments.output),
                "snapshot_hash": snapshot["snapshot_hash"],
                "database_revision": snapshot["database_revision"],
                "table_counts": snapshot["table_counts"],
            }
        else:
            report = verify_snapshot(_load_snapshot(arguments.snapshot))
    except VerificationInputError as error:
        report = {"ok": False, "errors": [{"code": "input.invalid", "message": str(error)}]}

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
