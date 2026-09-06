"""Add durable server-agent trace events.

Revision ID: 0049_agent_trace_events
Revises: 0048_merge_library_asset_and_cleanup_heads
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0049_agent_trace_events"
down_revision = "0048_merge_library_asset_and_cleanup_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_trace_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("run_seq", sa.Integer(), nullable=True),
        sa.Column("event_index", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("span_id", sa.String(length=64), nullable=False),
        sa.Column("parent_span_id", sa.String(length=64), nullable=True),
        sa.Column("tool_call_id", sa.String(length=128), nullable=True),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("model_view", sa.JSON(), nullable=False),
        sa.Column("full_ref", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('agent','model','tool','task','user','frontend','system')",
            name="ck_agent_trace_source",
        ),
        sa.CheckConstraint(
            "visibility IN ('model','ui','admin','debug')",
            name="ck_agent_trace_visibility",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_trace_events_created_at", "agent_trace_events", ["created_at"])
    op.create_index("ix_agent_trace_events_event_type", "agent_trace_events", ["event_type"])
    op.create_index("ix_agent_trace_events_parent_span_id", "agent_trace_events", ["parent_span_id"])
    op.create_index("ix_agent_trace_events_run_id", "agent_trace_events", ["run_id"])
    op.create_index("ix_agent_trace_events_span_id", "agent_trace_events", ["span_id"])
    op.create_index("ix_agent_trace_events_task_id", "agent_trace_events", ["task_id"])
    op.create_index("ix_agent_trace_events_thread_id", "agent_trace_events", ["thread_id"])
    op.create_index("ix_agent_trace_events_tool_call_id", "agent_trace_events", ["tool_call_id"])
    op.create_index("ix_agent_trace_events_tool_name", "agent_trace_events", ["tool_name"])
    op.create_index(
        "ix_agent_trace_thread_run_seq",
        "agent_trace_events",
        ["thread_id", "run_id", "run_seq"],
    )
    op.create_index(
        "ix_agent_trace_type_created",
        "agent_trace_events",
        ["event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_trace_type_created", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_thread_run_seq", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_tool_name", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_tool_call_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_thread_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_task_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_span_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_run_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_parent_span_id", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_event_type", table_name="agent_trace_events")
    op.drop_index("ix_agent_trace_events_created_at", table_name="agent_trace_events")
    op.drop_table("agent_trace_events")
