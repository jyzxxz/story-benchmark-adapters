# vn_graph_generator.py — learn note

> Source: `backend/app/services/vn_graph_generator.py` (373 LOC)
> Route: `high_reasoning` | Reuse target: **P3.1 在此基础上扩字段，不重写**
> Status: `[_]` → master-validated `[x]`

## 职责
规则版 VNGraph 生成。输入章节正文 + characters + scene + visual_assets + story_bible + chapter_outline，输出符合 VNGraph schema 的 JSON dict。

## ⭐ 入口签名
```python
def generate_vn_graph_from_chapter(
    project_id: int,
    chapter_index: int,
    chapter_content: str,
    characters: List[dict],
    scene: str,
    visual_assets: List[dict],
    story_bible: dict,
    chapter_outline: dict,
) -> dict:
```

## P3.1 改造点
节点 schema 现在只引用图片。P3 要给 dialogue/narration 节点加：
- `audio_url: Optional[str]`
- `voice_line_id: Optional[int]`

根 meta 加：
- `chapter_index: int`
- `chapter_title: str`
- `cover_image: Optional[str]`（取章节第一张背景图）
- `total_audio_sec: float`
- `asset_manifest: dict`（一键导出资源 URL 列表）

**所有新字段必须 Optional**，旧 vngraph JSON 仍能解析。

## ⭐ 节点结构（VNGraph schema）
- `Version`、`StartNodeIndex`、`Nodes[]`
- 每个 Node 有 `Index`、`NodeType`（1=Progress, 2=Action）、`NextNodeIndex`、`Text`/`Background`/`Character` 引用。

P3.1 在 Progress（叙事）和 Action（对白）节点上加 `audio_url`。

## worker 提示
- 这个 generator 是「规则版」（快但简单），还有 `llm_vn_graph_generator_simplified.py`（LLM 版）。P3.1 两个文件都要改。
- P3.2 `vn_graph_assembler` 不重写 generator，只读它输出的 dict 然后回填 audio_url。
