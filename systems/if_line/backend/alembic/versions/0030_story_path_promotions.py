"""Record idempotent candidate-to-StoryPath promotions.

Revision ID: 0030_story_path_promotions
Revises: 0029_nullable_candidate_set_head
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0030_story_path_promotions"
down_revision = "0029_nullable_candidate_set_head"
branch_labels = None
depends_on = None


TABLES = ("story_path_promotion_records",)


def upgrade() -> None:
    op.create_table(
        "story_path_promotion_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.String(36),
            sa.ForeignKey("branch_candidates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "story_path_id",
            sa.String(36),
            sa.ForeignKey("story_paths.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_story_path_promotion_user_idempotency",
        ),
    )
    op.create_index(
        "ix_story_path_promotion_records_user_id",
        "story_path_promotion_records",
        ["user_id"],
    )
    op.create_index(
        "ix_story_path_promotion_records_project_id",
        "story_path_promotion_records",
        ["project_id"],
    )
    op.create_index(
        "ix_story_path_promotion_candidate",
        "story_path_promotion_records",
        ["candidate_id", "story_path_id"],
    )


def downgrade() -> None:
    op.drop_table("story_path_promotion_records")
