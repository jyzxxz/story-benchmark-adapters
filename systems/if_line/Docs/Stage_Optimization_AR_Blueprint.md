# Stage_Optimization_AR_Blueprint

> Optimization-cron-builder 蓝图。设计哲学（一句话）：
> **「让每次 LLM 调用都有可衡量的 ROI，缓存优先于重算，降级优先于扩容。」**
>
> 此蓝图不直接改产品代码，只产出优化研究文档 + policy patch（`[_]` evidence），master 接受后才落 `Docs/optimization/route_decision_patch.md`。

## 与 execution-cron 的关系
execution-cron 跑 P4/P1/P2/P3 实现时，本蓝图的研究文档作为**优化建议源**。execution worker 实现一个 P 项 → 本蓝图对应的优化项评估其 ROI → 产出 patch → master 接受 → 写进 `Docs/optimization/`。

## AR Checklist（≤100 项，按 group 并行）

### G1. LLM 成本基线
- [ ] **OPT-G1.1** 跑一周 baseline，统计 P4 ai_flavor_check / P1 portrait_demand / P1 keyframe_moment / P2 voice_split 4 个新 LLM 入口的 token 用量与单价 → 落 `Docs/researches/Stage_Optimization_AR/G1.1_llm_cost_baseline.md`
- [ ] **OPT-G1.2** 找出 top-3 高 token 低 ROI 调用（例如 ai_flavor 重试 3 次仍失败的场景） → `G1.2_low_roi_calls.md`
- [ ] **OPT-G1.3** 给每个高 ROI 调用定 token 上限阈值，超限报警 → `G1.3_token_budget_thresholds.md`

### G2. 缓存命中率
- [ ] **OPT-G2.1** 在 `background_scene_analyzer_service._cache_get/_set`、`portrait_demand_analyzer._cache_*`、`ai_flavor_check._cache_*` 三处加命中率埋点 → `G2.1_cache_hit_instrumentation.md`
- [ ] **OPT-G2.2** baseline 一周后定命中率下限（默认 60%） → `G2.2_cache_hit_floor.md`
- [ ] **OPT-G2.3** 命中率低于下限的 analyzer 调整 hash key 输入（例如 portrait_demand 把 chapter_contents 截断方式从 3000 字改 2000 字稳定 hash） → `G2.3_hash_input_tuning.md`

### G3. 路由分级
- [ ] **OPT-G3.1** 把 ai_flavor_check 从 `glm-4-plus` 降级测试到 `glm-4-air`（便宜 60%），评审质量是否仍 ≥ 70 分阈值 → `G3.1_ai_flavor_route_downgrade.md`
- [ ] **OPT-G3.2** portrait_demand / keyframe_moment 选片这类「选片」任务降级到 `glm-4-flash`，对比选片质量 → `G3.2_selection_route_flash.md`
- [ ] **OPT-G3.3** 路由 patch 写进 `Docs/optimization/route_decision_patch.md`（标 `[_]` 等 master）

### G4. Worker 吞吐
- [ ] **OPT-G4.1** 监控 `worker_saturation = live_workers / MAX_CONCURRENCY` 与 `integration_backlog` 两周 → `G4.1_throughput_metrics.md`
- [ ] **OPT-G4.2** 低 saturation（<60%）持续 3 tick → 诊断 spawn 失败原因 → `G4.2_low_saturation_diagnosis.md`
- [ ] **OPT-G4.3** 高 backlog（>5 待集成）持续 3 tick → 评估小 diff 批量集成（≤256KiB 合并 apply） → `G4.3_integration_batching.md`

### G5. Prompt 瘦身
- [ ] **OPT-G5.1** 扫描 `Docs/learn/files/` 引用的 prompt 块，找冗余/重复 → `G5.1_prompt_bloat_audit.md`
- [ ] **OPT-G5.2** ai_flavor_check 的 SYSTEM_PROMPT（含套话词表）压测：词表从 ~25 词剪到 15 词，评审质量是否仍能命中前 5 类 issues → `G5.2_prompt_compression.md`

### G6. 反 AI 味词表迭代
- [ ] **OPT-G6.1** 收集 auto_creator 实际生成的章节里仍出现的 AI 味词，补进 ai_flavor_check 词表 → `G6.1_cliche_lexicon_iteration.md`
- [ ] **OPT-G6.2** 评估「跨章重复检测」加成（同一个套话词在多章出现 → 加重扣分） → `G6.2_cross_chapter_repeat.md`

## 完成验收 gate（每个 `[ ]` → `[x]`）
1. `Docs/researches/Stage_Optimization_AR/<id>_<slug>.md` 存在。
2. 文档**全篇围绕该 item**，不发散。
3. 基于稳定的 SOTA 或成熟前沿实践（引用模型 card / 论文 / 行业 benchmark）。
4. 落地为**针对 if_line 的具体建议**（含具体文件路径、阈值、参数）。
5. （master 接受）ROI 估算 ≥ 实施成本。

## policy 变更门
任何 AR 文档建议的 policy 变更（路由降级、阈值调整、词表增删）必须：
- `EvidenceLint`：证据链完整（baseline 数据 + ablation 对比）。
- `ROI` 估算：节省 token / 成本 / 时间 vs 实施工时。
- `ParetoGate`：不损害其他维度（如降级模型不能让 AI 味 score 显著下降）。
- `rollback`：有回滚开关（env flag / 旧 prompt 备份）。
- `master [x]`：master lane 显式接受。

不满足这 5 条的 AR 文档停留在 `[_]`，不进 `Docs/optimization/`。
