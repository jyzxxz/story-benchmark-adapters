"""
通用对话 Agent 规格。
"""
from __future__ import annotations

from pathlib import Path

from app.agent.agent_spec import AgentSpec
from app.agent.toolcall import CHAT_AGENT_TOOLCALL_SCHEMAS, execute_chat_agent_toolcall


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "chat_agent"


def _load_prompt(filename: str) -> str:
    text = (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Agent prompt 不能为空: {filename}")
    return text


CHAT_AGENT_SPEC = AgentSpec(
    agent_kind="chat_agent",
    system_prompt=_load_prompt("system.md"),
    mode_prompts={
        "default": _load_prompt("default.md"),
    },
    default_mode="default",
    tool_schemas=CHAT_AGENT_TOOLCALL_SCHEMAS,
    execute_toolcall=execute_chat_agent_toolcall,
)
