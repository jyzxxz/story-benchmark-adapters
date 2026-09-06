# B03 — background-style-classifier-system-prompt

## 背景

style classifier 的 system prompt 必须强约束 LLM "只从词表选、不自造"。如果措辞松（"请尽量从以下词表选择"），LLM 仍会自由发挥，例如把"market"分类时输出 `"vibrant_bustling_morning"`——这种自造词直接破坏下游 assembler 的稳定性。

需要在 `image_generation_profiles.json::rewriter.background_style_classifier` 写一个硬规则化的 system prompt，并配 3 个 genre 的 few-shot。

## SOTA 实践

**OpenAI prompt engineering "Be clear and direct"**（[`platform.openai.com/docs/guides/prompt-engineering/strategy-be-clear-and-direct`](https://platform.openai.com/docs/guides/prompt-engineering/strategy-be-clear-and-direct)）：

> 硬约束用"必须 / 不得"等祈使句；软约束用"建议 / 尽量"——但生产系统应最大化硬约束比例。

**Anthropic XML-tagged system prompt**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags)）：

> 用 `<AllowedTags>` 包裹词表，让模型在视觉上明确边界。

**SD community "tag-only prompting"**（[`civitai.com/articles/3097`](https://civitai.com/articles/3097)）：强调"用预定义 tag 而非自由描述"是稳定出图的关键。

## 落地建议

`image_generation_profiles.json::rewriter.background_style_classifier`：

```json
{
  "model": "${SEGMENTER_MODEL}",
  "temperature": 0.1,
  "max_tokens": 600,
  "system_prompt": "<Identity>你是背景风格分类器。</Identity>\n<Rules>\n1. 只从 <AllowedTags> 提供的词表中选标签，每维最多 1 个\n2. 至少覆盖 3 个维度（art_style / color_palette / lens / texture / mood）\n3. 严禁自造词或拼接词（如 vibrant_bustling 不允许）\n4. 严禁输出人名、动作、对白、角色关系\n5. 不得修改 scene_name / scene_selector / people_policy\n6. 输出严格 JSON: {\"style_tags\": [{\"dimension\": \"...\", \"value\": \"...\"}]}\n</Rules>\n<AllowedTags>{注入 background_style_taxonomy}</AllowedTags>\n<Examples>\n[3 个 genre few-shot]\n</Examples>",
  "response_format": {"type": "json_object"}
}
```

**3 genre few-shot**：

```yaml
genre: historical
input_scene:
  scene_type: war_camp
  atmosphere: 萧索
  time_of_day: night
expected_output:
  style_tags:
    - {dimension: art_style, value: 写实国风电影感}
    - {dimension: color_palette, value: 暮色紫灰}
    - {dimension: lens_or_camera_feel, value: 高机位远景}
    - {dimension: mood, value: 萧索}

genre: modern
input_scene:
  scene_type: street
  time_of_day: night
  weather: rain
expected_output:
  style_tags:
    - {dimension: art_style, value: 写实电影感}
    - {dimension: color_palette, value: 雨夜霓虹}
    - {dimension: texture_or_rendering, value: 潮湿反光}
    - {dimension: mood, value: 潮湿阴冷}

genre: scifi
input_scene:
  scene_type: temple_interior
  atmosphere: 压迫
expected_output:
  style_tags:
    - {dimension: art_style, value: 赛博朋克}
    - {dimension: color_palette, value: 金属蓝}
    - {dimension: lens_or_camera_feel, value: 室内一体化广角}
    - {dimension: mood, value: 压迫}
```

## 风险与权衡

1. **temperature=0.1 仍可能漂移**：弱模型偶发自造词。权衡：后置校验 `_validate_in_taxonomy` 强制拒绝非词表词，触发 deterministic fallback。
2. **system prompt 太长（含完整词表）→ token 成本**：5 维 × 5-10 词 × 5 genre ≈ 200 词。权衡：词表压缩为逗号分隔单行，避免每词一行。

## 完成判据自检

- ✅ 围绕"system prompt 设计"，不跑题到词表内容（B02）或 LLM 调用（B05）。
- ✅ 引用 OpenAI / Anthropic prompt engineering 指南、SD tag-only prompting。
- ✅ 落到 `image_generation_profiles.json::rewriter.background_style_classifier`。
- ✅ 符合哲学：硬规则约束 LLM，不引入新模型供应商。
