"""Validate an existing legacy database before Alembic baseline stamping.

The report contains only schema names and aggregate counts; it never prints
database URLs, story content, user data, credentials or row identifiers.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import inspect, text

from app.database import engine


REQUIRED_COLUMNS = {
    "users": {"id", "email", "quota_total", "quota_daily"},
    "projects": {
        "id",
        "owner_id",
        "title",
        "story_start",
        "story_end",
        "visibility",
    },
    "story_bibles": {"id", "project_id", "raw_json"},
    "chapter_outlines": {"id", "project_id", "chapter_index"},
    "chapter_contents": {"id", "project_id", "chapter_index", "content"},
    "assets": {"id", "project_id", "asset_type", "image_url"},
    "vn_graphs": {"id", "project_id", "chapter_index", "graph_json"},
}

OPTIONAL_LEGACY_TABLES = {
    "user_sessions",
    "workflow_runs",
    "generation_stats",
    "project_likes",
    "project_comments",
    "user_notifications",
    "api_usage_events",
    "character_voice_bindings",
}

V2_SENTINEL_TABLES = {
    "generation_tasks",
    "project_content_heads",
    "story_bible_revisions",
    "storage_objects",
    "reading_sessions",
    "project_releases",
}


def preflight(target_engine=engine) -> dict:
    inspector = inspect(target_engine)
    tables = set(inspector.get_table_names())
    errors: list[str] = []
    warnings: list[str] = []

    if "alembic_version" in tables:
        with target_engine.connect() as connection:
            revisions = sorted(
                str(row[0])
                for row in connection.execute(text("SELECT version_num FROM alembic_version"))
            )
        return {
            "ok": True,
            "database_state": "alembic_managed",
            "alembic_revisions": revisions,
            "errors": [],
            "warnings": [],
        }

    if tables & V2_SENTINEL_TABLES:
        errors.append("v2 tables exist without alembic_version; do not stamp automatically")

    for table_name, required in REQUIRED_COLUMNS.items():
        if table_name not in tables:
            errors.append(f"missing table: {table_name}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name in sorted(required - actual):
            errors.append(f"missing column: {table_name}.{column_name}")

    for table_name in sorted(OPTIONAL_LEGACY_TABLES - tables):
        warnings.append(f"optional legacy table absent: {table_name}")

    counts: dict[str, int] = {}
    orphans: dict[str, int] = {}
    if not errors:
        with target_engine.connect() as connection:
            for table_name in sorted(REQUIRED_COLUMNS):
                counts[table_name] = int(
                    connection.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).scalar_one()
                )
            for table_name in (
                "story_bibles",
                "chapter_outlines",
                "chapter_contents",
                "assets",
                "vn_graphs",
            ):
                orphans[table_name] = int(
                    connection.execute(
                        text(
                            f'SELECT COUNT(*) FROM "{table_name}" child '
                            'LEFT JOIN projects parent ON parent.id = child.project_id '
                            'WHERE parent.id IS NULL'
                        )
                    ).scalar_one()
                )
        if any(orphans.values()):
            errors.append("orphaned project-owned rows must be repaired before migration")

    return {
        "ok": not errors,
        "database_state": "legacy_unmanaged",
        "required_table_counts": counts,
        "orphan_counts": orphans,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    report = preflight()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
