# InfiPlot：Ubuntu 与 Windows WSL2 图文批量生成

**本页是项目依赖、原生行为和排错参考。首次接手及日常实验请使用 [操作者交接手册](../OPERATOR_HANDOFF.md) 的 `bash experiment.sh`，三个项目使用同一套选题、记录和离线回放流程。**

本指南面向 **Ubuntu 24.04 LTS**，以及 Windows 上的 **WSL2 + Ubuntu 24.04 LTS**。Windows 用户进入 WSL 的 Ubuntu 终端后，使用同一套 Linux 命令。此批量驱动使用 POSIX 进程组清理；不要直接用 Windows Python、PowerShell、Git Bash 或 `node.exe` 启动它。

**验证范围：** 仓库已有原生 Next/React、本地双进程并行、恢复零重发、64 MiB/null 请求容量及真实图文故事的记录；这些既有证据来自原开发环境。本文按冻结源码及官方安装说明核对 Linux 部署步骤，不能把它当作“新 Ubuntu/WSL2 机器已完成真实生成”的证明。新机器需要先执行下面的本地验收，再运行自己的真实样本。

## 1. 准备 Linux 环境

Windows 在管理员 PowerShell 中安装 WSL2 Ubuntu；系统要求重启时先完成重启，再创建 Linux 用户：

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
wsl -d Ubuntu-24.04
```

`wsl --list --verbose` 中 Ubuntu-24.04 的 VERSION 应为 2。安装命令和发行版选择以 [Microsoft WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install) 与 [Ubuntu 24.04 WSL 说明](https://documentation.ubuntu.com/wsl/latest/tutorials/develop-with-ubuntu-wsl/) 为准。

在 Ubuntu 终端或 Linux 服务器的 SSH 会话中克隆仓库。WSL 把仓库、依赖缓存和运行结果放在 Linux 文件系统，例如 Linux 用户的项目目录；不要混用 `/mnt/c` 内的 Windows 依赖或复制其他操作系统的 `node_modules`。

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
bash tools/bootstrap_linux.sh --system infiplot
source work/activate.sh
python3 tools/configure_batch.py
python3 tools/doctor.py --system infiplot
```

安装器会安装所选项目所需的系统和工作目录依赖，不开始真实故事生成。仅当管理员已另行安装完系统依赖时使用 `--skip-system-deps`。三个项目都需要时改用 `--system all`，后续仍共用一份模型与题目配置。检查 `work/config/batch.local.json` 的供应商地址/模型及题目；按仓库 [主说明](../../README.md) 填好仅本机保存的 `work/secrets.env`。`doctor` 不调用真实模型，因此通过不代表密钥余额、供应商能力或真实生成成功。

配置文件已存在时，生成器会拒绝覆盖；编辑现有文件，或确认需要重建时显式使用 `--force`，已有密钥文件保持原样。

每次新终端先回到仓库根目录并 `source work/activate.sh`；其中设置 Linux 工具 PATH 与 `PLAYWRIGHT_BROWSERS_PATH`，不设置 `PYTHONPATH` 或 `NODE_PATH`。本项目使用统一目录，不需要 Codex，也不依赖开发者的个人路径：

| 内容 | 仓库内位置 / 版本 |
|---|---|
| 公共 Python | `work/envs/common/bin/python`，Ubuntu 24.04 的 Python 3.12 环境，安装 Pillow |
| Node.js | `work/node/bin/node`，固定 **22.12.0**；原生声明要求 Node >=22 |
| pnpm | `work/node/bin/pnpm`，固定 **9.12.0**，与原生 `packageManager` 一致 |
| 原生依赖预热目录 | `work/infiplot-deps/`，复制原生 `package.json`、`pnpm-lock.yaml` |
| 浏览器模块 | `work/media-tools/node_modules/playwright`，固定 **1.55.1** |
| 浏览器缓存 | `work/browser-cache/`，激活脚本设置 `PLAYWRIGHT_BROWSERS_PATH` |
| 本地配置 | `work/config/batch.local.json` |
| 密钥 | `work/secrets.env`，仅本机保存，通过环境变量读取 |
| 原始源码 | `systems/infiplot/`，不得安装、构建或改写 |

Node 版本来自仓库既有验证条件；可核对 [Node.js 22.12.0 官方发行页](https://nodejs.org/en/blog/release/v22.12.0)。冻结 `pnpm-lock.yaml` 实际安装 **Next 16.2.7、React 19.2.7、OpenAI SDK 6.42.0**。外置配置钩子明确拒绝未经验证的其他 Next 版本；不能用 `pnpm update` 或重新生成 lockfile 来绕过。

安装器的公共 Python 依赖使用 [common Python 3.12 constraints](../../tools/linux/common-py312.constraints.txt)；外置 Vue/Playwright 工具把 [renderer npm 锁](../../tools/linux/renderer-package-lock.json) 复制到 `work/media-tools/package-lock.json` 后执行 `npm ci`。这份 npm 锁用于外置观察/播放工具，**不是 InfiPlot 的原生依赖锁**；原生项目仍严格使用自己的 `pnpm-lock.yaml`。所有锁的来源和平台范围见 [Linux 环境锁](../../tools/linux/README.md)。

本次 Ubuntu 24.04 x86_64 的依赖安装、模型准备以及 InfiPlot 空目录离线安装已经完成；这不等于原生图文故事已通过 Linux 验收。原生 smoke、真实故事和 Windows/ARM64 的具体验证范围以 [交接验证记录](../HANDOFF_VALIDATION_20260907.md) 为准。

## 2. 原生依赖缓存与无界面浏览器

InfiPlot 每个根运行都会复制 927 个冻结源码文件，在自己的临时目录执行：

```text
pnpm install --frozen-lockfile --offline
```

因此，只有安装 Playwright 或在别处已有 `node_modules` 并不足够。安装器应在**目标 Linux 系统、实际运行用户**下预热 pnpm store。需要手工补齐时，在仓库根执行：

```bash
export PATH="$PWD/work/node/bin:$PATH"
mkdir -p work/infiplot-deps
cp systems/infiplot/package.json systems/infiplot/pnpm-lock.yaml work/infiplot-deps/
pnpm --dir work/infiplot-deps install --frozen-lockfile
pnpm --dir work/infiplot-deps store path
```

这份冻结项目没有 workspace、相对本地包、补丁目录或项目 postinstall，预热仅复制上述两文件即可。不要加 `--prod`、`--no-optional` 或 `--ignore-scripts`：Next 运行编译和本机平台依赖仍需要完整冻结安装。原生安装使用的 store 必须与预热相同；统一安装器使用当前 Linux 用户的默认 store，任务也应由同一用户运行。若操作人员另设 `npm_config_store_dir`，预热、新终端、调度器及服务账户必须继承同一设置。`--offline` 在缺包时会失败，不会自动联网补包；`--frozen-lockfile` 不会重写锁。[pnpm 安装参数说明](https://pnpm.io/cli/install)

可用一个全新临时目录检查“空 node_modules + 离线安装”是否成立，避免仅检查已安装目录而误以为缓存齐全：

```bash
infiplot_cache_probe=$(mktemp -d "$PWD/work/infiplot-offline-check.XXXXXX")
cp systems/infiplot/package.json systems/infiplot/pnpm-lock.yaml "$infiplot_cache_probe/"
pnpm --dir "$infiplot_cache_probe" install --frozen-lockfile --offline
```

Playwright 包与 Chromium 必须配套。Linux 除下载 Chromium 外，还要安装其系统共享库；统一安装器负责这一步。如果需要修复浏览器安装，使用同一个 Playwright 模块和运行时采用的 `PLAYWRIGHT_BROWSERS_PATH`：

```bash
source work/activate.sh
node work/media-tools/node_modules/playwright/cli.js install --with-deps chromium
```

上述命令可能请求 sudo 安装 Linux 系统依赖。不要用另一个版本的 `npx playwright` 下载浏览器。[Playwright 浏览器及系统依赖说明](https://playwright.dev/docs/browsers)

驱动明确启动 `headless: true` 的 Chromium，Linux 服务器**不需要桌面、DISPLAY、Xvfb 或 WSLg**。可在已激活统一环境后执行不访问模型的浏览器检查：

```bash
env -u DISPLAY node - <<'JS'
const {chromium} = require('./work/media-tools/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({headless: true});
  try {
    const page = await browser.newPage();
    await page.setContent('<p>浏览器就绪</p>');
    console.log(await page.locator('p').textContent());
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
JS
```

pnpm 离线安装不代表故事运行完全离线。原生 `app/layout.tsx` 使用 `next/font/google` 的 Cormorant Garamond 与 Inter；首次原生 Next 编译可能访问 Google 字体资源。包安装还需 npm/浏览器下载站，真实生成需自己的模型供应商。字体请求、DNS 或证书失败应修复服务器网络条件并保留错误，不能通过改原生字体、关 TLS 校验或篡改源码锁让测试“通过”。

## 3. 检查 InfiPlot 本地配置

统一配置生成器会写入本机路径。InfiPlot 专用段应包含下列信息；这里是字段说明，路径由工具生成，不要把示例占位符直接当配置运行。

| 字段 | 应用值 / 含义 |
|---|---|
| `repo_path` / `source_lock` | 本次克隆中的 `systems/infiplot` / `baseline-lock.json` |
| `node_executable` / `pnpm_executable` | `work/node/bin` 下实际 Linux 可执行文件的绝对路径 |
| `playwright_module` | `work/media-tools/node_modules/playwright` 的绝对目录 |
| `chromium_executable` | 通常省略，使用上述 Playwright 配套 Chromium |
| `shared_opening` | `true` |
| `route_compatibility` | `native_render_entry` |
| `identity_mode` | `local_fixture` |
| `prefetch_policy` | `native_browser` |
| `native_request_body_limit_bytes` | `67108864`，即 64 MiB；显式 `null` 恢复冻结原生默认 10 MiB |
| `dependency_timeout_seconds` / `startup_timeout_seconds` | 统一配置为 600 / 120 秒；原生驱动的未配置默认值为 180 / 120 秒。属于安装与启动期限 |
| `keep_runtime` | 默认 `false`，保存证据后清理每次临时源码与依赖目录 |

不要只复制旧 `batch.example.json` 后直接运行：旧模板的 InfiPlot 段未填 `playwright_module` 与 `pnpm_executable`；默认 Playwright 查找位置是 Codex 私有运行时目录，新服务器通常不存在。`node_modules` 字段不能替代 InfiPlot 每次原生离线安装。配置文件移动后，相对路径按**新配置所在目录**解析；不要照抄历史证据中的 `/Users/...`、`/opt/homebrew/...` 或某个人的 `.nvm` 位置。

三类供应商只配置在公共 `providers` 中，保留 text/vision/image 三个条目，图片模型为共同 `gpt-image-2`。地址必须以 `/v1` 结束，密钥写环境变量名称。供应商需要支持原生使用的文字调用/流式响应以及图像 `images/generations`、带参考图的 `images/edits`；模型名称可填写不等于供应商一定提供。InfiPlot 单独运行也要保留配置的三个系统条目，但只会预检和执行所选项目。

## 4. 开始生成、并行与恢复

以下命令都在仓库根执行，并已加载统一环境、供应商环境变量和生成好的 `work/config/batch.local.json`。预检不调用付费模型，但会要求配置声明的密钥变量存在。

```bash
python3 tools/verify_sources.py
python3 tools/run_batch.py --system infiplot --preflight
```

统一包装入口安全读取 `work/secrets.env`，不把它作为 shell 脚本执行。终端已显式导出的同名密钥优先；改变供应商时同时检查旧环境变量。

先完成第 6 节的本地 smoke test，再用全新实验目录跑一个真实样本。下列日常命令使用统一入口当前的 30 题开放行动候选集，读取本机供应商配置与密钥，展示规模并要求 `RUN` 确认；`quick` 只选第一题：

```bash
bash experiment.sh quick --allow-pilot --systems infiplot \
  --out work/experiments/infiplot-live-check-01

bash experiment.sh run --allow-pilot --systems infiplot --count 6 --concurrency 2 \
  --out work/experiments/infiplot-batch-01
```

`--count` 是所选系统总尝试数，按选中题目顺序循环取题；默认 30 题时 `count=6` 只覆盖前 6 题。`--concurrency` 是同时运行的独立进程数。每个进程拥有自己的 Next 编译目录、浏览器、身份服务、原生端口与模型 relay；增加并发也会增加内存、磁盘、字体下载和模型调用压力，不能把单进程成功当作该服务器任意并发均可用。

```bash
bash experiment.sh resume --out work/experiments/infiplot-batch-01 --concurrency 2
bash experiment.sh verify --out work/experiments/infiplot-batch-01
```

恢复只派发从未开始的排队任务，不重发成功、失败或送达未知的故事。`state=sealed` 只表示证据封存。统一入口与底层批量程序的退出码不同，见 [实验参考](../OPEN_ACTION_EXPERIMENTS.md)；均不代表内容质量通过。更新适配器、原生来源或共同条件后，新建批次；不编辑旧 plan，也不覆盖失败证据。完整共同命令见 [批量运行说明](../BATCH_RUNNING.md)。

## 5. 连续故事的公平范围与记录

InfiPlot 原生持续续写，因此共同任务按连续阅读窗口结束；当前默认 30 题使用 **4000 个新增可见 Unicode 字符** 的候选观察范围。到达阈值后按公共句界规则保存，实际字数可能略多；公共开头、菜单标题、内部规划和未选预取不计新增正文。窗口从共同 bundle 读取，不在单个系统配置里另设。`scope_reached=true` 与 `stop_reason=reading_window` 表示完成本次观察，`native_ended=null` 不表示适配器没工作，也不能改写成全篇已结束。

`choice_indices` 是从 0 开始的菜单下标序列，用完重复最后一个。只记录原生 Session 确实提交的选择及其后续正文，不把点击意图或预取内容当成已走路径。评审只评价共同观察范围，不因某系统有结局额外加分，也不因 InfiPlot 没有全篇结局扣分；事实冲突、重复和没有推进仍可评价。

统一入口每根运行位于 `work/experiments/<实验>/infiplot/runs/<run_id>/`，单系统底层入口则是其批次目录中的 `runs/<run_id>/`。至少核对：

| 内容 | 证据位置 |
|---|---|
| 范围、停止原因、来源和文件哈希 | `manifest.json`、`errors.jsonl` |
| 公共提示词、固定开头和约束 | `inputs/`、`evaluation/requirements_blind.json` |
| 实际正文与已执行选择 | `trajectories/main/story.jsonl`、`trajectories/main/choices.jsonl` |
| 人物版本 | `characters/versions.jsonl` |
| 图片请求、候选、资产 | `images/requests.jsonl`、`images/outputs.jsonl`、`images/assets.jsonl` |
| 正文与画面的对应关系 | `visuals/frame_map.jsonl` 及其引用的 clean/UI 图片 |
| 用量与等待时间 | `telemetry/calls.jsonl`、`telemetry/events.jsonl`、`metrics.json` |
| 五份内容评审包 | `evaluation/` |
| 容量、身份与页面入口的实际条件 | `native/request-body-config.json`、`native/auth-provenance.json`、`native/route-compatibility.json` |
| 大响应与正文来源覆盖 | `native/server-response-capture-audit.json`、`native/text-call-lineage-audit.json` |
| 源码与服务清理 | `native/source-attestation.json`、`native/cleanup.json` |

M1/M2/M3/M5/M6 保存 AI 评审证据，分数在本阶段留空；M4 记录后端和无界面 DOM 时间，真实桌面呈现时间未测；M7 保存真实 usage 与缺失覆盖率；M8 分开统计图像请求、返回候选、落盘资产和实际画面。完整对应关系见 [八项评测记录说明](../BATCH_RECORDING.md)。

原生预取保持启用。停止时关闭浏览器与原生消费者，网关继续收集已经发送的模型请求、图片和用量，因此退出可能需要等待。未读预取被关闭可能令**全局响应捕获 incomplete**，应与**已显示正文的调用关联是否完整**分开核查，不能删除这类记录以宣称全通过。具体来源边界见 [大响应记录说明](../INFIPLOT_RESPONSE_RECORDING.md)。

生成/恢复后，本项目与其他两个项目一样自动整理离线图文目录和评审 ZIP。实验目录中的 `review-delivery.json.entry_file` 是本地浏览器入口，`REVIEW_DELIVERY.txt` 标出发送的 ZIP。接收者完整解压后双击 `打开故事.html`，无需运行原生服务。回放只展示实际路径；完整八项证据仍在原始目录，`organizer.json` 保留原始指标且不在盲审 ZIP 内。操作和故障定位统一见 [离线回放说明](../PLAYBACK_REVIEW.md)。

## 6. Linux 本地验收与故障定位

下面的测试只用 localhost 固定响应与合成图片，不需要真实 API 密钥。证据路径必须全新；运行前仍需准备 Linux 依赖、匹配 Chromium 和字体网络条件。优先使用统一入口：

```bash
source work/activate.sh
python3 tools/smoke_test.py --system infiplot --out work/smoke-infi
```

若要单独诊断原生浏览器或 64 MiB/null 容量，可以直接运行原始测试：

```bash
infiplot_check_root=$(mktemp -d "$PWD/work/infiplot-local-check.XXXXXX")
PYTHONPATH="$PWD/benchmark" \
INFIPLOT_BATCH_TEST=1 \
INFIPLOT_BATCH_EVIDENCE="$infiplot_check_root/native-browser" \
INFIPLOT_PLAYWRIGHT_MODULE="$PWD/work/media-tools/node_modules/playwright" \
work/envs/common/bin/python -m unittest discover \
  -s benchmark/tests -p test_infiplot_batch.py -v

PYTHONPATH="$PWD/benchmark" \
INFIPLOT_BODY_TEST=1 \
INFIPLOT_BODY_EVIDENCE="$infiplot_check_root/request-capacity" \
work/envs/common/bin/python -m unittest discover \
  -s benchmark/tests -p test_infiplot_request_body.py -v
```

原始 unittest 命令从仓库根执行时显式设置 `PYTHONPATH` 指向 `benchmark`；不要假设激活脚本已设置。不设置 `INFIPLOT_BATCH_TEST=1` / `INFIPLOT_BODY_TEST=1` 会跳过真实原生测试；不能把“无失败但有 skip”当成部署验收。容量测试故意缺少有效 Session，相同约 12 MiB JSON 在 64 MiB 下返回 `session is required`，证明完整解析；它不调用模型，也不等于真实长故事已生成。

| 症状 | 定位与处理 |
|---|---|
| `playwright_module_required` / `Executable doesn't exist` | 核对模块绝对路径、Playwright 1.55.1、Chromium 下载及运行时相同的 `PLAYWRIGHT_BROWSERS_PATH` |
| Chromium 缺共享库或无法启动 | 运行对应模块的 `install --with-deps chromium`；在目标 Linux 用户下执行无 DISPLAY 检查 |
| `dependency_install_failed` / 离线缺包 | 同用户、同 pnpm 9.12.0、同 store 重新预热，并用全新目录做离线安装验证；不要改 lockfile |
| `Unverified Next config loader version` | 恢复冻结 lockfile 的 Next 16.2.7；不要修改版本校验来放行 |
| `/play` 自重定向、页面一直 booting | 核对 `route_compatibility=native_render_entry`；查 `native/route-compatibility.json` 与 `native/console.jsonl` |
| 启动 120 秒超时、字体编译失败 | 查原生 console 的 DNS/TLS/字体错误和机器资源；初次部署降低并发。若调整基础设施超时，记录新条件并新建批次 |
| `Invalid JSON` / 大历史请求失败 | 看 request-body-config 实际 loads 和请求字节，确认 64 MiB 生效；64 MiB 是有限容量，不会自动扩容或无限续写 |
| 401/429/图片模型不支持 | 区分 localhost 身份路由与外部供应商错误；核对公共端点、环境变量及供应商服务能力，不改故事或自动挑另一路 |
| `text_call_lineage_incomplete` | 保留全部响应与正文证据，检查唯一 operation/Writer 对应；不能手工补猜 `source_call_ids` |
| 中断后退出慢 | 网关在收集已发送请求的终态；不要为缩短等待直接删除运行目录或改日志 |
| `native_source_verification_failed` / `unlocked_source_files` | 检查是否在 `systems/infiplot` 内安装/构建/编辑；依赖应在 work，原基线保持原字节 |

## 身份与部署边界

这个程序用于在服务器上批量生成和保存评测证据，**不是对外提供账号登录的网站部署器**。`identity_mode=local_fixture` 会为每根运行创建本地独立身份服务及随机会话，原生 API 鉴权代码照常执行；`production_account_authentication_verified=false` 明确表示没有验证真实 Supabase 账号。不得把本地 fixture 当成公开站点的生产鉴权方案，也不要把这些临时 localhost 服务暴露到公网。

`route_compatibility=native_render_entry` 只把 GET/HEAD `/play` 交给原 Next 中文页面渲染入口，绕过这一页面入口的 locale 重定向与 cookie 刷新；API 仍走原始鉴权和处理。`native_request_body_limit_bytes` 只改变运行时请求容量，原始配置文件、Session、图片字节、共同提示词不变。这些外置接入规则均在每次证据中披露，不能称为原始网站默认部署完全未受调整。
