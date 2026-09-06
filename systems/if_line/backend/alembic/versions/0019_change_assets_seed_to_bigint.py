"""fix: change assets.seed from INTEGER to BIGINT

Revision ID: 0019
Revises: 0018_drop_uq_vngraph_revision_hash
Create Date: 2026-07-27 23:54:38.877862
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_change_assets_seed_to_bigint"
down_revision: Union[str, None] = "0018_drop_uq_vngraph_revision_hash"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not op.get_context().as_sql:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("assets")}
        if "seed" not in columns:
            return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets") as batch:
            batch.alter_column(
                "seed",
                existing_type=sa.Integer(),
                type_=sa.BigInteger(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "assets",
            "seed",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if not op.get_context().as_sql:
        columns = {column["name"] for column in sa.inspect(bind).get_columns("assets")}
        if "seed" not in columns:
            return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("assets") as batch:
            batch.alter_column(
                "seed",
                existing_type=sa.BigInteger(),
                type_=sa.Integer(),
                existing_nullable=True,
            )
    else:
        op.alter_column(
            "assets",
            "seed",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=True,
        )
