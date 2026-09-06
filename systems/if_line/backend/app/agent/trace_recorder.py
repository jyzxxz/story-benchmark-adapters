"""
服务端 Agent 观测记录器。

这里隔离本地 trace 表、Phoenix/OTel span 和事件清洗规则。Agent 主流程只调用
语义方法，避免在状态机里反复拼 trace 字段。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from app.agent.server_agent_protocol import (
    EEvent_agent_message_delta,
    EEvent_turn_completed,
    EEvent_turn_failed,
    EItem_agent_message,
    EItem_tool_call,
)
from app.agent.trace import build_model_view, new_span_id, record_agent_trace_event
from app.observability.agent_otel import agent_otel


ETrace_agent_message_stream_started = "backend.agent_message.stream.started"
ETrace_agent_message_stream_completed = "backend.agent_message.stream.completed"
ETrace_context_compaction_completed = "backend.context.compaction.completed"


class AgentTraceRecorder:
    """把 Agent 语义事件写入本地 trace 表和 OTel。"""

    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.turn_id: Optional[str] = None
        self.run_span_id: Optional[str] = None
        self._current_run_otel_span: Any = None
        self._context_compaction_span_id: Optional[str] = None
        self._context_compaction_otel_span: Any = None
        self._context_summary_span_id: Optional[str] = None
        self._context_summary_otel_span: Any = None
        self._model_otel_spans: Dict[str, Any] = {}
        self._tool_otel_spans: Dict[str, Any] = {}
        self._tool_spans: Dict[str, str] = {}
        self._last_model_request_messages: Optional[list[Dict[str, Any]]] = None
        self._pending_sse_events: list[Dict[str, Any]] = []

    def begin_turn(self, turn_id: str, *, mode: str, message_count: int, run_seq: int) -> str:
        self.turn_id = turn_id
        self.run_span_id = new_span_id()
        self._model_otel_spans.clear()
        self._tool_otel_spans.clear()
        self._tool_spans.clear()
        self._context_compaction_span_id = None
        self._context_compaction_otel_span = None
        self._context_summary_span_id = None
        self._context_summary_otel_span = None
        self._current_run_otel_span = agent_otel.start_run(
            thread_id=self.thread_id,
            run_id=turn_id,
            mode=mode,
            message_count=message_count,
        )
        # thread.started 发生在首个 turn span 创建前；延迟到这里原样写入，避免产生空 Trace。
        for event in self._pending_sse_events:
            agent_otel.add_event(
                self._current_run_otel_span,
                str(event.get("type") or "agent.event"),
                payload=event,
            )
        self._pending_sse_events.clear()
        self._record(
            "backend.turn.started",
            run_seq=run_seq,
            source="agent",
            visibility="ui",
            span_id=self.run_span_id,
            payload={
                "mode": mode,
                "message_count": message_count,
            },
            model_view={
                "mode": mode,
                "message_count": message_count,
            },
        )
        return turn_id

    def user_message(
        self,
        content: str,
        *,
        mode: str,
        run_seq: int,
    ) -> None:
        self._record(
            "backend.user.message",
            run_seq=run_seq,
            source="user",
            visibility="model",
            parent_span_id=self.run_span_id,
            payload={
                "content": content,
                "mode": mode,
            },
            model_view={
                "content_preview": content[:1200],
                "content_chars": len(content),
            },
        )

    def assistant_message(
        self,
        sampling_step: int,
        message: Dict[str, Any],
        *,
        model: str,
        run_seq: int,
    ) -> None:
        self._record(
            "backend.assistant.message",
            run_seq=run_seq,
            source="model",
            visibility="model",
            parent_span_id=self.run_span_id,
            model=model,
            payload={
                "sampling_step": sampling_step,
                "message": message,
            },
            model_view=build_model_view(message),
        )

    def model_request_started(
        self,
        *,
        model: str,
        allow_tools: bool,
        message_count: int,
        context_chars: int,
        token_estimate: int,
        tools_count: int,
        messages: list[Dict[str, Any]],
        run_seq: int,
    ) -> str:
        previous_messages = self._last_model_request_messages
        common_prefix_count = 0
        if previous_messages is not None:
            common_limit = min(len(previous_messages), len(messages))
            while (
                common_prefix_count < common_limit
                and previous_messages[common_prefix_count] == messages[common_prefix_count]
            ):
                common_prefix_count += 1

        # 普通对话从旧窗口末尾开始新增；上下文压缩或 system prompt 改写时，
        # 从首个变化点展示重建后的新窗口，不能只按列表长度取尾部。
        message_delta = messages[common_prefix_count:]
        # messages 内含嵌套 tool_calls，保存快照后再比较，避免后续原地修改影响 delta。
        self._last_model_request_messages = deepcopy(messages)

        model_span_id = new_span_id()
        self._model_otel_spans[model_span_id] = agent_otel.start_model_call(
            parent_span=self._current_run_otel_span,
            thread_id=self.thread_id,
            run_id=self.turn_id,
            model_span_id=model_span_id,
            model=model,
            allow_tools=allow_tools,
            message_count=message_count,
            context_chars=context_chars,
            token_estimate=token_estimate,
            tools_count=tools_count,
            message_delta=message_delta,
        )
        self._record(
            "backend.model.request.started",
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=model_span_id,
            parent_span_id=self.run_span_id,
            model=model,
            payload={
                "allow_tools": allow_tools,
                "message_count": message_count,
                "context_chars": context_chars,
                "token_estimate": token_estimate,
                "tools_count": tools_count,
            },
            model_view={
                "allow_tools": allow_tools,
                "message_count": message_count,
                "context_chars": context_chars,
                "token_estimate": token_estimate,
            },
        )
        return model_span_id

    def model_request_failed(self, model_span_id: str, exc: Exception) -> None:
        agent_otel.fail_model_call(self._model_otel_spans.pop(model_span_id, None), exc)

    def model_request_finished(
        self,
        model_span_id: str,
        *,
        model: str,
        response_payload: Dict[str, Any],
        response_view: Dict[str, Any],
        run_seq: int,
    ) -> None:
        self._record(
            "backend.model.request.finished",
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=model_span_id,
            parent_span_id=self.run_span_id,
            model=model,
            payload=response_payload,
            model_view=response_view,
        )
        agent_otel.finish_model_call(
            self._model_otel_spans.pop(model_span_id, None),
            response_payload,
            response_view,
        )

    def stream_started(
        self,
        model_span_id: str,
        *,
        item_id: str,
        response_id: Optional[str],
        model: str,
        run_seq: int,
    ) -> None:
        self._record(
            ETrace_agent_message_stream_started,
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=model_span_id,
            parent_span_id=self.run_span_id,
            model=model,
            payload={
                "item_id": item_id,
                "response_id": response_id,
                "model": model,
            },
            model_view={
                "item_id": item_id,
                "model": model,
            },
        )

    def stream_completed(
        self,
        model_span_id: str,
        *,
        summary: Dict[str, Any],
        model: str,
        run_seq: int,
    ) -> None:
        self._record(
            ETrace_agent_message_stream_completed,
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=model_span_id,
            parent_span_id=self.run_span_id,
            model=model,
            payload=summary,
            model_view=summary,
        )

    def context_compacted(self, *, payload: Dict[str, Any], run_seq: int) -> None:
        self._record(
            ETrace_context_compaction_completed,
            run_seq=run_seq,
            source="system",
            visibility="debug",
            span_id=self._context_compaction_span_id,
            parent_span_id=self.run_span_id,
            payload=payload,
            model_view=payload,
        )
        agent_otel.finish_context_compaction(self._context_compaction_otel_span, payload)
        self._context_compaction_span_id = None
        self._context_compaction_otel_span = None

    def context_compaction_started(self, *, payload: Dict[str, Any], run_seq: int) -> str:
        span_id = new_span_id()
        self._context_compaction_span_id = span_id
        self._context_compaction_otel_span = agent_otel.start_context_compaction(
            parent_span=self._current_run_otel_span,
            thread_id=self.thread_id,
            run_id=self.turn_id,
            compaction_span_id=span_id,
            payload=payload,
        )
        self._record(
            "backend.context.compaction.started",
            run_seq=run_seq,
            source="system",
            visibility="debug",
            span_id=span_id,
            parent_span_id=self.run_span_id,
            payload=payload,
            model_view=payload,
        )
        return span_id

    def context_compaction_failed(self, exc: Exception, *, run_seq: int) -> None:
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        self._record(
            "backend.context.compaction.failed",
            run_seq=run_seq,
            source="system",
            visibility="debug",
            span_id=self._context_compaction_span_id,
            parent_span_id=self.run_span_id,
            payload=payload,
            model_view=payload,
        )
        agent_otel.fail_context_compaction(self._context_compaction_otel_span, exc)
        self._context_compaction_span_id = None
        self._context_compaction_otel_span = None

    def context_summary_started(
        self,
        *,
        model: str,
        messages: list[Dict[str, Any]],
        token_estimate: int,
        run_seq: int,
    ) -> str:
        span_id = new_span_id()
        payload = {
            "model": model,
            "message_count": len(messages),
            "token_estimate": token_estimate,
        }
        self._context_summary_span_id = span_id
        self._context_summary_otel_span = agent_otel.start_context_summary_model_call(
            parent_span=self._context_compaction_otel_span or self._current_run_otel_span,
            thread_id=self.thread_id,
            run_id=self.turn_id,
            model_span_id=span_id,
            model=model,
            messages=messages,
            token_estimate=token_estimate,
        )
        self._record(
            "backend.context.compaction.summary.started",
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=span_id,
            parent_span_id=self._context_compaction_span_id or self.run_span_id,
            model=model,
            payload=payload,
            model_view=payload,
        )
        return span_id

    def context_summary_finished(
        self,
        *,
        payload: Dict[str, Any],
        run_seq: int,
    ) -> None:
        self._record(
            "backend.context.compaction.summary.finished",
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=self._context_summary_span_id,
            parent_span_id=self._context_compaction_span_id or self.run_span_id,
            model=str(payload.get("model") or ""),
            payload=payload,
            model_view=payload,
        )
        agent_otel.finish_model_call(self._context_summary_otel_span, payload, payload)
        self._context_summary_span_id = None
        self._context_summary_otel_span = None

    def context_summary_failed(self, exc: Exception, *, run_seq: int) -> None:
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        self._record(
            "backend.context.compaction.summary.failed",
            run_seq=run_seq,
            source="model",
            visibility="debug",
            span_id=self._context_summary_span_id,
            parent_span_id=self._context_compaction_span_id or self.run_span_id,
            payload=payload,
            model_view=payload,
        )
        agent_otel.fail_model_call(self._context_summary_otel_span, exc)
        self._context_summary_span_id = None
        self._context_summary_otel_span = None

    def tool_started(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        arguments: Dict[str, Any],
        raw_arguments: str,
        argument_error: Optional[str],
        run_seq: int,
    ) -> None:
        tool_span_id = new_span_id()
        self._tool_spans[tool_call_id] = tool_span_id
        self._record(
            "backend.tool.call.started",
            run_seq=run_seq,
            source="tool",
            visibility="model",
            span_id=tool_span_id,
            parent_span_id=self.run_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "arguments": arguments,
                "argument_error": argument_error,
                "raw_arguments": raw_arguments,
            },
            model_view={
                "name": tool_name,
                "arguments": build_model_view(arguments),
                "argument_error": argument_error,
            },
        )
        self._tool_otel_spans[tool_call_id] = agent_otel.start_tool_call(
            parent_span=self._current_run_otel_span,
            thread_id=self.thread_id,
            run_id=self.turn_id,
            tool_span_id=tool_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
        )

    def tool_failed(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        result: Dict[str, Any],
        run_seq: int,
    ) -> None:
        self._record(
            "backend.tool.call.failed",
            run_seq=run_seq,
            source="tool",
            visibility="model",
            span_id=self._tool_spans.get(tool_call_id),
            parent_span_id=self.run_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={"result": result},
            model_view=build_model_view(result),
        )
        agent_otel.fail_tool_call(self._tool_otel_spans.pop(tool_call_id, None), result)

    def tool_exception(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        arguments: Dict[str, Any],
        exc: Exception,
        run_seq: int,
    ) -> None:
        error = f"{type(exc).__name__}: {exc}"
        self._record(
            "backend.tool.call.failed",
            run_seq=run_seq,
            source="tool",
            visibility="model",
            span_id=self._tool_spans.get(tool_call_id),
            parent_span_id=self.run_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={
                "name": tool_name,
                "arguments": arguments,
                "error": error,
            },
            model_view={
                "name": tool_name,
                "error": error,
            },
        )
        agent_otel.fail_tool_call(self._tool_otel_spans.pop(tool_call_id, None), exc)

    def tool_finished(
        self,
        tool_call_id: str,
        tool_name: str,
        *,
        arguments: Dict[str, Any],
        result: Any,
        run_seq: int,
    ) -> None:
        self._record(
            "backend.tool.call.finished",
            run_seq=run_seq,
            source="tool",
            visibility="model",
            span_id=self._tool_spans.get(tool_call_id),
            parent_span_id=self.run_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={
                "name": tool_name,
                "arguments": arguments,
                "result": result,
            },
            model_view=build_model_view(result),
        )
        tool_otel_span = self._tool_otel_spans.pop(tool_call_id, None)
        if tool_otel_span is not None:
            agent_otel.finish_tool_call(tool_otel_span, result)

    def emitted_event(self, event: Dict[str, Any], *, run_seq: int) -> None:
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        item_id = str(event.get("item_id") or item.get("id") or "")
        item_type = str(item.get("type") or "")
        event_type = str(event.get("type") or "agent.event")
        # SSE 事件原样进入 Phoenix，便于按前端协议排查；只有高频文本 delta 不重复记录。
        if event_type == EEvent_agent_message_delta:
            return
        if self._current_run_otel_span is None:
            self._pending_sse_events.append(deepcopy(event))

        tool_call_id = item_id if item_type == EItem_tool_call else None
        source = "agent"
        if item_type == EItem_agent_message:
            source = "model"
        elif item_type == EItem_tool_call:
            source = "tool"
        self._record(
            event_type,
            run_seq=run_seq,
            source=source,
            visibility="ui",
            parent_span_id=self.run_span_id,
            tool_call_id=str(tool_call_id) if tool_call_id else None,
            tool_name=str(item.get("name") or "") or None,
            payload=event,
            model_view=build_model_view(event),
        )
        if event_type in {EEvent_turn_completed, EEvent_turn_failed}:
            agent_otel.end_run(self._current_run_otel_span, event)
            self._current_run_otel_span = None

    def _record(
        self,
        event_type: str,
        *,
        run_seq: int,
        source: str = "agent",
        visibility: str = "ui",
        payload: Optional[Dict[str, Any]] = None,
        model_view: Optional[Dict[str, Any]] = None,
        full_ref: Optional[Dict[str, Any]] = None,
        span_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        task_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> None:
        record_agent_trace_event(
            thread_id=self.thread_id,
            run_id=self.turn_id,
            run_seq=run_seq if self.turn_id else None,
            event_type=event_type,
            source=source,
            visibility=visibility,
            span_id=span_id,
            parent_span_id=parent_span_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            task_id=task_id,
            model=model,
            payload=payload,
            model_view=model_view,
            full_ref=full_ref,
        )
        target_span = None
        # 前端 SSE 事件统一挂在 agent.run，只有后端工具观测事件进入工具子 span。
        # 否则 item.started 会在工具 span 尚存时被吸入，而 item.completed 会在其结束后回落根 span。
        if tool_call_id and event_type.startswith("backend."):
            target_span = self._tool_otel_spans.get(tool_call_id)
        if target_span is None and span_id == self._context_summary_span_id:
            target_span = self._context_summary_otel_span
        if target_span is None and span_id == self._context_compaction_span_id:
            target_span = self._context_compaction_otel_span
        if target_span is None and span_id:
            target_span = self._model_otel_spans.get(span_id)
        if target_span is None:
            target_span = self._current_run_otel_span
        if event_type == "backend.user.message" and payload:
            agent_otel.set_input(
                self._current_run_otel_span,
                str(payload.get("content") or ""),
                mime_type="text/plain",
            )
        elif event_type == "backend.assistant.message" and payload:
            message = payload.get("message") if isinstance(payload, dict) else None
            if isinstance(message, dict) and message.get("content"):
                agent_otel.set_output(
                    self._current_run_otel_span,
                    str(message.get("content") or ""),
                    mime_type="text/plain",
                )
        agent_otel.add_event(
            target_span,
            event_type,
            payload=payload,
        )
