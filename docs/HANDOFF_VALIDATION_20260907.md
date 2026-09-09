# Linux / Windows WSL2 交接检查（2026-09-07）

> **验证快照：2026-09-07 的 Linux 交接轮，发布提交 `3421000`。**这是历史安装与本地原生夹具证据，早于 30 题实验入口和离线评阅包；并非接收者当前环境或后续 `main` 的重新验收。当前步骤统一维护在[操作者交接说明](OPERATOR_HANDOFF.md)和[快速上手](QUICKSTART.md)。

本次在干净 **Ubuntu 24.04、x86_64、Python 3.12** 容器中完成从源码副本安装与本地原生流程验证。容器运行在 macOS 的 Docker 虚拟机上，用户空间执行 Linux x86_64 程序；不是实体 Windows、WSL2 或生产服务器实测。此前 macOS 的真实模型结果仍单独保存在历史报告，不能互相替代。

交接包已经包含三项目必需源码、外置适配器、三个批量入口、安装/配置/诊断/启动工具、依赖锁和使用说明。本次没有更改 AI4VisualNovel、InfiPlot 或 IF Line 的业务源码；IF Line 继续使用此前已披露的 HTML 修复变体。没有调用真实付费模型。

## 实际完成的检查

| 检查 | 结果 | 能证明的范围 |
|---|---|---|
| 干净 Linux 安装 | 通过 | 普通用户安装三个独立环境、Node 22.12.0/pnpm 9.12.0、匹配 Chromium、真实 u2net/isnet-anime 模型；未复制 macOS 虚拟环境或私人缓存 |
| 最终默认安装器复验 | 通过 | 实际执行含 sudo 系统包与 Playwright 系统依赖的 `bootstrap_linux.sh --system all`；外置 Python constraints 与 npm 锁生效 |
| InfiPlot 新目录离线依赖安装 | 通过 | 从相同用户的预热 pnpm store 向空目录安装冻结依赖；不宣称原生 Google 字体加载完全离线 |
| doctor | 3/3 项目通过 | 共 29 项项目检查，另有公共 Python 与 bundle 校验；包含真正启动 Chromium、CPU ONNX 加载、Pygame 中文字体渲染和源码身份 |
| IF Line 原生图文 fixture | 1/1，0跳过 | 20个可见字符、3对净图/UI图、2次真实原生选择；PostgreSQL、Redis、worker 与播放器真实运行 |
| AI4VisualNovel 原生图文 fixture | 1/1，0跳过 | 96个可见字符、7对画面、1次真实选择；原生设计、脚本和 Pygame 运行，抠图在此测试中是明确替身 |
| InfiPlot 原生图文 fixture | 1/1，0跳过 | 74个可见字符、4对画面、1次已执行选择，另保留1次未执行选择；原生 Next/React、预取及图像编辑记录 |
| AI4VisualNovel 双 worker 封存 | 1/1，0跳过 | count=2/concurrency=2，两个原生进程实际重叠；各自调用、时钟、路径、封存校验独立；resume新增调用0 |
| 三个公共包装入口 | 预检和 plan-only 均通过 | 系统 Python 自动切到 common venv；各生成2个排队项，未发起远程请求 |
| 现有回归组 | 297项中289通过、8跳过、0失败 | 正确选择既有原生 Python 环境后运行；此回归组在原开发机执行，不冒充 Linux 全套结果 |

上述 fixture 使用固定响应和工程图片，观察范围不同，用于检验接入与记录，**不是三系统公平质量实验样本**。AI4 的真实 CPU 分割模型已由安装器及 doctor 分别加载；其 fixture 替身仍不等于真实模型抠图质量检查。截图已检查中文可见，无方块缺字。

本次 Linux 的三项单根 smoke 不等于三个项目都做过新的 Linux 双任务负载测试。三个项目双任务运行的此前证据见 [整体复查](ADAPTER_AUDIT_20260907.md)；此次额外在 Linux 验证了共用调度、封存与恢复链路中的 AI4 双 worker。没有几十/上百并发或生产吞吐数据。

机器可读摘要、JUnit 和诊断报告见 [交接证据目录](evidence/handoff-20260907/)。大体积 fixture 原生现场保留在发布者本机交接档案中，不作为安装依赖。

## 输入、返回和八项数据

本次没有改共同任务、固定开头、窗口或原生创作方法。v4 live 配置仍对三者使用同一份公共任务与4000字符开发观察窗口，故事文字允许不同。三个 live plan-only 使用同一 bundle；首次创作接收审计仍由每次真实根运行执行，不能只用源文件哈希代替。

默认 bundle 公共文本 SHA256：`d2fb88452de72a51743f18cae9b62ef1e21a70c4f28d74a8a7ba1a0f2f3e5ce5`，2421字符。开头独立保存。本文 fixture 字数是工程检查输出，不能拿来判断哪个项目续写更充分。

八项要求原件仍为 [recording_spec.v0.1.md](../benchmark/source/recording_spec.v0.1.md)，对应 [记录对照](BATCH_RECORDING.md)。Linux smoke 验证正文、画面、选择和调用关联；额外的双 worker 批次验证实际证据封存与重算。M1/M2/M3/M5/M6 仍待独立质量评审；M4实际桌面显示时间未测，M7缺失用量不填零，费用无账单时不估造。

## 接收者操作入口（已更新）

1. 从公开的 [GitHub 仓库](https://github.com/jyzxxz/story-benchmark-adapters) 获取代码，在 Ubuntu 24.04 / Windows WSL2 中按[操作者交接说明](OPERATOR_HANDOFF.md)安装；不再需要私有仓库邀请。
2. 配置自己的公共文字、图片、视觉服务地址与密钥；本次安装检查没有验证接收者的供应商鉴权、余额或模型兼容性。
3. 先各运行一次真实模型，保留整个批次目录，再根据机器和供应商容量调整数量与并发。数量是尝试数，无适配器费用上限。
4. 仓库后续已收录 30 题及对应固定开头；默认是独立开放行动版本，仍须按[开放行动实验说明](OPEN_ACTION_EXPERIMENTS.md)区分 `pilot` 调试与正式内容批准。本页单题安装检查不能替代这 30 题的内容复核或真实运行。

AI4VisualNovel 原生节点数校验、内部循环失败，以及 Linux 大小写敏感路径风险仍保留，详见 [AI4说明](projects/AI4VISUALNOVEL.md)。它们不能通过“安装通过”变成成功保证，也不能由适配器改写故事或替换图片来隐藏。默认 InfiPlot 的身份与页面兼容接入仍按原有规则披露，不代表生产 Supabase 账号已经验证。
