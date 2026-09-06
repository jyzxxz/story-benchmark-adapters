# E05 — background-vn-graph-scene-selector-matching

## 背景

`LLMVNGraphGenerator`（`backend/app/services/llm_vn_graph_generator.py`）当前按 `Asset.scene_location` 匹配背景资产——这是自由文本字段，匹配易漂移。

新设计改为按 `scene_selector`（E04 生成的稳定 slug）匹配。但需要容错——LLM 生成的 vn_graph 节点可能用 `war_camp__night__rain__empty` 匹配场景图，但生成的图可能是 `war_camp__night__clear__empty`，需要前缀/fuzzy 匹配。

## SOTA 实践

**Elasticsearch fuzzy matching**（[`www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-fuzzy-query.html`](https://www.elastic.co/guide/en/elasticsearch/reference/current/query-dsl-fuzzy-query.html)）：基于编辑距离的模糊匹配，工业级搜索实践。

**Python `difflib.SequenceMatcher`**（[`docs.python.org/3/library/difflib.html`](https://docs.python.org/3/library/difflib.html)）：标准库 fuzzy match，无需引入依赖。

**Postgres trigram (pg_trgm)**（[`www.postgresql.org/docs/current/pgtrgm.html`](https://www.postgresql.org/docs/current/pgtrgm.html)）：DB 层 fuzzy match。

## 落地建议

`llm_vn_graph_generator.py` 中匹配函数改造：

```python
def _match_background_asset(
    self,
    scene_selector_query: str,
    candidates: list[Asset],
) -> Optional[Asset]:
    """按 scene_selector 匹配背景资产，支持前缀 + fuzzy"""
    if not scene_selector_query:
        return None

    # Step 1: 精确匹配
    exact = [a for a in candidates if a.variation_info.get("scene_selector") == scene_selector_query]
    if exact:
        return exact[0]

    # Step 2: 前缀匹配（query 是候选的前缀，或反之）
    prefix_matches = []
    for a in candidates:
        sel = a.variation_info.get("scene_selector", "")
        if sel.startswith(scene_selector_query) or scene_selector_query.startswith(sel):
            prefix_matches.append(a)
    if prefix_matches:
        # 多个匹配时取最长的（最具体）
        return max(prefix_matches, key=lambda a: len(a.variation_info.get("scene_selector", "")))

    # Step 3: fuzzy 匹配（SequenceMatcher ratio >= 0.7）
    fuzzy_matches = []
    for a in candidates:
        sel = a.variation_info.get("scene_selector", "")
        if not sel:
            continue
        ratio = difflib.SequenceMatcher(None, scene_selector_query, sel).ratio()
        if ratio >= 0.7:
            fuzzy_matches.append((ratio, a))
    if fuzzy_matches:
        fuzzy_matches.sort(key=lambda x: -x[0])
        return fuzzy_matches[0][1]

    # Step 4: fallback 到 scene_type 匹配
    target_type = scene_selector_query.split("__")[0]
    type_matches = [a for a in candidates
                    if a.variation_info.get("scene_type") == target_type]
    return type_matches[0] if type_matches else None
```

**Asset.variation_info 写入**（见 E06）：每个背景 asset 持久化时把 `scene_selector / scene_type / time_of_day / weather / people_policy.mode` 都写入 `variation_info` JSON。

## 风险与权衡

1. **fuzzy 阈值 0.7 可能误匹配**：例如 `war_camp__night__rain__empty` 和 `war_camp__night__clear__empty` ratio ≈ 0.85，可能匹配错。权衡：在 fuzzy 之上加 scene_type 严格相等检查；同时优先级 exact > prefix > fuzzy > type。
2. **variation_info 字段缺失**：旧 asset（重构前生成）没有 scene_selector。权衡：旧 asset 走 `Asset.scene_location` 匹配作为 fallback；新 asset 优先用 scene_selector。

## 完成判据自检

- ✅ 围绕"vn_graph scene_selector 匹配"，不跑题到 slug 生成（E04）。
- ✅ 引用 Elasticsearch fuzzy、Python difflib、Postgres pg_trgm。
- ✅ 落到 `llm_vn_graph_generator.py::_match_background_asset`。
- ✅ 符合哲学：稳定 slug + 多层容错匹配，不引入新模型供应商；保证 vn_graph 正确指背景。
