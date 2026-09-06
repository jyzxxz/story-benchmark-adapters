# 服务端 Agent API 与事件协议

本文档描述当前 `server_agent` 的前端接口和 SSE 事件。协议按 Codex CLI / SDK 的思路收敛为“少量生命周期事件 + 多种 item”，不再使用旧的 `EEvent_*` 事件作为前端协议。

## 概念

- `thread_id`：服务端 Agent 会话编号。前端只需要保存这个编号，并用它订阅事件、提交输入、恢复页面。服务重启后，后端会从数据库恢复 thread。
- `turn_id`：一次用户输入触发的一轮运行。从用户输入开始，到 `turn.completed` 或 `turn.failed` 结束。
- `seq`：thread 内事件递增序号，用作 SSE `id`，断线重连时传 `after_seq`。
- `item`：前端展示的核心对象。工具调用、Agent 回复、错误都表达为 item。

## API

### 创建 Agent

`POST /api/server-agent/threads`

请求：

```json
{
  "agent_kind": "server_agent",
  "mode": "restricted"
}
```

字段：

- `agent_kind`：Agent 类型，必填。当前常用 `server_agent`；也可传 `chat_agent` 创建普通对话 Agent。
- `mode`：可选。`server_agent` 可传 `restricted` 或 `solo`；不传时使用该 Agent 类型的默认模式。

返回：

```json
{
  "thread_id": "thread_xxx",
  "agent_kind": "server_agent",
  "current_turn_id": null,
  "last_turn_id": null,
  "mode": "restricted",
  "status": "waiting_user",
  "stop_reason": "waiting_user",
  "message_count": 1,
  "persisted_message_count": 1,
  "event_count": 1,
  "subscriber_count": 0,
  "created_at": "2026-08-16T12:00:00.000000",
  "updated_at": "2026-08-16T12:00:00.000000",
  "messages": [],
  "events": []
}
```

### 读取 Agent 会话列表

`GET /api/server-agent/threads?limit=50&offset=0`

用于页面恢复入口，返回当前用户自己的 Agent thread 列表。

参数：

- `limit`：每页数量，默认 50，最大 200。
- `offset`：分页偏移量。
- `status`：可选状态过滤，例如 `waiting_user`、`running`、`deleted`。
- `agent_kind`：可选 Agent 类型过滤。

### 订阅事件

`GET /api/server-agent/threads/{thread_id}/events?after_seq=0`

返回 `text/event-stream`。SSE 格式：

```text
id: 3
event: item.started
data: {"type":"item.started","seq":3,"thread_id":"...","turn_id":"...","item":{...}}
```

参数：

- `after_seq`：只补发 `seq > after_seq` 的事件。
- `turn_id`：可选。传入后只订阅某个 turn 的事件；普通前端建议不传，直接订阅整个 thread。
- SSE 断开只表示取消订阅，不会自动打断 Agent。用户点击停止时需要调用 cancel 接口。

### 分页读取事件

`GET /api/server-agent/threads/{thread_id}/events/page?after_seq=0&limit=100`

用于页面恢复时补历史事件。实时增量仍用 SSE。

返回里的 `next_after_seq` 可作为下一次请求的 `after_seq`。

### 提交用户输入

`POST /api/server-agent/threads/{thread_id}/turns/resume`

请求：

```json
{
  "content": "检查 project_id=1 第一章是否已有正文",
  "prompt": "",
  "mode": "restricted"
}
```

返回：

```json
{
  "thread_id": "thread_xxx",
  "turn_id": "turn_xxx",
  "accepted": true,
  "status": "waiting_user",
  "message": "Agent turn 已提交，后续事件请通过 SSE 订阅读取。"
}
```

说明：

- 这个接口只表示投递成功，不等待 Agent 执行完成。
- 前端必须通过 SSE 展示输出。
- `content` 是用户本轮真正输入。
- `prompt` 是本轮临时补充上下文，不是长期提示；普通聊天 UI 可以留空。内部调用可以放当前页面选中的项目、章节、资源等上下文。
- 本接口只用于普通用户输入，后端会自动创建新 turn。

### 提交前端工具结果

`POST /api/server-agent/threads/{thread_id}/tool-calls/{tool_call_id}/result`

Agent 调用某个需要前端参与的工具时，会发出一个 `waiting_input` 的 `tool_call`
item。当前 VNGraph 图片替换链路会使用：

- `request_frontend_vngraph_draft`：请求前端上报当前本地 VNGraph 草稿。
- `apply_vngraph_patch`：后端创建 `AssetAction`，生成 `vngraph_patch`，请求前端预览应用。
- `get_frontend_tool_result`：Agent 读取前端上报结果。
- `confirm_vngraph_patch`：后端重放 patch、校验 graph_json/hash，并写入新的 VNGraph revision。

当前端收到 `tool_call` item 且状态为 `waiting_input` 时，使用 item 的 `id` 作为
`tool_call_id` 调用本接口。

请求：

```json
{
  "result": {
    "ok": true,
    "selected": {
      "asset_id": 123,
      "asset_version_id": "asset_version_xxx"
    }
  }
}
```

返回：

```json
{
  "thread_id": "thread_xxx",
  "turn_id": "turn_xxx",
  "tool_call_id": "call_xxx",
  "accepted": true,
  "status": "running",
  "message": "前端工具结果已提交，Agent 可通过 get_frontend_tool_result 读取。"
}
```

说明：

- 普通用户输入不要走这个接口。
- 这个接口只把结果写入当前 Agent 的前端工具结果缓存，不会自动继续模型推理。
- Agent 如果需要读取结果，需要调用 `get_frontend_tool_result` 并传入刚才的 `tool_call_id`。
- 如果前端还没上报，`get_frontend_tool_result` 会返回 `status=pending`。

#### VNGraph apply 前端上报格式

当前端收到 `item.status=waiting_input`，且 `item.input_request.type` 为
`vngraph_apply.requested` 时：

1. 从 `item.input_request.vngraph_patch` 读取后端生成的 patch。
2. 在前端本地当前 VNGraph 草稿上应用 patch。
3. 计算应用后的完整 `graph_json` 和 `result_graph_hash`。
4. 调用本接口提交结果。

请求示例：

```json
{
  "result": {
    "action_id": "8109679b-38ea-4b78-a9f0-107631093610",
    "graph_json": {
      "Version": 1,
      "StartNodeIndex": 1,
      "Nodes": []
    },
    "result_graph_hash": "d05233563410291c9a418546a0a0cf06850a5a14fdc92a87c32bebc71415c63f"
  }
}
```

注意：

- `graph_json` 必须是前端应用 patch 后的完整 VNGraph，不是 patch 本身。
- 后端确认时会重新基于基础 revision 重放 `vngraph_patch`。如果前端提交的
  `graph_json` 和后端重放结果不同，会返回 `vngraph.derivation_invalid`。
- `confirm_vngraph_patch` 可以直接传 `graph_json/result_graph_hash`，也可以传
  `frontend_tool_call_id` 让 Agent 从前端工具结果缓存里读取。

### 打断当前 turn

`POST /api/server-agent/threads/{thread_id}/turns/{turn_id}/cancel`

用于用户显式点击停止。SSE 断开不会触发打断。

返回：

```json
{
  "thread_id": "thread_xxx",
  "turn_id": "turn_xxx",
  "cancelled": true,
  "status": "running",
  "stop_reason": null,
  "message": "Agent turn 已请求打断，完成状态请通过 SSE 订阅读取。"
}
```

说明：

- 如果 turn 正在运行，接口只表示已请求取消；最终状态通过 SSE 的 `turn.failed` 返回。
- 打断完成事件统一是 `turn.failed`，`stop_reason` 为 `interrupted`。
- 模型流式输出中打断时，已经推给前端的 `agent_message.delta` 不会落成一条完整 assistant message；刷新恢复后这段半截输出允许丢失。
- 工具调用阶段打断时，后端会补一条失败的 tool result message，避免恢复后出现孤立的 assistant tool_call。

### 查询 Agent 快照

`GET /api/server-agent/threads/{thread_id}`

用于调试和页面恢复，返回当前 Agent 快照。

- `messages`：当前模型活跃窗口，不是数据库全量审计历史。上下文压缩后，这里会是 `system + context_compaction 摘要 + 最近窗口`。
- `events`：前端事件历史，用于 UI 恢复。
- 服务重启后，后端会从数据库恢复 thread；如果发现上一次 `running` turn 没有正常结束，会补 `turn.failed + stop_reason=interrupted`。

### 删除 Agent

`DELETE /api/server-agent/threads/{thread_id}`

软删除 Agent thread。内存对象会被移除，数据库记录标记为 deleted。

## 顶层事件

当前前端协议只保留这些事件：

| 事件 | 含义 |
| --- | --- |
| `thread.started` | Agent thread 创建完成 |
| `turn.started` | 一次用户输入开始执行 |
| `turn.completed` | turn 正常结束 |
| `turn.failed` | turn 异常结束或被打断。被打断时 `stop_reason=interrupted` |
| `item.started` | 一个展示对象开始 |
| `item.updated` | 一个展示对象发生中间状态更新 |
| `item.completed` | 一个展示对象结束 |
| `agent_message.delta` | Agent 回复文本增量 |
| `error.occurred` | SSE 流上的不可恢复错误 |

`agent_message` 的 `phase` 与 Codex 协议一致：

- `commentary`：本轮中间说明，后面还会继续调用工具或产生其他回复。
- `final_answer`：本轮最终答复。

前端不要根据“后面是否出现工具调用”反推消息类型。phase 由 Agent 的实际流程统一填写：
继续执行工具是 `commentary`，结束 turn 是 `final_answer`。模型开始流式输出时还不能确定后续
是否有工具调用，因此 `item.started` 不携带 `phase`，`item.completed` 一定给出最终 `phase`。
`agent_message.delta` 通过 `item_id` 归入对应消息，不重复携带 `phase`。

### Phoenix 事件

除 `agent_message.delta` 数量过多不重复记录外，上述前端事件会保持原事件名写入 Phoenix
的 `agent.run` span，便于后端按前端实际收到的顺序排查问题。事件对象会展开为
`item.id`、`item.phase` 等点路径属性；对象数组保留为当前字段的 JSON，不再套
下标路径。同时保留不截断的 `ifline.payload_json` 原始事件，作为完整结构回退。
后端额外产生的模型请求、流式处理、工具执行和上下文压缩等观测事件统一使用
`backend.` 前缀，并归入相应的模型或工具 span，例如：

- `item.completed`：前端事件，`item.phase` 可判断中间说明或最终答复。
- `backend.model.request.started`：后端模型请求观测事件。
- `backend.agent_message.stream.completed`：后端流式响应摘要事件。
- `backend.tool.call.finished`：后端工具执行完成事件。

## Item 类型

### Agent 回复

开始：

```json
{
  "type": "item.started",
  "item": {
    "id": "msg_xxx",
    "type": "agent_message",
    "text": "",
    "status": "in_progress"
  }
}
```

流式文本：

```json
{
  "type": "agent_message.delta",
  "item_id": "msg_xxx",
  "delta": "这一章已经存在"
}
```

完成：

```json
{
  "type": "item.completed",
  "item": {
    "id": "msg_xxx",
    "type": "agent_message",
    "text": "这一章已经存在，正文长度为 4861 字。",
    "phase": "final_answer",
    "status": "completed"
  }
}
```

### 工具调用

开始：

```json
{
  "type": "item.started",
  "item": {
    "id": "call_xxx",
    "type": "tool_call",
    "name": "get_chapter_content_status",
    "arguments": {
      "project_id": 1,
      "chapter_index": 1
    },
    "status": "in_progress"
  }
}
```

进度：

```json
{
  "type": "item.updated",
  "item": {
    "id": "call_xxx",
    "type": "tool_call",
    "name": "run_chapter_workflow",
    "arguments": {
      "project_id": 1,
      "chapter_index": 1
    },
    "progress": {
      "stage": "content.done"
    },
    "status": "in_progress"
  }
}
```

完成：

```json
{
  "type": "item.completed",
  "item": {
    "id": "call_xxx",
    "type": "tool_call",
    "name": "get_chapter_content_status",
    "arguments": {
      "project_id": 1,
      "chapter_index": 1
    },
    "result": {
      "exists": true,
      "content_length": 4861
    },
    "status": "completed"
  }
}
```

### 上下文压缩

上下文压缩按 Codex CLI v2 的方式对齐为 item 生命周期，不再要求前端理解一套额外的业务事件。

开始：

```json
{
  "type": "item.started",
  "item": {
    "id": "cmp_xxx",
    "type": "context_compaction",
    "text": "正在压缩上下文",
    "status": "in_progress",
    "tokens_before": 118000,
    "trigger_tokens": 100000,
    "keep_recent_tokens": 24000,
    "is_split_turn": true
  }
}
```

完成：

```json
{
  "type": "item.completed",
  "item": {
    "id": "cmp_xxx",
    "type": "context_compaction",
    "text": "上下文已压缩",
    "status": "completed",
    "summary_message_index": 38,
    "first_kept_message_index": 12,
    "turn_start_message_index": 8,
    "turn_prefix_message_count": 3,
    "summarized_message_count": 11,
    "tokens_before": 118000,
    "tokens_after_estimate": 24000
  }
}
```

### 错误

```json
{
  "type": "item.completed",
  "item": {
    "id": "err_xxx",
    "type": "error.occurred",
    "message": "Agent 执行失败: ...",
    "status": "failed"
  }
}
```

### 打断

```json
{
  "type": "turn.failed",
  "thread_id": "thread_xxx",
  "turn_id": "turn_xxx",
  "seq": 12,
  "turn_seq": 10,
  "stop_reason": "interrupted",
  "error": "用户已打断本轮 Agent 执行"
}
```

如果打断发生在工具调用阶段，前面还会收到该工具的失败完成事件：

```json
{
  "type": "item.completed",
  "item": {
    "id": "call_xxx",
    "type": "tool_call",
    "name": "get_chapter_content_status",
    "arguments": {
      "project_id": 1,
      "chapter_index": 1
    },
    "result": {
      "ok": false,
      "interrupted": true,
      "error": "用户已打断本轮 Agent 执行"
    },
    "status": "failed"
  }
}
```

## 前端接入顺序

1. 登录或创建游客会话，拿到 cookie。
2. `POST /api/server-agent/threads` 创建 thread，保存返回的 `thread_id`。
3. 立刻打开 `GET /api/server-agent/threads/{thread_id}/events`。
4. 用户输入时调用 `POST /api/server-agent/threads/{thread_id}/turns/resume`。
5. 前端只根据 SSE 更新界面，不等待 resume 接口返回生成内容。
6. 如果收到 `tool_call.status=waiting_input`，调用 `POST /api/server-agent/threads/{thread_id}/tool-calls/{tool_call_id}/result` 回传前端工具结果。
7. 断线后先用 `events/page` 补历史，再用最后处理的 `seq` 作为 `after_seq` 重连 SSE。
8. 用户点击停止时调用 `POST /api/server-agent/threads/{thread_id}/turns/{turn_id}/cancel`，不要通过关闭 SSE 实现停止。


## 前端展示建议

- 按 `item.id` 聚合展示卡片。
- `agent_message.delta` 只追加到 `item_id` 对应的 `agent_message.text`。
- `agent_message.phase=commentary` 展示为中间探索消息；`phase=final_answer` 展示为本轮最终答复。
- `tool_call` 的 `item.started` 建卡片，`item.updated` 更新进度，`item.completed` 展示结果。
- `context_compaction` 的 `item.started` 建一条“正在压缩上下文”系统提示，`item.completed` 标记完成或失败。
- 顶层 `turn.completed` 只表示本轮结束，不直接展示为聊天气泡。
- 顶层 `turn.failed + stop_reason=interrupted` 表示本轮被打断。前端可以把当前 turn 中仍在 `in_progress` 的 item 标成已中断。
- `AgentTraceEvent` 只用于调试面板或 Phoenix 对照，不给普通产品 UI 使用。
