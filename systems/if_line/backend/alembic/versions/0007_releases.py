"""Immutable releases, reading sessions and persisted decisions.

Revision ID: 0007_releases
Revises: 0006_reading_branching
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0007_releases"
down_revision = "0006_reading_branching"
branch_labels = None
depends_on = None


TABLES = (
    "project_releases",
    "reading_sessions",
    "choice_decisions",
    "reading_bookmarks",
)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _create_historical_project_releases() -> None:
    op.create_table(
        "project_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
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
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "project_id", "version", name="uq_project_release_version"
        ),
        sa.UniqueConstraint(
            "project_id", "manifest_hash", name="uq_project_release_manifest"
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','withdrawn')",
            name="ck_project_release_status",
        ),
    )
    op.create_index(
        "ix_project_releases_project_id",
        "project_releases",
        ["project_id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        if name == "project_releases":
            _create_historical_project_releases()
        else:
            _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
