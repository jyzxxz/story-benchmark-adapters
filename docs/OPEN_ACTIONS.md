# 不预设关键行动：版本导航

当前默认版本是 `v4-30-open-actions-pilot.2`，接手运行见 [操作者交接手册](OPERATOR_HANDOFF.md)，协议参考见 [当前使用说明](OPEN_ACTION_EXPERIMENTS.md)，30题可直接阅读 [完整设定和开头](../benchmark/suites/eval30_open/PROMPTS.md)。

第一版 `v4-30-open-actions-pilot.1` 的实现、规则与前缀仍保留；第二版进一步中和固定开头末段中的二选一提示，提供可读题库与固定公共哈希，不覆盖第一版。两版都不预设关键行动，不能混用各自的case ID、源哈希或冻结实验。

```bash
# 当前默认第二版，免费导出
python3 tools/eval30.py --out work/open-v2
# 显式导出第一版开放题库
python3 tools/eval30.py --open-actions-v1 --out work/open-v1
# 显式导出最初指定行动题库
python3 tools/eval30.py --legacy-actions --out work/legacy
```

第一版的原始说明与70项验证记录保留在 [提交787a879的文档](https://github.com/jyzxxz/story-benchmark-adapters/blob/787a8793b8dd360c003fbe2e71539132604eab3f/docs/OPEN_ACTIONS.md)。当前整合验证见 [OPEN_ACTION_VALIDATION](OPEN_ACTION_VALIDATION.md)。没有把历史测试当作本次付费或原生生成结果。
