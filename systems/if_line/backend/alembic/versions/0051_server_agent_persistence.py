"""Add durable server-agent conversation tables.

Revision ID: 0051_server_agent_persistence
Revises: 0050_drop_agent_trace_agent_id
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0051_server_agent_persistence"
down_revision = "0050_drop_agent_trace_agent_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_threads",
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("agent_kind", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("user_prompt", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("max_sampling_steps", sa.Integer(), nullable=False),
        sa.Column("current_turn_id", sa.String(length=64), nullable=True),
        sa.Column("last_turn_id", sa.String(length=64), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('restricted','solo')", name="ck_agent_thread_mode"),
        sa.CheckConstraint("message_count >= 0", name="ck_agent_thread_message_count"),
        sa.CheckConstraint("event_count >= 0", name="ck_agent_thread_event_count"),
        sa.PrimaryKeyConstraint("thread_id"),
    )
    op.create_index("ix_agent_threads_current_turn_id", "agent_threads", ["current_turn_id"])
    op.create_index("ix_agent_threads_last_turn_id", "agent_threads", ["last_turn_id"])
    op.create_index("ix_agent_threads_status", "agent_threads", ["status"])

    op.create_table(
        "agent_turns",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("message_start_index", sa.Integer(), nullable=False),
        sa.Column("message_end_index", sa.Integer(), nullable=True),
        sa.Column("event_start_seq", sa.Integer(), nullable=False),
        sa.Column("event_end_seq", sa.Integer(), nullable=True),
        sa.Column("sampling_steps", sa.Integer(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("mode IN ('restricted','solo')", name="ck_agent_turn_mode"),
        sa.CheckConstraint(
            "status IN ('running','waiting_user','completed','failed','interrupted')",
            name="ck_agent_turn_status",
        ),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("turn_id"),
    )
    op.create_index("ix_agent_turn_thread_created", "agent_turns", ["thread_id", "created_at"])
    op.create_index("ix_agent_turns_status", "agent_turns", ["status"])
    op.create_index("ix_agent_turns_thread_id", "agent_turns", ["thread_id"])

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("message_index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("message", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "message_index", name="uq_agent_message_thread_index"),
    )
    op.create_index("ix_agent_message_thread_index", "agent_messages", ["thread_id", "message_index"])
    op.create_index("ix_agent_messages_thread_id", "agent_messages", ["thread_id"])
    op.create_index("ix_agent_messages_turn_id", "agent_messages", ["turn_id"])

    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["agent_threads.thread_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("thread_id", "seq", name="uq_agent_event_thread_seq"),
    )
    op.create_index("ix_agent_event_thread_seq", "agent_events", ["thread_id", "seq"])
    op.create_index("ix_agent_event_thread_turn_seq", "agent_events", ["thread_id", "turn_id", "seq"])
    op.create_index("ix_agent_events_event_type", "agent_events", ["event_type"])
    op.create_index("ix_agent_events_thread_id", "agent_events", ["thread_id"])
    op.create_index("ix_agent_events_turn_id", "agent_events", ["turn_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_events_turn_id", table_name="agent_events")
    op.drop_index("ix_agent_events_thread_id", table_name="agent_events")
    op.drop_index("ix_agent_events_event_type", table_name="agent_events")
    op.drop_index("ix_agent_event_thread_turn_seq", table_name="agent_events")
    op.drop_index("ix_agent_event_thread_seq", table_name="agent_events")
    op.drop_table("agent_events")

    op.drop_index("ix_agent_messages_turn_id", table_name="agent_messages")
    op.drop_index("ix_agent_messages_thread_id", table_name="agent_messages")
    op.drop_index("ix_agent_message_thread_index", table_name="agent_messages")
    op.drop_table("agent_messages")

    op.drop_index("ix_agent_turns_thread_id", table_name="agent_turns")
    op.drop_index("ix_agent_turns_status", table_name="agent_turns")
    op.drop_index("ix_agent_turn_thread_created", table_name="agent_turns")
    op.drop_table("agent_turns")

    op.drop_index("ix_agent_threads_status", table_name="agent_threads")
    op.drop_index("ix_agent_threads_last_turn_id", table_name="agent_threads")
    op.drop_index("ix_agent_threads_current_turn_id", table_name="agent_threads")
    op.drop_table("agent_threads")
