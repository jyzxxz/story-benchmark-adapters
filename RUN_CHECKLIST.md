# 开放行动实验：运行与交付检查清单

本清单配合现有 [完整使用说明](docs/OPEN_ACTION_EXPERIMENTS.md)，不替换原生生成流程。默认题库为 `v4-30-open-actions-pilot.2`。每题共享设定和前文，不共享预设关键行动；三个系统各自创作选项和后续剧情，用同一套指标检查各自实际故事。

## 开始前

已有仓库先保存未提交改动，再 `git pull --ff-only`。不要在正在执行的旧实验中更换代码。首次在 Ubuntu 24.04 / WSL2 Ubuntu 24.04 普通用户环境中执行 `bash experiment.sh setup`，按向导确认模型服务地址并填写自己的密钥；已有可用环境不必重装。不提供 macOS 适配。

安装和输入预览不调用付费故事模型。供应商权限、图像接口、额度和新机器原生环境仍需真实试跑确认，不能由免费检查推断成功。密钥保存在本机，不把 `work/secrets.env` 上传或作为 shell 脚本 source。

## 先检查本次真正输入的题库

```bash
# 不需要密钥，不调用模型；目录必须未存在
bash experiment.sh preview --out work/open-eval30-review

# 新增的只读验收工具；报告放在输入目录之外
python3 tools/audit_open_inputs.py \
  --suite-root work/open-eval30-review \
  --out work/reports/open-input-audit.json
```

查看导出的 `PROMPTS_30_COMPILED.md`，它包含30份完整公共输入。仓库中的可读题目源为 [PROMPTS.md](benchmark/suites/eval30_open/PROMPTS.md)，修改说明为 [CHANGES.md](benchmark/suites/eval30_open/CHANGES.md)。每次只提交一题，不能把全部30题或历史变更说明追加给某个系统。

验收工具调用现有编译器的 `load_case` 和 `verify_bundle`，检查题目数量和唯一标识、源文件哈希、源case与编译case、三处原生公共字符串、套件与任务哈希、`decision_policy=native_generated`、`decisions=[]`，以及明显残留的旧“第一次选择/第二次选择”指令。它不会修改输入、读取密钥、生成故事或替人批准题目。报告已存在时拒绝覆盖，失败退出码为2。

**`ok=true` 仅表示这些结构和完整性检查通过。它不证明所有文字在语义上都没有限制行动，不证明剧情质量，不证明原生服务和供应商可用。** 仍需阅读有效brief和开头，确认没有把尚未发生的行动、结果或选项偷偷写成必经情节。固定事实与条件性世界规则也不能因为开放行动而忽略。

已有人工作出的正式审批副本，可额外验证：

```bash
python3 tools/audit_open_inputs.py --suite-root work/open-eval30-approved --formal-only
```

对pilot使用formal-only会失败，这是预期行为。这个工具只验证现有审批记录与内容哈希，不会创建审批。正式题库生成方法见完整使用说明中的人工审核流程。

## 再执行你选择的规模

```bash
# 最小真实试跑：第一题，三个系统各一次，共3次
bash experiment.sh quick --allow-pilot

# 三个系统各6次，共18次；当前系统最多2个故事并行
bash experiment.sh run --allow-pilot --count 6 --concurrency 2 \
  --out work/experiments/open-round1

# 只运行IF Line，科幻题循环，共7次
bash experiment.sh run --allow-pilot --systems if_line \
  --genres SCI-FI --count 7 --concurrency 2

# 两题各重复2次，每系统4次，三个系统共12次
bash experiment.sh run --allow-pilot \
  --case-ids CAMPUS-01 MYSTERY-01 --repeat 2 --concurrency 2
```

以上是真实付费生成命令，终端展示规模后输入 `RUN` 才启动；无人值守时由使用者显式加 `--yes`。没有适配器总费用、token或时长上限。4000个新增可见字符是观察窗口，不是费用上限，规划、审核及预生成未选分支也可能产生消耗。

`--count` 是每系统总尝试数，`--repeat` 是每题每系统重复次数，不能同时使用。次数不必是题数的倍数，也不保证成功故事数。题目按选中顺序循环，不够次数时后面的题目可能未运行，应按计划披露。`run` 不给数量会默认90次，所以初次使用quick，之后明确指定count或repeat。

三个系统依次执行。`--concurrency 2` 是当前活跃系统最多两个根故事，不是三个系统各两个同时运行，也不是模型HTTP并发。`--systems` 可选一个、两个或三个；`--genres` 与 `--case-ids` 互斥。

默认 `--choices 0 1` 是第一个原生菜单选第一项，以后都选第二项，不是交替，不是公共行动路线。选项语义由各系统自行生成。菜单没有相应下标时如实失败，不自动改选。固定位置可能有偏好，应作为实验条件披露。

## 生成之后先看状态，再交付证据

```bash
bash experiment.sh verify --out work/experiments/open-round1
# 中断后的队列恢复，只处理从未开始的任务
bash experiment.sh resume --out work/experiments/open-round1 --concurrency 2
```

保留实验目录的 `experiment.json`、公共配置、输入副本、每个系统的 `results.json` / `results.csv`、完整 `runs/` 及汇总。恢复不重发失败、已启动或送达未知的尝试；不能改题目、模型或代码后复用旧实验目录。

`sealed`只表示封存，`comparison_ready`只表示比较条件与证据检查的状态，退出码不等于故事质量通过。逐次检查 `scope_reached`、`stop_reason`、错误、正文与画面覆盖、usage覆盖。只选一个或两个系统时，保留各自结果，不冒充三系统比较。

| 维度 | 交付时保留与检查 |
|---|---|
| M1 固定事实与要求 | 实际新版任务、开头、约束和正文，不用历史C1/C2作标准答案 |
| M2 连贯性 | 实际路径正文、选项、已执行选择与原生出处，不拼接互斥分支 |
| M3 人物视觉一致性 | 角色版本、参考素材与按路径关联的画面；跨系统不要求相同脸 |
| M4 等待时间 | 事件时间、时钟与调度条件；未测的真实桌面时间保持未知 |
| M5 阅读体验 | 匿名正文、对白、选项和截止位置；不按预设结局判断 |
| M6 图文匹配 | 本系统实际正文与实际显示画面的完整对应关系 |
| M7 token | 原始请求响应、供应商usage、失败与重试；缺失不能填0 |
| M8 图片数量 | 请求、候选、资产、实际引用、复用与合成分开记录 |

详细字段见 [BATCH_RECORDING](docs/BATCH_RECORDING.md)。M1/M2/M3/M5/M6仍等待独立内容评审，本项目没有新增自动外部AI评分；M4/M7/M8使用实际记录。不得只交付正文和PNG而丢掉关联、消耗或失败证据。盲审只提供对应评审包及引用素材，不交付密钥或整个私有调用目录。

## 本次补充工具的测试范围

`tests/test_eval30_input_audit.py` 新增20项离线测试，使用实际编译器生成的临时输入，不启动原生服务或模型。测试覆盖源文件和输入篡改、错误政策、重复题号、路径越界、公共哈希不符、pilot/正式审核、只读和报告拒绝覆盖等。复验：

```bash
python3 -m unittest discover -s tests -p 'test_eval30_input_audit.py' -v
```

本次这20项已在会话Linux容器执行并通过，编译器字节与提交 `16b014fa0786b3bd2914a8877ccdb7cced101aa6` 的 `compiler.py` Git blob `16c101cfd9d353d9098c72f29490ded8a876a444` 一致。没有将其声称为完整最终仓库回归、原生端到端运行、WSL2新装验收或真实故事质量验证。此前题库测试的报告由仓库原有验证文档单独说明。
