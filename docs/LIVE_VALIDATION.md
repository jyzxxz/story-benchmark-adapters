# 真实故事输出验证：2026-09-07

**本次验证未全部通过。IF Line、InfiPlot 已产生真实正文；AI4VisualNovel 三次运行均在设计阶段失败，尚无正文。** 公共输入一致、原生文件不变的检查通过，不等于三个项目能稳定生成满足全部要求的故事。

执行代码：初始版本 `9bdecf7fa63173e816e864cbfa4f64af45e62a79`；修复版本 `5d61e8df8b07c00445aaac90dde71e748222eb06`。

## 修复后结果

| 系统 | 真实输出 | 接入与内容结论 |
| --- | --- | --- |
| IF Line | 49 段，2,334 个非空白字符 | 8 次 HTTP 全完成，来源映射通过。原生规划从宿舍重新开局，未承接固定开头；本次 Script IR 无玩家选项。 |
| AI4VisualNovel | 无正文 | 同条件两次分别完成 3 次、5 次 HTTP；均生成 13 个图节点，违反原生 12 节点要求，设计阶段退出。 |
| InfiPlot | 21 段，836 个非空白字符，3 选项 | 3 次 HTTP 全完成，来源映射通过。封楼年数写错，缺少题目要求的“保护周遥并检查收音机”选项。 |

字数不含公共开头。工程审计通过与故事满足题意分别记录。没有用大纲或摘要代替 AI4VisualNovel 正文，也没有手工删节点、修改原生校验或修写故事。

## 共同条件

唯一任务为 [CAMPUS-01](../benchmark/examples/CAMPUS-01/shared_task.txt)，含固定开头的 1,939 字符、5,231 UTF-8 字节。七次独立运行的实际收件与第一次创作请求都核实公共原文完整出现一次。

```text
shared_task SHA-256
af81207f820c2e6e8ea56939e4b4ed313f1810bf089068bfa1853549c440302a
opening SHA-256
196d04a58ba5bce543e93788e496ae42fdd547d1583d185cda42c3b969159c65
```

真实供应商为 DeepSeek 官方 `https://api.deepseek.com`，模型 `deepseek-v4-flash`。修复后所有实际请求均含 `thinking: {type: disabled}`。初始运行没有显式指定模式；DeepSeek 模式见 [官方说明](https://api-docs.deepseek.com/guides/thinking_mode/)。

每个系统根运行上限 160 次 HTTP、1,800 秒；每次输出上限 16,384 tokens、完整紧凑请求上限 200,000 Unicode 字符，保留原生更低输出上限及原生采样设置。这不是等金额预算。IF Line 采用原生界面默认 13 章规划，只生成第 1 章。

InfiPlot 使用原生 Next.js 与真实模型，同时使用本地 Supabase 身份测试服务；原生鉴权代码未改。这是正文通路验证，未验收生产账号部署。没有执行完整分支回放、播放器或正式质量评分。

## 全部尝试

| 运行 | HTTP 尝试数 | 结果 |
| --- | --- | --- |
| `if_line-real-01` | 14 | 有正文，但 7 次传输没有完整响应证据；章数 1 触发单章完结规则。 |
| `ai4visualnovel-real-01` | 3 | 图生成耗尽输出额度，正式 content 为空，原生 JSON 解析失败。 |
| `infiplot-real-01` | 3 | Writer 正文完成，角色卡请求截断并使用原生兜底。 |
| `if_line-real-02` | 8 | 工程审计通过，正文不遵守固定开头。 |
| `ai4visualnovel-real-02` | 3 | 原生节点数量校验失败，13 与要求 12 不符。 |
| `infiplot-real-02` | 3 | 工程审计通过，有事实和选项偏差。 |
| `ai4visualnovel-real-03` | 5 | 与 real-02 配置完全相同的新目录运行，仍为相同节点数量错误。 |

共 39 次实际 HTTP。第二轮及 AI4VisualNovel 重复运行的供应商 usage 完整，依次为 IF Line 35,428、AI4VisualNovel 10,010 / 20,758、InfiPlot 12,219 tokens。首轮部分用量未知，费用保持 unavailable，不把未知发送当成零成本。

七次完整封存运行、请求/响应、实际收件、原生正文、审核日志、逐文件哈希及旁置内容审阅已交付本地 `story-output-validation-20260907` 证据包。失败运行全部保留；没有择优汇报或推断总体成功率。

## 外置修复与验证

- IF Line 章数默认恢复为整部规划 13；首批导出只生成第 1 章。
- IF Line 外置异步连接禁用 keepalive，解决原生缓存客户端跨事件循环复用故障，原生 SDK 重试保留。已通过真实本地 HTTP/1.1 复现对照和后续真实供应商验证。
- 共同模型控制只允许显式推理参数，实际 wire 注入发生在完整请求预算检查前，每次 HTTP 均审计相同配置；空参数保持原生行为。
- 截断响应保留字节与哈希并标记交付未知；审计支持不完整 UTF-8 字节归档。

公共测试 43 项、AI4VisualNovel 19 项通过；IF Line 适配测试、传输测试、真实隔离 PostgreSQL/Redis/Celery 本地链，以及 InfiPlot 参数/SSE/截断回归通过。三套原生共 2,162 文件在运行前后哈希均匹配冻结版本，源文件未改。

当前验收状态为：共同输入接入通过；IF Line / InfiPlot 正文通路通过；AI4VisualNovel 真实正文通路未通过；三系统完整实验尚未验收。
