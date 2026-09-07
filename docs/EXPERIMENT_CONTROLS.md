# 数量、题材与并发控制

默认使用不预设关键行动的 `v4-30-open-actions-pilot.2`，系统自行生成行动，不做跨系统语义匹配。旧版C1/C2只用于显式选择的历史题集。[完整说明](OPEN_ACTION_EXPERIMENTS.md)

```bash
# 先做3次真实尝试：同题、每系统1次
bash experiment.sh quick --allow-pilot
# 每系统12次，共36次，当前系统并发2
bash experiment.sh run --allow-pilot --count 12 --concurrency 2
# 单系统、科幻题，共7次
bash experiment.sh run --allow-pilot --systems if_line --genres SCI-FI --count 7 --concurrency 2
# 两题、每题每系统2次，共12次
bash experiment.sh run --allow-pilot --case-ids CAMPUS-01 MYSTERY-01 --repeat 2
```

`--count`为每系统总尝试数；`--repeat`为每题每系统次数，二者互斥。`--case-ids`与`--genres`也互斥。题目按给定顺序循环，count=7且两题时分配4/3次，少于题数则后面的题可能0次，终端与计划明确记录。

`--systems`可选择if_line、ai4visualnovel、infiplot中的一到三个。`--concurrency`为当前系统根故事并发，系统依次执行，不代表三个系统同时各运行相同并发，也不是供应商HTTP并发。数量均为尝试数，不自动补跑失败来凑成功数。

run未指定数量默认全部30题每题每系统一次；quick默认第一题一次。genres预设是六类各第一题，而`--genres SCI-FI`选择科幻全部五题。full的270次仅为可选预设，不代表正式内容审批。

`--choices 0 1`是第一次原生菜单选第一项，以后选第二项，耗尽后重复最后一个下标。不限定选项行动，不强求同一剧情，也不强制差异。

未显式授权不会静默调用付费模型，使用RUN确认或yes授权。没有适配器总费用、token或时间上限。恢复只启动未开始任务，既有题目、代码和配置不可覆盖。新默认输入与旧批次版本不同，更新后请用新实验目录。
