# asset_management_service.py — learn note

> Source: `backend/app/services/asset_management_service.py` (46KB, ~1100 LOC)
> Route: `high_reasoning` | Reuse target: **P1.3 / P2.1 改造入口**
> Status: `[_]` → master-validated `[x]`

## 职责
Asset 表 CRUD + 批量生成入口编排。所有图像生成路由都先走这里。

## ⭐ 关键方法（P1/P2 改造点）

### `generate_all_portraits(project_id, variations, batch_size)` (L22)
当前流程：
1. `_get_story_bible(project_id)` 查角色卡。
2. `prompt_builder_service.build_portrait_prompts_async(story_bible, project_id, variations)` 把 variations 转成英文 prompt 列表。
3. 按 `batch_size` 切批并发调 `image_generation_service.generate_portrait`。
4. 命中已存在 Asset 直接 cache hit。
5. `stat_service.start_generation` / 记录 generation_time。

**P1.3 改造**：加 `auto_demand: bool = False` 参数。True 时：
```python
if auto_demand:
    from app.services.portrait_demand_analyzer import PortraitDemandAnalyzer
    variations = await PortraitDemandAnalyzer().analyze(project_id, all_chapter_indices, story_bible)
    # variations: List[{character_id, emotion, outfit, pose, source_chapter_index, rationale}]
```

### `generate_chapter_backgrounds_v2(project_id, chapter_index, moods)` (L955)
**P1 不动这里**，这是 v2 内容驱动范例。背景图已 LLM 分析章节正文。

### `generate_chapter_keyframes(project_id, chapter_index)` (L408)
当前走 `scene_segmenter_service.segment_chapter` 抽段。

**P1.3 改造**：在 segmenter 之前插入 `KeyframeMomentSelector.select(chapter_content, outline, max_keyframes)`，把 LLM 选出的关键时刻作为 keyframe 来源，替代/补充纯分段逻辑。

## ⭐ Asset 表落库模式（P2 复用）

P2 `voice_line` 落库完全照抄这里的 Asset 实例化模式：

```python
asset = Asset(
    project_id=project_id,
    chapter_index=chapter_index,
    asset_type="voice_line",          # 新增类型
    target_name=line.speaker_name or "narration",
    prompt=line.text,                  # 原文 text，P3 装配时按这个匹配 vngraph dialogue
    image_url=audio_url,               # 复用 image_url 字段存 audio URL
    character_id=line.character_id,
    emotion=line.emotion,
    status="completed",
)
db.add(asset)
```

## helper 复用
- `_get_story_bible(project_id)` / `_get_chapter_outline(project_id, chapter_index)` / `_get_chapter_content(project_id, chapter_index)` —— P2 直接调，不重写。
- `_dedup_scenes(specs)` —— P1 `PortraitDemandAnalyzer` 输出也可走类似 dedup by `(character_id, emotion, outfit, pose)`。

## worker 提示
- 这个文件 46KB，按方法 grep，不要通读。
- P1.3 改造时保留 `variations` 参数（向后兼容），只加 `auto_demand` 默认开。
- P2 voice_line 落库前先 check 是否已存在（同 `chapter_index + character_id + text`），避免重复合成。
