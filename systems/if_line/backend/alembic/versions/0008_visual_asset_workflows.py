"""Unified visual assets, actions and reader continuations.

Revision ID: 0008_visual_asset_workflows
Revises: 0007_releases
"""
from __future__ import annotations

from alembic import op


revision = "0008_visual_asset_workflows"
down_revision = "0007_releases"
branch_labels = None
depends_on = None


TABLES = (
    "library_assets",
    "library_asset_embeddings",
    "scene_manifests",
    "asset_actions",
    "reading_continuations",
)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401

    return Base.metadata.tables[name]


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
