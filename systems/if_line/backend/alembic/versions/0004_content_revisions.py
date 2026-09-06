"""Immutable Bible, outline, chapter and VNGraph revisions.

Revision ID: 0004_content_revisions
Revises: 0003_tasks_usage
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0004_content_revisions"
down_revision = "0003_tasks_usage"
branch_labels = None
depends_on = None


TABLES = (
    "project_content_heads",
    "story_bible_revisions",
    "outline_revisions",
    "outline_revision_chapters",
    # Chapter revisions now carry a real FK to their generation state.  The
    # snapshot table is created here (rather than 0006) so PostgreSQL never
    # sees a forward reference to a table that does not exist yet.
    "state_snapshots",
    "chapter_revisions",
    "chapter_heads",
    "chapter_segments",
    "vn_graph_revisions",
    "vn_graph_heads",
)
DEFERRED_FOREIGN_KEY_TARGETS = {
    "outline_revisions": {"story_paths"},
    "outline_revision_chapters": {"story_path_chapters"},
    "chapter_revisions": {"chapter_slots", "story_paths"},
    "vn_graph_revisions": {"chapter_script_revisions"},
    "vn_graph_heads": {"chapter_script_revisions"},
}


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _create_historical_vn_graph_revisions() -> None:
    """Create the 0004 table without columns introduced by later revisions."""

    op.create_table(
        "vn_graph_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column(
            "chapter_revision_id",
            sa.String(36),
            sa.ForeignKey("chapter_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_revision_id",
            sa.String(36),
            sa.ForeignKey("vn_graph_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("graph_hash", sa.String(64), nullable=False),
        sa.Column("graph_json", sa.JSON(), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("compiler_version", sa.String(32), nullable=False),
        sa.Column("tachi_policy_version", sa.String(32), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("legacy_source_table", sa.String(64)),
        sa.Column("legacy_source_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "chapter_index", "revision_no", name="uq_vngraph_revision_no"
        ),
        sa.UniqueConstraint(
            "project_id", "chapter_index", "graph_hash", name="uq_vngraph_revision_hash"
        ),
    )
    op.create_index(
        "ix_vn_graph_revisions_project_id",
        "vn_graph_revisions",
        ["project_id"],
    )


def _create_historical_vn_graph_heads() -> None:
    op.create_table(
        "vn_graph_heads",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chapter_index", sa.Integer(), primary_key=True),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("vn_graph_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _create_historical_outline_revisions() -> None:
    op.create_table(
        "outline_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "bible_revision_id",
            sa.String(36),
            sa.ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "parent_revision_id",
            sa.String(36),
            sa.ForeignKey("outline_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("legacy_source_table", sa.String(64)),
        sa.Column("legacy_source_id", sa.String(64)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "revision_no", name="uq_outline_revision_no"),
        sa.UniqueConstraint(
            "project_id",
            "source_hash",
            "content_hash",
            name="uq_outline_revision_source_content",
        ),
    )
    op.create_index("ix_outline_revisions_project_id", "outline_revisions", ["project_id"])


def _create_historical_outline_chapters() -> None:
    op.create_table(
        "outline_revision_chapters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "outline_revision_id",
            sa.String(36),
            sa.ForeignKey("outline_revisions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("summary", sa.Text()),
        sa.Column("conflict", sa.Text()),
        sa.Column("characters", sa.JSON(), nullable=False),
        sa.Column("scene", sa.String(500)),
        sa.Column("emotion", sa.String(100)),
        sa.Column("visual_keywords", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("legacy_source_id", sa.String(64)),
        sa.UniqueConstraint(
            "outline_revision_id", "chapter_index", name="uq_outline_revision_chapter"
        ),
    )


def _create_historical_chapter_revisions() -> None:
    op.create_table(
        "chapter_revisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column(
            "parent_revision_id",
            sa.String(36),
            sa.ForeignKey("chapter_revisions.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "bible_revision_id",
            sa.String(36),
            sa.ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "outline_revision_id",
            sa.String(36),
            sa.ForeignKey("outline_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "state_snapshot_id",
            sa.String(36),
            sa.ForeignKey("state_snapshots.id", ondelete="SET NULL"),
        ),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column("legacy_source_table", sa.String(64)),
        sa.Column("legacy_source_id", sa.String(64)),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "chapter_index", "revision_no", name="uq_chapter_revision_no"
        ),
        sa.UniqueConstraint(
            "project_id",
            "chapter_index",
            "source_hash",
            "content_hash",
            name="uq_chapter_revision_source_content",
        ),
    )
    op.create_index("ix_chapter_revisions_project_id", "chapter_revisions", ["project_id"])
    op.create_index(
        "ix_chapter_revisions_state_snapshot_id",
        "chapter_revisions",
        ["state_snapshot_id"],
    )
    op.create_index(
        "ix_chapter_revision_lookup",
        "chapter_revisions",
        ["project_id", "chapter_index", "created_at"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        if name == "outline_revisions":
            _create_historical_outline_revisions()
        elif name == "outline_revision_chapters":
            _create_historical_outline_chapters()
        elif name == "chapter_revisions":
            _create_historical_chapter_revisions()
        elif name == "vn_graph_revisions":
            _create_historical_vn_graph_revisions()
        elif name == "vn_graph_heads":
            _create_historical_vn_graph_heads()
        else:
            _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=True)
