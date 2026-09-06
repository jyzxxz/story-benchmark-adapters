# llm_vn_graph_generator_simplified.py — learn note

> Source: `backend/app/services/llm_vn_graph_generator_simplified.py` (17.9KB, ~430 LOC)
> Route: `high_reasoning` | Reuse target: **P3.1 同步扩字段**
> Status: `[_]` → master-validated `[x]`

## 职责
LLM 语义分析版 VNGraph 生成。比规则版更智能但更慢。

## ⭐ 入口签名（参考 `routers/vn_graph.py:135`）
```python
await llm_vn_graph_generator_simplified.generate_vn_graph(
    chapter_content=...,
    chapter_outline={chapter_index, title, summary, scene, emotion},
    story_bible=bible.raw_json,
    characters=bible.characters,
    visual_assets=[{asset_type, target_name, prompt}, ...],
    chapter_index=...,
    uuid=project_id,
)
```

## P3.1 改造点
与 `vn_graph_generator.py` 完全同步：dialogue/narration 节点加 `audio_url` / `voice_line_id`，根 meta 加 `chapter_index` / `cover_image` / `total_audio_sec` / `asset_manifest`。

## worker 提示
- 两个 vn_graph generator（规则版 + LLM 版）输出 schema 必须保持一致。P3.1 改一处，另一处也要改。
- 改完后跑 `backend/test_dual_mode_vn_graph.py` / `backend/test_llm_vn_graph.py`（已存在的测试）确保没破坏。
- 还有个 `_optimized` 版本（13.8KB），P3.1 也扫一眼是否同样需要改。
