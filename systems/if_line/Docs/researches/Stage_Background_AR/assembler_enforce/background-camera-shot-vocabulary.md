# C09 — background-camera-shot-vocabulary

## 背景

`spec.camera_shot_type` 是 BackgroundSceneSpec 关键字段，决定 prompt 中镜头段措辞。当前 `image_generation_profiles.json` 没有受控 shot 词表，analyzer 可能输出自由文本（"近景特写" / "medium close-up"），破坏一致性。

需要 6 个标准 shot 类型，每个明确效果差异。

## SOTA 实践

**电影摄影 shot types**（[`www.studiobinder.com/blog/shot-types-in-film`](https://www.studiobinder.com/blog/shot-types-in-film/)）：行业标准 shot 分类——extreme wide / wide / medium / close-up / extreme close-up。

**SD "camera angle / shot" tags**（[`civitai.com/articles/4148`](https://civitai.com/articles/4148)）：常用 camera tag 包括 `wide shot` / `establishing shot` / `high angle` / `low angle` / `eye-level`。

**新海诚背景美术访谈**（[`www.kotaku.com.au/2016/09/animating-beautiful-backgrounds-makoto-shinkai-style/`](https://www.kotaku.com.au/2016/09/animating-beautiful-backgrounds-makoto-shinkai-style/)）：背景美术用 24-35mm 广角 + 高机位远景为多。

## 落地建议

`image_generation_profiles.json::background_camera_shots`：

```json
"background_camera_shots": {
  "establishing_wide_24mm": {
    "label": "24mm 广角建立镜头",
    "prompt_phrase": "24mm wide-angle establishing shot, expansive environment view, deep perspective",
    "use_for": ["market", "battlefield", "square", "harbor", "city_panorama"]
  },
  "environment_wide_35mm": {
    "label": "35mm 环境广角",
    "prompt_phrase": "35mm environment wide shot, balanced environment framing",
    "use_for": ["street", "courtyard", "temple_interior", "default"]
  },
  "high_angle_far": {
    "label": "高机位远景",
    "prompt_phrase": "high-angle far shot, bird's eye perspective emphasizing environment scale",
    "use_for": ["mountain", "war_camp", "ruins"]
  },
  "interior_wide": {
    "label": "室内一体化广角",
    "prompt_phrase": "interior integrated wide shot, single-point perspective showing full room",
    "use_for": ["bedroom", "study", "banquet_hall", "tavern"]
  },
  "long_compressed_far": {
    "label": "长焦压缩远景",
    "prompt_phrase": "long-lens compressed far shot, flattened depth, environment as backdrop",
    "use_for": ["dreamscape", "memory_space", "horizon_view"]
  },
  "eye_level_wide": {
    "label": "人眼水平广角",
    "prompt_phrase": "eye-level wide shot, neutral horizon, immersive environment view",
    "use_for": ["forest", "riverbank", "meadow", "natural_outdoor"]
  }
}
```

**Pydantic validator** 强制 `spec.camera_shot_type` 必须从这 6 个 key 中选：

```python
class BackgroundSceneSpec(BaseModel):
    camera_shot_type: Literal[
        "establishing_wide_24mm", "environment_wide_35mm",
        "high_angle_far", "interior_wide",
        "long_compressed_far", "eye_level_wide"
    ]
```

**default mapping by scene_category**：

```python
DEFAULT_SHOT_BY_CATEGORY = {
    "indoor_private": "interior_wide",
    "indoor_public": "interior_wide",
    "outdoor_urban": "environment_wide_35mm",
    "outdoor_natural": "eye_level_wide",
    "outdoor_military": "high_angle_far",
    "special": "long_compressed_far",
}
```

analyzer 输出 `camera_shot_type=""` 时，按 scene_category 查表自动填充。

## 风险与权衡

1. **6 个 shot 不够覆盖**：例如"低机位仰视巍峨建筑"。权衡：保留扩展空间，词表可加 `low_angle_grandeur`，但首版 6 个够用。
2. **不同 genre 对 shot 偏好不同**：动漫偏 wide，硬科幻偏 compressed far。权衡：genre_overrides 节点支持重映射 default shot。

## 完成判据自检

- ✅ 围绕"camera shot 词表"，不跑题到 style taxonomy 或 composition。
- ✅ 引用电影摄影 shot types、SD camera tags、新海诚背景美术。
- ✅ 落到 `image_generation_profiles.json::background_camera_shots` 和 `schemas.py::BackgroundSceneSpec.camera_shot_type`。
- ✅ 符合哲学：受控词表 + 默认映射，不引入新模型供应商。
