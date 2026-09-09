# IF Line：Linux / Windows WSL2 交接说明

**本页是项目依赖、原生行为和排错参考。首次接手及日常实验请使用 [操作者交接手册](../OPERATOR_HANDOFF.md) 的 `bash experiment.sh`，三个项目使用同一套选题、记录和离线回放流程。**

本页面向 **Ubuntu 24.04 服务器，以及 Windows 上的 WSL2 Ubuntu 24.04**。Windows 接收者应在 WSL 的 Linux 终端内安装并执行；不承诺原生 PowerShell、Windows Python 或 Windows PostgreSQL 能运行这一批量驱动。

日常入口为 `bash experiment.sh ... --systems if_line`；底层驱动为 `benchmark/run_if_line_batch.py`。它调用原生后端、原生图像生成及原生 Vue 播放器，交付一条实际选择路径的图文证据包。它不会部署完整的公共网站，也不提供玩家账号或在线分享服务。

已有干净 Ubuntu 24.04 容器安装及原生图文 fixture 验证，见 [交接检查](../HANDOFF_VALIDATION_20260907.md)。更早的生成及双任务证据见 [批量复查](../ADAPTER_AUDIT_20260907.md) 与 [并行复查](../PARALLEL_RECHECK_20260907.md)，平台条件分别保留。它们不代替接收者服务器、实体 WSL2、供应商与新代码版本的实际验收。

交接安装器使用 `tools/linux/` 中的 Ubuntu 24.04 / Python 3.12 constraints 固定本次依赖版本；外置播放器采用 npm 锁，原生源码依赖清单不改。实际安装记录和新 Linux 验证范围见 [交接检查](../HANDOFF_VALIDATION_20260907.md)。下文手动补装命令用于排错，正式复现优先使用统一安装器及同一套锁。

## 1. 先准备接收环境

Windows 可先在管理员 PowerShell 中安装 WSL，按提示重启并创建 Ubuntu 普通用户：

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
```

确认对应发行版的 VERSION 为 2，再打开 Ubuntu 终端。仓库、依赖和数据库放在 Linux 文件系统中，例如 Linux 用户的项目目录；不要混用 Windows 的 `python.exe` / `node.exe`，也不要把数据库目录放在 `/mnt/c/`。参见 [WSL 安装](https://learn.microsoft.com/en-us/windows/wsl/install) 和 [跨文件系统说明](https://learn.microsoft.com/en-us/windows/wsl/filesystems)。

以下操作从克隆仓库根目录开始。安装系统包可以使用 sudo；**生成程序必须由普通用户运行**。PostgreSQL 的 `initdb` 和数据库服务器会拒绝 root，容器也应给普通用户可写的工作目录。[PostgreSQL 16 说明](https://www.postgresql.org/docs/16/app-initdb.html)

三系统首次交接按手册执行 `bash experiment.sh setup`。以下为管理员只准备 IF Line 环境的进阶方式：

```bash
bash tools/bootstrap_linux.sh --system if_line
source work/activate.sh
python3 tools/configure_batch.py
bash experiment.sh configure
python3 tools/doctor.py --system if_line
python3 tools/smoke_test.py --system if_line --out work/smoke-ifline
```

安装阶段需要下载依赖和模型文件；doctor 与 smoke 不调用真实生成 API。`work/smoke-ifline` 必须是新的目录，不能复用旧证据。系统包已经由管理员安装时，bootstrap 可加 `--skip-system-deps`。配置程序默认写入 `work/config/batch.local.json`；正式模型凭证放在其配套的私有 `work/secrets.env`。

下面的变量供本页手动检查命令使用，与统一安装目录一致：

```bash
export BENCH_REPO="$PWD"
export IF_PY="$BENCH_REPO/work/envs/if_line/bin/python"
export IF_NODE="$BENCH_REPO/work/node/bin/node"
export IF_MEDIA="$BENCH_REPO/work/media-tools"
export U2NET_HOME="$BENCH_REPO/work/models/if_line"
export PLAYWRIGHT_BROWSERS_PATH="$BENCH_REPO/work/browser-cache"
export PATH="$BENCH_REPO/work/node/bin:$PATH"
```

新终端需要恢复这些路径和浏览器缓存变量。若统一安装脚本选择了其他浏览器缓存路径，安装和运行时必须使用同一值。所有环境、模型缓存和产物均放在 `work/`，不要在 `systems/if_line` 里创建环境、安装 npm 包或运行开发服务器。

入口源码见 [bootstrap_linux.sh](../../tools/bootstrap_linux.sh) 和 [configure_batch.py](../../tools/configure_batch.py)。下面列出 IF Line 的实际依赖和手动补装步骤，便于服务器管理员排查安装失败；不要同时混用两套不同的目录布局。

| 依赖 | 配置或位置 | 用途 / 边界 |
|---|---|---|
| Python 3.12 独立环境 | `work/envs/if_line/bin/python` | 安装外置 `requirements-media.txt`，其中包含文字后端依赖。不要混装另两个项目的 Python 包。 |
| PostgreSQL 16 | `pg_bin=/usr/lib/postgresql/16/bin` | 必须包含 `initdb`、`pg_ctl`、`createdb`；每根运行自动建立自己的临时集群。 |
| Redis | `redis_executable=/usr/bin/redis-server` | 每根运行自己的 loopback Redis，不需手工建立服务或数据库。 |
| Node.js 22 | `work/node/bin/node` | 公共安装器准备；Node 版本和操作系统须在一轮实验中冻结。 |
| Vue / 编译器 / esbuild / Playwright | `work/media-tools/node_modules` | `renderer-package.json` 指定 Vue 与 compiler-sfc 3.4.21、esbuild 0.25.10、Playwright 1.55.1。 |
| 对应版本的 Chromium | `PLAYWRIGHT_BROWSERS_PATH` | 使用外置 Playwright 1.55.1 下载的浏览器，不是系统 Chrome。 |
| rembg CPU / ONNX Runtime | IF Python 环境 | `rembg[cpu]==2.0.72`，ONNX Runtime 由该依赖提供；当前方案不要求 CUDA。 |
| `u2net.onnx` | `work/models/if_line/u2net.onnx` | 原生立绘透明背景处理使用的模型，须在生成之前准备。 |
| 字体与系统图形库 | Ubuntu 系统包 | 建议统一 `fonts-noto-cjk`、`fontconfig`；系统库由匹配版本的 Playwright 安装器安装。 |

图文批量模式关闭音频。系统 `ffmpeg` 是原生语音克隆等功能使用的工具，**不是本模式的必需项**；统一安装器若为其他项目安装它，不影响 IF Line。`rembg` 在本模式中处理单张图片，无需 `rembg` 视频 CLI。

每个并行根运行都会同时占用 PostgreSQL、Redis、API、Celery 工作进程、beat 和 Chromium，并加载图像处理依赖。先用 `--concurrency 1` 在接收机器测量内存、磁盘及速度，再增加并发；这里没有经过验证的最低内存承诺。数据库、原始图片、候选图片和截图都会保留，磁盘用量随根数、生成量增长。

## 2. 手动补装依赖

以下仅作为统一安装失败后的分项操作。系统包需管理员安装，后续命令以仓库所有者执行。安装 PostgreSQL 包可能同时创建系统默认集群，批量程序不会连接它，也无需停止已有业务数据库。

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv postgresql-16 \
  postgresql-client-16 redis-server fontconfig fonts-noto-cjk \
  ca-certificates curl xz-utils

python3.12 -m venv "$BENCH_REPO/work/envs/if_line"
"$IF_PY" -m pip install --upgrade pip
"$IF_PY" -m pip install -r "$BENCH_REPO/benchmark/native_shims/if_line/requirements-media.txt" \
  -c "$BENCH_REPO/tools/linux/if_line-py312.constraints.txt"
"$IF_PY" -m pip check

mkdir -p "$IF_MEDIA"
cp "$BENCH_REPO/benchmark/native_shims/if_line/renderer-package.json" "$IF_MEDIA/package.json"
cp "$BENCH_REPO/tools/linux/renderer-package-lock.json" "$IF_MEDIA/package-lock.json"
npm ci --prefix "$IF_MEDIA"
"$IF_NODE" "$IF_MEDIA/node_modules/playwright/cli.js" install-deps chromium
"$IF_NODE" "$IF_MEDIA/node_modules/playwright/cli.js" install chromium
fc-match ':lang=zh-cn'
```

执行 npm 前先完成统一安装器的 Node 步骤，并确认 `command -v node`、`command -v npm` 都来自 `work/node/bin`。`install-deps` 会安装系统库，可能要求 sudo；浏览器下载应以最终运行用户执行。使用这个明确的本地 CLI，避免 `npx` 在缺包时临时下载另一个版本。浏览器下载和运行的缓存配置见 [Playwright 官方说明](https://playwright.dev/docs/browsers)。

离屏播放器使用 `chromium.launch({headless:true})`，不需要桌面会话、DISPLAY、WSLg 或 `xvfb-run`。Xvfb 不能解决浏览器包缺失或系统 `.so` 缺失。中文字体由操作系统提供，仓库没有打包统一字体；正式对比时应统一字体安装、浏览器版本和截图环境，记录 `fc-match` 的结果。

预先下载并加载抠图模型，过程不调用生成模型 API：

```bash
mkdir -p "$U2NET_HOME"
"$IF_PY" - <<'PY'
from rembg import new_session
session = new_session("u2net", providers=["CPUExecutionProvider"])
print("u2net CPU session ready:", type(session).__name__)
PY
sha256sum "$U2NET_HOME/u2net.onnx"
```

冻结的 rembg 2.0.72 从 [rembg 官方模型发行文件](https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx) 下载；其下载器校验 MD5 `60024c5c889badc19c04ad937298a77b`。不要禁用模型校验。无法访问 GitHub 时，在能联网的**同架构目标环境**准备依赖，在可信机器下载同一文件并保留 SHA-256 后传入上述目录。只有 ONNX 模型文件本身可独立跨平台搬运，Python 环境、Node 模块及 Chromium 不能直接从另一操作系统复制。

`requirements-media.txt` 自身不是完整依赖锁；统一安装器及上述手动示例配合 `tools/linux/if_line-py312.constraints.txt` 与外置 npm 锁安装。首次安装成功后保留本机 `pip freeze`、`pip check`、`npm ls`、生成的 `package-lock.json`、Node/PG/Redis/Chromium 版本和模型 SHA-256；后续同一轮使用这套依赖，不要中途重新解析安装。

离线服务器需提前备齐：目标 Linux/Python/架构的 wheel 集合、同版本 Node Linux 包、npm 包或在同平台构建的 media-tools、对应 Playwright 浏览器目录、ONNX 文件、系统 `.deb` 及依赖、中文字体。可在目标兼容机器用 `pip wheel -r requirements-media.txt --wheel-dir ...` 制作 wheel 集，再用 `pip install --no-index --find-links ... -r requirements-media.txt` 安装。只下载故事仓库不包含这些大文件，也不代表已经具备离线运行环境。

## 3. 补齐配置后再预检

统一配置必须保留三个 `systems` 条目和公共的文字、图片、视觉审核模型，即使只执行 IF Line。密钥只通过环境变量传入；使用统一安装器生成的私有 `work/secrets.env`，不要把值写进 JSON。三个 provider 地址必须以 `/v1` 结束，图片模型必须是共同的 `gpt-image-2`。提供方还须支持原生实际使用的图像接口与视觉输入；预检不检查远端鉴权、限流或模型能力。

IF Line 当前必要字段如下；示例为布局说明，`<repo>` 要替换成接收机器的实际绝对路径：

```json
{
  "repo_path": "<repo>/systems/if_line",
  "source_lock": "<repo>/baseline-lock.json",
  "source_patch": "<repo>/native-patches/if_line/manifest.json",
  "python_executable": "<repo>/work/envs/if_line/bin/python",
  "node_executable": "<repo>/work/node/bin/node",
  "node_modules": "<repo>/work/media-tools/node_modules",
  "pg_bin": "/usr/lib/postgresql/16/bin",
  "redis_executable": "/usr/bin/redis-server",
  "rembg_model_dir": "<repo>/work/models/if_line",
  "chapter_count": 13
}
```

配置里的相对路径按 **配置文件所在目录** 解析，环境变量字符串不会自动展开。把模板从 `benchmark/configs/` 移到 `work/config/` 时，原来的 `../../...` 路径必须重新计算。统一配置程序会完成路径处理；手工填写时优先使用绝对路径，保留 venv 的 `bin/python` 路径，不要把它解符号链接成系统解释器。

`pg_bin` 不能省略：当前驱动的旧默认值不适用于 Linux。图文程序会覆盖旧文本入口的 `managed_runtime`、`entry_mode` 和 `database_url_env`，自行建立隔离服务；无需配置 `IFLINE_BENCH_DATABASE_URL`、手动迁移数据库、创建账号或提供 sid。图文模式自动采用 unlimited 政策，禁止在系统字段里额外设置 token、调用次数、时长或窗口上限。

现在的 IF Line 是原始基线 `572407fce9b648a4206ac37da6a9f6ed22631da8` 加公开的 `if_line_html_script_conversion_fix_v1` 源码变体。克隆下来的文件**已经包含补丁，不要再 apply 一次**。`source_lock` 继续使用原始基线锁，`source_patch` 指向补丁清单；两者缺一不可。HTML 格式标签和实体转换有确定的原文坐标映射，原始章节仍完整保存，Script IR 仍严格拒绝正文遗漏、改写或乱序。详见 [补丁范围](../IFLINE_HTML_PATCH.md)。

原生运行目录和数据库每根独立，临时服务仅监听 127.0.0.1，正常或异常退出都会尝试回收。无需 systemd，因此可在 WSL2 或普通容器进程中管理；数据库使用本机 trust 认证，这一部署应放在受控实验机器或隔离容器内，而非向其他租户开放的数据库服务。

## 4. 不付费的本机检查

常规预检只检查文件、依赖存在性、源码身份、题目与密钥变量是否存在。它**不会启动 Chromium，也不会验证 ONNX 能成功推理**。先执行上面的 ONNX 加载检查，再用本地 HTML 做 Chromium 启动和中文截图探针：

```bash
mkdir -p "$BENCH_REPO/work/checks/if_line"
"$IF_NODE" - <<'JS'
const fs = require('fs');
const path = require('path');
const repo = process.env.BENCH_REPO;
const {chromium} = require(path.join(repo, 'work/media-tools/node_modules/playwright'));
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.setContent('<meta charset="UTF-8"><p>雨夜信号：中文画面检查</p>');
    await page.screenshot({path: path.join(repo, 'work/checks/if_line/chromium.png')});
    fs.writeFileSync(path.join(repo, 'work/checks/if_line/chromium-version.txt'), browser.version());
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
JS

python3 tools/run_batch.py --system if_line --preflight
```

截图应有可读中文，而非空白或方框。以上步骤没有调用生成 API；预检中仅缺密钥时可先完成其他环境检查，再通过统一私有环境文件提供真实凭证。不要把占位密钥用于正式运行。

请优先执行 `tools/smoke_test.py`；它负责准备只使用 localhost 的固定响应环境、正确的 Linux PG 路径及当前源码补丁声明。手动设置旧文档的 `IFLINE_BATCH_E2E=1` 不会自动补齐全部环境变量。局部单元测试通过也不能替代原生 PG/Redis/Vue 图文流程验证。

## 5. 启动、窗口与选择

下列统一入口从当前 `v4-30-open-actions-pilot.2` 选择题目，并将共同输入冻结到新实验目录；4000 个新增可见字符为观察范围。公共开头、内部规划和候选预览不计新增字数。旧模板 `CAMPUS-01-V4` 是底层诊断的指定行动单题，不是统一入口的默认 30 题。

```bash
bash experiment.sh run --allow-pilot --systems if_line --count 2 --concurrency 1 \
  --out work/experiments/round-01-ifline
```

统一入口读取默认配置及 `work/secrets.env`，展示规模并要求 `RUN` 确认后调用真实模型。`--count` 是所选系统的总尝试数，按选中题目顺序循环使用；`--concurrency` 是同时活跃的根运行数，每根都有独立服务。验证接收机器资源后可把并发改为 2。三个项目要公平比较时，使用同一配置的题目顺序、数量、模型、选择政策和阅读延迟，并记录调度及机器负载。

IF Line 保留原生 Bible → 13 章总大纲 → 固定开头导入 → 原生候选 → 选择后路线提升 → 章节生成 → Script IR → 素材生成/审核 → 图编译 → 原生播放器。开头后的首次选择可能尚无新增正文，这是原生结构。`chapter_count=13` 是规划长度，不是承诺会生成 13 章，也不是共同窗口的替代品。

`choice_indices` 为从 0 开始的序号。例如 `[0, 1]` 表示第一菜单选第 1 项，其后选第 2 项；序列用完重复最后一项。作者路线提升和实际播放器菜单共用这一个累计序号；记录原生 ID、菜单文字及选择结果，不把不同项目菜单改写成同一语义。菜单中没有该序号会明确失败。当前只探索这一条实际路径，不自动展开所有路线。

新增正文到达共同窗口后，在规定句界停止；最后一幅原生画面和完整原生节点仍保留，导出正文可能略超目标字数。`scope_reached=true` 才表示完成本次观察，`native_ended` 另记原生是否结束。全篇结局不作为本次连续片段评审的加扣分项。

恢复和校验均使用原批次目录：

```bash
bash experiment.sh resume --out work/experiments/round-01-ifline --concurrency 1
bash experiment.sh verify --out work/experiments/round-01-ifline
```

`--resume` 只派发从未开始的排队根；已经启动、失败、封存或送达未知的根不会自动再收费重跑。新尝试用新的输出目录并保留旧失败。统一入口与底层批量程序的退出码不同，见 [实验参考](../OPEN_ACTION_EXPERIMENTS.md)。退出码均不代替内容评审和逐根停止原因检查。改动执行代码后不能恢复旧计划。

## 6. 把哪些产物交给评审

统一入口实验汇总在 `experiment_summary.json`；实验的 `if_line/` 子目录包含批次 `results.json` / `results.csv` 和每根 `runs/<run_id>/`。保留整个根目录才能离线核验引用和哈希；不要只复制故事文字或单张图片。

| 路径 | 内容 |
|---|---|
| `manifest.json`、`metrics.json` | 输入/代码身份、窗口完成度、停止原因、采集完整性及八项指标。 |
| `trajectories/main/story.jsonl`、`choices.jsonl` | 实际访问正文、顺序、原生来源和选择。 |
| `visuals/frame_map.jsonl` 及其引用图片 | 段落对应的无字画面/UI 画面、所用图片资产与可见人物线索。 |
| `images/assets.jsonl`、`outputs.jsonl` | 原生生成、派生、复用、候选和实际使用关系；生成数与使用数分列。 |
| `characters/versions.jsonl` | 人物版本及视觉参考资产；是否有足够重复出场另行标记。 |
| `telemetry/calls.jsonl`、`events.jsonl`、`raw/` | 实际调用、供应商 usage、重试/失败、等待时间和请求响应证据。 |
| `native/authoring/native/`、`native/trace/` | 原生章节/任务/Script IR/故事图及调用来源。 |
| `evaluation/` | 事实、连贯性、人物视觉、阅读体验、图文匹配的五份评审包。 |

八项字段逐条说明见 [BATCH_RECORDING.md](../BATCH_RECORDING.md) 和 [原始记录规范](../../benchmark/source/recording_spec.v0.1.md)。token 拿不到真实 usage 时保留 null 和已知小计，不能当作零；人物出场不足时标记证据不足；等待时间记录后台观察而非未测的真人界面显示时间。M1/M2/M3/M5/M6 的分数留空，由后续独立裁判评价。

生成/恢复后，本项目与其他两个项目一样自动整理离线图文目录和评审 ZIP。实验目录中的 `review-delivery.json.entry_file` 是本地浏览器入口，`REVIEW_DELIVERY.txt` 标出发送的 ZIP。接收者完整解压后双击 `打开故事.html`，无需运行原生服务。回放只展示实际路径；完整八项证据仍在原始目录，`organizer.json` 保留原始指标且不在盲审 ZIP 内。操作和故障定位统一见 [离线回放说明](../PLAYBACK_REVIEW.md)。

## 7. 常见失败怎么定位

| 现象 | 先检查 | 处理 |
|---|---|---|
| `missing_initdb` / `missing_pg_ctl` / `missing_createdb` | `pg_bin` 目录 | Ubuntu 24.04 的 PostgreSQL 16 应指向 `/usr/lib/postgresql/16/bin`，不是命令别名或系统服务地址。 |
| `isolated_postgres_failed` | `native/postgres/control.log` / `postgres.log` | 看是否使用 root、输出目录权限/磁盘不足、二进制缺失；由普通用户在 Linux 本地目录运行。 |
| `missing_native_dependency` / import 失败 | IF venv 中 `pip check` 及实际 import | 保留解释器路径，按外置 `requirements-media.txt` 安装；不要仅给公共 Python 装包。 |
| `missing_native_rembg_model` / ONNX 加载失败 | `rembg_model_dir`、模型哈希、CPU session 探针 | 提前补齐并验证模型，不在付费生成过程中临时下载。 |
| Chromium executable 不存在 / missing shared libraries | `native/.../playback_*/renderer.log` | 使用本地固定版本 Playwright 重新安装 Linux Chromium/系统库，并保持相同缓存环境变量。 |
| 中文截图方框或布局跨机变化 | 中文字体、`fc-match`、Chromium 版本 | 统一系统字体与浏览器后重做本地截图探针；不要改原生 Vue 源码临时补字体。 |
| 源码校验失败 | `source_lock`、`source_patch`、原生文件 | 当前快照已应用 HTML 补丁；声明正确清单，勿再次打补丁或更新原始基线锁掩盖变化。 |
| 401 / 429 / SSL 或连接失败 | `telemetry/calls.jsonl`、供应商状态 | 区分鉴权/限流/传输问题与原生创作失败。保留已发生调用，修配置后另开新批次；unlimited 不取消供应商限制。 |
| `native_task_failed` / 正文覆盖校验错误 | `native/authoring/native/*_task.json`、worker 日志 | 看原生任务的真实错误；HTML 格式已修复，但模型确实漏句仍应被拒绝。不要删除段落或绕过校验来让任务变绿。 |
| 退出 0 但正文不足 | 根 manifest 的 `stop_reason`、`scope_reached` 与 errors | 封存成功不等于故事成功；原生错误和输出质量问题均保留。 |
| Ctrl+C 后退出较慢 | 仍在收集的模型调用与自有服务 | 等待正常清理；强杀后先核实本次 PID/端口，勿用全局 `pkill` 误停其他任务。 |

排错时先保存失败根目录和版本，不自动续费重跑、不人工修订故事。旧文档的 v3 文字入口、手工数据库配置及有预算示例属于历史模式，本页对应当前 v4 图文批量入口。
