# 开放关键行动版本的验证范围

日期：2026-09-07。开始基于 `67a4b147376bd5e2b58519972d767b8cca7fe1ff`，提交前整合并行更新 `787a8793b8dd360c003fbe2e71539132604eab3f`，保留该版本已有的任意总次数、题材筛选、并发与根目录入口。

## 本次实际执行

| 检查 | 结果 |
|---|---|
| 原有实验与数量控制测试 | 42项通过，保持当前main中的版本 |
| 已发布第一版开放测试 | 28项通过，明确固定到第一版导出函数 |
| 新增开放行动回归测试 | 31项通过 |
| `bash experiment.sh test` 总结果 | 101项，101通过、0失败、0跳过 |
| 新版完整输入编译与verify | 30/30通过，逐题公共哈希已固定 |
| IF Line、AI4VisualNovel、InfiPlot公共payload一致性 | 新旧题库均通过 |
| 第一版开放30题与原编译器render对照 | 30/30公共字符串不变 |
| 旧30题在扩展编译器下重新编译 | 30/30与原公共输入SHA-256相同 |
| 新增与修改Python语法检查、Shell `bash -n` | 通过 |

31项新测试包括：真实空预设行动清单、原有题材/名单/玩家保持、开头非行动部分保留（SCI-FI-03首段菜单句修订单列）、行动/章绑定残留检查、公共输入哈希与三payload相等、源文件和bundle篡改拒绝、严格限定v4开放政策、缺省政策保持旧校验、pilot拒绝正式编译、人工确认与审批哈希、open-approved版本保留、默认新题库、显式旧题库、拒绝混合行动政策、任意次数与并发参数保留、代码冻结及免费prepare。

机器可读记录见 [validation.json](evidence/open-actions/validation.json)。此文件只报告实际测试与已验证的输入哈希，不包含故事质量分数或虚构API用量。测试中的`test-fixture-reviewer`只用于临时目录里的审批协议测试，没有发布为真实内容审批记录。

## 环境与证据边界

测试在本次会话的Linux容器完成。Git命令无法从容器解析GitHub，因此不是完整克隆、不是全仓库安装验收。需要的源码通过GitHub连接器读取，按返回的Git blob逐字节核对后在本地重建；原始题库结合此前提供的ZIP核对。未改动的IO、合同、配置向导、第一版开放模块与数量测试的Git blob见机器记录。

编译器与实验模块的基准分别为 `84ccdb9e46774ecc9155fc0a80aca28a5a0d00ca` 和 `d18dbcf96ee27a7aa9f03a8bdb5effcf2b3ce972`。新版本保留并行提交的合同/渲染器，仅为清单补充行动政策一致性校验，并将后者默认输入切到独立第二版导出器；没有用另写的模拟编译器冒充仓库编译器。

**本次没有重新安装三套环境，没有运行新的PostgreSQL/Redis/Pygame/Next原生端到端流程，没有调用真实付费模型，也没有执行外部质量评分。** 实验调度单元测试中的原生命令是明确mock。101项通过不能证明新提示词一定生成成功、窗口一定达标、图片一定充分或供应商usage永不缺失。原生能力与旧故障仍以对应历史验证文档和接收者真实试跑为准。

不修改`systems/`、`baseline-lock.json`、已披露的IF Line源码补丁、原生生成/选择代码或八维记录器。旧题库的实际文本和预期公共哈希保留；新题库没有占位C1/C2，既有记录器从空`decisions`生成空预设选择约束，同时继续保存实际原生菜单。

## 复验

```bash
bash experiment.sh test
bash experiment.sh preview --out work/open-recheck
bash experiment.sh preview-legacy --out work/legacy-recheck
```

以上均不调用模型。准备好Ubuntu/WSL2及自己的供应商后，使用 `bash experiment.sh quick --allow-pilot` 验证真实生成，再扩大规模。修改了代码与题目后不要恢复旧冻结实验，新建独立输出目录。参阅 [完整使用说明](OPEN_ACTION_EXPERIMENTS.md)。

并行更新中新增的 `contracts.native_decisions`、`open_actions.py`、第一版规则与前缀原样保留。第一版的28项测试仅将测试输入明确固定为该版本（两行调整），第二版默认及兼容CLI由新测试覆盖。原始指定行动、第一版开放、第二版开放三套输入都可独立导出；没有用本地旧快照覆盖并行更新。
