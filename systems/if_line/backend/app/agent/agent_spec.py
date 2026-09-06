"""
Agent 规格定义。

这个文件只描述“一个 Agent 长什么样”：提示词、模式和工具集合。
具体的 stream、SSE、持久化和 trace 仍留在 server_agent 运行时里。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


class AgentToolHandle(Protocol):
    """工具调用层可使用的 Agent 句柄。

    这里故意只暴露工具层需要的最小能力。工具如果需要前端协作，就通过这个
    句柄登记请求、推送 item.updated 事件，再由前端结果接口回填结果。
    """

    thread_id: str
    owner_id: int | None
    current_run_id: str | None

    def request_frontend_tool_input(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        request_event: dict[str, Any],
    ) -> dict[str, Any]:
        ...

    def read_frontend_tool_result(self, tool_call_id: str) -> dict[str, Any]:
        ...

    async def wait_frontend_tool_result(self, tool_call_id: str, timeout_seconds: float = 5.0) -> dict[str, Any]:
        ...


ToolExecutor = Callable[
    [Any, str, dict[str, Any], str, AgentToolHandle | None],
    Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class AgentSpec:
    agent_kind: str
    system_prompt: str
    mode_prompts: dict[str, str]
    default_mode: str
    tool_schemas: list[dict[str, Any]]
    execute_toolcall: ToolExecutor

    def normalize_mode(self, mode: str | None) -> str:
        mode_text = str(mode or "").strip()
        if mode_text in self.mode_prompts:
            return mode_text
        return self.default_mode

    def build_system_prompt(self, mode: str) -> str:
        return self.system_prompt + "\n\n" + self.mode_prompts[mode]

    def assert_mode(self, mode: str) -> None:
        if mode not in self.mode_prompts:
            modes = " / ".join(sorted(self.mode_prompts))
            raise ValueError(f"mode 必须是 {modes}")
