# API Key 池与会话级 BYOK 安全契约

## 1. 本期范围

本期实现两条密钥来源：

- **平台 Key 池**：由服务端环境变量提供，仅服务端可见，按请求轮询使用，对失败 Key 执行冷却。
- **会话级 BYOK**：用户在前端输入 Key，只保存到当前标签页的 `sessionStorage`，请求时通过 `X-LLM-API-Key` 发送。后端只把它放入当前请求的内存上下文，请求结束立即清除。

本期明确不做：

- 不把 BYOK 明文、密文或可恢复形式写入数据库、Redis、Celery 消息、日志、指标或追踪系统。
- 不提供持久化的用户 Key 增删改查 API。
- 不允许前端或请求头指定 `base_url`、provider 或 model；这些值仅由服务端配置，从而关闭 SSRF 和费用路由绕过面。
- 不将一个用户的 BYOK 分享给其他用户或其他并发请求。
- 本期不改动阿里云/讯飞 TTS 凭据；文本 BYOK 与文本、图片、视觉平台池均不会流入 TTS。

## 2. 信任边界与选择规则

```text
当前标签页 sessionStorage
        |
        | HTTPS + X-LLM-API-Key
        v
HTTP 请求中间件 -- ContextVar -- 当前同步 LLM 调用
        |                                  |
        | 请求结束 reset                 | BYOK 失败直接返回安全错误
        v                                  v
      不保存                         不回退平台 Key 池

未携带 BYOK 的请求 ----------------------> 平台 Key 池
```

选择顺序只有两种情况：

1. 请求携带有效 `X-LLM-API-Key`：该请求的 LLM 调用只能使用这枚 BYOK。它若失败，返回脱敏错误，**不得回退平台池**。
2. 请求未携带 BYOK：从平台池获取 Key。可对可重试故障切换池中下一枚 Key。

不回退是费用安全边界：否则用户自带 Key 失效时，会在无感知的情况下消耗平台额度。

## 3. HTTP 契约

### 3.1 请求头

- 唯一支持的 BYOK 请求头为 `X-LLM-API-Key`。
- 只允许单值；拒绝空值、仅空白、逗号、超长值、控制字符和换行（逗号也用于识别被代理合并的重复请求头）。
- 不应强制 `sk-` 前缀，因为 OpenAI-compatible 供应商的 Key 格式不同。
- 该请求头绝不能出现在响应头、JSON 响应、错误细节、访问日志或 tracing baggage 中。
- 生产环境必须使用 HTTPS。反向代理、APM 和 WAF 需将该请求头加入脱敏列表。

### 3.2 返回值

后端无需在业务响应中返回 Key 来源。如果产品需要告知本次费用方，只能返回低基数枚举，例如 `credential_source: "byok" | "platform"`，不得返回 Key 指纹、前缀、后四位或池索引。

## 4. 平台 Key 池行为

### 4.1 配置

- 文本 LLM：`OPENAI_API_KEYS`，保留 `OPENAI_API_KEY` 作为单 Key 兼容项。
- 图像生成：`AI_IMAGE_API_KEYS`，保留 `AI_IMAGE_API_KEY` 兼容项。
- 背景视觉检查：`BG_VISION_API_KEYS`，保留 `BG_VISION_API_KEY` 兼容项。

池配置要求：去除外围空白、忽略空项、去重；不在启动日志中打印数量以外的 Key 信息。平台池和单 Key 兼容项同时存在时，后者可作为去重后的补充项，但行为必须固定并有测试。

### 4.2 轮询与冷却

- 每次新建供应商请求时选择 Key，而不是在进程启动时永久绑定一枚。
- 选择器必须在多线程/多协程下保持状态安全；不要持有锁等待网络 I/O。
- `401/403`：认证或授权失败，当前平台 Key 立即进入长冷却/隔离，并可切换下一枚。
- `429`：限流，按 `Retry-After` 或指数退避冷却，然后可切换下一枚。
- `5xx`、连接失败、超时：短冷却并可切换下一枚。
- `400/404/409/422` 等请求或业务错误：不得通过换 Key 重试，避免放大错误请求和费用。
- 全部 Key 冷却时应快速失败，不在 Web worker 内长时间睡眠。
- 尝试次数不超过当次可用的不同 Key 数，同一请求不重复尝试同一枚 Key。
- 流式请求在返回第一个 chunk 之前发生的建连失败可以切换 Key；一旦已产出任何 chunk，后续错误只能标记冷却并向上传播，不得换 Key 重放请求，避免重复内容和重复计费。

BYOK 不参与平台池冷却状态，也不会在请求结束后被缓存。

## 5. 并发隔离与生命周期

BYOK 请求上下文必须满足：

- 两个并发请求各自只能读到自己的 Key。
- 无 BYOK 请求不能读到上一个请求的 Key，即使复用同一 worker 线程或异步任务上下文。
- 正常响应、HTTP 错误、未捕获异常和客户端断开四条路径都必须在 `finally` 中 reset token。
- 应用启动、模块导入或全局 LLM client 初始化不得捕获某次请求的 BYOK。
- Python `asyncio.create_task()` 会复制当前 Context，仅在父任务执行 `ContextVar.reset()` 不能撤销子任务已复制的 Key。请求上下文应具有可撤销的 active scope，请求结束后已脱离的子任务必须读到 `None`。
- 对 SSE/`StreamingResponse`，请求 scope 需持续到 ASGI response body 完全发送、异常或客户端断开。建议使用纯 ASGI middleware 在 `await app(scope, receive, send)` 的 `finally` 中撤销，避免 `BaseHTTPMiddleware/call_next` 过早返回导致 scope 提前失效。

### 异步任务的硬边界

`sessionStorage` BYOK 只适用于当前 HTTP 请求内完成的供应商调用。需要写入 Redis/Celery/outbox 的任务不得携带 Key、请求头快照或包含 Key 的 client 对象。

当一个业务 API 只是创建后台任务时，必须选择并向用户明确表达以下之一：

1. 该任务使用平台 Key 池，不使用本次 BYOK；或
2. 该接口拒绝 BYOK 并返回可理解的错误。

不得为了跨请求延长 BYOK 生命周期而将其序列化。

本期采用第 1 种边界：现有 Redis/Celery/outbox 持久任务始终使用平台池；会话级 BYOK 只作用于当前 HTTP 请求内实际发生的文本模型调用。未来若前端启用这些异步接口，必须在提交前明确提示费用来源为平台池。

## 6. 前端约束

- 只使用 `sessionStorage`，不使用 `localStorage`、IndexedDB、cookie、Pinia 持久化插件或 URL 参数。
- Key 输入框使用 `type="password"`，默认遮罩；不在 DOM 中渲染完整 Key。
- 不将 Key 放入 Vue 开发工具可持久化状态、错误上报、分析事件、console 或剪贴板自动备份。
- 用户可显式清除；注销时必须清除；标签页关闭后由浏览器清除。
- 登录身份变化时只通过 `BroadcastChannel` 广播固定的清理事件，让其他标签页删除各自的 BYOK；广播消息不得包含 Key 或用户输入。
- 只向同源 API 附加请求头。不向对象存储、CDN、签名上传 URL 或第三方 URL 附加该请求头。
- 页面需明示风险：页面中的 XSS 仍可读取 `sessionStorage`；BYOK 并不是浏览器硬件密钥保护。

## 7. 可观测性与审计

可记录：

- provider/model（均为服务端配置）、请求 ID、延迟、token 用量、状态码类别、`credential_source=byok|platform`。
- 平台池的可用 Key 数、冷却 Key 数和切换次数。低流量环境下应谨慎暴露过细的单 Key 标签。

禁止记录：

- 任何完整/部分 Key、Key hash/指纹、Authorization 或 `X-LLM-API-Key` 值。
- 可能包含请求头的 request 对象、provider client 对象或原始异常 `repr`。
- provider 原始错误响应；对外只返回稳定错误码和脱敏描述。
- 脱敏异常对象不得通过 `__context__`、`__cause__`或自定义属性保留 provider 原始异常。`raise safe_error from None` 如果仍在 `except` 块内执行，只会隐藏默认 traceback 展示，不会清除 `safe_error.__context__`；应在离开 `except` 后抛出。

## 8. 未来持久化 BYOK（非本期）

如果未来需要跨标签页或异步任务使用用户 Key，应单独设计持久化方案，至少包含：

- 使用经审计的 AEAD（例如 AES-GCM）进行信封加密，主密钥位于 KMS/秘密管理器，不在 DB 中。
- 按用户和用途绑定 AAD，密文不能在用户间交换。
- 只返回状态和非敏感标识，创建后也不回显明文。
- 主密钥版本、轮换、撤销、审计事件、数据删除、权限模型和备份恢复演练。
- 生产环境缺失加密主密钥时 fail closed，禁止退化为明文或只做 base64 编码。

在上述方案完成威胁建模和迁移评审前，不应为了支持 Celery 任务而临时把会话级 BYOK 写入持久化存储。

## 9. 发布验收清单

- [ ] 无 BYOK 时轮询使用平台池，兼容单 Key 配置。
- [ ] 携带 BYOK 时只使用它，失败不回退平台池。
- [ ] 两个并发请求的 BYOK 不串线，异常后 ContextVar 也被清理。
- [ ] `401/403`、`429`、`5xx/超时`、`4xx 业务错误` 的切换行为分别有单元测试。
- [ ] 请求头、响应、日志、metrics、task/outbox payload 的泄漏测试通过。
- [ ] 前端只用 `sessionStorage`，注销/显式清除有测试，第三方 URL 不附加 Key。
- [ ] 反向代理/APM/WAF 已将 `X-LLM-API-Key` 配置为敏感请求头。
- [ ] 生产环境已强制 HTTPS，CORS 只允许可信前端 origin。
