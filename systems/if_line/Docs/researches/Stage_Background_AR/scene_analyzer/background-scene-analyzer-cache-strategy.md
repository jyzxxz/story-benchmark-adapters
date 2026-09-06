# A10 — background-scene-analyzer-cache-strategy

## 背景

每次章节重生都会重新跑 analyzer LLM 调用，单次 ~3s + ~0.001 USD。如果同一章节多次重生（用户调整 outline、或重试 validator 失败），重复调用浪费成本和延迟。

现有 `prompt_rewriter_service._cache_key`（`backend/app/services/prompt_rewriter_service.py`）已建立 hash-based cache 模式，但只覆盖 rewriter 自身。analyzer 的输入（章节正文 + outline + story bible）字段更多，cache key 设计更复杂。

需要在 analyzer 内部加 cache 层，并确保：
1. 输入未变 → 命中
2. outline 微调（标点、空格）→ 不命中（避免假命中）
3. chapter content 改字 → 不命中

## SOTA 实践

**OpenAI prompt caching**（[`platform.openai.com/docs/guides/prompt-caching`](https://platform.openai.com/docs/guides/prompt-caching)）：自动缓存前缀相同的 prompt，但要求前缀**完全一致**（包括空格、标点）。本仓库需要更宽松的"语义同内容"缓存，必须用 hash。

**promptfoo eval framework**（[`github.com/promptfoo/promptfoo`](https://github.com/promptfoo/promptfoo)）：cache key 用 `sha256(input_json)` 标准做法，并对长文本做 normalize（去空格、去 BOM）后再 hash。

## 落地建议

```python
def _cache_key(self, chapter_index, chapter_content, outline, story_bible) -> str:
    normalize = lambda s: re.sub(r"\s+", " ", str(s or "").strip())
    payload = {
        "v": 1,  # cache schema 版本
        "chapter_index": chapter_index,
        "content_hash": sha256(normalize(chapter_content).encode()).hexdigest()[:16],
        "outline_hash": sha256(normalize(outline.model_dump_json()).encode()).hexdigest()[:16],
        "bible_hash": sha256(normalize(story_bible.model_dump_json()).encode()).hexdigest()[:16],
        "taxonomy_version": self._profiles.get("_version", "0"),
    }
    raw = json.dumps(payload, sort_keys=True)
    return "bg_scene_analyzer:" + sha256(raw.encode()).hexdigest()[:24]

# 持久化：磁盘文件，按 hash 命名
CACHE_DIR = "backend/.cache/bg_scene_analyzer"

def _cache_get(self, key):
    path = os.path.join(CACHE_DIR, key + ".json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            return [BackgroundSceneSpec(**s) for s in data]
        except Exception:
            return None
    return None

def _cache_set(self, key, specs):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, key + ".json")
    with open(path, "w") as f:
        json.dump([s.model_dump() for s in specs], f, ensure_ascii=False)
```

**失效策略**：
- 显式：`analyzer.invalidate(chapter_index)` 清该章 cache
- 隐式：taxonomy_version 升级 → 全部旧 cache 失效
- LRU：cache 目录超过 100MB 时按 mtime 清最旧

## 风险与权衡

1. **cache 假命中导致用户调整不生效**：用户微调 outline.summary 后期望重生，但 content_hash 未变 → 仍命中。权衡：outline 用 `model_dump_json()` 序列化后 hash，任何字段微调都会变 hash。
2. **磁盘 cache 文件膨胀**：长项目 100 章 × 100 重生 = 10k 文件。权衡：加 LRU + 上限 500MB。

## 完成判据自检

- ✅ 围绕"cache key 设计"，不跑题到 LLM 调用细节。
- ✅ 引用 OpenAI prompt caching、promptfoo。
- ✅ 落到 `background_scene_analyzer_service.py::_cache_key`、`_cache_get`、`_cache_set`。
- ✅ 符合哲学：复用现有 hash-based cache 模式，不引入新依赖。
