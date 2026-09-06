"""合并 Agent 持久化与 token/meme 素材迁移头"""
from __future__ import annotations


revision = "0056_merge_agent_persistence_and_token_heads"
down_revision = ("0049_agent_tokens_and_meme_assets", "0055_drop_agent_event_index")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
