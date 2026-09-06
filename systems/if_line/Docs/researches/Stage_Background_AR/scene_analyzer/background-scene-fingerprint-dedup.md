# A06 — background-scene-fingerprint-dedup

## 背景

当前 `asset_management_service.py::generate_chapter_backgrounds` 按 `segment.location` 去重——同一 location 在一章内只生成 1 张背景。这丢失了"同地点但日夜/天气/状态变化"这种**视觉上明显不同**的背景需求。

例如：本章先在太守府白天议事，夜里太守府遇袭火光冲天——这是视觉上完全不同的两张背景（白天 vs 燃烧），但按 location 去重会压成一张。

新链路必须按 **scene_fingerprint** 去重——`(location, time_of_day, weather, crowd_state, physical_state)` 五元组——让"同地点但可视环境变化"被识别为不同 scene。

## SOTA 实践

**Perceptual hashing**（pHash / dHash / aHash）在图像去重领域（[`github.com/JohannesBuchner/imagehash`](https://github.com/JohannesBuchner/imagehash)）的思路：把"什么算同一张图"形式化为"哪些维度必须相同"。把同样思路用到场景指纹上——"什么算同一背景"= 5 个可视维度都相同。

**Stable Diffusion prompt matrix**（[`github.com/AUTOMATIC1111/stable-diffusion-webui`](https://github.com/AUTOMATIC1111/stable-diffusion-webui)）的 XY plot 也是按"变与不变"的维度切分 prompt，证明分维度比较比整体字符串比较更稳定。

## 落地建议

`asset_management_service.py::generate_chapter_backgrounds` 改造：

```python
def _scene_fingerprint(spec: BackgroundSceneSpec) -> str:
    parts = [
        spec.scene_type,  # 主要 location 类型
        spec.time_of_day,
        spec.weather,
        spec.people_policy.mode,  # crowd_state proxy
        _physical_state_token(spec),  # tidy / wrecked / burning / flooded / normal
    ]
    return "|".join(p.strip().lower() for p in parts if p)

def _dedup_scenes(specs: list[BackgroundSceneSpec]) -> list[BackgroundSceneSpec]:
    seen = {}
    for spec in specs:
        fp = _scene_fingerprint(spec)
        if fp not in seen:
            seen[fp] = spec
        # 同 fingerprint 的场景合并 evidence_spans，保留第一个 scene_id
        else:
            seen[fp].evidence_spans.extend(spec.evidence_spans[:2])
    return list(seen.values())
```

`_physical_state_token(spec)`：扫描 `spec.environment_description` 关键词（`burning / wrecked / flooded / overgrown / pristine`）映射到 token。空则默认 `normal`。

dedup 后 scene 数量预计 1-4，与现有 location 去重时的 1-2 相比略增，但每张图都是可视上独立的背景。

## 风险与权衡

1. **fingerprint 粒度过细 → scene 数爆炸**：如果 `time_of_day` 用了 `dawn / morning / noon / afternoon / dusk / night` 6 档，可能一章切 5 个时段。权衡：把 time_of_day 桶化为 `day / dusk / night` 三档进 fingerprint，描述字段保留细粒度。
2. **scene_type 与 location 字段冗余**：`scene_type` 是受控词表，`location` 是自由文本。权衡：fingerprint 只用 `scene_type`，避免 location 字符串抖动（"太守府中庭" vs "太守府庭院"）。

## 完成判据自检

- ✅ 围绕"fingerprint 去重"，不跑题到 dedup 后的 assembler。
- ✅ 引用 pHash 项目、SD prompt matrix。
- ✅ 落到 `asset_management_service.py::_dedup_scenes` 和 `_scene_fingerprint`。
- ✅ 符合哲学：分维度去重，不引入新模型供应商。
