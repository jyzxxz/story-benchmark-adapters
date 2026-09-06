"""
故事创作 Agent 规格。
"""
from __future__ import annotations

from pathlib import Path

from app.agent.agent_spec import AgentSpec
from app.agent.toolcall import STORY_AGENT_TOOLCALL_SCHEMAS, execute_story_agent_toolcall


_PROMPT_DIR = Path(__file__).resolve().parent / "prompts" / "story_agent"


def _load_prompt(filename: str) -> str:
    text = (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Agent prompt 不能为空: {filename}")
    return text


DEFAULT_STORY_AGENT_SPEC = AgentSpec(
    agent_kind="server_agent",
    system_prompt=_load_prompt("system.md"),
    mode_prompts={
        "restricted": _load_prompt("restricted.md"),
        "solo": _load_prompt("solo.md"),
    },
    default_mode="restricted",
    tool_schemas=STORY_AGENT_TOOLCALL_SCHEMAS,
    execute_toolcall=execute_story_agent_toolcall,
)
