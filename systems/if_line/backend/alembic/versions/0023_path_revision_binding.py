"""Bind outlines and chapters to stable StoryPath identities.

Revision ID: 0023_path_revision_binding
Revises: 0022_story_path_identity
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0023_path_revision_binding"
down_revision = "0022_story_path_identity"
branch_labels = None
depends_on = None


TABLES = ("story_path_outline_heads",)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _upgrade_outline_revisions() -> None:
    with op.batch_alter_table("outline_revisions") as batch:
        batch.add_column(sa.Column("story_path_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_outline_revisions_story_path_id",
            "story_paths",
            ["story_path_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_outline_revisions_story_path_id", ["story_path_id"])
        batch.create_index(
            "ix_outline_revision_path_created", ["story_path_id", "created_at"]
        )
        batch.drop_constraint("uq_outline_revision_no", type_="unique")
        batch.drop_constraint("uq_outline_revision_source_content", type_="unique")
        batch.create_unique_constraint(
            "uq_outline_path_revision_no", ["story_path_id", "revision_no"]
        )
        batch.create_unique_constraint(
            "uq_outline_path_source_content",
            ["story_path_id", "source_hash", "content_hash"],
        )


def _upgrade_outline_chapters() -> None:
    with op.batch_alter_table("outline_revision_chapters") as batch:
        batch.add_column(sa.Column("story_path_chapter_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("display_index", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_outline_revision_chapters_path_chapter_id",
            "story_path_chapters",
            ["story_path_chapter_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_outline_revision_chapters_story_path_chapter_id",
            ["story_path_chapter_id"],
        )
        batch.create_unique_constraint(
            "uq_outline_revision_path_chapter",
            ["outline_revision_id", "story_path_chapter_id"],
        )
        batch.create_unique_constraint(
            "uq_outline_revision_display_index",
            ["outline_revision_id", "display_index"],
        )

    op.execute(
        "UPDATE outline_revision_chapters "
        "SET display_index = chapter_index WHERE display_index IS NULL"
    )
    with op.batch_alter_table("outline_revision_chapters") as batch:
        batch.alter_column("display_index", existing_type=sa.Integer(), nullable=False)
        batch.create_check_constraint(
            "ck_outline_revision_display_index", "display_index > 0"
        )


def _upgrade_chapter_revisions() -> None:
    with op.batch_alter_table("chapter_revisions") as batch:
        batch.add_column(sa.Column("chapter_slot_id", sa.String(36), nullable=True))
        batch.add_column(sa.Column("created_for_story_path_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column(
                "context_manifest",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            )
        )
        batch.add_column(sa.Column("context_hash", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_chapter_revisions_chapter_slot_id",
            "chapter_slots",
            ["chapter_slot_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_chapter_revisions_created_for_story_path_id",
            "story_paths",
            ["created_for_story_path_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_chapter_revisions_chapter_slot_id", ["chapter_slot_id"])
        batch.create_index(
            "ix_chapter_revisions_created_for_story_path_id",
            ["created_for_story_path_id"],
        )
        batch.create_index(
            "ix_chapter_revision_slot_created", ["chapter_slot_id", "created_at"]
        )
        batch.drop_constraint("uq_chapter_revision_no", type_="unique")
        batch.drop_constraint("uq_chapter_revision_source_content", type_="unique")
        batch.create_unique_constraint(
            "uq_chapter_slot_revision_no", ["chapter_slot_id", "revision_no"]
        )
        batch.create_unique_constraint(
            "uq_chapter_slot_context_content",
            ["chapter_slot_id", "context_hash", "content_hash"],
        )

    op.execute(
        "UPDATE chapter_revisions SET context_hash = source_hash "
        "WHERE context_hash IS NULL"
    )
    with op.batch_alter_table("chapter_revisions") as batch:
        batch.alter_column(
            "context_hash", existing_type=sa.String(64), nullable=False
        )
        batch.alter_column(
            "context_manifest",
            existing_type=sa.JSON(),
            nullable=False,
            server_default=None,
        )


def _upgrade_path_chapter_heads() -> None:
    with op.batch_alter_table("story_path_chapters") as batch:
        batch.add_column(sa.Column("current_revision_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_story_path_chapters_current_revision_id",
            "chapter_revisions",
            ["current_revision_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(
            "ix_story_path_chapters_current_revision_id", ["current_revision_id"]
        )


def upgrade() -> None:
    _upgrade_outline_revisions()
    _table("story_path_outline_heads").create(bind=op.get_bind(), checkfirst=False)
    _upgrade_outline_chapters()
    _upgrade_chapter_revisions()
    _upgrade_path_chapter_heads()


def downgrade() -> None:
    with op.batch_alter_table("story_path_chapters") as batch:
        batch.drop_index("ix_story_path_chapters_current_revision_id")
        batch.drop_constraint(
            "fk_story_path_chapters_current_revision_id", type_="foreignkey"
        )
        batch.drop_column("current_revision_id")

    with op.batch_alter_table("chapter_revisions") as batch:
        batch.drop_index("ix_chapter_revision_slot_created")
        batch.drop_index("ix_chapter_revisions_created_for_story_path_id")
        batch.drop_index("ix_chapter_revisions_chapter_slot_id")
        batch.drop_constraint("uq_chapter_slot_context_content", type_="unique")
        batch.drop_constraint("uq_chapter_slot_revision_no", type_="unique")
        batch.drop_constraint(
            "fk_chapter_revisions_created_for_story_path_id", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_chapter_revisions_chapter_slot_id", type_="foreignkey"
        )
        batch.drop_column("context_hash")
        batch.drop_column("context_manifest")
        batch.drop_column("created_for_story_path_id")
        batch.drop_column("chapter_slot_id")
        batch.create_unique_constraint(
            "uq_chapter_revision_no", ["project_id", "chapter_index", "revision_no"]
        )
        batch.create_unique_constraint(
            "uq_chapter_revision_source_content",
            ["project_id", "chapter_index", "source_hash", "content_hash"],
        )

    with op.batch_alter_table("outline_revision_chapters") as batch:
        batch.drop_constraint("ck_outline_revision_display_index", type_="check")
        batch.drop_constraint("uq_outline_revision_display_index", type_="unique")
        batch.drop_constraint("uq_outline_revision_path_chapter", type_="unique")
        batch.drop_index("ix_outline_revision_chapters_story_path_chapter_id")
        batch.drop_constraint(
            "fk_outline_revision_chapters_path_chapter_id", type_="foreignkey"
        )
        batch.drop_column("display_index")
        batch.drop_column("story_path_chapter_id")

    _table("story_path_outline_heads").drop(bind=op.get_bind(), checkfirst=False)

    with op.batch_alter_table("outline_revisions") as batch:
        batch.drop_constraint("uq_outline_path_source_content", type_="unique")
        batch.drop_constraint("uq_outline_path_revision_no", type_="unique")
        batch.drop_index("ix_outline_revision_path_created")
        batch.drop_index("ix_outline_revisions_story_path_id")
        batch.drop_constraint("fk_outline_revisions_story_path_id", type_="foreignkey")
        batch.drop_column("story_path_id")
        batch.create_unique_constraint(
            "uq_outline_revision_no", ["project_id", "revision_no"]
        )
        batch.create_unique_constraint(
            "uq_outline_revision_source_content",
            ["project_id", "source_hash", "content_hash"],
        )
