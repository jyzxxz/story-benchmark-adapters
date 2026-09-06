"""Cassette 录制与回放。

回放时只替换 LLM 响应(按录制顺序吐出 assistant 消息), 工具仍真实执行,
这样可以在不烧 token 的前提下反复打磨 checker 和断言。
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def _to_ns(value: Any) -> Any:
    """dict/list 递归转成 SimpleNamespace, 供非流式 OpenAI 响应结构使用。"""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_ns(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_ns(v) for v in value]
    return value


class ReplayCompletions:
    def __init__(self, assistant_messages: list[dict[str, Any]]) -> None:
        self._queue = list(assistant_messages)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._queue:
            raise RuntimeError("replay 队列耗尽: 模型调用次数多于录制条数")
        recorded = self._queue.pop(0)
        return SimpleNamespace(choices=[SimpleNamespace(message=_to_ns(recorded))])


class ReplayClient:
    def __init__(self, assistant_messages: list[dict[str, Any]]) -> None:
        self.chat = SimpleNamespace(completions=ReplayCompletions(assistant_messages))


def assistant_messages_from_result(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 agent.messages 抽取可回放的 assistant 消息(只保留模型字段)。"""
    out: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        out.append({
            "content": message.get("content"),
            "tool_calls": message.get("tool_calls") or None,
        })
    return out


def save_cassette(
    path: Path,
    task_id: str,
    instructions: str,
    model: str,
    assistant_messages: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "task_id": task_id,
        "instructions": instructions,
        "model": model,
        "assistant_messages": assistant_messages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cassette(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
