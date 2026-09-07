# 项目使用与维护说明

本项目把一份共同故事任务和固定开头送入三个冻结版本的原生创作流程，并将实际路径、图文、调用和错误保存成同一套证据结构。它是生成与采集工具，不负责替项目修改剧情、补画或执行质量评分。

## 版本与组成

`baseline-lock.json` 保存三个上游提交、逐文件哈希和省略项。IF Line 仅应用 `native-patches/if_line/manifest.json` 声明的 HTML 转换修复；另外两个项目保持冻结源码。运行前后均检查源码身份。不要在 `systems/` 中安装依赖、保存密钥或生成结果，也不要直接 `git pull` 更新某个上游快照。

```text
systems/                  冻结原生源码及原许可证
native-patches/            获准的 IF Line 源码差异
benchmark/source/          原始题库与 recording_spec.v0.1.md
benchmark/cases/           案例源文件
benchmark/examples/        已编译的共同输入示例
benchmark/profiles/        共同要求
benchmark/story_benchmark/ 编译、原生驱动、调度、采集、校验
benchmark/native_shims/    外置启动、观察与环境适配
tools/                    安装、配置、检查、启动包装器
docs/                     交接、运行、数据与历史验证
work/                     本机环境、缓存、配置、密钥和结果；Git 忽略
```

完整上游出处及文件范围见 [来源说明](PROVENANCE.md)。本仓库不是三个上游 Git 历史的镜像。省略的历史凭证、无关书籍和大型旧产物不需要接收者恢复。

## 一次批次的生命周期

1. 准备并复核公共案例，编译一次，共用同一个 bundle；不对三个项目分别改写提示词。
2. 在公共配置中冻结文字、图片、视觉审核模型与供应商，图片模型固定 `gpt-image-2`。模型采样参数与原生格式规则的差异会记录，完整内部消息不要求相同。
3. 预检通过后，按 `count` 和 `concurrency` 建立不可变计划，每个根运行有独立服务、目录和状态。
4. 原生流程生成并沿固定选项序号访问实际路径，达到共同窗口或出现原生结束/失败时停止；内部规划、审核、预取仍计入运行消耗。
5. 导出八项指标证据、检查关联、封存文件哈希；批次汇总成功和失败。
6. 比较输入与条件，再从评审包进行内容评价。评审结果另存，不能回传生成流程。

当前 v4 案例是 4000 个新增可见字符的开发观察窗口，实际句界可能略长。它不是全篇多结局交付，InfiPlot 不因没有全篇结局受罚。窗口不足和原生失败必须保留。其他 29 道原 brief 已保存，但未自动编造并批准固定开头；正式实验应先完成题目复核。

## 使用者要配置的内容

- 安装环境：按 [快速上手](QUICKSTART.md) 执行，三个原生运行环境分开。
- `work/config/batch.local.json`：公共供应商地址、模型、共同 bundle、选项序号和三个原生环境路径。
- `work/secrets.env`：仅保存 `BENCH_TEXT_API_KEY`、`BENCH_IMAGE_API_KEY`、`BENCH_VISION_API_KEY` 的本地值。终端已导出的同名变量优先，换 key 时注意清除旧变量。
- 每次命令：项目名、尝试数、并发数、全新的输出目录。生成预算不设上限；服务商及原生代码限制仍存在。

更换主机或移动仓库后重新执行 `configure_batch.py`，不要沿用另一台机器的绝对路径。脚本拒绝静默覆盖现有配置；确需重建可用 `--force`，旧供应商修改会被默认值替换，密钥文件不会覆盖。要保留自定义配置，请自行备份并迁移共同字段。

## 生产运行与数据交接

运行示例和退出码见 [批量运行](BATCH_RUNNING.md)。先用每项目 `count=1/concurrency=1` 确认接收者的供应商支持，再根据服务器资源与供应商速率调整。三个项目同时各并发 2，合计最多 6 个独立根运行；每个还可能有原生内部并行。没有大规模服务器负载测试数据，不给出未经测量的吞吐保证。

`--resume` 只启动从未开始的队列项，不为凑数重跑失败。升级代码或修改输入后创建新批次，不继续旧版本冻结计划。进程被强杀后应先检查现场与原生服务状态，按运行文档处理遗留锁。

给数据管理员保存整个批次目录，包括 `plan.json`、`scheduling/`、`runs/` 与汇总。给盲审者只提供相应 `evaluation/` 包及其引用的图片，不提供项目名、成本或私有诊断日志。八项所需字段及缺失语义见 [记录对照](BATCH_RECORDING.md)，原始需求在 [保存规范](../benchmark/source/recording_spec.v0.1.md)。M1/M2/M3/M5/M6 未自动评分，未知 token 与费用不当作零。

## 开发检查与更新

```bash
source work/activate.sh
python3 tools/verify_sources.py
python3 tools/doctor.py --system all
python3 tools/smoke_test.py --system all --out work/smoke-release
AI4VN_TEST_PYTHON="$PWD/work/envs/ai4visualnovel/bin/python" \
  PYTHONPATH="$PWD/benchmark" work/envs/if_line/bin/python -m unittest discover -s benchmark/tests
```

`smoke_test.py` 使用本地固定响应验证真实原生链路，未证明供应商模型可用或故事质量。全套测试中依赖 opt-in 的项目会跳过，必须区分跳过与通过；各系统真实 smoke 独立执行。新增代码需要确认原生文件校验仍通过、相同任务没有多次注入、八项证据未失联。不要修改封存历史运行来迁就新 schema。

发布时提交代码、文档、来源锁、示例与外置测试；排除 `work/`、密钥、数据库、依赖缓存和私人真实调用档案。交接安装器使用 `tools/linux/*py312.constraints.txt` 固定此次 Ubuntu 24.04 / Python 3.12 的 Python 解析版本，并用 `renderer-package-lock.json` 安装播放器依赖；InfiPlot 使用原生 pnpm 锁。实际版本另保存在 `work/installation/*freeze.txt`，随实验条件存档。系统包的安全更新仍由 Ubuntu 软件源提供；不能因此假定不同主机的硬件、内核和所有系统库完全相同。

## GitHub 交给其他人

仓库地址是 [jyzxxz/story-benchmark-adapters](https://github.com/jyzxxz/story-benchmark-adapters)。本次保持原有私有可见性；接收者需要先取得仓库读取权限，再通过自己的 GitHub 身份 clone。项目所有者可以在 GitHub Settings → Collaborators 中授予访问权限。不要通过共享生成 API key 来替代 GitHub 访问授权。

建议把仓库链接、使用的提交号、[快速上手](QUICKSTART.md) 和 [交接验证](HANDOFF_VALIDATION_20260907.md) 一起给接收者。接收者的 API 供应商、额度和机器性能由其实际环境决定；历史本机结果只证明当时记录的条件。
