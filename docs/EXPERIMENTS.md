# 故事生成与实验使用说明

**将仓库交给别人生成故事、运行实验，请从 [操作者交接手册](OPERATOR_HANDOFF.md) 开始。** 默认题库为不预设关键行动的 30 题候选集 `v4-30-open-actions-pilot.2`。本页仅作入口导航；参数、冻结和题目审批参考 [实验协议与命令参考](OPEN_ACTION_EXPERIMENTS.md)，本地阅读和打包见 [离线回放](PLAYBACK_REVIEW.md)。

```bash
bash experiment.sh setup
bash experiment.sh quick --allow-pilot
bash experiment.sh run --allow-pilot --count 12 --concurrency 2
```

第一条安装和配置不调用付费模型；后两条展示规模并要求输入 `RUN`，显式加 `--yes` 才跳过确认。次数是尝试数，无适配器总费用上限。

旧版指定两组行动的题库和导出程序不覆盖，可用 `bash experiment.sh preview-legacy --out work/legacy-eval30` 查看。使用旧版时显式传 `--suite-root`并单独标注，不能把旧题C1/C2要求套到新版。

[数量与并发](EXPERIMENT_CONTROLS.md) · [当前题目](../benchmark/suites/eval30_open/PROMPTS.md) · [迁移记录](../benchmark/suites/eval30_open/CHANGES.md) · [八维记录](BATCH_RECORDING.md) · [验证范围](OPEN_ACTION_VALIDATION.md)
