# 数量、题材与并发控制

本说明补充 [完整实验指南](EXPERIMENTS.md)。保留现有 30 题、配置向导、编译器、记录器与审批流程，只增加任意总次数、按题材筛选和根目录快捷入口。

## 第一次使用

在 Ubuntu 24.04 / Windows WSL2 Ubuntu 24.04 的仓库根目录运行，不提供 macOS 原生适配：

```bash
bash experiment.sh setup
```

安装后跟随现有向导核对文字、图片和原生视觉审核服务的地址与模型，填写自己的密钥。地址要逐项输入 `USE` 接受，密钥输入不回显并留在本机。已有环境只需配置时运行 `bash experiment.sh configure`，不必重新安装。安装、配置和预览不调用付费生成模型。

```bash
bash experiment.sh quick --allow-pilot
```

默认第一题、三个系统各尝试一次，共 3 次；每系统并发 1，系统依次运行。输入 `RUN` 才授权付费生成；自动化时显式使用 `--yes`。数量只是尝试数，不能保证得到同等数量的成功故事。

## 不必跑完整题库

```bash
# 12 次/系统，三个系统共 36 次，每个系统最多 2 个根任务并行
bash experiment.sh run --allow-pilot --count 12 --concurrency 2

# 单系统、科幻五题循环选取，总共 7 次
bash experiment.sh run --allow-pilot --systems if_line --genres SCI-FI \
  --count 7 --concurrency 2

# 校园五题 + 悬疑五题，每系统共 13 次
bash experiment.sh run --allow-pilot --genres CAMPUS MYSTERY --count 13 --concurrency 3

# 自定义两题的顺序，共 7 次/系统，分别得到 4 次和 3 次
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 SCI-FI-01 --count 7

# 两题每题每系统重复 2 次，共 4 次/系统、12 次总尝试
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 SCI-FI-01 --repeat 2
```

`run` 默认候选集合是全部 30 题；没有给 count/repeat 时每题每系统一次。`quick` 仍默认第一题一次。`genres` 规模预设代表六类各取第一题，区别于 `--genres SCI-FI` 选中科幻类所有五题。原有 `pilot`、`full` 等预设保留，但没有必须运行 270 次的要求。

| 参数 | 定义 |
|---|---|
| `--count N` | 每个所选系统总尝试 N 次，按选中题目的顺序循环 |
| `--repeat R` | 每个题目、每个系统重复 R 次；不能与 count 同时使用 |
| `--concurrency C` | 当前活跃系统最多并行 C 个根故事任务；默认 1 |
| `--systems ...` | 一个或多个系统名：if_line、ai4visualnovel、infiplot；默认全部 |
| `--case-ids ...` | 按输入顺序选择指定题目；不允许重复 ID |
| `--genres ...` | 按题库顺序选取指定题材全部题目；不能与 case-ids 同时使用 |
| `--out PATH` | 新实验目录，不覆盖已存在目录 |
| `--yes` | 明确授权真实付费运行，跳过交互确认；不是费用上限 |

题材名为 CAMPUS、SCI-FI、MYSTERY、FANTASY、HISTORY、EMOTION。总尝试数 = count × 所选系统数，或选中题目数 × repeat × 系统数。三个系统默认依次运行，所以 `--concurrency 2` 不是同时让三个系统各跑两份。原生系统内部仍可能并行调用、预取和审核，根任务数不等于供应商 HTTP 并发。

当 count 不是题目数的整数倍，靠前题目会多一次。例如两题、count=7，按 A/B/A/B/A/B/A 分配为 4/3。count 小于选中题目数量时，靠后题目可能一次也没运行；`attempts_by_case` 会明确记为 0，并在终端提示。**这里的 0 是计划尝试次数，不是把未知 token、耗时、质量或图片数据填成 0。** 实际结果仍需查看各系统 results.json。

## 同题创作、统一评判

相同的是公开 brief、固定开头、共同模型角色配置与观察范围。允许不同剧情、行动含义和人物设计，不要求跨系统语义路线一致。原生下标策略照常记录，不调用额外 AI 匹配选项。题目规定的事实和 C1/C2 仍是各自任务要求，但任意两个菜单不会自动算作完成 C1/C2。

本入口只生成、记录八维证据、校验并汇总，不进行外部 AI 评分、分数反馈或失败自动重试。M1/M2/M3/M5/M6 等待独立评审；M4/M7/M8 用真实记录，未知保持未知。4000 字符阅读窗口不是总调用量或费用上限。

## 预览、检查计划、恢复

```bash
# 不需配置或密钥：查看题目与导出全部完整提示词
bash experiment.sh list
bash experiment.sh preview --out work/eval30-preview

# 需要本机配置，但不调用模型：建立指定规模的冻结计划
bash experiment.sh prepare --preset pilot --allow-pilot --genres SCI-FI \
  --count 7 --concurrency 2 --out work/experiments/plan7

# 开始该计划，或中断后只运行从未开始的排队项
bash experiment.sh resume --out work/experiments/plan7 --concurrency 2

# 不读取密钥、不调用模型的只读验证
bash experiment.sh verify --out work/experiments/plan7
```

计划存入 experiment.json，新增 `count_mode`、`attempts_by_case`，并披露独立剧情实验原则。count 模式下 `repeat=null`，不伪装成每题重复相同次数。模型、题目、数量和下标策略在恢复时不能更改，改变这些需要创建新实验；只允许按既有规则调整并发，所有失败和已发送请求记录保留。

根 `experiment.sh` 仅转发到 `tools/experiment.sh`，不是第二套编排器。已有命令、文件布局、配置向导和题库人工审核工具仍使用原实现。

## 正式题库仍需人工批准

`--allow-pilot` 明确运行开发候选；上传许可不是题目内容审批。正式实验提供 `--suite-root` 指向人工审核并用 `tools/approve_eval30.py` 建立哈希记录的副本，**不添加 `--allow-pilot`**。已有编译器会拒绝 pilot、缺失审批或审批哈希不匹配的输入，不能为方便启动而自动批准。

```bash
bash experiment.sh run --suite-root work/eval30-approved-v1 --count 7 --concurrency 2
```

本次入口代码有变化，旧版本的冻结实验不会被强行续跑。保留旧提交用于旧批次；新入口使用新的输出目录。完整输出结构、八维证据定位、已知原生风险和真实生成边界见原实验指南。
