# 单系统批量入口：进阶参考

**首次交接和日常生成使用 [操作者交接手册](OPERATOR_HANDOFF.md) 中的 `bash experiment.sh`。** 统一入口负责当前 30 题选择、规模确认、冻结计划、顺序运行所选系统和整理整批离线评审包。

本文供已经准备自定义共同 bundle 或需要单系统诊断的维护者使用。`tools/run_batch.py` 是单系统包装器，会选对 Python 环境并安全读取 `work/secrets.env`，但使用配置中已有的 `bundles`，不选择默认 30 题，也没有 `RUN` 确认。更下层的三个入口还要求自行提供正确解释器和环境变量，不自动读取密钥文件。不要把这里的底层命令与统一入口的实验目录混用。

三个入口共用编译器、模型配置、阅读窗口和证据格式，各自运行对应项目的原生方法：

| 程序 | 项目 |
|---|---|
| `benchmark/run_if_line_batch.py` | IF Line |
| `benchmark/run_ai4visualnovel_batch.py` | AI4VisualNovel |
| `benchmark/run_infiplot_batch.py` | InfiPlot |

每个根运行独立生成一条实际访问路径。原生生成的整张故事图、未选择路线、预取和审核都保存；当前路径正文只包含实际访问的内容。程序不执行质量评分，不向生成器回灌评审信息。

## 共同范围

v4 采用连续阅读窗口。日常统一入口使用 `v4-30-open-actions-pilot.2` 的 30 题；本页编译演示使用较早的指定行动单题 `CAMPUS-01-V4`，用于接入诊断，不与开放行动题库混跑。该示例为 **4000 个新增可见 Unicode 字符的开发候选**，从共同开头之后开始累计；不计公共开头、内部规划、标签、菜单标题或未选候选预览。到达阈值后保留到该原生文本单元中第一个句界；若该单元没有后续句界，保留整个单元。该规则可能使样本略长于目标值，实际长度同时记录。

共同任务在编译时写入同一个窗口数值。程序从已验证的 bundle 读取窗口，不能单独给某个项目传另一个长度。试验前可复制 `cases/CAMPUS-01-V4.json`，修改 `case_id`、`case_version` 和 `output_contract.window_chars` 后重新编译，再让三个程序共用新 bundle。案例源文件放在 `benchmark/cases/`；不要修改已冻结批次中的文件。

到达窗口记 `scope_reached=true`。原生自行结束另记 `native_ended`；错误和中断另记 `stop_reason`。InfiPlot 没有全篇结束状态，达到窗口即可完成本次观察。五份评审包均声明：只评当前连续片段，全篇结局不加分、不扣分，事实冲突、重复或没有推进仍可评价。未生成足够正文、缺图和失败样本保留，不将未知写成通过。

仅旧指定行动题目中的 C1/C2 及后果需要对应内容核查，两个任意原生菜单不自动视为完成 C1/C2。当前开放行动题库没有此要求。程序按固定选项序号执行并保存原生 ID、文本、原始 effect 和状态；不会把不同项目的选项强行改写为同一种行动。

## 环境准备

公共入口使用 Python 3.10+，建议用具有 Pillow 的 Python 3.12 环境，以便计算像素哈希。三个项目分别安装其冻结版本的依赖，不合并原生 Python 环境。所有依赖目录、数据库、运行文件均放在 `systems/` 之外。

- IF Line：原生 Python 后端依赖、PostgreSQL、Redis、Node、原生 Vue 播放器依赖及 rembg 模型。额外依赖清单为 `benchmark/native_shims/if_line/requirements-media.txt` 和 `renderer-package.json`；详见该目录 README。
- AI4VisualNovel：原生 Python 依赖、Pygame、Pillow、rembg，以及原生使用的分割模型缓存。详见 `benchmark/native_shims/ai4vn/BATCH.md`。
- InfiPlot：冻结 Node/pnpm 依赖缓存、Playwright 及对应 Chromium；详见 `benchmark/native_shims/infiplot/BATCH.md`。每个运行使用独立 Next 目录、端口、身份服务和浏览器上下文。

Playwright 的包版本与浏览器版本必须匹配。建议用独立的 `PLAYWRIGHT_BROWSERS_PATH`，不要让多个项目的安装器互相清理全局浏览器缓存。依赖安装/启动检查的等待上限属于环境准备；生成阶段没有适配器总时长或调用数量预算。

InfiPlot 外置启动器默认配置 `systems.infiplot.native_request_body_limit_bytes=67108864`（64 MiB），防止原生内联图片历史触发默认 10 MiB 请求缓冲截断。它只调整框架运行容量，原文件、公共输入和图片字节不变；每次保存 `native/request-body-config.json` 并验证实际生效。设为 `null` 恢复原生默认。该配置需作为实验运行条件记录；它不是无限续写保证。实现边界和容量验证见 [InfiPlot 批量说明](../benchmark/native_shims/infiplot/BATCH.md)。

优先由安装流程中的 `tools/configure_batch.py` 生成本机配置。只有手动集成时才复制 `benchmark/configs/batch.example.json` 为本地配置；模板不含全部接收机器路径。保留三个系统条目，将本机 Python、Node、模块和模型缓存路径填入对应系统；相对路径均相对于配置文件所在目录解析。`pg_bin` 可指定 PostgreSQL 可执行文件目录。IF Line 自行创建独立数据库，不使用日常项目的数据。

当前 IF Line 另有用户授权的 [HTML 正文转换源码补丁](IFLINE_HTML_PATCH.md)，模板通过 `systems.if_line.source_patch` 显式指定。`source_lock` 仍指向原始基线锁；不要用修复后的文件覆盖原始锁。移动本地配置时，也要更新 `source_patch` 的相对路径或使用绝对路径。运行记录会保存补丁与来源信息。AI4VisualNovel 和 InfiPlot 不允许设置源码补丁。历史没有此补丁的批次与当前变体应分别标注。

模型只在 `providers` 配置一次：文字示例为 `deepseek-v4-flash`，图片为 `gpt-image-2`，原生视觉审核为 `gpt-5.4-mini`。供应商地址必须以 `/v1` 结束。三个程序读取同一份配置，禁止按系统覆盖共同模型、窗口或预算。密钥只通过 `BENCH_TEXT_API_KEY`、`BENCH_IMAGE_API_KEY`、`BENCH_VISION_API_KEY` 等命名环境变量传入，不写进 JSON、故事文件或 Git。

图像请求的大小、数量、参考图和原生审核方式保持各自实现，实际参数逐次保存。文字模型的原生温度和输出结构也保留；共同 `thinking` 等模型控制在配置中冻结。对比的是共同外部任务下的原生方法，并不要求全部模型 messages 相同。

## 编译与预检

以下命令在仓库 `benchmark/` 目录执行，`python3` 应指向上述公共 Python 环境。

```bash
python3 -m story_benchmark compile --case cases/CAMPUS-01-V4.json --out ../work/shared-v4 --allow-pilot
python3 -m story_benchmark verify --bundle ../work/shared-v4
```

在共同配置的 `bundles` 中填入编译目录。列表中可以有多道已经各自编译并验证的题目；各项目严格按相同顺序循环取题。`--count` 是根运行总数，不是“每题次数”。例如两题、`--count 6`，顺序为题一/题二各重复三次。

```bash
python3 run_if_line_batch.py --config ../work/config/batch.local.json --preflight
python3 run_ai4visualnovel_batch.py --config ../work/config/batch.local.json --preflight
python3 run_infiplot_batch.py --config ../work/config/batch.local.json --preflight
```

预检不调用付费模型。它检查输入、源码、环境与密钥变量是否存在；不能证明供应商余额、模型可用性或原生生成一定成功。模板 `allow_pilot=true` 明确允许开发案例；它不是正式题目批准记录。

## 批量执行

```bash
python3 run_if_line_batch.py --config ../work/config/batch.local.json --count 6 --concurrency 2 --out ../results/round-01-ifline
python3 run_ai4visualnovel_batch.py --config ../work/config/batch.local.json --count 6 --concurrency 2 --out ../results/round-01-ai4vn
python3 run_infiplot_batch.py --config ../work/config/batch.local.json --count 6 --concurrency 2 --out ../results/round-01-infiplot
```

这些底层命令可由维护者在三个终端同时启动；这是另外一种调度条件。日常 `experiment.sh` 按所选系统顺序运行，不采用这种三系统同时调度。不要将两种方式混为同一计时实验。每个程序内部最多同时运行 `--concurrency` 个独立进程；上例三个程序同时运行时最多有六个根运行活跃。模型供应商的速率限制仍可能触发原生重试，全部真实尝试照实记录。适配器不设置总调用量、总 token、总时长或费用上限，也不为获得好结果自动重跑失败样本。

`choice_indices` 是从 0 开始的序号列表，如 `[0, 1]` 表示第一次选第一项、以后选第二项；用完列表后重复最后一个序号。某个菜单没有对应序号时明确失败，不悄悄换选项。`reading_delay_seconds` 是统一的模拟阅读延迟，发生在选择之前，不计入选择后的系统响应时间。

首次启动会保存不可变 `plan.json`、公共输入副本、根运行列表和适配器代码清单。`--plan-only` 只完成预检和排队。任务目录已经存在时不会覆盖。

每次启动或恢复另存 `scheduling/` 会话记录，包括声明并发、进程容量、主机平台、实际启动任务及起止时间；根运行封存启动时的调度上下文。恢复时可以调整并发，但不会改写上一轮记录。比较器将生成条件和调度条件分开报告；等待时间还会受其他主机负载和供应商限流影响，不能仅凭公共提示词一致就宣称响应速度条件完全相同。

## 中断、恢复和验收

Ctrl+C 停止继续派发，并通知运行中的根进程清理原生服务。已发到供应商的请求会继续收集终态和用量，因而退出可能需要等待；第二次中断不用于隐藏已发生的调用。如果进程被强制杀死，现场按可能已送达处理。

```bash
python3 run_if_line_batch.py --out ../results/round-01-ifline --resume --concurrency 2
python3 run_if_line_batch.py --out ../results/round-01-ifline --verify
```

`--resume` 仅启动完全没有开始的排队任务；已运行、失败、已封存或送达未知的任务都不再发送。适配器代码变化后拒绝继续旧批次。需要重新尝试时使用新的输出目录，并保留旧尝试。`--verify` 不调用模型，会核对文件集合、哈希、正文原生锚点、图像引用和重算指标。

若强制杀进程遗留 `scheduler.lock`，先检查文件中 PID 是否仍在运行及原生子进程是否退出，再由操作人员移除锁；程序不会凭过期时间自动抢占可能仍在付费的运行。

三个批次完成后执行：

```bash
python3 -m story_benchmark.batch_compare ../results/round-01-ifline ../results/round-01-ai4vn ../results/round-01-infiplot --out ../results/round-01-comparison.json
```

比较器核对题目顺序/次数、公共模型和政策、实际接收文本、首个真实创作请求、适配器版本及源码/正文来源完整性。`comparison_ready` 表示工程证据可以进入下一步检查，不表示故事质量已获通过；同时报告每个原生失败、实际字数范围和是否结束。

## 输出与八项指标

每个批次有 `results.json`、便于浏览的 `results.csv`、`launch_logs/` 和 `runs/`。每个根运行按用户提供的记录规范保存，字段对应见 [证据采集对照](BATCH_RECORDING.md)。

批量入口退出码为 0 表示本次批次操作正常完成，不表示每份故事都成功；`state=sealed` 只表示证据已封存。逐份检查 `scope_reached`、`stop_reason`、正文/画面覆盖与错误记录。原生失败也能正常封存并进入批次汇总。

`metrics.json` 中 M1/M2/M3/M5/M6 分数为 `null`，附带证据可用性。M4 为事件实测时间，M7 为真实用量及覆盖，M8 区分请求、候选、资产和画面。原始内部规划不能代替玩家正文，图片 prompt 不能代替图文匹配标准。

每根封存后自动导出 `playback/<run_id>/review/index.html`；批次生成/恢复后自动整理总目录和 ZIP，路径见 `review-delivery.json` 与 `REVIEW_DELIVERY.txt`。本地直接用浏览器打开总目录，评审者接收完整 ZIP 后解压阅读。旧记录补导出和具体目录见 [离线回放说明](PLAYBACK_REVIEW.md)。播放器仅展示已记录路径，不改写封存指标。

`evaluation/reading_blind.json` 用于匿名文字阅读评价；其余包分别用于事实、连贯、人物视觉和图文匹配。视觉包提供相对根运行目录的稳定图片路径。交给裁判优先使用自动导出的评审 ZIP；按维度自行分发时只提供对应包及它引用的素材，不提供整个私有运行目录。组织者保留 ZIP 外的 `organizer.json`（含原始八项指标）与完整原始证据。后续裁判结果需另存模型版本、提示词哈希、输入包哈希、抽样/展示顺序、证据 ID、简短理由及外部评测用量。

当前截图是原生离屏播放器或明确标记的等价确定性合成。真实桌面用户显示时间未测时始终留空。原生资产缺失、重生后仍不合格、预取后未访问及原生异常都会保留；不使用额外 AI 补图或改故事。
