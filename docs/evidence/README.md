# 工程证据档案

三个项目均使用发布的无嵌套 Git 原始源码快照和同一份 CAMPUS-01 文件。原生程序、接口和 SDK 实际运行；文本模型响应均来自 localhost 固定工程夹具，未调用付费模型。

这里保存实际收件、HTTP 请求/响应、源码前后校验及原生产物。凭证按记录器规则脱敏，本机绝对路径替换为 `<workspace>` / `<user-home>`。这些是可复核的工程档案，不是完整的正式 runner 运行目录；使用根目录 `tools/verify_evidence.py` 检查，不对它们执行 `resume-export`。

- IF Line：原生任务快照收件；真实 PostgreSQL/Alembic、Redis、API/Celery worker/beat，5 个固定响应 HTTP 调用。
- AI4VisualNovel：原生需求读取函数的返回值；design/script CLI 与四类原生 Agent，78 个固定响应 HTTP 调用。
- InfiPlot：实际 SDK 中的公共任务区块；原生 Next.js API 与本地 auth fixture，2 个固定响应 HTTP 调用。不是直接观察路由内部的字段。

不同测试使用不同的本地 fixture 模型标识。调用次数、模拟正文及模拟用量不用于质量、效率或成本比较。正式配置将模型、最终 endpoint 与预算统一为一份，尚未选择和执行。

`common-input-equality.json` 由三套**实际收到的字符串**重新计算，不是把主文件哈希复制三次。完整的共同文件与开头位于 `benchmark/examples/CAMPUS-01/`。
