"""重命名 Agent thread 持久化消息数字段"""
from __future__ import annotations

from alembic import op


revision = "0054_rename_agent_thread_message_count"
down_revision = "0053_agent_default_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("ck_agent_thread_message_count", type_="check")
        batch.alter_column("message_count", new_column_name="persisted_message_count")
        batch.create_check_constraint(
            "ck_agent_thread_persisted_message_count",
            "persisted_message_count >= 0",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("ck_agent_thread_persisted_message_count", type_="check")
        batch.alter_column("persisted_message_count", new_column_name="message_count")
        batch.create_check_constraint(
            "ck_agent_thread_message_count",
            "message_count >= 0",
        )
