"""允许通用对话 agent 使用 default mode"""

from alembic import op


revision = "0053_agent_default_mode"
down_revision = "0052_agent_thread_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("ck_agent_thread_mode", type_="check")
        batch.create_check_constraint(
            "ck_agent_thread_mode",
            "mode IN ('restricted','solo','default')",
        )
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("ck_agent_turn_mode", type_="check")
        batch.create_check_constraint(
            "ck_agent_turn_mode",
            "mode IN ('restricted','solo','default')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent_turns") as batch:
        batch.drop_constraint("ck_agent_turn_mode", type_="check")
        batch.create_check_constraint(
            "ck_agent_turn_mode",
            "mode IN ('restricted','solo')",
        )
    with op.batch_alter_table("agent_threads") as batch:
        batch.drop_constraint("ck_agent_thread_mode", type_="check")
        batch.create_check_constraint(
            "ck_agent_thread_mode",
            "mode IN ('restricted','solo')",
        )
