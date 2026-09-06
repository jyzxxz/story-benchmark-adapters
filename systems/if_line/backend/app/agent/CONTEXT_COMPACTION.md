# Agent 上下文压缩机制

## Pi 的机制

Pi 的上下文压缩用于解决长会话超过模型上下文窗口的问题。它的核心流程是：

1. 判断触发条件：当当前上下文 token 数超过 `contextWindow - reserveTokens` 时自动压缩。Pi 默认给模型回复预留 `reserveTokens=16384`。
2. 从最新消息往前保留一段最近上下文。Pi 默认保留 `keepRecentTokens=20000`。
3. 保留窗口之前的消息不再直接发送给模型，而是序列化成文本后交给模型生成结构化摘要。
4. 保存一个 `CompactionEntry`，里面记录摘要、`firstKeptEntryId`、压缩前 token 数等信息。
5. 下一次模型请求时，实际上下文变成：`system prompt + compaction summary + firstKeptEntryId 之后的最近消息`。

Pi 的关键设计点：

- 压缩尽量按 turn 边界切，不把工具结果孤立出来。
- 单个 turn 太长时允许 split turn，但要把 split 前缀单独摘要。
- 工具结果序列化时会截断，避免摘要请求本身继续爆上下文。
- 重复压缩时，新摘要会吸收旧摘要，继续保留最近窗口。

参考文档：

- https://pi.dev/docs/latest/compaction
- https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/compaction/compaction.ts

## 本项目当前实现

当前实现是 Pi 风格的简化版，目标是先保证可用、可审计、可观测。

### 触发时机

每次 `ServerAgent` 请求模型前都会检查当前模型上下文长度：

```text
estimated_tokens > AGENT_CONTEXT_WINDOW_TOKENS - AGENT_CONTEXT_COMPACTION_RESERVE_TOKENS
```

默认值：

```text
AGENT_CONTEXT_WINDOW_TOKENS=1000000
AGENT_CONTEXT_COMPACTION_RESERVE_TOKENS=16000
AGENT_CONTEXT_COMPACTION_KEEP_RECENT_TOKENS=20000
AGENT_CONTEXT_COMPACTION_SUMMARY_MAX_TOKENS=2000
AGENT_CONTEXT_COMPACTION_ENABLED=true
```

当前 `deepseek-v4-flash` 官方 API context length 是 1M。token 估算不是供应商精确 tokenizer，只用于触发保护。DeepSeek 文档给出的直觉比例是中文字符约 `0.6 token`、英文字符约 `0.3 token`；但长重复中文实测接近 `1 字 1 token`，所以当前实现对 CJK 字符按 `1 token` 保守估算，避免压缩触发太晚。

### 压缩后的模型窗口

压缩前：

```text
system + 历史 user/assistant/tool + 最近 user/assistant/tool
```

压缩后：

```text
system + context_compaction 摘要 + 最近 user/assistant/tool
```

摘要是一条 `system` message。发送给模型时只保留 OpenAI chat 兼容字段；数据库里会额外保存内部元信息。

### Split-turn 处理

最近窗口不是一律从 `user` 消息开始。当前实现参考 Pi 的 cut point 思路：

- 合法切点是 `user` 或 `assistant`。
- 不在 `tool` 结果上切，避免保留孤立工具结果。
- 如果切点是 `user`，说明刚好按 turn 边界保留最近窗口。
- 如果切点是 `assistant`，说明切在一个大 turn 中间；后端会把这个 turn 从 `user` 到切点前的前缀写进“当前大 Turn 前缀”摘要，切点之后的后缀仍以原文进入最近窗口。

压缩后窗口仍是：

```text
system + context_compaction 摘要 + 最近窗口原文
```

但摘要里会额外包含大 turn 前缀信息，避免模型看到后缀工具结果时缺少前因。

### 数据库表现

历史消息不会删除。`agent_messages` 会追加一条摘要消息：

```json
{
  "role": "system",
  "content": "【上下文压缩摘要】...",
  "ifline_kind": "context_compaction",
  "first_kept_message_index": 12,
  "is_split_turn": true,
  "turn_start_message_index": 8,
  "turn_prefix_message_count": 3,
  "summary_message_index": 38,
  "summarized_message_count": 11,
  "tokens_before": 118000,
  "tokens_after_estimate": 24000,
  "source": "pi-style-compaction-v2",
  "model": "deepseek-v4-flash"
}
```

恢复会话时，后端读取最近一条 `ifline_kind=context_compaction`，重建：

```text
原始 system message + 最近压缩摘要 + first_kept_message_index 之后的消息
```

### 事件与观测

前端可见事件按 Codex CLI v2 对齐：上下文压缩是一个普通 item，
用 `item.started` 和 `item.completed` 表示开始、结束。

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
    "is_split_turn": true,
    "turn_start_message_index": 8,
    "turn_prefix_message_count": 3,
    "summarized_message_count": 11,
    "persisted_message_count": 39,
    "tokens_before": 118000,
    "tokens_after_estimate": 24000,
    "summary_chars": 1200,
    "model": "deepseek-v4-flash"
  }
}
```

这些事件会进入：

- `agent_events`：用于 SSE / 前端恢复。
- `agent_trace_events`：用于本地调试查询。
- Phoenix/OTel：同时记录为 `context.compaction` 子 span 上的事件。

Phoenix Trace 里推荐按下面的结构观察：

```text
agent.run
├── context.compaction      span_kind=CHAIN
│   └── llm.chat.summary    span_kind=LLM
└── llm.chat                span_kind=LLM
```

`context.compaction` 的 input/output 会记录压缩边界和压缩前后 token 估算；
`llm.chat.summary` 记录生成摘要的模型请求，output 里包含模型原始摘要
`summary` 以及最终写入模型上下文的 `summary_message`。前端展示压缩过程时使用
`context_compaction` item。

### 当前没有做的事情

- 没有精确 tokenizer。
- 没有单独的 compaction 表。
