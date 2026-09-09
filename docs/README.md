# 文档索引：先选你要做的事

当前交接入口是 **[生成者交接手册](OPERATOR_HANDOFF.md)**：在新机器安装、配置模型、生成故事并运行实验。现行默认任务是开放式 30 题，共同固定开头、共同图文阅读窗口、八项证据与离线回放。旧文本/首选择教程和历史验证结果不能代替这条流程。

## 接收者日常使用

| 需求 | 文档 |
|---|---|
| 接手整个项目并生产故事 | [生成者交接手册](OPERATOR_HANDOFF.md) |
| 安装和第一次试跑 | [快速上手](QUICKSTART.md)、[Windows WSL2](WINDOWS_WSL2.md) |
| 运行前后逐项确认 | [操作清单](../RUN_CHECKLIST.md) |
| 调整题目、次数、并发和预设 | [实验入口导航](EXPERIMENTS.md)、[数量控制](EXPERIMENT_CONTROLS.md)、[开放题库参数](OPEN_ACTION_EXPERIMENTS.md) |
| 本地查看、旧结果导出、整批 ZIP 交付 | [播放器使用说明](PLAYBACK_REVIEW.md) |
| 只阅读并评阅别人生成的故事 | [评审者简明说明](REVIEWER_QUICKSTART.md) |

## 当前实验设计与维护参考

| 主题 | 文档 |
|---|---|
| 30 道题的设定、固定开头和变更 | [当前 PROMPTS](../benchmark/suites/eval30_open/PROMPTS.md)、[变更记录](../benchmark/suites/eval30_open/CHANGES.md)、[开放行动设计](OPEN_ACTIONS.md) |
| 同题、独立剧情和统一评判 | [公平性边界](FAIRNESS.md) |
| 八项指标及正文—画面—调用关系 | [采集字段对照](BATCH_RECORDING.md) |
| 目录职责、版本更新和数据交接 | [项目维护说明](PROJECT_GUIDE.md) |
| 自定义 bundle / 直接三个批量入口 | [进阶批量运行](BATCH_RUNNING.md)，不作为新手默认入口 |
| 原生依赖、进阶安装和已知问题 | [IF Line](projects/IF_LINE.md)、[AI4VisualNovel](projects/AI4VISUALNOVEL.md)、[InfiPlot](projects/INFIPLOT.md)、[Linux 环境锁](../tools/linux/README.md) |
| 来源锁和唯一获准的原生源码修复 | [来源说明](PROVENANCE.md)、[IF Line HTML 补丁](IFLINE_HTML_PATCH.md) |
| InfiPlot 大响应和采集完整性 | [响应记录说明](INFIPLOT_RESPONSE_RECORDING.md) |

## 历史验证记录：用于查证，不是新机器的验收结果

每篇报告里的通过数、失败原因、模型和平台只对应其记载的提交、案例和执行条件。所有原始档案的类型与位置见 **[证据索引](EVIDENCE_INDEX.md)**。

| 验证阶段 | 报告 |
|---|---|
| 开放行动题库、数量控制和初版 30 题 | [OPEN_ACTION_VALIDATION](OPEN_ACTION_VALIDATION.md)、[EXPERIMENT_CONTROLS_VALIDATION](EXPERIMENT_CONTROLS_VALIDATION.md)、[EVAL30_VALIDATION](EVAL30_VALIDATION.md) |
| 离线播放器和整批交付 | [PLAYBACK_VALIDATION](PLAYBACK_VALIDATION.md) |
| 图文批量、并行和 Linux 交接 | [BATCH_VALIDATION](BATCH_VALIDATION.md)、[ADAPTER_AUDIT_20260907](ADAPTER_AUDIT_20260907.md)、[PARALLEL_RECHECK_20260907](PARALLEL_RECHECK_20260907.md)、[HANDOFF_VALIDATION_20260907](HANDOFF_VALIDATION_20260907.md) |
| v2/v3 接入、共同输入和限额调整 | [ACCEPTANCE](ACCEPTANCE.md)、[LIVE_VALIDATION](LIVE_VALIDATION.md)、[CLARIFIED_RETEST](CLARIFIED_RETEST.md)、[V3_VALIDATION](V3_VALIDATION.md)、[UNLIMITED_VALIDATION](UNLIMITED_VALIDATION.md) |

## 已被新流程替代的说明

下面文件保留原路径用于旧实验解释，已在页首标注历史范围。接收者开展新实验不需要照着执行。

| 历史资料 | 已替代的内容 | 当前入口 |
|---|---|---|
| [RUNNING](RUNNING.md)、[RUNNING_V2_HISTORY](RUNNING_V2_HISTORY.md) | v2/v3 文本、首选择、旧环境手工启动 | [生成者交接手册](OPERATOR_HANDOFF.md) |
| [V3_CONTRACT](V3_CONTRACT.md) | 停在第一个未执行选择的返回范围 | [当前公平范围](FAIRNESS.md)、[图文采集](BATCH_RECORDING.md) |
| [BATCH_IMPLEMENTATION_CONTRACT](../benchmark/BATCH_IMPLEMENTATION_CONTRACT.md) | 开发时的任务分工和待实现接口草案 | [批量进阶](BATCH_RUNNING.md)、实际源码 |
| `benchmark/native_shims/` 中的开发接入 README | 原生接入实现与旧模式的局部步骤 | 日常运行按交接手册，开发时按各页标注范围查阅 |

## 为什么有些旧文件仍然保留

- `systems/` 内的 README/Docs 是冻结上游来源的一部分。随意删改会影响来源核验；它们不是本仓库统一实验的安装教程。
- `benchmark/source/`、旧题库及历史案例保留原始材料和版本，不能为整理说明而改变实验输入。当前默认题库由主入口选择，旧题仅在显式选择旧变体时使用。
- `docs/evidence/` 中的原始记录（包括其 README）有历史哈希清单。保留字节原样，由新的 [证据索引](EVIDENCE_INDEX.md) 解释类型和时效，不改旧证据来让它看起来“已更新”。

2026-09-09 的文档整理将公开仓库、完整 30 题、当前图文窗口、实际选择、八项证据和离线播放统一到当前教程；移除了快速上手等文档中重复或过时的主流程，历史材料加上范围与跳转。此整理不修改原生程序、题库、封存证据或指标计算。

该次整理核查了 47 份非冻结文档、305 个相对链接与 110 段 Bash 语法；重新导出 30 题预览，并以 `prepare` 模式核对 6 组示例的题目、次数、开放行动政策与默认选择策略。三项目源码核验、首期证据档案核验和文档格式检查通过。没有重装 Linux/WSL、执行原生故事生成或调用付费 API，不把这次文档验证计作新机器生产验收。
