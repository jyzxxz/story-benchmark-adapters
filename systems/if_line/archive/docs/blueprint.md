# Blueprint — if_line 四大问题闭环

> 这是本仓库**唯一权威 blueprint 源**。execution-cron-builder 以本文件的 Execution checklist 段作为需求源，所有 worker claim、master 集成、`[x]` 验收都以本文件为准。
>
> 双游标协议：`[ ]` 未做（worker 可领）/ `[_]` worker 自测通过待 master 集成 / `[x]` master 验收集成完成。worker 永远不能写 `[x]`。

---

## Prose: 背景

if_line 是「小说 → 视觉小说」自动生成系统。现有链路：`AutoCreator` 跑 brainstorm idea → Project → Story Bible → Outline → 全部 ChapterContent → 素材生成 → VNGraph → TTS。

用户报了 4 个问题：

1. **生图是基于用户选项而非文章内容**。立绘由前端 `AssetPromptView.vue` 让用户选 `emotion × outfit × pose` 笛卡尔积；关键帧靠分段无智能选片；前端 `BackgroundGenerateRequest.moods` 也是用户选。背景图 v2 链路 (`generate_chapter_backgrounds_v2` → `BackgroundSceneAnalyzerService`) 已是 LLM 内容驱动，是可复用范例。
2. **生语音前端方式没体现**。后端 `tts_service.synthesize` 完整可用（讯飞超拟人、音色卡、缓存），但前端只有 `StoryBibleView.vue` 的角色级单行测试按钮，无章节级批量配音 + 播放器。
3. **没有完整 vngraph 输出**。VNGraph 按章存取 + 单章预览，节点只引用图片，不含音频；用户希望整章 vngraph 包含「文本 + 图 + 配音」一体化。
4. **生文评价标准不完善，连续生文 AI 味重**。现有 `quality_check.py` 只查字数/标题/角色名/ending_hook，没有「AI 味/套路化用语」评审；章节循环生成只传 200 字 previous_summary，缺反 AI 味负例。

## Prose: 已澄清范围

- **P1**：三类素材（立绘 + 背景 + 关键帧）全部内容驱动；前端只留「一键生成」按钮。
- **P2**：章节级批量配音 + 整章播放器（不只是扩展 Story Bible 测试）。
- **P3**：「完整 vngraph」= 整个章节的 vngraph，含文本 + 图片 + 配音。
- **P4**：AI 味/套路化用语检测为主（不优先 multi-dim 评分卡）。

## Prose: 五件 cron-builder 分工

| Skill | 角色 |
|---|---|
| `learn-cron-builder` | 把 11 个关键现有源文件转 1:1 `_learn.md`，作为 worker 可复用范本 |
| `compete-cron-builder` | 对 P4/P1/P2/P3 各跑一轮 blueprint 三方竞争，合并 patch 写回本文件 |
| `execution-cron-builder` | 以本文件为源，装 cron + Claude Code worker 池 + DAG 闸门 + 双游标 |
| `optimization-cron-builder` | LLM 成本/缓存命中率/吞吐优化迭代 |
| `looper-cron-builder` | ResourceLease 包外层，token/wall-clock/human-review/disk 闸控 |

Worker 平台固定 **Claude Code** (`claude -p --permission-mode auto`)。

---

## Execution checklist

> DAG 顺序：**P4 → P1 → P2 → P3**。strict layer gate：低层未 `[x]` 时高层 `[x]` 必须被 reset 为 `[ ]`。

### P4. AI 味/套路化用语检测层

- [x] **P4.1** 新增 `backend/app/agent/ai_flavor_check.py` — LLM 评审器，输入章节正文 + style_rules，输出 `{passed, score(0-100), issues[], rewrite_hint}`。复用 `Docs/learn/files/backend/app/services/background_scene_analyzer_service.py_learn.md` 里的 `_call_llm` 模式（`response_format=json` + 重试 + 缓存 hash）。
- [x] **P4.2** 改 `backend/app/agent/presets.py` — 加 `AI_FLAVOR_MIN_SCORE = 70`、`AI_FLAVOR_MAX_RETRIES = 2`。
- [x] **P4.3** 改 `backend/app/services/prompt_templates.py` — 章节正文 prompt 加反 AI 味负例清单（套话词表 + 模板句），加可选 `rewrite_hint` 段（非空时追加「Previous draft was rejected for these reasons: {hint}. Rewrite avoiding them.」）。
- [x] **P4.4** 改 `backend/app/agent/auto_creator.py` 的 `_step_generate_chapters` — 在 `quality_check.check_chapter_content` 通过后接 `ai_flavor_check.check`；不通过则带 `rewrite_hint` 重写；与 `quality_check` 共享 `MAX_CHAPTER_FAILURES` 配额（不叠加）；每次结果写 `runs/<run_id>/chapter_<ci>_flavor.json`。

### P1. 内容驱动生图层（依赖 P4）

- [x] **P1.1** 新增 `backend/app/services/portrait_demand_analyzer.py` — `async def analyze(project_id, chapter_indices, story_bible) -> List[PortraitDemand]`。LLM 从章节正文 + Story Bible 角色卡抽「哪一章哪一段哪个角色需要哪个 emotion/outfit/pose 变体」。Dedup by `(character_id, emotion, outfit, pose)`。
- [x] **P1.2** 新增 `backend/app/services/keyframe_moment_selector.py` — `async def select(chapter_content, chapter_outline, max_keyframes) -> List[KeyframeMoment]`。LLM 从正文挑「最有画面感 / 最该被定格」的 2-3 个时刻，输出 moment_summary/target_characters/visual_focus/source_excerpt/rationale。
- [x] **P1.3** 改 `backend/app/services/asset_management_service.py` — `generate_all_portraits` 加 `auto_demand: bool = False` 参数（True 时调 `PortraitDemandAnalyzer` 忽略外部 variations）；`generate_chapter_keyframes` 先调 `KeyframeMomentSelector` 再走 prompt builder。
- [x] **P1.4** 改 `backend/app/routers/image_generation.py` — `PortraitGenerateRequest` 加 `auto_demand: bool = True`（默认开），`variations` 标注 deprecated；`/generate-all` 路由直接传 `auto_demand=True`。
- [x] **P1.5** 改 `frontend/src/views/AssetPromptView.vue` + `frontend/src/api/imageGenerationApi.ts` — 砍掉 `selectedEmotions/selectedOutfits/selectedPoses` 多选 UI（保留展示但不发请求）；改成「一键生成本章/全部素材」按钮直接调 `/generate-all`。

### P2. 章节配音层（依赖 P4）

- [x] **P2.1** 新增 `backend/app/services/chapter_voice_service.py` — `async def generate_chapter_voices(project_id, chapter_index) -> List[VoiceLine]`。步骤：① 读 `ChapterContent.content` + `StoryBible.characters` 音色卡；② LLM 切有序 `VoiceLine`（`{kind: narration|dialogue, speaker_name?, text, character_voice?, emotion?}`）；③ 并发调 `tts_service.synthesize`（复用 `image_generation_service` 并发模式 + `tts_service` 音频缓存）；④ 落库 `Asset(asset_type="voice_line", chapter_index, character_id, emotion, prompt=text, image_url=audio_url)`。
- [x] **P2.2** 新增 `backend/app/routers/voice.py` — `POST /api/tts/chapters/{project_id}/{chapter_index}/generate-batch`、`GET /api/tts/chapters/{project_id}/{chapter_index}/manifest`。
- [x] **P2.3** 改 `backend/app/main.py` — 注册 voice router。
- [x] **P2.4** 新增 `frontend/src/api/voiceApi.ts`（`generateBatch` / `getManifest`）+ `frontend/src/components/ChapterVoicePlayer.vue`（按 manifest 顺序 `new Audio(audio_url).play()`，`ended` 触发下一句，当前行高亮 + 同步显示文本）。
- [x] **P2.5** 改 `frontend/src/views/ImmersiveReaderView.vue`（挂 `ChapterVoicePlayer`）+ `frontend/src/views/ChapterGenerateView.vue`（章节生成完成后加「生成本章配音」按钮）。

### P3. 整章 vngraph 装配层（依赖 P1 + P2）

- [x] **P3.1** 改 `backend/app/services/vn_graph_generator.py` + `backend/app/services/llm_vn_graph_generator_simplified.py` — dialogue/narration 节点加可选字段 `audio_url`、`voice_line_id`；根 `meta` 加 `chapter_index`、`chapter_title`、`cover_image`、`total_audio_sec`、`asset_manifest`。旧 JSON 必须仍能解析（字段全可选）。
- [x] **P3.2** 新增 `backend/app/services/vn_graph_assembler.py` — `def assemble_full_chapter(project_id, chapter_index) -> dict`。步骤：① 读按章 `VNGraph` 作骨架；② 读 `Asset where chapter_index=?` 的 portrait/background/keyframe 回填节点；③ 读 `Asset where asset_type='voice_line'`，按 `prompt`(原文) 匹配 dialogue 节点 `text` 回填 `audio_url`/`voice_line_id`；④ 缺失音/图可由 flag 触发自动生成；⑤ 输出整章完整 vngraph JSON。
- [x] **P3.3** 改 `backend/app/routers/vn_graph.py` — `GET /{project_id}/chapters/{chapter_index}/full-vn-graph`、`POST .../full-vn-graph/assemble`、`GET .../full-vn-graph/export`。
- [x] **P3.4** 改 `frontend/src/api/vnGraphApi.ts` — 加 `getFullChapter` / `assembleFullChapter` / `exportFullChapter`。
- [x] **P3.5** 改 `frontend/src/views/VNGraphPreviewView.vue` — 加整章 Tab：左侧节点列表（缩略图 + 音频时长），右侧逐节点预览（背景图 + 立绘层 + 当前对白 + 迷你播放），底部「播放整章」按钮走 `ChapterVoicePlayer`。

---

## 完成验收 gate（每项 `[x]` 必须过）

- **operable entry**：API/UI 入口能跑通（curl/浏览器实测，不是 type check）。
- **feature implemented**：input → run → result → evidence 真实路径。
- **validation evidenced**：`Docs/validation_log_P<id>.<seq>.md` 记录命令 + 输出摘要。
- **not mock**：placeholder / fake response / 字段 schema-only / doc-only 都不算 `[x]`。

## 端到端验证

1. `python backend/app/scripts/run_auto_creator.py` 跑完新项目，看 `runs/<id>/chapter_*_flavor.json` score 随重试上升。
2. 前端 AssetPromptView 只有一键生成，无多选 UI；生成结果立绘/关键帧 emotion/outfit 是 LLM 分析得来。
3. ChapterGenerateView 完成后能配音；ImmersiveReaderView 能整章连续播放。
4. VNGraphPreviewView 整章 Tab 能图文音一体化播。
5. `GET /full-vn-graph` 返回 JSON 每个 dialogue 节点都有非空 `audio_url`，每个背景节点都有 `image_url`。

## 回退开关

- `PORTRAIT_AUTO_DEMAND=0` / `KEYFRAME_AUTO_SELECT=0` 一键回退到旧用户选项模式。
- vngraph 新字段全可选，旧 JSON 仍能解析。

## Cron cleanup 硬条件（五条全过才删 cron）

1. blueprint `[ ]` count = 0
2. blueprint `[_]` count = 0
3. latest todo `Unfinished = 0`
4. 无活跃 agent-runner / tmux worker
5. 无 pending checkpoint artifact；cleanup 脚本成功且 crontab 验证移除

---

# StoryPath、章节版本与单次发布重构

> 本节是 `plan.md` 对应重构的权威执行清单。旧 P1-P4 清单保留为历史记录；新工作只使用 R0-R8 编号。
>
> 双游标协议：`[ ]` 未开始 / `[_]` 开发完成、等待 master 集成 / `[x]` master 验收并提交完成。worker 不得写 `[x]`。
>
> DAG：**R0 -> R1 -> R2 -> R3 -> R4 -> R5 -> R6 -> R7 -> R8**。前置阶段未全部 `[x]` 时，不得集成后续阶段。
>
> 每个编号项独立验收、独立提交。作者和提交者固定为 `fbh <2697927157@qq.com>`，提交不得夹带其他编号项或用户已有改动。

## Prose: 重构范围与不变量

1. `chapter_index` 降级为 `display_index`，只用于显示和排序；领域关联统一使用稳定 UUID。
2. Revision 表示同一逻辑内容的编辑历史，StoryPath 表示剧情分支，两者不得混用。
3. 章节生成只读取当前 StoryPath 的 predecessor 链和冻结状态，不读取兄弟路径。
4. 大纲直接接收合法 `chapter_count`，删除 8/13/20 固定映射。
5. Bible、Outline、Chapter、CandidateSet、Script 和 VNGraph 生成结果均先审阅，再显式激活 Head。
6. `ChapterScriptRevision` 是唯一 VNGraph 骨架；完整图绑定精确 Script、AssetVersion 和 VoiceLineVersion。
7. 作者工作区始终可编辑；公开读取只使用 `ProjectPublication.active_release_id` 指向的不可变 Release。
8. 发布是单次原子操作，不保留 prepare/publish 两阶段。
9. 旧创作和发布接口最终删除，不提供重定向或兼容层；Task、Asset、Voice、ReadingSession 和 continuation 的 ID 接口保留。

## Execution checklist: StoryPath 重构

### R0. Review 与契约冻结

- [x] **R0.1** 将 StoryPath Blueprint 合并进根目录 `blueprint.md`，记录范围、DAG、双游标、提交规范和回滚原则。提交：`docs(blueprint): define story path refactor execution plan`
- [x] **R0.2** 新增架构文档，包含 ER 图、状态机、模块依赖、公开/草稿真值表和 Revision/StoryPath 示例。提交：`docs(architecture): define story path and publication model`
- [x] **R0.3** 写入新 OpenAPI 契约及旧接口删除映射，完成后端、前端、数据迁移三方 Review。提交：`docs(api): freeze replacement authoring api contract`

### R1. 数据模型与迁移基础

- [x] **R1.1** 新增 StoryPath、ChapterSlot、StoryPathChapter 及数据库约束。提交：`feat(model): add story path chapter identity model`
- [x] **R1.2** 将 OutlineRevision 和 ChapterRevision 扩展为稳定 UUID 关系，并增加冻结 context manifest。提交：`refactor(model): bind outlines and chapters to story paths`
- [x] **R1.3** 新增 CandidateSetRevision、CandidateSetHead 和路径来源字段。提交：`feat(model): version branch candidate sets`
- [x] **R1.4** 将 Script、VNGraph Head 改为精确 Revision 关系。提交：`refactor(model): bind scripts and graphs to immutable revisions`
- [x] **R1.5** 新增 ProjectPublication，调整 Release 状态约束。提交：`feat(model): add single source publication state`

### R2. 路径上下文与生成

- [x] **R2.1** 实现 StoryContextResolver，只遍历当前路径 predecessor 链，并生成稳定 context hash。提交：`feat(context): resolve generation context by story path`
- [x] **R2.2** 重构 Bible 生成和 Head 激活，生成结果默认不自动激活。提交：`refactor(authoring): make bible generation reviewable`
- [x] **R2.3** 重构大纲生成，接收 `chapter_count` 并维护稳定 PathChapter ID。提交：`refactor(outline): support arbitrary chapter counts and path heads`
- [x] **R2.4** 重构章节生成任务，移除所有 `chapter_index < target` 上下文查询。提交：`refactor(chapter): generate from frozen path context`
- [x] **R2.5** 实现带 `If-Match` 的 Revision Head 激活和并发冲突处理。提交：`feat(revision): add optimistic head activation`

### R3. 分支候选与子路径

- [x] **R3.1** 重构候选生成，支持同 checkpoint 的多个 CandidateSetRevision。提交：`refactor(branch): generate versioned candidate sets`
- [x] **R3.2** 实现候选集人工激活和候选查询。提交：`feat(branch): add candidate set head review flow`
- [x] **R3.3** 实现 Candidate 到子 StoryPath 的事务性创建、共享前缀和状态快照应用。提交：`feat(branch): promote candidate into child story path`
- [x] **R3.4** 增加兄弟路径隔离、重复分叉和幂等测试。提交：`test(branch): verify multi-path isolation and idempotency`

### R4. Script、资源与 VNGraph

- [x] **R4.1** 重构 Script 生成和 Head，使其绑定精确 ChapterRevision。提交：`refactor(script): bind script revisions to chapter revisions`
- [x] **R4.2** 将 ChapterScriptRevision 确立为唯一 VNGraph 骨架输入。提交：`refactor(vngraph): use script revision as graph skeleton`
- [x] **R4.3** 重构 VNGraph 编译，冻结 Script、AssetVersion 和 VoiceLineVersion 清单。提交：`refactor(vngraph): compile from immutable resource manifest`
- [x] **R4.4** 停用按 `(project_id, chapter_index)` 查询的 legacy VNGraphAssembler。提交：`refactor(vngraph): retire index based assembly pipeline`

### R5. 单次发布与公开/草稿

- [x] **R5.1** 实现发布 readiness 和 authoring fingerprint 计算器。提交：`feat(release): calculate immutable publication fingerprint`
- [x] **R5.2** 实现 `POST /projects/{id}/publish` 原子发布服务。提交：`feat(release): add atomic single step publication`
- [x] **R5.3** 实现新 Release 发布时旧 Release 自动 supersede。提交：`feat(release): supersede previous active release`
- [x] **R5.4** 实现 unpublish 及 active Release 限定的公开读取。提交：`feat(release): enforce active release public access`
- [x] **R5.5** 将项目状态改为 authoring 与 publication 两个计算投影。提交：`refactor(project): separate authoring and publication state`
- [x] **R5.6** 增加已发布后继续编辑、并发发布、发布失败完全回滚测试。提交：`test(release): verify atomic publication lifecycle`

### R6. 新接口与前端切换

- [x] **R6.1** 注册新的 Project、Bible、StoryPath、Outline 和 Chapter Router。提交：`feat(api): expose path based authoring endpoints`
- [x] **R6.2** 注册 Candidate、Script、VNGraph 和单次发布 Router。提交：`feat(api): expose branch graph and publication endpoints`
- [x] **R6.3** 重写前端 API 类型和请求封装，所有创作操作改用 UUID。提交：`refactor(frontend): adopt path based api clients`
- [x] **R6.4** 将项目页改为路径树、章节顺序和 Revision 审阅视图。提交：`feat(frontend): add story path authoring workflow`
- [x] **R6.5** 将发布 UI 改成一次“发布”操作，显示 readiness、当前公开版本和未发布修改。提交：`feat(frontend): add atomic publication workflow`

### R7. 历史数据迁移

- [x] **R7.1** 为每个旧项目创建 root StoryPath，并迁移章节 Slot、顺序和 Head。提交：`migration(story): backfill root paths and chapter slots`
- [x] **R7.2** 迁移 Outline、CandidateSet、Script 和 VNGraph 的精确关联。提交：`migration(story): backfill immutable artifact relations`
- [x] **R7.3** 迁移现有公开状态；当前有效 Release 保持 active，其余 published 改为 superseded。提交：`migration(release): preserve active public releases`
- [x] **R7.4** 增加迁移前后数量、Head、manifest 和公开可读性校验脚本。提交：`test(migration): verify story and release backfill`

### R8. 删除旧接口与收尾

- [x] **R8.1** 删除旧 Project/Bible/Outline/Chapter/Branch/Script/VNGraph/Release Router 注册。提交：`refactor(api): remove legacy authoring endpoints`
- [x] **R8.2** 删除前端旧 API 封装和所有按 `chapter_index` 跳转、编辑与生成逻辑。提交：`refactor(frontend): remove legacy index based clients`
- [x] **R8.3** 替换路由契约测试，确认旧接口 404 且不出现在 OpenAPI。提交：`test(api): enforce removal of legacy routes`
- [x] **R8.4** 扫描活动代码，禁止 Outline、Chapter、Branch、Script、VNGraph 通过 `chapter_index` 关联。提交：`test(architecture): forbid chapter index identity joins`
- [x] **R8.5** 完成端到端验证、部署清单、回滚演练和最终文档同步。提交：`docs(release): finalize migration and operational runbook`

## StoryPath 完成验收 gate

每个编号项标记 `[x]` 前必须满足：实现完整、聚焦测试通过、受影响回归测试通过、存在 `docs/validation/story-path/R<阶段>.<编号>.md` 验证记录、完成 `chapter_index` 身份关联审查，并由指定身份创建唯一对应提交。

提交哈希以 Git 历史为权威证据，不写入提交自身内容，避免自引用哈希。验证记录必须列出测试命令、结果和预期提交标题。

## StoryPath 上线与回滚

1. 所有编号项在功能分支逐项提交，但仅在 R0-R8 全部验收后统一部署。
2. 部署前暂停旧创作任务入口，等待受影响的旧 Task 和 Outbox 排空并备份数据库。
3. 先执行 additive migration 和校验，再将后端、Worker、前端作为同一发布单元切换，禁止混合版本。
4. 旧表和旧列保留一个观察周期，但新代码不得读取；观察期后通过独立 Blueprint 删除。
5. 上线失败时整体回滚应用版本和数据库迁移，恢复旧任务入口；不得发布部分完成的 R 阶段。
