# Stage_Prompt_AR — 设计哲学

## 一句话哲学

**把中文叙事文本（StoryBible / ChapterOutline）通过 LLM 重写为信息密度高、视觉可解析、CogView-4 友好的结构化英文 prompt，再让模型去画——而不是把字段拼贴成 prompt 喂给模型。**

## 为什么是这条主线

当前 `prompt_builder_service.py` + `image_generation_service._build_*_prompt()` 的工作方式是：
从 StoryBible 取中文字段 → 直接拼到一堆英文模板槽里 → 在前面塞一长串 `NO TEXT, NO watermark, NO humans` 否定词。

这导致三类"图与文不符"问题：

1. **语义漂移**: 中文外貌描述（如"身着素白丧服的年轻女子"）被原样塞进英文模板槽，CogView-4 对中英混杂 prompt 的注意力分散，关键视觉元素被稀释。
2. **槽位缺失**: StoryBible 字段缺失时，模板用 "characters"、"standing pose" 之类占位符占位，画面变成模型自由发挥。
3. **否定词污染**: 一长串 `NO TEXT, NO letters, NO words...` 占据了 prompt 前 200 token，模型真正的"主任务"被推到后面，注意力衰减。

哲学主张用 LLM 把这些问题在 prompt 生成阶段一次性解决，而不是事后用 VLM 校验/重试——前者改一处（prompt 构造层），后者改两处（生成 + 校验）且每次失败都要花钱重生成图。

## 这条哲学下什么值得做、什么不做

**值得做**:
- 在 `prompt_builder_service` 上加一层 LLM Rewriter，把 `{name, appearance, outfit, mood, scene, action}` 这类结构化字段喂给 LLM，让它输出一段 60-120 词、主体优先、视觉可解析的英文 prompt。
- 用 system prompt 约束 LLM 输出结构（JSON），让代码可解析、可缓存、可回归测试。
- 中文 → 英文翻译和视觉化提炼合并到同一次 LLM 调用里（避免两次 round-trip）。
- 把 CogView-4 不响应的 `seed`、负面词堆砌挪到代码侧用尺寸约束/重复请求代替。

**不做**:
- 不引入新的图像模型供应商（BFL/Replicate/通义万相）。这超出 prompt 优化范围。
- 不上 VLM 视觉校验闭环。复杂度高、成本高，属于下一阶段的事。
- 不重写 CogView-4 调用层（_call_cogview_api 本身工作正常）。
- 不动前端。前端只看 `{success, image_url}`，prompt 优化对前端透明。

## 完成判据（每个 AR 项的硬性要求）

每个 checklist 项只有在以下三个条件全部满足时才能打 `[x]`：

1. 在 `Docs/researches/Stage_Prompt_AR/{portrait|background|keyframe|shared}/` 下存在一篇以该项 slug 命名的 `.md` 文档。
2. 该文档：完全围绕该项主题（不跑题），引用至少 1 个 SOTA 实践（论文/官方文档/权威指南），明确把推荐做法落到本仓库的具体文件和行号。
3. 该文档的所有推荐必须与本哲学一致——优先 LLM 重写路径、不引入新模型供应商、不引入 VLM 校验闭环。

## 边界

- 涉及的源代码文件: `backend/app/services/prompt_builder_service.py`、`backend/app/services/image_generation_service.py`、`backend/app/services/llm_service.py`（已有，可复用做 LLM Rewriter）、`backend/app/config/image_generation_profiles.json`。
- 不动: 前端、数据库 schema、API 路由签名、CogView-4 调用层。
- 研究产出 **只是文档**。AR 阶段不写产品代码——实现留给后续 execution-cron。
