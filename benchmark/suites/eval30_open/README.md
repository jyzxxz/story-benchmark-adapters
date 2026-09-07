# 开放关键行动的30题候选集

版本 `v4-30-open-actions-pilot.2`，仍为pilot。保留原题题材、角色、世界规则与初始事件，不预写关键行动，不规定两次共同选择或跨系统行动语义。

[逐题设定与开头](PROMPTS.md) · [统一前缀](prefix.txt) · [改动与人工审核边界](CHANGES.md) · [完整运行说明](../../../docs/OPEN_ACTION_EXPERIMENTS.md)

```bash
bash experiment.sh preview --out work/open-eval30-preview
bash experiment.sh quick --allow-pilot
bash experiment.sh run --allow-pilot --count 12 --concurrency 2
```

第一条不调用模型，导出30份完整shared_task和三种原生payload；后两条是付费生成，需输入RUN或显式yes。人数、原生内部流程、阅读窗口和八维记录器不变。各系统画面按自己的实际正文评审，不用另一系统剧情当答案。

catalog.json冻结当前源文件、历史来源和逐题公共输入哈希。`tools/open_eval30.py`与`tools/eval30.py`默认均导出第二版；后者用`--legacy-actions`导出原始指定行动版，用`--open-actions-v1`导出第一版开放题库。修改题目需要新版本与新哈希，不能修改冻结实验。没有批量自动审批，只有人工审阅后的hash-bound副本可不带allow-pilot运行。
