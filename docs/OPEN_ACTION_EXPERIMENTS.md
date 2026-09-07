# 不预设关键行动：从安装到批量实验

默认题库 `v4-30-open-actions-pilot.2`，六类题材、30题。共同的是外部题目、初始事实、人物名单、阅读范围及评判标准，不是未来行动。系统自创原生选项，适配器只选择、记录和继续。没有跨系统语义匹配，没有外部 AI 自动评分，没有为凑成功数而自动重生成。

## 1. 安装并配置一次

在 Ubuntu 24.04 或 Windows WSL2 Ubuntu 24.04 的普通用户终端中执行；不要使用 root 生成故事，IF Line 的临时 PostgreSQL 不接受这种方式。不提供 macOS 适配。WSL2 请将仓库放在 Linux 文件系统，而不是 Windows 挂载目录。

```bash
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
bash experiment.sh setup
```

该命令沿用原安装器，准备独立 Python 环境、Node、浏览器、模型缓存和公共配置，再运行供应商配置向导及 doctor。安装需要网络和适当的系统权限，但不发起付费故事生成。具体依赖、已知平台限制见 QUICKSTART.md 和各项目文档；安装通过不代表原生故事生成必定成功。

向导显示文字、图片和原生视觉审核的供应商 URL、模型名，分别输入 `USE` 确认地址。密钥输入不回显，以权限600保存在 `work/secrets.env`。换地址需要输入对该地址授权的密钥，不沿用旧密钥。不要上传、打印或作为 shell 脚本 source 这个文件。

公共配置：`work/config/batch.local.json`。文字、图像、视觉三种角色各自共用一份供应商配置，不能对某个系统偷偷换模型。当前图像协议限定 `gpt-image-2`；文字/视觉模型名称、文字调用及图像生成/参考图编辑能力必须由使用者与自己的供应商核对。模板模型名不保证权限和余额。这里的视觉模型用于原生审核，不是八维外部裁判。

已有环境仅需修改供应商或密钥：

```bash
bash experiment.sh configure
```

显式 export 的同名环境变量优先于本地密钥文件；换密钥时检查旧变量，防止覆盖。保留原生并行、审核和重试策略，不用统一任务去替换原生全部 messages。

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

# 准备好的计划开始运行；以后中断也用该命令恢复未开始项
bash experiment.sh resume --out work/experiments/open-round1

# 只校验，不读取密钥、不发出模型请求
bash experiment.sh verify --out work/experiments/open-round1
```

注意prepare默认quick的题目集合；需全部题目时加`--preset pilot`，或用case-ids/genres选择集合。run默认全部题目，quick默认第一题。这些模式沿用既有规模定义，终端会显示实际选择。

Ctrl+C停止继续派发并清理原生进程，已经发出的请求仍需收集终态。resume只运行从未开始的排队项，失败、已启动、送达未知和封存样本不重发。需要新尝试使用新目录，并保留旧失败。不能修改冻结题目、模型、代码后继续旧批次；本次更新后要新建实验，旧批次在原提交环境中恢复。

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

三系统全部选中时生成comparison.json，核对输入、条件与证据，不比较正文相似性。sealed、退出码0或comparison_ready不意味着故事成功或质量通过。逐份查看scope_reached、stop_reason、错误、正文与画面覆盖、usage覆盖。没有结局本身不扣分；故事缺少推进、自相矛盾仍可评价。

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
