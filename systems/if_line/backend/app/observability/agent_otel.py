"""Agent 语义到 OpenTelemetry span 的适配层。"""
from __future__ import annotations

import threading
from typing import Any

from app.observability.otel import (
    json_preview,
    json_value,
    otlp_timeout_seconds,
    otlp_trace_endpoint,
    otlp_trust_proxy_env,
    safe_attribute_value,
    safe_attributes,
)
from app.utils import logging as xlog


_MIME_JSON = "application/json"
_MIME_TEXT = "text/plain"


def _flatten_event_attributes(value: Any) -> dict[str, Any]:
    """把事件 JSON 展开成 Phoenix 可直接查看的点路径属性。"""
    attributes: dict[str, Any] = {}

    def append(current: Any, path: str) -> None:
        if isinstance(current, dict):
            if not current and path:
                attributes[path] = "{}"
                return
            for key, child in current.items():
                child_path = f"{path}.{key}" if path else str(key)
                append(child, child_path)
            return

        if isinstance(current, (list, tuple)):
            # OTel 只接受同类型基础值数组；对象数组保持为当前字段的 JSON，避免按下标制造大量属性。
            if current and all(type(item) is type(current[0]) for item in current) and all(
                isinstance(item, (str, bool, int, float)) for item in current
            ):
                attributes[path] = list(current)
            else:
                attributes[path] = json_value(current)
            return

        if current is None:
            attributes[path] = "null"
        elif isinstance(current, (str, bool, int, float)):
            attributes[path] = current
        else:
            attributes[path] = str(current)

    if value is not None:
        append(value, "")
    return attributes


class AgentOtelTracer:
    """把一次 Agent 运行映射成 Phoenix / OTel 可读的 span 树。"""

    def __init__(self) -> None:
        self._tracer_name = "if-line.server-agent"
        self._lock = threading.RLock()
        self._initialized = False
        self._tracer = None
        self._provider = None

    def setup(self) -> None:
        with self._lock:
            if self._initialized:
                return
            self._initialized = True
            self._setup_agent_tracer()

    def shutdown(self) -> None:
        with self._lock:
            provider = self._provider
            self._provider = None
            self._tracer = None
            self._initialized = False
        if provider is not None:
            try:
                provider.shutdown()
            except Exception:
                pass

    def start_run(
        self,
        *,
        thread_id: str,
        run_id: str,
        mode: str,
        message_count: int,
    ) -> Any:
        return self._start_span(
            "agent.run",
            None,
            {
                "ifline.thread_id": thread_id,
                "ifline.run_id": run_id,
                "ifline.agent.mode": mode,
                "ifline.message_count": message_count,
                "session.id": thread_id,
                "openinference.span.kind": "AGENT",
                "input.mime_type": _MIME_TEXT,
            },
        )

    def start_model_call(
        self,
        *,
        parent_span: Any,
        thread_id: str,
        run_id: str | None,
        model_span_id: str,
        model: str,
        allow_tools: bool,
        message_count: int,
        context_chars: int,
        token_estimate: int,
        tools_count: int,
        message_delta: list[dict[str, Any]],
    ) -> Any:
        return self._start_span(
            "llm.chat",
            parent_span,
            {
                "ifline.thread_id": thread_id,
                "ifline.run_id": run_id or "",
                "ifline.span_id": model_span_id,
                "llm.model_name": model,
                "llm.provider": _provider_from_model(model),
                "llm.system": "openai-compatible",
                "llm.request.type": "chat",
                "ifline.allow_tools": allow_tools,
                "ifline.message_count": message_count,
                "ifline.context_chars": context_chars,
                "ifline.token_estimate": token_estimate,
                "ifline.tools_count": tools_count,
                "session.id": thread_id,
                "openinference.span.kind": "LLM",
                "input.mime_type": _MIME_JSON,
                "input.value": json_value({"message_delta": message_delta}),
            },
        )

    def finish_model_call(self, span: Any, payload: dict[str, Any], view: dict[str, Any]) -> None:
        if not span:
            return
        usage = view.get("usage") if isinstance(view, dict) else None
        if isinstance(usage, dict):
            self._set_token_usage_attributes(span, usage)
        self._set_attribute(span, "llm.response.model", view.get("model"))
        self._set_attribute(span, "llm.response.finish_reason", view.get("finish_reason"))
        self._set_attribute(span, "ifline.tool_call_count", view.get("tool_call_count"))
        self._set_attribute(span, "output.mime_type", _MIME_JSON)
        self._set_attribute(span, "output.value", json_preview(payload))
        self.add_event(span, "backend.model.response", payload=payload)
        span.end()

    def fail_model_call(self, span: Any, exc: Exception) -> None:
        self._record_exception_and_end(span, exc)

    def start_context_compaction(
        self,
        *,
        parent_span: Any,
        thread_id: str,
        run_id: str | None,
        compaction_span_id: str,
        payload: dict[str, Any],
    ) -> Any:
        span = self._start_span(
            "context.compaction",
            parent_span,
            {
                "ifline.thread_id": thread_id,
                "ifline.run_id": run_id or "",
                "ifline.span_id": compaction_span_id,
                "session.id": thread_id,
                "openinference.span.kind": "CHAIN",
                "input.mime_type": _MIME_JSON,
                "input.value": json_preview(payload),
                **{f"ifline.{key}": value for key, value in payload.items()},
            },
        )
        return span

    def finish_context_compaction(self, span: Any, payload: dict[str, Any]) -> None:
        if not span:
            return
        for key, value in payload.items():
            self._set_attribute(span, f"ifline.{key}", value)
        self._set_attribute(span, "output.mime_type", _MIME_JSON)
        self._set_attribute(span, "output.value", json_preview(payload))
        span.end()

    def fail_context_compaction(self, span: Any, exc: Exception) -> None:
        self._record_exception_and_end(span, exc)

    def start_context_summary_model_call(
        self,
        *,
        parent_span: Any,
        thread_id: str,
        run_id: str | None,
        model_span_id: str,
        model: str,
        messages: list[dict[str, Any]],
        token_estimate: int,
    ) -> Any:
        return self._start_span(
            "llm.chat.summary",
            parent_span,
            {
                "ifline.thread_id": thread_id,
                "ifline.run_id": run_id or "",
                "ifline.span_id": model_span_id,
                "ifline.purpose": "context_compaction.summary",
                "llm.model_name": model,
                "llm.provider": _provider_from_model(model),
                "llm.system": "openai-compatible",
                "llm.request.type": "chat",
                "ifline.message_count": len(messages),
                "ifline.token_estimate": token_estimate,
                "session.id": thread_id,
                "openinference.span.kind": "LLM",
                "input.mime_type": _MIME_JSON,
                "input.value": json_value({"message_delta": messages}),
            },
        )

    def start_tool_call(
        self,
        *,
        parent_span: Any,
        thread_id: str,
        run_id: str | None,
        tool_span_id: str,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        span = self._start_span(
            f"tool.{tool_name}",
            parent_span,
            {
                "ifline.thread_id": thread_id,
                "ifline.run_id": run_id or "",
                "ifline.span_id": tool_span_id,
                "ifline.tool_call_id": tool_call_id,
                "ifline.tool_name": tool_name,
                "session.id": thread_id,
                "openinference.span.kind": "TOOL",
                "input.mime_type": _MIME_JSON,
                "input.value": json_preview(arguments),
            },
        )
        self.add_event(span, "backend.tool.arguments", payload=arguments)
        return span

    def finish_tool_call(self, span: Any, result: Any) -> None:
        if not span:
            return
        ok = result.get("ok") if isinstance(result, dict) else None
        if ok is not None:
            self._set_attribute(span, "ifline.tool.ok", bool(ok))
            if ok is False:
                self._set_error_status(span, _tool_error_message(result))
        output = json_value(result)
        self._set_tool_output_attributes(span, output)
        span.add_event("backend.tool.result")
        span.end()

    def fail_tool_call(self, span: Any, exc_or_result: Any) -> None:
        if isinstance(exc_or_result, Exception):
            self._record_exception_and_end(span, exc_or_result)
            return
        if not span:
            return
        self._set_attribute(span, "ifline.tool.ok", False)
        self._set_error_status(span, _tool_error_message(exc_or_result))
        output = json_value(exc_or_result)
        self._set_tool_output_attributes(span, output)
        span.add_event("backend.tool.failed")
        span.end()

    def add_event(
        self,
        span: Any,
        event_type: str,
        *,
        payload: Any = None,
    ) -> None:
        if not span:
            return
        attributes = _flatten_event_attributes(payload)
        if payload is not None:
            attributes["ifline.payload_json"] = json_value(payload)
        span.add_event(event_type, attributes=attributes)

    def end_run(self, span: Any, stop_event: dict[str, Any]) -> None:
        if not span:
            return
        reason = stop_event.get("reason") or ""
        self._set_attribute(span, "ifline.stop_reason", reason)
        if stop_event.get("error"):
            self._set_attribute(span, "ifline.error", str(stop_event.get("error")))
            self._set_attribute(span, "output.mime_type", _MIME_JSON)
            self._set_attribute(span, "output.value", json_preview(stop_event))
        span.end()

    def set_input(self, span: Any, value: Any, mime_type: str = _MIME_JSON) -> None:
        if not span:
            return
        self._set_attribute(span, "input.mime_type", mime_type)
        self._set_attribute(span, "input.value", value if isinstance(value, str) else json_preview(value))

    def set_output(self, span: Any, value: Any, mime_type: str = _MIME_JSON) -> None:
        if not span:
            return
        self._set_attribute(span, "output.mime_type", mime_type)
        self._set_attribute(span, "output.value", value if isinstance(value, str) else json_preview(value))

    def _start_span(self, name: str, parent_span: Any, attributes: dict[str, Any]) -> Any:
        tracer = self._tracer
        if tracer is None:
            return None
        try:
            from opentelemetry import trace

            if parent_span is not None:
                context = trace.set_span_in_context(parent_span)
                return tracer.start_span(name, context=context, attributes=safe_attributes(attributes))
            context = trace.set_span_in_context(trace.INVALID_SPAN)
            return tracer.start_span(name, context=context, attributes=safe_attributes(attributes))
        except Exception as exc:
            xlog.warn("server-agent", "[server-agent-otel] 创建 span 失败 name=%s error=%s", name, exc)
            return None

    def _setup_agent_tracer(self) -> None:
        endpoint = otlp_trace_endpoint()
        if not endpoint:
            return

        try:
            import os
            import requests
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception as exc:
            xlog.warn("server-agent", "[server-agent-otel] 依赖未安装，跳过 Agent trace 导出: %s", exc)
            return

        resource = Resource.create(
            {
                "service.name": os.getenv("AGENT_TRACE_SERVICE_NAME", "if-line-agent"),
                "service.namespace": "if-line",
                "deployment.environment": os.getenv("APP_ENV", "development"),
            }
        )
        provider = TracerProvider(resource=resource)
        session = requests.Session()
        session.trust_env = otlp_trust_proxy_env()
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=endpoint,
                    session=session,
                    timeout=otlp_timeout_seconds(),
                )
            )
        )
        self._provider = provider
        self._tracer = provider.get_tracer(self._tracer_name)
        xlog.info("server-agent", "[server-agent-otel] 已启用 Agent trace，统一导出到 Collector endpoint=%s", endpoint)

    def _record_exception_and_end(self, span: Any, exc: Exception) -> None:
        if not span:
            return
        try:
            span.record_exception(exc)
            self._set_attribute(span, "ifline.error", f"{type(exc).__name__}: {exc}")
            self._set_error_status(span, f"{type(exc).__name__}: {exc}")
        finally:
            span.end()

    def _set_error_status(self, span: Any, message: str) -> None:
        if span is None:
            return
        try:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, message))
        except Exception:
            return

    def _set_attribute(self, span: Any, key: str, value: Any) -> None:
        if span is None or value is None:
            return
        try:
            span.set_attribute(key, safe_attribute_value(value))
        except Exception:
            return

    def _set_token_usage_attributes(self, span: Any, usage: dict[str, Any]) -> None:
        """按 OpenInference 字段写 token，用于 Phoenix 聚合 token 和成本。"""
        self._set_attribute(span, "llm.token_count.prompt", usage.get("prompt_tokens"))
        self._set_attribute(span, "llm.token_count.completion", usage.get("completion_tokens"))
        self._set_attribute(span, "llm.token_count.total", usage.get("total_tokens"))

        prompt_details = usage.get("prompt_tokens_details")
        if isinstance(prompt_details, dict):
            self._set_attribute(
                span,
                "llm.token_count.prompt_details.cache_read",
                prompt_details.get("cached_tokens"),
            )
        completion_details = usage.get("completion_tokens_details")
        if isinstance(completion_details, dict):
            self._set_attribute(
                span,
                "llm.token_count.completion_details.reasoning",
                completion_details.get("reasoning_tokens"),
            )
            self._set_attribute(
                span,
                "llm.token_count.completion_details.audio",
                completion_details.get("audio_tokens"),
            )

        self._set_attribute(span, "llm.token_count.prompt_details.cache_read", usage.get("prompt_cache_hit_tokens"))
        self._set_attribute(span, "ifline.prompt_cache_miss_tokens", usage.get("prompt_cache_miss_tokens"))

    def _set_tool_output_attributes(self, span: Any, output: str) -> None:
        """Phoenix 保存完整工具输出，并附带便于判断上下文开销的体积估算。"""
        self._set_attribute(span, "output.mime_type", _MIME_JSON)
        self._set_attribute(span, "output.value", output)
        self._set_attribute(span, "ifline.tool.output_chars", len(output))
        self._set_attribute(span, "ifline.tool.output_bytes", len(output.encode("utf-8")))
        self._set_attribute(span, "ifline.tool.output_tokens_estimate", _estimate_tool_output_tokens(output))


agent_otel = AgentOtelTracer()


def _provider_from_model(model: str) -> str:
    lowered = str(model or "").lower()
    if "deepseek" in lowered:
        return "deepseek"
    if "gpt" in lowered or "openai" in lowered:
        return "openai"
    return "openai-compatible"


def _tool_error_message(result: Any) -> str:
    if not isinstance(result, dict):
        return "tool failed"
    message = result.get("error") or result.get("detail") or result.get("message")
    return str(message or "tool failed")


def _estimate_tool_output_tokens(text: str) -> int:
    """按字符类型保守估算工具结果进入下一次模型请求后的 token 数。"""
    score = 0.0
    for char in text:
        if char.isspace():
            continue
        code = ord(char)
        if 0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF:
            score += 1.0
        elif code < 128:
            score += 0.3
        else:
            score += 0.5
    return max(1, int(score + 0.999))


def setup_agent_otel() -> None:
    agent_otel.setup()


def shutdown_agent_otel() -> None:
    agent_otel.shutdown()
