"""
服务端最小 Agent 运行时。

这里按“可 resume 的协程 generator”组织:
- ServerAgent 持有 messages 和一个 async generator。
- 用户输入时调用 resume，把输入送进 generator。
- generator 产出 assistant / tool_call / stop 事件。
- emit 目前只写日志并缓存到内存，后续可以在 manager 层接 SSE。

创建入口:
- Python 内部: await server_agent_manager.create_agent(agent_kind="server_agent")
- HTTP 接口: POST /api/server-agent/threads，body 里传 agent_kind。

内部驱动与消费:
- 同步等待: await agent.resume(content="...")
- 后台驱动: server_agent_manager.submit_resume(agent, run_id=..., content="...")
- 前端工具结果: server_agent_manager.submit_frontend_tool_result(...)
- 事件消费: subscriber_id, queue = agent.subscribe_events(after_seq=0)，随后 await queue.get()
"""
import asyncio
import json
import os
import time
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, List, Optional

from app.agent.agent_spec import AgentSpec
from app.agent.server_agent_message_event import ServerAgentEventLog, ServerAgentMessageWindow
from app.agent.server_agent_protocol import (
    EEvent_agent_message_delta,
    EEvent_error,
    EEvent_item_completed,
    EEvent_item_started,
    EEvent_item_updated,
    EEvent_thread_started,
    EEvent_turn_completed,
    EEvent_turn_failed,
    EEvent_turn_started,
    EItem_agent_message,
    EItem_context_compaction,
    EItem_error,
    EItem_tool_call,
    EMessagePhase_commentary,
    EMessagePhase_final_answer,
    EItemStatus_completed,
    EItemStatus_failed,
    EItemStatus_in_progress,
    EItemStatus_waiting_input,
    EStatus_created,
    EStatus_running,
    EStatus_waiting_user,
    EStopReason_assistant_answer,
    EStopReason_error,
    EStopReason_interrupted,
    EStopReason_waiting_user,
)
from app.agent.context_compaction import (
    build_summary_request_messages,
    compaction_settings_from_env,
    estimate_messages_tokens,
    prepare_compaction,
    run_prepared_compaction,
)
from app.agent.server_agent_model_message import filter_model_message_fields
from app.agent.server_agent_stream import ModelStreamAccumulator, assistant_message_to_dict
from app.agent.server_agent_store import (
    begin_turn_record,
    create_thread_record,
    delete_thread_record,
    finish_turn_record,
    load_thread_record,
    persist_event,
    persist_message,
    persist_thread_snapshot,
)
from app.agent.agent_registry import get_agent_spec, require_agent_spec
from app.agent.story_agent_spec import DEFAULT_STORY_AGENT_SPEC
from app.agent.trace_recorder import AgentTraceRecorder
from app.database import SessionLocal
from app.services.api_key_pool import PooledAsyncOpenAI
from app.services.text_llm_config import DEFAULT_TEXT_LLM_BASE_URL, DEFAULT_TEXT_LLM_MODEL
from app.services.text_llm_config import resolve_request_model
from app.utils import logging as xlog


class ServerAgent:
    """驻留内存的最小 Agent 对象。"""

    def __init__(
        self,
        thread_id: Optional[str] = None,
        owner_id: int | None = None,
        client: Optional[Any] = None,
        model: Optional[str] = None,
        mode: str | None = None,
        spec: AgentSpec | None = None,
    ):
        self.spec = spec or DEFAULT_STORY_AGENT_SPEC
        self.thread_id = thread_id or uuid.uuid4().hex
        self.owner_id = owner_id
        self.mode = self.spec.normalize_mode(mode)

        self.client = client or PooledAsyncOpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            pool_env="OPENAI_API_KEYS",
            allow_byok=True,
            base_url=os.getenv("OPENAI_BASE_URL", DEFAULT_TEXT_LLM_BASE_URL),
        )
        self.model = model or os.getenv("LLM_MODEL", DEFAULT_TEXT_LLM_MODEL)

        self.status = EStatus_created
        self.stop_reason: Optional[str] = None

        # turn_id: 从一次用户输入开始，到模型完成回复或等待外部结果为止。
        self.current_run_id: Optional[str] = None
        self.last_run_id: Optional[str] = None
        self._run_seq = 0
        self.trace = AgentTraceRecorder(self.thread_id)

        self.created_at = datetime.utcnow()
        self.updated_at = self.created_at

        self.message_window = ServerAgentMessageWindow.initial(
            {"role": "system", "content": self._build_system_prompt()}
        )
        self.event_log = ServerAgentEventLog(self.thread_id)

        self._lock = asyncio.Lock()
        self._loop: Optional[AsyncGenerator[Dict[str, Any], Optional[Dict[str, Any]]]] = None
        
        # 当前正在运行的toolcall状态，用于中断操作
        self._active_tool_call: Optional[Dict[str, Any]] = None
        self._frontend_tool_requests: Dict[str, Dict[str, Any]] = {}
        self._frontend_tool_results: Dict[str, Dict[str, Any]] = {}

    @property
    def messages(self) -> List[Dict[str, Any]]:
        return self.message_window.items

    @property
    def events(self) -> List[Dict[str, Any]]:
        return self.event_log.items

    @classmethod
    def restore(cls, record: Dict[str, Any]) -> "ServerAgent":
        """从数据库快照恢复内存对象。"""
        spec = get_agent_spec(record.get("agent_kind"))
        agent = cls(
            thread_id=record["thread_id"],
            owner_id=record.get("owner_id"),
            model=record.get("model"),
            mode=record["mode"],
            spec=spec,
        )
        agent.status = record["status"]
        agent.stop_reason = record["stop_reason"]
        agent.current_run_id = record["current_turn_id"]
        agent.last_run_id = record["last_turn_id"]
        agent.created_at = record["created_at"]
        agent.updated_at = record["updated_at"]
        # active_messages：当前模型活跃窗口，不是全量 thread 历史。
        # active_message_db_indexes：当前窗口每条 message 对应的数据库编号。
        # next_persisted_message_index：下一条新落库 message 应该使用的数据库编号。
        agent.message_window = ServerAgentMessageWindow.restored(
            active_messages=record["active_messages"],
            active_message_db_indexes=record.get("active_message_db_indexes"),
            next_persisted_message_index=record.get("next_persisted_message_index"),
        )
        agent.event_log = ServerAgentEventLog(
            agent.thread_id,
            events=record["events"],
            next_event_seq=record.get("next_event_seq"),
        )
        if agent.current_run_id:
            agent._run_seq = max(
                (
                    int(event.get("turn_seq") or 0)
                    for event in agent.events
                    if event.get("turn_id") == agent.current_run_id
                ),
                default=0,
            )
        return agent

    async def start(self) -> Dict[str, Any]:
        """启动 generator，并停在第一次等待用户输入的位置。"""
        if self._loop is not None:
            return self.events[-1] if self.events else self.snapshot()

        self._loop = self._agent_loop()
        event = await self._loop.asend(None)
        return event

    async def resume(
        self,
        content: str,
        prompt: Optional[str] = None,
        run_id: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """把用户输入送进 generator，持续拉取事件直到当前 turn 结束。"""
        if self._lock.locked():
            raise ValueError("当前 Agent 已有进行中的 turn，请等待完成后再继续。")

        async with self._lock:
            if not content.strip() and not (prompt or "").strip():
                raise ValueError("输入内容不能为空")
            if self.current_run_id and run_id != self.current_run_id:
                raise ValueError("当前 Agent 已有进行中的 turn，请等待完成后再继续。")
            if self._loop is None:
                await self.start()

            payload = {
                "content": content,
                "prompt": prompt or "",
                "run_id": run_id,
                "mode": mode,
            }

            step_events: List[Dict[str, Any]] = []
            assert self._loop is not None
            event = await self._loop.asend(payload)
            step_events.append(event)

            while event.get("type") not in {EEvent_turn_completed, EEvent_turn_failed}:
                event = await self._loop.asend(None)
                step_events.append(event)

            return self._build_result(step_events)

    async def _agent_loop(self) -> AsyncGenerator[Dict[str, Any], Optional[Dict[str, Any]]]:
        """主循环: EEvent_stop 等输入，其他事件向外 emit。"""
        payload = None

        xlog.info( self.thread_id, "[server-agent] start new agent")
        while True:
            self.status = EStatus_waiting_user
            self.stop_reason = EStopReason_waiting_user
            self.updated_at = datetime.utcnow()
            if not payload:
                if self.events:
                    payload = yield dict(self.events[-1])
                else:
                    payload = yield self._emit_event( { "type": EEvent_thread_started, })
            if not payload:
                continue

            self._set_mode_for_run(payload.get("mode"))
            self._begin_run(str(payload.get("run_id") or ""))
            yield self._emit_event(
                {
                    "type": EEvent_turn_started,
                    "turn_id": self.current_run_id,
                    "mode": self.mode,
                }
            )

            # 配置
            content = str(payload["content"])
            prompt = str(payload.get("prompt") or "")
            start = time.monotonic()
            tool_trace: List[Dict[str, Any]] = []
            sampling_steps = 0

            self.status = EStatus_running
            self.stop_reason = None
            self.updated_at = datetime.utcnow()
            user_message = self._build_user_message(content, prompt)
            begin_turn_record(
                thread_id=self.thread_id,
                turn_id=str(self.current_run_id or ""),
                mode=self.mode,
                content=content,
                prompt=prompt,
                message_start_index=self.message_window.next_index,
                event_start_seq=self.event_log.next_seq,
                created_at=self.updated_at,
            )
            self._append_message({"role": "user", "content": user_message})
            self.trace.user_message(
                user_message,
                mode=self.mode,
                run_seq=self._run_seq,
            )

            while True:
                # 请求模型；文本内容会按 delta 流式推送，工具调用在流结束后执行。
                assistant_dict, assistant_item_id = await self._request_assistant_message(
                    allow_tools=True
                )
                sampling_steps += 1
                tool_calls = assistant_dict.get("tool_calls") or []
                self._finish_agent_message(assistant_dict, assistant_item_id)
                self._append_message(assistant_dict)
                self.trace.assistant_message(
                    sampling_steps,
                    assistant_dict,
                    model=resolve_request_model(self.model),
                    run_seq=self._run_seq,
                )

                xlog.info(self.thread_id, "[server-agent] agent reply:%s", assistant_dict)
                # assistant 回复，停止，等待用户输入
                if not tool_calls:
                    elapsed_ms = int((time.monotonic() - start) * 1000)
                    xlog.info(
                        self.thread_id,
                        "[server-agent] stop run_id=%s seq=%d reason=%s elapsed_ms=%d tool_calls=%d",
                        self.current_run_id,
                        self._run_seq,
                        EStopReason_assistant_answer,
                        elapsed_ms,
                        len(tool_trace),
                    )
                    self.status = EStatus_waiting_user
                    self.stop_reason = EStopReason_assistant_answer
                    self.updated_at = datetime.utcnow()
                    payload = yield self._emit_event(
                        {
                            "type": EEvent_turn_completed,
                            "turn_id": self.current_run_id,
                            "stop_reason": EStopReason_assistant_answer,
                            "sampling_steps": sampling_steps,
                            "tool_call_count": len(tool_trace),
                            "usage": None,
                        }
                    )
                    break

                # toolcall输入，执行工具并回馈，加入到message，再重新发送
                for tool_call in tool_calls:
                    tool_call_id = str(tool_call.get("id") or uuid.uuid4().hex)
                    function = tool_call.get("function") or {}
                    tool_name = str(function.get("name") or "")
                    raw_arguments = str(function.get("arguments") or "")
                    arguments, argument_error = self._load_tool_arguments(raw_arguments)
                    self.trace.tool_started(
                        tool_call_id,
                        tool_name,
                        arguments=arguments,
                        raw_arguments=raw_arguments,
                        argument_error=argument_error,
                        run_seq=self._run_seq,
                    )
                    self._active_tool_call = {
                        "turn_id": self.current_run_id,
                        "tool_call_id": tool_call_id,
                        "tool_name": tool_name,
                        "arguments": arguments,
                    }
                    yield self._emit_item_started(
                        {
                            "id": tool_call_id,
                            "type": EItem_tool_call,
                            "name": tool_name,
                            "arguments": arguments,
                            "status": EItemStatus_in_progress,
                        }
                    )
                    xlog.info(
                        self.thread_id,
                        "[server-agent] tool:%s begin run_id=%s tool_call_id=%s seq_current=%d "
                        "arguments=%s argument_error=%s raw_arguments=%s",
                        tool_name,
                        self.current_run_id,
                        tool_call_id,
                        self._run_seq,
                        self._json_log(arguments),
                        argument_error,
                        raw_arguments,
                    )

                    # 执行工具调用
                    tool_exception: Exception | None = None
                    progress_events: List[Dict[str, Any]] = []
                    if argument_error:
                        result = {
                            "ok": False,
                            "error": argument_error,
                            "raw_arguments_preview": raw_arguments[:2000],
                        }
                    else:
                        # 一个 toolcall 独占一个 Session 和事务，失败不能污染同一 turn 的后续工具。
                        tool_db = SessionLocal()
                        try:
                            tool_result = await self.spec.execute_toolcall(
                                tool_db,
                                tool_name,
                                arguments,
                                tool_call_id,
                                self,
                            )
                            result = tool_result.get("result")
                            progress_events = list(tool_result.get("events") or [])
                            if isinstance(result, dict) and result.get("ok") is False:
                                tool_db.rollback()
                            else:
                                tool_db.commit()
                        except Exception as exc:
                            tool_db.rollback()
                            result = {
                                "ok": False,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                            tool_exception = exc
                        finally:
                            tool_db.close()

                    # 工具事务完成后再推送进度，避免前端先看到尚未提交的数据。
                    for progress_event in progress_events:
                        emitted_progress = dict(progress_event)
                        stage = emitted_progress.pop("type", "")
                        normalized_stage = self._normalize_progress_stage(stage)
                        yield self._emit_item_updated(
                            {
                                "id": tool_call_id,
                                "type": EItem_tool_call,
                                "name": tool_name,
                                "arguments": arguments,
                                "progress": {"stage": normalized_stage, **emitted_progress},
                                "status": EItemStatus_in_progress,
                            }
                        )


                    xlog.info(
                        self.thread_id,
                        "[server-agent] finish tool:%s with result: %s",
                        tool_name,
                        result
                    )
                    if tool_exception is not None:
                        self.trace.tool_exception(
                            tool_call_id,
                            tool_name,
                            arguments=arguments,
                            exc=tool_exception,
                            run_seq=self._run_seq,
                        )
                    elif isinstance(result, dict) and result.get("ok") is False:
                        self.trace.tool_failed(
                            tool_call_id,
                            tool_name,
                            result=result,
                            run_seq=self._run_seq,
                        )
                    else:
                        self.trace.tool_finished(
                            tool_call_id,
                            tool_name,
                            arguments=arguments,
                            result=result,
                            run_seq=self._run_seq,
                        )
                    trace_item = {
                        "name": tool_name,
                        "arguments": arguments,
                        "result": result,
                    }
                    tool_trace.append(trace_item)

                    self._append_message(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "name": tool_name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    self._active_tool_call = None
                    item_status = EItemStatus_completed
                    if isinstance(result, dict) and result.get("ok") is False:
                        item_status = EItemStatus_failed
                    yield self._emit_item_completed(
                        {
                            "id": tool_call_id,
                            "type": EItem_tool_call,
                            "name": tool_name,
                            "arguments": arguments,
                            "result": result,
                            "status": item_status,
                        }
                    )

    def request_frontend_tool_input(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        request_event: Dict[str, Any],
    ) -> Dict[str, Any]:
        """供工具调用层请求前端输入，并由 Agent 统一完成事件推送。"""
        normalized_event = dict(request_event or {})
        normalized_event.update(
            {
                "thread_id": self.thread_id,
                "turn_id": self.current_run_id,
                "tool_call_id": tool_call_id,
            }
        )
        self._register_frontend_tool_request(tool_call_id, normalized_event)
        self._emit_frontend_tool_waiting_input(
            tool_call_id,
            tool_name,
            arguments,
            normalized_event,
        )
        return {
            "ok": True,
            "status": "waiting_frontend",
            "tool_call_id": tool_call_id,
            "message": "已请求前端处理；需要结果时调用 get_frontend_tool_result。",
            "next_tool": "get_frontend_tool_result",
        }

    def _register_frontend_tool_request(self, tool_call_id: str, request_event: Dict[str, Any]) -> None:
        self._frontend_tool_requests[tool_call_id] = dict(request_event)

    def _emit_frontend_tool_waiting_input(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        request_event: Dict[str, Any],
    ) -> None:
        self._emit_item_updated(
            {
                "id": tool_call_id,
                "type": EItem_tool_call,
                "name": tool_name,
                "arguments": arguments,
                "input_request": request_event,
                "status": EItemStatus_waiting_input,
            }
        )

    def read_frontend_tool_result(self, tool_call_id: str) -> Dict[str, Any]:
        tool_call_id = tool_call_id.strip()
        if not tool_call_id:
            return {"ok": False, "error": "tool_call_id 不能为空"}
        request = self._frontend_tool_requests.get(tool_call_id)
        if request is None:
            return {"ok": False, "error": "前端工具调用不存在或已过期", "tool_call_id": tool_call_id}
        if tool_call_id not in self._frontend_tool_results:
            return {
                "ok": True,
                "status": "pending",
                "tool_call_id": tool_call_id,
                "message": "前端尚未上报结果。",
                "request": request,
            }
        return {
            "ok": True,
            "status": "completed",
            "tool_call_id": tool_call_id,
            "result": self._frontend_tool_results[tool_call_id],
            "request": request,
        }

    async def wait_frontend_tool_result(self, tool_call_id: str, timeout_seconds: float = 5.0) -> Dict[str, Any]:
        """短时间等待前端工具结果。

        这里不能用阻塞 sleep；Agent 和结果上报接口跑在同一个服务进程里，
        阻塞事件循环会导致前端 POST 结果无法被处理。
        """
        deadline = time.monotonic() + max(0.0, min(float(timeout_seconds or 0), 30.0))
        while True:
            result = self.read_frontend_tool_result(tool_call_id)
            if not result.get("ok") or result.get("status") == "completed":
                return result
            if time.monotonic() >= deadline:
                return {
                    **result,
                    "wait_timeout_seconds": timeout_seconds,
                    "message": "前端尚未上报结果，请稍后重试 get_frontend_tool_result。",
                }
            await asyncio.sleep(0.2)

    async def _request_assistant_message(
        self,
        allow_tools: bool,
    ) -> tuple[Dict[str, Any], Optional[str]]:
        """调用模型并返回 OpenAI chat message 字典。

        文本内容按 Codex 子集协议实时发出:
        - item.started: 新 assistant message item
        - agent_message.delta: 文本增量
        - item.completed: 完整 assistant message item

        工具调用需要等流式输出结束后才能拿到完整 JSON 参数。返回的 item_id 用于让
        主循环根据下一步是执行工具还是结束 turn，补齐 Codex MessagePhase 并完成 item。
        """
        await self._compact_context_if_needed()
        request_messages = [filter_model_message_fields(message) for message in self.messages]
        kwargs: Dict[str, Any] = {
            "model": resolve_request_model(self.model),
            "messages": request_messages,
            "temperature": 0.2,
            "timeout": 60.0,
            "stream": True,
        }
        context_chars = self._estimate_context_chars()
        xlog.info(
            self.thread_id,
            "[server-agent] request model stream turn_id=%s seq_current=%d allow_tools=%s messages=%d context_chars=%d token_estimate=%d",
            self.current_run_id,
            self._run_seq,
            allow_tools,
            len(request_messages),
            context_chars,
            max(1, context_chars // 3),
        )
        if allow_tools and self.spec.tool_schemas:
            kwargs["tools"] = self.spec.tool_schemas
            kwargs["tool_choice"] = "auto"

        token_estimate = max(1, context_chars // 3)
        model_span_id = self.trace.model_request_started(
            model=kwargs["model"],
            allow_tools=allow_tools,
            message_count=len(request_messages),
            context_chars=context_chars,
            token_estimate=token_estimate,
            tools_count=len(self.spec.tool_schemas) if allow_tools and self.spec.tool_schemas else 0,
            messages=request_messages,
            run_seq=self._run_seq,
        )
        xlog.trace(
            self.thread_id,
            "[server-agent] model request run_id=%s seq_current=%d payload=%s",
            self.current_run_id,
            self._run_seq,
            self._json_log(kwargs),
        )
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            self.trace.model_request_failed(model_span_id, exc)
            raise

        assistant_dict: Dict[str, Any]
        # payload 尽量保留模型原始响应用于排查；view 是给观测面板看的摘要。
        response_payload: Dict[str, Any]
        response_view: Dict[str, Any]
        assistant_item_id: Optional[str]

        # __aiter__ 表示 SDK 返回的是异步流，需要逐段消费 delta 并拼回完整消息。
        if hasattr(response, "__aiter__"):
            (
                assistant_dict,
                response_payload,
                response_view,
                assistant_item_id,
            ) = await self._consume_model_stream(
                response,
                model=str(kwargs["model"]),
                model_span_id=model_span_id,
            )
        else:
            assistant_dict = assistant_message_to_dict(response.choices[0].message)
            response_payload = self._model_response_trace_payload(response)
            response_view = self._model_response_trace_view(response)
            content = str(assistant_dict.get("content") or "")
            assistant_item_id = "msg_" + uuid.uuid4().hex if content else None
            if assistant_item_id is not None:
                item = {
                    "id": assistant_item_id,
                    "type": EItem_agent_message,
                    "text": "",
                    "status": EItemStatus_in_progress,
                }
                self._emit_item_started(item)

        self.trace.model_request_finished(
            model_span_id,
            model=kwargs["model"],
            response_payload=response_payload,
            response_view=response_view,
            run_seq=self._run_seq,
        )
        xlog.trace(
            self.thread_id,
            "[server-agent] model response run_id=%s seq_current=%d payload=%s",
            self.current_run_id,
            self._run_seq,
            self._json_log(response_payload),
        )
        return assistant_dict, assistant_item_id

    async def _consume_model_stream(
        self,
        response: Any,
        *,
        model: str,
        model_span_id: str,
    ) -> tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Optional[str]]:
        stream = ModelStreamAccumulator(model)
        message_item_id: Optional[str] = None
        stream_started_at: Optional[float] = None

        # ── 1. 消费流式 chunk，并把正文 delta 推给前端 ──
        async for chunk in response:
            content_text = stream.consume_chunk(chunk)

            # 没有文本输出就跳过
            if content_text is None:
                continue

            # emit 文本 stream开始事件： item.started
            if message_item_id is None:
                message_item_id = "msg_" + uuid.uuid4().hex
                item = {
                    "id": message_item_id,
                    "type": EItem_agent_message,
                    "text": "",
                    "status": EItemStatus_in_progress,
                }
                self._emit_item_started(item)

            # phoenix 看板遥测
            if stream_started_at is None:
                stream_started_at = time.perf_counter()
                self.trace.stream_started(
                    model_span_id,
                    item_id=message_item_id,
                    response_id=stream.response_id,
                    model=stream.output_model,
                    run_seq=self._run_seq,
                )

            # agent_message.delta事件
            self._emit_event(
                {
                    "type": EEvent_agent_message_delta,
                    "turn_id": self.current_run_id,
                    "item_id": message_item_id,
                    "delta": content_text,
                }
            )

        # ── 2. 关闭流式 trace，并拼回标准 assistant message ──
        if stream_started_at is not None:
            stream_duration_ms = round((time.perf_counter() - stream_started_at) * 1000, 2)
            stream_summary = stream.stream_summary(item_id=message_item_id, duration_ms=stream_duration_ms)
            self.trace.stream_completed(
                model_span_id,
                summary=stream_summary,
                model=stream.output_model,
                run_seq=self._run_seq,
            )
            xlog.info(
                self.thread_id,
                "[server-agent] model stream completed turn_id=%s item_id=%s delta_count=%d content_chars=%d finish_reason=%s duration_ms=%.2f",
                self.current_run_id,
                message_item_id,
                stream.delta_count,
                stream.content_chars,
                stream.finish_reason,
                stream_duration_ms,
            )

        assistant_dict = stream.assistant_message()
        return (
            assistant_dict,
            stream.response_payload(assistant_dict),
            stream.response_view(),
            message_item_id,
        )

    def snapshot(self) -> Dict[str, Any]:
        """返回内存态快照。"""
        return {
            "thread_id": self.thread_id,
            "owner_id": self.owner_id,
            "agent_kind": self.spec.agent_kind,
            "current_turn_id": self.current_run_id,
            "last_turn_id": self.last_run_id,
            "mode": self.mode,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "message_count": self.message_window.active_count,
            "persisted_message_count": self.message_window.persisted_count,
            "event_count": self.event_log.count,
            "subscriber_count": self.event_log.subscriber_count,
            "created_at": self._datetime_to_text(self.created_at),
            "updated_at": self._datetime_to_text(self.updated_at),
            "messages": self.messages,
            "events": self.events,
        }

    def subscribe_events(self, after_seq: int = 0, turn_id: str | None = None) -> tuple[str, asyncio.Queue]:
        """订阅 thread 事件；调用方负责 unsubscribe。"""
        return self.event_log.subscribe(after_seq=after_seq, turn_id=turn_id)

    def unsubscribe_events(self, subscriber_id: str) -> None:
        """取消事件订阅。"""
        self.event_log.unsubscribe(subscriber_id)

    def submit_frontend_tool_result(self, tool_call_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """接收前端工具结果，供 Agent 后续通过 get_frontend_tool_result 读取。"""
        tool_call_id = tool_call_id.strip()
        if not tool_call_id:
            raise ValueError("tool_call_id 不能为空")
        request = self._frontend_tool_requests.get(tool_call_id)
        if request is None:
            raise ValueError("前端工具调用不存在或已过期")
        if tool_call_id in self._frontend_tool_results:
            raise ValueError("前端工具结果已经提交")
        self._frontend_tool_results[tool_call_id] = result
        self._emit_item_updated(
            {
                "id": tool_call_id,
                "type": EItem_tool_call,
                "name": request.get("name"),
                "input_request": request,
                "frontend_result_status": "received",
                "status": EItemStatus_in_progress,
            }
        )
        xlog.info(
            self.thread_id,
            "[server-agent] frontend tool result accepted turn_id=%s tool_call_id=%s",
            request.get("turn_id"),
            tool_call_id,
        )
        return {
            "thread_id": self.thread_id,
            "turn_id": request.get("turn_id"),
            "tool_call_id": tool_call_id,
            "accepted": True,
            "status": self.status,
            "message": "前端工具结果已提交，Agent 可通过 get_frontend_tool_result 读取。",
        }

    def emit_run_error(self, run_id: str, message: str) -> None:
        """后台任务异常时，把错误也走同一条 turn 事件流。"""
        if run_id and self.current_run_id != run_id:
            self.current_run_id = run_id
            self.last_run_id = run_id
            self._run_seq = 0
        self._active_tool_call = None
        self.status = EStatus_waiting_user
        self.stop_reason = EStopReason_error
        self.updated_at = datetime.utcnow()
        self._emit_item_completed(
            {
                "id": "err_" + uuid.uuid4().hex,
                "type": EItem_error,
                "message": message,
                "status": EItemStatus_failed,
            }
        )
        self._emit_event(
            {
                "type": EEvent_turn_failed,
                "turn_id": self.current_run_id,
                "stop_reason": EStopReason_error,
                "error": message,
            }
        )

    def emit_run_interrupted(self, run_id: str, message: str) -> None:
        """主动打断或崩溃恢复时，把当前 turn 收束成 interrupted。

        打断工具调用时最关键的是保持模型 messages 协议完整：
          1. 模型返回 tool_call 后，messages 里已经追加了 assistant message；
          2. OpenAI chat 协议要求 assistant tool_call 后面必须跟一条对应 tool result；
          3. 如果这时直接中断，恢复后再次请求模型会带着孤立 tool_call；
          4. 所以这里要补一条 interrupted 的失败 tool result message。
        """
        if run_id and self.current_run_id != run_id:
            self.current_run_id = run_id
            self.last_run_id = run_id
            self._run_seq = 0
        active_tool_call = self._interruptible_tool_call()
        if active_tool_call and active_tool_call.get("turn_id") == self.current_run_id:
            tool_call_id = str(active_tool_call.get("tool_call_id") or "")
            tool_name = str(active_tool_call.get("tool_name") or "")
            arguments = active_tool_call.get("arguments") or {}
            result = {"ok": False, "interrupted": True, "error": message}
            self._append_message(
                {
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )
            self.trace.tool_failed(
                tool_call_id,
                tool_name,
                result=result,
                run_seq=self._run_seq,
            )
            self._emit_item_completed(
                {
                    "id": tool_call_id,
                    "type": EItem_tool_call,
                    "name": tool_name,
                    "arguments": arguments,
                    "result": result,
                    "status": EItemStatus_failed,
                    "error": message,
                }
            )
            self._active_tool_call = None

        self.status = EStatus_waiting_user
        self.stop_reason = EStopReason_interrupted
        self.updated_at = datetime.utcnow()
        self._emit_event(
            {
                "type": EEvent_turn_failed,
                "turn_id": self.current_run_id,
                "stop_reason": EStopReason_interrupted,
                "error": message,
            }
        )

    def _interruptible_tool_call(self) -> Optional[Dict[str, Any]]:
        """返回当前 turn 中需要补失败 tool result 的工具调用。

        正常运行时直接用 _active_tool_call；服务重启后这个内存字段会丢失，所以再从
        已落库事件里倒查最近一个未 completed/failed 的 tool_call item。
        """
        if self._active_tool_call:
            return self._active_tool_call

        # 特殊恢复路径：
        # 正常运行时不会走到这里；只有服务重启 / 内存 Agent 被重建后，
        # _active_tool_call 这种运行时字段才会丢失。
        #
        # 典型场景：
        #   1. 模型返回 assistant tool_call，assistant message 已落库；
        #   2. 后端 emit item.started / item.updated(waiting_input)，事件已落库；
        #   3. 服务重启，_active_tool_call 丢失；
        #   4. 用户选择 cancel，本函数必须从事件账本里找回 tool_call_id，
        #      这样才能补一条 interrupted tool result，保持 messages 协议完整。
        #
        # reversed(self.events) 是倒查“最近一个未收尾工具”的恢复手段，不参与普通执行路径。
        for event in reversed(self.events):
            if event.get("turn_id") != self.current_run_id:
                continue
            item = event.get("item")
            if not isinstance(item, dict):
                continue
            if item.get("type") != EItem_tool_call:
                continue
            status = item.get("status")
            if status in {EItemStatus_completed, EItemStatus_failed}:
                return None
            if status in {EItemStatus_in_progress, EItemStatus_waiting_input}:
                return {
                    "turn_id": self.current_run_id,
                    "tool_call_id": item.get("id"),
                    "tool_name": item.get("name"),
                    "arguments": item.get("arguments") or {},
                }
        return None

    def _emit_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """提交一个 Agent 事件：编号、保存、推送，并同步运行状态。"""
        now = datetime.utcnow()
        # ── 1. 补齐事件公共字段 ──
        event = dict(event)
        event.setdefault("thread_id", self.thread_id)
        event.setdefault("created_at", self._datetime_to_text(now))
        if self.current_run_id and "turn_id" not in event:
            event["turn_id"] = self.current_run_id

        # 全局事件序号，字段seq
        self.event_log.assign_seq(event)

        # turn内部序号，字段turn_seq
        if self.current_run_id and event.get("turn_id") == self.current_run_id:
            self._run_seq += 1
            event.setdefault("turn_seq", self._run_seq)

        # ── 2. 保存事件并推送给 SSE 订阅者 ──
        persist_event(thread_id=self.thread_id, event=event, created_at=now)
        self.event_log.append_and_publish(event)
        self.trace.emitted_event(event, run_seq=self._run_seq)

        # ── 3. turn 结束时同步 turn 状态 ──
        event_type = event.get("type")
        if event_type in {EEvent_turn_completed, EEvent_turn_failed}:
            finish_turn_record(
                thread_id=self.thread_id,
                turn_id=event.get("turn_id"),
                event=event,
                message_end_index=self.message_window.last_persisted_index,
                updated_at=now,
            )
            self.current_run_id = None

        # ── 4. 同步 thread 快照 ──
        persist_thread_snapshot(
            thread_id=self.thread_id,
            mode=self.mode,
            status=self.status,
            stop_reason=self.stop_reason,
            current_turn_id=self.current_run_id,
            last_turn_id=self.last_run_id,
            persisted_message_count=self.message_window.persisted_count,
            event_count=self.event_log.count,
            updated_at=now,
        )

        # ── 5. 记录可检索日志；delta 太密，只走 trace / SSE，不打普通 info ──
        if event_type == EEvent_agent_message_delta:
            return event
        log_item = event.get("item") if isinstance(event.get("item"), dict) else {}
        log_item_id = event.get("item_id") or log_item.get("id") or ""
        xlog.info(
            self.thread_id,
            "[server-agent] emit:%s turn_id=%s seq=%s item_id=%s stop_reason=%s",
            event.get("type"),
            event.get("turn_id"),
            event.get("seq"),
            log_item_id,
            event.get("stop_reason"),
        )
        return event

    def _append_message(self, message: Dict[str, Any]) -> None:
        """追加模型上下文消息，并同步保存可恢复消息。"""
        message_index = self.message_window.append(message)
        now = datetime.utcnow()
        persist_message(
            thread_id=self.thread_id,
            turn_id=self.current_run_id,
            message_index=message_index,
            message=message,
            created_at=now,
        )
        persist_thread_snapshot(
            thread_id=self.thread_id,
            mode=self.mode,
            status=self.status,
            stop_reason=self.stop_reason,
            current_turn_id=self.current_run_id,
            last_turn_id=self.last_run_id,
            persisted_message_count=self.message_window.persisted_count,
            event_count=self.event_log.count,
            updated_at=now,
        )

    def _emit_item_started(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self._emit_event(
            {
                "type": EEvent_item_started,
                "item": item,
            }
        )

    def _emit_item_updated(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self._emit_event(
            {
                "type": EEvent_item_updated,
                "item": item,
            }
        )

    def _emit_item_completed(self, item: Dict[str, Any]) -> Dict[str, Any]:
        return self._emit_event(
            {
                "type": EEvent_item_completed,
                "item": item,
            }
        )

    def _finish_agent_message(
        self,
        message: Dict[str, Any],
        item_id: Optional[str],
    ) -> None:
        """按 Agent 的实际后续动作确定消息阶段，并完成对应的前端 item。"""
        phase = (
            EMessagePhase_commentary
            if message.get("tool_calls")
            else EMessagePhase_final_answer
        )
        message["phase"] = phase
        if item_id is not None:
            self._emit_item_completed(
                {
                    "id": item_id,
                    "type": EItem_agent_message,
                    "text": str(message.get("content") or ""),
                    "phase": phase,
                    "status": EItemStatus_completed,
                }
            )

    def _begin_run(self, run_id: str = "") -> str:
        """开始一次用户输入驱动的运行。"""
        self.current_run_id = run_id.strip() or uuid.uuid4().hex
        self.last_run_id = self.current_run_id
        self._run_seq = 0
        self.trace.begin_turn(
            self.current_run_id,
            mode=self.mode,
            message_count=len(self.messages),
            run_seq=self._run_seq,
        )
        xlog.info(
            self.thread_id,
            "[server-agent] begin run_id=%s seq=%d",
            self.current_run_id,
            self._run_seq,
        )
        return self.current_run_id

    def _build_result(self, step_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        stop_event = step_events[-1] if step_events else {}
        turn_id = None
        for event in step_events:
            if event.get("turn_id"):
                turn_id = event.get("turn_id")
                break
        return {
            "thread_id": self.thread_id,
            "turn_id": turn_id or self.current_run_id,
            "status": self.status,
            "stop_reason": stop_event.get("stop_reason") or self.stop_reason,
            "answer": stop_event.get("answer") or self._last_assistant_answer(step_events),
            "messages": self.messages,
            "events": step_events,
        }

    def _last_assistant_answer(self, step_events: List[Dict[str, Any]]) -> str:
        for event in reversed(step_events):
            if event.get("type") != EEvent_item_completed:
                continue
            item = event.get("item") or {}
            if not isinstance(item, dict) or item.get("type") != EItem_agent_message:
                continue
            content = item.get("text") or ""
            if content:
                return content
        for message in reversed(self.messages):
            if message.get("role") != "assistant":
                continue
            content = message.get("content") or ""
            if content:
                return str(content)
        return ""

    def _build_system_prompt(self) -> str:
        return self.spec.build_system_prompt(self.mode)

    def _set_mode_for_run(self, mode: Any) -> None:
        mode_text = str(mode or "").strip()
        if not mode_text:
            return
        self.spec.assert_mode(mode_text)
        if mode_text == self.mode:
            return
        self.mode = mode_text
        if self.messages and self.messages[0].get("role") == "system":
            self.messages[0]["content"] = self._build_system_prompt()

    def _build_user_message(self, content: str, prompt: str) -> str:
        parts = []
        if prompt.strip():
            parts.append("本轮补充上下文:\n" + prompt.strip())
        if content.strip():
            parts.append("用户输入:\n" + content.strip())
        return "\n\n".join(parts)

    async def _compact_context_if_needed(self) -> None:
        """在请求模型前按需压缩上下文。

        当前轮的用户消息已经写入内存窗口和数据库；如果窗口估算 token 超过阈值，
        这里先把较早历史压成一条 context_compaction system message，再用压缩后的
        内存窗口继续请求模型。前端只需要看 item.started / item.completed；Phoenix
        里会看到 context.compaction 与 llm.chat.summary 两段 span。
        """
        settings = compaction_settings_from_env()

        # prepare_compaction 只做切点计算，不调用模型也不改内存。
        # 第二个参数是“当前内存窗口每条 message 对应的数据库 message_index”，
        # 它用来把内存切点翻译成 first_kept_message_index，恢复会话时还要靠这个字段重建窗口。
        prepared = prepare_compaction(
            self.messages,
            self.message_window.db_indexes,
            settings,
        )
        if prepared is None:
            return

        # 走到这里说明本轮会真的压缩；不在未触发时打日志，避免普通对话刷屏。
        summary_messages = build_summary_request_messages(prepared)
        summary_model = resolve_request_model(self.model)
        xlog.info(
            self.thread_id,
            "[server-agent] context compaction planned run_id=%s messages=%d tokens_before=%d trigger=%d keep_recent=%d first_kept=%s split=%s summarized=%d kept=%d",
            self.current_run_id,
            len(self.messages),
            prepared.tokens_before,
            settings.trigger_tokens,
            settings.keep_recent_tokens,
            prepared.first_kept_message_index,
            prepared.is_split_turn,
            len(prepared.messages_to_summarize),
            len(prepared.kept_messages),
        )
        compaction_item = {
            "id": "cmp_" + uuid.uuid4().hex,
            "type": EItem_context_compaction,
            "text": "正在压缩上下文",
            "status": EItemStatus_in_progress,
            "message_count": len(self.messages),
            "tokens_before": prepared.tokens_before,
            "trigger_tokens": settings.trigger_tokens,
            "keep_recent_tokens": settings.keep_recent_tokens,
            "first_kept_message_index": prepared.first_kept_message_index,
            "turn_start_message_index": prepared.turn_start_message_index,
            "is_split_turn": prepared.is_split_turn,
            "turn_prefix_message_count": len(prepared.turn_prefix_messages),
            "summarized_message_count": len(prepared.messages_to_summarize),
            "tokens_after_estimate": prepared.tokens_after_estimate,
            "model": summary_model,
        }
        self._emit_item_started(compaction_item)
        
        # context.compaction span
        self.trace.context_compaction_started(
            payload={
                "message_count": len(self.messages),
                "tokens_before": prepared.tokens_before,
                "trigger_tokens": settings.trigger_tokens,
                "keep_recent_tokens": settings.keep_recent_tokens,
                "first_kept_message_index": prepared.first_kept_message_index,
                "turn_start_message_index": prepared.turn_start_message_index,
                "is_split_turn": prepared.is_split_turn,
                "turn_prefix_message_count": len(prepared.turn_prefix_messages),
                "summarized_message_count": len(prepared.messages_to_summarize),
                "tokens_after_estimate": prepared.tokens_after_estimate,
                "model": summary_model,
            },
            run_seq=self._run_seq,
        )
        
        # 这个就是llm.chat.summary span
        self.trace.context_summary_started(
            model=summary_model,
            messages=summary_messages,
            token_estimate=estimate_messages_tokens(summary_messages),
            run_seq=self._run_seq,
        )
        try:
            # 先让模型生成摘要；只有摘要成功，后面才会写 summary message 并替换内存窗口。
            # 这样失败时数据库仍然保持压缩前的完整历史，不会出现半压缩状态。
            result = await run_prepared_compaction(
                client=self.client,
                model=self.model,
                prepared=prepared,
                settings=settings,
                summary_messages=summary_messages,
            )
        except Exception as exc:
            xlog.error(
                self.thread_id,
                exc,
                "[server-agent] context compaction summary failed run_id=%s first_kept=%s tokens_before=%d",
                self.current_run_id,
                prepared.first_kept_message_index,
                prepared.tokens_before,
            )
            self.trace.context_summary_failed(exc, run_seq=self._run_seq)
            self.trace.context_compaction_failed(exc, run_seq=self._run_seq)
            self._emit_item_completed(
                {
                    **compaction_item,
                    "text": "上下文压缩失败",
                    "status": EItemStatus_failed,
                    "error_message": str(exc),
                }
            )
            raise

        self.trace.context_summary_finished(
            payload={
                "model": result.model,
                "summary": result.summary_text,
                "summary_message": result.summary_message.get("content"),
                "summary_chars": result.summary_chars,
                "tokens_before": result.tokens_before,
                "tokens_after_estimate": result.tokens_after_estimate,
                "is_split_turn": result.is_split_turn,
                "turn_start_message_index": result.turn_start_message_index,
                "turn_prefix_message_count": result.turn_prefix_message_count,
                "summarized_message_count": result.summarized_message_count,
            },
            run_seq=self._run_seq,
        )

        try:
            # 摘要已经拿到，下面进入真正的状态切换：落库摘要 message，再改内存窗口。
            self._apply_context_compaction_result(result, compaction_item["id"])
        except Exception as exc:
            xlog.error(
                self.thread_id,
                exc,
                "[server-agent] context compaction apply failed run_id=%s first_kept=%s summarized=%d",
                self.current_run_id,
                result.first_kept_message_index,
                result.summarized_message_count,
            )
            self.trace.context_compaction_failed(exc, run_seq=self._run_seq)
            self._emit_item_completed(
                {
                    **compaction_item,
                    "text": "上下文压缩失败",
                    "status": EItemStatus_failed,
                    "error_message": str(exc),
                }
            )
            raise

    def _apply_context_compaction_result(
        self,
        result: Any,
        compaction_item_id: str,
    ) -> None:
        """落库摘要，并把内存窗口替换为 system + summary + 最近原文消息。

        数据库不删除旧消息，它仍然是审计账本；这里改的是后续喂给模型的活跃窗口。
        """
        # summary_index 是新摘要消息在数据库 agent_messages 里的编号。
        # 它不能用 len(self.messages)，因为内存窗口压缩后不再等于数据库全量历史。
        summary_index = self.message_window.next_index
        summary_message = dict(result.summary_message)
        # 摘要消息自己也带落库编号，单独查看 JSON 时可以快速定位它在历史里的位置。
        summary_message["summary_message_index"] = summary_index
        now = datetime.utcnow()
        persist_message(
            thread_id=self.thread_id,
            turn_id=self.current_run_id,
            message_index=summary_index,
            message=summary_message,
            created_at=now,
        )
        # apply_compaction 只改内存窗口和对应的 db index 映射：
        # 原始历史已经在上面落库保留，不会被删除。
        self.message_window.apply_compaction(
            summary_message=summary_message,
            kept_messages=result.kept_messages,
            kept_db_indexes=result.kept_db_indexes,
        )
        payload = {
            "summary_message_index": summary_index,
            "first_kept_message_index": result.first_kept_message_index,
            "turn_start_message_index": result.turn_start_message_index,
            "is_split_turn": result.is_split_turn,
            "turn_prefix_message_count": result.turn_prefix_message_count,
            "summarized_message_count": result.summarized_message_count,
            "persisted_message_count": self.message_window.persisted_count,
            "tokens_before": result.tokens_before,
            "tokens_after_estimate": result.tokens_after_estimate,
            "summary_chars": result.summary_chars,
            "model": result.model,
        }
        xlog.info(
            self.thread_id,
            "[server-agent] context compacted run_id=%s summary_index=%d first_kept=%d summarized=%d tokens_before=%d tokens_after=%d",
            self.current_run_id,
            summary_index,
            result.first_kept_message_index,
            result.summarized_message_count,
            result.tokens_before,
            result.tokens_after_estimate,
        )
        self.trace.context_compacted(payload=payload, run_seq=self._run_seq)
        self._emit_item_completed(
            {
                "id": compaction_item_id,
                "type": EItem_context_compaction,
                "text": "上下文已压缩",
                "status": EItemStatus_completed,
                **payload,
            }
        )
        persist_thread_snapshot(
            thread_id=self.thread_id,
            mode=self.mode,
            status=self.status,
            stop_reason=self.stop_reason,
            current_turn_id=self.current_run_id,
            last_turn_id=self.last_run_id,
            persisted_message_count=self.message_window.persisted_count,
            event_count=self.event_log.count,
            updated_at=now,
        )

    def _estimate_context_chars(self) -> int:
        total = 0
        for message in self.messages:
            try:
                total += len(json.dumps(message, ensure_ascii=False))
            except TypeError:
                total += len(str(message))
        return total

    def _normalize_input_messages(self, input_messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        normalized: List[Dict[str, str]] = []
        for item in input_messages:
            role = item.get("role")
            if role not in {"user", "assistant"}:
                raise ValueError("messages 只允许 user / assistant")
            normalized.append({"role": role, "content": item.get("content") or ""})
        return normalized

    def _model_response_trace_payload(self, response: Any) -> Dict[str, Any]:
        choices = getattr(response, "choices", None) or []
        choice_views = []
        for choice in choices:
            message = getattr(choice, "message", None)
            choice_views.append(
                {
                    "index": getattr(choice, "index", None),
                    "finish_reason": getattr(choice, "finish_reason", None),
                    "message": self._assistant_message_to_dict(message) if message is not None else None,
                }
            )
        usage = getattr(response, "usage", None)
        return {
            "id": getattr(response, "id", None),
            "model": getattr(response, "model", None),
            "created": getattr(response, "created", None),
            "choices": choice_views,
            "usage": usage.model_dump(mode="json") if hasattr(usage, "model_dump") else usage,
        }

    def _model_response_trace_view(self, response: Any) -> Dict[str, Any]:
        payload = self._model_response_trace_payload(response)
        choices = payload.get("choices") or []
        first = choices[0] if choices else {}
        message = first.get("message") if isinstance(first, dict) else {}
        tool_calls = message.get("tool_calls") if isinstance(message, dict) else []
        content = message.get("content") if isinstance(message, dict) else ""
        return {
            "id": payload.get("id"),
            "model": payload.get("model"),
            "finish_reason": first.get("finish_reason") if isinstance(first, dict) else None,
            "content_chars": len(content or ""),
            "content_preview": str(content or "")[:1200],
            "tool_call_count": len(tool_calls or []),
            "usage": payload.get("usage"),
        }

    def _load_tool_arguments(self, raw_arguments: str) -> tuple[Dict[str, Any], Optional[str]]:
        try:
            data = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as exc:
            return {}, (
                f"工具参数不是合法 JSON: {exc.msg} "
                f"pos={exc.pos} line={exc.lineno} col={exc.colno}。"
                "常见原因是正文或选项文本中使用了未转义英文双引号；"
                "请改用中文引号「」或正确转义为 \\\"。"
            )
        if not isinstance(data, dict):
            return {}, "工具参数 JSON 顶层必须是对象"
        return data, None

    def _normalize_progress_stage(self, stage: Any) -> str:
        text = str(stage or "").strip()
        if text.startswith("EEvent_"):
            text = text[len("EEvent_"):]
        return text.lower().replace("_", ".")

    def _json_log(self, value: Any) -> str:
        try:
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            elif hasattr(value, "to_dict"):
                value = value.to_dict()
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception as exc:
            return json.dumps(
                {
                    "serialization_error": str(exc),
                    "preview": str(value),
                },
                ensure_ascii=False,
            )

    def _datetime_to_text(self, value: Optional[datetime]) -> Optional[str]:
        return value.isoformat() if value else None


class ServerAgentManager:
    """进程内 Agent 管理器。缺失对象时从数据库恢复。"""

    def __init__(self):
        self._threads: Dict[str, ServerAgent] = {}
        self._tasks: set[asyncio.Task] = set()
        self._turn_tasks: Dict[tuple[str, str], asyncio.Task] = {}

    async def create_agent(
        self,
        owner_id: int | None = None,
        agent_kind: str = "server_agent",
        mode: str | None = None,
    ) -> ServerAgent:
        spec = require_agent_spec(agent_kind)
        if mode:
            spec.assert_mode(mode)
        agent = ServerAgent(
            owner_id=owner_id,
            mode=mode,
            spec=spec,
        )
        create_thread_record(
            thread_id=agent.thread_id,
            owner_id=agent.owner_id,
            agent_kind=agent.spec.agent_kind,
            mode=agent.mode,
            status=agent.status,
            stop_reason=agent.stop_reason,
            model=agent.model,
            current_turn_id=agent.current_run_id,
            last_turn_id=agent.last_run_id,
            messages=agent.messages,
            events=agent.events,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        await agent.start()
        self._threads[agent.thread_id] = agent
        return agent

    def get_thread(self, thread_id: str, owner_id: int | None = None) -> Optional[ServerAgent]:
        agent = self._threads.get(thread_id)
        if agent:
            if owner_id is not None and agent.owner_id != owner_id:
                return None
            return agent
        record = load_thread_record(thread_id, owner_id=owner_id)
        if not record:
            return None
        agent = ServerAgent.restore(record)
        self._threads[thread_id] = agent
        return agent

    def delete_thread(self, thread_id: str, owner_id: int | None = None) -> bool:
        agent = self._threads.get(thread_id)
        if agent and owner_id is not None and agent.owner_id != owner_id:
            return False
        removed_memory = self._threads.pop(thread_id, None) is not None
        removed_record = delete_thread_record(thread_id, owner_id=owner_id)
        return removed_memory or removed_record

    def submit_resume(
        self,
        agent: ServerAgent,
        *,
        run_id: str,
        content: str,
        prompt: str = "",
        mode: Optional[str] = None,
    ) -> None:
        """提交一次 run 到后台；调用方通过 SSE 观察结果。"""
        if agent.current_run_id:
            raise ValueError("当前 Agent 已有进行中的 turn，请等待完成后再继续。")
        agent.current_run_id = run_id
        agent.last_run_id = run_id
        task_key = (agent.thread_id, run_id)
        existing_task = self._turn_tasks.get(task_key)
        if existing_task and not existing_task.done():
            raise ValueError("当前 turn 已在运行中。")
        task = asyncio.create_task(
            self._resume_task(
                agent,
                run_id=run_id,
                content=content,
                prompt=prompt,
                mode=mode,
            )
        )
        self._tasks.add(task)
        self._turn_tasks[task_key] = task

        def _forget_task(done_task: asyncio.Task, key: tuple[str, str] = task_key) -> None:
            self._tasks.discard(done_task)
            if self._turn_tasks.get(key) is done_task:
                self._turn_tasks.pop(key, None)

        task.add_done_callback(_forget_task)

    def submit_frontend_tool_result(
        self,
        thread_id: str,
        tool_call_id: str,
        result: Dict[str, Any],
        owner_id: int | None = None,
    ) -> Dict[str, Any]:
        """把前端工具结果交给正在等待的 Agent turn。"""
        agent = self.get_thread(thread_id, owner_id=owner_id)
        if not agent:
            raise ValueError("Agent 不存在")
        return agent.submit_frontend_tool_result(tool_call_id, result)

    def cancel_turn(self, thread_id: str, turn_id: str, owner_id: int | None = None) -> Dict[str, Any]:
        """请求打断当前 turn。实际停止发生在下一个 await 挂起点。"""
        agent = self.get_thread(thread_id, owner_id=owner_id)
        if not agent:
            raise ValueError("Agent 不存在")
        if not turn_id:
            raise ValueError("turn_id 不能为空")
        if agent.current_run_id != turn_id:
            raise ValueError("只能打断当前正在运行或等待外部结果的 turn。")

        task = self._turn_tasks.get((thread_id, turn_id))
        if task and not task.done():
            # 这里cancel后，_resume_task会捕获cancel并调用emit_run_interrupted
            task.cancel()
            xlog.info(
                thread_id,
                "[server-agent] cancel requested run_id=%s",
                turn_id,
            )
            return {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "cancelled": True,
                "status": agent.status,
                "stop_reason": agent.stop_reason,
                "message": "Agent turn 已请求打断，完成状态请通过 SSE 订阅读取。",
            }

        raise ValueError("当前 turn 没有可打断的后台任务。")

    async def _resume_task(
        self,
        agent: ServerAgent,
        *,
        run_id: str,
        content: str,
        prompt: str,
        mode: Optional[str],
    ) -> None:
        try:
            await agent.resume(
                content=content,
                prompt=prompt,
                run_id=run_id,
                mode=mode,
            )
        except asyncio.CancelledError:
            xlog.info(
                agent.thread_id,
                "[server-agent] background run interrupted run_id=%s",
                run_id,
            )
            agent.emit_run_interrupted(run_id, "用户已打断本轮 Agent 执行")
        except Exception as exc:
            xlog.error(
                agent.thread_id,
                exc,
                "[server-agent] background run failed run_id=%s",
                run_id,
            )
            agent.emit_run_error(run_id, f"Agent 执行失败: {exc}")


server_agent_manager = ServerAgentManager()
