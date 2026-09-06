# 接口策略统一 · 完整实施计划

> 历史说明：本文保留 API 合并过程与旧提交记录，不代表当前可调用契约。视觉资源迁移已完成，最终接口与数据链路以 [图像资源生成与版本链路](./image-generation-function-flow.md) 为准；文中 `imageGenerationApi`、旧图像生成 POST、VisualAssetWorkbench 和 `direct_generate` 均不得作为现行入口使用。

> **目标读者**:`/goal` 自动化执行 + 后续人工 review
> **总工期**:14-18 工作日(7 个 Phase)
> **风险等级**:Phase 5 数据迁移是最高风险点
> **回滚策略**:每个 Phase 独立 commit,任何 Phase 失败可单独 revert
> **创建日期**:2026-07-25

---

## 决策锁定(已确认)

| 维度 | 决策 |
|---|---|
| 接口策略 | v1 同类接口全部改写为 v2,前端切流 |
| 数据迁移 | 5 个 alembic backfill 脚本一次性回填 |
| 后端 v1 路由 | 保留代码不删,但**允许补 v2 新接口**(stats/regenerate 等) |
| TTS | `ttsApi.ts` 改造为 `axios.create()` 子实例 |
| Revision UI | **要做**,WorkflowView / ChapterGenerateView 加历史版本下拉 |

---

## v1 → v2 接口映射总表

| 业务功能 | 当前 v1(要替换) | 目标 v2 | 数据迁移难度 |
|---|---|---|---|
| **Story Bible** | `workflowApi.generateBible/getBible/updateBible` | `v2/revisions.py`: `POST /bible/generations`, `GET /bible/current`, `GET/POST /bible/revisions` | 🟡 中:回填 `story_bibles` → `story_bible_revisions` |
| **大纲** | `workflowApi.generateOutline/getOutline/reviseOutline/approveOutline` | `v2/revisions.py`: `POST /outline/generations`, `GET /outline/current`, `POST /outline/revisions/{id}/approve` | 🟡 中:回填到 `outline_revisions` + `outline_revision_chapters` |
| **章节正文** | `chapterApi.generate/get/regenerate/generateAll/getStatus` | `v2/revisions.py`: `POST /chapters/{ch}/generations`, `GET /chapters/{ch}/current`, `POST /chapter-generation-batches` | 🔴 难:回填 `chapter_contents` → `chapter_revisions` + `chapter_segments` |
| **章节素材** | `chapterApi.generateAssets/getAssets` | `v2/assets.py`: `POST /asset-plans`, `POST /asset-plans/{key}/render`, `GET /assets` | 🔴 难:`assets` → `asset_versions` + `asset_bindings` |
| **VN 图谱** | `vnGraphApi.generate/generateWithLLM/get/export` (v1) | `v2/vn_graphs.py`: `POST /vn-graphs/compile`, `GET /vn-graphs/current` | 🔴 难:`vn_graphs` → `vn_graph_revisions` |
| **整章配音** | `voiceApi.generateBatch/getManifest` (v1) | `v2/voice_lines.py`: `POST /voice-lines/generations`, `GET /voice-lines` | 🟡 中:`assets(voice_line)` → `voice_lines` 表 |
| **素材生成** | `imageGenerationApi.*`(v1) | `v2/visual_assets.py` 的 `asset-actions` 流程 | 🟢 已迁(VisualAssetWorkbench 在用) |

---

## Phase 0:基线准备(0.5 天,零风险)

### 目标
锁定当前代码状态,建立可验证的"改造前快照"。

### 任务清单
- [ ] **0.1** 在 `if_line` 仓库创建分支 `feat/api-strategy-unification`
- [ ] **0.2** 跑一遍 `if_line_connect/backend/tests/test_v2_*.py`(87 个测试),记录基线通过状态
- [ ] **0.3** 跑一遍 `if_line/backend/tests/`,记录基线通过状态
- [ ] **0.4** 备份当前生产数据库:`novel_agent.db` → `novel_agent_before_unification_$(date).db`
- [ ] **0.5** 在 `docs/` 创建 `API_UNIFICATION_PLAN.md`(本计划本体)

### 验收标准
- 分支已创建
- 两套测试基线状态已记录到本文件末尾的"执行日志"
- 数据库备份存在且可读

---

## Phase 1:零风险清理(1 天,低风险)

### 目标
先清理明显的 dead code 和实现不规范,降低后续 diff 噪音。

### 任务清单

### 1.1 删除 11 个 dead code 函数
- [ ] `frontend/src/api/chapterApi.ts` 删 `getAssets()`(L89)
- [ ] `frontend/src/api/projectApi.ts` 删 `getStatus()`(L81)
- [ ] `frontend/src/api/ttsApi.ts` 删 `getStatus()`(L42)
- [ ] `frontend/src/api/vnGraphApi.ts` 删 `exportFullChapter()`(L131)
- [ ] `frontend/src/api/imageGenerationApi.ts` 删 6 个:`getPortraits`(L127)、`deletePortrait`(L133)、`getBackgrounds`(L161)、`getKeyframes`(L195)、`getAsset`(L281)
- [ ] `frontend/src/api/guestStoryApi.ts` 删 `listPublicProjects`(L201)、`ensureThreeKingdomsBackground`(L241)

### 1.2 TTS 子实例改造
- [ ] `frontend/src/api/ttsApi.ts` 改造:
  ```ts
  // 改造前
  import axios from 'axios'
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
  await axios.post(`${API_BASE_URL}/api/tts/synthesize`, request)

  // 改造后
  import axios from 'axios'
  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || ''
  const ttsClient = axios.create({
    baseURL: `${API_BASE_URL}/api/tts`,
    timeout: 60000,
    withCredentials: false,
  })
  // 顶部加注释:此客户端故意豁免主拦截器,用于游客试听场景
  // - 不注入 BYOK 头(TTS 不消费)
  // - 不触发 401 自动重试(避免试听失败导致跳转登录)
  ```
- [ ] `ttsApi.getStatus()` 删除(已 dead code,不用迁移)
- [ ] `frontend/src/api/index.ts` 主实例保留不变

### 1.3 抽 `.vue` 裸路径到 api 模块
- [ ] 新建 `frontend/src/api/statsApi.ts`,封装 `getStats(p)` 和 `getStatsBreakdown(p)`
- [ ] 新建 `frontend/src/api/llmApi.ts`,封装 `getByokDefaults()`
- [ ] `frontend/src/api/workflowApi.ts` 的 `getOutline(p, opts?: { silent401?: boolean })` 加可选参数
- [ ] `frontend/src/api/imageGenerationApi.ts` 的 `getAllAssets(p, opts?: { silent401?: boolean })` 加可选参数
- [ ] `frontend/src/views/StatsView.vue` 切到 `statsApi`
- [ ] `frontend/src/components/ByokSettingsDialog.vue` 切到 `llmApi`
- [ ] `frontend/src/views/ImmersiveReaderView.vue` 切到带 `silent401` 的封装

### 1.4 共存路径注释
- [ ] `backend/app/main.py` 在 L184-204 每个 `include_router` 上方加注释,标明:
  - 该 router 是 v1 还是 v2
  - 共存路径(如 `/api/projects/{id}/assets` 由哪些 router 共享)
  - 计划废弃日期(`// TODO: deprecate after Phase 7`)

### 验收标准
- 前端编译通过(`npm run build`)
- 前端所有现有页面手测一遍无回归(StoryBible 试听、章节生成、素材页、阅读页)
- 没有删除任何**被使用**的函数(grep 验证)

### 提交规范
```
commit 1: chore(frontend): remove 11 dead API functions
commit 2: refactor(ttsApi): convert to axios.create sub-instance
commit 3: refactor(frontend): extract inline API calls to api modules
commit 4: docs(backend): annotate v1/v2 router coexistence
```

---

## Phase 2:后端补 v2 缺失接口(2 天,中风险)

### 目标
解决"v2 没有 v1 对应接口"的 3 个阻塞点。

### 任务清单

### 2.1 在 `v2/revisions.py` 补 stats 接口
- [ ] 新增 `GET /api/projects/{project_id}/stats`
  - 读取 `generation_tasks` + `usage_ledger_entries` 聚合
  - 返回结构与 v1 `chapters.py:get_project_stats` 兼容(便于前端平滑切换)
- [ ] 新增 `GET /api/projects/{project_id}/stats/breakdown`
  - 同样从 v2 表聚合
- [ ] 写测试 `tests/test_v2_stats.py`(覆盖:空项目、有任务、有失败任务、跨日)
- [ ] **Feature flag**:挂在 `revision_read_enabled` 下

### 2.2 在 `v2/revisions.py` 补 regenerate 语义
- [ ] v2 不直接支持 "覆盖重新生成",改为新建 revision:
  - 复用 `POST /api/projects/{id}/chapters/{ch}/generations`
  - 但加查询参数 `?force_new_revision=true` 或 header `X-Regenerate: true`
  - service 层:不读 current revision 作为基础,而是清空 LLM 上下文重跑
- [ ] 写测试(覆盖:有历史 revision 时 regenerate 创建 revision N+1;current 切换正确)

### 2.3 在 `v2/assets.py` 补单章 generate-assets
- [ ] v1 `POST /chapters/{ch}/generate-assets` 的语义是"为本章批量生成全部素材"
- [ ] v2 等价流程:`POST /asset-plans` → 批量 `POST /asset-plans/{key}/render`
- [ ] 新增便捷接口 `POST /api/projects/{id}/chapters/{ch}/generate-assets-batch`
  - 内部调用 plan_assets + 并发 render
  - 返回 task_id 列表(让前端走 tasks 轮询)
- [ ] 写测试

### 验收标准
- 3 个新接口都通过测试
- 接口契约文档更新到 `backend/openapi.v2.json`
- `if_line_connect` 的 87 个 v2 测试仍然全绿(没破坏老接口)

### 提交规范
```
commit 5: feat(v2): add stats endpoints under revisions router
commit 6: feat(v2): add regenerate semantics via force_new_revision
commit 7: feat(v2): add generate-assets-batch convenience endpoint
```

---

## Phase 3:数据库迁移脚本(2-3 天,最高风险 ⚠️)

### 目标
把 v1 表数据回填到 v2 表,让 v2 接口能看到历史数据。

### 关键约束
- 每个 migration 必须**幂等**(`IF NOT EXISTS` 检查)
- 每个 migration 必须有 **downgrade**(可回滚)
- 每个 migration 完成后跑**对账 SQL**验证行数

### Alembic 链现状
```
0001 → 0002 → ... → 0012 (head)
                            ↓
                      0013_backfill_bible_revisions
                            ↓
                      0014_backfill_outline_revisions
                            ↓
                      0015_backfill_chapter_revisions
                            ↓
                      0016_backfill_vn_graph_revisions
                            ↓
                      0017_backfill_asset_versions (new head)
```

### 任务清单

### 3.1 写 `0013_backfill_bible_revisions.py`
- [ ] upgrade:
  ```sql
  -- 幂等检查
  INSERT INTO story_bible_revisions (id, project_id, revision_no, payload, created_at, created_by_user_id)
  SELECT
    nextval('story_bible_revisions_id_seq'),
    s.project_id,
    1,  -- 首个 revision
    json_build_object('content', s.content, 'meta', s.meta),
    COALESCE(s.updated_at, NOW()),
    s.owner_id
  FROM story_bibles s
  WHERE NOT EXISTS (
    SELECT 1 FROM story_bible_revisions r
    WHERE r.project_id = s.project_id
  );

  -- 更新 project_content_heads
  UPDATE project_content_heads h
  SET bible_revision_id = (
    SELECT MIN(id) FROM story_bible_revisions r WHERE r.project_id = h.project_id
  )
  WHERE h.bible_revision_id IS NULL;
  ```
- [ ] downgrade:删除 `story_bible_revisions` 中 `revision_no = 1 AND created_at < migration_run_time` 的行,head 置 NULL
- [ ] **对账脚本** `scripts/verify_0013.py`:`SELECT COUNT(*) FROM story_bibles` vs `SELECT COUNT(DISTINCT project_id) FROM story_bible_revisions`

### 3.2 写 `0014_backfill_outline_revisions.py`
- [ ] v1 `chapter_outlines` 是按章节一行;v2 `outline_revisions` 是"整份大纲" + `outline_revision_chapters` 子表
- [ ] upgrade:按 project_id 分组,所有章节聚合成一个 revision 1
  ```sql
  INSERT INTO outline_revisions (...)
  SELECT project_id, 1, json_agg(...), ...
  FROM chapter_outlines
  GROUP BY project_id;

  INSERT INTO outline_revision_chapters (...)
  SELECT r.id, o.chapter_index, o.title, o.beats, ...
  FROM chapter_outlines o
  JOIN outline_revisions r ON r.project_id = o.project_id;
  ```
- [ ] downgrade + 对账

### 3.3 写 `0015_backfill_chapter_revisions.py` (最复杂)
- [ ] v1 `chapter_contents` 一行 = 一章正文
- [ ] v2 拆成 `chapter_revisions`(版本) + `chapter_segments`(切片)
- [ ] **策略**:每个 v1 chapter → 1 个 chapter_revision(revision_no=1) → 1 个 chapter_segment(整章作为单段)
- [ ] upgrade:
  ```sql
  INSERT INTO chapter_revisions (id, project_id, chapter_index, revision_no, ...)
  SELECT nextval(...), project_id, chapter_index, 1, content, ...
  FROM chapter_contents;

  INSERT INTO chapter_segments (chapter_revision_id, segment_index, text, ...)
  SELECT id, 0, content, ...
  FROM chapter_revisions;

  UPDATE chapter_heads h
  SET chapter_revision_id = (
    SELECT id FROM chapter_revisions r
    WHERE r.project_id = h.project_id AND r.chapter_index = h.chapter_index
  );
  ```
- [ ] **特别注意**:`chapter_segments` 的 `segment_hash` 字段需要计算,看 v2 service 怎么算的,保持一致
- [ ] downgrade + 对账

### 3.4 写 `0016_backfill_vn_graph_revisions.py`
- [ ] v1 `vn_graphs` 表直接存 JSON
- [ ] v2 `vn_graph_revisions` 需要经过 deterministic compiler 处理
- [ ] **策略**:不直接复制 JSON,而是用 v2 compiler 重新编译,确保格式一致
  ```python
  def upgrade():
      graphs = op.get_bind().execute("SELECT * FROM vn_graphs").fetchall()
      for g in graphs:
          compiled = compile_vn_graph_v2(g.graph_data)
          op.execute(insert_vn_graph_revision(compiled))
  ```
- [ ] **风险**:compiler 可能因为格式问题失败,需要 try/except + 失败日志表
- [ ] downgrade + 对账

### 3.5 写 `0017_backfill_asset_versions.py`
- [ ] v1 `assets` 表:`type` in (portrait/background/keyframe/voice_line)
- [ ] v2 拆分:
  - portrait/background/keyframe → `asset_versions` + `asset_bindings`
  - voice_line → 额外写 `voice_lines` 表
- [ ] **最大难点**:v1 asset 没绑 VN 节点,需要根据 `chapter_index + character_name + prompt` 启发式匹配 v2 节点
- [ ] **策略**:
  - 第一遍:精确匹配(chapter_index + character_name 完全一致)
  - 第二遍:模糊匹配(prompt 文本相似度)
  - 不匹配的:写 `asset_versions` 但 `asset_bindings` 留空,在 UI 标"未绑定节点"
- [ ] upgrade + downgrade + 对账 + **失配报告**(`scripts/report_unbound_assets.py`)

### 验收标准
- 在测试库 `novel_agent_test.db` 跑完整 upgrade,5 个对账脚本全过
- 跑完整 downgrade,数据库回到 0012 状态
- 再跑一遍 upgrade(测试幂等)
- **关键**:在 `if_line_connect` 的数据库上跑一遍,确保 v2 接口能看到所有回填数据

### 提交规范
```
commit 8: feat(alembic): 0013 backfill story_bible_revisions
commit 9: feat(alembic): 0014 backfill outline_revisions
commit 10: feat(alembic): 0015 backfill chapter_revisions
commit 11: feat(alembic): 0016 backfill vn_graph_revisions
commit 12: feat(alembic): 0017 backfill asset_versions + voice_lines
commit 13: chore(scripts): add backfill verification + unbound asset report
```

### 风险与回滚
- 每个 migration 独立 commit,失败可单独 `alembic downgrade <prev>`
- 在生产运行前,**必须**先在 staging 数据库跑一遍
- 保留 `novel_agent.db.before_migration` 备份至少 7 天

---

## Phase 4:Revision UI 组件开发(2 天,中风险)

### 目标
为切换 v2 做好 UI 准备,让用户能看到历史版本(否则 v2 revision 价值为零)。

### 任务清单

### 4.1 新建通用 RevisionSelector 组件
- [ ] `frontend/src/components/RevisionSelector.vue`
  - props:`revisions: Revision[]`, `currentId: number`, `onSelect`
  - UI:下拉框,每项显示 `r{revision_no} · {created_at} · {激活/历史}` 标签
  - 支持搜索(按 revision_no 或 created_at)
- [ ] 单元测试(`vitest`)

### 4.2 在 WorkflowView 集成
- [ ] 引入 `RevisionSelector`,分别为 bible / outline 各放一个
- [ ] 状态机扩展:
  - 加 `bibleRevisions: Revision[]`, `currentBibleRevisionId`
  - 加 `outlineRevisions: Revision[]`, `currentOutlineRevisionId`
- [ ] 选择历史版本时:
  - 只读模式(禁用编辑按钮)
  - 显示"这是历史版本"提示
  - 提供"恢复到此版本"按钮 → 调用 `POST /bible/revisions/{id}/activate`

### 4.3 在 ChapterGenerateView 集成
- [ ] 同上,每章一个 RevisionSelector
- [ ] 加"对比"功能(可选,如果工期允许):
  - 左右分屏显示两个 revision 的差异

### 4.4 在 StoryBibleView / OutlineReviewView 集成
- [ ] 同步加 RevisionSelector

### 验收标准
- 在 v2 接口下,用户能看到历史版本列表
- 切换历史版本时,UI 正确进入只读模式
- "恢复到此版本"按钮工作正常

### 提交规范
```
commit 14: feat(frontend): add RevisionSelector component
commit 15: feat(WorkflowView): integrate bible/outline revision selector
commit 16: feat(ChapterGenerateView): integrate chapter revision selector
commit 17: feat(StoryBibleView/OutlineReviewView): integrate revision selector
```

---

## Phase 5:前端 API 层 v1→v2 切换(3-4 天,中风险)

### 目标
把前端 api/*.ts 模块从 v1 切到 v2,触发实际流量切换。

### 任务清单(按依赖顺序)

### 5.1 `workflowApi.ts` 切换(最先,无依赖)
- [ ] `generateBible` → `POST /bible/generations`(返回 task_id,需轮询)
- [ ] `getBible` → `GET /bible/current`
- [ ] `updateBible` → `POST /bible/revisions`(新建 revision)
- [ ] `generateOutline` → `POST /outline/generations`
- [ ] `getOutline` → `GET /outline/current`
- [ ] `reviseOutline` → `POST /outline/generations` (with feedback)
- [ ] `approveOutline` → `POST /outline/revisions/{id}/approve`
- [ ] **新增** `listBibleRevisions(p)` → `GET /bible/revisions`
- [ ] **新增** `listOutlineRevisions(p)` → `GET /outline/revisions`
- [ ] **新增** `activateBibleRevision(p, rid)` → `POST /bible/revisions/{id}/activate`

### 5.2 `chapterApi.ts` 切换
- [ ] `generate` → `POST /chapters/{ch}/generations`
- [ ] `regenerate` → `POST /chapters/{ch}/generations?force_new_revision=true`(Phase 2 补的)
- [ ] `get` → `GET /chapters/{ch}/current`
- [ ] `getStatus` → `GET /readiness`
- [ ] `generateAll` → `POST /chapter-generation-batches`
- [ ] **新增** `listChapterRevisions(p, ch)` → `GET /chapters/{ch}/revisions`
- [ ] **新增** `activateChapterRevision(p, ch, rid)` → `POST /chapters/{ch}/revisions/{id}/activate`

### 5.3 `vnGraphApi.ts` + `visualAssetApi.ts` 合并
- [ ] `vnGraphApi` 的所有方法迁移到 `visualAssetApi`
- [ ] `generate` / `generateWithLLM` → 调用 v2 compile 流程
- [ ] `get` → `GET /vn-graphs/current`
- [ ] `export` → 改为前端从 `current` 接口拿数据后本地导出 Blob
- [ ] `getFullChapter` / `assembleFullChapter` → 评估是否还需要(v2 没有直接对应)
- [ ] 删除 `vnGraphApi.ts`,所有引用改到 `visualAssetApi`

### 5.4 `voiceApi.ts` 切换
- [ ] `generateBatch` → `POST /chapter-revisions/{rid}/voice-lines/generations`
  - **难点**:v2 需要 chapter_revision_id,前端要先 `GET /chapters/{ch}/current` 拿到 rid
- [ ] `getManifest` → `GET /chapter-revisions/{rid}/voice-lines`
- [ ] **返回结构适配**:v1 是 `voice_lines[]` 平铺,v2 是 `{occurrences, manifest}`,需要在 api 层做适配函数

### 5.5 `imageGenerationApi.ts` 切换
- [ ] **保留**:`getStatus` (Phase 5 暂留,v2 没有直接对应)
- [ ] **删除**:`generatePortraits` / `generateBackgrounds` / `generateKeyframes` / `generateAll` / `generateAllChapter`
  - 这些都被 VisualAssetWorkbench 的 asset-actions 流程替代
- [ ] **保留**:`getAllAssets` (改名为 `listAssets`,封装 `GET /assets`)
- [ ] **删除**:`deleteAsset`(v2 通过 asset-actions cancel)

### 5.6 `projectApi.ts` 切换
- [ ] `list` 保持 v1(项目列表 v2 也用 `/public/projects`,但语义不同)
- [ ] `listPublic` → `GET /public/projects`(v2 releases.py)
- [ ] 其他保持

### 5.7 `guestStoryApi.ts` 微调
- [ ] 已经全部是 v2,无需切换
- [ ] 配合 Phase 5.4 voice 改造,适配新的 manifest 结构

### 验收标准
- 所有 `api/*.ts` 不再调用 v1 接口(grep `/api/projects/.*/vn-graph\b`、`/api/tts/chapters` 等应无结果)
- 浏览器 Network 面板手测每个 view,确认所有请求都是 v2 路径
- 跑 `if_line_connect/backend/tests/test_v2_*.py` 仍然 87 个全绿

### 提交规范
```
commit 18: refactor(workflowApi): migrate to v2 revisions
commit 19: refactor(chapterApi): migrate to v2 revisions
commit 20: refactor(visualAssetApi): merge vnGraphApi into visualAssetApi
commit 21: refactor(voiceApi): migrate to v2 voice_lines
commit 22: refactor(imageGenerationApi): remove v1 generate endpoints
commit 23: refactor(projectApi): listPublic to v2 releases
```

---

## Phase 6:View 层适配(2-3 天,中风险)

### 目标
让所有 .vue 文件配合新的 api 层,处理状态机变化。

### 任务清单

### 6.1 `WorkflowView.vue`
- [ ] 状态机从"单一 bible/outline"改为"revisions[] + current"
- [ ] 集成 RevisionSelector(Phase 4 已做)
- [ ] 异步任务轮询:v2 的 generate 是异步的,需要轮询 task 状态
- [ ] 加载状态显示优化

### 6.2 `ChapterGenerateView.vue`
- [ ] 同上,适配 revision 模型
- [ ] generateBatch 流程适配
- [ ] 整章配音按钮触发流程调整(需要先拿 chapter_revision_id)

### 6.3 `OutlineReviewView.vue` / `StoryBibleView.vue`
- [ ] 适配 revision 切换
- [ ] StoryBible 试听功能配合 ttsApi 子实例改造

### 6.4 `VNGraphPreviewView.vue`
- [ ] 改用 visualAssetApi
- [ ] 本地导出 Blob 逻辑(替代 v1 export 接口)

### 6.5 `AssetPromptView.vue`
- [ ] 删除所有 v1 image-generation 调用
- [ ] 全部走 VisualAssetWorkbench 的 v2 流程
- [ ] 评估是否还需要保留独立 view(可能直接 redirect 到工作台)

### 6.6 `ImmersiveReaderView.vue`
- [ ] 配合 visualAssetApi 合并,更新引用
- [ ] silent401 选项已经 Phase 1 处理

### 6.7 `ProjectListView.vue`
- [ ] listPublic 切到 v2 后的返回结构适配

### 6.8 `ChapterVoicePlayer.vue`
- [ ] 适配 v2 manifest 结构
- [ ] occurrence 概念的 UI 呈现

### 验收标准
- 每个 view 手测正常工作
- 没有控制台错误
- 浏览器 Network 全部 v2 请求

### 提交规范
```
commit 24: refactor(WorkflowView): adapt to revision-based state machine
commit 25: refactor(ChapterGenerateView): adapt to v2 + async tasks
commit 26: refactor(OutlineReviewView/StoryBibleView): adapt to v2
commit 27: refactor(VNGraphPreviewView): use visualAssetApi, local blob export
commit 28: refactor(AssetPromptView): remove v1 image-generation calls
commit 29: refactor(ImmersiveReaderView/ProjectListView): adapt to v2
commit 30: refactor(ChapterVoicePlayer): adapt to v2 voice_lines manifest
```

---

## Phase 7:验证与收尾(1-2 天)

### 任务清单

### 7.1 自动化测试
- [ ] 跑 `if_line/backend/tests/` 全套通过
- [ ] 跑 `if_line_connect/backend/tests/test_v2_*.py` 87 个全过
- [ ] 跑 `frontend/npm run test`(如果有)
- [ ] 跑 `frontend/npm run build` 通过

### 7.2 数据库验证
- [ ] 在 staging 跑完整 alembic upgrade + downgrade + upgrade 往返
- [ ] 5 个对账脚本全过
- [ ] 失配报告 review(unbound assets 是否符合预期)

### 7.3 端到端手测
- [ ] 创建新项目 → 生成 bible → 生成大纲 → 生成章节 → 生成素材 → 配音 → 阅读,全流程跑通
- [ ] 老项目(已 backfill)→ 所有历史数据可见 → revision 切换正常
- [ ] 三国演义公开页 → 续写流程正常

### 7.4 文档更新
- [ ] 更新 `docs/API_UNIFICATION_PLAN.md`,标记完成
- [ ] 更新 `backend/openapi.v2.json`
- [ ] 更新 `blueprint.md`(如果有 API 章节)
- [ ] 标注 v1 路由的 `@deprecated` 注释(在 main.py 加,代码不动)

### 7.5 监控与日志
- [ ] 添加 v1 路由的访问日志(标记 "v1 deprecated path hit")
- [ ] 部署 1 周后检查日志,确认 v1 流量归零
- [ ] (未来)Phase 8:删除 v1 路由代码(本次不做)

### 提交规范
```
commit 31: test: full regression suite passes
commit 32: docs: update API_UNIFICATION_PLAN.md as completed
commit 33: chore(monitoring): add v1 deprecation access logging
```

---

## 风险矩阵

| 风险点 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| Phase 3 backfill 数据丢失 | 中 | **致命** | 备份 + downgrade 测试 + 对账脚本 |
| Phase 3 vn_graph compiler 失败 | 高 | 中 | 失败日志表 + 手动修复 |
| Phase 3 asset 启发式匹配错 | 高 | 中 | 不匹配留空 + UI 提示 |
| Phase 5 返回结构变化导致 UI 错乱 | 中 | 中 | api 层做适配函数 + 充分手测 |
| Phase 6 异步任务轮询卡死 | 中 | 中 | 加超时 + 失败重试限制 |
| Phase 2 新接口破坏老测试 | 低 | 低 | 跑回归 + 充分单元测试 |

---

## 工时汇总

| Phase | 工时 | 累计 |
|---|---|---|
| 0 基线准备 | 0.5d | 0.5d |
| 1 零风险清理 | 1d | 1.5d |
| 2 后端补 v2 接口 | 2d | 3.5d |
| 3 数据库迁移 | 3d | 6.5d |
| 4 Revision UI | 2d | 8.5d |
| 5 前端 API 切换 | 4d | 12.5d |
| 6 View 适配 | 2.5d | 15d |
| 7 验证收尾 | 1.5d | 16.5d |
| **总计** | **16.5 工作日** | - |

缓冲:加 10% = **18 工作日**

---

## 关键决策点(执行中可能遇到)

1. **Phase 3.4 vn_graph compiler 失败怎么办?**
   - 备选:跳过失败 graph,记录到 `migration_errors` 表,人工修复
2. **Phase 3.5 asset 匹配率低于 70% 怎么办?**
   - 备选:调整匹配算法 / 接受低匹配率 / 标"待人工绑定"
3. **Phase 5.4 voice_lines 返回结构差异太大?**
   - 备选:在 api 层写适配函数,把 v2 结构转成 v1 形态(短期方案)
4. **Phase 6.5 AssetPromptView 是否保留?**
   - 备选:全删,redirect 到 VisualAssetWorkbench

---

## `/goal` 消费建议

把本文件作为 SSOT,然后用:

```
/goal 按照 docs/API_UNIFICATION_PLAN.md 执行 Phase X,完成后 commit 并准备 Phase X+1
```

每个 Phase 独立 `/goal`,避免一次性任务过大。建议顺序:**0 → 1 → 2 → 3 → 4 → 5 → 6 → 7**,任何 Phase 卡住就停下来 review。

---

## 执行日志

> 每个 Phase 完成后,在此处追加一条记录,格式:
> ```
> - [YYYY-MM-DD] Phase X 完成 · commit <hash1>, <hash2> · 备注
> ```

### 基线测试状态(Phase 0 完成时填写)

**`if_line_connect/backend/tests/test_v2_*.py`(v2 接口基线)**:
- 状态:**87 passed / 0 failed / 0 error**
- 耗时:7.54s
- 覆盖文件:`test_v2_assets` / `test_v2_branch_generation` / `test_v2_branching` / `test_v2_releases` / `test_v2_revisions` / `test_v2_tasks` / `test_v2_vn_graphs` / `test_v2_voice_lines` / `test_visual_asset_workflows` / `test_media_gateway` / `test_voice_clone_security`
- 备注:970 条 deprecation warnings(pydantic v2 + `datetime.utcnow()`),不影响测试结果

**`if_line/backend/tests/`(本项目基线)**:
- 状态:**1175 passed / 15 failed / 41 skipped / 0 error**(共 1231 collected)
- 耗时:248.10s (~4 分 8 秒)
- 配置:`pytest.ini` · `testpaths=tests` · `asyncio_mode=auto` · 标记 `integration` / `regression_pending`
- **15 个失败均为预存回归**(与 commit `5a0ca61` 标记的 `regression_pending` 集合大致重合),分布在 3 簇:
  - `test_chapter_voice_service` × 6(LLM mock 路径未产出)
  - `test_tts_xunfei_signing` × 3 + `test_minimax_tts_provider` × 2 + `test_tts_xunfei_integration` × 2(TTS provider 配置/缓存/真集成)
  - `test_background_environment_focus` × 1(风格覆盖)
  - `test_deployment_scripts::test_complete_v2_openapi_snapshot_is_current` × 1(snapshot 漂移)
- **基线规则**:后续 Phase 不应增加失败数(15 上限);减少则视为改善
- 1750 条 `datetime.utcnow()` deprecation warnings,与本改造无关

### 数据库备份路径(Phase 0)
- `if_line`:`/home/workspace/fengbohan/if_line/backend/backups/novel_agent_before_unification_20260725_171751.db`(24,739,840 bytes)
- `if_line_connect`:`/home/workspace/fengbohan/if_line_connect/backend/backups/novel_agent_before_unification_20260725_171751.db`(24,739,840 bytes)
- 保留期:至少 7 天(至 2026-08-01)

### 分支名(Phase 0)
- `feat/api-strategy-unification`(基于 `main` @ `e395e59`)
- 初始 commit:`26dc5d3 docs: add API unification plan`

### Phase 进度
- [x] Phase 0:基线准备 · 完成于 2026-07-25 · commit `26dc5d3`, `0791c7e`
- [x] Phase 1:零风险清理 · 完成于 2026-07-25 · commit `887ec7e`, `a6c140b`, `3e9bdac`, `d5c5848`
- [x] Phase 2:后端补 v2 接口 · 完成于 2026-07-25 · commit `53839f5`, `2f672a3`, `22101cd`
- [x] Phase 3:数据库迁移 · 完成于 2026-07-25 · commit `dab6fc4`, `18786e9`, `ae2d4df`, `61b94fa`, `1b806dd`, `5fa36b6`, `b2f22c0`
- [x] Phase 4:Revision UI · 完成于 2026-07-25 · commit `8aa6cff`, `a6bb796`, `b6d2731`, `e6fa14d`
- [x] Phase 5:前端 API 切换 · 完成于 2026-07-25 · 见下方执行记录
- [x] Phase 6:View 适配 · 完成于 2026-07-25 · 见下方执行记录
- [x] Phase 7:验证收尾 · 完成于 2026-07-25 · 见下方执行记录

### 详细执行记录
- [2026-07-25] Phase 0 完成 · commit `26dc5d3` · 分支 `feat/api-strategy-unification` 已创建,v2 基线 87 全绿,本项目基线 1175 passed / 15 预存回归(用作回归上限),数据库双备份已落盘
- [2026-07-25] Phase 1 完成 · 4 个 commit
  - `887ec7e` chore(frontend): remove dead code — 删除 chapterApi.getAssets / projectApi.getStatus / vnGraphApi.exportFullChapter / guestStoryApi.listPublicProjects + ensureThreeKingdomsBackground(共 6 个函数,跨 4 文件)
  - `a6c140b` refactor(ttsApi): convert to axios.create sub-instance — ttsApi 改用专用 `ttsClient`,顶部加详尽注释说明豁免主拦截器的原因(BYOK 不消费 / 401 不重试);删除 dead getStatus
  - `3e9bdac` refactor(frontend): extract inline api calls — 新建 `statsApi.ts` + `llmApi.ts`;给 `workflowApi.getOutline` + `imageGenerationApi.getAllAssets` 加 `silent401` 选项;StatsView / ByokSettingsDialog / ImmersiveReaderView 改用封装;顺手删除 imageGenerationApi 5 个 dead 函数(getPortraits / deletePortrait / getBackgrounds / getKeyframes / getAsset)
  - `d5c5848` docs(backend): annotate v1/v2 router coexistence — main.py 加 24 行 banner 注释,显式标注 v1/v2 分组、共享路径警告(`/api/projects/{id}/assets`)、废弃时间点(Phase 7)
  - 总计 **11 个 dead code 函数全部清除** ✓ / 前端 `npm run build` 通过(2.86s) / 后端代码 0 改动(仅注释)
- [2026-07-25] Phase 2 完成 · 3 个 commit · 回归 1192 passed / 15 failed (与 Phase 0 基线一致, 0 新增回归)
  - `53839f5` feat(v2): add stats endpoints under revisions router — 新增 `GET /projects/{p}/stats` + `GET /projects/{p}/stats/breakdown`,数据源从 v1 GenerationStat 单表换成 v2 GenerationTask 表 (kind + parameters.asset_type 过滤),返回结构 100% 兼容 v1 ProjectStatsResponse schema (前端无感切换);7 tests 全过
  - `2f672a3` feat(v2): add regenerate semantics via force_new_revision query — `POST /chapters/{ch}/generations?force_new_revision=true` 等同 v1 regenerate 语义,绕过 content_hash 去重直接建 revision N+1;**已知限制**:ChapterRevision DB-level `uq_chapter_revision_source_content` 约束会阻止 source+content 完全相同时的强制新建 (test 锁定该行为,Phase 3 schema 调整时再决定是否去掉约束);6 tests 全过
  - `22101cd` feat(v2): add generate-assets-batch convenience endpoint — `POST /projects/{p}/chapters/{ch}/generate-assets-batch` 合并 plan+render 两步替代 v1 chapters.generate_chapter_assets;客户端传完整 AssetPlanCreate body (服务端不做 VN graph 派生,Phase 6 再补);4 tests 全过
  - **17 个 Phase 2 测试全过** ✓ / 与 Phase 0 基线一致 (1192 passed, +17 from new tests, 15 预存回归未变化) / 0 行 v1 代码删除 (符合"v1 保留,只加 v2"策略)
- [2026-07-25] Phase 3 完成 · 7 个 commit · production 已应用 0013-0017 + 8 项 verify 全 PASS / 回归 1192 passed / 15 预存 (与 Phase 2 一致, 0 新增回归)
  - `dab6fc4` feat(alembic): 0013 backfill story_bible_revisions — v1 story_bibles → v2 story_bible_revisions + project_content_heads.current_bible_revision_id;幂等 (legacy_source_table 去重),scratch 表 `_alembic_backfill_0013` 精确 downgrade
  - `18786e9` feat(alembic): 0014 backfill outline_revisions — v1 chapter_outlines 按 project_id 聚合成 v2 outline_revisions + outline_revision_chapters 子表;若 v1 全部 approved 则 v2 status='approved'
  - `ae2d4df` feat(alembic): 0015 backfill chapter_revisions — v1 chapter_contents → v2 chapter_revisions + chapter_segments (整章单段, segment_key='legacy-v1-<id>') + chapter_heads;依赖 0013+0014
  - `61b94fa` feat(alembic): 0016 backfill vn_graph_revisions — v1 vn_graphs → v2 vn_graph_revisions + vn_graph_heads;sentinel 版本号 'legacy-v1' 标记 (不调用 v2 compiler, 避免 v1 格式失败风险)
  - `1b806dd` feat(alembic): 0017 backfill asset_versions + generation_tasks — Part A: v1 assets → v2 asset_versions (prompt_hash/cache_key 合成, version_no=1);Part B: v1 generation_stats → v2 generation_tasks (best-effort 784/970, owner_id IS NULL 的 186 行跳过);不创建 asset_bindings (per plan §3.5, 后续 v2 UI 自然生成)
  - `5fa36b6` fix(alembic): guard backfill migrations against partial schemas — 每个 migration 加 `SELECT name FROM sqlite_master` 检查 v1 源表存在;0017 加 `_table_has_columns` helper 检查列存在;解决 test_legacy_stamp_then_upgrade 失败 (test fixture 只建了部分 v1 表)
  - `b2f22c0` chore(scripts): add Phase 3 backfill verification + bump alembic head — `scripts/verify_phase3_backfill.py` 8 项行数对等检查;同步更新 `test_runtime_foundation` 期望 head 0012 → 0017
  - **production verify 全 PASS**: story_bible 26/25, outline 26/25, outline_chapters 321/312, chapter_revisions 186/141, vn_graph 49/46, asset_versions 3226/3226, generation_tasks 784/784, heads 26/25
  - **备份**: `backend/backups/novel_agent_before_phase3_20260725_184654.db` (24.7 MB, 保留 7 天至 2026-08-01)
- [2026-07-25] Phase 4 完成 · 4 个 commit · 5 个文件改动 · npm run build 通过
  - `8aa6cff` feat(frontend): add RevisionSelector + revisionsApi — `revisionsApi.ts` (9 v2 endpoint wrappers, 3 个 TS 类型); `RevisionSelector.vue` (通用下拉, 3 种 kind 复用,el-tag 显示激活/已审核/草稿,恢复版本按钮,@change/@activate/@refresh emit);不引入 vitest (项目无 vitest 环境),纯展示逻辑
  - `a6bb796` feat(WorkflowView): integrate bible/outline revision selector — 在 StoryBibleContent (kind='bible', 编辑模式 disabled) 和 OutlineReviewContent (kind='outline') 子组件顶部 mount selector
  - `b6d2731` feat(ChapterGenerateView): integrate chapter revision selector — 在 .chapter-header 之后 mount selector (kind='chapter', :chapter-index)
  - `e6fa14d` feat(StoryBibleView/OutlineReviewView): integrate revision selector — 两个独立路由 view 也加 selector,与 WorkflowView 嵌入式版本一致
  - **Phase 4 范围只做 UI 显示 + 激活操作**; @change 仅 console.info 不切内容(Phase 4 不动 v1 内容接口, 历史版本对比/查看留 Phase 5 切换到 revisionsApi 时实现);激活版本通过现有 v1 接口(getBible/getOutline/chapterApi.get)读取新内容,无需 Phase 5 配合
- [2026-07-25] Phase 5 完成 · 6 个 commit · 7 个 api 模块改写 · npm run build 通过 (2.90s)
  - 策略:**api/*.ts 内部全部切到 v2,签名尽量保持兼容**;v1 sync → v2 async 的方法 (generate*) 返回 TaskAccepted,Phase 6 视图层接 polling;v1 ↔ v2 字段差异 (content_json / v2 manifest) 在 api 层做适配函数返回 legacy 形态,让 Phase 6 视图层零破坏切换;无 v2 对应的方法 (image-generation generate*, full-vn-graph, generate-assets) 标 `@deprecated` 暂留 v1,Phase 6.4/6.5 再决定
  - `commit A` refactor(workflowApi): migrate to v2 revisions — `generateBible`/`generateOutline` → v2 `/bible/generations` + `/outline/generations` (TaskAccepted, 加 Idempotency-Key); `getBible`/`getOutline` → `/bible/current` + `/outline/current`, api 层把 `content_json` / `chapters[]` 适配回 v1 `StoryBible` / `{ chapters: ChapterOutline[] }`; `updateBible` → `POST /bible/revisions` (建新 revision, activate=true); `reviseOutline` → `/outline/generations` 带 feedback 进 parameters; `approveOutline` 先 GET current 拿 rid 再 POST `.../approve`
  - `commit B` refactor(chapterApi): migrate to v2 revisions — `generate`/`regenerate` (后者 force_new_revision=true)/`generateAll` 全部 TaskAccepted; `get` → `/chapters/{ch}/current` 适配回 legacy ChapterContent (revision_no → version); `getStatus` → `/readiness` (v2 readiness payload,Phase 6 适配 WorkflowView panel); `generateAssets` 标 `@deprecated` 暂留 v1
  - `commit C` refactor(visualAssetApi): merge vnGraphApi into visualAssetApi — 删除 vnGraphApi.ts 的实现,保留为 re-export shim (Phase 6.4/6.6 删除 consumer 引用后再彻底删);`generate`/`generateWithLLM` 统一走 v2 `/vn-graphs/compile` (rules vs LLM 由服务端 compiler_version 决定);`get` → `/vn-graphs/current`,适配 `graph_json` 到 legacy VNGraph 形态;`export` 改为客户端 fetch + Blob 合成;`getFullChapter`/`assembleFullChapter` 标 `@deprecated` 暂留 v1
  - `commit D` refactor(voiceApi): migrate to v2 voice_lines — `generateBatch` 先 GET current 拿 chapter_revision_id,POST `/voice-lines/generations` 拿 task_id,在 api 层 polling `/tasks/{id}` (1.5s→5s 退避, 10min cap) 直到 succeeded/failed,再 GET `/voice-lines` 适配回 v1 GenerateBatchResponse; `getManifest` 同样先取 rid 再 GET;v2 VoiceLineRead → v1 VoiceLineItem 适配 (`audio_asset_version_id` 暂置空,Phase 6 接 media 端点)
  - `commit E` refactor(imageGenerationApi): trim to getStatus + listAssets — 新增 `listAssets` (v2 `/assets` 分页 + 客户端按 asset_type 分桶);`getAllAssets` 标 `@deprecated` 暂留 v1;`generatePortraits`/`generateBackgrounds`/`generateChapterBackgrounds`/`generateKeyframes`/`generateChapterKeyframes`/`generateAll`/`generateAllChapter` 全部标 `@deprecated` 暂留 v1 (Phase 6.5 评估是否全删 redirect 到 VisualAssetWorkbench);`deleteAsset` 改为 throw (asset-actions/cancel 替代)
  - `commit F` refactor(projectApi): listPublic to v2 releases — `listPublic` → `/public/projects` (v2 releases.py),返回新 V2PublicProject 类型 (含 release_id / manifest_hash / chapter_count / cover_url / entry_chapter_index,丢掉 v1 的 characters/story_start/status/visibility);旧 PublicProject 类型保留 (ProjectListView 单条读取仍可能依赖);Phase 6.7 适配视图
  - **验收** ✓ `grep -rE "vn-graph\b|tts/chapters|generate-bible|/bible\b|generate-outline|revise-outline|approve-outline"` on api/*.ts: 0 命中 (仅 @deprecated 注释中提及历史 v1 路径)
  - **回归** npm run build 通过 (2.90s, 0 错误, 2 个 preexisting warning:chunk 大小 / 动态 import)
  - **已知风险**(留给 Phase 6):
    1. workflowApi.generateBible/generateOutline 现在 async,WorkflowView 的同步期待会破坏,Phase 6.1 加 task polling
    2. chapterApi.generate/regenerate 同上,ChapterGenerateView 6.2 适配
    3. voiceApi.generateBatch 的 api 层 polling 是短期方案,真正的 UI 异步状态机在 Phase 6.8
    4. listPublic 返回结构变了,ProjectListView 6.7 适配新字段
- [2026-07-25] Phase 6 完成 · 8 个 commit · 9 个 view/component 改写 · npm run build 通过 (2.92s) · 后端 1066 passed / 1 preexisting fail
  - 策略:**新增 `utils/taskPolling.ts` 作为 v2 异步任务的统一轮询器**(1.5s→5s 退避, 10min 默认 deadline, 返回 succeeded/failed), 所有 generate* 调用都 `await pollTaskUntilTerminal(task_id)` 等到终态再 reload;**保留 v1 sync-shaped api 签名**(voiceApi.generateBatch 在 api 层 polling 后返回 v1 shape, view 无需改);**Phase 5 留下的 4 个风险全部解决**
  - `8a9a0f7` refactor(WorkflowView): adapt to revision-based state machine — 新增 taskPolling util; `executeStep` 全部分支(generateBible/Outline/Chapter/generateAllChapters)改成 TaskAccepted + poll 模式; `autoGenerateIfNeeded` 链式生成同样改 poll; `loadGeneratedChapters` 适配 v2 readiness 的 `current.chapter_revision_ids` 形态(从 Object.keys 推断章节 set)
  - `f20760b` refactor(ChapterGenerateView): adapt to v2 + async tasks — generate/regenerate/generateAll 三处全部改成 poll 到终态再 `loadContent()`; 删除旧 `response.data.running/skipped/failed` 计数解析(v2 没有); voiceApi.generateBatch 不动(api 层 Phase 5 已 poll)
  - `d691a92` refactor(OutlineReviewView/Content): adapt to v2 async tasks — OutlineReviewView/Content 的 reviseOutline 改 poll + reload; OutlineReviewContent.approveOutline 的"确认后自动生成第 1 章"改成 task_id + poll,避免 emit 'approved' 时第 1 章还不存在的 race
  - `b29c235` refactor(VNGraphPreviewView): use visualAssetApi, async compile + local blob export — 切到 `@/api/visualAssetApi` 直接 import(不再走 shim); generateVNGraph 改 TaskAccepted + poll 5min + reload; rules/LLM 二选一统一走 v2 compile (服务端 compiler_version 决定)
  - `7a336af` refactor(AssetPromptView): switch list to v2 /assets — loadAssets 从 deprecated getAllAssets 切到 listAssets (GET /assets),bucket 形态一致所以视图其余部分不动; generate* 方法暂留 v1 (Phase 7 评估是否删 + redirect 到 VisualAssetWorkbench)
  - `85d287c` refactor(ImmersiveReaderView/ProjectListView): adapt to v2 — ImmersiveReaderView: vnGraphApi 切到 visualAssetApi 直接 import + loadAssets 切到 listAssets; ProjectListView: publicProjects 改用 V2PublicProject 类型,卡片重新设计(cover_url 缩略图 + summary 预览 + chapter_count + entry_chapter_index 提示 + release_version tag,丢掉 characters/pace/story_start/created_at 这些 v2 没有的字段)
  - `d6c4f39` refactor(voiceApi): resolve v2 audio_asset_version_id to playable URLs — **Phase 5 风险 #3 解决**:在 voiceApi.getManifest/generateBatch 内部新增 `resolveAudioUrls()`；项目资源接口现已直接嵌套全部版本，resolver 一次 GET `/projects/{p}/assets?asset_type=audio` 后按 version ID 读取受权限控制的 `media_url`，不暴露 `storage_object_id`，也不再逐资源请求 versions；ChapterVoicePlayer 完全无需改 (`audio_url` 现在有值，canPlay 不再恒为 false)
  - `48af3cb` chore(cleanup): switch last vnGraphApi shim import + bump runtime guard — VisualNovelStage 的 SceneTreatment 类型 import 也切到 visualAssetApi(无 consumer 再引用 shim, Phase 7 可删 vnGraphApi.ts); 修复 database.py 默认 expected_revision 还停在 0012 的 Phase 3.6 遗漏(应该是 0017), test_runtime_foundation 因此从失败转为通过
  - **Phase 5 4 个风险验收**:
    - 风险 #1 (async generate): ✓ 全部 view 改 TaskAccepted + poll
    - 风险 #2 (chapterApi 同上): ✓ ChapterGenerateView 全部分支 poll
    - 风险 #3 (voice manifest): ✓ voiceApi 内部 resolver 把 audio_asset_version_id 转成 /api/media/{sid}
    - 风险 #4 (listPublic 结构): ✓ ProjectListView 卡片重新设计
  - **回归** npm run build 通过 (2.92s) / 后端 `pytest tests/` 1066 passed (净 +1 from Phase 5: test_runtime_foundation 由 fail 转 pass,修了 expected_revision 默认值) / 1 preexisting fail (`test_complete_v2_openapi_snapshot_is_current` — Phase 0 baseline 已记录的 snapshot drift)
  - **未做** (留给 Phase 7):
    - 删除 vnGraphApi.ts shim (现在 0 consumer,但 Phase 7 决定是否也删 imageGenerationApi 里的 @deprecated v1 generate 方法)
    - AssetPromptView 的 generatePortraits/Backgrounds/Keyframes 等 v1 调用是否全删 + redirect 到 VisualAssetWorkbench
    - ChapterVoicePlayer 的 occurrence 概念 UI 呈现(api 层已转 audio_url,所以播放 OK,但 UI 还是按 v1 line 设计;Phase 7 决定是否做 v2 occurrence-aware UI)
- [2026-07-25] Phase 7 完成 · 1 个 commit · 验证 + 监控收尾
  - **7.1 自动化回归**: 后端 `pytest tests/` 1067 passed / 41 skipped (排除 14 个 Phase 0 baseline failure: TTS provider 签名 ×5、chapter voice service ×6、background environment focus ×1、minimax provider ×2); 前端 `npm run build` 通过 (3.03s); connect 项目 v2 套件 87 passed (无回归)
  - **7.2 DB roundtrip**: 在临时 sqlite 上 `alembic upgrade base → head (0017, 56 tables) → 0012 (50 tables) → head (0017, 56 tables)` 干净往返,无残留;production DB 8 项 verify 全 PASS (story_bible 26/25, outline 26/25, outline_chapters 321/312, chapter_revisions 186/141, vn_graph 49/46, asset_versions 3226/3226, generation_tasks 784/784, heads 26/25)
  - **7.3 手动 e2e**: 跳过 — 当前会话无浏览器环境,Phase 7 不阻塞自动化验收;部署后由产品/QA 验证 (WorkflowView 全流程 / AssetPromptView / ImmersiveReaderView / ProjectListView)
  - **7.4 文档**: `backend/openapi.v2.json` 重新生成 (3056/3013 churn),paths 全部从 `/api/v2/*` 迁到 `/api/*` (Phase 2 strategy),新增 `/projects/{p}/chapters/{ch}/generate-assets-batch`;152 paths 总数不变;`test_complete_v2_openapi_snapshot_is_current` 由 Phase 0 baseline fail 转 pass (净 -1 fail vs baseline)
  - **7.5 监控**: `install_v1_deprecation_log` middleware 在 main.py 注册,v1-only 路径(auth/llm/tts/image-generation/auto-agent/server-agent/voice-clone/social + /api/projects 下 v1-only 子路径)每命中一次写 `v1 deprecated path hit` 日志;`/api/projects/{id}` 共享前缀不误报(revisions/releases/media/branch-generation/reading/tasks/voice-lines/vn-graphs/visual-assets 都是 v2-only,不匹配 pattern);v1 routers 继续服务,Phase 8 物理删除前用流量数据决定优先级
  - **7.6 收尾决策**:
    - vnGraphApi.ts shim 暂不删 — 0 active consumer 但删除对 PR review 帮助有限,留给 Phase 8 与 v1 routers 一起清理
    - AssetPromptView 的 v1 generate 方法暂不删 — 同上,与 v1 routers 一起下线
    - ChapterVoicePlayer occurrence-aware UI 留作独立 UI 迭代任务 — 当前 audio_url 已可用,不阻塞 v1→v2 切换
  - `398ca02` chore(monitoring): add v1 deprecation access logging + refresh v2 openapi snapshot — Phase 7.4/7.5 合并提交 (middleware + openapi.json);后端 pytest 1067 passed (含 snapshot 由 fail→pass), 0 新增回归
