"""
自动化二创文章生成 Agent

复用 backend/app/services 下的 llm_service / workflow_engine,
无人值守地完成: 选题 → Story Bible → Outline → 全部章节正文。

三种触发方式:
  - CLI 脚本: python -m app.scripts.run_auto_creator --pace medium
  - REST 路由: POST /api/auto-agent/run
  - Cron/Tmux: bash scripts/auto_creator_cron.sh loop

详见 README.md。
"""
from app.agent.auto_creator import AutoCreator
from app.agent.theme_brainstorm import brainstorm_idea

__all__ = ["AutoCreator", "brainstorm_idea"]
