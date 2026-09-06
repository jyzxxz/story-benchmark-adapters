"""Allow a CandidateSet Head to exist before its first review selection.

Revision ID: 0029_nullable_candidate_set_head
Revises: 0028_candidate_set_payload
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0029_nullable_candidate_set_head"
down_revision = "0028_candidate_set_payload"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    with op.batch_alter_table("candidate_set_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM candidate_set_heads WHERE current_revision_id IS NULL"))
    with op.batch_alter_table("candidate_set_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
