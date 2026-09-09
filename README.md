# 三系统故事生成与实验

同一份题目、人物设定和固定开头交给 IF Line、AI4VisualNovel、InfiPlot。**不预设关键行动，不对齐跨系统行动语义；三个系统各自发展剧情，使用相同的评判标准。** 允许故事不同，也不强迫故事不同。

默认题库现在是 **`v4-30-open-actions-pilot.2`**：原来的六类题材、30 道题，取消预先给出的两组行动和与它们绑定的剧情阶段。保留已发生事实、世界规则、人物名单、共同阅读窗口和八维证据。原始文件与旧的指定行动题库没有覆盖，新输入拥有独立版本和哈希。已发布的第一版开放题库也保留，第二版使用不同的 `OPEN02` case ID。

## 第一次使用

在普通 Ubuntu 24.04 用户或 Windows WSL2 Ubuntu 24.04 的 Linux 文件系统中操作；不提供 macOS 或 Windows 原生适配。

```bash
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
bash experiment.sh setup
```

安装后按向导填写文字、图片、原生视觉审核的供应商、模型和密钥，逐项确认请求将发送的地址。密钥不回显，只保存到本机 `work/secrets.env`，不提交 Git。安装、配置和环境检查不调用付费生成模型，但会下载依赖、浏览器与原生模型。

已有可用环境不必重复安装。只改配置：`bash experiment.sh configure`。已有仓库先保存本地改动，再执行 `git pull --ff-only`。

## 一条命令开始试跑

```bash
bash experiment.sh quick --allow-pilot
```

默认第一题、三个系统各尝试一次，共 3 次。终端展示规模后输入 `RUN` 才产生付费调用；自动化可显式加 `--yes`。全部所选系统先预检、建立不可变计划，再开始生成。

**次数是尝试数，不保证同等数量的成功故事。没有适配器总 token、总时间或总费用上限。** 4000 新增可见 Unicode 字符只是阅读窗口，不限制原生规划、审核、预取或未选分支消耗。先检查快速试跑结果再扩大规模。

## 自己决定题目、数量和并发

```bash
# 每个系统尝试 12 次，共 36 次；当前系统内部并发 2
bash experiment.sh run --allow-pilot --count 12 --concurrency 2

# 只跑 IF Line 的科幻题，总共 7 次，并发 2
bash experiment.sh run --allow-pilot --systems if_line --genres SCI-FI --count 7 --concurrency 2

# 两道题，每题每系统重复 2 次，共 12 次尝试
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 MYSTERY-01 --repeat 2 --concurrency 2

# 每类题材取第一题，共 18 次尝试
bash experiment.sh genres --allow-pilot
```

`--count` 是每系统总尝试数，`--repeat` 是每题每系统重复次数，二者互斥。题材还可选择 CAMPUS、MYSTERY、FANTASY、HISTORY、EMOTION。`--systems` 可选一个、两个或全部系统。

三个系统依次运行；`--concurrency` 限制当前活跃系统的根故事任务数，不是 HTTP 并发。数量不必是题目数的整数倍，逐题次数和未运行题目会写入计划。完整 270 次只是可选的 `full` 预设，不是必跑要求。

## 提示词和使用说明

[完整使用说明](docs/OPEN_ACTION_EXPERIMENTS.md) 覆盖安装、试跑、数量、原生选项、恢复、评审数据和正式审批。[30 道题的设定与开头](benchmark/suites/eval30_open/PROMPTS.md)、[统一前缀](benchmark/suites/eval30_open/prefix.txt)、[逐项变更说明](benchmark/suites/eval30_open/CHANGES.md) 已放入仓库。

```bash
bash experiment.sh list
bash experiment.sh preview --out work/open-eval30-preview
```

预览无需密钥或原生依赖。生成 `PROMPTS_30_COMPILED.md` 和逐题 `compiled/<case_id>/shared_task.txt`，内容完整、没有占位符。**实际运行使用编译后的同一公共字符串，不需要分别粘贴到三个前端。** IF Line 接收 `extra_requirements`，AI4VisualNovel 接收需求文件，InfiPlot 接收 `worldSetting`。

旧版仍可用 `bash experiment.sh preview-legacy --out work/legacy-eval30` 导出。`tools/eval30.py`、默认实验和 `preview` 统一导出第二版；原始指定行动版本用 `--legacy-actions`，已发布的第一版开放题库用 `--open-actions-v1`，两种历史输入均保留。旧题中的 C1/C2 只适用于旧版指定行动任务，不是新版评分标准。

## 结果、恢复与校验

```bash
bash experiment.sh run --allow-pilot --count 4 --out work/experiments/open-round1
bash experiment.sh resume --out work/experiments/open-round1
bash experiment.sh verify --out work/experiments/open-round1
```

实验目录保存 `experiment.json`、共同 `config.json`、`inputs/`、三个系统的完整批次、`experiment_summary.json`、`EXPERIMENT_REPORT.md`；选中全部三个系统时还生成 `comparison.json`。恢复只派发从未开始的排队项，不重发失败、已启动或送达未知的尝试。新代码不能覆盖或继续旧的冻结批次，请使用新目录；历史批次应在原提交环境中恢复或复核。

八维为：固定事实与要求、连贯性、人物视觉一致性、等待时间、阅读体验、图文匹配、token、图片数量。M1/M2/M3/M5/M6 只保存证据，尚不自动评分；M4/M7/M8 使用实测与供应商用量，未知不填零。正文、画面、原生选项、已选行动、失败、重试和所有原生消耗均按现有记录器留档。[八维记录对照](docs/BATCH_RECORDING.md)

`sealed`、退出码和 `comparison_ready` 不代表质量通过。各故事以自己的实际路径和画面受评，不把另一个系统的剧情当成答案。不仅保留故事或 PNG，必须保留完整批次及 `runs/`。

新运行结束后会自动打包**所有已封存小说**：在实验/批次输出目录打开 `REVIEW_DELIVERY.txt`，把其中指定的 `review.zip` 发给评审者。接收者解压后双击 **`打开故事.html`**，即可浏览全部小说、逐页播放并返回总目录，无需安装项目、账号或 API 密钥。目录保留失败样本并显示尚未封存的数量，八项原始指标不变。

每篇仍有独立的 `playback/<run_id>/` 回放；旧结果或整个实验也可手动整理成可分发的目录和 ZIP：

```bash
bash experiment.sh playback --input work/experiments/open-round1 --out work/review/open-round1
```

解压完整 ZIP 后打开 `index.html`，无需 API、原生服务或额外安装。回放展示本次实际路径和选择，不继续生成未选分支；原始八维证据保持不变。详见 [故事查看与人工评审](docs/PLAYBACK_REVIEW.md)。

## 版本与验证边界

内置题库仍为 `pilot`，上传、编译成功不代表人工批准。正式运行需人工审阅后使用 `tools/approve_eval30.py` 创建与内容哈希绑定的副本，命令见完整使用说明。`full` 仅表示规模，不表示正式审批。

沿用已合入的外置合同扩展，v4 任务可显式使用 `decision_policy=native_generated` 和真正的空 `decisions=[]`。旧合同保持兼容，旧 30 题公共哈希不变。输入投递合同仍为 3.0，阅读输出合同仍为 4.0，具体开放案例版本为 4.1-open-actions-pilot.2；这些是不同层次的版本。

**没有修改 `systems/`、`baseline-lock.json`、既有 IF Line 补丁或原生创作机制。** IF Line 继续使用已披露的 HTML 修复变体；AI4VisualNovel 和 InfiPlot 保持原冻结来源。详见 [来源](docs/PROVENANCE.md)、[公平性](docs/FAIRNESS.md)、[IF Line 修复](docs/IFLINE_HTML_PATCH.md)。

```bash
bash experiment.sh test
python3 tools/verify_sources.py
```

[本次验证范围](docs/OPEN_ACTION_VALIDATION.md) 将编译、离线测试与真实原生生成严格区分。依赖安装细节和原生已知问题仍见 [QUICKSTART](docs/QUICKSTART.md)、[WSL2](docs/WINDOWS_WSL2.md)、[IF Line](docs/projects/IF_LINE.md)、[AI4VisualNovel](docs/projects/AI4VISUALNOVEL.md)、[InfiPlot](docs/projects/INFIPLOT.md)。这些历史文档中的旧题示例不覆盖新版的开放行动合同。
