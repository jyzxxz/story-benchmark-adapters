"""Stable StoryPath and chapter identity primitives.

Revision ID: 0022_story_path_identity
Revises: 0021_project_asset_history
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0022_story_path_identity"
down_revision = "0021_project_asset_history"
branch_labels = None
depends_on = None


TABLES = (
    "story_paths",
    "chapter_slots",
    "story_path_chapters",
)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _create_historical_story_path_chapters() -> None:
    op.create_table(
        "story_path_chapters",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "story_path_id",
            sa.String(36),
            sa.ForeignKey("story_paths.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_slot_id",
            sa.String(36),
            sa.ForeignKey("chapter_slots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("display_index", sa.Integer(), nullable=False),
        sa.Column(
            "predecessor_path_chapter_id",
            sa.String(36),
            sa.ForeignKey("story_path_chapters.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "inherited_from_path_chapter_id",
            sa.String(36),
            sa.ForeignKey("story_path_chapters.id", ondelete="RESTRICT"),
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "story_path_id", "display_index", name="uq_story_path_chapter_display_index"
        ),
        sa.UniqueConstraint(
            "story_path_id", "chapter_slot_id", name="uq_story_path_chapter_slot"
        ),
        sa.CheckConstraint("display_index > 0", name="ck_story_path_chapter_display_index"),
        sa.CheckConstraint("lock_version > 0", name="ck_story_path_chapter_lock_version"),
        sa.CheckConstraint(
            "predecessor_path_chapter_id IS NULL OR predecessor_path_chapter_id <> id",
            name="ck_story_path_chapter_not_self_predecessor",
        ),
        sa.CheckConstraint(
            "inherited_from_path_chapter_id IS NULL OR inherited_from_path_chapter_id <> id",
            name="ck_story_path_chapter_not_self_inherited",
        ),
    )
    for column in (
        "story_path_id",
        "chapter_slot_id",
        "predecessor_path_chapter_id",
        "inherited_from_path_chapter_id",
    ):
        op.create_index(f"ix_story_path_chapters_{column}", "story_path_chapters", [column])
    op.create_index(
        "ix_story_path_chapter_order",
        "story_path_chapters",
        ["story_path_id", "display_index"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        if name == "story_path_chapters":
            _create_historical_story_path_chapters()
        else:
            _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
