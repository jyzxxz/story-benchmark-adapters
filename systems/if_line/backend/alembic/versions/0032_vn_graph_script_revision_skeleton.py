"""Key VNGraph history and review Heads by ChapterScriptRevision.

Revision ID: 0032_vn_graph_script_revision_skeleton
Revises: 0031_chapter_script_revision_heads
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0032_vn_graph_script_revision_skeleton"
down_revision = "0031_chapter_script_revision_heads"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def _upgrade_revision_keys_offline() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS ix_vn_graph_revisions_script_revision_id"))
    op.execute(sa.text("ALTER TABLE vn_graph_revisions DROP CONSTRAINT IF EXISTS uq_vngraph_revision_no"))
    op.execute(sa.text("ALTER TABLE vn_graph_revisions DROP CONSTRAINT IF EXISTS uq_vngraph_revision_hash"))
    op.execute(
        sa.text(
            "DO $migration$ BEGIN "
            "IF NOT EXISTS (SELECT 1 FROM pg_constraint "
            "WHERE conname = 'uq_vngraph_script_revision_no') THEN "
            "ALTER TABLE vn_graph_revisions "
            "ADD CONSTRAINT uq_vngraph_script_revision_no "
            "UNIQUE (script_revision_id, revision_no); "
            "END IF; END $migration$"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_vngraph_revision_script_created "
            "ON vn_graph_revisions (script_revision_id, created_at)"
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    if offline:
        _upgrade_revision_keys_offline()
    else:
        inspector = sa.inspect(bind)
        indexes = {
            item["name"] for item in inspector.get_indexes("vn_graph_revisions")
        }
        constraints = {
            item["name"]
            for item in inspector.get_unique_constraints("vn_graph_revisions")
        }
        if "ix_vn_graph_revisions_script_revision_id" in indexes:
            op.drop_index(
                "ix_vn_graph_revisions_script_revision_id",
                table_name="vn_graph_revisions",
            )
        with op.batch_alter_table("vn_graph_revisions") as batch:
            if "uq_vngraph_revision_no" in constraints:
                batch.drop_constraint("uq_vngraph_revision_no", type_="unique")
            if "uq_vngraph_revision_hash" in constraints:
                batch.drop_constraint("uq_vngraph_revision_hash", type_="unique")
            if "uq_vngraph_script_revision_no" not in constraints:
                batch.create_unique_constraint(
                    "uq_vngraph_script_revision_no",
                    ["script_revision_id", "revision_no"],
                )
        if "ix_vngraph_revision_script_created" not in indexes:
            op.create_index(
                "ix_vngraph_revision_script_created",
                "vn_graph_revisions",
                ["script_revision_id", "created_at"],
            )

    op.execute(
        sa.text(
            "UPDATE vn_graph_revisions AS graph "
            "SET script_revision_id = ("
            "SELECT script.id FROM chapter_script_revisions AS script "
            "WHERE script.chapter_revision_id = graph.chapter_revision_id "
            "ORDER BY script.revision_no DESC, script.created_at DESC LIMIT 1"
            ") WHERE graph.script_revision_id IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE vn_graph_heads "
            "SET script_revision_id = ("
            "SELECT script_revision_id FROM vn_graph_revisions "
            "WHERE vn_graph_revisions.id = vn_graph_heads.current_revision_id"
            ") WHERE script_revision_id IS NULL"
        )
    )
    with op.batch_alter_table("vn_graph_revisions") as batch:
        batch.alter_column(
            "script_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
    with op.batch_alter_table("vn_graph_heads") as batch:
        batch.alter_column(
            "script_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(sa.text("DELETE FROM vn_graph_heads WHERE current_revision_id IS NULL"))
    with op.batch_alter_table("vn_graph_heads") as batch:
        batch.alter_column(
            "current_revision_id",
            existing_type=sa.String(36),
            nullable=False,
        )
        batch.alter_column(
            "script_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )
    with op.batch_alter_table("vn_graph_revisions") as batch:
        batch.alter_column(
            "script_revision_id",
            existing_type=sa.String(36),
            nullable=True,
        )
    op.drop_index(
        "ix_vngraph_revision_script_created",
        table_name="vn_graph_revisions",
    )
    with op.batch_alter_table("vn_graph_revisions") as batch:
        batch.drop_constraint("uq_vngraph_script_revision_no", type_="unique")
        batch.create_unique_constraint(
            "uq_vngraph_revision_no",
            ["project_id", "chapter_index", "revision_no"],
        )
        if bind.dialect.name == "sqlite":
            batch.create_unique_constraint(
                "uq_vngraph_revision_hash",
                ["project_id", "chapter_index", "graph_hash"],
            )
    op.create_index(
        "ix_vn_graph_revisions_script_revision_id",
        "vn_graph_revisions",
        ["script_revision_id"],
    )
