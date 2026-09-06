"""Versioned story state and branch DAG primitives.

Revision ID: 0006_reading_branching
Revises: 0005_artifact_domain
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0006_reading_branching"
down_revision = "0005_artifact_domain"
branch_labels = None
depends_on = None


TABLES = (
    "story_nodes",
    "branch_candidates",
    "branch_edges",
)
DEFERRED_FOREIGN_KEY_TARGETS = {
    "branch_candidates": {"candidate_set_revisions"},
}


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _create_historical_branch_candidates() -> None:
    op.create_table(
        "branch_candidates",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_node_id",
            sa.String(36),
            sa.ForeignKey("story_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("option_key", sa.String(128), nullable=False),
        sa.Column(
            "preview_node_id",
            sa.String(36),
            sa.ForeignKey("story_nodes.id", ondelete="SET NULL"),
        ),
        sa.Column("preview_revision_id", sa.String(36)),
        sa.Column("state_delta", sa.JSON(), nullable=False),
        sa.Column("candidate_status", sa.String(24), nullable=False),
        sa.Column("predicted_probability", sa.Float()),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "checkpoint_node_id", "option_key", name="uq_branch_candidate_option"
        ),
    )
    op.create_index("ix_branch_candidates_project_id", "branch_candidates", ["project_id"])
    op.create_index(
        "ix_branch_candidates_preview_revision_id",
        "branch_candidates",
        ["preview_revision_id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        if name == "branch_candidates":
            _create_historical_branch_candidates()
        else:
            _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
