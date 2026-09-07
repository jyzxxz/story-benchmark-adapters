# 三系统共同输入适配器

同一份故事设定、固定开头和续写要求，交给 IF Line、AI4VisualNovel、InfiPlot 各自的原生流程，生成图文故事并保留八项评测证据。

## 当前默认：不预设关键行动

**同题创作、统一评判，不是同一剧情的三次改写。** 默认题库为 `v4-30-open-actions-pilot.1`：保留原 30 题的题材、标题、角色名单和固定开头，不预先指定两组关键选择、选项语义、行动顺序或结局。各系统根据自身剧情生成原生选项。允许不同走向，也不要求故意写出不同走向。

世界规则、既成前文、实际执行的选择仍须遵守。当前角色总名单边界保持不变，本次不扩大原生角色设计能力，也没有把用于说明的升学例子加入题库。

旧版 `v4-30-pilot.1` 源文件保留，旧公共输入哈希保持不变；新版本的 case ID、版本和哈希独立。原始 v1 brief 在 `benchmark/source/if_line_eval_prompts_30.v1.md`。新版使用明确记录差异的派生 brief，**不声称派生文本与原文逐字相同**。范围与变更详见 [不预设行动说明](docs/OPEN_ACTIONS.md)。

## 一键开始生成与实验

首次在普通 Ubuntu 24.04 用户或 WSL2 Ubuntu 24.04 中安装依赖并填写自己的供应商和密钥：

```bash
bash experiment.sh setup
```

向导显示模型请求的目标地址，逐项输入 `USE` 确认；密钥输入不回显，仅保存在本机 `work/secrets.env`。安装与配置不调用付费模型。没有依赖、模型权限和密钥时不能真实生成。本项目不新增 macOS 原生适配。

配置完成后，运行最小真实试验：

```bash
bash experiment.sh quick --allow-pilot
```

根目录 `experiment.sh` 仅转发到既有 `tools/experiment.sh`，两种入口都可用。此命令默认使用新版无预设行动题库。查看尝试数后输入 `RUN`，或显式添加 `--yes` 用于自动化。同一道题，三个系统各尝试一次，共 3 次尝试。失败不自动补跑。

```bash
# 每个系统总尝试 12 次，共 36 次；当前系统并发 2
bash experiment.sh run --allow-pilot --count 12 --concurrency 2

# 只运行 IF Line 的科幻题，总尝试 7 次
bash experiment.sh run --allow-pilot --systems if_line --genres SCI-FI --count 7 --concurrency 2

# 六类题材各一题，每个系统一次，共 18 次尝试
bash experiment.sh genres --allow-pilot

# 自选两题，每题每系统重复 2 次，当前系统并发 2，共 12 次尝试
bash experiment.sh quick --allow-pilot \
  --case-ids CAMPUS-01 MYSTERY-01 --repeat 2 --concurrency 2 \
  --out work/experiments/open-round1

# 也可以只运行一个系统
bash experiment.sh quick --allow-pilot \
  --systems infiplot --case-ids SCI-FI-01 --repeat 5 --concurrency 2
```

**`--count` 是每系统总尝试数，`--repeat` 是每题每系统重复次数，二者互斥。** 支持任意正整数，不要求是题目数的整数倍；按所选题目顺序循环，逐题计划次数和未运行题目写入 `experiment.json`。详见 [数量、题材与并发说明](docs/EXPERIMENT_CONTROLS.md)。

不要求跑完整题库。题目、系统、总尝试数、每题重复次数和并发均由使用者选择。数量是尝试数，不保证成功故事数。默认三个系统依次执行，`--concurrency` 控制当前系统内部的根运行并发，原生内部并行仍保留。

**没有适配器总费用、token 或时长上限。** 4000 字符是阅读观察窗口，不是费用上限。开始付费前会对选中系统预检并建立不可变原生计划。先检查最小试验，再扩大规模。

[完整实验指南](docs/EXPERIMENTS.md) 介绍安装、配置、规模、恢复和八维数据。该指南中的旧题库逐字一致说明仅适用于历史版本，当前任务语义以 [OPEN_ACTIONS](docs/OPEN_ACTIONS.md) 为准。已有原生环境与共同配置无需重装；旧单系统 `tools/run_batch.py` 入口继续可用。

## 查看完整提示词，不调用模型

```bash
python3 tools/eval30.py --list
python3 tools/eval30.py --out work/eval30-open-preview
```

导出 `PROMPTS_30_COMPILED.md`、逐题 case、brief、opening 和 `compiled/<case_id>/shared_task.txt`。`CHANGELOG.json` 保存每题删除/替换的原句及新旧 scope，`history/` 保存历史来源。这些历史材料不进入新版编译 bundle，也不作为新版评审要求。输出目录已存在时拒绝覆盖。

需要检查旧版指定行动题库时，必须显式导出：

```bash
python3 tools/eval30.py --legacy-actions --out work/eval30-legacy-preview
```

`--suite-root` 继续支持自备题库，忠实执行指定副本，不会自动把旧版或已批准的题库改成新版。正式模式仍只接受带人工确认和内容哈希审核记录的副本，不加 `--allow-pilot` 时内置候选题不能开始生成。`tools/approve_eval30.py` 只记录人工确认，不执行内容审阅或质量评分。

## 输出和八项评测证据

未指定 `--out` 时，输出位于 `work/experiments/<UTC时间>-<随机后缀>/`：

```text
experiment.json / config.json       冻结次数、模型、输入与来源条件
inputs/                             当前题库、固定前文及编译产物
if_line/                            IF Line 全部运行证据
ai4visualnovel/                      AI4VisualNovel 全部运行证据
infiplot/                           InfiPlot 全部运行证据
comparison.json                     条件及证据比较，不是质量排名
experiment_summary.json             范围、停止原因及覆盖情况
EXPERIMENT_REPORT.md                 可读汇总
```

八项维度：M1 固定事实与要求，M2 连贯性，M3 人物视觉一致性，M4 等待时间，M5 阅读体验，M6 图文匹配，M7 token，M8 图片数量。新版 `decisions=[]` 表示没有标准行动答案，不等于禁止互动。只对各自实际路径进行事实、连贯性和图文核查，不因走了不同路线而扣分，不因缺少旧版指定选项而扣分。

M1/M2/M3/M5/M6 只准备证据，仍待独立评审；M4/M7/M8 使用实际记录，缺失保持未知。不把 `sealed`、退出码或 `comparison_ready` 当作质量通过。字段见 [八维记录对照](docs/BATCH_RECORDING.md)。

```bash
bash experiment.sh resume --out work/experiments/open-round1 --yes
bash experiment.sh verify --out work/experiments/open-round1
```

恢复只启动从未开始的排队项，不重发失败或送达未知的尝试。更新代码后不可用新代码恢复旧冻结批次，应在旧提交环境处理旧批次，新输入使用新目录。保留完整 `runs/`，不要只交付正文和图片。

## 共同输入与原生方法

共同阅读范围仍为固定开头之后 **4000 个新增可见 Unicode 字符**，按统一句界规则截取，不计公共前文、内部规划、菜单和未选预览，不要求全篇结局。公共字符串只编译一次：IF Line 接收 `extra_requirements`，AI4VisualNovel 接收需求文件，InfiPlot 接收 `worldSetting`。不要求原生 system/user/history 消息相同。

`--choices 0 1` 只是原生下标序列，用完后重复最后一个下标，不是跨系统语义匹配，也不是两组必须出现的故事行动。没有加入 AI 选项匹配器、额外事实提醒、自动补图、外部评分回灌或失败补跑。

仓库采用**冻结基线 + 独立外置适配器**。本次只扩展 v4 公共输入的 `decision_policy=native_generated` 和派生题库工具；原生源码、来源锁、已披露补丁、原生生成/选择方法及八维记录器不变。输入合同仍为 3.0，阅读输出合同仍为 4.0；新 case 的显式策略字段与空指定行动列表由编译器联合校验并绑定哈希，旧 v3 两选择校验不放宽。

| 项目 | 原始冻结提交 |
|---|---|
| IF Line | `572407fce9b648a4206ac37da6a9f6ed22631da8`，另有已披露 HTML 修复 |
| AI4VisualNovel | `0faf120244d175866eea3813f053281f5689ab19` |
| InfiPlot | `a60e18bc663caaa134d9323a2b89159b7cc9bd05` |

详见 [公平性](docs/FAIRNESS.md)、[IF Line 修复](docs/IFLINE_HTML_PATCH.md)、[来源说明](docs/PROVENANCE.md)。

## 文档与检查

[新版语义与验证](docs/OPEN_ACTIONS.md) · [实验指南](docs/EXPERIMENTS.md) · [Linux 快速上手](docs/QUICKSTART.md) · [WSL2](docs/WINDOWS_WSL2.md) · [批量入口](docs/BATCH_RUNNING.md) · [旧题库](benchmark/suites/eval30/README.md) · [历史题库验证](docs/EVAL30_VALIDATION.md) · [既有原生验证](docs/HANDOFF_VALIDATION_20260907.md)

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
python3 tools/verify_sources.py
```

编译和离线测试不等于新供应商可用、原生生成成功或故事质量通过。原生检查仍使用 `tools/smoke_test.py`，真实生成由使用者在配置好的环境中先进行 `quick` 验证。
