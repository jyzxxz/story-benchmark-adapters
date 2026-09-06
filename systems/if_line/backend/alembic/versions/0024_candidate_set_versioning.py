"""Version authoring branch candidate sets per StoryPath checkpoint.

Revision ID: 0024_candidate_set_versioning
Revises: 0023_path_revision_binding
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0024_candidate_set_versioning"
down_revision = "0023_path_revision_binding"
branch_labels = None
depends_on = None


TABLES = ("candidate_set_revisions", "candidate_set_heads")


def _create_candidate_set_revisions() -> None:
    op.create_table(
        "candidate_set_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "story_path_id",
            sa.String(36),
            sa.ForeignKey(
                "story_paths.id",
                name="fk_candidate_set_revisions_story_path_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column(
            "checkpoint_node_id",
            sa.String(36),
            sa.ForeignKey("story_nodes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "chapter_revision_id",
            sa.String(36),
            sa.ForeignKey("chapter_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "state_snapshot_id",
            sa.String(36),
            sa.ForeignKey("state_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_revision_id",
            sa.String(36),
            sa.ForeignKey("candidate_set_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "story_path_id",
            "checkpoint_node_id",
            "revision_no",
            name="uq_candidate_set_path_checkpoint_revision",
        ),
        sa.UniqueConstraint(
            "generation_task_id",
            name="uq_candidate_set_generation_task",
        ),
        sa.CheckConstraint(
            "candidate_count >= 2 AND candidate_count <= 4",
            name="ck_candidate_set_candidate_count",
        ),
    )
    for column in ("project_id", "story_path_id", "checkpoint_node_id"):
        op.create_index(
            f"ix_candidate_set_revisions_{column}",
            "candidate_set_revisions",
            [column],
        )
    op.create_index(
        "ix_candidate_set_path_checkpoint_created",
        "candidate_set_revisions",
        ["story_path_id", "checkpoint_node_id", "created_at"],
    )
    op.create_index(
        "ix_candidate_set_source",
        "candidate_set_revisions",
        ["chapter_revision_id", "state_snapshot_id", "source_hash"],
    )


def _create_candidate_set_heads() -> None:
    op.create_table(
        "candidate_set_heads",
        sa.Column(
            "story_path_id",
            sa.String(36),
            sa.ForeignKey("story_paths.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "checkpoint_node_id",
            sa.String(36),
            sa.ForeignKey("story_nodes.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("candidate_set_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lock_version > 0",
            name="ck_candidate_set_head_lock_version",
        ),
    )


def upgrade() -> None:
    _create_candidate_set_revisions()
    _create_candidate_set_heads()

    with op.batch_alter_table("branch_candidates") as batch:
        batch.add_column(
            sa.Column("candidate_set_revision_id", sa.String(36), nullable=True)
        )
        batch.create_foreign_key(
            "fk_branch_candidates_candidate_set_revision_id",
            "candidate_set_revisions",
            ["candidate_set_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_branch_candidates_candidate_set_revision_id",
            ["candidate_set_revision_id"],
        )
        batch.drop_constraint("uq_branch_candidate_option", type_="unique")
        batch.create_unique_constraint(
            "uq_branch_candidate_set_option",
            ["candidate_set_revision_id", "option_key"],
        )


def downgrade() -> None:
    with op.batch_alter_table("branch_candidates") as batch:
        batch.drop_constraint("uq_branch_candidate_set_option", type_="unique")
        batch.create_unique_constraint(
            "uq_branch_candidate_option", ["checkpoint_node_id", "option_key"]
        )
        batch.drop_index("ix_branch_candidates_candidate_set_revision_id")
        batch.drop_constraint(
            "fk_branch_candidates_candidate_set_revision_id", type_="foreignkey"
        )
        batch.drop_column("candidate_set_revision_id")

    op.drop_table("candidate_set_heads")
    op.drop_table("candidate_set_revisions")
