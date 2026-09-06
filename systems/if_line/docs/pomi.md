# Pominis Agent 交互与 if_line 差距整理

本文基于当前 Pominis VN Studio 页面、前端打包 JS、以及 `if_line` 当前代码整理。Pominis 后端源码不可见，后端行为来自接口实测、前端调用路径和 SSE 事件反推。

## 1. Pominis Agent 交互 API

Pominis 的 VN Studio 不是单纯调用“生成接口”，而是围绕 `project -> session -> run -> event` 组织。前端打开项目时先获得一个 runtime session，之后所有对话、编辑器动作、保存草稿都围绕 session 进行。

### 1.0 ID 层级：project / session / run / tool

Pominis 这里最容易混淆的是 `sessionId` 和 `runId`。它们不是同一个东西：

| 层级 | Pominis 名称 | if_line 可对应概念 | 语义 |
|---|---|---|---|
| 作品 | `projectId` / `storyId` | `project_id` / VNGraph 所属项目 | 一个作品或项目，是持久化编辑对象。 |
| Agent 会话 | `sessionId` | 当前 `agent_id` 更接近这一层 | 打开某个作品后建立的 Agent 运行会话，承载消息、工具历史、当前远端草稿和生命周期。 |
| 消息线程 | `conversationId` | 可选的 `conversation_id` | 对话历史归属，Pominis 会把它和 session 一起返回。if_line 当前还没有单独拆这一层。 |
| 一次执行 | `runId` | 建议新增 `run_id` | 用户发一次消息、点一次编辑器 AI 动作、继续一次工具决策，都应该形成一次 run。run 有自己的状态、错误、开始结束时间和事件序号。 |
| 工具调用 | `toolUseId` / tool use id | `tool_call_id` | 某个 run 内部的一次工具调用，用于把 `tool_use`、`tool_result` 和前端卡片对应起来。 |
| 事件序号 | `seq` | 建议新增 `seq` | 某个 run 内单调递增的事件序号，用于断线后 `afterSeq` 补拉和前端去重。 |

所以，`sessionId` 可以近似理解成 if_line 现在的 `agent_id`，但 `runId` 不应该和 `agent_id` 合并。`agent_id/sessionId` 表示“这个 Agent 会话是谁”，`runId` 表示“这一次 Agent 正在做哪件事”。

`runId` 的价值主要有四个：

- 前端可以查询某次执行的状态，而不是只知道整个 Agent 当前状态。
- 前端可以取消 active run，不必销毁整个 Agent session。
- SSE 可以按 `run_id + seq` 去重，避免刷新、重连、并发动作导致旧事件混进当前 UI。
- 断线后可以用 `events?afterSeq=N` 只补拉某个 run 的缺失事件。

如果 if_line 后续补这个模型，建议保留 `agent_id` 作为长生命周期会话 ID，再新增 `run_id` 作为每次 `resume` 或每次前端触发 Agent 动作产生的执行 ID。

### 1.1 项目与 Session

| 方法 | 路径 | 语义 | 行为 |
|---|---|---|---|
| `POST` | `/api/vn-agent/projects` | 创建 VN Agent 项目 | 返回项目/故事标识，供 Studio 打开。 |
| `POST` | `/api/vn-agent/projects/{projectId}/open` | 打开项目并创建/恢复运行态 session | 请求体包含 `provider`、`model`。返回 `sessionId`、`conversationId`、`storyId`、`readOnly`、`studioAccess`、`sessionLifecycle`。 |
| `GET` | `/api/vn-agent/projects/{projectId}/sharing` | 读取 Studio 分享设置 | 返回当前访问权限。 |
| `PATCH` | `/api/vn-agent/projects/{projectId}/sharing` | 更新 Studio 分享设置 | 请求体为 `{ studioAccess }`。 |
| `GET` | `/v1/stories/public/{storyId}/creation-process` | 公开作品创作过程 | 用于只读公开页，不是主编辑 session。 |

### 1.2 对话与 Run

| 方法 | 路径 | 语义 | 行为 |
|---|---|---|---|
| `POST` | `/api/vn-agent/sessions/{sessionId}/messages` | 向 Agent 发送用户消息 | 请求体为 `{ message, attachments?, persistMessage? }`。响应是 `text/event-stream`，前端直接从本次响应体读取 SSE。 |
| `GET` | `/api/vn-agent/sessions/{sessionId}/runs/{runId}` | 查询 run 状态 | 返回 `status`、`error`、`lastSeq`、开始/结束时间等。状态可为 `running/succeeded/failed/cancelled/interrupted`。 |
| `GET` | `/api/vn-agent/sessions/{sessionId}/runs/{runId}/events?afterSeq=N` | 订阅或补拉某个 run 的事件 | 用 `afterSeq` 做断点续读。前端按 `run_id + seq` 去重。 |
| `DELETE` | `/api/vn-agent/sessions/{sessionId}/runs/active` | 取消当前活跃 run | 前端取消生成时调用。 |

`messages` 接口返回的不是 WebSocket，也不是浏览器原生 `EventSource`。前端使用：

```js
const reader = response.body.getReader()
const decoder = new TextDecoder()
// 累积文本，按 "\n\n" 切块，读取 "data: " 行，JSON.parse
```

事件流实际是 SSE 格式，但前端只依赖 `data:`，不依赖 `event:` 字段。

### 1.3 Session 状态与编辑器动作

| 方法 | 路径 | 语义 | 行为 |
|---|---|---|---|
| `GET` | `/api/vn-agent/sessions/{sessionId}` | 读取当前 session 状态 | 返回顶层 `title`、`characters`、`chapters`、`messages`、`toolHistory`、`startupBrief`、`sessionLifecycle` 等。 |
| `PATCH` | `/api/vn-agent/sessions/{sessionId}` | 保存编辑器草稿或写入用户选择 | 常见请求体为 `{ title, characters, chapters }`，也可写入 startup brief / user decision 结果。 |
| `POST` | `/api/vn-agent/sessions/{sessionId}/undo` | 回滚上一次 Agent checkpoint | 返回 restored checkpoint，前端刷新 session 并追加 undo 提示。 |
| `POST` | `/api/vn-agent/sessions/{sessionId}/editor-actions?async=1` | 触发编辑器内 AI 动作 | 请求体为 action 对象。返回 `runId`，前端再查 run 状态和事件。 |

编辑器动作和普通聊天不同：它通常不是直接从 `POST` 响应持续读完整 SSE，而是先拿 `runId`，再通过 `runs/{runId}` 和 `events?afterSeq=` 追踪。

### 1.4 资源相关 API

这些不是 Agent 主协议，但被 VN Studio 编辑器和 tool 结果使用：

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/v1/images/upload` | 申请图片上传。 |
| `POST` | `/v1/images/upload-base64` | 上传 base64 图片。 |
| `GET` | `/v1/upload/presign?path=...` | 申请对象存储 presigned URL。 |
| `POST` | `/v1/image-gen/jobs` | 创建图片生成任务。 |
| `GET` | `/v1/image-gen/jobs?jobId=...` | 查询图片生成任务。 |
| `POST` | `/v1/video/generate` | 创建视频生成任务。 |
| `GET` | `/v1/video/generate?jobId=...` | 查询视频生成任务。 |

## 2. Pominis SSE 事件

Pominis 事件核心字段：

```json
{
  "run_id": "uuid",
  "seq": 1,
  "type": "text"
}
```

前端按 `run_id` 保存最大 `seq`，收到重复或旧事件会跳过。断线后用 `events?afterSeq=N` 继续。

| 事件 | 语义 | 前端行为 |
|---|---|---|
| `run_start` | run 已创建并开始执行 | 记录 run id，进入 streaming/running 状态。 |
| `text` | assistant 文本增量 | 追加到当前 assistant message。 |
| `tool_use` | 后端开始或更新工具调用 | 创建/更新 tool card，展示工具名、步骤、状态、输入。 |
| `tool_result` | 后端工具完成 | 标记 tool card done，必要时刷新 session。 |
| `context_compaction_start` | 后端开始压缩上下文 | 前端显示“整理上下文”类 activity。 |
| `context_compaction_done` | 上下文压缩完成 | 移除 activity。 |
| `done` | run 正常完成 | 清理 running tool，刷新 session。 |
| `error` | run 失败 | 清理 running 状态，显示错误；如果是余额/订阅错误会走专门提示。 |
| `cancelled` | 用户或系统取消 | 标记 running tool 为 cancelled。 |
| `interrupted` | 后端中断，可能是达到轮数或被新 run 打断 | 标记 interrupted，可追加后端 message/reason。 |

### 2.1 Pominis 事件订阅、防丢、防重

Pominis 的事件流不是 WebSocket，而是 HTTP SSE 风格响应。前端有两种读取路径：

| 场景 | 读取方式 | 语义 |
|---|---|---|
| 普通聊天 `POST /messages` | 直接读取本次 `fetch` 响应体的 `response.body.getReader()` | 用户发消息后，后端边执行边把 run 事件写到这次 HTTP 响应里。 |
| 异步编辑器动作 `editor-actions?async=1` | 先返回 `runId`，再请求 `runs/{runId}/events?afterSeq=N` | 动作先创建 run，前端再按 run 订阅事件，支持从指定序号之后补拉。 |

前端解析流的方式是：

```js
const reader = response.body.getReader()
const decoder = new TextDecoder()
// 累积 chunk 文本
// 按 "\n\n" 切出 SSE block
// 读取 data: 行
// 跳过 [DONE]
// JSON.parse(data)
```

每个事件里关键字段是：

```json
{
  "run_id": "uuid",
  "seq": 12,
  "type": "tool_result"
}
```

防重逻辑在前端完成：前端维护一个“每个 run 已处理到的最大 seq”表，可以理解成：

```js
lastSeqByRun[run_id] = maxSeq
if (event.seq <= lastSeqByRun[run_id]) {
  // 旧事件或重复事件，跳过
}
lastSeqByRun[run_id] = event.seq
```

防丢逻辑依赖后端保留 run 事件，并开放补拉接口：

```text
GET /api/vn-agent/sessions/{sessionId}/runs/{runId}/events?afterSeq=N
```

如果前端已经处理到 `seq=12`，重连时就从 `afterSeq=12` 继续拉。这样不会像“重新订阅整个 session 事件流”那样把所有历史事件再推一遍，也不会因为只订阅 live stream 而丢掉断线期间的事件。

这个设计的关键点是：`seq` 是 run 内序号，不是全局 session 序号。一个 run 失败、取消、完成后，它的事件边界很清楚；前端 UI 也能知道某个 `tool_use/tool_result/error/done` 到底属于哪一次用户请求。

我实测发送一次不持久化消息时，后端因为余额不足返回：

```text
run_start -> error
error = Insufficient credits. VN agent requires at least 10 credits to start.
```

这说明消息接口本身确实建立了 SSE，失败由后端 run 层返回。

## 3. Pominis Toolcall 设计

Pominis 的 toolcall 是后端执行，前端负责显示和少量用户输入交互。前端不会真的执行 `GenerateScenes`、`PatchState` 这类工具，它只消费 `tool_use/tool_result` 事件并渲染 tool card。

前端识别到的工具名包括：

| 工具 | 作用分类 |
|---|---|
| `WebSearch`、`WebFetch` | 联网研究。 |
| `UpdateAgentPlan` | 更新 Agent 内部计划。 |
| `ConfigureStartupBrief` | 配置启动参数，可能需要用户确认。 |
| `RequestUserDecision` | 请求用户做分支选择。 |
| `AttachReferenceFiles` | 附加参考文件。 |
| `SelectImageStyle` | 选择图像风格。 |
| `InspectStoryState`、`SearchState`、`ReadState`、`ViewAsset` | 读取/检查故事状态或资源。 |
| `GenerateNarrative` | 生成故事大纲。 |
| `ContinueStory` | 续写故事。 |
| `EditDialogues` | 修改对白。 |
| `EditStoryState`、`PatchState` | 修改故事结构/状态。 |
| `GenerateCharacters`、`ReviseCharacter` | 生成或修订角色。 |
| `GenerateCharacterPortraits` | 生成角色立绘。 |
| `GenerateScenes`、`RewriteChapterDialogue` | 生成或重写场景对白。 |
| `GenerateImagePrompts` | 生成图片提示词。 |
| `GenerateSceneImages`、`GenerateSceneVideos` | 生成场景图片/视频。 |
| `PatchNodeGraph` | 修改节点图。 |
| `RegenerateNodeAssets` | 刷新节点资源。 |
| `AssignBGM`、`AssignSceneMotion` | 配乐和场景运动。 |
| `ValidateStory` | 校验故事结构。 |
| `SaveStory` | 保存故事。 |

需要用户参与的工具，例如 `ConfigureStartupBrief` 和 `RequestUserDecision`，前端会显示输入卡片。用户提交后前端先 `PATCH /sessions/{sessionId}` 写入确认结果，再发送一条隐藏 follow-up message，通常带 `persistMessage:false`，让后端继续 run。

## 4. Pominis 远端状态、本地草稿与冲突处理

Pominis 编辑器有两个状态：

```js
K = 从后端 sessionState 派生的已保存快照
G = 编辑器本地 draft
```

dirty 判断是纯前端比较：

```js
JSON.stringify({ title: G.title, characters: G.characters, chapters: G.chapters })
!== 
JSON.stringify({ title: K.title, characters: K.characters, chapters: K.chapters })
```

保存时：

```http
PATCH /api/vn-agent/sessions/{sessionId}
Content-Type: application/json

{
  "title": "...",
  "characters": [...],
  "chapters": [...]
}
```

我实测标题临时改名再还原，服务端会立即接受并在下一次 `GET session` 中返回新值。

Pominis 没看到复杂的三方 merge、etag、baseVersion 或 revision guard。它的主要冲突规避方式是：

1. 用户在编辑器里修改字段，前端只改本地 `G`，形成 dirty。
2. 用户点击编辑器内 AI 动作时，前端先 silent save 当前 `G`。
3. 保存成功后才调用 `editor-actions?async=1`。
4. 后端 Agent 基于已保存 session 修改。
5. run 完成后前端刷新 `GET session`。
6. 新的 sessionState 派生出新 `K`。
7. `useEffect` 把 `G` 重置为 `K`，dirty 清零。

所以它不是靠合并解决冲突，而是靠“编辑器 AI 动作前强制保存”减少冲突窗口。

仍然存在的风险：如果用户有本地 dirty，又直接从聊天框要求 Agent 改故事，而聊天发送路径没有先保存本地 draft，后端可能基于旧 session 修改；run 完成刷新后，本地 `G` 会被新的 `K` 覆盖，未保存编辑可能丢失。

### 4.1 Pominis Patch 执行边界

Pominis 的 patch 更像是后端工具直接修改远端 story state。前端从 `tool_use` 事件里拿到工具名、输入摘要和状态，用 tool card 展示；等 `tool_result` 后再刷新 session。

前端能看到的 patch 类工具包括：

| 工具 | 前端能确认的输入形态 | 推断语义 |
|---|---|---|
| `PatchState` | `input.edits` 数组，前端只展示 `N edits` | 通用 story state patch。 |
| `EditDialogues` | `input.operations` 数组，前端只展示 `N operations` | 专门对白 patch。 |
| `EditStoryState` | `chapterId`、`targetChapterIds`、`targetNodeIds` | 结构级故事状态重构。 |
| `PatchNodeGraph` | 前端仅展示工具名 `Adjust node graph` | 节点图拓扑调整，例如节点连接、分支、入口等。 |
| `ReadState` / `ViewAsset` | `input.path` | 按 path 读取 story state 或资源。 |
| `SearchState` | `input.query` | 搜索 story state。 |

当前前端包里没有暴露 `PatchState.edits[]` 或 `PatchNodeGraph` 的完整后端 schema。当前项目的 `toolHistory` 也没有真实 `PatchState` / `PatchNodeGraph` 样本，所以只能确认执行边界和大致形态，不能确认每个 patch operation 的字段枚举。

重要区别：

```text
Pominis:
Agent 后端工具直接 patch 后端状态
前端展示 tool card
前端刷新 session 得到新状态

if_line 设计:
Agent 后端只生成 patch 请求
前端基于当前 localDraft apply patch
前端执行 VNGraph 校验
前端把 tool_result 回传给 Agent
最终是否保存由前端决定
```

这个差异对 VNGraph 很关键。VNGraph 的真实编辑状态通常在前端画布里，且校验逻辑也在前端更贴近交互语义。如果改成 Pominis 那种后端直接 patch，就需要后端也维护一套 VNGraph patch apply 和校验逻辑，容易出现两份规则不一致。

## 5. if_line 当前实现

### 5.1 已有后端 Agent 雏形

`if_line` 已有一个内存态服务端 Agent：

- `backend/app/agent/server_agent.py`
- `backend/app/agent/server_agent_events.py`
- `backend/app/routers/server_agent.py`
- `backend/app/agent/toolcall/*`

它的运行模型是：

1. `ServerAgentManager.create_agent()` 创建进程内 Agent。
2. Agent 保存 `messages`、`events` 和一个 async generator。
3. `resume()` 把用户输入送进 generator。
4. 模型返回 assistant 或 tool_calls。
5. 后端执行 toolcall。
6. 如果工具需要前端草稿，就发 `EEvent_tool_input_request` 并 stop，等待前端回传 `tool_result`。

当前定义的事件：

| 事件 | 语义 |
|---|---|
| `EEvent_stop` | Agent 停止并等待用户或工具结果。 |
| `EEvent_start` | 收到用户输入，开始处理。 |
| `EEvent_assistant_reply` | 模型 assistant message。 |
| `EEvent_tool_input_request` | 后端要求前端执行某个工具，例如应用 VNGraph patch 或上传当前草稿。 |
| `EEvent_tool_result` | 工具执行完成，结果已写回模型上下文。 |
| `EEvent_message` | SSE 默认 event 类型兜底。 |

当前状态：

| 状态 | 语义 |
|---|---|
| `EStatus_created` | 已创建。 |
| `EStatus_running` | 正在模型采样或执行工具。 |
| `EStatus_waiting_user` | 等待下一次用户输入或工具结果。 |

当前 stop reason：

| 原因 | 语义 |
|---|---|
| `EStopReason_waiting_user` | 初始或普通等待用户。 |
| `EStopReason_waiting_tool_result` | 等前端回传工具结果。 |
| `EStopReason_assistant_answer` | 模型已经给出自然语言答案。 |
| `EStopReason_max_sampling_steps` | 采样轮数达到安全上限。 |

### 5.2 if_line 当前 Agent API

`backend/app/routers/server_agent.py` 定义了：

| 方法 | 路径 | 语义 |
|---|---|---|
| `POST` | `/agents` | 创建内存 Agent。 |
| `GET` | `/agents/{agent_id}` | 获取 Agent 快照。 |
| `GET` | `/agents/{agent_id}/events?replay_existing=true` | SSE 订阅 Agent 事件。 |
| `POST` | `/agents/{agent_id}/resume` | 输入用户消息或 tool_result，推进 Agent。 |
| `DELETE` | `/agents/{agent_id}` | 删除内存 Agent。 |

但是 `backend/app/main.py` 当前只挂载到了：

```python
app.include_router(auto_agent.router, prefix="/api/auto-agent", tags=["auto-agent"])
```

没有看到：

```python
app.include_router(server_agent.router, prefix="/api/server-agent", tags=["server-agent"])
```

因此 `server_agent` 路由目前虽然写了，但默认并没有对外注册。

### 5.3 if_line SSE 行为

`server_agent.py` 的 SSE 是标准 `event:` 模式：

```text
id: {event_index}
event: {event.type}
data: {完整 event JSON}
```

订阅时会先推：

```text
: subscribed
```

空闲 15 秒推：

```text
: keepalive
```

和 Pominis 相比：

- if_line 有全局 `event_index`，但没有按执行拆分的 `run_id`。
- if_line 没有 run 内单调递增的 `seq` 字段。
- if_line 没有 `afterSeq` 补拉参数。
- if_line 的 replay 是 `replay_existing=true/false`，不能从某个事件序号后恢复。
- if_line 事件挂在 Agent 上，不挂在 run 上。

这意味着 if_line 当前的 `agent_id` 更像 Pominis 的 `sessionId`，还缺 Pominis 的 `runId` 这一层。现在一次用户输入、一次工具回传、一次后端继续执行，都会继续往同一个 Agent 事件列表里追加事件；前端无法天然区分“这条事件属于哪一次用户请求”。

### 5.3.1 if_line 当前防丢、防重能力

if_line 当前事件链路是：

```text
_emit_event()
  -> event["event_index"] = len(self.events)
  -> self.events.append(event)
  -> event_bus.publish(event)
  -> SSE 输出 id/event/data
```

当前已经具备的能力：

- 有内存事件历史：`self.events` 保存 Agent 生命周期内所有事件。
- 有事件编号：`event_index` 是 Agent 内递增编号。
- 有 SSE `id:`：输出时把 `event_index` 写成 SSE id。
- 有历史 replay：订阅 `/agents/{agent_id}/events?replay_existing=true` 时，会先把 `event_history` 塞进订阅队列。
- 有 keepalive：15 秒没有事件时推 `: keepalive`，防止连接长时间静默。
- 有慢消费者保护：每个订阅者队列 `maxsize=256`，实时发布时队列满了会丢掉最老事件再塞新事件，避免 Agent 被慢连接拖住。

当前不足：

- 路由还没挂到 `main.py`，所以这套 SSE 默认没有真正对外生效。
- 只有 `replay_existing=true/false`，没有 `after_event_index` 或 `afterSeq`，重连不能从某个编号后精确续订。
- SSE 写了 `id:`，但服务端没有读取浏览器重连带来的 `Last-Event-ID` 请求头。
- 如果 `replay_existing=true`，重连会重放整个 Agent 历史，前端必须自己用 `event_index` 去重；但当前前端还没接这套 `server_agent` 事件流。
- 如果 `replay_existing=false`，断线期间事件会直接丢。
- 每个订阅者队列只有 256 个槽，实时慢消费者会被丢旧事件；历史 replay 时如果历史事件超过 256 条，当前 `put_nowait` 没有兜底，订阅阶段也可能失败。
- 所有事件挂在 Agent 上，不挂在 run 上；同一个 Agent 多次 `resume` 的事件混在一条历史里。
- Agent 事件只在内存里，进程重启后无法补拉。

所以 if_line 现在是“轻量内存事件总线 + 可全量 replay”，不是 Pominis 那种“run 级事件日志 + afterSeq 精确补拉”。如果只是本地开发和短连接调试，这个实现够用；如果要做稳定 Agent 面板，就需要补 `run_id`、`seq`、精确续订和前端去重。

### 5.4 if_line 当前 toolcall

if_line 当前工具分四组：

| 工具组 | 工具 |
|---|---|
| 项目读取 | `view_project` |
| VNGraph 读取/规则 | `get_vngraph_editing_rules`、`get_current_vngraph`、`get_vngraph_node_tool_design` |
| VNGraph 前端执行 | `request_frontend_vngraph_draft`、`apply_vngraph_patch`、`create_vngraph_nodes`、`connect_vngraph_nodes`、`update_vngraph_node_fields` |
| 资源 | `request_image_generation`、`generate_image_resource`、`request_voice_generation`、`generate_voice_resource`、`generate_audio_resource`、`request_background_generation`、`generate_background_resource` |
| 章节正文 | `get_chapter_content_status`、`generate_chapter_content_for_node` |

if_line 的设计和 Pominis 有一个明显不同点：  
Pominis 的故事状态 patch 多数在后端直接完成，前端主要展示；if_line 的 VNGraph patch 设计成“后端只生成 patch，前端基于当前画布草稿应用 patch，再把 tool_result 回传”。

这对解决本地 dirty 有帮助，因为后端不直接覆盖前端画布。当前后端提示里已经明确写了乐观策略：

1. 默认认为 Agent 操作期间用户没有手动改画布，不要每次 patch 前都同步前端草稿。
2. 先调用 `apply_vngraph_patch`，让前端基于当前画布草稿应用 patch。
3. 如果前端返回 `ok=false`、`patch_errors` 或 `validation.errors`，再调用 `request_frontend_vngraph_draft` 读取最新前端草稿。
4. Agent 基于最新草稿重新生成 patch，再次调用 `apply_vngraph_patch`。

因此 if_line 的后端设计不是缺少冲突方案，而是已经选择了“乐观 patch + 失败后读取前端草稿”的方案。目前缺的是前端完整闭环：订阅 tool input request、应用 patch、校验、回传 tool_result。

### 5.5 if_line 当前远端/本地草稿操作

当前 if_line 普通 VNGraph 页面是直接：

1. `GET /api/projects/{projectId}/chapters/{chapterIndex}/vn-graph`
2. `POST /generate-vn-graph` 或 `POST /generate-vn-graph-llm`
3. 把返回的 `graph_json` 放进前端 `vnGraph.value`

代码里没有看到 Pominis 那种：

- session 级 draft。
- 本地 `savedSnapshot` / `draft` 双状态。
- dirty 比较。
- silent save before AI。
- run 完成后按 session 刷新。
- 冲突提示或 base revision。

后端 VNGraph 模型只有 `graph_json`、`status`、`updated_at`，没有专门的 revision 字段。`ChapterContent` 有 `version`，但 VNGraph 没有类似版本控制。

## 6. if_line 缺失的 Agent 功能

下面是对照 Pominis 后，if_line 当前缺失或未闭环的功能。

### 6.1 路由与会话生命周期

- 缺少挂载 `server_agent.router`，当前后端 Agent API 默认不可访问。
- 缺少 Project open/session 概念：Pominis 有 `project.open -> sessionId`，if_line 现在是单独创建内存 Agent，和项目页生命周期没有绑定。
- 缺少 session 持久化：if_line Agent 在进程内，重启丢失；Pominis session 可通过项目恢复。
- 缺少 `sessionLifecycle`：没有 active run id、run kind、run status、startedAt 等统一字段。
- 缺少 readOnly/studioAccess 语义：Pominis 打开项目就知道是否只读，if_line Agent 路由目前也没有权限依赖。

### 6.2 Run 与 SSE

- 缺少 run 对象：if_line 事件属于 agent，不属于 run。
- 缺少 `run_id`：当前 `agent_id` 只能标识长生命周期 Agent 会话，不能标识一次具体执行。
- 缺少单次 run 内的单调递增 `seq`。
- 缺少 `events?afterSeq=N` 断线续订。
- 缺少按 run 查询状态的接口。
- 缺少取消 active run 的接口。
- 缺少 `context_compaction_start/context_compaction_done`。
- 缺少 Pominis 风格的 `tool_use` 事件，当前只有 assistant message 里的 tool_calls 和 `EEvent_tool_result`。
- 缺少统一错误事件规范：Pominis 所有 run 失败都落到 `error` 事件和 run status；if_line 现在更多依赖 HTTP 500 或 `EEvent_stop`。

### 6.3 前端 Agent 体验

- 缺少前端 `serverAgentApi`。
- 缺少前端 EventSource/fetch stream 订阅。
- 缺少聊天面板。
- 缺少消息队列。
- 缺少 tool card 展示。
- 缺少 tool running/done/error/cancelled 状态映射。
- 缺少 `RequestUserDecision` 类输入卡片。
- 缺少 `ConfigureStartupBrief` 类启动参数确认卡片。
- 缺少余额/订阅/权限错误的专门前端提示。

### 6.4 草稿与冲突控制

- 缺少前端 `savedSnapshot` / `localDraft` 双状态。
- 缺少 dirty 判断。
- 缺少用户手动保存和 discard。
- 缺少前端 `apply_vngraph_patch` 执行器：基于当前 localDraft 应用 patch、校验、更新 localDraft、回传 tool_result。
- 缺少前端 `request_frontend_vngraph_draft` 执行器：把当前 localDraft 上传给 Agent 作为重算 patch 的上下文。
- 缺少 run 完成后如何处理 localDraft 和 savedSnapshot 的规则。
- 缺少 base revision / graph hash 保存检查。虽然 `get_current_vngraph` 返回 `graph_hash`，但保存/patch 合并没有强制使用它。
- 缺少冲突 UI：当用户本地 dirty，同时远端发生变化时，没有保留本地、采用远端、手动合并等选择。
- 不建议照搬 Pominis 的“AI 前 silent save 到远端”作为 VNGraph 默认策略；if_line 当前“前端 localDraft apply patch，最后用户决定保存”的语义更适合图编辑器。

### 6.5 Toolcall 能力覆盖

if_line 已有 VNGraph 和资源工具，但相对 Pominis 仍缺：

- 故事状态级工具：`InspectStoryState`、`SearchState`、`ReadState`、`PatchState`。
- Agent 计划工具：`UpdateAgentPlan`。
- 用户决策工具：`RequestUserDecision`。
- 启动参数工具：`ConfigureStartupBrief`。
- 文件/参考资料工具：`AttachReferenceFiles`。
- 风格选择工具：`SelectImageStyle`。
- 叙事生成工具：`GenerateNarrative`、`ContinueStory`。
- 角色生成/修订工具：`GenerateCharacters`、`ReviseCharacter`、`GenerateCharacterPortraits`。
- 场景生成工具：`GenerateScenes`、`RewriteChapterDialogue`、`GenerateImagePrompts`。
- 资源编排工具：`GenerateSceneImages`、`GenerateSceneVideos`、`RegenerateNodeAssets`。
- 音乐/运动工具：`AssignBGM`、`AssignSceneMotion`。
- 校验和保存工具：`ValidateStory`、`SaveStory`。
- Web 研究工具：`WebSearch`、`WebFetch`。

### 6.6 后端 Agent 工程能力

- 缺少 checkpoint/undo。
- 缺少 toolHistory 持久化。
- 缺少 messages 持久化。
- 缺少 prompt attachment/history。
- 缺少 active run 恢复。
- 缺少 run 级错误落库。
- 缺少队列化消息处理。
- 缺少上下文压缩。
- 缺少多模型 provider/model 选择。
- 缺少按项目权限隔离 Agent。

## 7. 建议 if_line 的落地顺序

优先级建议如下：

1. 先挂载并保护 `server_agent.router`，加上登录态和项目权限校验。
2. 增加前端 `serverAgentApi`：create、get、resume、events、delete。
3. 在 `agent_id` 之下补 `run_id`：每次用户消息、编辑器 AI 动作、工具结果继续执行，都创建或推进一个明确 run。
4. 把 SSE 协议改成 Pominis 风格的 run 级事件：`run_id + seq + type`。
5. 增加 `GET /agents/{agent_id}/runs/{run_id}` 和 `GET /agents/{agent_id}/runs/{run_id}/events?after_seq=N`。
6. 增加取消接口，例如 `DELETE /agents/{agent_id}/runs/active` 或 `DELETE /agents/{agent_id}/runs/{run_id}`。
7. 前端做最小 chat panel：展示 user/assistant/text/tool_use/tool_result/error。
8. 给 VNGraph 编辑页增加 `savedGraphSnapshot`、`localGraphDraft`、`isDirty`。
9. 完成 `apply_vngraph_patch` 前端执行器：应用到当前 localDraft、执行现有 VNGraph 校验、把结果回传给 Agent。
10. 完成 `request_frontend_vngraph_draft` 前端执行器：patch 失败时把最新 localDraft 上传给 Agent 重算。
11. 给 VNGraph 保存加 `base_graph_hash` 或 `revision`，防止保存时远端覆盖本地。
12. 明确 Agent 修改只进入 localDraft，除非用户点击保存，不直接覆盖远端 VNGraph。
13. 再补 Pominis 那类故事级工具和用户决策工具。

## 8. 一句话结论

Pominis 的核心不是某个生成接口，而是一个完整的 `session + run + SSE + tool card + draft save` 协议。`sessionId` 标识长生命周期 Agent 会话，`runId` 标识一次具体执行。它的 patch 更偏后端直接修改远端状态；if_line 当前设计更适合 VNGraph：后端 Agent 只提出 patch，前端基于 localDraft 应用和校验，失败后再把最新草稿交给 Agent 重算。if_line 当前主要缺的是把这个后端设计接到真实前端工作流里，形成 `agent_id -> run_id -> seq`、toolcall UI、localDraft、patch 执行器和保存冲突检查的闭环。
