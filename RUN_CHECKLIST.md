# 每轮生成与实验检查清单

首次接手项目，请完整阅读 [操作者交接手册](docs/OPERATOR_HANDOFF.md)。本页只用于每轮执行前后核对；参数和审批参考见 [实验协议](docs/OPEN_ACTION_EXPERIMENTS.md)，不要把历史验证报告中的开发命令当作当前生产入口。

## 开始前

- [ ] 记录当前提交号；本次仓库和运行目录没有正在执行的旧任务。正在运行或将恢复旧实验时，不拉取新代码、不移动目录。
- [ ] 在 Ubuntu 24.04 / WSL2 Ubuntu 24.04 的普通用户环境完成安装、`doctor` 和本机小规模验证。生成目录位于 Linux 文件系统中。
- [ ] 公共配置中的文字、图片、原生视觉审核供应商与模型已经确认，图片模型统一为 `gpt-image-2`；密钥在本机 `work/secrets.env` 或命名环境变量，不在题目或 Git 中。检查旧 export 不会覆盖新密钥。
- [ ] 确认本轮题库版本、题目范围、`count` 或 `repeat`、选择下标政策、并发和输出目录。默认当前 30 题为 `v4-30-open-actions-pilot.2`，全部有固定开头，但仍是 pilot。
- [ ] 清楚本轮是尝试数，失败不自动补跑；没有适配器总费用/token/时长上限，原生规划、图片审核及重试计入消耗。

## 输入复核与免费检查

```bash
# 新目录；不需要密钥，不调用模型
bash experiment.sh preview --out work/open-eval30-review
python3 tools/audit_open_inputs.py \
  --suite-root work/open-eval30-review \
  --out work/reports/open-input-audit.json
```

- [ ] 阅读导出的 `PROMPTS_30_COMPILED.md`，确认固定事实、已发生开头及 4000 字符观察范围。每次只送一题；不将 30 题、变更说明或额外剧情提示拼给某个系统。
- [ ] 检查审计报告。`ok=true` 只说明已实现的结构、哈希和旧行动措辞检查通过，不能代替人工语义审核、原生环境验收或供应商可用性检查。
- [ ] 正式实验使用实际人员审核形成的 approved 副本及 `--suite-root`；候选试跑明确使用 `--allow-pilot`。完整审批命令在交接手册，不手改审批字段。

## 执行选定规模

```bash
# 首次真实试跑：第一题、三个系统各一次，共 3 次
bash experiment.sh quick --allow-pilot

# 示例：每系统 6 次，共 18 次；默认题库中仅前 6 题被访问
bash experiment.sh run --allow-pilot --count 6 --concurrency 2 \
  --out work/experiments/open-round1
```

- [ ] 在终端核对实际题目和规模，再输入 `RUN`。无人值守时由操作者明确使用 `--yes`。
- [ ] `--count` 是每系统总尝试数，`--repeat` 是每题每系统次数，不能同时使用。`run` 不给数量默认为 90 次，首次使用 `quick` 或明确数量。
- [ ] 统一入口按所选系统顺序执行；`--concurrency 2` 指当前系统最多两个根任务，不代表三个系统各两个同时运行，也不是 HTTP 并发。记录主机负载和供应商限流。
- [ ] 默认 `--choices 0 1` 表示第一个原生菜单选第一项，以后选第二项；不是交替，也不是相同语义路线。新版不以旧题 C1/C2 作为答案，实际选项和后果按原样保留。

## 完成、中断与交付

```bash
bash experiment.sh verify --out work/experiments/open-round1
# 中断后仅恢复从未开始项；不重发失败、已开始或送达未知的请求
bash experiment.sh resume --out work/experiments/open-round1 --concurrency 2
```

- [ ] 读取 `experiment_summary.json` / `EXPERIMENT_REPORT.md`、每系统的 `results.json` / `results.csv`。检查每根 `scope_reached`、`stop_reason`、错误、正文/画面及 usage 覆盖，失败不能删除来凑成功数。
- [ ] 理解退出码：统一生成/恢复的 `0` 要求阶段正常且全部达到观察范围、证据通过；`1` 为汇总检查未全部通过（范围、证据或比较器），`2` 为配置/阶段错误，`130` 为中断。底层批量程序的 `0` 含义较窄。任何状态均不意味着内容质量已评审通过。
- [ ] 组织者保存整个实验目录、安装版本记录和运行日志。八项材料包括事实/约束、实际路径/选择、人物版本/参考图、等待时间、匿名阅读材料、正文画面对应、逐次真实 usage、图片请求/候选/资产/引用；详见 [记录对照](docs/BATCH_RECORDING.md)。未知值不填零，M1/M2/M3/M5/M6 待独立评审。
- [ ] 检查 `review-delivery.json` 是否 ready、partial 或失败，核对计划数和封存数。按其 `entry_file` 用本地浏览器打开全部小说目录，并查看图文页和失败样本。
- [ ] 按 `REVIEW_DELIVERY.txt` 将 ZIP 交给评审者，同时发 [评审者简明说明](docs/REVIEWER_QUICKSTART.md)。组织者映射和原始日志不进入盲审包；在无桌面服务器生成时先把 ZIP 下载到自己的电脑检查。
- [ ] 保存人工/AI 后续评分及评审版本到独立目录，不改写封存故事。播放只展示已记录路径；缺少全篇结局不扣分，事实矛盾、重复和没有推进仍可评价。

原始输入审计工具可用 `python3 -m unittest discover -s tests -p 'test_eval30_input_audit.py' -v` 做离线维护检查。旧提交测试数量和平台记录属于当时快照，现行验证按 [验证范围](docs/OPEN_ACTION_VALIDATION.md) 与 [回放验证](docs/PLAYBACK_VALIDATION.md) 分别阅读。
