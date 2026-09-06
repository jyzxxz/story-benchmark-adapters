"""Expand Alembic's revision column before long revision identifiers.

Revision ID: 0016a_expand_version_col
Revises: 0016_backfill_vn_graph_revisions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0016a_expand_version_col"
down_revision = "0016_backfill_vn_graph_revisions"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
