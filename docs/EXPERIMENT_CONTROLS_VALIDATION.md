# 验证记录：数量控制与实验入口的整合复验

> **验证快照：以 `15fa304` 为父版本，数量控制发布于 `67a4b14`。**本轮使用原始指定行动 30 题，早于默认开放行动题库；下文“当前”“新版本”均指本轮。接手运行请使用[操作者交接说明](OPERATOR_HANDOFF.md)，不要将本页测试命令和历史题库身份当作当前生产步骤。

本次以 `15fa304159f6f83f4320273cde4caff4aa670a74` 为父版本整合，保留该版本已加入的完整 30 题输入、配置向导、实验入口、原始文档和审核工具。没有覆盖为另一套题库或并行实现。

修改仅涉及根快捷入口、已有上层实验脚本的任意总次数/题材选择、文档和新增测试。没有修改 `systems/`、原始 brief、30 题的前缀/开头/内容解释、编译器、记录器、基线锁或已有原生补丁。

## 实际执行结果

```bash
python3 -m unittest discover -s tests -p 'test_eval30*.py' -v
bash -n experiment.sh
bash -n tools/experiment.sh
python3 -m py_compile tools/experiment.py tools/eval30.py \
  tools/setup_experiment.py tools/approve_eval30.py tests/test_eval30_counts.py
```

**42 项通过，0 失败，0 跳过**，包括既有 30 项测试与新增 12 项。Python 与两份 shell 脚本的语法检查通过。本次没有调用付费模型。

新增覆盖：任意 count 与原生轮转顺序一致；count 不足时保留计划 0 次题目；repeat 原语义不变；count/repeat 与 case-ids/genres 互斥；负值/零/非法参数拒绝；单系统与题材选择；恢复时禁止更改冻结 count/genres；真实根 shell 转发；无模型的实际计划编译；下层 count/concurrency/resume 参数映射。

既有测试在修改后的代码上重新执行，包含 30 个公共任务的编译、完整 prompt 导出、原 brief 逐字保留、三个原生输入封装相同、未审批和内容篡改拒绝、输入/配置/代码冻结、先预检再派发、只读校验不加载密钥、失败与中断记录，以及本地独占锁。

30/30 任务的 shared_task SHA-256 与上一版 v4-30-pilot.1 一致。全部题目仍是 pilot。测试中的 `test-fixture-reviewer` 只用于临时合成审批测试，不是实际内容批准，未提交任何 approved 题库。

## 来源核对

本地测试使用经 Git blob SHA 核对的当前题库源、原 v1 brief、已有 eval30.py、setup_experiment.py、approve_eval30.py、原 30 项测试和编译核心；对现有 experiment.py 应用增量修改。关键原文件：

```text
experiment.py（修改前） a18b947d09a7daf0848756ae0ea51cc384000bb8
eval30.py               fc5797299872c53a6ac3d8115a7c108f7953d1ea
catalog.json            046d01c707a79dda9987ca81d5408bead4d41ef0
compiler.py             7b55194e0079460bac8b24c736ad63d4bcb63586
contracts.py            d299cd63d36def5d0e8e467f0aa0af3534ab7af2
io.py                   69f15029afef6c00b6f0cc5fd94ae4e969d9efd8
```

## 没有证明的事项

这是上层输入与控制逻辑的离线回归，不是完整仓库的原生回归、Ubuntu/WSL2 安装验收或真实供应商端到端测试。原生生成调用在测试中仍被 mock；实际子进程仅执行帮助、离线编译和临时审批，不运行三个原生服务，不调用付费模型。

因此不能据此保证新机器依赖、供应商鉴权/余额、模型格式、故事质量、出图成功、usage 完整或生产吞吐。第一次真实实验仍需配置使用者自己的服务后运行 quick，检查实际范围、图文与调用证据。M1/M2/M3/M5/M6 没有被自动打分。

原先已生成的实验计划绑定旧代码。此次代码更新后应使用新目录；不能修改旧计划哈希强行恢复或用此次测试冒充旧实验的生成结果。
