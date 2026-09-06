# B08 — background-style-batch-call

## 背景

一章通常 1-4 scene，每个 scene 单独调 classifier 是 4 次 round-trip × 1.5s = 6s。如果改成 batch（一次 LLM 调用输出全部 4 spec 的 tags），可压缩到 ~2.5s（1 次 round-trip + 输出 token 略多）。

但 batch 模式有 schema 复杂度上升（输出是 `[{scene_id, style_tags}, ...]`）和单点失败影响全部 spec 的风险。

## SOTA 实践

**OpenAI cookbook "Batch processing"**（[`cookbook.openai.com/examples/batch_processing`](https://cookbook.openai.com/examples/batch_processing)）：单次请求处理多 input 显著降低平均延迟，但有 partial failure 风险。

**LLM parallel tool calling**（[`platform.openai.com/docs/guides/function-calling#parallel-function-calling`](https://platform.openai.com/docs/guides/function-calling#parallel-function-calling)）：模型一次输出多个 tool 调用，等效于 batch——这种"批量"在 OpenAI 文档中被推荐用于"高相似度子任务"。

## 落地建议

**方案 A：batch 模式（推荐用于 ≥3 scene）**

```python
async def classify_many(self, specs: list[BackgroundSceneSpec], genre: str) -> dict[str, list[StyleTag]]:
    cache_hits = {}
    pending = []
    for spec in specs:
        cached = self._cache_get(self._cache_key(spec, genre))
        if cached:
            cache_hits[spec.scene_id] = cached
        else:
            pending.append(spec)

    if not pending:
        return cache_hits

    if len(pending) == 1:
        # 单 spec 走原 classify 路径
        tags = await self.classify(pending[0], genre)
        cache_hits[pending[0].scene_id] = tags
        return cache_hits

    # batch：一次 LLM 调用输出全部
    try:
        raw = await self._call_llm_batch(pending, genre)
        result = {}
        for item in raw["scenes"]:
            tags = [StyleTag(**t) for t in item["style_tags"]]
            self._validate_coverage(tags)
            self._validate_in_taxonomy_all(tags, genre)
            result[item["scene_id"]] = tags
            self._cache_set(self._cache_key(
                next(s for s in pending if s.scene_id == item["scene_id"]), genre
            ), tags)
        result.update(cache_hits)
        return result
    except (TimeoutError, ValidationError):
        # batch 失败 → 降级到串行 classify
        for spec in pending:
            cache_hits[spec.scene_id] = await self.classify(spec, genre)
        return cache_hits
```

**batch LLM 调用** `_call_llm_batch`：

```python
async def _call_llm_batch(self, specs, genre) -> dict:
    user_prompt = json.dumps({
        "scenes": [{"scene_id": s.scene_id, "scene_spec": s.model_dump(exclude={"style_tags"})} for s in specs],
        "project_genre": genre,
    }, ensure_ascii=False)
    # ... 同 _call_llm 但 max_tokens 调大到 600 × len(specs)
```

batch 输出格式：

```json
{
  "scenes": [
    {"scene_id": "s1", "style_tags": [{"dimension": "art_style", "value": "..."}, ...]},
    {"scene_id": "s2", "style_tags": [...]}
  ]
}
```

## 风险与权衡

1. **batch schema 复杂 → LLM 偶发漏 scene_id**：例如输出 3 个 spec 但只给 2 个的 tags。权衡：每个 scene_id 必须在输出中出现，否则触发 fallback 到单 spec classify。
2. **batch 失败时整体降级**：1 个 spec 出错可能让整批降级。权衡：用 try/except 包裹 batch，失败时优雅降级到串行。

## 完成判据自检

- ✅ 围绕"batch vs 串行"，不跑题到单次 LLM 调用细节。
- ✅ 引用 OpenAI batch processing、parallel function calling。
- ✅ 落到 `background_style_classifier_service.py::classify_many` 和 `_call_llm_batch`。
- ✅ 符合哲学：用 batch 降低延迟，失败时优雅降级，复用现有 GLM 配置。
