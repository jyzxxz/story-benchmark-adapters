# 故事查看、离线回放与人工评审

当前验证范围、结果与可复现检查见 [回放验证记录](PLAYBACK_VALIDATION.md)。

## 整批小说发给评审者：只发一个 ZIP

更新后，通过 `bash experiment.sh quick/run/resume ...` 完成实际生成时，会自动把本次实验三个项目**所有已封存的小说**整理成一个评审包。单独运行某个项目的批量程序，也会自动整理该项目的全部已封存结果。

1. 在实验或批次输出目录打开 `REVIEW_DELIVERY.txt`，里面写明要发送的 `review.zip` 完整路径；终端也会打印 `Review ZIP to send:`。
2. 将这个 ZIP 发给评审者。组织者的 `organizer.json` 和原始 `runs/` 自己保留，盲评时不一起发。
3. 评审者**解压整个 ZIP，双击“打开故事.html”**（或 `index.html`），即可看到全部小说目录。点击任一小说可逐页播放，点击“返回全部故事”切换下一部。

接收者不需要安装仓库、Python、Node、Docker，不需要命令行、账号或 API 密钥，也不需要网络。不要在压缩包预览窗口只点开一个 HTML；要先完整解压，保留随包图片和脚本的位置。

目录默认列出全部样本，支持按样本编号搜索和按有无正文筛选。生成失败而没有正文的记录仍然保留；筛选只改变屏幕显示，不删除证据。目录会显示计划数、已封存数和未封存数。任务尚未完成时，交付状态为 `partial`，不会用现有小说数量冒充全部完成。没有可核对的计划时，明确显示计划数未知。

整体交付的位置为：

```text
实验或批次输出目录/
├── REVIEW_DELIVERY.txt             # 给组织者：该发送哪个 ZIP
├── review-delivery.json            # ready / partial / failed / no_sealed_runs
├── review-deliveries/<snapshot>/
│   ├── review.zip                  # 发给评审者的全部小说包
│   ├── organizer.json              # 组织者保留：来源对应和原始八项指标
│   └── review/
│       ├── 打开故事.html            # 解压后直接双击
│       ├── index.html              # 同一个总目录
│       ├── samples.json            # AI 可读的全部样本索引
│       ├── coverage.json           # 计划与实际收录数量
│       └── samples/<sample_id>/    # 各小说播放器、图文与评审 JSON
└── runs/ 或三个系统子目录           # 原始证据保持原样
```

同一份封存结果重复运行整理时，校验原始证据及评审包后复用已有快照；有新增结果时生成新的快照，旧包不被覆盖。`--verify`、预检及只准备输入不会自动打包。它们不调用生成模型，也不会为缺失的故事自动补跑。

以前生成的整批结果可使用下方 `playback --input ... --out ...` 命令补打包，同样获得总目录、`打开故事.html` 和 ZIP。单篇导出则直接进入该篇播放器。

生成结束后，可以在浏览器中逐页阅读已经生成的图文故事。回放包包含本次实际阅读路径的正文、图片和选择记录；接收者不必安装三个原生项目，不需要启动服务或配置 API 密钥。

批量生成仍使用各项目的原生流程。离线回放只读取已封存的证据，不运行原生引擎，不调用模型，不生成新剧情，也不改变实验记录中的八项指标。

## 新故事在哪里打开

更新后新运行的每个根故事在证据封存、校验通过后，自动生成回放包。假设实验输出目录是 `work/experiments/open-round1`，相应位置为：

```text
work/experiments/open-round1/
├── if_line/
│   ├── runs/<run_id>/                 # 原始封存证据
│   ├── playback/<run_id>/             # 派生回放包
│   └── playback-status/<run_id>.json  # 导出成功或失败状态
├── ai4visualnovel/                    # 同样的目录规则
└── infiplot/                         # 同样的目录规则
```

单独通过 `tools/run_batch.py` 或 `benchmark/run_*_batch.py` 生成时，`playback/` 和 `playback-status/` 位于所指定的批次目录，与 `runs/` 同级。

找到 `playback/<run_id>/review/index.html`，用浏览器打开。它使用随包附带的本地文件，可以断网阅读。不要只复制 `index.html`：图片等文件必须保持相对位置。回放中的选择显示本次实际选择结果；未生成的路线不会被伪造成可执行分支。

Linux 服务器可以把回放 ZIP 下载到有桌面的电脑。Windows 使用者解压整个 ZIP 后双击 `index.html`，不需要为阅读安装 WSL、Python、Node 或 PostgreSQL。WSL/Linux 环境要求仍适用于**故事生成**。

## 把整个实验整理成一个可分发的目录

在仓库根目录执行：

```bash
bash experiment.sh playback \
  --input work/experiments/open-round1 \
  --out work/review/open-round1
```

这条命令为已经封存的故事生成统一目录和 ZIP。打开 `work/review/open-round1/review/index.html`，或将 `work/review/open-round1/review.zip` 发给阅读者。命令返回的报告也给出 `entry_file` 和 `zip_file`。原始运行目录不被修改；输出目录必须是新目录，不覆盖历史回放包。

也可以只导出一个系统批次：

```bash
bash experiment.sh playback \
  --input work/experiments/open-round1/infiplot \
  --out work/review/infiplot-round1
```

或只导出某次根运行：

```bash
bash experiment.sh playback \
  --input work/experiments/open-round1/infiplot/runs/RUN_ID \
  --out work/review/one-story
```

将 `RUN_ID` 换成 `runs/` 中实际的目录名。直接使用 Python 的等价入口为：

```bash
python3 tools/export_playback.py --input /path/to/sealed-run --out /path/to/new-review
```

导出只需要 Python 标准库；不需要加载 `work/secrets.env` 或运行安装向导。加 `--no-zip` 可只生成文件夹。`--input` 可是根运行、单系统批次或整个实验目录；它必须包含已经封存的运行，不能拿尚在写入的半成品冒充完成故事。

## 以前生成的故事也能导出

只要仍保留完整的 `runs/<run_id>/` 证据目录，就可以用上面的命令转换，不需要重新生成或再次付费。工具根据该次封存清单验证证据文件与哈希，不要求历史运行使用现在的适配器版本。

源文件缺失、哈希不符或格式不支持时，导出会报告失败。不要修改原始清单或编造缺失正文来绕过校验；先找回原始完整证据。仅有几张 PNG、一个摘要或不完整的日志，不等于保存了完整故事路径。

## 回放和自由游玩的区别

| 操作 | 当前离线回放 |
|---|---|
| 从开头查看已经记录的图文 | 支持 |
| 阅读下一页、回看前面的内容 | 支持 |
| 查看系统提供的选项和这次实际选择 | 支持 |
| 改选未生成的路线、输入新行动 | 不执行；保留为未选内容 |
| 超出已保存范围继续生成 | 不支持，回放不会调用 API |
| 播放音频或配音 | 本实验为文本加图片，未生成的音频不会补造 |

固定开头是三个项目共同收到的输入，需要与新增正文分开阅读和评价，不能把公共开头的质量记作某个系统独有的生成贡献。

当前公共窗口默认约 4000 个新增可见 Unicode 字符，并在句界结束。读到“观察范围结束”不代表原生故事存在结局。InfiPlot 没有固定全篇结束点，另外两个项目可能提前结束或报错；回放保留相应状态，不补写结局，不把截断包装为完整通关，也不把失败样本从评审中静默移除。

若要自由选择新路线，需要另行运行原生交互服务并接入对应的存档、图片和身份配置。未生成的新场景可能继续调用模型。这与本次离线人工评审是不同的使用方式，不能通过开启 `keep_runtime` 就自动得到可玩的在线网站。

## 人工评审和 AI 评审分别保留什么

单故事导出结构为：

```text
导出目录/
├── organizer.json       # 组织者映射、原始 M1–M8 和来源信息；不在 ZIP 中
├── report.json          # 导出报告；不在 ZIP 中
├── review.zip           # 只打包下方 review/ 的内容
└── review/
    ├── index.html       # 双击打开
    ├── story.json       # 结构化实际路径
    ├── transcript.md    # 可读正文转录
    ├── evaluation/      # 五类内容评审材料
    └── images/          # 本地图片
```

批次或整个实验导出的 `review/index.html` 是匿名样本目录；每个样本位于 `review/samples/<sample_id>/`，有自己的 `index.html`、JSON、转录和图片。阅读、连贯性、要求满足、图文匹配和人物视觉材料分别位于 `evaluation/reading.json`、`coherence.json`、`requirements.json`、`image_text.json`、`character_visual.json`。

阅读者使用离线 HTML 查看实际图文路径。导出的结构化 JSON 和文本转录便于 AI 读取正文、画面关系和已执行选择。它们都是原始证据的派生表示，不重新写故事，不重新计算八项指标，也不把隐藏的原生规划、未选分支或 API prompt 填进可见正文。

用于分发的 ZIP 不包含原始 API 请求、供应商响应日志、账号配置或运行时目录。组织者的样本映射 `organizer.json` 保持在 ZIP 外；如果需要匿名评审，只分发回放 ZIP，不要把组织者映射、原生日志和实验配置一起交给评审者。匿名包装也不保证正文自身完全没有系统线索。

完整审计仍应保存原始 `runs/`、批次计划及结果。特别是等待时间、token、图片数量和失败原因，需以原始 `metrics.json`、`telemetry/`、`images/`、`manifest.json` 为证据；不从回放翻页速度推算原始等待时间，不把重复显示的同一张图片算成多次生成。

八项评测字段和适用范围见 [BATCH_RECORDING.md](BATCH_RECORDING.md)。回放本身不自动产生内容评分，也不代表质量验收通过。

## 导出失败时怎么处理

自动导出的结果写在 `<批次>/playback-status/<run_id>.json`，同时记录到已有 `launch_logs/<run_id>.log`。`status=ready` 表示回放导出完成；`status=failed` 表示派生导出失败。文件里会记录导出阶段、异常类型和原因。若目标已存在且有阅读入口，记录 `status=already_exists` 并保留现有目录；这不表示重新验证过旧回放，需要时应手动导出到新目录。

导出失败不会改写封存故事的 `stop_reason`、`metrics.json` 或原始 `errors.jsonl`，不会把一次已经执行的生成任务重新排队，也不会自动消耗 API 补跑。可以修复磁盘空间、目录权限或缺失文件后，手动导出到新的目录：

```bash
bash experiment.sh playback \
  --input work/experiments/open-round1/if_line/runs/RUN_ID \
  --out work/review/recovered-story
```

原生生成失败而没有正文的样本，仍应保留为失败/空内容样本；不要用播放器是否能打开来替代故事生成成功判定。
