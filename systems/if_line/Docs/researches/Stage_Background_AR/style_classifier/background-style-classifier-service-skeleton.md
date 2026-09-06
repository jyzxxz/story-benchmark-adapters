# B01 — background-style-classifier-service-skeleton

## 背景

新链路有 3 个 LLM 步骤：analyzer / classifier / assembler（deterministic）。analyzer 已经把 BackgroundSceneSpec 抽出来了，但 spec 里 `style_tags=[]` 是空——下游 assembler 需要这些 tag 来选 art_style / color_palette / lens / texture / mood。

如果让 assembler 自己拼 tag，会回到 free-form rewriter 的老问题（anime 硬编码等）。需要独立成 `BackgroundStyleClassifierService`，**只做受控词表分类**，输出可追溯的 tag 列表。

历史教训：用户在早期反馈"不管什么类型的小说都是 Makoto Shinkai / galgame"，正是因为 rewriter 没受控词表约束、把 anime tag 写死。

## SOTA 实践

**OpenAI function calling enum 参数**（[`platform.openai.com/docs/guides/function-calling`](https://platform.openai.com/docs/guides/function-calling)）：

> 当 LLM 需从有限集合选择时，声明参数为 `enum` 类型，模型物理上无法输出集合外的值。

**Anthropic tool use + JSON schema**（[`docs.anthropic.com/en/docs/build-with-claude/tool-use`](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)）：通过 tool definition 强制 schema，等效于 enum 约束。

**Stable Diffusion Prompt-from-BoW**（[`github.com/damtharsh/prompt_generator`](https://github.com/damtharsh/prompt_generator)）：把 prompt 拆为 bag-of-tags，从词表采样组合——这种"tag 拼接"思路是新海诚风格 SD 社区的工业级实践。

## 落地建议

新增 `backend/app/services/background_style_classifier_service.py`：

```python
class BackgroundStyleClassifierService:
    def __init__(self, llm_client, profiles, cache):
        self._llm = llm_client
        self._profiles = profiles  # background_style_taxonomy
        self._cache = cache

    async def classify(self, spec: BackgroundSceneSpec, genre: str) -> list[StyleTag]:
        cache_key = self._cache_key(spec, genre)
        if cached := self._cache.get(cache_key):
            return cached
        try:
            raw = await self._call_llm(spec, genre)
            tags = [StyleTag(**t) for t in raw["style_tags"]]
            self._validate_coverage(tags)  # ≥3 dimensions
            self._validate_no_character_tags(tags)  # 不得含人名/动作
            self._cache.set(cache_key, tags)
            return tags
        except (TimeoutError, JSONDecodeError, ValidationError):
            return self._deterministic_fallback(spec, genre)

    async def classify_many(self, specs, genre):
        # 见 B08
        ...
```

`StyleTag` schema（在 `schemas.py`）：

```python
class StyleTag(BaseModel):
    dimension: Literal["art_style", "color_palette", "lens_or_camera_feel", "texture_or_rendering", "mood"]
    value: str  # 必须从 background_style_taxonomy[dimension] 中选
```

调用入口：在 `asset_management_service.generate_chapter_backgrounds` 中，每个 spec 拿到后调 `classifier.classify(spec, genre)`，把返回的 tags 写回 `spec.style_tags`。

## 风险与权衡

1. **新增 LLM 调用成本**：每章 1-4 scene × 1 调用 ≈ 0.001-0.004 USD。权衡：可用 batch 模式（B08）压缩到 1 次调用。
2. **分类器输出与 spec 重复**：spec 里已经有 atmosphere/lighting 字段，classifier 又输出 mood tag。权衡：classifier 只选"受控词表的标准化词"，与 spec 自由文本字段不冲突——前者进 negative+positive prompt 的固定段，后者进 description 段。

## 完成判据自检

- ✅ 围绕"style classifier service 骨架"，不跑题到 taxonomy 内容（B02）。
- ✅ 引用 OpenAI function calling、Anthropic tool use、SD Prompt-from-BoW。
- ✅ 落到 `backend/app/services/background_style_classifier_service.py` 和 `schemas.py::StyleTag`。
- ✅ 符合哲学：受控词表分类，不引入新模型供应商。
