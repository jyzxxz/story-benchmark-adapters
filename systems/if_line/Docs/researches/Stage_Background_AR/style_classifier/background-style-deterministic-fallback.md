# B06 — background-style-deterministic-fallback

## 背景

classifier LLM 调用失败时（超时 / JSON 解析失败 / 词表违规 / 覆盖不足），必须有 deterministic fallback——不能让整章背景生成卡死。

但 fallback 不能输出空 `style_tags=[]`，否则 assembler 拼出的 prompt 又回到"无 art_style 默认中性灰"问题。需要按 `(genre, scene_type)` 从配置里查默认 tags。

## SOTA 实践

**Default profile / preset pattern**（[`refactoring.guru/design-patterns/singleton`](https://refactoring.guru/design-patterns/singleton) 例）：fallback 用预定义的 preset，保证最低质量底线。

**Stable Diffusion community "fallback prompt"**（[`github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features`](https://github.com/AUTOMATIC1111/stable-diffusion-webui/wiki/Features)）：每个 category 都有"默认 prompt"——人像默认"masterpiece, best quality, 1girl"，风景默认"scenery, no humans, cinematic"。这是工业级 fallback 实践。

## 落地建议

`image_generation_profiles.json::background_style_fallback`：

```json
"background_style_fallback": {
  "historical": {
    "default": [
      {"dimension": "art_style", "value": "写实国风电影感"},
      {"dimension": "color_palette", "value": "青灰冷调"},
      {"dimension": "lens_or_camera_feel", "value": "35mm环境广角"},
      {"dimension": "mood", "value": "萧索"}
    ],
    "by_scene_category": {
      "indoor_public": [
        {"dimension": "art_style", "value": "古风厚涂"},
        {"dimension": "color_palette", "value": "暖金棕"},
        {"dimension": "lens_or_camera_feel", "value": "室内一体化广角"},
        {"dimension": "mood", "value": "壮阔"}
      ],
      "outdoor_natural": [
        {"dimension": "art_style", "value": "宋代山水"},
        {"dimension": "color_palette", "value": "墨色为主"},
        {"dimension": "lens_or_camera_feel", "value": "高机位远景"},
        {"dimension": "mood", "value": "静谧"}
      ]
    }
  },
  "modern": { "default": [...] },
  "scifi": { "default": [...] },
  "fantasy": { "default": [...] },
  "anime": { "default": [...] }
}
```

service 代码：

```python
def _deterministic_fallback(self, spec: BackgroundSceneSpec, genre: str) -> list[StyleTag]:
    fb_cfg = self._profiles["background_style_fallback"].get(genre, {})
    category = self._classify_scene_category(spec.scene_type)
    by_cat = fb_cfg.get("by_scene_category", {})
    raw_tags = by_cat.get(category) or fb_cfg.get("default", [])

    if not raw_tags:
        # 终极兜底：返回通用 tags
        raw_tags = [
            {"dimension": "art_style", "value": "写实电影感"},
            {"dimension": "mood", "value": "静谧"},
            {"dimension": "lens_or_camera_feel", "value": "35mm环境广角"},
        ]

    tags = [StyleTag(**t) for t in raw_tags]
    self._log("style_fallback_used", genre=genre, scene_type=spec.scene_type)
    return tags
```

## 风险与权衡

1. **fallback tags 不能根据具体场景微调**：例如古风"雨夜战场"用 default，颜色不会自动调成"雨夜蓝黑"。权衡：fallback 是底线，不是最优；只要 art_style 和 mood 正确，CogView-4 自己会按 description 段调色。
2. **fallback 频繁触发说明 classifier LLM 不稳**：监控 fallback 频率 >10% 应升级 model 或调 system prompt。

## 完成判据自检

- ✅ 围绕"deterministic fallback"，不跑题到 cache 或 batch。
- ✅ 引用 default profile pattern、SD community fallback prompt。
- ✅ 落到 `image_generation_profiles.json::background_style_fallback` 和 `_deterministic_fallback`。
- ✅ 符合哲学：保证可用性，复用受控词表，不引入新模型供应商。
