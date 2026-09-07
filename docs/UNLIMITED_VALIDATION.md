# 无预算上限适配与真实重测（2026-09-07）

已按用户要求取消三套适配器的生成预算上限；三套冻结源码保持原样。共同任务和开头未改，原生流程及生成文字不修补。

无限模式实现和接入检查通过。真实重测中 IF Line、InfiPlot 到达第一次未执行选项；AI4VisualNovel 在原生大纲修订后因节点数量校验失败停止，尚未生成正文。不能据此报告三个项目都正常产出故事。

## 当前配置

执行提交：`d16bdea317da242d38ab24adb3d229b24722a65a`。三个运行的可执行代码清单哈希相同：`43fb325a63313cd445ee5b7e330679c851ceb45814cb0c1029578462b674e553`。

```json
{
  "budget_mode": "unlimited",
  "timeout_seconds": null,
  "max_calls": null,
  "max_output_tokens": null,
  "max_input_chars": null
}
```

四项显式 null，取消适配器总生成时限、原生生成任务等待期限、HTTP 调用上限、输入字符上限及输出 token 注入/压低。不是把上限改成大数字。清除继承的旧限额环境变量，HTTP 次数和可得 usage 继续记录。

原生已有的 token 参数、循环停止规则、SDK/服务商限制继续保留。依赖安装、服务启动、API 提交/查询与清理采用独立超时，不作为总生成截止。缺省模式仍按旧 bounded 规则拒绝空限额，旧实验可复现；新 v3 模板默认 unlimited。单系统不得覆盖共同模式或限额。

## 相同条件真实重测

三者共用 deepseek-v4-flash、https://api.deepseek.com 与 thinking.type=disabled。CAMPUS-01-V3 为开发 pilot，公共文本 2565 个字符，SHA-256：`c8741573a37879a743113da4013765d432d0e3dc9cc22f60697d1883030b9335`。开头 SHA-256：`196d04a58ba5bce543e93788e496ae42fdd547d1583d185cda42c3b969159c65`。每系统只运行一次，失败不挑选重试。

| 项目 | 接入检查 | 原生结果 | 当前新增正文字符 | 选项 | 未来预览 | 实际 HTTP | total tokens |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| if_line | passed | completed | 0 | 2 | 2 | 6 | 18777 |
| ai4visualnovel | passed | native_error | 0 | 0 | 0 | 11 | 35435 |
| infiplot | passed | completed | 850 | 2 | 0 | 3 | 12929 |

IF Line 原生选项前新增正文为 0，两个未来预览共 249 字符；v3 明确允许固定开头后直接出现选项。InfiPlot 新正文 16 段、850 字符。计数包含空白，固定开头不计新增正文。质量与选项语义尚未评分。

AI4VisualNovel 本轮错误是 `native_graph_node_count_mismatch:expected=12:actual=11:native_exit=1`。初始大纲及第一次修订均通过 12 节点检查；原生第二轮普通审核要求删除一个结局并补过渡节点保持总数；第二次修订未补足，仅返回 11 节点，被原生校验拒绝，脚本阶段没有启动。磁盘上保留的上一份 12 节点设计不能代表被拒绝的最新响应。原生审核还出现 JSON schema 的 null 字段不符合字符串要求，原生重试/普通审核回退均保留。

上一轮的 160 次 HTTP 耗尽是实验调用上限：原生多 Agent 演绎反复调用 API，占用 144 次后，后续正文整理被预算拦截。本轮已无该上限；11 次调用后停止是另一个原生校验失败，不能继续归因于预算。未补节点、改审核结果或重写故事。

## 核验与本地回归

- 三个原生接收观察均与共同文件逐字相同，首个真实创作请求完整出现公共块一次。完整模型消息仍保留原生方法差异。
- 三个成功/失败 result 均通过同一 JSON Schema、来源映射和封存文件校验；resume-export 回读相等且未发新请求。三者 adapter_status 均为 passed；这不把 AI4 的原生失败算作成功。
- 本轮 20 个 HTTP 均有完整响应记录，未观察到 budget_exhausted 或 delivery_unknown。供应商 token 可得，金额不可得为 null，不估算为零。
- 运行前后 2,162 份原生文件均匹配冻结源码锁：IF 1,200 / AI4 35 / Infi 927。
- 完整 unittest 运行 206 项，196 通过、10 跳过；其中 7 个缺 jsonschema 的测试在独立完整环境实际执行，v3 38 项全部通过，另 3 个 opt-in 原生集成分别执行。
- 公共无限模式 9 项回归通过：四显式 null、混入限额拒绝、共同条件不得单方覆盖、根生成不设计时器、线程内执行和封存恢复。
- IF 单测 22 项通过，trace 18 项通过；无限和 bounded 两条真 PostgreSQL/Redis/Celery 本地固定响应链各通过，源码 1,200 文件不变。
- AI4 完整套件 37 项通过；161 次 localhost HTTP 跨旧 160 上限，保留计数和原生 token 参数；无限模式原生 design/script CLI 完整通过。
- Infi 本地传输覆盖 161 次 HTTP 与超过旧输入长度，原始 Next 鉴权路由无限模式通过；公共 helper 对齐后的 preflight/unit 21 项通过。

固定响应测试只证明连接和控制行为，不代表故事质量或真实供应商容量。Infi 真实模型重测采用本地隔离身份 fixture，通过原生鉴权代码；生产账号部署未在本轮验收。

## 证据

`results.json` 与三个项目子目录保存统一结果/审计/配置/manifest。原生来源路径应相对完整 run 根目录解析；不能只拿单独 result 副本当完整原始证据。

完整原始三次运行保存在 `unlimited-live-runs.zip`，22846510 字节，SHA-256：`59ee0d51dde099c85e26e4628c2d6c1d885cf6c2833e11e9907701f7a8e7b1ba`。共 221 个文件，三个封存清单均经压缩包逐文件复核，实际模型凭证字节未包含。旧运行及旧报告未覆盖。

本阶段不修改项目原生缺陷，也不补故事内容。完整分支回放、30 题正式评测与文学质量统计仍属于后续实验。

[统一核验数据](evidence/unlimited-20260907/validation-summary.json) · [三个返回](evidence/unlimited-20260907/results.json)
