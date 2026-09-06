"""
服务端 Agent 模型流式响应拼接。

OpenAI 兼容接口的 stream chunk 会把正文和 tool_call 拆成多个增量片段。
这里只负责把这些片段拼回标准 assistant message，以及生成 trace 需要的
payload / view；不负责写 Agent 状态、SSE 推送和 OTel 打点。
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional


def assistant_message_to_dict(message: Any) -> Dict[str, Any]:
    """把非流式 SDK message 转成 Agent 内部统一结构。"""
    result: Dict[str, Any] = {
        "role": "assistant",
        "content": getattr(message, "content", None),
    }
    tool_calls = getattr(message, "tool_calls", None) or []
    if tool_calls:
        result["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": tool_call.type,
                "function": {
                    "name": tool_call.function.name,
                    "arguments": tool_call.function.arguments,
                },
            }
            for tool_call in tool_calls
        ]
    return result


class ModelStreamAccumulator:
    """累积模型流式 chunk，并在结束时恢复成普通 assistant 响应。"""

    def __init__(self, model: str):
        self.model = model
        self.content_parts: List[str] = []
        self.tool_call_parts: Dict[int, Dict[str, Any]] = {}
        self.response_id: Optional[str] = None
        self.response_model: Optional[str] = None
        self.finish_reason: Optional[str] = None
        self.usage: Any = None
        self.delta_count = 0
        self.content_chars = 0
        self.first_delta_preview = ""
        self.last_delta_preview = ""

    @property
    def output_model(self) -> str:
        return self.response_model or self.model

    @property
    def content(self) -> str:
        return "".join(self.content_parts)

    def consume_chunk(self, chunk: Any) -> Optional[str]:
        """消费一个 SDK chunk；没有正文时返回 None，tool_call 仍会在内部累积。"""
        self._capture_chunk_metadata(chunk)
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            return None

        choice = choices[0]
        self.finish_reason = getattr(choice, "finish_reason", None) or self.finish_reason
        delta = getattr(choice, "delta", None)
        if delta is None:
            return None

        content_text: Optional[str] = None
        content_delta = getattr(delta, "content", None)
        if content_delta:
            content_text = str(content_delta)
            self._append_content_delta(content_text)

        self._append_tool_call_deltas(getattr(delta, "tool_calls", None) or [])
        return content_text

    def assistant_message(self) -> Dict[str, Any]:
        """恢复成非流式接口同形状的 assistant message。"""
        result: Dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
        }
        tool_calls = self.tool_calls()
        if tool_calls:
            result["tool_calls"] = tool_calls
        return result

    def tool_calls(self) -> List[Dict[str, Any]]:
        tool_calls: List[Dict[str, Any]] = []
        for index in sorted(self.tool_call_parts):
            item = self.tool_call_parts[index]
            item["id"] = item["id"] or ("call_" + uuid.uuid4().hex)
            item["type"] = item["type"] or "function"
            tool_calls.append(item)
        return tool_calls

    def stream_summary(self, *, item_id: Optional[str], duration_ms: float) -> Dict[str, Any]:
        return {
            "item_id": item_id,
            "response_id": self.response_id,
            "model": self.output_model,
            "finish_reason": self.finish_reason,
            "delta_count": self.delta_count,
            "content_chars": self.content_chars,
            "duration_ms": duration_ms,
            "first_delta_preview": self.first_delta_preview,
            "last_delta_preview": self.last_delta_preview,
        }

    def response_payload(self, assistant_dict: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": self.response_id,
            "model": self.output_model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": self.finish_reason,
                    "message": assistant_dict,
                }
            ],
            "usage": self.usage_value(),
        }

    def response_view(self) -> Dict[str, Any]:
        return {
            "id": self.response_id,
            "model": self.output_model,
            "finish_reason": self.finish_reason,
            "content_chars": len(self.content),
            "content_preview": self.content[:1200],
            "tool_call_count": len(self.tool_call_parts),
            "usage": self.usage_value(),
        }

    def usage_value(self) -> Any:
        return self.usage.model_dump(mode="json") if hasattr(self.usage, "model_dump") else self.usage

    def _capture_chunk_metadata(self, chunk: Any) -> None:
        # 顶层 id/model/usage 可能只在部分 chunk 中出现；第一次看到就记录下来。
        if self.response_id is None:
            self.response_id = str(getattr(chunk, "id", "") or "") or None
        if self.response_model is None:
            self.response_model = str(getattr(chunk, "model", "") or "") or None
        if getattr(chunk, "usage", None) is not None:
            self.usage = getattr(chunk, "usage")

    def _append_content_delta(self, content_text: str) -> None:
        self.delta_count += 1
        self.content_chars += len(content_text)
        if not self.first_delta_preview:
            self.first_delta_preview = content_text[:120]
        self.last_delta_preview = content_text[-120:]
        self.content_parts.append(content_text)

    def _append_tool_call_deltas(self, tool_call_deltas: List[Any]) -> None:
        for tool_delta in tool_call_deltas:
            index = int(getattr(tool_delta, "index", 0) or 0)
            item = self.tool_call_parts.setdefault(
                index,
                {
                    "id": "",
                    "type": "function",
                    "function": {
                        "name": "",
                        "arguments": "",
                    },
                },
            )
            if getattr(tool_delta, "id", None):
                item["id"] = str(tool_delta.id)
            if getattr(tool_delta, "type", None):
                item["type"] = str(tool_delta.type)
            function = getattr(tool_delta, "function", None)
            if function is None:
                continue
            if getattr(function, "name", None):
                item["function"]["name"] += str(function.name)
            if getattr(function, "arguments", None):
                item["function"]["arguments"] += str(function.arguments)
