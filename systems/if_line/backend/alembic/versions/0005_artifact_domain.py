"""Private storage, voice profiles and versioned media artifacts.

Revision ID: 0005_artifact_domain
Revises: 0004_content_revisions
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_artifact_domain"
down_revision = "0004_content_revisions"
branch_labels = None
depends_on = None


TABLES = (
    "storage_objects",
    "voice_profiles",
    "asset_versions",
    "asset_bindings",
    "voice_lines",
)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def _create_storage_objects() -> None:
    # This migration is a historical snapshot. Keep later model changes out of
    # the bootstrap DDL so their owning migrations remain executable offline.
    op.create_table(
        "storage_objects",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "owner_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
        ),
        sa.Column("storage_backend", sa.String(32), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False, unique=True),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("byte_size >= 0", name="ck_storage_object_size"),
        sa.CheckConstraint(
            "visibility IN ('private','release','public')",
            name="ck_storage_object_visibility",
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','quarantined','deleted')",
            name="ck_storage_object_status",
        ),
    )
    op.create_index("ix_storage_objects_owner_id", "storage_objects", ["owner_id"])
    op.create_index("ix_storage_objects_project_id", "storage_objects", ["project_id"])
    op.create_index("ix_storage_objects_sha256", "storage_objects", ["sha256"])


def _create_asset_versions() -> None:
    op.create_table(
        "asset_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_revision_id", sa.String(36)),
        sa.Column(
            "generation_task_id",
            sa.String(36),
            sa.ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "storage_object_id",
            sa.String(36),
            sa.ForeignKey("storage_objects.id", ondelete="RESTRICT"),
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("cache_key", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64)),
        sa.Column("model", sa.String(128)),
        sa.Column("prompt", sa.Text()),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("negative_prompt_hash", sa.String(64)),
        sa.Column("seed", sa.BigInteger()),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("postprocess_version", sa.String(64)),
        sa.Column("validator_version", sa.String(64)),
        sa.Column("quality_score", sa.Float()),
        sa.Column("safety_status", sa.String(24), nullable=False),
        sa.Column("rights_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "version_no", name="uq_asset_version_no"),
    )
    op.create_index("ix_asset_versions_asset_id", "asset_versions", ["asset_id"])
    op.create_index(
        "ix_asset_versions_source_revision_id", "asset_versions", ["source_revision_id"]
    )
    op.create_index("ix_asset_versions_cache_key", "asset_versions", ["cache_key"])


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        if name == "storage_objects":
            _create_storage_objects()
        elif name == "asset_versions":
            _create_asset_versions()
        else:
            _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
