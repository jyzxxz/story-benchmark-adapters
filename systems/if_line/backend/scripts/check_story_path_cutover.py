"""Audit whether the database is ready for the StoryPath API cutover."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.application.story_path_task_compatibility import (
    STORY_PATH_TASK_KINDS,
    inspect_story_path_task_source,
)
from app.database import engine, get_alembic_head_revision
from app.models_v2 import GenerationTask, OutboxEvent


EXPECTED_ALEMBIC_REVISION = get_alembic_head_revision()
ACTIVE_TASK_STATUSES = frozenset({"queued", "running"})
OPEN_OUTBOX_STATUSES = frozenset({"pending", "publishing", "failed"})
REQUIRED_TABLES = frozenset(
    {"alembic_version", "generation_tasks", "outbox_events"}
)


def _limited(values: list[str], maximum: int) -> list[str]:
    return values[: max(0, maximum)]


def _summary(values: list[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def audit_story_path_cutover(
    target_engine: Engine = engine,
    *,
    max_details: int = 20,
) -> dict[str, Any]:
    """Return a privacy-safe report; callers decide whether to stop deploy."""

    table_names = set(inspect(target_engine).get_table_names())
    missing_tables = sorted(REQUIRED_TABLES - table_names)
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    revision: str | None = None
    if "alembic_version" in table_names:
        with target_engine.connect() as connection:
            revisions = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
        if len(revisions) == 1:
            revision = str(revisions[0])
    revision_ok = revision == EXPECTED_ALEMBIC_REVISION
    if missing_tables:
        blockers.append(
            {"code": "database.tables_missing", "tables": missing_tables}
        )
    if not revision_ok:
        blockers.append(
            {
                "code": "database.revision_mismatch",
                "expected": EXPECTED_ALEMBIC_REVISION,
                "actual": revision,
            }
        )

    active_rows: list[Any] = []
    outbox_rows: list[Any] = []
    incompatible_rows: list[tuple[Any, str]] = []
    if not missing_tables:
        session = sessionmaker(bind=target_engine, autoflush=False)()
        try:
            task_columns = (
                GenerationTask.id,
                GenerationTask.kind,
                GenerationTask.status,
                GenerationTask.source_refs,
                GenerationTask.parameters,
            )
            active_rows = (
                session.query(*task_columns)
                .filter(
                    GenerationTask.kind.in_(STORY_PATH_TASK_KINDS),
                    GenerationTask.status.in_(ACTIVE_TASK_STATUSES),
                )
                .order_by(GenerationTask.created_at, GenerationTask.id)
                .all()
            )
            terminal_rows = (
                session.query(*task_columns)
                .filter(
                    GenerationTask.kind.in_(STORY_PATH_TASK_KINDS),
                    ~GenerationTask.status.in_(ACTIVE_TASK_STATUSES),
                )
                .order_by(GenerationTask.created_at, GenerationTask.id)
                .all()
            )
            for row in terminal_rows:
                compatibility = inspect_story_path_task_source(
                    row.kind,
                    source_refs=row.source_refs,
                    parameters=row.parameters,
                )
                if not compatibility.compatible:
                    incompatible_rows.append(
                        (row, compatibility.reason or "unknown")
                    )
            outbox_rows = (
                session.query(
                    OutboxEvent.id.label("event_id"),
                    OutboxEvent.status.label("event_status"),
                    GenerationTask.id.label("task_id"),
                    GenerationTask.kind.label("task_kind"),
                )
                .join(
                    GenerationTask,
                    GenerationTask.id == OutboxEvent.aggregate_id,
                )
                .filter(
                    OutboxEvent.aggregate_type == "generation_task",
                    OutboxEvent.status.in_(OPEN_OUTBOX_STATUSES),
                    GenerationTask.kind.in_(STORY_PATH_TASK_KINDS),
                )
                .order_by(OutboxEvent.created_at, OutboxEvent.id)
                .all()
            )
        finally:
            session.close()

    if active_rows:
        blockers.append(
            {"code": "tasks.active_story_path", "count": len(active_rows)}
        )
    if outbox_rows:
        blockers.append(
            {"code": "outbox.open_story_path", "count": len(outbox_rows)}
        )
    if incompatible_rows:
        warnings.append(
            {
                "code": "tasks.terminal_legacy_source",
                "count": len(incompatible_rows),
                "action": "retained_for_audit_and_blocked_from_retry",
            }
        )

    report: dict[str, Any] = {
        "ok": not blockers,
        "database_revision": {
            "ok": revision_ok,
            "expected": EXPECTED_ALEMBIC_REVISION,
            "actual": revision,
        },
        "active_story_path_tasks": {
            "count": len(active_rows),
            "by_kind": _summary([row.kind for row in active_rows]),
            "sample_task_ids": _limited([row.id for row in active_rows], max_details),
        },
        "open_story_path_outbox_events": {
            "count": len(outbox_rows),
            "by_status": _summary([row.event_status for row in outbox_rows]),
            "sample_event_ids": _limited(
                [row.event_id for row in outbox_rows], max_details
            ),
        },
        "terminal_legacy_tasks": {
            "count": len(incompatible_rows),
            "by_kind": _summary([row.kind for row, _reason in incompatible_rows]),
            "by_reason": _summary([reason for _row, reason in incompatible_rows]),
            "sample_task_ids": _limited(
                [row.id for row, _reason in incompatible_rows], max_details
            ),
        },
        "blockers": blockers,
        "warnings": warnings,
    }
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-details",
        type=int,
        default=20,
        help="maximum number of opaque task/event IDs per report section",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.max_details < 0 or arguments.max_details > 100:
        _parser().error("--max-details must be between 0 and 100")
    try:
        report: Mapping[str, Any] = audit_story_path_cutover(
            max_details=arguments.max_details
        )
    except SQLAlchemyError as error:
        report = {
            "ok": False,
            "blockers": [
                {
                    "code": "database.audit_failed",
                    "error_type": type(error).__name__,
                }
            ],
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
