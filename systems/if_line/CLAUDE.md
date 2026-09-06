# if_line — AI 视觉小说生成平台

一句话:小说 → 故事圣经/大纲/章节 → 章节脚本 → VN 图(Godot 节点图) → 素材(立绘/背景/CG) → TTS,前端 web 编辑器 + 外部 Godot 播放器。

## 服务与端口(本机)

| 服务 | 地址 | 说明 |
|---|---|---|
| 真后端(当前代码) | `127.0.0.1:60002` | `backend/venv` + uvicorn --reload,**前端代理指向这里** |
| 旧后端实例 | `127.0.0.1:8000` | miniconda 起的 v1 API,勿用、勿当参照 |
| 前端 dev | `127.0.0.1:5173` | `frontend/`,vite 代理 /api /static → 60002 |
| celery worker + beat | — | 与 uvicorn 三件套,缺任何一个生成任务卡 queued |

**一键管理三件套**:`backend/dev.sh start|stop|restart|status|logs api|worker|beat`(uvicorn:60002 + 全队列 worker + beat)。
日志在 `logs/{uvicorn,celery-worker,celery-beat}.log`,PID 在 `backend/.runtime/pids/`;stop 只杀 cwd=本仓 backend 的进程,selfimprove 克隆/其他项目不受影响。

后端起不来先查 `logs/uvicorn.log`;数据库迁移后须 `alembic upgrade` + touch 文件触发 reload。

## 数据库

PostgreSQL:`postgresql+psycopg://if_line:ifline_dev_pwd_2026@127.0.0.1:40002/if_line`(见 backend/.env)。
用 `backend/venv/bin/python` + `from app.database import SessionLocal` 查询(机器上无 psql)。

核心表:projects(owner_id)、story_bible_revisions(content_json)、chapter_revisions(content)、chapter_script_revisions(script_json)、vn_graph_revisions(graph_json)、generation_tasks(kind/status)、assets/asset_versions、user_sessions(只存哈希)。

## 代码结构(backend/app)

- `routers/v2/` — API v2(authoring_* 系列),prefix /api
- `application/` — 任务编排(story_generation_service: bible/outline/chapter 任务;chapter_script_service)
- `services/` — 领域逻辑(chapter_script_ir: LLM 标注→段落 IR;vn_graph_compiler: IR→Godot 图;prompt_templates: 所有提示词)
- `integrations/llm/` — LLM 适配器(chapter_script_adapter: 切分+标注契约)
- `workers/` — celery 任务

## Agent(backend/app/agent/)

两套并存,均在传统管线之上封装、产出写回同一批表,零迁移:

- **ServerAgent(真正 agentic loop)**:`server_agent.py` 的 `_agent_loop` 标准 while 循环:LLM 带 tool_calls → `spec.execute_toolcall` 执行 → 结果回填消息再发,无 tool_calls 即停。支持 wait_tool_result(暂停等外部回填后 resume)、SSE 流(`server_agent_stream.py`)、SQLite 持久化(`server_agent_store.py`)、上下文压缩(`context_compaction.py`)、trace 记录。
- **AgentSpec 体系**:`agent_spec.py`(system_prompt + mode_prompts + tool_schemas + execute_toolcall,restricted/solo 两模式)、`story_agent_spec.py`、`agent_registry.py`、`presets.py`。
- **工具层**:`toolcall/`(story_bible_tools / chapter_tools / project_tools / vngraph_node_registry_v1.json)——把 StoryBibleWorkflowService、chapter、vn_graph 等正式生成服务暴露为 function tools,由 LLM 自主决定调用顺序。
- **API**:`routers/server_agent.py`(threads/turn/SSE)。
- **AutoCreator(纯 pipeline,非工具循环)**:`auto_creator.py` 顺序跑 选题→Bible→Outline→全部章节,靠 `resilient_llm.py` 重试纠错;入口 `app/scripts/run_auto_creator.py`。
- 文档:`backend/app/agent/README.md`、`SERVER_AGENT_API.md`。

## 生成管线关键事实

- bible 生成输入只有标题/开篇/结局/风格 → 只产出"核心角色";配角是章节写作时 LLM 发明的,**不回写 bible**(chapter 输出的 appearing_characters 字段目前无人消费)
- 章节脚本标注的 speaker_character_id 只能引用 bible 角色;不在表里的说话人在 `chapter_script_ir.py` 被静默降级为"旁白"(已知 Bug)
- chapter_script 表演规范提示词蒸馏在 `chapter_script_adapter.py` 常量里(来源 performance-rules(1).md)
- 章节场景切分 segment_chapter 有内存缓存(md5(content+outline)),vngraph 与素材生成共享

## 自改进闭环(LOOP-VN-GEN-SELF-IMPROVE)

一键"生成→Claude Code 审查→修后端→重验"闭环,attempt 在隔离克隆运行:

- guard:`backend/venv/bin/python .cron/scripts/selfimprove_guard.py {validate|tick|start|status|signal|master}`;一键发射 `start --chapter-count 3`
- 审计:`qa/pipeline_audit.py --project-id N`(五层 12 项,0-100 分);evidence/账本在 `.cron/looper/selfimprove/`
- 隔离克隆:`/home/workspace/fengbohan/if_line_selfimprove`(worktree,分支 selfimprove/attempt-0001)+ 独立库 `if_line_selfimprove` + uvicorn 60003 + celery broker redis db2;克隆无 venv,统一用主仓 backend/venv
- **改动回流规矩:attempt 只 commit 到克隆分支,禁止自动 merge**;操作员检查 diff 后明确说 merge 才合并,merge 后才发射下一轮
- worker 权限白名单在克隆 `.claude/settings.local.json`(allow Bash,deny git push/crontab/alembic downgrade)
- 接口改动必须标注:`API-CHANGE`(破坏性 `BREAKING`)+ evidence 列 `api_changes`

**隔离铁律(踩过的坑)**:
- `.env` 里 `CELERY_BROKER_URL` 才是 celery broker,`REDIS_URL` 是另一回事——克隆隔离**两个都要改**,只改一个会导致克隆 beat/worker 连上生产 db0,双倍投递 dispatch_outbox 造成数千任务积压、并与线上 worker 抢任务
- 仍共享、无法隔离:同一 PG 实例连接数(克隆三件套 +40 连接)、同一生图 API key 配额、同一机器 CPU——克隆跑重活时主仓会变慢
- 克隆服务重启方式见 `.cron/looper/selfimprove/OPERATOR_GUIDE.md`;克隆 chapter_script_ir.py 尾部有 build_script_ir 兼容别名(旧迁移 0020 需要,merge 时可丢)

## 前端

- web:`frontend/`(Vue3),路由见 src/router/index.ts;`/project/:id` 是编辑主页
- 真播放器是外部 Godot 项目 `/home/workspace/aivn`(字段铁律:TachiIamge 拼写、ART 隐式清屏、Actions 并行)
- 查看前端 UI 用 skill `view-frontend`(vite + Playwright 截图 + 测试账号登录)

## 工作约定

- 拉远程用 merge 不用 rebase;不主动 push(前端依赖远程部署测试)
- openapi.v2.json 快照必须用 backend/venv 生成(miniconda pydantic 有伪差异)

## 测试账号与配额(踩过的坑)

- 402 `额度不足`(usage_service.reserve_usage)按**发起任务的用户**双轨校验:`quota_total`(总额)+`quota_daily`(日额,按 `quota_reset_at` 由 `reset_daily_quota_if_needed` 自动滚动重置)。排查 402 先看任务 `user_id` 归属,再看该用户双轨余量,别只看 999 的总额(100000)
- **游客试用账号**(email 形如 `guest-*@guest.ifline.local`)配额只有 1000,几个素材渲染批次就会打满出 402;前端在未登录态操作会**静默创建游客账号并以其身份建项目/跑任务**(项目 owner、任务 user_id 全是 guest)——E2E 脚本必须先 API 登录并断言 200+user id,任务创建后复查 `GET /api/tasks?project_id=` 里 `user_id` 全为 87
- 长链路 E2E(bible→大纲→多章正文→脚本,全是 LLM 任务)会持续消耗 units;生图(asset.render,每张 15)是后置步骤,常在链路尾段才撞额度
- 不允许手工改库给测试账号充配额(计费边界);日额会按 `quota_reset_at` 自动重置,等重置或让用户处理
- 查 per-user 问题:测试账号 999@qq.com(前端登录用,见 view-frontend skill),常用账号 dtr@qq.com
- 勿信根目录大量 *.md 报告文档(历史生成物,多已过时);以代码和 Docs/ 为准
