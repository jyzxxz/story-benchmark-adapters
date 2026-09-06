---
name: agent-toolcall-dev
description: if_line 项目 Agent ToolCall 的开发与验收规范。开发 backend/app/agent/toolcall/ 下的工具、创建异步生成类工具、使用 server_agent_client 测试控制台联调、或在 Phoenix 上验收 Agent 能力时使用。记录开发入口、不可违反的约束和验收标准。
---

# Agent ToolCall 开发与验收

本文只记录开发入口、不可违反的约束和验收标准。具体写法优先阅读同目录现有实现，不要另起一套架构。

## 1. 开发环境

- 开发分支：`agent-toolcall`
- 服务器开发目录：`/home/workspace/fengbohan/if_line`
- Phoenix：http://101.42.10.67:6006
- Phoenix 账号：`admin@localhost`
- Phoenix 密码：`if_line`

服务器当前开发目录可能存在其他人的未提交修改。**切换分支前先检查 `git status`，不要覆盖或清理他人的工作。**

## 2. ToolCall 开发入口

主要代码位置：

- 工具实现：`backend/app/agent/toolcall/`
- 工具统一注册：`backend/app/agent/toolcall/__init__.py`
- Agent 规格与工具调度：`backend/app/agent/agent_spec.py`
- 故事 Agent 提示词：`backend/app/agent/prompts/story_agent/system.md`

参考实现：

- 普通读取工具：`backend/app/agent/toolcall/project_tools.py`
- 异步生成工具：`backend/app/agent/toolcall/story_bible_tools.py`
- 任务观察工具：`backend/app/agent/toolcall/task_tools.py`
- 前端协作工具：`backend/app/agent/toolcall/vngraph_tools.py`

开发时只需给 Codex 业务目标和上述入口，让它先阅读现有实现再修改。

**必须遵守：**

- ToolCall 是 Agent 的业务能力，不是把 HTTP 接口逐个包装一遍。
- 参数只保留 Agent 无法从项目、资源或版本编号反查的信息。
- 读取工具不得产生写入副作用；写入工具必须明确最终写入了什么。
- 每次 ToolCall 使用独立数据库 Session 和事务，失败时不得污染后续 ToolCall。
- 返回给模型的是有边界的业务视图，不直接返回 ORM 对象或无边界的全量数据。
- 不要把新工具逻辑塞进 `server_agent.py`，也不要为单个工具新增多层抽象。
- 工具有固定调用顺序、确认边界或后续动作时，同步更新故事 Agent 提示词。

## 3. 异步任务

已有通用工具：

- `get_task_status`：立即查询任务状态。
- `wait_for_task`：等待任务结束，超时后返回最新状态。

创建生成类 ToolCall 时参考 `generate_story_bible`，并遵守：

- 使用 `create_generation_task` 创建任务，保留任务、额度和 Outbox 的事务一致性。
- 使用 `thread_id + tool_call_id + 业务类型` 构造稳定幂等键，避免模型重试造成重复任务和扣费。
- 工具只返回 `task_id` 和当前状态，**不得在任务完成前声称产物已经生成**。
- Agent 随后使用 `get_task_status` 或 `wait_for_task` 观察任务。
- 新任务类型必须注册到 `backend/app/application/story_generation_service.py` 的 `HANDLERS`，并确认实际 worker 队列会消费它。
- 业务参数错误不可重试；模型超时、限流等临时故障才允许重试。

## 4. 测试控制台

测试脚本：`backend/tools/server_agent_client.py`

服务器配置已写入 `backend/tools/server_agent_client.env`，登录后直接运行：

```bash
cd /home/workspace/fengbohan/if_line
backend/venv/bin/python backend/tools/server_agent_client.py
```

脚本只显示 `>`，Agent 对话和 ToolCall 过程去 Phoenix 查看。常用命令：

- `/new`：创建新会话。
- `/resume`：恢复最近会话。
- `/resume list`：列出最近会话。
- `/loadvn <json文件> [版本编号]`：加载 VNGraph 前端工作区。
- `/quit`：退出并打印 thread_id。

本地使用时，从 `backend/tools/server_agent_client.env.example` 复制配置，并填写后端地址、测试账号及前端 C# 校验器路径。

前端校验器：`IfLine/tools/VNGraphFrontendTool/VNGraphFrontendTool.csproj`

## 5. 验收标准

- 开发者自行通过现有接口准备测试数据，不把数据准备工作留给验收人。
- 使用普通用户会说的自然语言触发，不在 Prompt 中指定工具名、tool_call_id 或内部版本编号。
- 必须跑真实 Agent 流程；不能只跑单元测试或直接调用工具函数。
- Phoenix 中可按 thread_id 找到完整 Session，并看到 `llm.chat`、`tool.<工具名>` 和最终回答。
- 异步能力必须看到「创建任务、查询或等待任务、取得结果」的完整轨迹。
- HTTP 成功、Span 成功和业务成功必须同时成立，最终数据库或读取接口中能够确认结果。
- VNGraph 工具必须复用前端 C# 校验器，不能用 Python 重新实现简化校验。
- VNGraph Patch 必须在前端工作区应用并通过校验；需要确认的保存操作不能在预览阶段自动提交。
