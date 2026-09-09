# 开放行动实验：协议与命令参考

默认题库 `v4-30-open-actions-pilot.2`，六类题材、30题。共同的是外部题目、初始事实、人物名单、阅读范围及评判标准，不是未来行动。系统自创原生选项，适配器只选择、记录和继续。没有跨系统语义匹配，没有外部 AI 自动评分，没有为凑成功数而自动重生成。

## 1. 安装与配置的现行入口

初次接手、Windows/服务器安装、供应商和密钥配置、第一轮验收请按 [操作者交接手册](OPERATOR_HANDOFF.md) 执行。本页保留实验参数与协议参考，避免维护另一份重复安装教程。命令均在仓库根目录的 Linux/WSL2 终端执行。

三个项目对每种模型角色共用一份 `providers` 配置；图片模型为 `gpt-image-2`。原生视觉审核模型不是八项指标的外部裁判。原生并行、审核、重试和内部消息保留，不由共同任务替换。配置和密钥的具体字段见 [交接手册](OPERATOR_HANDOFF.md) 与 [配置说明](PROJECT_GUIDE.md)。

## 2. 免费查看完整输入

```bash
bash experiment.sh list
bash experiment.sh preview --out work/open-eval30-preview
```

第二条导出30题的 case、brief、opening、共同前缀、三种输入封装和 `PROMPTS_30_COMPILED.md`。不需要原生环境和密钥，使用标准库与本仓库的真实编译器。已有输出目录不覆盖。

源文件在 `benchmark/suites/eval30_open/`：PROMPTS.md 是完整逐题设定与开头，prefix.txt 是统一前缀，CHANGES.md 是修订记录，catalog.json 冻结源哈希和逐题公共输入哈希。旧版原文在 `benchmark/source/` 和 `benchmark/suites/eval30/`。

不要将30道题一起提交为一次请求，也不要手工分别为三个项目加剧情提示。编译后的 shared_task.txt 在 IF Line 的 extra_requirements、AI4VisualNovel 的需求文件和 InfiPlot 的 worldSetting 中完全一致。编译器只做共同任务拼接和字段映射，不另加故事。

## 3. 最小真实试跑

```bash
bash experiment.sh quick --allow-pilot
```

第一题、三个系统各尝试一次，共3次，系统顺序执行、每系统并发1。终端展示规模，输入 `RUN` 才授权调用。没有密钥或原生环境时预检失败，不开始付费生成。无人值守可显式加 `--yes`。

**没有适配器总费用、token或时长上限。** 4000字符仅为新增可见正文的观察范围，公共开头、菜单、内部规划、未选预览不计入；到阈值后按统一句界规则截取，不要求全篇结局。原生全图预生成、审核、修订、未选路线预取和失败重试仍可能消耗资源。

## 4. 自定数量、题材和并发

```bash
# 每个系统12次，三个系统共36次；当前系统并发2
bash experiment.sh run --allow-pilot --count 12 --concurrency 2

# 单系统、科幻五题循环，共7次
bash experiment.sh run --allow-pilot --systems if_line --genres SCI-FI --count 7 --concurrency 2

# 两个系统、校园和悬疑类，共13次/系统、26次总尝试
bash experiment.sh run --allow-pilot --systems if_line infiplot --genres CAMPUS MYSTERY --count 13 --concurrency 3

# 指定两题按给定顺序循环；每题每系统2次，共12次
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 MYSTERY-01 --repeat 2 --concurrency 2

# 六类题材各第一题，共18次
bash experiment.sh genres --allow-pilot
```

| 参数 | 含义 |
|---|---|
| --count N | 每系统总尝试数，按选中题目顺序循环 |
| --repeat R | 每题每系统重复次数，不能与count同时使用 |
| --concurrency C | 当前活跃系统的根故事任务并发，默认1；不是HTTP并发 |
| --systems ... | if_line、ai4visualnovel、infiplot，可选择一到三个 |
| --case-ids ... | 指定题号及顺序，不能重复 |
| --genres ... | CAMPUS、SCI-FI、MYSTERY、FANTASY、HISTORY、EMOTION；与case-ids互斥 |
| --choices 0 1 | 原生选项位置序列，见下一节 |
| --out PATH | 独立输出目录，拒绝覆盖 |
| --yes | 明确授权付费运行，跳过RUN确认，不是费用上限 |

`count`不必是题数的整数倍。两题count=7会分配4/3次；少于选题数时后面的题可能未访问，计划明确记录0次。这不是把缺失质量、token或耗时填为0。次数是尝试数，不保证成功。

`run`未给数量时默认全部30题、每题每系统1次，共90次，所以首次应使用quick或明确count。`pilot`预设也是90次；`full`预设为30题×3次×3系统=270次，仅供主动选择，绝非必须一次跑完。full不代表approved。

三个系统依次运行，concurrency=2不会同时启动三个系统各两份。根任务内部仍可能有原生模型并行。等待时间还受主机负载、服务限流和冷启动影响，不能仅由公共输入一致声称完全公平计时。

## 5. 原生选择与开放行动

新版case显式设置 `decision_policy: native_generated`、`decisions: []`，没有占位的C1/C2。选项文本、行动顺序、关键事件和结果由原生系统产生。角色身份、世界规则和已发生的前文仍必须遵守，不把“开放”当作任意重置事实。

默认 `--choices 0 1` 表示第一次原生菜单选第一项，第二次及以后选第二项，序列耗尽重复最后一个下标，不是交替。`--choices 0` 则始终选第一项。如果菜单没有指定下标，保留失败，不悄悄换选项。

这只是可记录的位置策略，不把不同系统的第一项解释成相同动作；固定位置也可能有位置偏好，应在实验报告中披露。不生成语义匹配器，不把另一系统的故事或评审结果回灌。IF Line 的原生候选接口仍请求两个候选，但候选的行动内容不预写；接口数量不等于预设剧情。

不会强制所有系统产生同一篇故事，也不会为了差异而要求它们必须不同。原生未选预览与已发生正文分开，不拼接互斥路线。

## 6. 计划、停止、恢复和校验

```bash
# 只编译和冻结计划，不调用模型
bash experiment.sh prepare --allow-pilot --count 4 --out work/experiments/open-round1

# 已有计划开始运行；先预检全部所选系统，再运行；中断后同样恢复未开始项
bash experiment.sh resume --out work/experiments/open-round1

# 只校验，不读取密钥、不发出模型请求
bash experiment.sh verify --out work/experiments/open-round1
```

`prepare` 默认第一题（quick 预设），只编译并冻结实验配置和输入，不检查原生环境，也不创建每系统的队列。要全部题目可加 `--preset pilot`，或显式使用 `--case-ids` / `--genres`。`run` 默认全部题目，`quick` 默认第一题。

需要在付费启动前检查所选环境，可用 `bash experiment.sh preflight --allow-pilot --count 4 --out work/experiments/open-preflight1`。它也会先创建实验，因此 `--out` 必须是新目录；不能在刚 `prepare` 的同一目录上再执行 `preflight`。通过后用 `resume --out work/experiments/open-preflight1` 启动该计划。已有计划的 `resume` 本身会在任何付费调用前预检全部所选系统。预检只检查本地条件和密钥变量存在性，不请求供应商。

Ctrl+C停止继续派发并清理原生进程，已经发出的请求仍需收集终态。resume只运行从未开始的排队项，失败、已启动、送达未知和封存样本不重发。需要新尝试使用新目录，并保留旧失败。不能修改冻结题目、模型、代码后继续旧批次；变更被校验的运行代码后要新建实验，旧批次在原提交环境中恢复。

输入目录被复制并绑定哈希，行动政策与套件版本写入experiment.json。不允许一个实验混用specified和native_generated任务。旧版仍可导出并通过suite-root显式运行，但报告必须标明是指定行动任务，不能与开放版当成同一个实验。

## 7. 保留八维证据

每系统完整证据位于实验目录的 `<system>/runs/<run_id>/`。不得只保留最终正文或PNG。已有记录器从真实调用、原生产物与实际画面采集，不要求模型自报数据。

| 维度 | 核心材料与评判边界 |
|---|---|
| M1 固定事实与要求 | inputs、实际正文、constraints.json；新版prescribed_decisions为空，不检查旧C1/C2 |
| M2 连贯性 | trajectories/main/story.jsonl与choices.jsonl、native来源；只判断各自实际路径 |
| M3 人物视觉一致性 | characters/versions.jsonl、参考图与frame_map；同一故事内一致，跨系统脸不必相同 |
| M4 等待时间 | telemetry/events.jsonl、统一时钟与调度条件；真实桌面时间未测仍未知 |
| M5 阅读体验 | evaluation/reading_blind.json与实际选项；不按预定剧情答案评价，不强求全篇收束 |
| M6 图文匹配 | clean_frames、ui_captures和完整正文映射；图必须对应本系统自己的正文 |
| M7 token | 每次调用、raw usage、失败与重试；覆盖不全只报告已知小计，未知不填0 |
| M8 图片数 | requests、outputs、assets与实际使用；区分候选、资产、复用与合成 |

M1/M2/M3/M5/M6尚未自动评分，生成内部审核不是外部裁判。M4/M7/M8是实测汇总而非质量分。完整字段与脱敏规则见BATCH_RECORDING.md。审阅旧文档涉及的C1/C2时，只能适用于指定行动旧题；新版没有此标准。

正常执行到三系统校验后生成 `comparison.json`，核对输入、条件与证据，不比较正文相似性；早期环境错误或中断可能尚无比较报告。统一 `experiment.sh` 的生成/恢复退出码：`0` 表示各阶段正常、计划中所有尝试达到观察范围且证据校验通过；`1` 表示汇总检查未全部通过（如观察范围未达到、证据未全部通过或比较器返回非零）；`2` 表示配置、预检或阶段执行错误；`130` 表示中断。底层单系统批量程序的 `0` 只表示批次操作完成，可能含失败样本。`sealed`、退出码 `0` 或 `comparison_ready` 均不代表五项内容质量已通过。逐份查看scope_reached、stop_reason、错误、正文与画面覆盖、usage覆盖。没有结局本身不扣分；故事缺少推进、自相矛盾仍可评价。

生成或恢复后自动创建整批离线评审包，`REVIEW_DELIVERY.txt` 标出 ZIP，`review-delivery.json.entry_file` 指向本地总目录。组织者保留整个实验、ZIP 外的 `organizer.json` 和八项指标；评审者只需完整解压 ZIP，双击 `打开故事.html`。播放已记录路径不调用模型，不替代原始证据。失败与零正文样本仍保留，未封存项通过数量及 `partial` 状态披露。详细操作见 [本地查看与评审交付](PLAYBACK_REVIEW.md)。

## 8. 正式题目审批

所有内置输入保持pilot，不在本次发布中编造审阅人或审批。先预览、人工阅读30份完整任务以及CHANGES.md，再由真实操作者执行：

```bash
python3 tools/approve_eval30.py \
  --suite-root work/open-eval30-preview \
  --out work/open-eval30-approved \
  --reviewer '实际审阅人姓名或标识' \
  --confirm-reviewed

# 无allow-pilot，正式输入缺少审核或哈希不符就拒绝
bash experiment.sh run --suite-root work/open-eval30-approved \
  --count 6 --concurrency 1 --out work/experiments/approved-round1
```

审批创建新副本，不覆盖pilot；版本保留open标识，原始文本、源哈希、case哈希和审阅时间关联。人工确认不是生成质量通过。修改正式题目后必须新版本、重新导出审阅，不能手动改approved绕过检查。

## 9. 历史版本与排错

```bash
bash experiment.sh preview-legacy --out work/legacy-eval30
# 保留第一版开放输入，不覆盖其开头与哈希
python3 tools/eval30.py --open-actions-v1 --out work/open-v1
# 明确进行旧版指定行动实验，不能宣称开放行动
bash experiment.sh run --suite-root work/legacy-eval30 --allow-pilot --count 3

bash experiment.sh test
python3 tools/verify_sources.py
```

旧30题原文和旧公共输入哈希保持不变。eval30.py与统一入口现在都默认第二版；显式`--legacy-actions`保留最初指定行动版本，`--open-actions-v1`保留已发布的第一版开放版本。已合入的编译合同仅对显式开放v4任务允许空预设行动，旧v3/旧指定行动合同仍保留。新需求不修改systems、原生补丁、来源锁或原生选择/生成代码。

预检失败看各项目安装文档及launch_logs，不能把环境通过当成真实模型必成功。AI4的原生节点校验和大小写路径风险、InfiPlot的依赖/浏览器/请求容量等旧风险没有被本次题目修改修复。验证范围见OPEN_ACTION_VALIDATION.md，不混淆离线单元测试、原生fixture和付费模型实测。
