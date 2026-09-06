"""Add an explicit active/detached lifecycle to PathChapters.

Revision ID: 0044_story_path_chapter_lifecycle
Revises: 0043_repair_legacy_outline_contract
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0044_story_path_chapter_lifecycle"
down_revision = "0043_repair_legacy_outline_contract"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_TABLE = "story_path_chapters"
_ACTIVE_DISPLAY_INDEX = "uq_story_path_chapter_display_index"
_DETACHED_BY_INDEX = (
    "ix_story_path_chapters_detached_by_outline_revision_id"
)
_DETACHED_BY_FK = "fk_story_path_chapters_detached_by_outline_revision_id"
_STATUS_CHECK = "ck_story_path_chapter_status"
_DETACHMENT_CHECK = "ck_story_path_chapter_detachment"


def _has_table(bind) -> bool:
    return inspect(bind).has_table(_TABLE)


def _sqlite_referencing_triggers(bind) -> tuple[tuple[str, str], ...]:
    if bind.dialect.name != "sqlite":
        return ()
    rows = bind.execute(
        sa.text(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'trigger' AND sql IS NOT NULL "
            "AND (tbl_name = :table_name OR instr(lower(sql), :table_name) > 0)"
        ),
        {"table_name": _TABLE.lower()},
    ).all()
    return tuple((str(row.name), str(row.sql)) for row in rows)


def _drop_sqlite_triggers(bind, triggers: tuple[tuple[str, str], ...]) -> None:
    for name, _sql in triggers:
        quoted_name = name.replace('"', '""')
        bind.exec_driver_sql(f'DROP TRIGGER IF EXISTS "{quoted_name}"')


def _restore_sqlite_triggers(bind, triggers: tuple[tuple[str, str], ...]) -> None:
    for _name, sql in triggers:
        bind.exec_driver_sql(sql)


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_table(bind):
        return
    sqlite_triggers = _sqlite_referencing_triggers(bind)
    _drop_sqlite_triggers(bind, sqlite_triggers)

    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_ACTIVE_DISPLAY_INDEX, type_="unique")
        batch.add_column(
            sa.Column(
                "status",
                sa.String(24),
                nullable=False,
                server_default="active",
            )
        )
        batch.add_column(
            sa.Column("detached_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(
            sa.Column(
                "detached_by_outline_revision_id",
                sa.String(36),
                nullable=True,
            )
        )
        batch.create_foreign_key(
            _DETACHED_BY_FK,
            "outline_revisions",
            ["detached_by_outline_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_check_constraint(
            _STATUS_CHECK,
            "status IN ('active','detached')",
        )
        batch.create_check_constraint(
            _DETACHMENT_CHECK,
            "(status = 'active' AND detached_at IS NULL "
            "AND detached_by_outline_revision_id IS NULL) OR "
            "(status = 'detached' AND detached_at IS NOT NULL "
            "AND detached_by_outline_revision_id IS NOT NULL)",
        )

    op.create_index(
        _ACTIVE_DISPLAY_INDEX,
        _TABLE,
        ["story_path_id", "display_index"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        _DETACHED_BY_INDEX,
        _TABLE,
        ["detached_by_outline_revision_id"],
        unique=False,
    )
    _restore_sqlite_triggers(bind, sqlite_triggers)


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_table(bind):
        return
    detached = bind.execute(
        sa.text(
            "SELECT 1 FROM story_path_chapters "
            "WHERE status <> 'active' OR detached_at IS NOT NULL "
            "OR detached_by_outline_revision_id IS NOT NULL LIMIT 1"
        )
    ).first()
    if detached is not None:
        raise RuntimeError(
            "0044 cannot be downgraded after PathChapters have been detached"
        )

    sqlite_triggers = _sqlite_referencing_triggers(bind)
    _drop_sqlite_triggers(bind, sqlite_triggers)
    op.drop_index(_DETACHED_BY_INDEX, table_name=_TABLE)
    op.drop_index(_ACTIVE_DISPLAY_INDEX, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_constraint(_DETACHMENT_CHECK, type_="check")
        batch.drop_constraint(_STATUS_CHECK, type_="check")
        batch.drop_constraint(_DETACHED_BY_FK, type_="foreignkey")
        batch.drop_column("detached_by_outline_revision_id")
        batch.drop_column("detached_at")
        batch.drop_column("status")
        batch.create_unique_constraint(
            _ACTIVE_DISPLAY_INDEX,
            ["story_path_id", "display_index"],
        )
    _restore_sqlite_triggers(bind, sqlite_triggers)
