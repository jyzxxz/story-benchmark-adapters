"""删除 Agent 事件冗余 event_index"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0055_drop_agent_event_index"
down_revision = "0054_rename_agent_thread_message_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_trace_events") as batch:
        batch.drop_column("event_index")
    with op.batch_alter_table("agent_events") as batch:
        batch.drop_column("event_index")


def downgrade() -> None:
    op.add_column("agent_events", sa.Column("event_index", sa.Integer(), nullable=True))
    op.execute("UPDATE agent_events SET event_index = seq - 1")
    with op.batch_alter_table("agent_events") as batch:
        batch.alter_column("event_index", existing_type=sa.Integer(), nullable=False)
    op.add_column("agent_trace_events", sa.Column("event_index", sa.Integer(), nullable=True))
