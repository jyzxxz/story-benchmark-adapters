"""项目改为软删除。

Revision ID: 0058_project_soft_delete
Revises: 0057_drop_agent_thread_prompt_sampling_config
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0058_project_soft_delete"
down_revision = "0057_drop_agent_thread_prompt_sampling_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 部分测试/离线场景会在只含少量表的历史库上跑完整迁移链，
    # projects 表不存在时跳过即可（真实库在 0001 已建表）。
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("projects"):
        return
    op.add_column(
        "projects",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_projects_deleted_at", "projects", ["deleted_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("projects"):
        return
    op.drop_index("ix_projects_deleted_at", table_name="projects")
    op.drop_column("projects", "deleted_at")
