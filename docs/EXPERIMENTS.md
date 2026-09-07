# 一键开始：30 题故事生成与实验

这一入口把题库展开、公共输入编译、三系统预检、原生批次排队、生成、八维证据采集、校验与条件比较串在一起。不改三个项目的创作方法，不自动修故事，不替失败样本补跑，不自动给五个内容质量维度打分。

**第一次安装并配置自己的模型服务和密钥后，一条命令即可开始生成。** 不存在无需依赖、供应商权限和 API 密钥就能真实生图的模式。运行平台沿用 Ubuntu 24.04 / WSL2 Ubuntu 24.04；macOS 可以离线查看题库，但本页统一原生启动入口要求 Linux。

## 1. 第一次使用

在普通 Ubuntu 用户的终端中取得仓库并进入根目录：

```bash
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
bash tools/experiment.sh setup
```

已有仓库先自行保存未提交改动，再更新到包含本入口的版本。不要覆盖日常工作目录。

`setup` 调用原有 `bootstrap_linux.sh --system all` 安装三个独立环境；没有共同配置时调用原有 `configure_batch.py`；随后进入本地交互配置向导并执行 `doctor`。已有配置不会被默认模板覆盖，但向导会保存你明确输入的修改。

向导逐项确认文字、图片和原生视觉审核的供应商 URL、模型及密钥。URL 必须以 `/v1` 结束，确认目标地址时输入 `USE`。密钥输入不回显，写入权限为 `600` 的 `work/secrets.env`。切换供应商地址必须明确重新输入该地址授权使用的密钥，不会偷偷沿用旧密钥。不要把该 env 文件作为 shell 脚本 `source`，不要提交 Git。

当前原生协议限定公共图片模型为 `gpt-image-2`；向导不会假装支持任意替代图片模型。文字和视觉模型名称以你的供应商实际权限为准。模板出现某模型名不等于供应商保证支持。文字/视觉共用模型参数可在向导中输入 `{}` 清空旧控制参数；图片请求参数继续保持各项目原生方式。

三个系统对同一种模型角色共用同一个配置。视觉服务是原生视觉审核，不是八项评测的外部裁判。安装/配置/doctor 不调用真实模型，但会下载依赖、浏览器和分割模型。首次安装细节及兼容性边界仍见 [快速上手](QUICKSTART.md)。

仅重新配置密钥和模型，不重复安装：

```bash
bash tools/experiment.sh configure
```

已经在 shell 中 export 的同名密钥变量优先于 `secrets.env`。更换密钥时先检查是否有旧变量覆盖；向导会提醒变量名，不会显示其值。

## 2. 一条命令开始真实故事生成

先运行最小真实试验：

```bash
bash tools/experiment.sh quick --allow-pilot
```

终端显示题号、系统、重复次数和总尝试数。确认要产生真实消耗时输入 `RUN`。无人值守运行须显式提供 `--yes`：

```bash
bash tools/experiment.sh quick --allow-pilot --yes
```

这会对 CAMPUS-01 让三个系统分别尝试一次，不是本地 fixture。不会因为某项目失败而反复生成到成功。所有系统先通过预检、建立原生不可变计划，才开始第一个系统的付费生成。

| 预设 | 题目 | 每题每系统重复 | 三系统总尝试 |
|---|---:|---:|---:|
| `quick` | 1 | 1 | 3 |
| `genres` | 6，每种题材第一题 | 1 | 18 |
| `pilot` | 全部 30 | 1 | 90 |
| `full` | 全部 30 | 3 | 270 |

跨题材试跑和完整规模：

```bash
bash tools/experiment.sh genres --allow-pilot --yes
bash tools/experiment.sh full --allow-pilot --yes --out work/experiments/eval30-round1
```

**`full` 表示规模预设，不表示题库获得正式审批。** 内置题目仍是 `pilot`。有真实内容审核记录的正式副本见第 6 节。

默认三个系统依次执行，每个系统并发 1，减少跨系统资源争用。`--concurrency 2` 表示当前系统内部最多两个根运行；本入口不会同时开启三个系统。原生模型并行、审核、预取及重试保留。等待时间比较仍须披露主机其他负载、冷启动和供应商限流，不能声称默认顺序执行就完全消除了时序差异。

**没有适配器总 token、总调用数、总时长或总费用上限。** 4000 字符是阅读观察范围，不是费用上限；原生完整故事图、审核和未选路线预取也可能消耗资源。先检查最小试验的消耗、内存和磁盘，再扩大规模。

## 3. 提示词在哪里，如何使用

题库来源分层保存，不覆盖历史材料：

| 文件 | 内容 |
|---|---|
| `benchmark/source/if_line_eval_prompts_30.v1.md` | 原始 30 题及历史 v1 要求，保留原文件；不要把历史前缀直接作为本轮输入 |
| `benchmark/suites/eval30/prefix.txt` | 本轮统一续写前缀 |
| `benchmark/suites/eval30/catalog.json` | 共同约定、输入/输出合同和原文哈希 |
| 同目录的六个题材 JSON | 每题角色名单、两组选择、固定开头、语义解释和预期公共输入哈希 |
| 同目录 `CONTENT_REVIEW_NOTES.md` | 新增候选解释、角色边界、题目歧义与近重复披露 |

无需环境安装或密钥，就能导出全部 30 份完整、可阅读的提示词：

```bash
bash tools/experiment.sh preview
# 或指定全新目录
python3 tools/eval30.py --out work/eval30-review
# 只列题目
python3 tools/eval30.py --list
```

默认导出到 `work/eval30-preview/`，包含 `PROMPTS_30_COMPILED.md`、独立 case/brief/opening/prefix，以及 `compiled/<case_id>/shared_task.txt` 和三个原生 payload。导出过程调用仓库实际编译器，核对 30 题公共输入是否与提供的 `v4-30-pilot.1` 包逐题一致。已有目录不覆盖。

不要把整份 30 题文件一次性传给模型，不要手工给三个前端分别改写需求。统一入口将编译后的同一题按原生字段传递：IF Line 使用 `extra_requirements`，AI4VisualNovel 使用需求文件，InfiPlot 使用 `worldSetting`。固定开头单列，IF Line 不需要再次手工粘贴到 `story_start`。保持三者相同外部任务，不强制内部 system/history messages 相同。

## 4. 自选题目、重复和路线

```bash
python3 tools/experiment.py \
  --case-ids CAMPUS-02 SCI-FI-03 MYSTERY-04 \
  --repeat 2 --choices 0 1 --concurrency 1 \
  --allow-pilot --run --yes \
  --out work/experiments/custom-round1
```

这是三题乘两次重复乘三个系统，共 18 次尝试。只运行某个系统可增加 `--systems if_line`；也支持显式选择多个系统。不足三系统的批次仍采集证据和汇总，但不调用要求三份输入的比较器。

`--choices 0 1` 是**原生菜单下标**：第一个菜单选第一项，之后选第二项，序列用完后重复最后一项。不是持续交替，也不是语义 C1/C2 自动匹配。原生第一、第二个菜单不一定就是原题的 C1/C2，评审仍要检查。改成 `--choices 1 0` 应创建新的独立实验目录；它不等于从同一个原生故事根穷举出反事实分支。

只准备实验输入与配置，不启动原生服务或模型：

```bash
bash tools/experiment.sh prepare --preset full --allow-pilot \
  --out work/experiments/prepared-round1
```

此操作需要已有 `work/config/batch.local.json`，但不需要有效密钥。单独提供 `--yes` 不会开始生成。准备后启动保存的计划：

```bash
bash tools/experiment.sh resume --out work/experiments/prepared-round1 --yes
```

预检但不生成：

```bash
bash tools/experiment.sh preflight --preset quick --allow-pilot \
  --out work/experiments/preflight-round1
```

## 5. 结果、八项证据与恢复

未指定 `--out` 时自动使用 `work/experiments/<UTC时间>-<随机后缀>/`，每次新实验独立。目录结构：

```text
experiment.json / experiment.sha256  冻结的题目、次数、系统、配置与代码清单
config.json                         共同配置快照，原生相对路径转绝对路径
inputs/                             固定前文、公共输入、来源与编译产物
sessions/                           每次调度的工具阶段和退出状态
if_line/                            IF Line 原生批次，包含 results 与完整 runs
ai4visualnovel/                      AI4VisualNovel 原生批次
infiplot/                           InfiPlot 原生批次
comparison.json                     三系统条件/来源/证据比较，不是质量排名
experiment_summary.json             完成范围、证据覆盖、停止原因、工具状态
EXPERIMENT_REPORT.md                 可读汇总
```

每系统 `runs/<run_id>/` 保留 M1 固定事实、M2 连贯性、M3 人物视觉一致性、M4 等待时间、M5 阅读体验、M6 图文匹配、M7 token、M8 图片数量所需记录。字段及判断边界以 [记录对照](BATCH_RECORDING.md) 为准。M1/M2/M3/M5/M6 的分数仍为待评审，M4/M7/M8 来自记录器；缺失 usage/画面/真实桌面时点不能补零或推断通过。不要只保存最终正文和图片文件夹。

完成后自动调用各批次 `--verify`，三系统齐全时调用 `batch_compare`。汇总不替换或改写任何原生 `metrics.json`，不把内部审核分当成外部质量分。达到窗口不等于有完整结局，`sealed` 不等于生成成功。

中断使用 Ctrl+C。入口会向当前原生批次传递 SIGINT，保留已发送请求的终态收集与原生清理，不用短超时强杀。恢复和只读校验：

```bash
bash tools/experiment.sh resume --out work/experiments/eval30-round1 --concurrency 1 --yes
bash tools/experiment.sh verify --out work/experiments/eval30-round1
```

恢复只启动从未开始的排队项。已开始、失败、已封存或送达未知的任务不再发送；需要新的尝试必须新建目录并保留旧证据。恢复只能调整并发和本地密钥文件位置，不能修改题目、模型、次数或选项策略。新代码、新输入或配置哈希不匹配会拒绝恢复。额外实验级锁防止两个入口同时操作同一目录；原生 scheduler 锁仍保留，不自动删除。

退出码 0 表示全部预定根达到窗口且证据校验及工具流程通过，不代表五个质量维度通过；1 表示流程已汇总但存在范围不足、失败、证据缺失或比较未通过；2 表示入口/预检/工具阶段错误而提前停止；130 表示中断。预检/准备模式的 0 只表示对应操作完成。保留 `experiment_summary.json` 中的各阶段原始退出码和各系统 `stop_reason`。

## 6. 正式内容审核

新增开头和共同解释没有因为上传仓库就变成已批准输入。特别注意：章映射为 C1/C2 的逻辑阶段、角色名单对背景对象的呈现限制、MYSTERY-04 父亲姓名歧义、相近广播站题目、CAMPUS-01 已用于开发。这些见题库的 `CONTENT_REVIEW_NOTES.md`。

先导出并人工阅读全部输入；确认后才执行：

```bash
python3 tools/eval30.py --out work/eval30-review
python3 tools/approve_eval30.py \
  --suite-root work/eval30-review --out work/eval30-approved \
  --reviewer YOUR_REAL_REVIEWER_ID --confirm-reviewed
bash tools/experiment.sh full --suite-root work/eval30-approved --yes \
  --out work/experiments/eval30-approved-round1
```

这里不使用 `--allow-pilot`。审核工具创建新副本及与内容哈希绑定的记录，不自动判断内容正确，也不修改原 pilot。若需要调整事实、角色或窗口，先修订并版本化源文件及哈希再重新审核，不能在某系统表现不好后单独改题。

## 7. 验证范围

```bash
bash tools/experiment.sh test
```

新增测试为纯离线测试：真实公共编译器与三个输入封装校验，加上用明确替身进行的调度/失败/恢复逻辑测试。它们不启动 PostgreSQL、Redis、Pygame、Next 或远程模型，不能代替原生端到端检查。现有原生 fixture 入口仍为 `tools/smoke_test.py`；真实供应商首批生成仍由使用者执行。此次变更不修改 `systems/`、源码锁、原生补丁、编译核心或证据记录器。原生已知失败和 Linux 兼容性风险继续按现有项目文档披露。
