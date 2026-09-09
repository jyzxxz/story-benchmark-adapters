# 从零安装与生成故事

这份仓库包含三个冻结项目、外置适配器、独立批量入口和评测记录器。接收者在 **Ubuntu 24.04 LTS 普通用户**下运行；Windows 用户先按 [WSL2 安装说明](WINDOWS_WSL2.md) 进入 Ubuntu。安装系统软件包时可以使用 sudo，后续安装工作目录依赖与生成故事不要用 root；IF Line 的临时 PostgreSQL 也要求非 root 用户。

本文是交接操作流程。已有开发机测试记录与**新 Linux/WSL2 服务器验收**须分开：不能因为安装器、预检或本地夹具通过，就宣称三项目真实生成必定成功。原生故障与当前 Linux 兼容性边界见本页末尾及各项目说明。开始真实运行前，尤其先核对 AI4 的角色图片大小写路径风险。

## 1. 取得仓库

如果仓库是私有的，接收者需先获得仓库所有者授予的 GitHub 访问权限，并用自己的 GitHub 身份完成 HTTPS 或 SSH 认证。链接可见、持有 API 密钥，都不等于有仓库读取权限；`Repository not found` / 403 先检查访问授权。不要把 GitHub token 写进 clone URL。

在普通 Ubuntu 用户的 Linux 文件系统中执行：

```bash
sudo apt-get update
sudo apt-get install -y git
cd ~
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
git rev-parse HEAD
```

记录这个提交号。`systems/` 是受哈希校验的原生来源，依赖、密钥、数据库、模型缓存与产物放在 `work/`。不要把 Windows/macOS 的虚拟环境、`node_modules` 或个人绝对路径复制进来。

## 2. 安装三个独立环境

准备全部项目：

```bash
bash tools/bootstrap_linux.sh --system all
source work/activate.sh
python3 tools/configure_batch.py
python3 tools/doctor.py --system all
```

只使用一个项目时，把两处 `all` 改为 `if_line`、`ai4visualnovel` 或 `infiplot`。公共配置仍保留三个系统条目，运行时只启动所选项目。当前安装器始终准备公共 Node/Playwright 工具，项目专用 Python 环境和模型按 `--system` 选择安装。管理员已经准备完所需系统软件包时，bootstrap 才使用 `--skip-system-deps`。

安装器需要联网获取系统包、Python/Node 依赖、Chromium 和原生抠图模型；不开始付费故事生成。默认目录：

| 目录 | 用途 |
|---|---|
| `work/envs/common/` | 公共批量入口与证据校验 |
| `work/envs/if_line/`、`work/envs/ai4visualnovel/` | 各自独立的原生 Python 环境 |
| `work/node/` | Node 22.12.0 / pnpm 9.12.0 工具 |
| `work/media-tools/`、`work/browser-cache/` | 固定版本的浏览器模块与匹配 Chromium |
| `work/infiplot-deps/` | InfiPlot 冻结 pnpm 依赖缓存预热 |
| `work/models/` | 各项目原生 CPU 抠图模型 |
| `work/config/batch.local.json` | 一份共同模型、题目和本机路径配置 |
| `work/secrets.env` | 仅本机保存的 API 密钥 |
| `work/results/` | 每次运行的独立批次目录 |

配置文件已存在时，`configure_batch.py` 会拒绝覆盖；直接编辑现有配置，或在确认要重建默认配置时显式使用 `--force`。已有密钥文件不会被覆盖。

交接安装器现在使用本次 **Ubuntu 24.04 x86_64 / Python 3.12** 实际安装得到的三个独立 Python constraints：公共入口、IF Line 与 AI4VisualNovel 分别固定版本。外置 Vue/Playwright 工具使用 `tools/linux/renderer-package-lock.json` 和 `npm ci`；InfiPlot 原生依赖继续使用原有 `systems/infiplot/pnpm-lock.yaml`，不会换成这份外置 npm 锁。锁文件与适用范围见 [Linux 环境锁](../tools/linux/README.md)。

本次 Linux 依赖安装、模型准备及 InfiPlot 空目录离线依赖安装已完成；原生 smoke 和真实故事是否通过仍以 [交接验证记录](HANDOFF_VALIDATION_20260907.md) 为准，不能从“安装通过”推断生成通过。ARM64 安装逻辑不等于已执行 ARM64 验收，Windows 实体主机也须另行验证。软件包、浏览器、模型仍需下载，系统 apt 包的实际版本也应随实验保存。

`doctor` 不调用真实模型。它检查依赖与配置，不证明供应商余额、模型输出结构、原生剧情质量或新机器的完整生产能力。新终端先回到仓库根目录，再 `source work/activate.sh`；它设置公共 Python、Linux Node 的 PATH 和浏览器缓存路径；不导出 `NODE_PATH` 或 `PYTHONPATH`。统一工具自行处理模块路径。

项目所需环境细节分别见 [IF Line](projects/IF_LINE.md)、[AI4VisualNovel](projects/AI4VISUALNOVEL.md)、[InfiPlot](projects/INFIPLOT.md)。不要把三套原生 Python 包装进同一个环境。

## 3. 配置一次供应商和题目

编辑配置生成器创建的 `work/config/batch.local.json`。三个项目对同一种模型角色共用这一处配置：

| 公共字段 | 填写内容 |
|---|---|
| `providers.text` | 文字模型、以 `/v1` 结束的供应商地址、密钥环境变量名、共同模型参数 |
| `providers.image` | 共同图片模型 **`gpt-image-2`**、对应供应商地址、密钥变量名 |
| `providers.vision` | 原生视觉审核使用的模型、供应商地址、密钥变量名 |
| `bundles` | 已编译并验证的共同故事题目目录列表 |
| `choice_indices` | 从 0 开始的原生菜单下标序列；用完重复最后一个 |

供应商必须支持各项目实际使用的文字调用、图像生成及带参考图的图像编辑接口。某个模型名出现在模板里，不代表任意供应商都支持它。`providers.vision` 是原生流程的视觉审核，不是后续八项评测的外部裁判。

在 `work/secrets.env` 中填写上述配置所引用的变量，默认名称为：

```dotenv
BENCH_TEXT_API_KEY=替换为文字供应商密钥
BENCH_IMAGE_API_KEY=替换为图片供应商密钥
BENCH_VISION_API_KEY=替换为视觉供应商密钥
```

三种角色可使用不同密钥；三个项目对同一角色使用同一配置。`tools/run_batch.py` 默认读取此本地文件，不需要把密钥传给原生源码目录。不要提交或分享 `work/secrets.env`。在本机限制文件权限：

```bash
chmod 600 work/secrets.env
```

默认开发题 `CAMPUS-01-V4` 是 **pilot**，共同观察窗口为 **4000 个新增可见 Unicode 字符**。三个项目收到相同设定、固定开头和共同任务文本；原生规划、记忆、审核、预取和输出形式保留。故事文字允许不同，统一的是外部任务及评审范围。

需要改窗口或换题时，复制案例、更新案例版本与 `output_contract.window_chars`，重新编译一次，三个项目共用新 bundle。不要分别修改三个系统的提示词，也不要改已经冻结的批次。正式题目须完成内容确认，默认 pilot 不代表已批准的正式实验题库。操作详见 [共同范围与编译](BATCH_RUNNING.md)。

## 4. 先跑不付费的本地验收

三次使用**全新输出目录**；这里的响应和图片是明确标记的本地夹具：

```bash
python3 tools/smoke_test.py --system if_line --out work/smoke-ifline
python3 tools/smoke_test.py --system ai4visualnovel --out work/smoke-ai4vn
python3 tools/smoke_test.py --system infiplot --out work/smoke-infi
```

检查测试日志中的失败和 skip。依赖下载/字体访问可以联网，但这些 smoke 命令不调用真实付费模型。本地夹具验证原生入口、选择、输出关联和记录；它不能检验真实模型的创造质量、供应商限制，也不替代原生重型抠图模型在新机器上的加载检查。某项目检查未过，先查看对应项目文档与原始日志，不把失败目录改成成功。

## 5. 真实生成与并行

以下命令会使用配置中的真实模型，产生相应供应商消耗。先分别生成一次，确认新服务器的实际结果：

```bash
python3 tools/run_batch.py --system if_line --count 1 --concurrency 1 --out work/results/check-ifline
python3 tools/run_batch.py --system ai4visualnovel --count 1 --concurrency 1 --out work/results/check-ai4vn
python3 tools/run_batch.py --system infiplot --count 1 --concurrency 1 --out work/results/check-infi
```

确认各项目表现和机器资源后，可在三个终端并行运行。每个终端都进入同一仓库并 `source work/activate.sh`：

```bash
python3 tools/run_batch.py --system if_line --count 6 --concurrency 2 --out work/results/round1-ifline
python3 tools/run_batch.py --system ai4visualnovel --count 6 --concurrency 2 --out work/results/round1-ai4vn
python3 tools/run_batch.py --system infiplot --count 6 --concurrency 2 --out work/results/round1-infi
```

在一个终端依次执行上面三行会顺序运行；在三个终端分别执行才是跨项目同时运行。`count=6` 表示**每项目六次独立尝试**，不保证六个成功故事，也不是每个题目六次。多题按共同 `bundles` 顺序循环取题。`concurrency=2` 是**每项目**最多两个根进程；三个项目同时运行时最多六个根进程，根内部还保留原生模型并行、重试和预取。

适配器没有总调用次数、总 token、总生成时长或总费用预算。观察窗口控制采集范围，不能作为费用上限：某项目会先做完整故事图、审核、重写或预取未选路线，这些调用均计入本次消耗。供应商限流、额度、原生循环上限和单请求期限仍存在。先从低并发检查实际账单、内存和磁盘，再决定批量规模。

## 6. 查看结果、恢复队列

每个批次的 `results.json` / `results.csv` 汇总运行状态；每根的 `manifest.json`、`errors.jsonl` 与正文/图片证据在 `runs/<run_id>/`。

- `scope_reached=true`：达到共同观察范围；不表示故事全篇已经结束。
- `stop_reason=reading_window`：按共同窗口停止。InfiPlot 的 `native_ended=null` 保持原样，评审不因没有全篇结局而扣分。
- `state=sealed` 或程序退出码 0：证据或批次操作完成；原生生成仍可能失败，必须逐根查看结果。
- `metrics.json`：M1/M2/M3/M5/M6 保存待评审证据，分数留空；M4 是实测事件时间，M7 是 usage 与覆盖，M8 是图片请求/候选/资产/画面数量。真实桌面显示时间未测时保持未知。

停止时用 Ctrl+C；已经发出的供应商请求仍需收集终态和用量，因此清理可能需要等待。恢复与只读校验也使用统一入口，例如：

```bash
python3 tools/run_batch.py --system infiplot --out work/results/round1-infi --resume --concurrency 2
python3 tools/run_batch.py --system infiplot --out work/results/round1-infi --verify
```

包装入口在恢复时安全读取本地 dotenv 文件；校验模式不读取密钥、不调用模型。不要把 `work/secrets.env` 当作 shell 脚本 `source`。已在终端显式导出的同名密钥优先于 dotenv 文件；需要换供应商时检查自己的环境，避免旧变量覆盖新配置。

`--resume` 只派发**从未开始**的队列项；不补跑失败、已封存或送达未知的尝试。需要重新试验就新建输出目录，并保留旧失败。源码/适配器/共同条件变化后不得继续原来的冻结批次。校验不调用模型，也不会修复或美化故事。

正文、选择、人物、原始模型调用、候选图片、实际显示画面及五份内容评审包的字段对应见 [八项记录规范](BATCH_RECORDING.md)。不要只交付最终正文或图片文件夹；保留整个根运行目录，才能复核一段文字对应哪张画面、来源调用和使用量。

### 本地查看和发给评审者

实际生成结束后，实验/批次输出目录会写入 `review-delivery.json` 和 `REVIEW_DELIVERY.txt`。本地查看时，按 JSON 顶层的 `entry_file` 路径，用浏览器打开 `打开故事.html`，无需启动原生服务或先解压 ZIP。要交给别人，则发送 TXT 指定的 `review.zip`；接收者完整解压后双击 `打开故事.html`。

总目录包含所有已封存样本，失败和零正文记录也保留。`ready` 表示评阅包已就绪，不代表故事质量通过；`partial` 表示仍有未封存记录。八项原始证据继续保留在原批次中。旧结果补导出、Windows/WSL 和 Linux 服务器查看步骤见 [本地回放与人工评审](PLAYBACK_REVIEW.md)。给接收者阅读的短版见 [评审者使用说明](REVIEWER_QUICKSTART.md)。

## 7. 先区分环境、适配与原生故障

| 项目或层次 | 当前需要保留的边界 |
|---|---|
| 公共环境/适配 | 缺依赖、错误路径、输入不一致、来源哈希变化、正文/图片关联缺失属于需要处理的环境或接入问题；用 doctor、smoke 和封存校验定位。 |
| IF Line | 已包含用户批准并独立披露的 HTML 正文到脚本转换源码补丁；它与未改版 IF Line 是不同原生变体，不能隐去补丁，也不保证所有原生生成都成功。 |
| AI4VisualNovel | 原生严格要求故事图节点数，11/13 节点对期望 12 都可能失败；原生多 Agent 循环和审核可能大量调用。源码核查另发现：Artist 按原角色 ID 建目录，播放器却按 `character_id.lower()` 找立绘；ID 含大写时，在 Linux 大小写敏感文件系统上可能出现“已生成却未显示”的角色图。原生源码未修，此风险须在新机器验收中核对，见 [AI4 项目说明](projects/AI4VISUALNOVEL.md)。 |
| InfiPlot | 连续故事达到共同窗口即可参与评审；64 MiB 是有限请求容量。默认页面入口映射和本地独立身份 fixture 均公开记录，真实 Supabase 生产账号未验证。 |
| 供应商 | 401、429、网络/余额/模型支持错误须按实际响应判定；不要一概归为故事 prompt 或适配器问题。 |

三项目源文件、既有原生失败与发布测试范围分别见 [来源说明](PROVENANCE.md)、[整体复查](ADAPTER_AUDIT_20260907.md)、[真实并行记录](PARALLEL_RECHECK_20260907.md)。交接后的首次 Linux/WSL2 实测应另保存环境版本与输出证据；本文不会把尚未执行的操作写成通过。
