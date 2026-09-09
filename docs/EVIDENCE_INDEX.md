# 历史证据档案索引

`docs/evidence/` 保存不同时点的工程夹具、真实模型摘要和离线编译检查。**不是全部来自模拟响应，也不是全部属于同一版本或同一轮实验。**当前接收者应从[操作者交接说明](OPERATOR_HANDOFF.md)安装和生成自己的结果；档案中的测试次数、模型、旧预算、旧首选择范围均只适用于对应报告。

| 目录或文件 | 类型与对应报告 |
|---|---|
| `mock/`、`environments/`、`common-input-equality.json`、`root-runner-ai4vn-mock.json`、`archive-sha256.json` | 2026-09-06 首期固定响应接入档案，见[第一期验收](ACCEPTANCE.md)。 |
| `clarified-retest-20260907/` | 旧 C1 任务的真实模型重测摘要、旁置审阅及档案回执，见[澄清重测](CLARIFIED_RETEST.md)。 |
| `v3-final-20260907/` | 有预算上限的 v3 真实运行与派生重审摘要，见[v3 验证](V3_VALIDATION.md)。 |
| `unlimited-20260907/` | 取消适配器预算上限后的 v3 真实重测及本地回归，见[无限模式验证](UNLIMITED_VALIDATION.md)。 |
| `handoff-20260907/` | Linux 安装、诊断与本地原生夹具记录，见[交接验证](HANDOFF_VALIDATION_20260907.md)。不含接收者供应商的鉴权验收。 |
| `open-actions/` | 开放行动题库的离线编译与一致性验证，见[开放行动验证](OPEN_ACTION_VALIDATION.md)。未生成真实故事。 |

首次图文、后续并行和播放器的其他记录还可能位于 `docs/validation/` 或发布者本地档案；以[并行重测报告](PARALLEL_RECHECK_20260907.md)和[回放验证报告](PLAYBACK_VALIDATION.md)中列出的实际位置及范围为准。仓库里的摘要不等于完整本地故事与图片包。

这里保留实际收件、HTTP 请求/响应、源码前后校验、原生产物或它们的发布摘要。发布时凭证按记录器规则脱敏；首期档案中的本机绝对路径已替换为 `<workspace>` / `<user-home>`。**不要对本目录运行 `resume-export` 或批次恢复。**部分 manifest 指向发布者另存的完整 run，单独复制的 result 或摘要不是可恢复运行目录。

从仓库根目录运行 `python3 tools/verify_evidence.py`，可检查其清单覆盖的首期档案与公共收件；它不是对本页全部后续子目录、所有历史运行或当前 `main` 的统一验收。其他记录的复核范围按对应报告和封存清单确定。原始 JSON、图片、哈希和失败结果继续保留，文档更新不重写历史证据。

原封存的 [evidence/README.md](evidence/README.md) 已计入 `archive-sha256.json`，其描述只适用于首期固定响应档案。为保持哈希完整，不修改该原文；本页负责维护跨版本索引。

## 首期固定响应档案的读法

首期三个项目使用无嵌套 Git 的原始源码快照和同一份 CAMPUS-01 文件；原生程序、接口和 SDK 实际运行，文本模型由 localhost 固定工程夹具替代，未调用付费模型。

- IF Line：原生任务快照收件；真实 PostgreSQL/Alembic、Redis、API/Celery worker/beat，5 个固定响应 HTTP 调用。
- AI4VisualNovel：原生需求读取函数的返回值；design/script CLI 与四类原生 Agent，78 个固定响应 HTTP 调用。
- InfiPlot：实际 SDK 中的公共任务区块；原生 Next.js API 与本地 auth fixture，2 个固定响应 HTTP 调用。不是直接观察路由内部的字段。

不同工程测试使用不同的 fixture 模型标识。调用次数、模拟正文及模拟用量不能用于质量、效率或成本比较。`common-input-equality.json` 从三套**实际收到的字符串**重新计算，不是把主文件哈希复制三次；它对应的共同文件与开头在 `benchmark/examples/CAMPUS-01/`，并非当前默认 30 题的运行证明。
