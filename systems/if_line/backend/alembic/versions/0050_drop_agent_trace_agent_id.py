"""Drop redundant server-agent trace agent_id.

Revision ID: 0050_drop_agent_trace_agent_id
Revises: 0049_agent_trace_events
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0050_drop_agent_trace_agent_id"
down_revision = "0049_agent_trace_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        inspector = sa.inspect(bind)
        columns = {column["name"] for column in inspector.get_columns("agent_trace_events")}
        if "agent_id" not in columns:
            return
        indexes = {index["name"] for index in inspector.get_indexes("agent_trace_events")}
        if "ix_agent_trace_agent_run" in indexes:
            op.drop_index("ix_agent_trace_agent_run", table_name="agent_trace_events")
        if "ix_agent_trace_events_agent_id" in indexes:
            op.drop_index("ix_agent_trace_events_agent_id", table_name="agent_trace_events")
    with op.batch_alter_table("agent_trace_events") as batch:
        batch.drop_column("agent_id")


def downgrade() -> None:
    op.add_column("agent_trace_events", sa.Column("agent_id", sa.String(length=64), nullable=True))
    op.execute("UPDATE agent_trace_events SET agent_id = thread_id")
    with op.batch_alter_table("agent_trace_events") as batch:
        batch.alter_column("agent_id", existing_type=sa.String(length=64), nullable=False)
    op.create_index("ix_agent_trace_events_agent_id", "agent_trace_events", ["agent_id"])
    op.create_index("ix_agent_trace_agent_run", "agent_trace_events", ["agent_id", "run_id"])
