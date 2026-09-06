"""Key Script revision history and review Heads by ChapterRevision.

Revision ID: 0031_chapter_script_revision_heads
Revises: 0030_story_path_promotions
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0031_chapter_script_revision_heads"
down_revision = "0030_story_path_promotions"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def _upgrade_script_revision_keys_offline() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_chapter_script_revision_lookup"))
    op.execute(
        sa.text(
            "ALTER TABLE chapter_script_revisions "
            "DROP CONSTRAINT IF EXISTS uq_chapter_script_revision_no"
        )
    )
    op.execute(
        sa.text(
            "DO $migration$ BEGIN "
            "IF NOT EXISTS ("
            "SELECT 1 FROM pg_constraint "
            "WHERE conname = 'uq_chapter_script_chapter_revision_no'"
            ") THEN "
            "ALTER TABLE chapter_script_revisions "
            "ADD CONSTRAINT uq_chapter_script_chapter_revision_no "
            "UNIQUE (chapter_revision_id, revision_no); "
            "END IF; END $migration$"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_chapter_script_revision_chapter_created "
            "ON chapter_script_revisions (chapter_revision_id, created_at)"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    if op.get_context().as_sql:
        _upgrade_script_revision_keys_offline()
    else:
        inspector = sa.inspect(bind)
        indexes = {
            item["name"]
            for item in inspector.get_indexes("chapter_script_revisions")
        }
        unique_constraints = {
            item["name"]
            for item in inspector.get_unique_constraints("chapter_script_revisions")
        }
        if "ix_chapter_script_revision_lookup" in indexes:
            op.drop_index(
                "ix_chapter_script_revision_lookup",
                table_name="chapter_script_revisions",
            )
        if "uq_chapter_script_revision_no" in unique_constraints:
            with op.batch_alter_table("chapter_script_revisions") as batch:
                batch.drop_constraint("uq_chapter_script_revision_no", type_="unique")
                if "uq_chapter_script_chapter_revision_no" not in unique_constraints:
                    batch.create_unique_constraint(
                        "uq_chapter_script_chapter_revision_no",
                        ["chapter_revision_id", "revision_no"],
                    )
        elif "uq_chapter_script_chapter_revision_no" not in unique_constraints:
            with op.batch_alter_table("chapter_script_revisions") as batch:
                batch.create_unique_constraint(
                    "uq_chapter_script_chapter_revision_no",
                    ["chapter_revision_id", "revision_no"],
                )
        if "ix_chapter_script_revision_chapter_created" not in indexes:
            op.create_index(
                "ix_chapter_script_revision_chapter_created",
                "chapter_script_revisions",
                ["chapter_revision_id", "created_at"],
            )
    op.execute(
        sa.text(
            "UPDATE chapter_script_heads "
            "SET chapter_revision_id = ("
            "SELECT chapter_revision_id FROM chapter_script_revisions "
            "WHERE chapter_script_revisions.id = chapter_script_heads.current_revision_id"
            ") WHERE chapter_revision_id IS NULL"
        )
    )
    with op.batch_alter_table("chapter_script_heads") as batch:
        batch.alter_column(
            "chapter_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM chapter_script_heads "
            "WHERE current_revision_id IS NULL"
        )
    )
    with op.batch_alter_table("chapter_script_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch.alter_column(
            "chapter_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )
    op.drop_index(
        "ix_chapter_script_revision_chapter_created",
        table_name="chapter_script_revisions",
    )
    with op.batch_alter_table("chapter_script_revisions") as batch:
        batch.drop_constraint(
            "uq_chapter_script_chapter_revision_no",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_chapter_script_revision_no",
            ["project_id", "chapter_index", "revision_no"],
        )
    op.create_index(
        "ix_chapter_script_revision_lookup",
        "chapter_script_revisions",
        ["project_id", "chapter_index", "created_at"],
    )
