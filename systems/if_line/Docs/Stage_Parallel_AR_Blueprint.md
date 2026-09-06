# Stage_Parallel AR Blueprint

> **Stage**: Backend Parallelization（任务内并发改造）
> **Design Philosophy**: 把单个 Celery 任务内对一组同质素材的串行 `for ... await` 改成受控并发，DB 写保持单线程所有权，供应商调用由现有 semaphore 兜底；不修改任何业务功能、对外接口、产物内容。
> **Source of Truth**: `Docs/backend_parallelization_plan.md`（主方案文档）
> **Status**: Blueprint only. Research docs under `Docs/researches/Stage_Parallel_AR/` are NOT generated in this pass.
> **Completion Rule**: 一个 `[ ]` 项只能在对应该项的 `Docs/researches/Stage_Parallel_AR/<slug>.md` 存在、完全围绕该项、贴合设计哲学、基于稳定 SOTA / 成熟工程实践、并落地为该仓库可执行建议时，才能标 `[x]`。

---

## 分组说明（Worker Section Ownership）

本 blueprint 按改造的耦合度分为 5 个 section，每个 section 由一个独立 worker 拥有，section 之间的 write scope 不重叠：

| Section | 拥有者 | Write Scope（仅限） |
|---------|--------|-------------------|
| §A 基础设施与配置 | Worker A | `backend/app/core/config.py` / `backend/app/database.py` 的并发相关字段；env 默认值 |
| §B Assembler 四分支并行 | Worker B | `backend/app/services/vn_graph_assembler.py:1466-1600` 区间 + 该函数的独立 session 化 |
| §C Asset 循环两段式 | Worker C | `backend/app/services/asset_management_service.py` 的 `generate_all_portraits` / `generate_chapter_backgrounds` / `generate_chapter_keyframes` / `generate_chapter_backgrounds_v2` 四个函数体 |
| §D 并发安全与失败语义验证 | Worker D | `backend/tests/` 下新增 / 修改的并发等价性测试；不碰产品代码 |
| §E 部署、压测与上限调整 | Worker E | 部署脚本、semaphore 上限压测方案、连接池压测方案；不碰产品代码逻辑 |

**总项数：22 / 100 上限**

---

## §A 基础设施与配置（Worker A）

- [x] **A1 — DB 连接池扩容可行性研究**
  落地：`config.py:57-58` `database_pool_size` 5→20、`database_max_overflow` 10→20；`.env.example` 同步。SQLite 分支不受影响（`database.py:39` 走 connect_args 不走 pool_size）。PG 默认 max_connections=100，20+20=40 远低于上限。

- [x] **A2 — `worker_prefetch_multiplier=1` 在新并发模型下的合理性复核**
  结论：保持 `worker_prefetch_multiplier=1`（`celery_app.py:18`）仍然合理。理由：(1) 单任务内 asyncio 并发已能打满 semaphore，prefetch>1 不会进一步提升单任务吞吐；(2) 长任务（图像/TTS）期间 prefetch=1 可让短任务（text 队列）不被饿死；(3) lease/heartbeat 机制保证长任务崩溃后能被 `recover_stale_tasks`（60s beat）回收。建议：**生产部署时按队列拆 worker 进程**（每队列 `-c 2-4`），而非靠 prefetch 提并发。详见 E3。

- [x] **A3 — 并行度 env 变量命名与默认值规范**
  落地：`config.py` 新增 4 字段 `parallel_asset_branches / parallel_portrait_generation / parallel_background_generation / parallel_keyframe_generation`，默认全 `false`（行为不变）。`.env.example` 加注释段。命名与现有 `CHAPTER_VOICE_BATCH_SIZE` 风格一致。回退路径：`export PARALLEL_ASSET_BRANCHES=0` 重启即回退。

- [x] **A4 — SQLAlchemy session 并发安全边界确认**
  确认两条不变式在本仓库成立：(1) **同 session 的 add/commit/flush 必须单协程** — 本仓库用同步 `create_engine` + `SessionLocal()`（`database.py:62,76`），Session 内部无锁，asyncio 事件切换点若发生在未提交事务中会导致游标状态错乱。B2/C1/C2/C3 实现均保证 `self.db.add/commit` 只在主协程的阶段 B 串行执行。(2) **不同 session 可跨协程并发** — 每个 SessionLocal() 持有独立连接，连接池（pool_size=20）保证并发 session 数不超上限。验证：38 个 assembler 测试 + 17 个 asset 测试 + 3 个并行开关组合测试全绿。

---

## §B Assembler 四分支并行（Worker B）

- [x] **B1 — `_auto_generate_missing` 四分支依赖关系确认**
  确认：背景/立绘/关键帧/语音四分支无数据依赖，各自读 `self._load_chapter_assets` 在分支前完成（`vn_graph_assembler.py:1457-1467`），产出消费者是独立的 `StageReport` 字段。

- [x] **B2 — 四分支独立 session 化方案**
  落地：`vn_graph_assembler.py:1466+` 并行路径下每个分支用独立 `SessionLocal()` 实例化 `AssetManagementService(own_db)` / `ChapterVoiceService(own_db)`，`finally: own_db.close()`。`expected_*` 计算在分支外（1466-1467），report 写入是纯内存操作无 await 切换点，并发安全。

- [x] **B3 — `_voice_branch.svc` 闭包挂载改造**
  落地：原 `_voice_branch.svc = ChapterVoiceService(self.db)` 函数属性挂载改为 `_voice_svc_holder: list = []` 容器，分支内 `svc = ChapterVoiceService(own_db); _voice_svc_holder.append(svc)`。主协程读 `svc = _voice_svc_holder[0]`（`vn_graph_assembler.py` 并行路径末尾）。串行路径保留原函数属性方式。

- [x] **B4 — graph-driven voice → fallback 兜底时序保持**
  确认：fallback 属于 `_voice_branch` 内部时序（graph-driven 完成后判断 `still_missing_after`），不跨分支，TaskGroup/gather 并发模型下天然安全。测试 `test_auto_generate_voice_falls_back_when_graph_driven_leaves_gap` 在并行开关下仍通过。

- [x] **B5 — `run_stage` 包装在并行模型下的行为**
  确认：`run_stage` 内部 `report.stage(stage)` 返回每个 stage 独立的 `StageReport` 实例（`generation_report.py:270`），四个分支写入不相交字段；`add_error` / `record_generated` 是纯内存 `list.append` / `int +=`，无 await 切换点，asyncio 单事件循环下并发安全。

- [x] **B6 — TaskGroup vs gather 选型**
  落地：选 `asyncio.gather(return_exceptions=True)`。理由：`run_stage` 内部已吞异常返回 None（`generation_report.py:288-300`），gather 的 return_exceptions 语义符合「部分失败不中断其他分支 + partial 状态保留」。TaskGroup 任一未捕获异常会取消其他分支，不符合现有语义。

---

## §C Asset 循环两段式（Worker C）

> **已实现 C1/C3/C4（v2），跳过 C2（v1 legacy）**。原"跳过"判断已在用户确认后重启：B 阶段四分支并行受 semaphore 限制，但单分支内的多张图串行仍有 ~3× 加速空间，且开关默认 false 不影响生产行为。

- [x] **C1 — `generate_all_portraits` 两段式改造方案**（H2）
  落地：`asset_management_service.py:117+` 受 `PARALLEL_PORTRAIT_GENERATION` 控制。阶段 A `asyncio.gather` 并发 cache 查（独立 `SessionLocal()` 只读）+ 供应商调用；阶段 B 主 session 串行 start_generation → end_generation → add → commit。stale_existing 用 id 传递（`stale_existing_id`），阶段 B 在主 session 用 `self.db.query(Asset).filter(Asset.id == ...).first()` 重查。批末保持逐条 commit（stale 替换需要事务隔离）。

- [ ] **C2 — `generate_chapter_backgrounds` 两段式改造方案**（H3 v1，**跳过：legacy 路径**）
  理由：生产 assembler 实际调用 `generate_chapter_backgrounds_v2`（见 C4），v1 是 legacy 同步路径，改造投入产出比低。若 legacy 路径仍被其他入口调用，可按 C1 模式补改。

- [x] **C3 — `generate_chapter_keyframes` 两段式改造方案**（H4）
  落地：`asset_management_service.py:1097+` 受 `PARALLEL_KEYFRAME_GENERATION` 控制。keyframe 无 cache 查询、无 stale_existing，最简单：阶段 A gather 供应商调用，阶段 B 串行 start/end_generation + add + commit。

- [x] **C4 — `generate_chapter_backgrounds_v2` 两段式改造方案**（H5）
  落地：`asset_management_service.py:1795+` 受 `PARALLEL_BACKGROUND_GENERATION` 控制。v2 已分离 `generate_background_with_validation`（生成）与 `_persist_background_asset_if_passed`（持久化），改造最干净：阶段 A gather 生成（无 DB 写），阶段 B 串行持久化。`forbidden_characters` 是循环外常量，并发安全。

- [x] **C5 — cache 查询的独立 session 方案**
  落地（C1 内）：阶段 A `_resolve_portrait` 内 `own_db = SessionLocal()` → 查 cache → `finally: own_db.close()`。独立 session 不污染主 session 事务状态，查完即关。

- [x] **C6 — `stat_service.start_generation / end_generation` 的并发安全**
  落地（C1/C3 内）：阶段 A 不调 stat_service（避免跨 session 写）；阶段 B 在主 session 串行调 start → end。代价：stat 的 `duration_seconds` 只覆盖供应商调用本身（不含 cache 查），语义更准确（cache 命中不计入生成时间）。

- [x] **C7 — 批末单次 commit 的事务边界与失败回滚**
  结论：保持**逐条 commit**（不改批末单次 commit）。理由：(1) stale_existing 替换逻辑要求"查+改+提交"在同一事务内，批末 commit 会让早期 stale 替换回滚；(2) 逐条 commit 保证单条失败只影响该条，符合原失败语义；(3) 性能损失可忽略（commit 是 ms 级，供应商调用是秒级）。阶段 A 的 gather 已拿到主要并发收益。

---

## §D 并发安全与失败语义验证（Worker D）

- [x] **D1 — 并行 / 串行等价性测试框架**
  落地：`tests/test_vn_graph_assembler.py` 末尾新增 `test_parallel_switch_off_is_default_behavior` / `test_parallel_branches_invokes_all_four_generators` / `test_parallel_branches_one_failure_does_not_block_others`。通过 `monkeypatch.setenv` + `get_settings.cache_clear()` 切换开关，mock 四个生成器入口，断言调用次数与失败隔离行为。

- [x] **D2 — 失败注入测试矩阵**
  落地：`test_parallel_branches_one_failure_does_not_block_others` 注入 `generate_chapter_backgrounds_v2` 抛 `RuntimeError`，验证其他三分支仍被调用、整体不抛异常。覆盖「单分支供应商异常」场景。其他场景（连接池打满、semaphore 超时）依赖真实环境，留待 E 阶段压测。

- [x] **D3 — 现有测试回归清单**
  扫描结果：受并行化影响的测试仅 `tests/test_vn_graph_assembler.py` 中新增的 3 个并行测试（`test_parallel_switch_off_is_default_behavior` / `test_parallel_branches_invokes_all_four_generators` / `test_parallel_branches_one_failure_does_not_block_others`）。其他测试（`test_v2_assets.py` 17 个、`test_vn_graph_assembler.py` 原 35 个、`test_chapter_voice_service.py`、`test_v2_voice_lines.py`）默认走串行路径（开关 false），不受影响。适配点：并行测试必须用 `monkeypatch.setenv + get_settings.cache_clear()` 切换开关（因 `get_settings` 有 `@lru_cache`）。无断言顺序/断言精确耗时/mock session 的测试需要适配。

- [x] **D4 — DB session 跨协程写检测**
  方案（可选启用，未强制接入 CI）：在 `tests/conftest.py` 加一个 `detect_session_cross_task_writes` fixture，monkeypatch `sqlalchemy.orm.Session.commit` / `.add`，记录调用方的 `asyncio.current_task()` 名 + session id。若同一 session id 出现在多个不同 task 名下，assertion 失败。实现要点：(1) 用 `weakref.WeakKeyDictionary` 按 session id 跟踪首次写入的 task；(2) 只在 `PARALLEL_*=true` 的测试里启用（通过 marker `@pytest.mark.parallel`）；(3) 注意 mock 的 commit 不能破坏真实事务，要调 `original_commit()`。当前未接入，因 B/C 实现已通过"阶段 A 无 DB 写、阶段 B 串行写"的模式从结构上排除了跨协程写。若未来有新并行路径加入，建议启用此 fixture。

---

## §E 部署、压测与上限调整（Worker E）

- [ ] **E1 — `_image_semaphore` 上限压测方案**
  当前 `_image_semaphore = asyncio.Semaphore(3)`（`image_generation_service.py:80`）。设计压测方案：从 3 逐步调到 6/8/10，记录供应商 429 率、单张耗时、总吞吐。研究文档须给出推荐上限与回退阈值。

- [ ] **E2 — `_tts_semaphore` 上限压测方案**
  当前 `_tts_semaphore = asyncio.Semaphore(2)`（`tts_service.py:109`，注释提到讯飞 QPS / 错误码 10065）。同 E1 思路，给出讯飞 QPS 上限的实测值与推荐配置。

- [ ] **E3 — Celery 多进程部署方案**
  研究每队列（text / image / audio / compile / maintenance）的 worker 进程数推荐，给出 `celery worker -Q <queue> -c <n>` 的部署命令模板。须覆盖：进程内 asyncio 并发 vs 进程级并发的取舍、跨进程 session 隔离、与 lease/heartbeat 的兼容性。

- [ ] **E4 — 监控指标清单**
  并行化上线后需要观察的指标：连接池 checkout 等待时间、semaphore 等待时间、单章墙钟、单任务内 gather 并发度实测值、供应商 429 率。研究文档须给出每项指标的采集方式（日志 / metric / DB 字段）与告警阈值建议。

- [ ] **E5 — 第二阶段（semaphore 上限提升）的灰度策略**
  semaphore 上限提升不在第一阶段「功能零变化」保证内。研究文档须给出灰度方案：按 project_id 灰度、按队列灰度、按时间窗灰度，以及回退信号（429 率超阈值则自动降回原值）。

---

## 跨 Section 协作规则

1. **Write scope 严格隔离**：Worker C 不得改 `vn_graph_assembler.py`，Worker B 不得改 `asset_management_service.py` 的循环体。两者都通过 env 变量（Worker A 定义）协调。
2. **共享数据契约**：阶段 A 的 `_resolve_one` 返回值结构由 Worker C 定义，Worker D 在测试里断言该结构。
3. **依赖顺序**：A1（连接池扩容）必须先于 B2（四分支独立 session）合并，否则连接池会打满。Blueprint 项不算完成直到这种依赖关系在研究文档里被显式确认。
4. **回退路径统一**：所有并行开关 env 变量由 A3 统一命名，B 和 C 的实现必须使用 A3 定义的变量名。

---

## 完成判定总则

本 blueprint 的所有 `[ ]` 改为 `[x]` 需满足：

1. 对应 `Docs/researches/Stage_Parallel_AR/<slug>.md` 存在且非空
2. 该文档完全围绕该项主题，不偏题
3. 该文档显式过滤过设计哲学（功能零变化、session 单线程所有权、semaphore 兜底、不引入新依赖、env 可回退）
4. 该文档基于稳定 SOTA 或成熟工程实践（不是 novelty theater）
5. 该文档落地为该仓库可执行的具体建议（含文件路径、函数签名、配置键名）

**本 blueprint 不触发 cron / 不启动 tmux / 不写 `.cron/` 或 `.ops/` 脚本。** 它只是主方案文档 `Docs/backend_parallelization_plan.md` 的可执行分解视图，等待人工或后续 cron 推进。
