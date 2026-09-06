# B07 — background-style-cache-strategy

## 背景

每次重生章节都会触发 classifier，单 spec 分类 ~1.5s。如果同 spec 在不同上下文重生（用户调 outline 但 scene 内容没变），重复分类浪费成本。

但 cache 不能完全按 spec 全字段 hash——某些字段变化（如 `style_tags` 自身）会导致循环。需要选择稳定字段做 hash key。

更关键的是：**taxonomy 版本升级时**，旧 cache 必须失效，否则升级等于没生效。

## SOTA 实践

**HTTP Cache-Control ETag**（[`developer.mozilla.org/en-US/docs/Web/HTTP/Headers/ETag`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/ETag)）：版本化 hash + 显式 invalidation 是 caching 通用最佳实践。

**OpenAI prompt caching**（[`platform.openai.com/docs/guides/prompt-caching`](https://platform.openai.com/docs/guides/prompt-caching)）：相同前缀自动缓存，但 taxonomy 升级需要手工 bump version。

**Redis cache versioning**（[`redis.io/docs/manual/client-side-caching/`](https://redis.io/docs/manual/client-side-caching/)）：cache key 包含 `version` 字段，升级时 bump version 让旧 key 自然失效。

## 落地建议

```python
def _cache_key(self, spec: BackgroundSceneSpec, genre: str) -> str:
    taxonomy_version = self._profiles.get("_version", "v1")
    # spec 序列化时排除 style_tags 自身（避免循环）
    spec_payload = spec.model_dump(exclude={"style_tags"})
    payload = {
        "v": taxonomy_version,
        "genre": genre,
        "spec_hash": sha256(
            json.dumps(spec_payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:24],
    }
    raw = json.dumps(payload, sort_keys=True)
    return "bg_style_classifier:" + sha256(raw.encode()).hexdigest()[:24]

CACHE_DIR = "backend/.cache/bg_style_classifier"

# 失效策略：
# 1. taxonomy_version 升级 → 全部旧 cache 失效
# 2. spec 字段变化（除 style_tags 外）→ 该 spec cache 失效
# 3. 显式 invalidate(spec_id) → 单条失效
```

`image_generation_profiles.json` 新增顶层 `_version` 字段：

```json
{
  "_version": "v2.1",
  "background_style_taxonomy": {...},
  ...
}
```

每次 taxonomy 词表调整，开发者手工 bump `_version`。

## 风险与权衡

1. **cache 命中率低**：spec 序列化包含 environment_description（自由文本），微调一字就 hash 变。权衡：cache 价值在于"完全相同 spec 重生时省 LLM 调用"——这种 case 在 validator retry 流程中频繁出现（同 spec 多次走 assembler + classifier）。
2. **磁盘 cache 膨胀**：同 A10，加 LRU。

## 完成判据自检

- ✅ 围绕"cache key + 版本失效"，不跑题到 LLM 调用细节。
- ✅ 引用 ETag、OpenAI prompt caching、Redis versioning。
- ✅ 落到 `background_style_classifier_service.py::_cache_key` 和 `image_generation_profiles.json::_version`。
- ✅ 符合哲学：复用现有 cache 模式，版本机制让升级可控。
