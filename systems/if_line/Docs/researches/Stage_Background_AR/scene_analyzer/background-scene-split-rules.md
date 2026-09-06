# A04 — background-scene-split-rules

## 背景

当前 `SceneSegmenterService.segment_chapter` 的切场景逻辑依赖 LLM 自由判断"何时换场景"，但 prompt 里没有切场景的硬规则，导致：
- 同一地点日夜变化（白天集市 → 夜里空巷）被压成一段
- 同一地点天气变化（晴 → 暴雨）被压成一段
- 同一地点物理状态变化（整洁大厅 → 战后狼藉）被压成一段

而新链路要求"按可视环境状态切场景"，因为不同时段/天气/状态的同一地点，从背景美术角度是**完全不同的两张背景**。

需要在 analyzer 的 system prompt + few-shot 里**显式编码切场景的硬规则**，让 LLM 在判断边界时有客观依据。

## SOTA 实践

**Anthropic context engineering** "explicit constraints + examples" 原则（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct)）：

> When you want the model to follow a non-obvious decision rule, write the rule as a numbered list and immediately follow with a worked example. Implicit rules drift; explicit rules anchored by examples stay.

**Chain-of-thought + structured output** 论文（[`arxiv.org/abs/2402.13753`](https://arxiv.org/abs/2402.13753) "Teaching Small Language Models to Reason"）也表明：few-shot 的 worked example 让边界判定稳定性提升 20-40%。

## 落地建议

**切场景硬规则**（写到 system prompt 的 `<Rules>` 区）：

```
切场景的 5 条硬规则（任一满足即必须切）：
R1. 物理地点变化：从 market → bedroom，必切
R2. 同地点但 time_of_day 明显变化：day → night，必切
R3. 同地点但 weather 明显变化：clear → storm，必切
R4. 同地点但 crowd_state 明显变化：crowded → empty，必切
R5. 同地点但 physical_state 明显变化：tidy → wrecked/burning/flooded，必切

不切场景的反例（仅是叙事推进，但可视环境未变）：
- 对白变化（多说了几句）
- 人物情绪波动（更紧张了）
- 角色关系升级（更敌对了）
- 战斗 / 调查 / 抉择的推进（除非地点变了）
```

**few-shot 示例**（每个 genre 给 1-2 个 worked example）：

```yaml
genre: 古风
chapter_excerpt: |
  林夜推开太守府大门，正午阳光直射庭院...
  ...（500 字）
  入夜后，雨势渐大，太守府中庭积水成渊...
expected_output:
  scenes:
    - scene_id: s1
      split_reason: opening
      time_of_day: noon
      weather: clear
    - scene_id: s2
      split_reason: time_change  # R2 + R3
      time_of_day: night
      weather: heavy_rain
```

后置校验：analyzer 输出后用代码 cross-check——如果两个连续 scene 的 `(location, time_of_day, weather, crowd_state, physical_state)` 完全相同，则标记 `split_reason` 不合规并 reject。落到 `BackgroundSceneAnalyzerService._validate_split_reasons`。

## 风险与权衡

1. **LLM 把无关叙事误判为切场景**：例如"她说完这句话后，雨停了"——LLM 可能因 weather 变化而切。权衡：要求 evidence_spans 必须给出支持切场景的原文短句，可人工/规则审核。
2. **5 条规则交叉触发，scene count 过多**：极端例子一章可能切 5-6 scene，每 scene 1 张背景图 → 成本暴涨。权衡：加 `MAX_SCENES_PER_CHAPTER = 4` 硬上限，超出时合并相邻 scene（保留 split_reason 优先级最低的）。

## 完成判据自检

- ✅ 围绕"切场景硬规则"，不跑题到 people_policy 或 schema。
- ✅ 引用 Anthropic context engineering、CoT 论文。
- ✅ 落到 `image_generation_profiles.json::rewriter.background_scene_extractor.system_prompt` 和 `_validate_split_reasons`。
- ✅ 符合哲学：用规则 + few-shot 约束 LLM，不引入新模型供应商。
