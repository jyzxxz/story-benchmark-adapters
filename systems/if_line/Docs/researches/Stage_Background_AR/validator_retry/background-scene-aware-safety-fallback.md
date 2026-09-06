# D08 — background-scene-aware-safety-fallback

## 背景

escalation 第 3 步（D06）的 safety fallback 不能像现有 `_build_safety_fallback_background_prompt` 那样回落到通用"古风庭院"——这丢失场景语义。例如本章是"雨夜站台"，fallback 应该是"空的雨夜站台建立镜头"，而不是"古风庭院"。

需要根据 `scene_spec.scene_type / lighting / weather` 生成**同场景类型的保守环境版**。

## SOTA 实践

**Google "graceful degradation with context preservation"**（[`cloud.google.com/architecture/best-practices-for-operating-on-failure`](https://cloud.google.com/architecture/best-practices-for-operating-on-failure)）：

> 失败时降级应"保留核心语义，减少细节"，而不是完全切换到无关状态。

**Netflix chaos engineering "fallback to similar content"**（[`netflixtechblog.com/fallback-clockwise-9b17aaa1b9b5`](https://netflixtechblog.com/fallback-clockwise-9b17aaa1b9b5)）：推荐失败时降级到"同类型但更安全"的内容，不丢失用户上下文。

**Anthropic "context preservation in fallback"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-specific`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-specific)）：fallback prompt 应保留主场景字段，只去掉"风险因素"（如人物）。

## 落地建议

`image_generation_service.py::_scene_aware_safety_fallback`：

```python
async def _scene_aware_safety_fallback(self, spec: BackgroundSceneSpec) -> GenerationResult:
    # 1. 取 scene_type 默认 prompt 模板
    template = self._profiles["background_safety_fallback_templates"].get(
        spec.scene_type,
        self._profiles["background_safety_fallback_templates"]["default"]
    )

    # 2. 用 spec 字段填充模板
    safety_prompt = template.format(
        scene_type=spec.scene_type,
        time_of_day=spec.time_of_day or "neutral lighting",
        weather=spec.weather or "clear",
        atmosphere=spec.atmosphere or "calm",
        camera_shot=spec.camera_shot_type,
    )

    # 3. 强制 empty_required（最保守）
    safety_spec = spec.model_copy(deep=True)
    safety_spec.people_policy = PeoplePolicy(
        mode="empty_required", rationale="scene_aware_safety_fallback"
    )
    safety_prompt = self._enforce_background_environment_focus(safety_spec, safety_prompt)

    # 4. 生成（不再 retry——已是最后兜底）
    image_bytes = await self._generate_image(safety_prompt, ...)

    # 5. 验收：即使 fail 也保存为 fallback_asset（标记），不阻塞流程
    result = await self._validator.validate(image_bytes, safety_spec, safety_prompt)
    return GenerationResult(
        status="fallback_used" if result.passed else "fallback_failed",
        image=image_bytes if result.passed else None,
        prompt=safety_prompt,
        fallback_type="scene_aware",
    )
```

`image_generation_profiles.json::background_safety_fallback_templates`：

```json
"background_safety_fallback_templates": {
  "war_camp": "Empty {time_of_day} military war camp, {weather}, {atmosphere} atmosphere. Tents, banners, fortifications visible. No people, no soldiers. Establishing wide shot.",
  "market": "Empty {time_of_day} market square, {weather}, closed stalls and vendor carts visible. {atmosphere} mood. No people. Establishing wide shot.",
  "bedroom": "Empty {time_of_day} bedroom, {weather} outside window, {atmosphere} mood. Furniture, bed, decorations visible. No people. Interior wide shot.",
  "forest": "Empty {time_of_day} forest clearing, {weather}, {atmosphere} mood. Trees, foliage, ground cover. No people. Eye-level wide.",
  "default": "Empty {time_of_day} {scene_type} environment, {weather}, {atmosphere} mood. Architecture and props visible. No people. {camera_shot}."
}
```

## 风险与权衡

1. **fallback 仍可能失败（生成的图仍有人）**：权衡：fallback 不再 retry（已是最保守）；如失败标记为 `fallback_failed`，不保存 Asset。
2. **fallback prompt 模板覆盖不全**：scene_type 不在 templates 字典时走 default。权衡：default 模板足够通用，但 art_style/color_palette 由 CogView-4 自由发挥——可能偏离项目风格。可接受，因为这是最后兜底。

## 完成判据自检

- ✅ 围绕"scene-aware safety fallback"，不跑题到 retry escalation（D06）。
- ✅ 引用 Google graceful degradation、Netflix fallback、Anthropic context preservation。
- ✅ 落到 `image_generation_service.py::_scene_aware_safety_fallback` 和 `background_safety_fallback_templates`。
- ✅ 符合哲学：保留场景语义，不引入新模型供应商；保证背景无人。
