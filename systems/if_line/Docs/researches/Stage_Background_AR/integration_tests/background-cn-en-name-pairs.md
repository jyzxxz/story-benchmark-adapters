# E03 — background-cn-en-name-pairs

## 背景

CogView-4 同时支持中英文 prompt——中文模型对中文角色名识别敏感（看到"林夜"就尝试画），英文 negative clause 对英文别名敏感（"No Lin Ye" 才生效）。

`forbidden_characters` 需要在最终 prompt 中保留**中英双版本**：

```
Named cast exclusion (CN): 林夜, 苏晚晴, 赵峰
Named cast exclusion (EN): Lin Ye, Su Wanqing, Zhao Feng
```

## SOTA 实践

**Bilingual prompt engineering**（[`civitai.com/articles/4148`](https://civitai.com/articles/4148)）：SD 社区建议负向 prompt 同时含中英文，覆盖模型对两种语言的 attention。

**Google Multilingual BERT**（[`github.com/google-research/bert`](https://github.com/google-research/bert)）：多语言模型对实体识别需要双语对照——同思路适用图像生成。

**OpenAI DALL-E multilingual**（[`platform.openai.com/docs/guides/images`](https://platform.openai.com/docs/guides/images)）：DALL-E 3 对中英文 prompt 都敏感，建议关键约束双语标注。

## 落地建议

`_enforce_background_environment_focus`（C02）的 Named cast exclusion 段：

```python
def _format_named_cast_exclusion(self, forbidden_pairs: list[tuple[str, str]]) -> str:
    cn_names = [cn for cn, en in forbidden_pairs if cn and self._is_chinese(cn)]
    en_names = [en for cn, en in forbidden_pairs if en and not self._is_chinese(en)]

    parts = []
    if cn_names:
        parts.append(f"Named cast exclusion (CN): {', '.join(cn_names)}")
    if en_names:
        parts.append(f"Named cast exclusion (EN): {', '.join(en_names)}")
    return "\n".join(parts)

def _is_chinese(self, s: str) -> bool:
    return bool(re.search(r"[一-鿿]", s))
```

**输出示例**：

```
Named cast exclusion (CN): 林夜, 苏晚晴, 赵峰, 阿夜
Named cast exclusion (EN): Lin Ye, Su Wanqing, Zhao Feng
```

**assembler / enforce 调用**：

```python
prompt = assembler.assemble(spec, tags, forbidden_pairs=pairs)
# assembler 内部不处理 named cast；交由 enforce 处理
prompt = image_gen._enforce_background_environment_focus(
    spec, prompt, forbidden_pairs=pairs
)
```

**英文别名查找**（`_lookup_english_name`）：

```python
def _lookup_english_name(self, cn_name: str, story_bible: StoryBible) -> str:
    for char in story_bible.characters or []:
        if char.name == cn_name or cn_name in (char.aliases or []):
            return char.english_name or ""
    return ""
```

## 风险与权衡

1. **某些角色没有英文别名**：forbidden_pairs 中只有中文名。权衡：自动 fallback 用拼音（如"林夜" → "lin ye"）作为弱英文别名；或省略英文行（只保留中文）。
2. **双语 prompt 让 CogView-4 attention 分散**：权衡：保持 named cast exclusion 段独立于主 prompt，CogView-4 在 negative attention 段处理。

## 完成判据自检

- ✅ 围绕"中英双版本 named cast exclusion"，不跑题到 forbidden_chars 收集（E02）。
- ✅ 引用 SD bilingual prompt、Multilingual BERT、OpenAI DALL-E multilingual。
- ✅ 落到 `image_generation_service._format_named_cast_exclusion` 和 `_enforce_background_environment_focus`。
- ✅ 符合哲学：双语 prompt 适应 CogView-4 双语特性，不引入新模型供应商。
