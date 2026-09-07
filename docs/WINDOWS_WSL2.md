# Windows：在 WSL2 Ubuntu 中运行

当前三个批量程序按 Linux/POSIX 进程、信号、服务和路径组织。Windows 用户使用 **WSL2 + Ubuntu 24.04 LTS**；不直接使用 Windows Python、Node、PostgreSQL 或 PowerShell 执行生成程序。Windows 安装 WSL 后，故事生成、模型缓存、图片与记录全部在 Linux 环境中运行。

本页描述安装步骤，不表示当前 Windows 机器已经通过验收。安装后仍需按 [快速上手](QUICKSTART.md) 完成本地检查，再保存自己的真实生成记录。

## 1. 安装并确认 WSL2

打开**管理员 PowerShell**：

```powershell
wsl --install -d Ubuntu-24.04
```

按系统提示重启。第一次打开 Ubuntu-24.04 时创建普通 Linux 用户及密码；密码用于安装软件时的 sudo。Microsoft 的自动安装命令要求 Windows 10 2004 / Build 19041 或更高版本，或者 Windows 11；更旧系统按其手动安装指南处理。[Microsoft WSL 安装说明](https://learn.microsoft.com/en-us/windows/wsl/install)

回到 PowerShell 检查：

```powershell
wsl --list --verbose
```

Ubuntu-24.04 的 VERSION 必须为 `2`。如果已有该发行版且 VERSION 是 `1`：

```powershell
wsl --set-version Ubuntu-24.04 2
```

进入 Ubuntu：

```powershell
wsl -d Ubuntu-24.04
```

后面的 `bash`、`python3`、`git` 命令均在**这个 Ubuntu 终端**执行。发行版名称可通过 `wsl --list --online` 核对；Ubuntu 官方也提供 [Ubuntu 24.04 的 WSL 安装步骤](https://documentation.ubuntu.com/wsl/latest/tutorials/develop-with-ubuntu-wsl/)。

## 2. 把仓库放在 Linux 文件系统

使用 Linux 用户主目录，例如 `~/story-benchmark-adapters`：

```bash
cd ~
pwd
id -u
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
```

`pwd` 应类似 `/home/你的Linux用户名`；`id -u` 不应为 `0`。私有仓库须先取得 GitHub 访问权限并完成自己的 Git 认证。不要把 GitHub token 拼进仓库 URL，也不要使用别人的 API 密钥代替 GitHub 授权。

不要从 `/mnt/c/Users/...` 中直接运行全部数据库和依赖，也不要复制 Windows 的虚拟环境或 `node_modules`。Microsoft 建议 Linux 工具处理的项目放在 WSL 文件系统中，以避免跨系统文件访问的性能损失；Linux 的大小写与权限行为也不同。[WSL 文件系统说明](https://learn.microsoft.com/en-us/windows/wsl/filesystems)

## 3. 按共同流程安装

在仓库根目录中运行：

```bash
bash tools/bootstrap_linux.sh --system all
source work/activate.sh
python3 tools/configure_batch.py
python3 tools/doctor.py --system all
```

只运行一个项目时，把 `all` 改为对应的 `if_line`、`ai4visualnovel` 或 `infiplot`。系统包安装可请求 sudo；不要 `sudo bash` 运行整个安装器，也不要用 root 运行生成任务。`--skip-system-deps` 仅适用于管理员已安装好所有所需系统软件包的情况。

配置文件已存在时生成器拒绝覆盖；修改现有配置，或确认需要重建时显式使用 `--force`。已有密钥文件不会被覆盖。编辑 `work/config/batch.local.json`，只在共同 `providers` 内配置文字、图片和视觉模型；共同图片模型为 `gpt-image-2`。密钥只放在 `work/secrets.env`，`tools/run_batch.py` 默认安全读取它，无需也不应把密钥文件作为 shell 脚本执行。完整配置、成本、试跑与恢复规则见 [快速上手](QUICKSTART.md)，不要在三个项目里分别填写三套提示词和模型条件。

安装器使用 [Linux 交接环境锁](../tools/linux/README.md)：三个 Python 环境各自使用 Ubuntu 24.04 / Python 3.12 constraints，公共外置浏览器工具使用 npm 锁和 `npm ci`，InfiPlot 原生继续使用冻结 pnpm 锁。锁来自本次 Linux x86_64 实际安装，不表示在实体 Windows/WSL2 或 ARM64 上已完成验收。WSL 应在自己的 Linux 环境中安装这些版本，不能复制其他平台的二进制依赖。当前结果与待验收项见 [交接验证记录](HANDOFF_VALIDATION_20260907.md)。

每次重新进入 Ubuntu 终端，执行：

```bash
cd ~/story-benchmark-adapters
source work/activate.sh
command -v python3
command -v node
```

三个项目全部安装时，应看到本仓库 `work/envs/common/bin/python3` 与 `work/node/bin/node`，而不是 `/mnt/c/.../python.exe` 或 `node.exe`。当前安装器始终安装公共 Node/Playwright，即使只选择 AI4VisualNovel；这不表示 AI4 原生流程使用了 Node。旧开发机的 `/Users/...`、Homebrew、Codex 私有缓存路径对 WSL 无效；本地配置须由当前仓库位置生成。

## 4. 验收与运行

先运行 localhost 夹具，输出目录必须全新：

```bash
python3 tools/smoke_test.py --system if_line --out work/smoke-ifline
python3 tools/smoke_test.py --system ai4visualnovel --out work/smoke-ai4vn
python3 tools/smoke_test.py --system infiplot --out work/smoke-infi
```

检查失败和 skip；通过只表示对应本地检查成立，不能代替真实供应商验收。AI4VisualNovel 的原生 Artist 按原角色 ID 建目录，播放器按小写 ID 找立绘；ID 含大写时，在 Linux 上可能出现已生成图片却未加载的情况。该原生源码未修，须在新机器验收中核对，详见 [项目说明](projects/AI4VISUALNOVEL.md)。不要为了隐藏路径问题把整个实验迁到大小写不敏感的磁盘，也不要改冻结来源锁。

确认配置与本地检查后，下面的例子使用真实模型：

```bash
python3 tools/run_batch.py --system infiplot --count 1 --concurrency 1 --out work/results/wsl-infi-check
```

另外两个项目只需换 `--system` 和独立输出目录。批量数量是尝试数；三个项目在三个终端运行时，并发数会相加。4000 字符是默认共同观察范围，适配器未设费用或总调用预算；原生规划、审核、失败重试与未选预取消耗均保存。

IF Line / InfiPlot 使用无界面 Chromium；AI4 使用原生 Pygame 的 dummy 驱动。当前批量采集不要求 WSLg、桌面或 Xvfb，但仍需要浏览器/SDL 系统共享库及字体。不能把无界面截图时间称为真人桌面显示时间。

## 5. 查看结果与保留现场

生成完成后，在 Ubuntu 中进入结果目录并用 Windows 资源管理器查看：

```bash
cd ~/story-benchmark-adapters/work/results
explorer.exe .
```

这只是打开结果文件，不是用 Windows 程序启动运行管线。也可从资源管理器访问 `\\wsl$` 下的对应 Ubuntu 文件系统。[Microsoft 文件访问说明](https://learn.microsoft.com/en-us/windows/wsl/filesystems)

保留每根完整证据目录，包含公共输入、正文、选择、图像、画面映射、usage、停止原因和 manifest；不要只复制最终图片。`work/secrets.env` 不属于可分享结果。不要编辑运行中的文件，也不要在任务未完成时执行 `wsl --shutdown`、强制结束虚拟机或移除锁文件；Ctrl+C 会先停止派发并收集已发送请求的终态。恢复不会补跑已失败的故事，具体命令见快速上手。

## 常见环境问题

| 现象 | 处理方向 |
|---|---|
| 安装 WSL 卡住 / 虚拟化不可用 | 按 Microsoft 安装与故障说明检查 Windows 功能、重启及虚拟化条件；先让 Ubuntu 正常启动。 |
| `Repository not found` / GitHub 403 | 核对私有仓库授权与 Git 认证，不要改下载地址或把 token 写进 URL。 |
| `initdb cannot be run as root` | 退出 root 会话，使用创建的普通 Linux 用户和其可写工作目录。 |
| Node/Python 指向 `.exe` 或 `/mnt/c` | 进入 Ubuntu 并重新 `source work/activate.sh`，不要调用 Windows 工具链。 |
| 数据库/依赖安装很慢、权限异常 | 确认仓库与 work 均在 `/home/...` 下，未混用 Windows 环境或文件权限。 |
| Chromium/SDL 缺共享库或中文方框 | 重跑对应项目的依赖/字体检查；不要仅因 headless 就省略系统库。 |
| 字体或模型下载失败、401/429 | 分别检查 Linux 网络、证书、供应商端点和密钥；这类错误不能统一归为 prompt 问题。 |

各项目的具体依赖和原生失败边界：[IF Line](projects/IF_LINE.md) · [AI4VisualNovel](projects/AI4VISUALNOVEL.md) · [InfiPlot](projects/INFIPLOT.md)。
