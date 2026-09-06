"""Add fencing tokens to generation task leases.

Revision ID: 0045_generation_task_lease_fencing
Revises: 0044_story_path_chapter_lifecycle
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0045_generation_task_lease_fencing"
down_revision = "0044_story_path_chapter_lifecycle"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()
_TABLE = "generation_tasks"


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not inspect(bind).has_table(_TABLE):
        return
    columns = {column["name"] for column in inspect(bind).get_columns(_TABLE)}
    if "lease_token" not in columns:
        with op.batch_alter_table(_TABLE) as batch:
            batch.add_column(sa.Column("lease_token", sa.String(36), nullable=True))
    if {"status", "lease_owner", "heartbeat_at"} <= columns:
        generation_tasks = sa.table(
            _TABLE,
            sa.column("status", sa.String(24)),
            sa.column("lease_owner", sa.String(255)),
            sa.column("lease_token", sa.String(36)),
            sa.column("heartbeat_at", sa.DateTime(timezone=True)),
        )
        # A rolling deploy must stop old workers before this migration. Mark
        # their tokenless leases stale so the new recovery loop can reclaim them.
        op.execute(
            generation_tasks.update()
            .where(
                generation_tasks.c.status == "running",
                generation_tasks.c.lease_owner.is_not(None),
                generation_tasks.c.lease_token.is_(None),
            )
            .values(lease_owner=None, heartbeat_at=None)
        )


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not inspect(bind).has_table(_TABLE):
        return
    columns = {column["name"] for column in inspect(bind).get_columns(_TABLE)}
    if "lease_token" in columns:
        generation_tasks = sa.table(
            _TABLE,
            sa.column("lease_token", sa.String(36)),
        )
        active_tokens = bind.execute(
            sa.select(sa.func.count())
            .select_from(generation_tasks)
            .where(generation_tasks.c.lease_token.is_not(None))
        ).scalar_one()
        if active_tokens:
            raise RuntimeError(
                "0045 cannot be downgraded while generation task leases are active"
            )
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column("lease_token")
