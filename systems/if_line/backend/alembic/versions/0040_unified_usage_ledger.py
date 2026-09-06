"""Migrate legacy API usage events into the durable usage ledger.

Revision ID: 0040_unified_usage_ledger
Revises: 0039_story_path_integrity
"""
from __future__ import annotations

from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0040_unified_usage_ledger"
down_revision = "0039_story_path_integrity"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_LEGACY_ID_PREFIX = "legacy-api-usage-"
_PROJECT_FK = "fk_usage_ledger_entries_project_id_projects"
_PROJECT_INDEX = "ix_usage_ledger_entries_project_id"
_RESERVATION_FK = (
    "fk_usage_ledger_entries_reservation_id_usage_reservations"
)
_TASK_FK = "fk_usage_ledger_entries_task_id_generation_tasks"
_FK_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_table(bind, table_name: str) -> bool:
    return _is_offline() or inspect(bind).has_table(table_name)


def _column_names(bind, table_name: str) -> set[str]:
    if _is_offline():
        # Model-backed migration 0003 emits the current table definition in a
        # full offline script, so the new columns already exist in that mode.
        return {"project_id", "activity_type"}
    return {
        str(column["name"])
        for column in inspect(bind).get_columns(table_name)
    }


def _index_names(bind, table_name: str) -> set[str]:
    if _is_offline():
        return {_PROJECT_INDEX}
    return {
        str(index["name"])
        for index in inspect(bind).get_indexes(table_name)
        if index.get("name")
    }


def _foreign_keys(bind, table_name: str) -> list[dict]:
    if _is_offline():
        return [
            {"name": _PROJECT_FK, "constrained_columns": ["project_id"]},
            {
                "name": _RESERVATION_FK,
                "constrained_columns": ["reservation_id"],
                "options": {"ondelete": "SET NULL"},
            },
            {
                "name": _TASK_FK,
                "constrained_columns": ["task_id"],
                "options": {"ondelete": "SET NULL"},
            },
        ]
    return list(inspect(bind).get_foreign_keys(table_name))


def _set_ledger_source_fks(bind, *, reservation_ondelete: str) -> None:
    if not _has_table(bind, "usage_ledger_entries") or _is_offline():
        # Offline migrations support complete new-database bundles only. The
        # model-backed 0003 definition already emits both current SET NULL FKs.
        return
    expected = {
        "reservation_id": (
            "usage_reservations",
            _RESERVATION_FK,
            reservation_ondelete.upper(),
        ),
        "task_id": ("generation_tasks", _TASK_FK, "SET NULL"),
    }
    source_fks = {
        str((item.get("constrained_columns") or [""])[0]): item
        for item in _foreign_keys(bind, "usage_ledger_entries")
        if list(item.get("constrained_columns") or [])
        in (["reservation_id"], ["task_id"])
    }
    if set(source_fks) == set(expected) and all(
        str((source_fks[column].get("options") or {}).get("ondelete") or "").upper()
        == definition[2]
        for column, definition in expected.items()
    ):
        return

    recreate = "always" if bind.dialect.name == "sqlite" else "auto"
    with op.batch_alter_table(
        "usage_ledger_entries",
        recreate=recreate,
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch:
        for column in expected:
            item = source_fks.get(column)
            if item is None:
                continue
            batch.drop_constraint(
                str(item.get("name") or expected[column][1]),
                type_="foreignkey",
            )
        for column, (target_table, constraint_name, ondelete) in expected.items():
            batch.create_foreign_key(
                constraint_name,
                target_table,
                [column],
                ["id"],
                ondelete=ondelete,
            )


def _ensure_ledger_provenance_columns(bind) -> None:
    if not _has_table(bind, "usage_ledger_entries"):
        return
    columns = _column_names(bind, "usage_ledger_entries")
    add_project = "project_id" not in columns
    add_activity = "activity_type" not in columns
    if add_project or add_activity:
        with op.batch_alter_table("usage_ledger_entries") as batch:
            if add_project:
                batch.add_column(sa.Column("project_id", sa.Integer(), nullable=True))
                batch.create_foreign_key(
                    _PROJECT_FK,
                    "projects",
                    ["project_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
                batch.create_index(_PROJECT_INDEX, ["project_id"], unique=False)
            if add_activity:
                batch.add_column(
                    sa.Column("activity_type", sa.String(length=100), nullable=True)
                )

    # Freeze provenance on every pre-existing v2 entry. The reservation is a
    # fallback for rows whose task was already removed during legacy cleanup.
    op.execute(
        sa.text(
            "UPDATE usage_ledger_entries SET project_id = COALESCE(project_id, "
            "(SELECT t.project_id FROM generation_tasks t "
            "WHERE t.id = usage_ledger_entries.task_id), "
            "(SELECT r.project_id FROM usage_reservations r "
            "WHERE r.id = usage_ledger_entries.reservation_id))"
        )
    )
    op.execute(
        sa.text(
            "UPDATE usage_ledger_entries SET activity_type = COALESCE("
            "(SELECT t.kind FROM generation_tasks t "
            "WHERE t.id = usage_ledger_entries.task_id), "
            "NULLIF(activity_type, ''), 'usage')"
        )
    )
    if add_activity:
        with op.batch_alter_table("usage_ledger_entries") as batch:
            batch.alter_column(
                "activity_type",
                existing_type=sa.String(length=100),
                nullable=False,
            )


def _migrate_legacy_events(bind) -> None:
    if not _has_table(bind, "api_usage_events"):
        return
    if _is_offline():
        # Offline bundles are supported only for a new database (see the
        # Alembic README). Migration 0003 therefore already emitted the
        # current ledger columns and the legacy table is known to be empty.
        op.execute(
            sa.text(
                "INSERT INTO usage_ledger_entries ("
                "id, reservation_id, user_id, project_id, task_id, activity_type, "
                "entry_type, amount, currency, event_metadata, created_at"
                ") SELECT "
                f"'{_LEGACY_ID_PREFIX}' || CAST(e.id AS VARCHAR), NULL, "
                "e.user_id, e.project_id, NULL, "
                "COALESCE(NULLIF(e.event_type, ''), 'legacy_usage'), "
                "'adjustment', e.amount, 'credit', "
                "COALESCE(e.event_metadata, CAST('{}' AS JSON)), "
                "COALESCE(e.created_at, CURRENT_TIMESTAMP) "
                "FROM api_usage_events e"
            )
        )
        op.drop_table("api_usage_events")
        return

    legacy_count = int(
        bind.execute(sa.text("SELECT COUNT(*) FROM api_usage_events")).scalar_one()
    )
    if legacy_count == 0:
        op.drop_table("api_usage_events")
        return
    required_tables = ("users", "projects", "usage_ledger_entries")
    missing_tables = [
        table_name
        for table_name in required_tables
        if not inspect(bind).has_table(table_name)
    ]
    if missing_tables:
        raise RuntimeError(
            "non-empty api_usage_events requires legacy owner tables: "
            + ", ".join(missing_tables)
        )

    orphan = bind.execute(
        sa.text(
            "SELECT e.id FROM api_usage_events e "
            "LEFT JOIN users u ON u.id = e.user_id "
            "LEFT JOIN projects p ON p.id = e.project_id "
            "WHERE u.id IS NULL OR (e.project_id IS NOT NULL AND p.id IS NULL) "
            "LIMIT 1"
        )
    ).first()
    if orphan is not None:
        raise RuntimeError(
            "orphan api_usage_events row prevents 0040 upgrade: "
            f"event={orphan[0]}"
        )

    id_expression = f"'{_LEGACY_ID_PREFIX}' || CAST(e.id AS VARCHAR)"
    collision = bind.execute(
        sa.text(
            "SELECT e.id FROM api_usage_events e "
            "JOIN usage_ledger_entries l ON l.id = " + id_expression + " LIMIT 1"
        )
    ).first()
    if collision is not None:
        raise RuntimeError(
            "legacy usage ledger id collision prevents 0040 upgrade: "
            f"event={collision[0]}"
        )

    empty_json = "CAST('{}' AS JSON)" if bind.dialect.name == "postgresql" else "'{}'"
    bind.execute(
        sa.text(
            "INSERT INTO usage_ledger_entries ("
            "id, reservation_id, user_id, project_id, task_id, activity_type, "
            "entry_type, amount, currency, event_metadata, created_at"
            ") SELECT "
            f"{id_expression}, NULL, e.user_id, e.project_id, NULL, "
            "COALESCE(NULLIF(e.event_type, ''), 'legacy_usage'), "
            "'adjustment', e.amount, 'credit', "
            f"COALESCE(e.event_metadata, {empty_json}), "
            "COALESCE(e.created_at, CURRENT_TIMESTAMP) FROM api_usage_events e"
        )
    )

    migrated_count = int(
        bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM api_usage_events e "
                "JOIN usage_ledger_entries l ON l.id = " + id_expression
            )
        ).scalar_one()
    )
    if migrated_count != legacy_count:
        raise RuntimeError(
            "legacy usage migration count mismatch: "
            f"expected={legacy_count} actual={migrated_count}"
        )
    op.drop_table("api_usage_events")


def upgrade() -> None:
    bind = op.get_bind()
    _ensure_ledger_provenance_columns(bind)
    _set_ledger_source_fks(bind, reservation_ondelete="SET NULL")
    _migrate_legacy_events(bind)


def _restore_legacy_events(bind) -> None:
    if not _has_table(bind, "usage_ledger_entries"):
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, user_id, project_id, activity_type, amount, "
            "event_metadata, created_at FROM usage_ledger_entries "
            "WHERE id LIKE :prefix"
        ),
        {"prefix": f"{_LEGACY_ID_PREFIX}%"},
    ).mappings().all()
    restored: list[dict] = []
    restored_ids: list[str] = []
    for row in rows:
        ledger_id = str(row["id"])
        suffix = ledger_id[len(_LEGACY_ID_PREFIX):]
        if not suffix.isdigit():
            continue
        restored.append(
            {
                "id": int(suffix),
                "user_id": row["user_id"],
                "event_type": row["activity_type"],
                "amount": int(Decimal(str(row["amount"]))),
                "project_id": row["project_id"],
                "event_metadata": row["event_metadata"],
                "created_at": row["created_at"],
            }
        )
        restored_ids.append(ledger_id)

    if restored:
        metadata_type = sa.JSON() if bind.dialect.name == "postgresql" else sa.Text()
        created_type = (
            sa.DateTime(timezone=True)
            if bind.dialect.name == "postgresql"
            else sa.Text()
        )
        legacy_table = sa.table(
            "api_usage_events",
            sa.column("id", sa.Integer()),
            sa.column("user_id", sa.Integer()),
            sa.column("event_type", sa.String(length=100)),
            sa.column("amount", sa.Integer()),
            sa.column("project_id", sa.Integer()),
            sa.column("event_metadata", metadata_type),
            sa.column("created_at", created_type),
        )
        bind.execute(legacy_table.insert(), restored)
        bind.execute(
            sa.text("DELETE FROM usage_ledger_entries WHERE id IN :ids").bindparams(
                sa.bindparam("ids", expanding=True)
            ),
            {"ids": restored_ids},
        )


def _drop_ledger_provenance_columns(bind) -> None:
    if not _has_table(bind, "usage_ledger_entries"):
        return
    columns = _column_names(bind, "usage_ledger_entries")
    drop_project = "project_id" in columns
    drop_activity = "activity_type" in columns
    if not (drop_project or drop_activity):
        return
    indexes = _index_names(bind, "usage_ledger_entries")
    project_fk_names = [
        str(item["name"])
        for item in _foreign_keys(bind, "usage_ledger_entries")
        if item.get("name")
        and list(item.get("constrained_columns") or []) == ["project_id"]
    ]
    with op.batch_alter_table("usage_ledger_entries") as batch:
        if drop_project and _PROJECT_INDEX in indexes:
            batch.drop_index(_PROJECT_INDEX)
        if drop_project:
            for constraint_name in project_fk_names:
                batch.drop_constraint(constraint_name, type_="foreignkey")
            batch.drop_column("project_id")
        if drop_activity:
            batch.drop_column("activity_type")


def downgrade() -> None:
    bind = op.get_bind()
    if _is_offline() or not _has_table(bind, "api_usage_events"):
        op.create_table(
            "api_usage_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("event_type", sa.String(length=100), nullable=False),
            sa.Column("amount", sa.Integer(), nullable=False),
            sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
            sa.Column("event_metadata", sa.JSON()),
            sa.Column("created_at", sa.DateTime()),
        )
        op.create_index("ix_api_usage_events_id", "api_usage_events", ["id"])
        op.create_index(
            "ix_api_usage_events_user_id", "api_usage_events", ["user_id"]
        )
        op.create_index(
            "ix_api_usage_events_project_id", "api_usage_events", ["project_id"]
        )
    if not _is_offline():
        _restore_legacy_events(bind)
    _drop_ledger_provenance_columns(bind)
    _set_ledger_source_fks(bind, reservation_ondelete="CASCADE")
