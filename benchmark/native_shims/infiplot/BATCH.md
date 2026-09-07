# InfiPlot 图文批量驱动（v4）

大响应旁录、浏览器缓存驱逐、封存前脱敏与来源完整性检查见 [响应记录说明](../../../docs/INFIPLOT_RESPONSE_RECORDING.md)。

本模块供公共 `batch` 入口调用，使用冻结 InfiPlot `a60e18bc663caaa134d9323a2b89159b7cc9bd05` 的原生中文 React 页面、`/api/start`、`/api/scene`、Session、Writer、角色生成、Painter 和预取。927 个原始源码文件逐字复制到每次运行的临时目录；源码锁在前后验证。它与 [只导出首个未选择菜单的 v3 适配器](README.md) 是两个运行入口。

## 路径与停止规则

公共编译文件通过原生自定义故事 UI 使用的 `sessionStorage["infiplot:custom"]` 进入原页面。浏览器真正发送的 `/api/start` JSON 保存为 `native/browser-wire/*.request.json`；`route-transmitted-task.json` 明确是浏览器发送边界，不能冒称服务器解码收件。首个真实 SDK 请求中的公共任务区在 `native/text-relay/received-*.json` 提取并逐字比对。只有首次 Writer 的唯一冷开场句执行 README 中的固定替换；后续原生续写保持原消息，不追加故事、演员、摘要、提醒或人工历史。

驱动点击原生正文卡片和菜单，按 `choice_indices` 的零基下标选择；序列耗尽后重复最后一个下标，越界保留失败。只有真实 Session 的 exit/目标 beat 提交后才记录已执行选择。真实回访属于实际路径，会再次记录；静止 DOM 的重复观察不重复计字。

正文按原生可见顺序采集 narration、带 speaker 的 line。隐藏 line、choice seed、内部计划和未选分支不填充正文。公共 Recorder 在达到 `window_chars` 后的句界结束可读窗口；最后裁剪段仍关联实际画面。菜单数和 native scene/API 次数不充当等长单位。没有原生结局时 `native_ended=null`，不会编造 end；失败不补写、不另选路径、不自动补跑。

原页面在场景进入时预取所有 change-scene 选项，并可继续原生单选链预取。`prefetch_policy="native_browser"` 保留该行为，适配器不抑制或追加预取。正文只含实际观看路径，公共网关记录所有已发送的预取调用、图片候选和 usage。结束时关闭浏览器及原生消费者，公共网关继续排空已发送的上游请求；不把未选内容冒充已读正文，也不省略预取消耗。

## 外置接入规则及已知原生入口问题

当前默认 `route_compatibility="native_render_entry"`。在本机冻结 Next 环境中，原生中文 `/play?custom=1` 经默认 locale middleware 返回指向自身的 HTTP 307，原网页启动失败。外置 `compatibility_server.cjs` 仅将 **GET/HEAD `/play`** 转交已经初始化的原 Next render server 的 `/zh-CN/play` 页面。API 请求继续使用原 `getRequestHandler`、原 `requireUser`；中文生成语言、页面源码、React 状态及预取不变。此项绕过了页面入口 middleware 的重定向及 cookie 刷新，不能宣称默认原生页面或生产账号流程已通过。每次保存 `native/route-compatibility.json`，包括原 next.config 文件 hash、前后入口和影响范围。`route_compatibility="none"` 可用于复现未适配入口；无效的 env/config 试探方案不在执行代码中。

原生静音自定义输入可能触发 SSR/client 音频按钮的 hydration mismatch；错误全文和 page_error 保留。原生 globals.css 自己隐藏 Next 开发诊断层；适配器不另行隐藏或关闭错误 UI。正常菜单点击会等待原生可见性过渡结束，不使用 force click，也不修改应用错误处理。

外置启动器默认将 `native_request_body_limit_bytes` 设为 **67108864（64 MiB）**。冻结原生服务端请求会把含内联图片的 Session 整体发送；Next 16.2.7 默认 10 MiB 缓冲可能截断 JSON。外部 `request_body_config.cjs` 在原配置加载完成后，仅覆盖 `experimental.proxyClientMaxBodySize`，不改 `next.config.ts`、框架文件、Session、图片或模型请求。该冻结版本的自定义服务器不把 `options.conf` 传进路由初始化，因此适配挂接实际配置加载器，并将每次加载的原值和生效值记入 `native/request-body-config.json`；启动检查要求原生初始化实际读取该设置。其他 Next 版本拒绝启动，需先复核接入。容量不足时仍保留原生失败，不自动扩容或重试。

在 `systems.infiplot` 中显式设 `"native_request_body_limit_bytes": null` 可恢复原生上限；缺省为 64 MiB。`route_compatibility="none"` 也使用外置启动器，但所有页面和 API 均走原 Next 路由，不应用 `/play` 页面入口映射。这是公开的运行条件调整，不是生成调用、token 或费用预算；64 MiB 不表示无限续写，也不消除历史图片持续增长。比较实验应冻结并记录该参数。

当前身份为每次独立端口、随机 cookie/token 的本地 Supabase 身份 fixture；原鉴权代码实际执行并调用该服务。`native/auth-provenance.json` 明确 `production_account_authentication_verified=false`。不会使用别次运行的会话。暂不支持批量入口的生产账号模式。

会话隔离元数据使用布尔字段 `session_isolated_per_run`，并记录独立监听端口及关闭状态。旧字段 `cookie_isolated_per_run` 会被通用密钥脱敏规则替换为 `[REDACTED]`；旧档案保持原样，该字符串不能作为布尔隔离证明。新记录不保存真实会话凭证，脱敏规则不放宽。更新适配器后须新建批次，不能继续原代码清单冻结的旧队列。

冻结原生 Painter 将 `timeout: undefined` 显式传给 OpenAI 6.42 SDK；本地媒体路径观察到 SDK 在发送前拒绝该参数。外置启动器通过原生已有 `IMAGE_TIMEOUT_MS` 环境配置恢复**安装的冻结 SDK 自身** `OpenAI.DEFAULT_TIMEOUT`，当前值 600000 ms（`node_modules/openai/client.js` 的 `OpenAI.DEFAULT_TIMEOUT = 600000; // 10 minutes`）。实际读取值和原因保存在 `native/native-image-settings.json`。这是原 SDK 每请求期限，非额外生成总预算。可显式配置 `native_image_timeout_ms`，其值必须披露；`native_image_hedge_ms` 亦通过原生设置记录。默认不从操作员 shell 继承这两项调参。原生 SDK retries、Painter 参考图失败降级等保持原实现。

## 图像、调用与时间证据

- 原生文本、图像和视觉模型经公共网关，真实配置来自同一份 batch JSON。图像使用 `gpt-image-2`，视觉使用 `gpt-5.4-mini`。图像 generation JSON、edit multipart 参考图字节、所有候选、完成/失败/未知 usage 都由网关记录。没有发生的 vision 调用不能报已验证。
- 外置 Next 服务给每次真实原生 `/api/start` 或 `/api/scene` 分配观测 operation id。AsyncLocalStorage 将它附在发往本地模型传输的 HTTP 头；响应头也返回同 id。网关不会把这些观测头转发给外部供应商。请求 JSON、故事字段和 native Session 均不增加 id。
- `native/observations/*.lineage.json` 根据原生响应里的唯一 scene id 和上述 operation id，对应同一 operation 的实际 Writer 调用；`source_call_ids` 表示这些确定属于该原生操作的 Writer 尝试，**不猜多次尝试中哪次响应最终被采纳**。无唯一关系时保持 null 并记录原因，不按文本相似度或预取顺序配对。
- 每个可见 beat 的完整原生观察保存在 `native/observations/*.json`，正文以 JSON pointer 指向确切字段；人物和原生状态版本保留实际值。图片只有与网关候选原始字节 hash 唯一相同才关联 output id，重复候选或缺证据留 unknown。原生 scene image 是整张背景，角色 portrait 是生成参考，不能虚构成叠加立绘层。
- `visuals/frame_map.jsonl` 指向实际背景、正文段、clean 图和原始 UI 截图。clean 图是原 img 元素按原 CSS 尺寸的浏览器截图：短暂隐藏其同级字幕/控件，由 Playwright screenshot 的临时 style 机制在截图后自动移除，不写原元素 style；不重绘、不生成新图片。UI 截图保留原字幕和控件。空正文真实菜单可记录空 `segment_ids`，不会当作新增正文图文帧。
- 原页面 JSON API 响应是原生选择；没有改为 SSE 播放。供应商 Writer SSE 在传输边界采集，不能称为浏览器可见流式延迟。
- `frame_presented` 携带 Recorder 返回的 `frame_id`。离屏观测使用父 Recorder 同一时钟，关联已解码原生 img 的事件；浏览器 `performance.now()` 仅作诊断。`offscreen_native_dom` 呈现时间单独报告，真人桌面呈现未测量，不能等同真人阅读速度。
- 证据写入使用公共 `redact_evidence` 去除签名 URL query，原生 console 还显式隐藏进程中密钥/cookie。发给原项目和供应商的响应本体不因此修改。保留原始内容 hash 与可复核的安全表示。

## 依赖与配置

需要 Node >=22、pnpm 9.12.0、冻结 lockfile 对应的 pnpm 缓存，以及 Playwright 和匹配的 Chromium。原生页面使用 Google 字体，首次 Next 编译可能需要对应字体网络资源；构建失败会保留原错误，不能算模型失败或自动换字体。

在仓库根目录执行下面的缓存准备，安装发生在独立副本，不能在 `systems/infiplot` 直接安装或构建：

```bash
mkdir -p work/infiplot-dependency-cache
cp systems/infiplot/package.json systems/infiplot/pnpm-lock.yaml work/infiplot-dependency-cache/
(cd work/infiplot-dependency-cache && pnpm install --frozen-lockfile)
mkdir -p work/infiplot-browser-tools
npm install --prefix work/infiplot-browser-tools playwright@1.55.1
PLAYWRIGHT_BROWSERS_PATH="$PWD/work/infiplot-browser-cache" \
  node work/infiplot-browser-tools/node_modules/playwright/cli.js install chromium
```

配置 `systems.infiplot.playwright_module` 为安装后的绝对模块目录；运行时也设置相同 `PLAYWRIGHT_BROWSERS_PATH`。可用 `chromium_executable` 指向明确浏览器。不要混用 Playwright 版本与另一版本的 Chromium 缓存。`node_executable`、`pnpm_executable` 缺省或 null 时从 PATH 查找；`node_modules` 不用于取代原生冻结安装。运行始终在新目录执行 `pnpm install --frozen-lockfile --offline`，默认依赖安装期限 180 秒、服务/页面启动期限 120 秒。浏览器操作与清理有独立基础设施期限，模型生成没有总截止或调用/输入/输出 token 适配器上限。

除公共 provider、bundle、window、choice 配置外，InfiPlot 专用参数：

| 参数 | 默认/用途 |
| --- | --- |
| `repo_path`, `source_lock` | 未修改的源目录和根 baseline-lock.json |
| `node_executable`, `pnpm_executable` | null/缺省时分别 `node`、`pnpm` |
| `playwright_module`, `chromium_executable` | 独立浏览器模块/可选浏览器可执行文件 |
| `identity_mode` | `local_fixture`；暂不支持生产账号 |
| `route_compatibility` | `native_render_entry`；`none` 仅诊断默认原生入口 |
| `prefetch_policy` | `native_browser`；保持原页面调度 |
| `shared_opening` | true；固定冷开场句兼容 |
| `dependency_timeout_seconds`, `startup_timeout_seconds` | 180、120，基础设施期限 |
| `native_image_timeout_ms`, `native_image_hedge_ms` | 显式原生调参；未配置 timeout 时读取 SDK 原值 |
| `native_request_body_limit_bytes` | 缺省 67108864（64 MiB）；正整数为显式容量，null 保留原生默认 |
| `keep_runtime` | false；源码校验和清理通过后删除临时目录 |

批量 count/concurrency 等统一命令见 [批量运行说明](../../../docs/BATCH_RUNNING.md)。每次运行有独立 Next 目录、端口、模型 relay、身份服务及 browser context。密钥只通过共同 provider 声明的环境变量传入，切勿放入 JSON。

## 本地验证及局限

从 `benchmark` 目录运行纯单元测试（不会调用付费供应商）：

```bash
python3 -m unittest discover -s tests -p 'test_infiplot*.py' -v
```

启用原页面多模态 fixture，证据目录必须是全新路径：

```bash
INFIPLOT_BATCH_TEST=1 \
INFIPLOT_BATCH_EVIDENCE=/absolute/path/new-infiplot-fixture-evidence \
INFIPLOT_PLAYWRIGHT_MODULE=/absolute/path/playwright \
python3 -m unittest discover -s tests -p test_infiplot_batch.py -v
```

这组测试使用本地固定 Writer、图片 PNG、usage 和身份响应，验证原生鉴权页面、真实 DOM 操作、实际 choice 提交、未选分支预取、图像 generation/edit multipart、原始可见正文/实际帧/人物与 source 锁；不证明模型故事质量、生产账号鉴权、真人桌面时延或供应商可用性。原图片是测试色块，不作为画质证据。正式真实样本、测试计数、封存 hash 与失败尝试应以交付验证记录为准；预取的失败也保留，不能因实际选路成功而删去。

单独验证大请求容量（无模型调用、无付费供应商）：

```bash
INFIPLOT_BODY_TEST=1 \
INFIPLOT_BODY_EVIDENCE=/absolute/path/new-capacity-evidence \
python3 -m unittest discover -s tests -p test_infiplot_request_body.py -v
```

同一份约 12 MiB 的合成 JSON 分别经过两次独立原生部署：原生默认上限返回 `Invalid JSON`；64 MiB 设置下完整解析后返回原路由的 `session is required`。刻意不提供有效 Session，以保证不进入生成引擎。小请求控制组及未登录 401 同时检查；运行前后验证原文件哈希。这是容量接入证据，不是完整故事或模型质量结果。
