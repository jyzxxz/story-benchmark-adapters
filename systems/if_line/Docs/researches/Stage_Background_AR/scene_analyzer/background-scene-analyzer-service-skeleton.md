# A01 — background-scene-analyzer-service-skeleton

## 背景

当前背景图链路的核心病灶是"输入对象错误"：`AssetManagementService.generate_chapter_backgrounds`（`backend/app/services/asset_management_service.py`）把 `ChapterOutline.summary` 直接喂给 `PromptBuilder.build_background_prompts_async`，而 summary 本质是"本章发生了什么故事"，CogView-4 拿到这种中文剧情文本就会画剧情插画而不是环境背景。

`PromptRewriter` 看到这种剧情摘要，会顺手把对白/动作/角色关系翻译进英文 prompt，污染加剧。即使后续 `_enforce_background_no_human_subject`（`image_generation_service.py`）做了禁人收口，也无法在 prompt 主体段清除剧情感。

因此需要新增一个独立服务 `BackgroundSceneAnalyzerService`，作为编排层与图像生成层之间的"环境场景抽取闸门"——它**只输出环境 schema，不输出图片 prompt**，让剧情文本到此为止。

## SOTA 实践

OpenAI 官方对"需要稳定遵循字段结构"的任务明确推荐使用 **Structured Outputs**（[`platform.openai.com/docs/guides/structured-outputs`](https://platform.openai.com/docs/guides/structured-outputs)）：

> Structured Outputs 是 JSON mode 的强化版，不仅保证 JSON 合法，还保证 schema adherence。

智谱 GLM 文档（[`open.bigmodel.cn/dev/api/normal-model/glm-4`](https://open.bigmodel.cn/dev/api/normal-model/glm-4)）支持 `response_format={"type": "json_object"}`，与 OpenAI 兼容；`glm-4.6` 进一步支持 function-calling 形式的 tool use，可作为升级路径。

Anthropic 官方 prompt engineering 指南（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview)）强调 "developer message 应明确分区 identity / instructions / examples / context，并在 system 层 invariant repetition"。

## 落地建议

新增文件 `backend/app/services/background_scene_analyzer_service.py`，骨架包含：

```python
class BackgroundSceneAnalyzerService:
    def __init__(self, llm_client, profiles, cache):
        self._llm = llm_client
        self._profiles = profiles  # image_generation_profiles.json
        self._cache = cache

    async def analyze_chapter(
        self, chapter_index: int, chapter_content: str,
        outline: ChapterOutline, story_bible: StoryBible,
        genre: str,
    ) -> list[BackgroundSceneSpec]:
        cache_key = self._cache_key(chapter_index, chapter_content, outline, story_bible)
        if cached := self._cache.get(cache_key):
            return cached
        try:
            raw = await self._call_llm(chapter_content, outline, story_bible, genre)
            specs = [BackgroundSceneSpec(**s) for s in raw["scenes"]]
            self._validate_no_plot(specs)
            self._infer_people_policy(specs)
            self._build_scene_selector(specs)  # deterministic slug
            self._cache.set(cache_key, specs)
            return specs
        except (TimeoutError, JSONDecodeError, ValidationError):
            return self._fallback_to_segmenter(chapter_index, chapter_content, outline)
```

调用入口走现有 `LLMService`（`backend/app/services/llm_service.py`），传入 `SEGMENTER_MODEL` 环境变量配置的 GLM 模型。

`asset_management_service.py::generate_chapter_backgrounds` 在切场景前先 `await analyzer.analyze_chapter(...)` 拿 `BackgroundSceneSpec[]`，再用它驱动后续 dedup/assembler/generator/validator。

## 风险与权衡

1. **新增 LLM 调用延迟与成本**：每章多 1 次 LLM round-trip（~3s, ~0.001 USD），可被 cache 抵消。如果项目方希望零延迟路径，可加 `BACKGROUND_ANALYZER_ENABLED=false` 跳过本服务，直接走旧 segmenter。
2. **Structured Outputs 在 GLM-4-flash 上稳定性弱于 glm-4.6**：建议生产用 glm-4.6，开发期可用 flash 省钱。需要在 `image_generation_profiles.json::rewriter.background_scene_extractor.model` 暴露可配置项。

## 完成判据自检

- ✅ 文档完全围绕"analyzer service 骨架"主题（A01），未跑题到 A02 schema 或 A03 system prompt。
- ✅ 引用 SOTA：OpenAI Structured Outputs、GLM 官方文档、Anthropic prompt engineering 指南。
- ✅ 落地到 `backend/app/services/background_scene_analyzer_service.py` 和 `asset_management_service.py::generate_chapter_backgrounds`。
- ✅ 符合哲学：复用 GLM、不替换 CogView-4、不引入 VLM-only 闭环（analyzer 是 LLM 文本任务）。
