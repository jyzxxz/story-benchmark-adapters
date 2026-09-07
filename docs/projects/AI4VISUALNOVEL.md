# AI4VisualNovel：Ubuntu / Windows WSL2 批量生成

本页面向 **Ubuntu 24.04 LTS 服务器**，以及 **Windows 上的 WSL2 Ubuntu 24.04**。以下 Bash 命令均在 Linux 终端、仓库根目录执行。不要在 Windows 原生 Python、PowerShell 或 CMD 中直接运行批量程序：当前启动与清理使用 POSIX 进程组和信号。

**本页是按代码与依赖要求整理的安装路径，尚未完成 Ubuntu/WSL2 从零安装及真实模型验收。** 已有开发机的原生夹具验证不能替代 Linux 验收；历史 AI4 环境使用 Python 3.10.20，本次交付环境统一为 Python 3.12。

AI4 原生源码保持冻结版本 `0faf120244d175866eea3813f053281f5689ab19`，35 个文件逐字节校验。IF Line 的 HTML 源码补丁不适用于 AI4；不要给 `systems.ai4visualnovel` 添加 `source_patch`。

交接安装器使用 `tools/linux/` 中的 Ubuntu 24.04 / Python 3.12 constraints 固定本次依赖版本；外置播放器采用 npm 锁，原生源码依赖清单不改。实际安装记录和新 Linux 验证范围见 [交接检查](../HANDOFF_VALIDATION_20260907.md)。下文手动补装命令用于排错，正式复现优先使用统一安装器及同一套锁。

## 1. Windows 用户先进入 WSL2

已使用 Ubuntu 24.04 服务器的用户跳过本节。在管理员 PowerShell 中执行：

```powershell
wsl --install -d Ubuntu-24.04
wsl --list --verbose
```

按安装提示重启并创建 Linux 用户；确认 Ubuntu-24.04 的 VERSION 为 `2`。若已有该发行版但仍为 WSL1，执行 `wsl --set-version Ubuntu-24.04 2`，然后用 `wsl -d Ubuntu-24.04` 进入 Linux。[Microsoft WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)

在 Linux 用户目录中克隆仓库并安装依赖，例如 `~/story-benchmark-adapters`；使用 WSL 内的 Linux Python 和文件权限。后续生成在 Linux 文件系统内进行，结果可在生成结束后复制到 Windows。仓库若为私有，接收者需先取得 GitHub 访问权限。

## 2. 使用统一安装入口

先克隆仓库并进入其根目录。仅准备 AI4 时执行：

```bash
bash tools/bootstrap_linux.sh --system ai4visualnovel
source work/activate.sh
python3 tools/configure_batch.py
```

若要在同一服务器准备全部三个项目，将安装命令的系统改为 `--system all`。安装器需要获取系统软件包、Python 依赖与模型缓存；`--skip-system-deps` 只适用于管理员已经准备好系统依赖的机器。不要用 `sudo` 运行后续批次，也不要从旧开发机复制虚拟环境或绝对路径。

[Linux 安装器](../../tools/bootstrap_linux.sh) 准备环境，[配置生成器](../../tools/configure_batch.py) 默认建立 `work/config/batch.local.json`；详细统一流程见 [仓库入口说明](../../README.md)。

本项目所需目录如下。所有环境、模型和输出均在 `systems/` 之外：

| 路径 | 用途 |
| --- | --- |
| `work/envs/common/bin/python` | 公共批量入口、记录器、图片像素哈希与校验 |
| `work/envs/ai4visualnovel/bin/python` | Python 3.12 原生 AI4 独立环境 |
| `work/models/ai4visualnovel/isnet-anime.onnx` | 原生动漫人物抠图模型 |
| `work/config/batch.local.json` | 三项目共同配置，保留全部三个系统条目 |
| `work/secrets.env` | 本机密钥；统一 `tools/run_batch.py` 默认读取此文件 |
| `work/results/` | 每次批次的新输出目录 |

AI4 原生运行不需要 PostgreSQL、Redis、Node、Playwright 或可见桌面。统一安装器也准备了其他项目使用的公共工具和系统包，但它们不参与 AI4 原生生成。AI4 实际使用 Python、Pygame/SDL、Pillow、rembg 和 ONNX Runtime；文字、图像及原生视觉审核通过配置中的供应商 API 生成，抠图使用本机 CPU。

原始 [requirements.txt](../../systems/AI4VisualNovel/requirements.txt) 全部使用最低版本范围，**它不是依赖锁**。其中还保留 Google、Streamlit、PyInstaller 等原生其他入口的依赖；本批量程序使用共同 OpenAI 兼容网关，不启动 Streamlit，也不打包 Windows 可执行程序。统一安装器的 CPU 约束及安装后的版本记录用于固定实际运行环境，实验期间不要单独升级某个原生环境。

## 3. 核对 CPU 依赖、模型与中文渲染

Ubuntu 24.04 提供 Python 3.12 与 `python3.12-venv`。Pygame 需要可导入的 SDL/字体运行库；统一安装后先检查依赖冲突。[Ubuntu Python 3.12 venv 软件包](https://packages.ubuntu.com/noble/python3.12-venv)

```bash
work/envs/ai4visualnovel/bin/python --version
work/envs/ai4visualnovel/bin/python -m pip check
work/envs/common/bin/python -m pip check
```

CPU 环境应安装 `rembg[cpu]` 和 `onnxruntime`，不要同时装 `onnxruntime-gpu`、ROCm 或 DirectML 变体。历史 AI4 使用 `rembg 2.0.69`、`onnxruntime 1.23.2`、`pygame 2.6.1`；rembg 2.0.69 声明支持 Python 3.12，但这不等于整个新环境已通过验证。[rembg 2.0.69 CPU 安装与 Python 支持](https://pypi.org/project/rembg/2.0.69/)

下面只核对安装包及可用后端，不调用故事或图片 API：

```bash
work/envs/ai4visualnovel/bin/python - <<'PY'
from importlib.metadata import distributions

names = {d.metadata['Name'].lower().replace('_', '-') for d in distributions()}
conflicts = names & {'onnxruntime-gpu', 'onnxruntime-rocm', 'onnxruntime-directml'}
assert not conflicts, f'请新建独立 CPU 环境，存在冲突包：{sorted(conflicts)}'
assert 'onnxruntime' in names, '缺少 CPU onnxruntime'
import onnxruntime as ort
import openai, pygame, PIL, rembg, dotenv, yaml, jsonschema, requests
assert 'CPUExecutionProvider' in ort.get_available_providers()
print('Python 依赖与 CPU 后端可用：', ort.get_available_providers())
PY
```

不要在旧的 GPU 环境上直接覆盖安装 CPU 包。它们共享 `onnxruntime` 导入命名空间；新建独立 CPU 虚拟环境更容易确认实际加载的是哪一个发行包。[ONNX Runtime 安装说明](https://onnxruntime.ai/docs/install/)

### 预下载原生抠图模型

原生 `Artist._remove_background()` 固定调用 `new_session("isnet-anime")`。`u2net.onnx` 是另一个模型，不能代替它。外置驱动把配置中的 `rembg_model_dir` 传成原生 `U2NET_HOME`；原生预检只确认模块可导入，不下载模型，也不保证抠图推理一定成功。

安装器已下载模型时，可用下面命令重新确认能在 CPU 上加载；缺失时 rembg 会按它自己的下载地址获取模型。这是依赖准备，会访问模型下载站点，不是付费生图：

```bash
mkdir -p work/models/ai4visualnovel
U2NET_HOME="$PWD/work/models/ai4visualnovel" \
  work/envs/ai4visualnovel/bin/python - <<'PY'
from pathlib import Path
import os
from rembg import new_session

session = new_session('isnet-anime', providers=['CPUExecutionProvider'])
model = Path(os.environ['U2NET_HOME']) / 'isnet-anime.onnx'
assert model.is_file() and model.stat().st_size > 0
print('isnet-anime CPU 会话已加载')
PY
sha256sum work/models/ai4visualnovel/isnet-anime.onnx
```

离线服务器应先在可联网机器上准备同版本 rembg 的模型文件，再放入上述目录并核对哈希。先完成模型下载与加载，再启动多 worker，避免多个原生进程同时下载同一模型。不要关闭 rembg 自带的下载校验。

### 检查 SDL 离屏与中文字体

批量驱动在创建原生子进程前设置 `SDL_VIDEODRIVER=dummy`、`SDL_AUDIODRIVER=dummy`。它仍执行原生 Pygame `GameManager`、`DialogueScene` 和真实选项，只是不打开桌面窗口。因此无需 X11、Wayland、Xvfb 或 WSLg；也不能把离屏截图时间当成真人桌面显示时间。[Pygame dummy video driver](https://www.pygame.org/wiki/DummyVideoDriver)

冻结源码已包含 `SourceHanSansCN-Regular.otf`，属于 35 个被校验的原生文件，随每次源码副本一同复制。原生 `game_engine/ui.py` 优先使用这份本地字体，之后才查 `sourcehansanscn`、`notosanssc` 等系统字体名；完整克隆通常不需另行下载字体。安装器还将该字体复制到运行用户的外部字体目录。

Ubuntu 的 `fonts-noto-cjk` 可能只注册 `Noto Sans CJK SC`，不能只凭该包已安装就判断系统后备字体能匹配。下面按原生优先级检查本地字体和系统字体，并只在 `work/` 保存诊断图：

```bash
mkdir -p work/diagnostics
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \
  work/envs/ai4visualnovel/bin/python - <<'PY'
import pygame
from pathlib import Path

pygame.init()
screen = pygame.display.set_mode((720, 120))
assert pygame.display.get_driver() == 'dummy'
native = Path('systems/AI4VisualNovel')
local_names = ('SourceHanSansCN-Regular.otf', 'font.ttf', 'SimHei.ttf')
names = ('sourcehansanscn', 'notosanssc', 'microsoftyaheiui',
         'microsoftyahei', 'pingfangsc', 'heiti', 'simhei', 'arialunicodems')
path = next((str(native / name) for name in local_names if (native / name).is_file()), None)
if path is None:
    path = next((p for name in names if (p := pygame.font.match_font(name))), None)
assert path, '未匹配原生字体，请先核对源码快照和字体环境'
font = pygame.font.Font(path, 26)
sample = '批量故事：雨夜校园，中文对白。'
assert all(item is not None for item in font.metrics(sample)), '所选字体缺少中文字符'
screen.fill('white')
screen.blit(font.render(sample, True, 'black'), (16, 35))
pygame.image.save(screen, 'work/diagnostics/ai4vn-font.png')
print('原生可匹配的字体：', path)
pygame.quit()
PY
```

若本地字体缺失，先执行 `tools/verify_sources.py` 核对克隆完整性，不要用系统字体掩盖不完整的源码快照。需要额外的系统后备字体时，可把 Google 官方 **Noto Sans SC** 安装到运行用户的字体目录；完整克隆通常可以跳过下面这一步。服务器安装用户与实际执行批次的用户应相同，不要把新字体加入冻结的 `systems/AI4VisualNovel`。[Google Fonts 的 Noto Sans SC 文件及许可证](https://github.com/google/fonts/tree/main/ofl/notosanssc)

```bash
AI4VN_FONT_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/fonts/story-benchmark"
mkdir -p "$AI4VN_FONT_DIR"
curl --fail --location \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf' \
  --output "$AI4VN_FONT_DIR/NotoSansSC.ttf"
curl --fail --location \
  'https://raw.githubusercontent.com/google/fonts/main/ofl/notosanssc/OFL.txt' \
  --output "$AI4VN_FONT_DIR/OFL.txt"
fc-cache -f "$AI4VN_FONT_DIR"
sha256sum "$AI4VN_FONT_DIR/NotoSansSC.ttf"
```

把实际安装的字体哈希、模型哈希和 `pip freeze` 结果与环境准备记录一起保留。字体会影响中文 UI 和换行，比较批次应使用同一套已验证字体；上面的 `main` 下载地址本身不是不可变字体版本。

## 4. 共同配置与密钥

统一配置必须保留三个系统条目；AI4 独立入口只启动 AI4，不会顺带运行另两个项目。确认 `systems.ai4visualnovel` 的字段指向本机 Linux 路径：

| 字段 | 应指向 |
| --- | --- |
| `repo_path` | 当前克隆的 `systems/AI4VisualNovel` |
| `source_lock` | 当前克隆根目录 `baseline-lock.json` |
| `python_executable` | `work/envs/ai4visualnovel/bin/python` 的绝对路径 |
| `rembg_model_dir` | `work/models/ai4visualnovel` 的绝对路径，填写目录而非 `.onnx` 文件 |

路径相对于 **JSON 所在目录** 解析，不能把 `benchmark/configs/` 下的模板原封不动搬到 `work/config/`。配置生成器负责本机路径；手工修改时也须保留公共 `providers`、`bundles` 和其他两个系统条目。

文字、图片及原生视觉审核都在共同 `providers` 配置；供应商地址以 `/v1` 结束。共同图片模型固定为 `gpt-image-2`。三种角色可以使用各自的密钥，但三个项目对同一角色使用同一模型和条件。`providers.vision` 用于原生图片审核，不是八项评测的外部裁判。

在本机编辑 `work/secrets.env`，设置 `BENCH_TEXT_API_KEY`、`BENCH_IMAGE_API_KEY`、`BENCH_VISION_API_KEY`。统一入口 `tools/run_batch.py` 默认读取这个文件；原生项目只收到本地网关的临时凭证，不读取原生目录旧 `.env`。若使用底层 `benchmark/run_ai4visualnovel_batch.py` 而非统一入口，则需要自行导出环境变量：

```bash
chmod 600 work/secrets.env
set -a
source work/secrets.env
set +a
```

文件只包含你确认过的环境变量赋值。不要把密钥写进 JSON、公共故事文件、命令参数或 Git。预检会检查命名变量是否存在，但不会试余额或向供应商发请求。

## 5. 预检、批量运行与恢复

先确认输入 bundle 和源码，再预检 AI4。示例配置采用已有的 `CAMPUS-01-V4` 开发材料；自定义案例的编译与共同字数窗口见 [批量运行说明](../BATCH_RUNNING.md)。

```bash
source work/activate.sh
python3 tools/verify_sources.py
python3 tools/doctor.py --system ai4visualnovel
```

检查器通过后再继续。它不发送真实模型请求；模块可导入不能替代前面的模型加载、字体和 SDL 检查，也不能保证模型输出满足原生结构。

可先在全新目录跑一次本地固定响应测试，检验此 Linux 环境中的原生链与记录。该测试不消费真实模型 API，rembg 的重型分割使用明确的测试替身，因此仍需先完成真实 `isnet-anime` 加载检查：

```bash
python3 tools/smoke_test.py --system ai4visualnovel --out work/smoke-ai4vn
```

测试不通过时保存日志排查，不把已有开发机的通过记录当成这台服务器的验收。

首次验证使用一个全新输出目录：

```bash
python3 tools/run_batch.py --system ai4visualnovel \
  --count 1 --concurrency 1 --out work/results/ai4vn-check-01
```

准备好后自行调整数量和并行数，例如：

```bash
python3 tools/run_batch.py --system ai4visualnovel \
  --count 10 --concurrency 2 --out work/results/ai4vn-batch-01
```

**`count=10` 表示 10 次独立尝试，不保证 10 个成功故事。** 若 `bundles` 有多题，按相同题目顺序循环；不是每题各生成 10 次。`concurrency=2` 表示本程序最多两个根 worker 同时运行，每个根都有独立源码副本、输出目录、时钟和调用编号。原生每个根内部还有自己的调用和图片流程；高并发会增加 CPU、内存与供应商并发需求。先用 `concurrency=1` 确認环境，再增加并发。

一个根运行执行原生 `design → script → render`，随后离屏读取一条实际选择路径。原生可能先为未访问节点生成脚本和图片，这些消耗照实记录。共同窗口从固定开头之后累计；到达窗口停止观察，不强行补结局。原生先结束则记为 `native_end`，不足的字数也保留。

```bash
python3 tools/run_batch.py --system ai4visualnovel \
  --out work/results/ai4vn-batch-01 --resume --concurrency 2
python3 tools/run_batch.py --system ai4visualnovel \
  --out work/results/ai4vn-batch-01 --verify
```

`--resume` 只启动完全没开始的排队任务。已运行、失败、已封存或可能送达的根任务都不会再次发送；失败后若决定再试，要用新的输出目录，并保留旧尝试。不要删除 `states/`、根目录或锁文件来强制重跑。

## 6. 查看产物与八项数据

先读批次 `results.json` 或 `results.csv`，再看对应 `runs/<run_id>/`。程序退出码 0 表示批次操作完成，`state=sealed` 表示证据封存；二者都不保证故事成功。逐根检查 `stop_reason`、`scope_reached`、`native_ended`、`visible_chars` 和 `errors.jsonl`。

| 内容 | 根运行内的文件 |
| --- | --- |
| 共同任务、开头和要求来源（M1） | `inputs/`、`inputs/constraints.json` |
| 实际正文、次序和已执行选择（M1/M2/M5） | `trajectories/main/story.jsonl`、`choices.jsonl`、`trajectories/paths.json` |
| 人物定义、参考图和出场关联（M3） | `characters/versions.jsonl`、`images/assets.jsonl`、`visuals/frame_map.jsonl` |
| 后端与离屏等待时间（M4） | `telemetry/events.jsonl`、`metrics.json` |
| 正文、干净画面、UI 画面和资产关联（M6） | `visuals/frame_map.jsonl` 及其引用文件 |
| 所有生成/审核/重试的原始用量（M7） | `telemetry/calls.jsonl`、`telemetry/raw/` |
| 请求、返回候选、实际资产与派生图（M8） | `images/requests.jsonl`、`outputs.jsonl`、`assets.jsonl` |
| 匿名评审材料 | `evaluation/`；质量分数尚未计算 |
| 原生全过程与失败位置 | `native/ai4vn/source/data/`、`native/ai4vn/observations.jsonl`、`native/ai4vn/full.stdout.log`、`errors.jsonl` |

文件名位于同一列所写目录内，例如上表 `choices.jsonl` 的完整相对路径为 `trajectories/main/choices.jsonl`。正文和原生产物均保留；未选择分支不能当作已阅读的故事。缺失的 usage、实际账单、真人桌面显示时间为未知；五项内容质量分数为 `null`，不会把“资料存在”当成“评测通过”。完整字段见 [八项记录说明](../BATCH_RECORDING.md)。

## 7. 常见失败如何判断

| 现象 | 已知边界与处理 |
| --- | --- |
| `story_graph 节点数不匹配，期望 12` | 原生 `agents/designer_agent.py` 固定校验 12 个节点；模型返回 11 或 13 均会失败。保留失败样本，不由适配器删节点或补剧情。 |
| 很久没有可见正文、已有很多 API 调用 | 原生会先规划、审核并为故事图写脚本；单个 plot 的 Actor/Writer 交互有原生 50 回合安全上限，图像也有原生审核重试。大量调用不等于批量程序重复派发。检查阶段与调用日志。 |
| HTTP 401/429、连接错误 | 分别检查供应商鉴权、限流或网络，保留真实响应和用量缺失；不要把它们一概归为 prompt 或节点数错误。原生重试保留，适配器不会自动重跑失败根。 |
| rembg 导入失败、CUDA 依赖错误 | 检查独立 CPU 环境和 ONNX 包冲突，运行 `pip check` 与上面的 CPU 检查。 |
| 抠图模型下载失败 | 先准备 `isnet-anime.onnx` 并核对 `rembg_model_dir`；仅有 `u2net.onnx` 无效。原生可能保留未成功处理的图像，文件存在不代表抠图成功。 |
| 中文方框、缺字、字体检测失败 | 在实际运行用户下安装原生能匹配的中文字体，重新启动 Python 并查看诊断 PNG；不要修改冻结的 UI 源码来绕过。 |
| 已生成立绘，但 Linux 播放器找不到人物图 | 原生 `Artist` 按原样角色 ID 建目录，播放器却使用 `character_id.lower()` 查找。若 ID 为 `LinChe` 等含大写字母，大小写敏感文件系统上的 `LinChe/` 与 `linche/` 不同。纯中文或全小写 ID 不触发这一差异；当前只按源码确认了风险，未完成 Linux 实机复现。此原生问题保留，不由适配器改 ID、重命名目录或补图。 |
| `No available video device` | 确认使用本批量入口及对应 Linux 环境，检查 SDL dummy 诊断；直接运行原生 GUI `main.py` 不等同批量路径。 |
| 运行完整但没有正文/图片 | 查看 `stop_reason`；设计阶段失败会正确封存空内容。文件封存成功不能改记为故事生成成功。 |

适配器没有总调用、总 token、费用或生成时长预算。运行数量是尝试数量，原生循环、SDK 重试和供应商限制仍存在。AI4 的这些原生结构与高调用行为未修改，也没有承诺每次真实模型调用都成功。

目录大小写的代码位置是 `agents/artist_agent.py:109,121` 与 `game_engine/scenes.py:195`。外置驱动中的资产 `samefile` 检查只关联已经加载的文件，不能恢复播放器此前未找到的立绘。因此默认本地夹具通过，也不能消除含大写角色 ID 的 Linux 风险。

源码接入细节见 [AI4 外置批量驱动说明](../../benchmark/native_shims/ai4vn/BATCH.md)，已完成验证的主机和范围见 [并行复验记录](../PARALLEL_RECHECK_20260907.md)。Ubuntu/WSL2 需要按本页完成本机检查；未执行的 Linux 验证不能引用历史开发机结果代替。
