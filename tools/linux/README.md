# Linux 交接环境锁

开始安装、配置和批量实验请读 [生成者交接手册](../../docs/OPERATOR_HANDOFF.md)；本页解释依赖锁，不是另一套运行入口。

这些 Python constraints 来自 2026-09-07 干净 Ubuntu 24.04 x86_64、Python 3.12 环境的实际安装记录。安装器仍读取每个项目的原始依赖清单，再用 constraints 固定解析版本；它们不是新的原生源码，也不含本地路径或密钥。common/IF/AI4 环境分开，不能混用锁。

`renderer-package-lock.json` 对应外置 Vue/Playwright 播放器，安装器使用 `npm ci`。InfiPlot 继续使用 `systems/infiplot/pnpm-lock.yaml`，并在工作目录中预热和检查空目录离线安装。

Node 22.12.0 下载按官方 SHA256 清单检查；rembg 模型由对应固定版本的库按原生模型名下载并校验。模型文件、Python wheels、浏览器和 node_modules 不提交到 GitHub，安装需要访问软件源。实际模型 SHA256 和加载结果保存在 doctor 报告中。

Ubuntu 系统包由 Ubuntu 24.04 软件源安装，完整实际版本应随每次实验存档。ARM64 安装分支按同一逻辑提供，但历史目标验证为 x86_64；Windows 通过 WSL2 使用 Linux 环境，未在实体 Windows 主机运行那次验证。具体范围见 [历史交接检查](../../docs/HANDOFF_VALIDATION_20260907.md)，不能当成每台新机器的验收结果。
