# InfiPlot 大响应记录与调用来源

64 MiB 设置解决原生 HTTP 请求接收容量。浏览器开发者工具另有响应缓存限制：一次真实运行中，三个超过旧 10 MiB 的请求已生成成功，但后续 Chromium inspector 缓存驱逐了三个响应体，导致 21/80 段正文缺少直接 Writer 调用关联。这不是原生复用正文的证据，也不能把记录标为完全合格。

外置 `compatibility_server.cjs` 现在旁录 `/api/start`、`/api/scene` 的原生响应 `write/end`：不消费请求流、不重发请求、不缓冲完整响应后再发送；原参数、`this`、回调、返回值及原生背压保持不变。磁盘写入独立排队，原生响应和旁录文件完整关闭后才使用完整状态。失败、中断和解码异常明确记为缺失。

浏览器先保存 HTTP 状态与操作 ID，再读取 CDP 响应体。即使 CDP 驱逐正文，场景来源仍可由外置服务的响应字节确认，CDP 错误本身也会保留。新浏览器记录还保存实际 `postData()` 的 UTF-8 长度与 SHA256；服务器旁录只读取 HTTP `Content-Length` 头，不读取请求数据流。

共同提示词、原生文字、图片传输字节、风格及生成预算不变。采集本身有开销，因此等待时间仍代表带观察器的本地离屏运行。

## 文件与脱敏

`native/server-wire/<operation>.json` 保存操作、HTTP 状态、内容编码、完整性和哈希。生成期间 `.response.raw` 是私有临时旁录，关闭原生服务后、封存前会：

1. 确定性解码 identity/gzip/deflate/br，解析 JSON 或 SSE 原生事件及最终 `done.response`。
2. 依照共同 `redact_evidence` 规则保存 `.response.evidence`，去除供应商签名 URL 参数等秘密。
3. 区分原始 `original_response_sha256/original_response_bytes` 与安全文件的 `response_sha256/captured_bytes`。原响应未完整采到时，原始总量为未知。
4. 删除临时原文，包括无法匹配元数据的孤立旁录；无法解析的响应只保留可用哈希和明确缺失状态。

`native/response-retention.json` 记录转换。异常硬终止可能留下未封存的私有临时文件；这类根运行不得直接公开或上传。

封存入口还会拒绝任何仍含 `.response.raw` 的根运行。即使脱敏或删除因磁盘、权限问题失败，也不能把原始敏感响应作为成功封存产物。

`native/server-response-capture-audit.json` 检查响应采集是否齐全；`native/text-call-lineage-audit.json` 检查每个正文段到唯一原生操作及 Writer 尝试的关联。迟到记录可以在封存前补齐 `source_call_ids`，不会改文字、顺序或显示修订。最终出现冲突时保持未知并记录 `text_call_lineage_incomplete`，不得猜测。

已有 `manifest.json` 的根运行明确拒绝再次修改来源或保留文件。旧缺口只能另存可追溯的派生分析，不能改旧证据或倒称旧采集已完整。

## 八项指标

M1/M2/M5 的 `evidence_status=available` 表示正文可供评审，不代表调用来源齐全。M3/M6 的图像关联、M4 的单调时钟事件、M7 的 usage 覆盖、M8 的候选计数继续按原规则计算。新增来源审计独立保存，旧 `metrics.json` 重算规则未变。

未达到共同窗口、供应商交付未知或采集缺失时，须同时报告停止原因和覆盖。例如 3968/4000 字不能当成已达到 4000 字；TLS 失败调用的 usage 未知不能当零。

## 本地回归

`benchmark/tests/test_infiplot_response_capture.py` 覆盖真实 112 MiB HTTP 流、原字节哈希、首字节提前交付、112 次回调及背压、请求流不被消费、压缩/SSE、签名 URL 脱敏、存储失败、中断、歧义来源、缓存失效、孤立旁录和封存保护。

设置 `INFIPLOT_PLAYWRIGHT_MODULE` 后，使用真实 Chromium/CDP 1 KiB 缓存触发 inspector 驱逐。故障注入仅把测试 `Response.body()` 读取接到该独立 CDP 会话；异常由 Chromium 产生，未使用预制异常字符串。生产观察器通过服务器旁录恢复来源。

```bash
cd benchmark
INFIPLOT_TEST_NODE=/绝对路径/node \
INFIPLOT_PLAYWRIGHT_MODULE=/绝对路径/node_modules/playwright \
INFIPLOT_RESPONSE_EVIDENCE=/绝对路径/新的测试目录 \
python -m unittest discover -s tests -p test_infiplot_response_capture.py -v
```

`test_infiplot_batch.py` 继续验证原生 Next/React、小图生成、预取和实际选择。所有测试只使用本地固定响应，不等价于真实模型生成或质量评审。
