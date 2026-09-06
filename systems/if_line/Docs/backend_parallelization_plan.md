# 后端并行化优化方案

> 范围：把当前后端从「任务级已并行、任务内仍串行」推进到「任务内受控并发」。
> 硬约束：**只改并发结构，不修改任何业务功能、不改变任何对外接口语义、不改变产物内容。**
> 状态：**第一阶段（基础设施 + 受控并发代码骨架）已实现并默认关闭；第二阶段（开关放量 + semaphore 上限压测 + 部署灰度）尚未推进。** 本文档反映 2026-07-17 代码核对结果。

---

## 0. 文档定位

- 本文档是**主方案文档（Source of Truth）**。
- `Docs/Stage_Parallel_AR_Blueprint.md` 是它的可执行分解视图，已逐项落地 17/22。
- `Docs/researches/Stage_Parallel_AR/` 暂未生成研究文档，留待第二阶段压测时产出。
- 本次更新（2026-07-17）**只重写方案文档**，不修改任何 `.py` 代码（用户明确要求："把当前后端转成并行模式的优化方案写成优化文档我看看，改这个不要修改任何其他的功能"）。

---

## 1. 现状判定：项目并非「只能串行」

跨任务并行基础设施早已就位，串行只发生在**单个 Celery 任务内部**。

### 1.1 已经做对的部分（不要动）

| 组件 | 位置 | 作用 |
|------|------|------|
| Celery 多队列 | `backend/app/workers/celery_app.py:21-33` | `text / image / audio / compile / maintenance` 五队列按资源类型隔离 |
| 任务依赖图 | `backend/app/models_v2.py` `GenerationTask` + `GenerationTaskDependency` | 章节间依赖、跨素材类型依赖由 DB 表表达 |
| Outbox 派发器 | `backend/app/workers/tasks.py` `dispatch_outbox` | 1 秒 beat 轮询，事件 → Celery task |
| Lease + Heartbeat | `backend/app/workers/celery_app.py:16-18` | `task_acks_late=True` + `worker_prefetch_multiplier=1` + `task_reject_on_worker_lost=True` |
| 图像并发信号量 | `backend/app/services/image_generation_service.py:81` | `_image_semaphore = asyncio.Semaphore(3)` 进程内全局上限 |
| 图像调用位 | `backend/app/services/image_generation_service.py:265` | `async with _image_semaphore:` 包裹付费调用 |
| TTS 并发信号量 | `backend/app/services/tts_service.py:117` | `_tts_semaphore = asyncio.Semaphore(2)` 供应商 QPS 限流 |
| TTS 调用位 | `backend/app/services/tts_service.py:728, 924` | `async with _tts_semaphore:` 包裹付费调用 |
| 语音批内并发 | `backend/app/services/chapter_voice_service.py:1062` `_synthesize_batch_concurrent` | 已经用 `asyncio.gather`（1077 行），已逼近 TTS 上限 |
| 章节语音批大小 | `backend/app/services/chapter_voice_service.py:65` | `CHAPTER_VOICE_BATCH_SIZE = 5`（可 env 覆盖） |
| DB 连接池 | `backend/app/core/config.py:57-58` | `database_pool_size=20, database_max_overflow=20`（已扩容，承载并行下每分支独立 session） |

**结论**：跨任务并行（不同章节、不同素材类型同时跑）已经可用。瓶颈在**单任务内对一组同质素材的串行 `for ... await`**。

### 1.2 第一阶段改造点（已实现，默认关闭）

下表所有行号均经源码核对（核对日期 2026-07-17）。

| # | 位置 | 已实现的并行形态 | 控制开关（默认 false） | 加速预期 |
|---|------|---------------|---------------------|---------|
| H1 | `vn_graph_assembler.py:1450-1651` `_auto_generate_missing` | 并行路径 `asyncio.gather(_wrapped_bg/_portrait/_keyframe/_voice, return_exceptions=True)`（1610-1615 行），每个分支独立 `SessionLocal()` + finally close；串行路径保留原行为（1627-1650） | `parallel_asset_branches` | 单章 ~1.6-2× |
| H2 | `asset_management_service.py:45-262` `generate_all_portraits` | 阶段 A `_resolve_portrait` 内 `SessionLocal()` 查 cache + 调供应商，`asyncio.gather`（198 行）；阶段 B 主 session 串行 add/commit | `parallel_portrait_generation` | ~3×（受 `_image_semaphore(3)` 限） |
| H4 | `asset_management_service.py:999-1206` `generate_chapter_keyframes` | 阶段 A `_gen_one_kf` 并发供应商调用，`asyncio.gather`（1117-1119 行）；阶段 B 串行持久化 | `parallel_keyframe_generation` | ~3× |
| H5 | `asset_management_service.py:1815-2030` `generate_chapter_backgrounds_v2` | 阶段 A `_gen_one_bg` 并发供应商调用，`asyncio.gather`（1930-1932 行）；阶段 B 串行持久化 | `parallel_background_generation` | ~3× |
| —  | `core/config.py:57-58` | DB pool 已扩容 | `database_pool_size=20, database_max_overflow=20` | — |
| —  | `core/config.py:114-120` | 4 个并行开关 | 默认全 `False`，env 可逐项打开 | — |

### 1.3 已知未改造点（不影响生产行为）

| # | 位置 | 状态 | 影响评估 |
|---|------|------|---------|
| H3 | `asset_management_service.py:539+` `generate_chapter_backgrounds`（v1） | **仍串行** | 生产 assembler 走 `generate_chapter_backgrounds_v2`（H5），v1 是 legacy 同步路径，未被 `_auto_generate_missing` 调用；改造 ROI 极低，列入 backlog 而非阻塞项 |
| M1 | （原 `story_generation_service.py:262-265` N 次串行查询） | **文件已不存在** | 该服务已重构或删除，此项作废 |

### 1.4 不在改造范围内（纯 CPU 内存操作，并行无收益）

- `vn_graph_generator.py` 的节点构建循环（纯内存）
- `vn_graph_assembler.py` 的资产匹配循环（纯内存匹配）
- Celery 队列路由 / outbox 派发 / lease / heartbeat（已经是并行基础设施）

---

## 2. 设计原则（硬约束，第一阶段已遵守）

1. **功能零语义变化**：同一组输入必须产出同一组 Asset / VoiceLine / VNGraph 节点，仅墙钟时间变短。失败语义保持「部分成功 + partial 状态」不变。
2. **DB session 单线程所有权**：SQLAlchemy session 不是并发安全的。已实现保证「同一 session 的 `add/commit/flush` 只在一个协程里发生」。
3. **供应商调用的并发由现有 semaphore 兜底**：`_image_semaphore(3)` 与 `_tts_semaphore(2)` 已经是进程全局，并行化后自动受其限流，**不需要新增限流器**。
4. **不引入新依赖**：仅用标准库 `asyncio`。不引入 `aiohttp`、`httpx` 重构、不改 Celery 版本。
5. **配置可回退**：所有并行度通过 env 变量控制，默认值等于「行为不变」，可逐步放量。
6. **不改对外 API**：HTTP 路由、request/response schema、任务状态机、outbox 事件类型全部不动。

---

## 3. 统一改造模式：两段式（gather + 串行提交）

H2 / H4 / H5 三类循环同构，已套用同一个模式（H1 是横切四分支 gather，模式稍异）。

### 3.1 当前形态（以 portrait 为例，`asset_management_service.py:126-259` 串行路径）

```python
for i in range(0, len(portrait_prompts), batch_size):
    batch = portrait_prompts[i:i+batch_size]
    for prompt_data in batch:
        # 1. 查缓存（self.db 只读）
        existing = self.db.query(Asset).filter(...).first()
        if existing:
            results.append({...}); continue
        # 2. 调供应商（await，受 _image_semaphore 限）
        result = await image_generation_service.generate_portrait(...)
        # 3. 写 DB（self.db.add + self.db.commit）
        asset = Asset(...); self.db.add(asset); self.db.commit()
        results.append({...})
```

问题：步骤 2 的供应商 I/O 是循环里最慢的部分（单张立绘 ~5s），却被前一张的 commit 阻塞。

### 3.2 已实现的并行形态（`asset_management_service.py:129-205` 并行路径）

```python
for i in range(0, len(portrait_prompts), batch_size):
    batch = portrait_prompts[i:i+batch_size]

    # === 阶段 A：纯 I/O 并发，无 DB 写 ===
    async def _resolve_portrait(prompt_data) -> dict:
        own_db = SessionLocal()
        try:
            # cache 查（独立只读 session）+ 调供应商，返回 "cached" 或 "generated"
            ...
        finally:
            own_db.close()

    batch_tasks = [_resolve_portrait(p) for p in batch]
    batch_outcomes = await asyncio.gather(*batch_tasks, return_exceptions=True)

    # === 阶段 B：串行持久化，主 session 单事务 ===
    for prompt_data, outcome in zip(batch, batch_outcomes):
        if isinstance(outcome, Exception):
            failed += 1; results.append({"success": False, ...}); continue
        if outcome.get("cached"):
            results.append(outcome); continue
        # 落库（self.db.add + 逐条 commit；逐条是 stale_existing 替换要求，不是性能取舍）
        asset = Asset(...); self.db.add(asset); self.db.commit()
```

### 3.3 模式的三条不变式（已落地）

1. **阶段 A 不持有主 session 写锁**：cache 查用独立短生命 session（`SessionLocal()` 上下文），供应商调用本身已是 async + semaphore-bounded。
2. **阶段 B 必须串行**：主协程遍历结果列表，所有 `self.db.add` 集中、按现有失败语义逐条 `commit`。
3. **stale-existing 替换逻辑**（"查到旧记录但 prompt 契约变了，替换"）通过 `stale_existing_id` 在阶段 A 收集、阶段 B 在主 session 重查并替换，保证「查 + 写」在同一事务内，不跨协程。

### 3.4 失败语义保持

当前语义：单条失败计入 `failed`，其余继续，整批返回 `partial`。
并行语义：`gather(return_exceptions=True)` 把异常作为元素返回，阶段 B 里分类计数，**对外行为完全一致**。

---

## 4. 单点改造详案（已实现）

### 4.1 H1：`_auto_generate_missing` 四分支 gather（最高 ROI，已实现）

**实现位置**：`vn_graph_assembler.py:1450-1651`

- 分支定义（1487-1552 行）：`_bg_branch / _portrait_branch / _keyframe_branch / _voice_branch`，每个分支接受 `own_db` 参数。
- 调度（1556-1650 行）：
  - `_parallel = get_settings().parallel_asset_branches`
  - 并行路径：每个分支在 `_wrapped_*()` 里 `own_db = SessionLocal()` → `run_stage(stage_fn=lambda: _xxx_branch(own_db))` → `finally: own_db.close()`，最后 `asyncio.gather(return_exceptions=True)`（1610-1615）。
  - 串行路径：`_xxx_branch(self.db)` 顺序 `await`（1627-1650）。
- report 后处理（1652+）：串行遍历 `bg_result / portrait_result / kf_result / voice_result`，`StageReport.record_generated / add_error`。

**关键阻碍的解法**：四个分支都 `AssetManagementService(self.db)` / `ChapterVoiceService(self.db)` 共享同一 session —— 已通过并行路径下每分支独立 session 解决。

**闭包挂载点**：`_voice_branch` 内 `svc = ChapterVoiceService(own_db); _voice_svc_holder.append(svc)`（1516-1523 行），主协程末尾 `svc = _voice_svc_holder[0] if _voice_svc_holder else None`（1681 行）读 `last_summary`，保留 degraded_mode 信息。

**加速预期**：
- 串行总墙钟 ≈ max(背景, 立绘, 关键帧) + 语音 ≈ 25s + 15s = ~40s（图像受 semaphore 排队）
- 并行总墙钟 ≈ max(背景, 立绘, 关键帧, 语音) ≈ 25s（立绘通常最长）
- **单章 ~1.6-2× 加速**（受 `_image_semaphore(3)` 自然限流，因为三个图像分支加起来仍是 ~11 张图，semaphore 让它们排队）

### 4.2 H2 / H4 / H5：三个 asset 循环两段式（已实现）

H2 `generate_all_portraits`（45-262）/ H4 `generate_chapter_keyframes`（999-1206）/ H5 `generate_chapter_backgrounds_v2`（1815-2030）已套用第 3 节的统一模式。每个函数：`if get_settings().parallel_xxx: 并行路径 else: 串行路径`。

**加速预期**：每个函数单看 ~3×（受 `_image_semaphore(3)` 限）。若 H1 已并行化、三个图像分支同时跑，semaphore 会自然把总并发压回 3，**整体图像吞吐不变，墙钟由 semaphore + 总图数决定**。

### 4.3 H3：v1 旧背景路径（不改造）

`asset_management_service.py:539+` `generate_chapter_backgrounds`（v1）仍是纯串行。

**不改造理由**：生产 assembler 走 `generate_chapter_backgrounds_v2`（H5），v1 仅作为 legacy 同步入口，未被 `_auto_generate_missing` 调用。改造 ROI 极低，列入 backlog 而非阻塞项。若未来 v1 被新入口调用，按 H5 模式补改即可。

### 4.4 M1：previous chapter heads 查询（已作废）

`Docs/Stage_Parallel_AR_Blueprint.md` 原登记的 `story_generation_service.py:262-265` 文件已不存在，此项作废。

---

## 5. 不改造清单（避免误伤）

| 文件/循环 | 为什么不动 |
|-----------|----------|
| `vn_graph_generator.py` 节点构建循环 | 纯 CPU 内存操作，非 I/O bound |
| `vn_graph_assembler.py` 资产匹配循环 | 内存匹配，无 I/O |
| `chapter_voice_service.py:1062-1080` `_synthesize_batch_concurrent` | 已 gather，已逼近 TTS 上限 |
| `vn_graph_assembler.py:1543-1552` graph-driven 后的 fallback 兜底 | 时序依赖（必须先 graph-driven 再 fallback），不能并行 |
| Celery 队列路由 / outbox 派发 / lease / heartbeat | 已经是并行基础设施 |
| 任何 HTTP 路由 / schema / 状态机 | 违反「不改功能」约束 |

---

## 6. 第二阶段：开关放量 + semaphore 上限（待推进）

第一阶段代码已就位但默认关闭，第二阶段是**真正放出收益**的工作。这一节**不在第一阶段的「功能零变化」保证内**（因为会改变供应商调用频率，可能触发限流），需要先压测。

### 6.1 开关放量路径

第一阶段默认值全部 `false`，第二阶段按以下顺序逐项打开（每步独立合并、独立回退）：

| 步骤 | 开关 | 验证条件 | 回退动作 |
|------|------|---------|---------|
| 1 | `parallel_portrait_generation=true` | 单章立绘批耗时下降 ~2× 以上，无 429 | `export PARALLEL_PORTRAIT_GENERATION=false` 重启 |
| 2 | `parallel_background_generation=true` | 单章背景批耗时下降 ~2× 以上 | 同上 |
| 3 | `parallel_keyframe_generation=true` | 单章关键帧批耗时下降 ~2× 以上 | 同上 |
| 4 | `parallel_asset_branches=true` | 单章整体墙钟下降 ~1.6× 以上，连接池 checkout 等待无上升 | `export PARALLEL_ASSET_BRANCHES=false` 重启 |

每步放量后必须观察：连接池 checkout 等待、semaphore 等待、供应商 429 率、单章墙钟、`_generation_report.errors` 是否新增未知错误。

### 6.2 semaphore 上限压测（可选第二阶段）

光改并行结构不够，semaphore 是硬天花板。

| 信号量 / 配置 | 当前 | 候选值 | 前提 | 风险 |
|--------------|------|--------|------|------|
| `_image_semaphore` | 3 | 6-8 | 图像供应商并发配额允许 | 429 / 限流 |
| `_tts_semaphore` | 2 | 4-5 | 讯飞 QPS 上限允许 | 错误码 10065 |
| `CHAPTER_VOICE_BATCH_SIZE` | 5 | 10 | 配合上面 semaphore | 内存 |
| Celery worker 进程数 | `worker_prefetch_multiplier=1` | 每队列 2-4 进程 | CPU/内存余量 | 跨进程 session 隔离需复查 |

**关键依赖**：第一阶段 H1 改成每个分支独立 session 后，单任务可能同时持有 4 个 session，`pool_size=20 / max_overflow=20`（已扩容，`core/config.py:57-58`）可承载。**连接池扩容已完成，无需再改**。

### 6.3 待研究的压测项（对应 `Stage_Parallel_AR_Blueprint.md` 的 E1-E5）

- E1 `_image_semaphore` 上限压测方案（3 → 6/8/10，记录 429 率）
- E2 `_tts_semaphore` 上限压测方案（讯飞 QPS 实测）
- E3 Celery 多进程部署方案（按队列拆 worker `-c 2-4`）
- E4 监控指标清单（连接池 checkout、semaphore 等待、单章墙钟、429 率）
- E5 第二阶段灰度策略（按 project_id / 队列 / 时间窗灰度，429 自动降级）

这 5 项每项需要一份 `Docs/researches/Stage_Parallel_AR/<slug>.md` 研究文档，**未在本轮产出**。

---

## 7. 落地顺序与回退策略

### 7.1 落地顺序

- ~~第一步：DB 连接池扩容~~ —— **已完成**（`config.py:57-58`，5→20 / 10→20）
- ~~第二步：H1 四分支 gather~~ —— **代码已实现**（默认关闭）
- ~~第三步：H2 / H4 / H5 三个 asset 循环两段式~~ —— **代码已实现**（默认关闭）
- ~~第四步：H3 v1 路径 / M1 previous chapter~~ —— **前者 backlog，后者作废**
- **第五步（待推进）：开关放量**（见 §6.1）
- **第六步（可选）：semaphore 上限压测**（见 §6.2-6.3）

### 7.2 回退策略

每个开关都通过 env 变量保留串行回退路径：

```bash
# 任何一项出问题，重置为默认 false 即可，无需回滚代码
export PARALLEL_ASSET_BRANCHES=false
export PARALLEL_PORTRAIT_GENERATION=false
export PARALLEL_BACKGROUND_GENERATION=false
export PARALLEL_KEYFRAME_GENERATION=false
```

### 7.3 验证清单（每个开关打开前）

- [ ] 单元测试：`backend/tests/test_vn_graph_assembler.py` 末尾 3 个并行测试全绿（`test_parallel_switch_off_is_default_behavior` @ 1406 / `test_parallel_branches_invokes_all_four_generators` @ 1350 / `test_parallel_branches_one_failure_does_not_block_others` @ 1375）
- [ ] 等价性：对同一 chapter 生成两次（串行 vs 并行），Asset / VoiceLine 集合按业务键（character_id+emotion / scene_location+mood / line_id）相等
- [ ] 失败注入：mock 供应商返回异常，确认 `failed` 计数与串行路径一致
- [ ] 连接池监控：观察 `pool.checkout` 等待时间不上升
- [ ] 供应商 429 率：观察是否触发限流

---

## 8. 风险登记

| 风险 | 触发条件 | 当前缓解 |
|------|---------|---------|
| DB session 跨协程写 | 改造时遗漏某条 `self.db.add` 进了 gather | 阶段 A/B 结构性排除；code review；单测覆盖 |
| 连接池打满 | H1 开关打开但 `pool_size` 未扩 | **已解决**（pool_size=20 / max_overflow=20） |
| 供应商限流 | 第二阶段调 semaphore 上限过激 | 保留旧默认值，env 控制；E1/E2 研究文档待产出 |
| xlog 日志乱序 | 并发分支日志交错 | 已是 async-safe，trace_id 仍在；可接受 |
| `stale_existing` 替换竞态 | 两条 prompt 同时判定同一旧 asset stale | 业务键唯一，DB unique 约束兜底；阶段 B 串行写 |
| `_voice_svc_holder` 闭包挂载 | H1 改造时遗漏 | 已改造为 list 容器（1516-1523 行）；生命周期与 own_db 绑定 |

---

## 9. 配套：AR Blueprint

详细的逐项研究清单见 `Docs/Stage_Parallel_AR_Blueprint.md`，已落地 17/22（A1-A4 / B1-B6 / C1/C3-C7 / D1-D4 全部 `[x]`），剩余 5 项 `[ ]`（E1-E5）是第二阶段压测研究文档。

---

## 10. 本次产物的边界

本文档于 2026-07-17 重写，**只更新方案文档反映代码现状**，未修改任何 `.py` 代码。

**本次明确不做**：
- 不修改 `backend/` 下任何 `.py` 文件
- 不安装 cron / 不启动 tmux worker / 不写 `.cron/` 或 `.ops/` 脚本
- 不生成 E1-E5 研究文档（留待第二阶段压测时产出）
- 不改变任何对外 API、schema、状态机、事件类型
- 不打开任何并行开关（保持默认 `false`）

**本次核对发现的差异**（相对 2026-07-16 版）：
- 行号偏移：`_image_semaphore` 由 `image_generation_service.py:80` 移到 `:81`；`_image_semaphore` 使用点 `:265`；`_tts_semaphore` 使用点 `:728, 924`（原文档只列了 1 处）
- 开关位置：4 个并行开关在 `config.py:114-120`，注释明确标注 H1/H2/H3-H5/H4 归属
- 其他事实（默认值、semaphore 值、连接池大小、H1/H2/H4/H5 实现位置、测试用例位置）均与上一版文档一致
