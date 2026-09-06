"""Add owner to server-agent threads.

Revision ID: 0052_agent_thread_owner
Revises: 0051_server_agent_persistence
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0052_agent_thread_owner"
down_revision = "0051_server_agent_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 离线渲染 DDL 按“完整库”输出；在线时最小测试库可能没有 users 表，
    # batch 外键反射会 NoSuchTableError，按 0050 的惯例守卫。
    offline = op.get_context().as_sql
    has_users = offline or (
        "users" in sa.inspect(op.get_bind()).get_table_names()
    )
    with op.batch_alter_table("agent_threads") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        if has_users:
            batch.create_foreign_key(
                "fk_agent_threads_owner_id_users",
                "users",
                ["owner_id"],
                ["id"],
                ondelete="CASCADE",
            )
    op.create_index("ix_agent_threads_owner_id", "agent_threads", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_threads_owner_id", table_name="agent_threads")
    if op.get_context().as_sql:
        with op.batch_alter_table("agent_threads") as batch:
            batch.drop_constraint("fk_agent_threads_owner_id_users", type_="foreignkey")
            batch.drop_column("owner_id")
        return
    inspector = sa.inspect(op.get_bind())
    with op.batch_alter_table("agent_threads") as batch:
        fk_names = {
            fk["name"]
            for fk in inspector.get_foreign_keys("agent_threads")
            if fk.get("name")
        }
        if "fk_agent_threads_owner_id_users" in fk_names:
            batch.drop_constraint("fk_agent_threads_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
