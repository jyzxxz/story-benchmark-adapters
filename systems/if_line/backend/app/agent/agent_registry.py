"""
Agent 类型注册表。

创建入口:
- Python 内部: await server_agent_manager.create_agent(agent_kind="server_agent")
- HTTP 接口: POST /api/server-agent/threads，body 里传 agent_kind。

内部驱动与消费:
- 同步等待: await agent.resume(content="...")
- 后台驱动: server_agent_manager.submit_resume(agent, run_id=..., content="...")
- 事件消费: subscriber_id, queue = agent.subscribe_events(after_seq=0)，随后 await queue.get()
"""
from __future__ import annotations

from app.agent.agent_spec import AgentSpec
from app.agent.chat_agent_spec import CHAT_AGENT_SPEC
from app.agent.story_agent_spec import DEFAULT_STORY_AGENT_SPEC


AGENT_SPECS = {
    DEFAULT_STORY_AGENT_SPEC.agent_kind: DEFAULT_STORY_AGENT_SPEC,
    CHAT_AGENT_SPEC.agent_kind: CHAT_AGENT_SPEC,
}


def get_agent_spec(agent_kind: str | None) -> AgentSpec:
    return AGENT_SPECS.get(str(agent_kind or ""), DEFAULT_STORY_AGENT_SPEC)


def require_agent_spec(agent_kind: str | None) -> AgentSpec:
    kind = str(agent_kind or DEFAULT_STORY_AGENT_SPEC.agent_kind).strip()
    spec = AGENT_SPECS.get(kind)
    if spec is None:
        kinds = " / ".join(sorted(AGENT_SPECS))
        raise ValueError(f"agent_kind 必须是 {kinds}")
    return spec
