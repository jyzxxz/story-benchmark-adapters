# C05 — background-groups-allowed-subclause

## 背景

`background_people_optional` 和 `background_groups_required` 两档允许"远景匿名群体"，但必须严格防止：
- 单人成为主体
- 任何人物居中
- 任何人物前景大特写

这是最难设计的子句——既要让模型理解"可以有群体"，又要让它"绝不画单人主体"。

## SOTA 实践

**Anthropic "positive + negative constraints"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-clear-and-direct)）：

> 复杂约束应同时给"应该怎么做"和"不应该怎么做"的对照。

**电影摄影 "establishing shot with extras"**（[`www.studiobinder.com/blog/what-is-an-establishing-shot`](https://www.studiobinder.com/blog/what-is-an-establishing-shot/)）：远景人群作为"atmosphere extras"的标准摄影手法——必须在画面边缘、远景、模糊、无人特写。

**SD "crowd vs 1girl" negative discrimination**（[`civitai.com/articles/3097`](https://civitai.com/articles/3097)）：通过正负 prompt 区分"crowd"（允许）和"1girl"（禁止单人）。

## 落地建议

`background_negative_clauses` 中：

```json
"background_people_optional": "Background atmosphere: tiny distant anonymous groups optional for public space authenticity. If any humans appear, they must be: (a) far in the background, (b) small relative to environment scale, (c) visually subordinate, (d) never isolated, (e) never central, (f) never foreground. No single prominent person. No centered figure. No character in focus.",
"background_groups_required": "Public activity atmosphere: background groups required to convey public space authenticity. Required properties for any humans: tiny scale, distant placement, scattered distribution, anonymous (no faces, no individual features). Forbidden: single isolated person, centered figure, foreground person, character in focus, hero shot, portrait composition.",
"establishing_shot_extras_enforcement": "Crowd as texture only — never as subject. Environment occupies 80%+ of frame; crowd occupies <20% of frame, distributed at frame edges or far background."
}
```

**关键措辞**：
- "tiny distant anonymous groups" — 三个形容词同时出现，强化尺度感
- "never isolated" — 防"孤单一人"模式
- "never central / never foreground" — 防居中和前景
- "crowd as texture, never as subject" — 让模型把人群当背景纹理而非主体

**branch logic in `_enforce_background_environment_focus`**（见 C02）：

```python
if mode == "background_people_optional":
    parts.append(cfg["background_people_optional"])
    parts.append(cfg["establishing_shot_extras_enforcement"])
elif mode == "background_groups_required":
    parts.append(cfg["background_groups_required"])
    parts.append(cfg["establishing_shot_extras_enforcement"])
```

## 风险与权衡

1. **措辞太长 → 模型 attention 分散**：60+ 词的子句让模型困惑。权衡：拆分为多句短句（每句一个约束），用句号分隔。
2. **`groups required` 让模型画特写人群**：可能误画"3-5 人特写"。权衡：必须配 `establishing_shot_extras_enforcement`，强制远景视角。

## 完成判据自检

- ✅ 围绕"groups 子句设计"，不跑题到 empty_required（C04）。
- ✅ 引用 Anthropic positive+negative constraints、电影 establishing shot、SD crowd discrimination。
- ✅ 落到 `image_generation_profiles.json::background_negative_clauses` 和 `_enforce_background_environment_focus` branch。
- ✅ 符合哲学：精确措辞 + 多重约束，不引入新模型供应商。
