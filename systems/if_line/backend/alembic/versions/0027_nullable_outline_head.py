"""Allow a StoryPath Outline Head to exist before its first selection.

Revision ID: 0027_nullable_outline_head
Revises: 0026_single_source_publication
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0027_nullable_outline_head"
down_revision = "0026_single_source_publication"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    with op.batch_alter_table("story_path_outline_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM story_path_outline_heads WHERE current_revision_id IS NULL"
        )
    )
    with op.batch_alter_table("story_path_outline_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
