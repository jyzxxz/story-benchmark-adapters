# 故事生成与实验使用说明

当前默认已经切换到**不预设关键行动**的30题候选集 `v4-30-open-actions-pilot.2`。完整、现行操作流程见 [OPEN_ACTION_EXPERIMENTS.md](OPEN_ACTION_EXPERIMENTS.md)，涵盖首次安装、配置、免费预览、快速试跑、数量与并发、恢复、八维记录和正式审批。

```bash
bash experiment.sh setup
bash experiment.sh quick --allow-pilot
bash experiment.sh run --allow-pilot --count 12 --concurrency 2
```

第一条安装和配置不调用付费模型；后两条展示规模并要求RUN确认，显式加yes才跳过确认。次数是尝试数，无适配器总费用上限。

旧版指定两组行动的题库和导出程序不覆盖，可用 `bash experiment.sh preview-legacy --out work/legacy-eval30` 查看。使用旧版时显式传suite-root并单独标注，不能把旧题C1/C2要求套到新版。

[数量与并发](EXPERIMENT_CONTROLS.md) · [当前题目](../benchmark/suites/eval30_open/PROMPTS.md) · [迁移记录](../benchmark/suites/eval30_open/CHANGES.md) · [八维记录](BATCH_RECORDING.md) · [验证范围](OPEN_ACTION_VALIDATION.md)
