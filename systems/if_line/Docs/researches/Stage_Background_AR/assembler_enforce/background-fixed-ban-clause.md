# C03 — background-fixed-ban-clause

## 背景

固定 ban clause 是 enforce 函数的核心内容。措辞决定 CogView-4 是否真的"听话"——例如 `no people` 和 `no human subject` 效果不同；`no centered figure` 比 `no character` 更精确。

需要通过对比测试选定最有效的 phrase 组合，并配置化进 `image_generation_profiles.json::background_negative_clauses`。

## SOTA 实践

**CogView-4 官方文档**（[`github.com/THUDM/CogView4`](https://github.com/THUDM/CogView4)）：负向 prompt 应包含具体可视化的"禁止概念"，避免抽象词。

**OpenAI DALL-E 3 prompt engineering**（[`platform.openai.com/docs/guides/prompt-engineering`](https://platform.openai.com/docs/guides/prompt-engineering)）：

> Negative prompts 应描述"不要画什么"，而不是"不要做什么"。

**SD community "negative prompt presets"**（[`civitai.com/articles/4148`](https://civitai.com/articles/4148)）：常用 negative phrase 列表，证明 `centered figure`、`lone figure`、`foreground person` 是高效禁人物措辞。

## 落地建议

`image_generation_profiles.json::background_negative_clauses`：

```json
"background_negative_clauses": {
  "base": "Environment-focused background art. Location establishing shot. The environment, architecture, landscape, props, lighting, weather, and atmosphere are the main subject. By default, no people. Never depict a named cast member. Never depict a single person as the subject. No human subject. No person as the focal point. No centered lone figure. No large foreground person. No prominent human figure. No portrait. No hero shot. No half-body or full-body character emphasis. No close-up face. No detailed costume focus. No eye contact with camera. No character-focused composition. No text. No watermark.",

  "empty_required": "Strictly empty environment: no people, no humans, no silhouettes, no pedestrians, no guards, no soldiers, no servants, no crowd. Empty environment only.",

  "background_people_optional": "If any humans appear, they must be tiny, distant, anonymous background groups only, never isolated, never central, never dominant.",

  "background_groups_required": "Background groups required for public activity atmosphere, but must be tiny, distant, anonymous, scattered extras — never a single prominent person, never central, never foreground.",

  "establishing_shot_enforcement": "Establishing shot composition: environment fills 90%+ of frame, no character-driven close-ups, no over-the-shoulder shots."
}
```

**phrase 选择依据**：
- `No human subject` 比 `no people` 更明确——明确"人物成为主体"才禁，比无差别禁人物更精确
- `No centered lone figure` 是 CogView-4 上禁"单人中央构图"的高效短语（SD 社区验证）
- `No large foreground person` 防"前景大人物"模式
- `No eye contact with camera` 防"角色与观众对视"——这是 portrait 模式的明显信号
- `No character-focused composition` 是兜底，覆盖未列举的人物主体化模式

## 风险与权衡

1. **negative prompt 过长 → CogView-4 attention 稀释**：>150 词的 negative 可能让模型 attention 漂到无关词。权衡：base clause 控制在 ~80 词；empty_required 子句独立，只在需要时追加。
2. **不同 model 版本对 phrase 反应不同**：CogView-4 vs CogView-3 措辞效果可能不同。权衡：保留 `model_specific_overrides` 节点，按 model 名切 phrase。

## 完成判据自检

- ✅ 围绕"ban clause 措辞选择"，不跑题到 enforce 函数实现（C02）。
- ✅ 引用 CogView-4 文档、OpenAI DALL-E 3 指南、SD negative prompt presets。
- ✅ 落到 `image_generation_profiles.json::background_negative_clauses`。
- ✅ 符合哲学：措辞精确化，不引入新模型供应商。
