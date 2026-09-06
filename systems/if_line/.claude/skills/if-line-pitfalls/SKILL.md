---
name: if-line-pitfalls
description: if_line 项目已踩过的错误与坑实录（含现象、根因、修复、预防）。开发 Agent 工具、调试任务队列、数据库迁移、Windows 本地环境或遇到"任务卡住/报错为空/改了代码不生效"等诡异现象时，先读本 skill 再动手，避免重复踩坑。
---

# if_line 已知错误与坑实录

以下问题全部在本项目实际踩过并有根因结论。**遇到类似现象先来这里对照，不要重新排查一遍。**

## 一、Agent 工具层

### 1. 写入工具不 commit → 任务永远 queued、会话结束全回滚

- **现象**：`retry_chapter_resources` 返回 ok，模型能查到 `asset.render` 任务，但状态永远停在 queued；数据库 `generation_tasks` 里查不到这些任务；outbox 无派发记录；会话结束后全部消失。
- **根因**：工具函数从头到尾没有 `db.commit()`。任务行和 `OutboxEvent` 只存在于会话事务里——不提交则 outbox 永不派发、worker 永远看不到任务，回合结束 Session 关闭即回滚。
- **修复**：写入工具成功路径必须 `db.commit()`，异常路径 `db.rollback()` 后返回错误视图（参考 `chapter_tools.py` 的 `retry_chapter_resources_tool` / `generate_chapter_script_tool`）。
- **预防**：新增写入类 ToolCall 时对照 agent-toolcall-dev skill 的约束"写入工具必须明确最终写入了什么"；验收时必须**另开会话查数据库**确认落库，不能只看工具返回值。

### 2. HTTPException 的 str() 是空串 → 错误信息为空

- **现象**：Agent 事件流报 `"Agent 执行失败: "`，后面什么都没有，无从排查。
- **根因**：FastAPI `HTTPException` 没有重写 `__str__`，`str(exc)` 恒为空。服务层（如 `legacy_chapter_script_queries.get_current_script_revision`）抛 HTTPException 穿透到 Agent 循环时就变成空错误。
- **修复**：`_error_result` 取错误文本的顺序改为：`exc.message`（业务异常）→ `exc.detail`（HTTPException）→ `str(exc)` 兜底。
- **预防**：在 Agent 工具调用链里新增 raise 时，用业务异常（带 `.message`）或确保 `_error_result` 兼容；不要裸抛 HTTPException 穿透到 agent 循环。

### 3. 回合在工具执行中崩溃 → 悬空 tool_calls 毒化整个线程

- **现象**：某回合崩溃后，该 thread 之后**每个新回合都在毫秒级失败**（报错为空），连 LLM 都没调用。
- **根因**：assistant 消息带着 `tool_calls` 被持久化，但对应的 tool 结果消息没写进去。下一次请求把这个历史发给 LLM API，OpenAI 协议要求 tool_calls 后必须跟 tool 结果，请求被拒 → 线程永久损坏，只能弃用重开。
- **现状**：**未修复**（server_agent.py 持久化层问题），规避手段是崩溃后换新 thread。
- **预防**：排查"新回合秒失败"时先查 `agent_messages` 表最后一条 assistant 消息的 JSON 里有没有没有结果的 `tool_calls`；修复方向是在加载历史或回合失败时补写合成 tool 结果。

### 4. 工具契约：资源渲染一次只能一种类型

- **现象**：一次传 `resource_types: ["background","portrait","keyframe"]` → 422；模型自作主张并行发起三次调用 → 崩溃。
- **根因**：`request_script_resource_render` 底层约束一次一个 role；并行调用还会在同一个 DB Session 上产生并发问题。
- **修复/预防**：schema description 和 system.md 都写明"一次只传一种 resource_type，逐类调用"；失败也让模型按 `next_actions` 提示逐类重试。

## 二、任务管线与数据链

### 5. 两套数据模型断层：Agent 写旧表，资源渲染要新表

- **现象**：`run_chapter_workflow` 成功生成正文和素材 Prompt，但 `retry_chapter_resources` 报 404"Chapter Script revision 不存在"；工作台 UI 里也看不到 Agent 生成的设定/大纲/正文。
- **根因**：Agent 工具链写 legacy 表（`story_bibles`/`chapter_contents`/`chapter_outlines`），而脚本生成（`chapter_script.generate`）前置校验要求完整 v2 链（`story_bible_revisions`→`outline_revisions`→`chapter_revisions`，且 context_manifest 逐字段一致），资源渲染又要求 `chapter_script_revisions`。两边互不相通。
- **修复**：`generate_chapter_script` 工具内置 `_ensure_v2_chapter_ready` 桥接（复用 AutoCreator `_step_bridge_v2` 的既有服务序列：create_bible_revision → StoryPath/outline_revision → create_manual_story_path_chapter_revision → activate head，全部幂等复用）。
- **预防**：给 Agent 加"生成类"工具前，先确认产物落在哪套表、下游消费方读哪套表；桥接逻辑复用 `app/agent/auto_creator.py` 与 `app/application/` 既有函数，不要另写。

### 6. 脚本生成任务不激活 head → 旧查询 404

- **现象**：脚本任务 succeeded 后，`retry_chapter_resources` 仍报脚本不存在。
- **根因**：`chapter_script.generate` handler 只写 `chapter_script_revisions`，**不更新** `chapter_script_heads.current_revision_id`（激活是人工确认动作）；而旧查询只认 head。
- **修复**：解析改三级回退：显式 script_revision_id → head.current_revision_id → 按 (project, chapter) 取最新修订（见 `chapter_resource_orchestrator._resolve_chapter_script_revision`）。
- **预防**：凡依赖"当前修订"的查询，不要只信 head；head 只代表人工激活的版本。

## 三、进程与环境（Windows 本地）

### 7. uvicorn --reload 在 SSE 长连接下不完成重载

- **现象**：改完代码看到 "WatchFiles detected changes... Reloading..."，以为新代码已生效，但新请求仍跑旧逻辑（修复"没生效"的假象）；旧进程因 SSE 连接和进行中回合无法优雅退出，一直拖着。
- **修复/预防**：改代码后**彻底停掉 uvicorn 进程再启动**（TaskStop + taskkill 残留 PID），不要依赖 --reload 在有 SSE/长回合时生效；启动命令可不带 --reload。

### 8. Windows 保留端口段导致绑定失败

- **现象**：vite 起 5173 报 `EACCES: permission denied`，但端口明明没人占用。
- **根因**：Hyper-V/Windows 保留端口段（`netsh interface ipv4 show excludedportrange protocol=tcp` 查看），本机 5117-5216 恰好包含 5173。
- **修复**：vite 改 5273；后端因 60002 端口冲突史改 61002（CORS、vite proxy、启动脚本三处同步）。
- **预防**：本机选端口前先对照保留段表；`EACCES` 不等于端口被占用。

### 9. Python 3.13 装不上旧版依赖

- **现象**：系统 Python 3.13 装 requirements.txt 失败（pydantic 2.5 / aiohttp 3.9 无 cp313 wheel，源码编译又缺工具链）。
- **修复**：用 miniconda 的 Python 3.10 建 `backend/venv`（`D:\Program_Data\minconda\python.exe -m venv venv`），依赖原样安装。
- **预防**：requirements.txt 锁的是老版本，别用最新 Python 建环境；另外 celery 在 Windows 必须 `--pool=solo`。

### 10. celery worker 不热加载

- **现象**：改了后端代码，uvicorn 重启了，但 celery 任务仍跑旧逻辑。
- **预防**：改代码后 **worker 和 beat 必须一并重启**；它们消费任务时才加载业务代码。

### 16. 端口对不上 ≠ Bug：端口是各机器自己的本地设置（已真实误报过一次）

- **现象**：审查/测量时发现文档或代码默认值写 60002/5173，而实际跑的是 61002/5273，被当作"配置漂移/文档过时/CORS 白名单不一致"的问题上报。
- **根因**：端口本来就是**按机器本地化**的配置——Windows 保留端口段、端口冲突史都因机器而异（见第 8 条）。机制是：`frontend/.env`（VITE_API_PROXY_TARGET / VITE_DEV_SERVER_PORT）+ `backend/.env`（PORT）各机器自设，未设置时才回落 `vite.config.js` / `core/config.py` 里的默认值；文档写的 60002/5173 只是默认参照，不要求全局一致。CORS 默认白名单同理——dev 走 vite 代理（/api 同源转发），不依赖 CORS 白名单放行前端端口。
- **预防**：看到端口对不上，先确认是不是 `.env` 本地覆盖机制在起作用；端口差异本身不是项目 Bug，只有**绕过 .env 的硬编码端口**（src 代码里写死地址）才算问题。
- **项目知识**：本机当前 = 前端 5273 → 代理后端 61002（均在 `frontend/.env`，故意不入库）；其他机器可能仍是 5173/60002。

## 四、接口与调用

### 11. Git Bash 里 curl -d 直接传中文 JSON 会出错

- **现象**：`curl -d '{"title":"中文"}'` 报 400 body 解析失败或乱码。
- **修复**：中文 body 写入临时文件，用 `curl --data-binary @file.json -H "Content-Type: application/json"`。
- **预防**：Git Bash (MSYS) 对内联 UTF-8 不可靠，一律走文件。

### 12. 新旧密码哈希不互通

- **现象**：拉取新代码后，旧代码时期注册的账号登录报"邮箱或密码不正确"，新注册账号一切正常。
- **根因**：上游改过密码哈希实现，旧哈希新代码校验不过。
- **修复**：本地测试账号直接用新代码的 `hash_password` 重新生成 hash 并 UPDATE；正式环境需做迁移策略。
- **预防**：升级后老账号批量登录失败时先怀疑哈希方案变更，而不是数据库丢数据。

### 13. 猜字段测量会伪装成 Bug（已真实误报过一次）

- **现象**：用 `d.get('chapters') or d.get('path_chapters')` 这类"猜字段链"的脚本测 API，字段不存在时**静默返回空值**，空值被解读成"API 返回 0 条数据"，写成对比报告误导结论。真实案例：`GET /story-paths/{id}` 本来就没有 chapters 字段（只返回路径元数据），被误报为"路径详情返回 0 章节、读取 API 与数据链脱节"。
- **修复**：先 dump 响应原始结构（顶层字段列表、每字段类型），确认字段存在再去数；结论写进报告前用原始 JSON 复核一遍。
- **预防**：**测量方法本身要先被验证**。任何"API 返回空/0"的断言，先证明字段确实存在且语义理解正确，再下"API 有问题"的结论。这是"测量误差伪装成 Bug"的经典模式。
- **项目知识**：REST 资源拆分——路径元数据在 `GET /story-paths/{id}`，章节列表在专属端点 `GET /story-paths/{id}/chapters`（且只返回当前活动大纲链上的挂载点，被替换的旧挂载点不出现，属预期行为）。

### 14. 两套 head 体系：写入方与读取方各在一侧（计数 0/0 的根因）

- **现象**：Agent `view_project` 报"0 条大纲 / 0 章正文"，但 legacy 表和路径链上数据齐全。
- **根因**：StoryPath 迁移期存在**两套并行 head**：
  - 项目级（旧）：`project_content_heads.current_outline_revision_id` + `chapter_heads.current_revision_id` —— 只有手动审查接口（PUT /revisions 线）写；
  - 路径级（新）：`story_path_outline_heads` + `story_path_chapters.current_revision_id` —— 桥接/AutoCreator/finalize-publish 全部写这套。
  生成链写新体系、`view_project` 读旧体系，中间无同步 → 计数恒为 0。
- **修复方向**：view_project 计数改为优先读根路径活动链（outline_revision_chapters 绑定数 + 有选中修订的挂载点数），空时回落 legacy 表。
- **预防**：排查"Agent/接口看不到数据"时，先确认**读写双方用的是哪套 head**，双体系未收敛前这是第一嫌疑（与"发布锚点自洽"问题同源）。

### 15. 分支候选链是代码级死链（无 checkpoint 生产者）

- **现象**：候选集生成 API（`POST /story-paths/{id}/checkpoints/{node_id}/candidate-set-generations`）必填已存在的 `node_type="checkpoint"` StoryNode + state_snapshot；二者全库为 0，前端工作台也只提供"手动粘贴检查点 UUID"输入框。
- **根因**：**全后端没有任何代码创建 checkpoint 节点**（唯一 StoryNode 创建处是候选生成自身的 preview 节点，且它要求 checkpoint 先存在——鸡生蛋死锁）。checkpoint 生产者大概率规划在外部 Godot VN 图编辑器侧，仓库内未实现。
- **影响**：故事支线/分支候选/候选提升为路径这条功能链，**用 Agent 或不用 Agent 都走不通**，不是工具封装问题。
- **预防**：给"分支/支线"做任何 Agent 工具或前端功能前，先确认 checkpoint 节点与 state snapshot 的生产链存在，否则纯做无用功。

## 五、排查口诀

1. 任务卡 queued → 先查**事务有没有 commit**，再查 outbox 派发、worker 队列。
2. 报错信息为空 → 先怀疑 **HTTPException 穿透**，去 `python-backend.local.log` 看 `ERR[]` 条目，必要时在独立脚本里复现拿完整 traceback。
3. 新回合秒失败 → 查 `agent_messages` 里的**悬空 tool_calls**。
4. 改代码不生效 → 确认 uvicorn/worker **进程真的换了**（看进程 PID，别信 --reload）。
5. Agent 看不到某数据 / 下游 404 → 先画清楚**读写双方各在哪套表**（legacy vs v2 修订链）。
6. "API 返回 0/空" → **先证明字段存在、语义没理解错**，再断言 API 有问题（猜字段测量=伪 Bug）。
7. 数据"读不到但库里明明有" → 查**读写用的是哪套 head**（项目级 vs 路径级，双体系未收敛）。
8. 端口/文档对不上 → 先看 `frontend/.env`、`backend/.env` 的**本地覆盖**，端口差异是机器设置不是 Bug（第 16 条）。
