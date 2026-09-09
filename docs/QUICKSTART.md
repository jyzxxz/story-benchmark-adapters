# 从零安装与第一次运行

接手项目并生产故事，请按 [生成者交接手册](OPERATOR_HANDOFF.md) 操作；本页保留安装和第一次运行的最短路径。旧版默认 `CAMPUS-01-V4` 单题、直接调用 `run_batch.py` 的教程已由当前开放式 30 题统一入口替代。需要自定义 bundle 或跨项目同时启动时再看 [批量进阶说明](BATCH_RUNNING.md)。

## 环境和安装

使用 **Ubuntu 24.04 LTS 普通用户**，可以在安装系统软件包时使用 sudo。Windows 用户先按 [WSL2 安装说明](WINDOWS_WSL2.md) 进入 Ubuntu。IF Line 的临时 PostgreSQL 不允许 root；不要 `sudo bash` 运行整个安装器或生成任务。

仓库当前公开，在 Linux 用户主目录中执行：

```bash
sudo apt-get update
sudo apt-get install -y git
cd ~
git clone https://github.com/jyzxxz/story-benchmark-adapters.git
cd story-benchmark-adapters
git rev-parse HEAD
bash experiment.sh setup
source work/activate.sh
```

记录提交号。`setup` 安装三套独立环境、生成本机配置、打开模型/密钥向导并执行 doctor；安装器需要联网下载软件、浏览器和模型，不调用付费生成模型。各角色填写自己获授权使用的服务地址，确认 `USE` 后输入密钥。共同图片模型为 `gpt-image-2`，其余模型及参数使用双方约定的共同条件。

后续终端回到仓库根目录并 `source work/activate.sh`；这份环境脚本不包含密钥。只改配置或 key 用 `bash experiment.sh configure`。不要 `source work/secrets.env`，也不要在三个项目中分别放密钥。详细字段、配置示例的含义和环境变量优先级见 [交接手册](OPERATOR_HANDOFF.md)。

## 本机文件的位置

| 位置 | 用途 |
|---|---|
| `systems/`、`baseline-lock.json`、`native-patches/` | 冻结项目和公开 IF Line HTML 补丁；不写入依赖、密钥或结果 |
| `work/envs/common/`、`work/envs/if_line/`、`work/envs/ai4visualnovel/` | 相互独立的 Python 环境 |
| `work/node/`、`work/media-tools/`、`work/browser-cache/` | Node、外置浏览器模块和匹配 Chromium |
| `work/infiplot-deps/`、`work/models/` | 原生依赖缓存及抠图模型 |
| `work/config/batch.local.json`、`work/secrets.env` | 当前机器的共同配置和私有密钥 |
| `work/experiments/` | 推荐统一入口输出：每次新实验一个新目录 |

不要复制 Windows/macOS 的虚拟环境、`node_modules` 或旧开发机绝对路径。安装器使用 [独立 Python constraints 与浏览器锁](../tools/linux/README.md)。Ubuntu 24.04 x86_64 的历史环境检查、ARM64/实体 WSL2 的未验收边界见 [历史交接验证](HANDOFF_VALIDATION_20260907.md)，不是新机器自动通过的证明。

## 检查并试跑

在仓库根目录执行：

```bash
python3 tools/doctor.py --system all
bash experiment.sh list
bash experiment.sh quick --allow-pilot --out work/experiments/check-01
bash experiment.sh verify --out work/experiments/check-01
```

`doctor` 不调用生成模型。`quick` 为第一题、每项目一次，共 3 次真实尝试，输入 `RUN` 才付费生成。三个系统顺序运行，失败不会被伪造成成功。`verify` 不发生成请求，应结合 `EXPERIMENT_REPORT.md` 和各项目的 `results.json` 判断范围、证据与原生失败。

可选本地固定响应检查：`python3 tools/smoke_test.py --system all --out work/smoke/check-01`。它不使用付费供应商；检查失败和 skip，测试样本不能参与真实质量评分。安装、配置、预检成功都不保证真实供应商鉴权、额度或原生输出成功。

数量、并发、正式题库、恢复和交付流程见 [交接手册](OPERATOR_HANDOFF.md)。当前默认开放题库共 30 道，仍为 pilot；`run` 不指定次数时会安排 90 次，`full` 会安排 270 次，适配器没有总 token/费用/生成时长上限。

## 查看结果和排错

本地按实验输出 `review-delivery.json` 的顶层 `entry_file` 打开 `打开故事.html`；发给别人则用 `REVIEW_DELIVERY.txt` 指定的 ZIP，完整解压后阅读。原始八项证据继续保留在整个实验目录中。[播放器说明](PLAYBACK_REVIEW.md) · [八项记录](BATCH_RECORDING.md)

环境或原生问题按项目定位：[IF Line](projects/IF_LINE.md)、[AI4VisualNovel](projects/AI4VISUALNOVEL.md)、[InfiPlot](projects/INFIPLOT.md)。AI4 的节点数校验、多轮 API 调用及大小写角色图路径风险属于需保留和检查的原生行为；不要修改冻结源码或删失败结果来通过验收。
