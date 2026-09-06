"""Add minimax voice binding columns to character_voice_bindings.

Revision ID: 0011_voice_binding_minimax
Revises: 0010_publish_continuation_media
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0011_voice_binding_minimax"
down_revision = "0010_publish_continuation_media"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
    if op.get_context().as_sql:
        return True
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("character_voice_bindings"):
        return
    with op.batch_alter_table("character_voice_bindings") as batch:
        batch.add_column(sa.Column("minimax_voice_id", sa.String(length=120), nullable=True))
        batch.add_column(sa.Column("minimax_speed", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("minimax_pitch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("minimax_volume", sa.Integer(), nullable=True))


def downgrade() -> None:
    if not _table_exists("character_voice_bindings"):
        return
    with op.batch_alter_table("character_voice_bindings") as batch:
        batch.drop_column("minimax_volume")
        batch.drop_column("minimax_pitch")
        batch.drop_column("minimax_speed")
        batch.drop_column("minimax_voice_id")
