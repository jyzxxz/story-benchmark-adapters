# InfiPlot Best Practices

PR-Agent 的 `/improve` 工具读本文件作为项目级规则。违反时会被打 `Organization best practice` 标签。

权威来源是 `AGENTS.md`；本文件只列由 PR-Agent 自动审查的高价值不变量。新增时两边同步。

---

## Pattern 1: Server 必须无状态

引擎语义是 `Session + EngineConfig → SceneResult`。客户端持有完整 `Session` 并在每次请求时发回；服务端不引入按用户键的全局缓存、模块级会话存储或持久化层。

## Pattern 2: Writer 不得 patch `StoryState` 的 stable 字段

`StoryState` 分两区：stable 字段 `logline / genreTags / protagonist / castNotes` 由 Architect 在会话开始时写入，**Writer 永不覆盖**；volatile 字段（`synopsis / openThreads / relationships / nextHook`）每场景可重写。`applyStoryStatePatch` 必须过滤 patch 中的 stable 键。

## Pattern 3: LLM 输出必须经 `parseJsonLoose()` 解析

`parseJsonLoose`（`lib/engine/jsonParser.ts`）依次尝试 direct parse、fenced extraction、object slicing、jsonrepair。核心 agent 输出禁止裸 `JSON.parse`。

## Pattern 4: Writer prompt 的 stable prefix 不可重排或重排版

`buildWriterPlanUserMessage()` 与 `buildWriterBeatsUserMessage()` 的 stable prefix（world / style / story spine / archived history / known scene keys / character list）顺序与格式直接决定 prompt cache 命中率；重排、改 markdown 风格都会击穿缓存。dynamic suffix 可以变。

## Pattern 5: TTS 隐私 — 剥离 `referenceAudioBase64` 并尊重 `clientTts`

客户端在调用 `/api/scene`、`/api/vision`、`/api/insert-beat` 前必须从 `Session` 里 strip 掉 `voice.referenceAudioBase64`，请求返回后本地合并回去。当 `clientTts: true` 时，服务端路由必须 drop `config.tts`，确保用户 TTS 密钥永不触达服务器、服务端 TTS 不被意外触发。

## Pattern 6: `orientation` 在会话开始时锁定，缺省值统一回退 `"landscape"`

`orientation` 控制 prompt framing、生成尺寸、mock 图像与 PlayCanvas 布局，整个会话期间不变。所有进入 `scene.orientation` 的路径（Architect 输出、Prebaked firstact、fallback 分支、异常 / 缺失输入）都必须把无效或缺省值 coerce 成 `"landscape"`，不允许透传 undefined 或非法字符串。fallback 分支尤其容易漏归一化。
