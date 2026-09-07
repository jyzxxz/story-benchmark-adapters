# 三系统共同输入适配器

同一份故事设定、固定开头和续写要求，原样交给 IF Line、AI4VisualNovel、InfiPlot，由三个项目各自的原生流程生成图文故事，并保存八项评测需要的证据。

## 一键开始生成与实验

**已经加入 30 题公共测试输入、使用说明和统一实验入口。** 首次在普通 Ubuntu 24.04 用户或 WSL2 Ubuntu 24.04 中安装依赖并填写自己的供应商和密钥：

```bash
bash tools/experiment.sh setup
```

向导显示各模型请求将发送的地址，逐项输入 `USE` 确认；密钥输入不回显，只保存在 `work/secrets.env`。安装和配置不调用付费模型。没有密钥、模型权限或原生依赖时不能直接真实生成。

环境配置完成后，一条命令让三个系统开始最小真实试验：

```bash
bash tools/experiment.sh quick --allow-pilot
```

查看尝试数后输入 `RUN`，或明确添加 `--yes` 用于自动化。常用规模：

```bash
# 六类题材各一题，每个系统各尝试一次，共 18 次
bash tools/experiment.sh genres --allow-pilot --yes

# 30 题，每题每系统重复 3 次，共 270 次尝试
bash tools/experiment.sh full --allow-pilot --yes --out work/experiments/eval30-round1
```

默认三个系统依次运行、各自并发 1；`--concurrency 2` 调整单系统并发。数量是尝试数，不保证成功故事数。**没有适配器总费用、token 或时长上限；先检查最小试验，再扩大规模。** 所有系统预检并建立不可变原生计划后才开始付费生成，失败不自动补跑。

[完整一键实验指南](docs/EXPERIMENTS.md) 包含安装、供应商设置、自选题目、次数、路线、恢复、八维数据和正式审核流程。已有原生环境和配置无需重新安装；旧的 `tools/run_batch.py` 单系统入口继续可用。

## 查看提示词，不调用模型

```bash
python3 tools/eval30.py --list
python3 tools/eval30.py --out work/eval30-preview
```

无需原生环境或密钥即可导出 `work/eval30-preview/PROMPTS_30_COMPILED.md`、全部 case 和逐题 `compiled/<case_id>/shared_task.txt`。程序使用本仓库实际编译器，检查 30 题公共输入与提供的 `v4-30-pilot.1` 包逐题一致、三个原生输入封装一致。输出目录已存在时拒绝覆盖。

题目源码与固定开头见 [30 题目录](benchmark/suites/eval30/README.md)，原始 brief 继续保存在 `benchmark/source/if_line_eval_prompts_30.v1.md`。原来的完整多结局前缀和回忆检查是历史材料，不直接用于这轮 v4 生成。

**内置题目仍为 pilot 候选。** 新增开头、章/场解释及角色名单边界须人工确认；上传仓库和编译通过不等于正式内容审批。正式副本由 `tools/approve_eval30.py` 生成与内容哈希绑定的确认记录，见实验指南。`full` 只是规模预设，不等于 approved。

## 结果和八项评测证据

新入口自动完成题库编译、预检、原生批次生成、证据封存、校验和三系统条件比较。未指定目录时，结果保存到 `work/experiments/<UTC时间>-<随机后缀>/`：

```text
experiment.json / config.json       冻结输入、次数、模型和来源条件
inputs/                             公共任务、固定前文及编译产物
if_line/                            IF Line 全部批次和根运行证据
ai4visualnovel/                      AI4VisualNovel 全部批次和根运行证据
infiplot/                           InfiPlot 全部批次和根运行证据
comparison.json                     条件及证据比较，不是质量排名
experiment_summary.json             范围、停止原因、覆盖与工具状态
EXPERIMENT_REPORT.md                 可读汇总
```

八项维度为固定事实与要求、连贯性、人物视觉一致性、等待时间、阅读体验、图文匹配、token、图片数量。M1/M2/M3/M5/M6 仅准备证据，须独立内容评审；M4/M7/M8 使用实际记录和用量，缺失保持未知。不要把 `sealed`、程序退出码或 `comparison_ready` 当作故事质量通过。详细字段见 [八维记录对照](docs/BATCH_RECORDING.md)。

```bash
bash tools/experiment.sh resume --out work/experiments/eval30-round1 --yes
bash tools/experiment.sh verify --out work/experiments/eval30-round1
```

恢复只启动从未开始的排队项，不重发失败或送达未知的尝试。新代码、题目或模型条件不能覆盖旧实验。保留完整 `runs/`，不要只交付正文和图片文件夹。

## 共同任务与原生方法边界

v4 观察固定开头之后的一条实际访问路径，当前题库窗口为 **4000 个新增可见 Unicode 字符**，达到阈值后按统一句界规则停止阅读。不计公共开头、内部规划、菜单和未选预览；不要求全篇结局，因此连续生成的 InfiPlot 可以按相同片段参与。窗口不是成本上限，原生预取、规划、审核及未选分支的消耗仍记录。

公共字符串只编译一次：IF Line 接收 `extra_requirements`，AI4VisualNovel 接收需求文件，InfiPlot 接收 `worldSetting`。三个系统共享外部任务，不强制全部 system/user/history 消息相同，也不要求产生同一篇故事。`--choices 0 1` 是原生下标，序列用完重复最后一项；不是语义 C1/C2 自动路由，不做 AA/AB/BA/BB 全分支探索。

仓库采用**冻结基线 + 独立外置适配器**。IF Line 使用已单独披露的 HTML 正文到脚本修复变体；AI4VisualNovel 和 InfiPlot 原生源码保持冻结。此次题库与实验入口不改 `systems/`、`baseline-lock.json`、原生补丁、编译器或记录器。详见 [公平性边界](docs/FAIRNESS.md)、[IF Line 修复](docs/IFLINE_HTML_PATCH.md) 和 [来源说明](docs/PROVENANCE.md)。

| 项目 | 原始冻结提交 |
|---|---|
| IF Line | `572407fce9b648a4206ac37da6a9f6ed22631da8`，另有已披露修复 |
| AI4VisualNovel | `0faf120244d175866eea3813f053281f5689ab19` |
| InfiPlot | `a60e18bc663caaa134d9323a2b89159b7cc9bd05` |

## 文档与验证

| 需求 | 文档 |
|---|---|
| 新用户一键实验 | [EXPERIMENTS](docs/EXPERIMENTS.md) |
| 从零部署和底层单系统命令 | [QUICKSTART](docs/QUICKSTART.md)、[WSL2](docs/WINDOWS_WSL2.md)、[批量运行](docs/BATCH_RUNNING.md) |
| 项目专用环境和已知风险 | [IF Line](docs/projects/IF_LINE.md)、[AI4VisualNovel](docs/projects/AI4VISUALNOVEL.md)、[InfiPlot](docs/projects/INFIPLOT.md) |
| 30 题来源和内容边界 | [题库](benchmark/suites/eval30/README.md)、[内容审核说明](benchmark/suites/eval30/CONTENT_REVIEW_NOTES.md) |
| 此次新增入口测试范围 | [离线验证记录](docs/EVAL30_VALIDATION.md) |
| 既有原生/Linux 验证 | [交接验证](docs/HANDOFF_VALIDATION_20260907.md)、[整体复查](docs/ADAPTER_AUDIT_20260907.md)、[并行复验](docs/PARALLEL_RECHECK_20260907.md) |
| 历史 v3 文本合同与验证 | [v3 合同](docs/V3_CONTRACT.md)、[运行](docs/RUNNING.md)、[无预算验证](docs/UNLIMITED_VALIDATION.md) |
| 维护和项目结构 | [项目说明](docs/PROJECT_GUIDE.md) |

新增入口的免费回归检查：

```bash
bash tools/experiment.sh test
python3 tools/verify_sources.py
```

编译及离线测试不代表真实供应商可用、所有故事成功、或故事质量通过。旧的原生 fixture 检查继续由 `tools/smoke_test.py` 执行；新机器真实模型首次运行仍需使用者完成。Windows 原生命令行不支持此原生进程管理，使用 WSL2 Linux 文件系统。
