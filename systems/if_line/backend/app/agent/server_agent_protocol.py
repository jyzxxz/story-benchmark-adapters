"""
服务端 Agent 前端协议常量。

这里集中放 SSE 事件名、item 类型和状态值，保证前端协议字段能全局检索，
也避免 server_agent 与 trace_recorder 各自维护一份字面量。
"""

EEvent_thread_started = "thread.started"
EEvent_turn_started = "turn.started"
EEvent_turn_completed = "turn.completed"
EEvent_turn_failed = "turn.failed"
EEvent_item_started = "item.started"
EEvent_item_updated = "item.updated"
EEvent_item_completed = "item.completed"
EEvent_agent_message_delta = "agent_message.delta"
EEvent_error = "error.occurred"

EItem_agent_message = "agent_message"
EItem_context_compaction = "context_compaction"
EItem_tool_call = "tool_call"
EItem_error = "error"

# 与 Codex MessagePhase 对齐：commentary 是本轮中间说明，final_answer 是本轮最终答复。
EMessagePhase_commentary = "commentary"
EMessagePhase_final_answer = "final_answer"

EItemStatus_in_progress = "in_progress"
EItemStatus_completed = "completed"
EItemStatus_failed = "failed"
EItemStatus_waiting_input = "waiting_input"

EStatus_created = "created"
EStatus_running = "running"
EStatus_waiting_user = "waiting_user"

EStopReason_waiting_user = "waiting_user"
EStopReason_assistant_answer = "assistant_answer"
EStopReason_error = "error"
EStopReason_interrupted = "interrupted"
