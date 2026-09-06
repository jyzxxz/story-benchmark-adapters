# A07 — background-scene-type-taxonomy

## 背景

`scene_type` 是 BackgroundSceneSpec 的关键字段——它驱动 `people_policy` 推断、style fallback 选择、validator 阈值选取。如果 scene_type 是 LLM 自由文本，这三个下游都会失稳。

当前 `image_generation_profiles.json` 有 `genre_styles` / `moods` / `lighting_presets` / `architecture_vocabulary` 等词表，但**没有 background scene type 词表**，导致 analyzer 输出的 scene_type 是自由字符串。

需要新增 `background_scene_taxonomy` 受控词表，覆盖古风/现代/科幻/奇幻/动漫 5 个 genre × 6-10 个场景原型。

## SOTA 实践

**OpenAI function calling enum**（[`platform.openai.com/docs/guides/function-calling`](https://platform.openai.com/docs/guides/function-calling)）：

> When the model must choose from a fixed vocabulary, declare the parameter as `enum`. The model will be physically unable to emit values outside the enum.

**WordNet / FrameNet** 的语义分类思想（[`wordnet.princeton.edu`](https://wordnet.princeton.edu)）：把开放语义空间桶化为有限类别，可显著提升下游规则匹配稳定性。

**Comic / VN 美术资源分类**（[`github.com/yui5ch/ArtPromptHelper`](https://github.com/yui5ch/ArtPromptHelper)）也是按"室内/室外/特殊"3 大类细分 ~30 原型，与新链路需求高度契合。

## 落地建议

`image_generation_profiles.json` 新增节：

```json
"background_scene_taxonomy": {
  "indoor_private": {
    "label": "私有室内",
    "default_people_policy": "empty_required",
    "members": ["bedroom", "study", "private_room", "kitchen", "bathroom"]
  },
  "indoor_public": {
    "label": "公共室内",
    "default_people_policy": "background_groups_required",
    "members": ["banquet_hall", "tavern", "temple_interior", "courtroom", "lobby"]
  },
  "outdoor_urban": {
    "label": "城市户外",
    "default_people_policy": "background_people_optional",
    "members": ["market", "street", "square", "gate", "harbor", "bridge", "alley"]
  },
  "outdoor_natural": {
    "label": "自然户外",
    "default_people_policy": "empty_required",
    "members": ["forest", "riverbank", "mountain", "cave", "meadow", "cliff"]
  },
  "outdoor_military": {
    "label": "军事户外",
    "default_people_policy": "background_people_optional",
    "members": ["battlefield", "marching_road", "war_camp", "training_ground", "watchtower"]
  },
  "special": {
    "label": "特殊/异空间",
    "default_people_policy": "empty_required",
    "members": ["dreamscape", "ruins", "void", "memory_space", "magical_realm"]
  }
}
```

每个 genre 在 `genre_overrides` 节点可重映射（例如"动漫"genre 下 `classroom` 加入 `indoor_public`，但"古风"genre 下没有 classroom）。

`BackgroundSceneSpec.scene_type` 必须从这个词表选值，由 Pydantic `Literal[...]` 或 `field_validator` 强制。

## 风险与权衡

1. **词表覆盖不全 → OOD 输入漂移**：例如"火车车厢"在古风里没有。权衡：每个 genre 词表预留 5-10% 的 `other` 兜底类，让 LLM 至少不报错；同时定期根据日志 review `scene_type` 分布扩词表。
2. **词表过细 → 选择疲劳**：30 个原型 vs 6 大类。权衡：分两层——`scene_category`（6 大类，进 fingerprint）+ `scene_type`（30+ 原型，自由描述）。

## 完成判据自检

- ✅ 围绕"scene_type 受控词表"，不跑题到 people_policy 或 style。
- ✅ 引用 OpenAI function calling enum、WordNet。
- ✅ 落到 `image_generation_profiles.json::background_scene_taxonomy`。
- ✅ 符合哲学：受控词表约束 LLM，不引入新模型供应商。
