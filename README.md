# 三系统共同输入适配器

同一份故事设定、固定开头和续写要求，原样交给 IF Line、AI4VisualNovel、InfiPlot，由三个项目各自的原生流程处理。

本仓库采用**冻结基线 + 独立外置适配器**。2026-09-07 起，按用户明确授权，IF Line 增加单独披露的 HTML 正文到脚本转换修复；AI4VisualNovel 和 InfiPlot 原生源码仍与冻结提交逐字节相同。原始 `baseline-lock.json` 保留，IF Line 的源码差异另以补丁和逐文件哈希锁定，详见 [IF Line 源码修复](docs/IFLINE_HTML_PATCH.md)。接入规则、记录器、预算与启动包装位于 `benchmark/`，运行期间禁止再改源文件。

当前新增 **v4 图文批量程序**：同一份公共任务和开头，三个独立入口可调整生成数与并发数，沿原生实际选择路径读取至共同窗口，保留八项评审所需证据。示例窗口 4000 字符仍是开发候选；不要求全篇结局，因此 InfiPlot 可以按相同片段范围参与。使用方法见 [批量运行说明](docs/BATCH_RUNNING.md)，保存字段见 [八项指标对照](docs/BATCH_RECORDING.md)，当前验证范围见 [并行复验记录](docs/PARALLEL_RECHECK_20260907.md)。补丁前的 [历史批量验收记录](docs/BATCH_VALIDATION.md) 单独保留。

历史 **v3 公共输入与统一返回合同** 保留：返回到第一次尚未选择的原生选项。允许开头后直接显示选项；正文、选项、选项自带的未来预览分栏保存，故事文字可以不同。v3 三个系统的成功与失败仍使用同一个 `result.json` 结构，原生错误如实保留。

v3 接入边界见 [v3 合同](docs/V3_CONTRACT.md)，无预算模式重测见 [无限模式验证](docs/UNLIMITED_VALIDATION.md)；之前有预算上限的 [v3 验证](docs/V3_VALIDATION.md) 保留。v4 当前每个根运行采集一条实际路径，不做 AA/AB/BA/BB 全分支探索、AI 自动评分或论文统计。历史 [C1 重测](docs/CLARIFIED_RETEST.md)、[首轮实测](docs/LIVE_VALIDATION.md) 和 [初始工程验收](docs/ACCEPTANCE.md) 保留，不能代替 v4 图文接入证据。

最新检查：[适配器与三个批量程序整体复查](docs/ADAPTER_AUDIT_20260907.md)。

## 目录

```text
benchmark/         编译器、接入器、外置启动器、审计、导出、测试
systems/           原生源码快照，IF Line 含已披露的 HTML 修复
baseline-lock.json 发布文件哈希、原始提交及省略文件清单
native-patches/    IF Line 授权源码补丁及独立校验清单
docs/              公平性、运行说明、来源与验收证据
tools/             仓库完整性与干净副本验证
```

## 快速检查

公共工具不需要模型密钥，Python 3.10+：

```bash
cd benchmark
python3 -m story_benchmark compile --case cases/CAMPUS-01-V3.json --out ../work/CAMPUS-01-V3 --allow-pilot
python3 -m story_benchmark verify --bundle ../work/CAMPUS-01-V3
python3 -m unittest discover -s tests -p test_core.py
python3 -m unittest discover -s tests -p test_provenance.py
cd ..
python3 tools/verify_sources.py
```

编译输出明确为 `payload_check=passed, native_integration=not_run`。这不能代替原生接入测试。

## 公共输入

图文开发案例为 `benchmark/cases/CAMPUS-01-V4.json`；历史文本案例 `CAMPUS-01-V3.json` 保留。它们明确角色总名单含玩家、行动先后和角色知识。原始 brief 和固定开头逐字保留；旧案例未覆盖。只在编译时统一一次 UTF-8、LF 与外围空白，生成唯一 `shared_task.txt`。IF Line 使用 `extra_requirements`，AI4VisualNovel 使用需求文件，InfiPlot 使用 `worldSetting`；里面的公共字符串完全相同。

开发任务保留人物、固定事实、两组关键选择和续写要求；不叠加六场景、两个结局或路径总字数的硬要求。原始 v1 题库与 v2 前缀保存在 `benchmark/source/`，没有覆盖。

目前公共开头及时间解释仍是 `pilot` 候选。使用 `--allow-pilot` 只能用于明确的开发任务；正式运行需要与内容哈希绑定的确认记录。没有为其他 29 题编造开头。

## 运行与配置

三个项目分别使用各自环境，具体安装和启动命令见 [运行说明](docs/RUNNING.md)。`benchmark/configs/experiment.v3.example.json` 将模型、供应商地址和预算放在共同配置中，拒绝某个系统单独覆盖这些条件。模板默认采用 `budget_mode=unlimited`：适配器不限制总生成时间、HTTP 调用次数、输入长度或单次输出 token；原生项目和模型服务的限制仍保留。模板不启用真实生成，未选择模型会在发送前拒绝。

真实生成可通过单系统 `run-first` 或三系统 `run-set` 执行。v3 的 `resume-export` 核验并返回已封存的统一结果，包括原生失败；不自动重发生成。各接入器原生幂等与恢复边界见运行说明。

共享的是外部任务，不要求三个系统的全部 system/user/history 消息相同，也不要求生成同一篇故事。新 IF Line 模式通过原生人工修订接口一次性导入公共开头，补建空状态检查点后调用原生候选分支接口；只做公共字段映射，不提炼剧情记忆、不重写输出。其接入方式与原生默认首章生成不同，必须随版本披露。详见 [公平性边界](docs/FAIRNESS.md)。

## 上游与文件范围

| 项目 | 冻结提交 |
|---|---|
| IF Line | `572407fce9b648a4206ac37da6a9f6ed22631da8` + 已披露 HTML 修复 |
| AI4VisualNovel | `0faf120244d175866eea3813f053281f5689ab19` |
| InfiPlot | `a60e18bc663caaa134d9323a2b89159b7cc9bd05` |

这是源码快照仓库，不复制三个项目的 Git 历史。IF Line 既有大型生成素材、无关参考书和历史凭证材料未重新分发；每个省略项及原因列在 `baseline-lock.json`。IF Line 新增源码修复独立记录，历史未修改基线的实测结果继续保留。完整上游位置和许可证说明见 [来源说明](docs/PROVENANCE.md)。
