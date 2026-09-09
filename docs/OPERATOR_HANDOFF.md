# 接收项目后：生成故事与运行实验

这份说明面向接手仓库、使用自己的 Windows/WSL2 或 Linux 服务器生成故事的人。三个冻结项目、外置适配器、30 道开放式题目、批量程序、八项评测记录器和离线播放器都在仓库中，无需另找三个上游仓库拼装。

按“安装配置 → 小规模试跑 → 检查证据 → 批量实验 → 交付结果”操作。只阅读故事的人使用 [评审者说明](REVIEWER_QUICKSTART.md)，不需要安装项目。当前教程与历史资料见 [文档索引](README.md)。

## 1. 交接时先约定实验条件

| 内容 | 交接要求 |
|---|---|
| 程序版本 | 仓库链接与提交号；用 `git rev-parse HEAD` 记录，不在一轮实验中更新代码 |
| 模型和供应商 | 共同文字模型、共同图片模型 `gpt-image-2`、共同原生视觉审核模型及参数；接收者准备自己获授权使用的端点与密钥 |
| 题目及审批 | 默认开放式 30 题仍为 `pilot`；试跑可显式允许，正式内容确认按第 6 节操作 |
| 运行规模 | 哪些题、哪些项目、重复次数或总尝试数、并发数和原生菜单下标策略 |
| 交付物 | 完整实验证据交给实验组织者；离线 ZIP 交给盲评者；失败记录一起保留 |

公开仓库可以直接克隆。API 密钥不随仓库提供，不写进聊天、Git 或故事输入。不要复制原开发机的虚拟环境、`node_modules`、私人配置绝对路径代替新机安装。

## 2. 安装与配置

生成环境使用 **Ubuntu 24.04 LTS 普通用户**，安装系统依赖需要 sudo。Windows 先完成 [WSL2 Ubuntu 安装](WINDOWS_WSL2.md)，以下命令在 Ubuntu 终端中运行。无桌面的 Linux 服务器也可生成，采集使用离屏渲染。已保存的离线故事可用 Windows 浏览器直接阅读。

在 Linux 用户主目录操作，不要用 root 运行整个安装器或生成任务：

```bash
sudo apt-get update
sudo apt-get install -y git
cd ~
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
git rev-parse HEAD
bash experiment.sh setup
source work/activate.sh
```

如果交接方指定了提交号，在 `setup` 前切换到该提交；否则记录此次克隆的 `main` 提交。安装会下载 Python/Node 依赖、浏览器、字体及原生抠图模型，**不会调用付费故事生成模型**。不需要分别启动数据库、三个网页或 Docker 容器；批量运行器负责隔离原生服务。

`setup` 随后打开交互配置向导。为 text、image、vision 三种角色逐项填写供应商地址、模型和密钥；地址以 `/v1` 结束。每次输入 `USE` 表示接受该角色的发送地址，密钥输入不回显。图片角色固定为共同的 `gpt-image-2`。

模板中的文字/视觉模型和供应商地址是配置示例，不代表接收者拥有服务权限。换文字模型时核对共同参数，例如不支持模板的 `thinking` 参数时将参数 JSON 改成 `{}`。vision 是原生生成过程的视觉审核，**不是八项评测的外部裁判**。供应商还需支持原生实际使用的文字、图片生成、参考图编辑和视觉输入接口；环境检查不能证明远端支持这些接口。

| 文件 | 用途 |
|---|---|
| `work/config/batch.local.json` | 本机依赖路径和三个项目共同使用的模型配置 |
| `work/secrets.env` | `BENCH_TEXT_API_KEY`、`BENCH_IMAGE_API_KEY`、`BENCH_VISION_API_KEY`，仅本机保存 |
| `work/activate.sh` | 设置本仓库 Python、Node 与浏览器缓存环境，不包含密钥 |

新开终端后回到仓库根目录并 `source work/activate.sh`。只换供应商或密钥时运行 `bash experiment.sh configure`，不用重装。不要 `source work/secrets.env`，运行器会安全读取它。终端已经导出的同名变量优先于密钥文件，换 key 时检查是否仍有旧变量。更改模型或共同参数后使用新实验目录。

安装路径、锁版本与平台验证边界见 [安装说明](QUICKSTART.md) 和 [Linux 环境锁](../tools/linux/README.md)。

## 3. 确认题目和环境

在仓库根目录检查环境、查看题目，并导出完整公共输入：

```bash
python3 tools/doctor.py --system all
python3 tools/verify_sources.py
bash experiment.sh list
bash experiment.sh preview --out work/input-review/open-round1
```

预览目录必须全新。`PROMPTS_30_COMPILED.md` 和 `compiled/<case_id>/shared_task.txt` 是完整文本，无需密钥。当前默认为 `v4-30-open-actions-pilot.2`：各题都有共同设定和固定开头，后续关键行动由原生系统产生。命令选择题目时使用 `list` 显示的源题号，例如 `CAMPUS-01`；材料另外记录带 `OPEN02` 的案例 ID，不要混用。

可选的新机器原生接入检查使用本地固定响应，不使用付费供应商：

```bash
python3 tools/smoke_test.py --system all --out work/smoke/handoff-01
```

检查输出中的失败和跳过项。固定响应样本仅验证程序，不能用于故事质量评分；历史测试通过也不代替新机器的真实供应商试跑。验证范围见 [证据索引](EVIDENCE_INDEX.md)。

## 4. 真实小规模试跑

下面命令会调用配置的真实供应商：

```bash
bash experiment.sh quick --allow-pilot --out work/experiments/check-01
```

默认第一题、每项目 1 次，共 **3 次尝试**。终端显示规模，输入 `RUN` 后才开始真实生成；`--allow-pilot` 表示接受候选题库用于此次试跑，不是绕过 API 鉴权，也不是免费运行。脚本先检查所选系统，再派发任务。`--yes` 可以显式跳过交互确认用于自动化；仅在操作者已确定付费规模时使用。

三个系统在统一入口下依次运行，各自内部可并发；AI4VisualNovel 保留原生规划、审核、重写等多次调用。**没有适配器总 token、总时间或总费用上限。** 默认阅读阈值为 4000 个新增可见 Unicode 字符，达到后按统一句界规则截取，实际可能略多；固定开头、菜单、内部规划和未选预览不计入。原生预生成、失败重试和未读预取也可能产生消耗。供应商余额、限流和原生单次限制仍存在。

结束后执行证据校验：

```bash
bash experiment.sh verify --out work/experiments/check-01
```

查看 `EXPERIMENT_REPORT.md`、`experiment_summary.json`、各项目 `results.json` 和实际图文回放。校验不调用模型或补齐故事；其退出状态可能反映范围未达成，须区分“文件校验通过”和“原生生成成功”。确认输入、正文、图片、选择、用量及失败原因后，再扩大规模。图文回放位置见第 8 节。

## 5. 调整题目、数量与并发

下面是互相独立的示例，**按需要选一条**，不是要求全部执行。每次新运行用新目录。

```bash
# 每项目共 12 次尝试，合计 36 次；按所选题目顺序分配
bash experiment.sh run --allow-pilot --count 12 --concurrency 2 --out work/experiments/count12

# 两道题，每题每项目重复 2 次，合计 12 次
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 MYSTERY-01 --repeat 2 --concurrency 2 --out work/experiments/two-cases

# 全部 30 题，每题每项目 1 次，合计 90 次
bash experiment.sh run --allow-pilot --repeat 1 --concurrency 2 --out work/experiments/all30-r1

# 只跑 InfiPlot 的科幻题，共 5 次尝试
bash experiment.sh run --allow-pilot --systems infiplot --genres SCI-FI --count 5 --out work/experiments/infi-scifi
```

| 参数/预设 | 含义 |
|---|---|
| `--count N` | 每项目总尝试数；顺序轮转题目，不保证 N 个成功故事；小于题目数时后面的题不会运行 |
| `--repeat N` | 每题每项目重复 N 次；与 `--count` 互斥 |
| `--case-ids` / `--genres` | 源题号或题材，二者互斥；题材为 CAMPUS、SCI-FI、MYSTERY、FANTASY、HISTORY、EMOTION |
| `--systems` | `if_line`、`ai4visualnovel`、`infiplot` 中的一项或多项；默认全部 |
| `--concurrency N` | 当前活跃项目最多 N 个根故事进程，非 HTTP 请求并发；原生内部还可能并行调用 |
| `--choices 0 1` | 默认菜单策略：第一次选第 1 项，以后选第 2 项；下标从 0 开始，用完重复最后一个，不代表跨项目选择相同剧情 |
| `genres` | 每种题材第一题、各 1 次，三项目共 18 次 |
| `run` / `pilot` 无数量参数 | 全部 30 题各 1 次，三项目共 90 次，并不是 quick |
| `full` | 全部 30 题各 3 次，三项目共 270 次；不表示题目已经正式审批 |

原生菜单没有指定下标时保留实际失败，不按语义替换选项。跨项目同时启动的低层方法见 [批量进阶说明](BATCH_RUNNING.md)；它增加并发总量，不是统一入口默认调度。比较等待时间时保留并发、主机和供应商限流条件。

## 6. 只准备计划、预检，以及正式题库

先检查计划而不派发生成：

```bash
bash experiment.sh prepare --preset pilot --allow-pilot --repeat 2 --out work/experiments/planned-round1
```

这是 30 题 × 2 次 × 3 项目，共 180 次计划。检查 `experiment.json` 中的 `total_attempts`、`attempts_by_case`、`suite_version`、`decision_policy`。确定后开始该计划：

```bash
bash experiment.sh resume --out work/experiments/planned-round1 --concurrency 2
```

`prepare` 不读密钥、不检查原生环境。若还希望预检，在**另一个新目录**使用 `bash experiment.sh preflight --allow-pilot --out work/experiments/preflight-01`，默认是 quick 的 3 次计划。`preflight` 不调用模型，但读取密钥并要求非空，不验证远端鉴权和余额。二者都会创建实验目录；不能随后用 `run` 或再次 `preflight` 覆盖同一目录，启动既有计划用 `resume`。

正式实验前，由真实内容负责人审阅第 3 节导出的全部材料。完成内容确认后才能创建审批副本：

```bash
python3 tools/approve_eval30.py --suite-root work/input-review/open-round1 --out work/approved/open-round1 --reviewer '填写实际内容审核者姓名' --confirm-reviewed
bash experiment.sh run --suite-root work/approved/open-round1 --repeat 1 --out work/experiments/approved-round1
```

必须替换审核者姓名并实际完成审核。这里不使用 `--allow-pilot`；脚本记录内容哈希与人工声明，不自动判断题目或故事质量。改题后重新版本化、预览和确认；不要修改已冻结的 `inputs/`，也不要分别给三个系统改 prompt。

## 7. 中断、换 key 和恢复

正常中断用 Ctrl+C，等待已发送请求终态收集与清理。运行中不要关机、更新代码、移动目录或删锁文件。服务器长任务使用团队已有的持久终端/作业会话，不依赖临时 SSH 窗口。

```bash
bash experiment.sh resume --out work/experiments/check-01 --concurrency 1
```

`resume` 只执行从未开始的队列项，不重发失败、已完成、已启动或送达未知的请求。不要再传 `--count`、`--repeat`、`--allow-pilot` 等冻结参数。调小并发会留档；换 key 可用 `configure` 修改密钥，更换模型/端点或共同参数则新建实验。原生失败要重新尝试时也另建目录，保留旧失败。

实验绑定输出绝对路径、代码、输入和配置。搬到另一台机器或更新运行代码后，不能修改哈希强行恢复。接收者开展新实验应克隆指定代码、在新机配置并新建目录；历史故事仍可从完整封存证据独立导出阅读。

## 8. 本地看故事和交付评审

实际运行结束后会整理所有已封存样本，包括失败/零正文记录：

1. 本地查看：按实验目录 `review-delivery.json` 顶层 `entry_file` 打开 `打开故事.html`；也可进入 TXT 指定 ZIP 同级的 `review/` 文件夹打开，不用先解压。
2. 发给评审者：按 `REVIEW_DELIVERY.txt` 中路径发送 `review.zip`，接收者完整解压后双击 `打开故事.html`，可逐页阅读和切换样本。
3. Linux 服务器无桌面时：把 ZIP 下载到自己的电脑后打开；Windows 阅读无需 WSL、Python 或 API。

目录标明计划数、封存数和缺失数。`ready` 表示交付包就绪，`partial` 表示仍有未封存记录，都不等于故事质量通过。没有封存结果或导出失败时查看 `review-delivery.json`。

旧结果或派生包需重新整理时，用新导出目录：

```bash
bash experiment.sh playback --input work/experiments/check-01 --out work/review/check-01
```

导出不调用模型，不改变原始证据。回放展示实际保存的路径，不生成未选分支；共同观察窗口结束不等于全篇结局。InfiPlot 按相同范围参与评价，不仅因没有全篇结局而扣分。完整操作见 [播放器说明](PLAYBACK_REVIEW.md)。

## 9. 实验完成后应交回哪些文件

| 交给谁 | 应保留/交付的内容 |
|---|---|
| 实验组织者 | 完整实验输出目录：`experiment.json`、`config.json`、`inputs/`、三个项目的计划、`runs/`、结果与调度日志、汇总、回放导出报告和 `organizer.json`；另记录代码提交与新机器环境检查结果 |
| 人工盲评者 | `review.zip` 与 [评审者说明](REVIEWER_QUICKSTART.md)；组织者映射、模型配置和原始调用日志留在包外 |
| 外部 AI 评阅流程 | 对应样本的正文、`story.json`、`transcript.md`、`evaluation/` 及引用图片；裁判模型、评阅提示词与消耗另记 |

八项指标为：固定事实与要求、连贯性、人物视觉一致性、等待时间、阅读体验、图文匹配、token 消耗、图片生成数量。五类内容指标（M1/M2/M3/M5/M6）只准备材料，不自动评分；等待时间、token、图片数（M4/M7/M8）来自真实记录。未知、缺失和失败不填成 0，原生审核不当作外部质量分。

不要只交故事或 PNG，不删失败根运行来提高成功率。正文—画面—选择—调用关联及原生消耗以封存证据为准；页数/翻页时间不能代替图片生成数/生成等待时间。字段见 [八项采集规范](BATCH_RECORDING.md)。`work/secrets.env`、依赖环境和缓存不属于评审产物。

## 10. 常见问题

| 问题 | 处理 |
|---|---|
| `Repository not found` / 403 | 仓库当前公开，核对 URL、网络和本机 Git 凭据；不用生成 API key 登录 GitHub |
| 配置向导无法读取终端 | 在交互 Ubuntu 终端运行 `setup/configure`，不要直接放进无终端作业 |
| 安装失败 / root / Chromium / 字体或模型缺失 | 查 [安装说明](QUICKSTART.md) 和项目文档，保留日志；不改 `systems/` 绕过来源检查 |
| API 401 / 429 / 超时 | 核对真实端点、key、旧环境变量、供应商额度与限流，不直接判断为 prompt 错误 |
| AI4 节点数校验失败或调用很多 | 原生图规划和多 Agent 审核保留，失败与消耗如实记录；见 [AI4 说明](projects/AI4VISUALNOVEL.md) |
| 目录已存在 / 冻结哈希变化 | 新实验换新目录；既有计划仅在原路径、原条件下恢复，不覆盖历史证据 |
| 只有开头 / 没有故事 | 查停止原因和新增正文数量；固定开头或可打开的播放器不证明生成成功 |

当前程序、历史真实运行、固定响应测试和平台安装的验证范围见 [证据索引](EVIDENCE_INDEX.md)。本说明是操作流程，不承诺任意新机器、供应商或每次原生生成都必然成功。
