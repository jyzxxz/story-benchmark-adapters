# E02 — background-forbidden-chars-multi-source

## 背景

`forbidden_characters` 当前只来自 `outline.characters`，遗漏：
- StoryBible.characters 中的英文别名 / nickname
- SceneSegment.characters_present（章节内出现的角色）
- 章节正文中命中的角色名（特别是别名）

导致 LLM 生成的图可能含命名角色（因为该角色名未在 forbidden 列表里）。

新设计要求 **4 源合并** + 别名命中扫描。

## SOTA 实践

**Aho-Corasick 多模式匹配**（[`github.com/abusix/ahocorasick`](https://github.com/abusix/ahocorasick)）：O(n) 时间复杂度扫多关键词，工业级实体识别工具。

**Hugging Face tokenization for entity matching**（[`huggingface.co/docs/tokenizers`](https://huggingface.co/docs/tokenizers)）：基于子词的实体匹配，对中文别名友好。

**spaCy EntityRuler**（[`spacy.io/api/entityruler`](https://spacy.io/api/entityruler)）：基于规则的多源实体合并，支持 alias。

## 落地建议

`asset_management_service.py::_collect_forbidden_characters`：

```python
def _collect_forbidden_characters(
    self,
    outline: ChapterOutline,
    specs: list[BackgroundSceneSpec],
    story_bible: StoryBible,
    chapter_content: str,
) -> list[str]:
    forbidden_set = set()

    # Source 1: outline.characters
    for char in outline.characters or []:
        forbidden_set.add(char.strip())

    # Source 2: SceneSegment.characters_present（specs 内）
    for spec in specs:
        for char in spec.forbidden_characters or []:
            forbidden_set.add(char.strip())

    # Source 3: StoryBible.characters（name + english_name + aliases + nicknames）
    for char in story_bible.characters or []:
        if char.name:
            forbidden_set.add(char.name.strip())
        if char.english_name:
            forbidden_set.add(char.english_name.strip())
        for alias in char.aliases or []:
            forbidden_set.add(alias.strip())
        for nick in char.nicknames or []:
            forbidden_set.add(nick.strip())

    # Source 4: chapter_content 实体命中（兜底，捕获正文里出现的别名）
    # 用简单的 substring 扫描（不引入 Aho-Corasick 依赖，避免 build 复杂度）
    additional = set()
    for char in story_bible.characters or []:
        candidates = []
        if char.name:
            candidates.append(char.name)
        candidates.extend(char.aliases or [])
        candidates.extend(char.nicknames or [])
        for cand in candidates:
            if cand and cand in chapter_content:
                additional.add(cand.strip())
    forbidden_set.update(additional)

    # 过滤空字符串和噪声
    return sorted(c for c in forbidden_set if c and len(c) >= 2)
```

**性能优化**：
- 章节 content 通常 5-20k 字，substring 扫 ~50 个角色 × 5k 字 = 250k 比较，~50ms（可接受）
- 若项目角色 > 200 个，考虑 Aho-Corasick（pyahocorasick 库）

**输出格式**（保留中英双版本，见 E03）：

```python
# 把每个角色整理为 (cn_name, en_name) 元组
forbidden_pairs = []
for char_name in forbidden_set:
    en = self._lookup_english_name(char_name, story_bible)
    forbidden_pairs.append((char_name, en))
```

## 风险与权衡

1. **角色别名过多导致 prompt 膨胀**：100 个别名 × 2 字 ≈ 200 字进 negative clause。权衡：限制 forbidden_chars ≤ 30，超出按出现频率取前 30。
2. **substring 命中误伤**：例如角色名 "苏墨" 在正文出现"墨水"被误命中。权衡：只匹配完整词（用 `\b` 或中文边界）；或要求候选名长度 ≥2 字。

## 完成判据自检

- ✅ 围绕"forbidden_chars 4 源合并"，不跑题到具体 prompt 拼接（E03）。
- ✅ 引用 Aho-Corasick、Hugging Face tokenizers、spaCy EntityRuler。
- ✅ 落到 `asset_management_service.py::_collect_forbidden_characters`。
- ✅ 符合哲学：多源合并保证不漏角色，不引入新模型供应商；保证背景无命名角色。
