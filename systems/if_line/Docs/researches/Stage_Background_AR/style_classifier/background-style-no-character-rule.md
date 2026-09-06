# B09 — background-style-no-character-rule

## 背景

style classifier 的硬规则之一是"不得新增人物、动作、对白、角色关系"。如果 LLM 偶发输出 `{"dimension": "art_style", "value": "少女立绘风"}` 或 `{"dimension": "mood", "value": "战斗紧张"}`——这些 tag 都隐含人物主体，与"背景必须无人主体"哲学冲突。

需要在 service 层加 `_validate_no_character_tags`，检测 tag.value 中是否含人物相关词。

## SOTA 实践

**Content moderation keyword filter**（[`platform.openai.com/docs/guides/moderation`](https://platform.openai.com/docs/guides/moderation)）：用关键词列表做 fast-path moderation，是工业级 NLP 通行做法。

**WordNet / HowNet 中文语义分类**（[`open.hownet.org`](http://open.hownet.org/)）：把词汇按语义类（人物 / 动作 / 关系）分桶。

**SD "tag blacklist"**（[`github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#negative-prompt`](https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features#negative-prompt)）：tag-based 系统普遍用黑名单过滤不期望的标签。

## 落地建议

`image_generation_profiles.json::background_style_forbidden_keywords`：

```json
"background_style_forbidden_keywords": {
  "character_words": ["少女", "少年", "立绘", "portrait", "1girl", "1boy", "人物", "少女立绘"],
  "action_words": ["战斗", "奔跑", "跳跃", "挥剑", "战斗紧张", "fighting", "running"],
  "relationship_words": ["情侣", "师徒", "对峙", "对峙中", "lovers", "confrontation"],
  "dialogue_words": ["对话", "对白", "说笑", "dialogue", "conversation"]
}
```

service 校验：

```python
def _validate_no_character_tags(self, tags: list[StyleTag]):
    forbidden = self._profiles["background_style_forbidden_keywords"]
    for tag in tags:
        value_lower = tag.value.lower()
        for category, words in forbidden.items():
            for w in words:
                if w.lower() in value_lower:
                    raise StyleCharacterLeakError(
                        f"style tag '{tag.value}' contains forbidden word '{w}' "
                        f"(category={category}) — backgrounds must not imply characters"
                    )
```

校验失败 → `_deterministic_fallback(spec, genre)`。

## 风险与权衡

1. **黑名单覆盖不全**：LLM 可能输出 `"少女感青灰"`，"少女感" 不在词表但隐含人物。权衡：词表持续维护；同时强制 tag.value 必须在 `background_style_taxonomy` 词表中——词表本身就不含人物词，相当于双保险。
2. **误伤合法词**：例如 `"战斗遗迹"` 是合法 mood tag（描述战场氛围），但"战斗"在 action_words。权衡：黑名单用 substring 匹配 + 人工 review 词表，避免出现"合法词包含 forbidden substring"。

## 完成判据自检

- ✅ 围绕"无人物 tag 规则"，不跑题到 taxonomy 内容。
- ✅ 引用 OpenAI moderation、HowNet、SD tag blacklist。
- ✅ 落到 `background_style_classifier_service.py::_validate_no_character_tags` 和 `image_generation_profiles.json::background_style_forbidden_keywords`。
- ✅ 符合哲学：保证背景无人物主体，不引入新模型供应商。
