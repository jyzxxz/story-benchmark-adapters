"""移除服务端 Agent thread 的旧提示和采样配置字段。

Revision ID: 0057_drop_agent_thread_prompt_sampling_config
Revises: 0056_merge_agent_persistence_and_token_heads
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0057_drop_agent_thread_prompt_sampling_config"
down_revision = "0056_merge_agent_persistence_and_token_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_column("max_sampling_steps")
        batch.drop_column("user_prompt")


def downgrade() -> None:
    # 先带 server_default 加列让存量行落值，再去掉默认；拆成两个 batch
    # 是因为 SQLite 重建表时单批内去默认会导致 NOT NULL 列无值可填。
    with op.batch_alter_table("agent_threads") as batch:
        batch.add_column(
            sa.Column("user_prompt", sa.Text(), nullable=False, server_default="")
        )
        batch.add_column(
            sa.Column(
                "max_sampling_steps", sa.Integer(), nullable=False, server_default="8"
            )
        )
    with op.batch_alter_table("agent_threads") as batch:
        batch.alter_column("user_prompt", server_default=None)
        batch.alter_column("max_sampling_steps", server_default=None)
