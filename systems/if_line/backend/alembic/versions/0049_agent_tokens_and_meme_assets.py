"""Add agent API tokens and meme asset library.

Revision ID: 0049_agent_tokens_and_meme_assets
Revises: 0048_merge_library_asset_and_cleanup_heads
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0049_agent_tokens_and_meme_assets"
down_revision = "0048_merge_library_asset_and_cleanup_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_api_tokens",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_api_tokens_id", "user_api_tokens", ["id"])
    op.create_index("ix_user_api_tokens_user_id", "user_api_tokens", ["user_id"])
    op.create_index("ix_user_api_tokens_token_hash", "user_api_tokens", ["token_hash"], unique=True)

    op.create_table(
        "meme_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("storage_object_id", sa.String(length=36), nullable=False),
        sa.Column("cutout_storage_object_id", sa.String(length=36), nullable=True),
        sa.Column("source_url", sa.String(length=1024), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("real_person_ref", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="raw"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storage_object_id"], ["storage_objects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["cutout_storage_object_id"], ["storage_objects.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "category IN ('influencer','game','abstract','film','society','tech','other')",
            name="ck_meme_asset_category",
        ),
        sa.CheckConstraint(
            "status IN ('raw','selected','used','discarded')",
            name="ck_meme_asset_status",
        ),
    )
    op.create_index("ix_meme_assets_owner_id", "meme_assets", ["owner_id"])
    op.create_index("ix_meme_assets_project_id", "meme_assets", ["project_id"])
    op.create_index("ix_meme_assets_status", "meme_assets", ["status"])
    op.create_index(
        "ix_meme_assets_cutout_storage_object_id", "meme_assets", ["cutout_storage_object_id"]
    )
    op.create_index("ix_meme_asset_owner_status", "meme_assets", ["owner_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_meme_asset_owner_status", table_name="meme_assets")
    op.drop_index("ix_meme_assets_cutout_storage_object_id", table_name="meme_assets")
    op.drop_index("ix_meme_assets_status", table_name="meme_assets")
    op.drop_index("ix_meme_assets_project_id", table_name="meme_assets")
    op.drop_index("ix_meme_assets_owner_id", table_name="meme_assets")
    op.drop_table("meme_assets")
    op.drop_index("ix_user_api_tokens_token_hash", table_name="user_api_tokens")
    op.drop_index("ix_user_api_tokens_user_id", table_name="user_api_tokens")
    op.drop_index("ix_user_api_tokens_id", table_name="user_api_tokens")
    op.drop_table("user_api_tokens")
