# 30 题与统一实验入口：离线验证记录

> **验证快照：2026-09-07，基于 `3421000`，发布提交 `15fa304`。**本页验证原始指定行动 30 题的整理，早于当前默认的开放行动 v2 题库。数据、哈希和测试计数保留用于追溯；现行安装/生成入口见[操作者交接说明](OPERATOR_HANDOFF.md)，题库区别见[开放行动实验说明](OPEN_ACTION_EXPERIMENTS.md)。

日期：2026-09-07。基于仓库提交 `3421000736f2b7ba0a9252afa7285bc65a59cdb7` 和对话中提供的 `story_eval_suite_v4_30.zip`。

## 实际完成

- 从提供的测试包读取 30 个 case、brief、opening、prefix 和公共输入预期哈希，按六个题材保存为可读源文件。原 v1 文件 Git blob 为 `5f36365767779d5e2af598727d2dd726f27216f0`，与仓库原件一致。
- 30/30 个公共任务通过真实编译核心重新编译和 verify，全部与提供包的对应 `shared_task.txt` SHA-256 一致；三种原生输入封装中的公共字符串相同。此次分文件整理未改变这 30 份公共任务。
- 30 项新增离线 unittest 通过，0 失败。测试包括源文本保留、源哈希与开头篡改拒绝、三系统 payload 一致、拒绝覆盖、pilot/approved 及审核哈希、规模换算、未知/重复题号、配置相对路径、输入/代码冻结、默认不发起模型调用、预检先于生成、失败中止、恢复条件冻结、只读校验不传密钥文件、实验锁和中断记录。
- `bash -n tools/experiment.sh` 和新增 Python 文件语法编译通过。

## 验证边界

测试在本次会话的 Linux 容器执行。用于验证的编译器、合同和 IO 文件通过 Git blob 校对，与上述仓库提交一致：

| 文件 | Git blob |
|---|---|
| `benchmark/story_benchmark/compiler.py` | `7b55194e0079460bac8b24c736ad63d4bcb63586` |
| `benchmark/story_benchmark/contracts.py` | `d299cd63d36def5d0e8e467f0aa0af3534ab7af2` |
| `benchmark/story_benchmark/io.py` | `69f15029afef6c00b6f0cc5fd94ae4e969d9efd8` |

原生批量命令在新增调度单元测试中被明确 mock，不是新的 PostgreSQL/Redis/Pygame/Next 端到端运行。本次没有重跑三项目安装器、真实原生服务或付费模型，也没有真实新故事样本或外部质量评分。API 密钥向导的实际交互及各供应商鉴权、模型格式和额度仍需接收者在准备好的环境中验证。原生已知失败继续披露，不由离线测试结论覆盖。

正式内容批准仅由用户执行审核工具作出。本次发布所有内置题目保持 pilot。测试中创建的 `test-fixture-reviewer` 审核记录仅在临时目录验证哈希协议，不会发布为真实题目批准记录。

本次改动仅包括题目源、说明、额外工具及测试；不修改原生源码、来源锁、已披露补丁、编译核心或八维记录器。此前 Linux/原生接入的证据仍分别见 [交接验证](HANDOFF_VALIDATION_20260907.md)，不能把历史检查算作本次新执行。

复验命令：

```bash
bash tools/experiment.sh test
python3 tools/eval30.py --out work/eval30-recheck
```

只有真实生成完成、原生证据校验和后续独立评审后，才可讨论质量与成本表现。完整使用流程见 [实验指南](EXPERIMENTS.md)。
