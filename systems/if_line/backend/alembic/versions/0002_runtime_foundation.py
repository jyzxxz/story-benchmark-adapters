"""Bridge manually migrated legacy project columns.

Revision ID: 0002_runtime_foundation
Revises: 0001_current_schema
"""
from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision = "0002_runtime_foundation"
down_revision = "0001_current_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Offline SQL represents the new-database path: 0001 already contains all
    # runtime-foundation columns. The legacy bridge is data/schema conditional
    # and must be executed online after preflight and stamp.
    if context.is_offline_mode():
        return

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "projects" not in inspector.get_table_names():
        raise RuntimeError("legacy preflight failed: projects table is missing")

    existing_columns = {column["name"] for column in inspector.get_columns("projects")}
    missing = {
        "owner_id": sa.Column("owner_id", sa.Integer(), nullable=True),
        "visibility": sa.Column(
            "visibility",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'private'"),
        ),
        "is_draft": sa.Column(
            "is_draft",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        "published_at": sa.Column("published_at", sa.DateTime(), nullable=True),
    }
    columns_to_add = [column for name, column in missing.items() if name not in existing_columns]
    if columns_to_add:
        with op.batch_alter_table("projects") as batch:
            for column in columns_to_add:
                batch.add_column(column)

    inspector = sa.inspect(bind)
    foreign_keys = inspector.get_foreign_keys("projects")
    if not any(fk.get("constrained_columns") == ["owner_id"] for fk in foreign_keys):
        with op.batch_alter_table("projects") as batch:
            batch.create_foreign_key(
                "fk_projects_owner_id_users",
                "users",
                ["owner_id"],
                ["id"],
            )

    index_names = {index["name"] for index in sa.inspect(bind).get_indexes("projects")}
    if "ix_projects_owner_id" not in index_names:
        op.create_index("ix_projects_owner_id", "projects", ["owner_id"])


def downgrade() -> None:
    # This bridge may have encountered a database that already had any subset
    # of these columns. Removing them blindly would destroy legacy data.
    raise RuntimeError("0002_runtime_foundation is intentionally irreversible")
