"""Add the optional source work used by fan-created project visual profiles.

Revision ID: 0012_project_source_work
Revises: 0011_voice_binding_minimax
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0012_project_source_work"
down_revision = "0011_voice_binding_minimax"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.add_column(sa.Column("source_work", sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("source_work")
