# 三系统共同输入适配器

同一份故事设定、固定开头和续写要求，原样交给 IF Line、AI4VisualNovel、InfiPlot，由三个项目各自的原生流程处理。

本仓库采用**未修改的基线源码 + 独立外置适配器**。`systems/` 中发布的每个文件都与冻结提交逐字节相同；接入规则、记录器、预算与启动包装全部位于 `benchmark/`。文件不改不等于运行时完全不包装：必要的输入渲染与预算适配均公开记录。

当前范围为公共输入、原生首次输出、选项与候选预览、请求和用量证据。没有实现完整分支播放器、AA/AB/BA/BB 探索、AI 评分或论文统计。[澄清提示词后的最新重测](docs/CLARIFIED_RETEST.md) 中，IF Line 已从固定开头生成两个原生分支预览，但缺少独立选项标题；AI4VisualNovel 仍在原生设计阶段失败；InfiPlot 返回正文和两个选项，但 B 选项不符合澄清后的行动要求。**三者均未完整通过共同输出要求。** [上一轮结果](docs/LIVE_VALIDATION.md) 和 [初始工程验收](docs/ACCEPTANCE.md) 保留。

## 目录

```text
benchmark/         编译器、接入器、外置启动器、审计、导出、测试
systems/           三套冻结基线源码
baseline-lock.json 发布文件哈希、原始提交及省略文件清单
docs/              公平性、运行说明、来源与验收证据
tools/             仓库完整性与干净副本验证
```

## 快速检查

公共工具不需要模型密钥，Python 3.10+：

```bash
cd benchmark
python3 -m story_benchmark compile --case cases/CAMPUS-01.json --out ../work/CAMPUS-01 --allow-pilot
python3 -m story_benchmark verify --bundle ../work/CAMPUS-01
python3 -m unittest discover -s tests -p test_core.py
python3 -m unittest discover -s tests -p test_provenance.py
cd ..
python3 tools/verify_sources.py
```

编译输出明确为 `payload_check=passed, native_integration=not_run`。这不能代替原生接入测试。

## 公共输入

最新开发案例为 `benchmark/cases/CAMPUS-01-C1.json`，澄清行动先后、角色知识、审核范围，并明确导出到第一次选择为止。原始 brief 和固定开头逐字保留；旧 `CAMPUS-01.json` 未覆盖。只在编译时统一一次 UTF-8、LF 与外围空白，生成唯一 `shared_task.txt`。IF Line 使用 `extra_requirements`，AI4VisualNovel 使用需求文件，InfiPlot 使用 `worldSetting`；里面的公共字符串完全相同。

开发任务保留人物、固定事实、两组关键选择和续写要求；不叠加六场景、两个结局或路径总字数的硬要求。原始 v1 题库与 v2 前缀保存在 `benchmark/source/`，没有覆盖。

目前公共开头及时间解释仍是 `pilot` 候选。使用 `--allow-pilot` 只能用于明确的开发任务；正式运行需要与内容哈希绑定的确认记录。没有为其他 29 题编造开头。

## 运行与配置

三个项目分别使用各自环境，具体安装和启动命令见 [运行说明](docs/RUNNING.md)。`benchmark/configs/experiment.example.json` 将模型、供应商地址和预算放在共同配置中，拒绝某个系统单独覆盖这些条件。模板默认不启用真实生成，未选择模型或未设置预算会在发送模型请求前拒绝。

真实生成只通过 `run-first` 执行，失败产物和投递不确定状态均保留；`resume-export` 只恢复已保存产物的导出，不自动重发生成。各接入器原生幂等与恢复边界见运行说明。

共享的是外部任务，不要求三个系统的全部 system/user/history 消息相同，也不要求生成同一篇故事。新 IF Line 模式通过原生人工修订接口一次性导入公共开头，补建空状态检查点后调用原生候选分支接口；只做公共字段映射，不提炼剧情记忆、不重写输出。其接入方式与原生默认首章生成不同，必须随版本披露。详见 [公平性边界](docs/FAIRNESS.md)。

## 上游与文件范围

| 项目 | 冻结提交 |
|---|---|
| IF Line | `572407fce9b648a4206ac37da6a9f6ed22631da8` |
| AI4VisualNovel | `0faf120244d175866eea3813f053281f5689ab19` |
| InfiPlot | `a60e18bc663caaa134d9323a2b89159b7cc9bd05` |

这是源码快照仓库，不复制三个项目的 Git 历史。IF Line 既有大型生成素材、无关参考书和历史凭证材料未重新分发；每个省略项及原因列在 `baseline-lock.json`。保留的源码没有替换、修正或格式化。完整上游位置和许可证说明见 [来源说明](docs/PROVENANCE.md)。
