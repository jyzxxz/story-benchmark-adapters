"""Freeze ordered output on CandidateSetRevision.

Revision ID: 0028_candidate_set_payload
Revises: 0027_nullable_outline_head
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0028_candidate_set_payload"
down_revision = "0027_nullable_outline_head"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    with op.batch_alter_table("candidate_set_revisions") as batch:
        batch.add_column(sa.Column("candidates_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("content_hash", sa.String(64), nullable=True))
        batch.create_index(
            "ix_candidate_set_revisions_content_hash",
            ["content_hash"],
        )


def downgrade() -> None:
    with op.batch_alter_table("candidate_set_revisions") as batch:
        batch.drop_index("ix_candidate_set_revisions_content_hash")
        batch.drop_column("content_hash")
        batch.drop_column("candidates_json")
