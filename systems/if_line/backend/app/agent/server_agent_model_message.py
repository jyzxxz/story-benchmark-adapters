"""
服务端 Agent 的模型消息字段清洗。

内部 messages 可以带 if_line 自己的恢复和压缩字段；发给 LLM API 前只能保留
Chat Completions 接受的字段。
"""
from __future__ import annotations

from typing import Any


def filter_model_message_fields(message: dict[str, Any]) -> dict[str, Any]:
    allowed = {"role", "content", "tool_call_id", "name", "tool_calls"}
    return {key: value for key, value in dict(message or {}).items() if key in allowed}
