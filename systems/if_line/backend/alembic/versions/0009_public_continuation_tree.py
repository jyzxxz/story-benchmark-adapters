"""Persist public continuation-tree identity and reader selection.

Revision ID: 0009_public_continuation_tree
Revises: 0008_visual_asset_workflows
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0009_public_continuation_tree"
down_revision = "0008_visual_asset_workflows"
branch_labels = None
depends_on = None


def _state_json(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    context = op.get_context()
    if context.as_sql:
        # Earlier migrations create tables from live application metadata. In
        # offline PostgreSQL output, guard against columns already present in
        # that metadata while keeping upgrades from historical 0008 databases.
        quote = context.dialect.identifier_preparer.quote
        type_sql = column.type.compile(dialect=context.dialect)
        nullable_sql = "" if column.nullable else " NOT NULL"
        op.execute(
            f"ALTER TABLE {quote(table_name)} ADD COLUMN IF NOT EXISTS "
            f"{quote(column.name)} {type_sql}{nullable_sql}"
        )
        return
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}
    if column.name not in existing:
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str,
    table_name: str,
    columns: list[str],
) -> None:
    context = op.get_context()
    if context.as_sql:
        quote = context.dialect.identifier_preparer.quote
        rendered_columns = ", ".join(quote(column) for column in columns)
        op.execute(
            f"CREATE INDEX IF NOT EXISTS {quote(index_name)} "
            f"ON {quote(table_name)} ({rendered_columns})"
        )
        return
    existing = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    _add_column_if_missing(
        "reading_sessions",
        sa.Column("selected_continuation_id", sa.String(36)),
    )
    _create_index_if_missing(
        "ix_reading_sessions_selected_continuation_id",
        "reading_sessions",
        ["selected_continuation_id"],
    )
    _add_column_if_missing(
        "reading_continuations",
        sa.Column("release_id", sa.String(36)),
    )
    _add_column_if_missing(
        "reading_continuations",
        sa.Column("chapter_number", sa.Integer()),
    )
    _create_index_if_missing(
        "ix_reading_continuations_release_id",
        "reading_continuations",
        ["release_id"],
    )
    _create_index_if_missing(
        "ix_reading_continuations_chapter_number",
        "reading_continuations",
        ["chapter_number"],
    )
    _create_index_if_missing(
        "ix_public_continuation_chapter",
        "reading_continuations",
        ["release_id", "chapter_number", "status", "confirmed_at"],
    )

    if op.get_context().as_sql:
        # Data backfill requires row access and is intentionally omitted from
        # offline SQL rendering. Fresh databases contain no rows to backfill.
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT rc.id, rs.release_id, ss.state_json
            FROM reading_continuations AS rc
            JOIN reading_sessions AS rs ON rs.id = rc.session_id
            LEFT JOIN state_snapshots AS ss ON ss.id = rs.state_snapshot_id
            """
        )
    ).mappings()
    for row in rows:
        state = _state_json(row["state_json"])
        raw_chapter = state.get("chapter_number", state.get("chapter"))
        try:
            chapter_number = int(raw_chapter) if raw_chapter is not None else None
        except (TypeError, ValueError):
            chapter_number = None
        bind.execute(
            sa.text(
                """
                UPDATE reading_continuations
                SET release_id = :release_id, chapter_number = :chapter_number
                WHERE id = :continuation_id
                """
            ),
            {
                "release_id": row["release_id"],
                "chapter_number": chapter_number,
                "continuation_id": row["id"],
            },
        )


def downgrade() -> None:
    op.drop_index("ix_public_continuation_chapter", table_name="reading_continuations")
    op.drop_index("ix_reading_continuations_chapter_number", table_name="reading_continuations")
    op.drop_index("ix_reading_continuations_release_id", table_name="reading_continuations")
    with op.batch_alter_table("reading_continuations") as batch:
        batch.drop_column("chapter_number")
        batch.drop_column("release_id")
    op.drop_index("ix_reading_sessions_selected_continuation_id", table_name="reading_sessions")
    with op.batch_alter_table("reading_sessions") as batch:
        batch.drop_column("selected_continuation_id")
