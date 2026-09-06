# C01 — background-prompt-assembler-design

## 背景

新链路最关键的"反 free-form 重写"防线是 deterministic assembler。当前 `prompt_rewriter_service.to_cogview_prompt` 是 LLM 自由重写——即使 system prompt 强约束，输出顺序仍不可控、字段顺序漂移。

`BackgroundPromptAssembler` 必须是 **pure function**：输入 `(scene_spec, style_tags)`，输出固定结构英文 prompt。无 LLM 调用、无随机性、无副作用。

## SOTA 实践

**OpenAI image generation prompting guide**（[`platform.openai.com/docs/guides/prompt-engineering`](https://platform.openai.com/docs/guides/prompt-engineering)）：

> Image prompt 推荐顺序：subject → key details → composition → style → constraints。

**Anthropic prompt engineering "structure your prompt"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/use-xml-tags)）：

> 按固定顺序拼装 prompt 让模型 attention 分布稳定。

**SD community "structured prompt"**（[`github.com/new-tinker/prompt_studio`](https://github.com/new-tinker/prompt_studio)）：5 段固定顺序（主体 / 场景 / 风格 / 镜头 / 反向），证明 deterministic 顺序在多个 SD 模型上效果稳定。

## 落地建议

新增 `backend/app/services/background_prompt_assembler_service.py`：

```python
class BackgroundPromptAssembler:
    def __init__(self, profiles):
        self._profiles = profiles

    def assemble(
        self,
        spec: BackgroundSceneSpec,
        style_tags: list[StyleTag],
        forbidden_characters: list[str] = None,
    ) -> str:
        # 固定 9 段顺序
        sections = [
            self._section_scene_name(spec),
            self._section_environment_description(spec),
            self._section_architecture_props(spec),
            self._section_lighting_weather_time(spec),
            self._section_camera_shot(spec),
            self._section_composition(spec),
            self._section_people_policy(spec),
            self._section_style_tags(style_tags),
            self._section_ban_clause(spec, forbidden_characters or spec.forbidden_characters),
        ]
        return "\n\n".join(s for s in sections if s)

    def _section_scene_name(self, spec):
        return f"[Scene] {spec.scene_name} (selector: {spec.scene_selector})"

    def _section_environment_description(self, spec):
        return f"[Environment] {spec.environment_description}"

    def _section_architecture_props(self, spec):
        parts = []
        if spec.architecture:
            parts.append("Architecture: " + ", ".join(spec.architecture))
        if spec.props:
            parts.append("Props: " + ", ".join(spec.props))
        return "; ".join(parts)

    def _section_lighting_weather_time(self, spec):
        return (
            f"[Atmosphere] {spec.time_of_day}, {spec.weather}, lighting={spec.lighting}; "
            f"atmosphere={spec.atmosphere}"
        )

    def _section_camera_shot(self, spec):
        return f"[Camera] {spec.camera_shot_type}"

    def _section_composition(self, spec):
        if not spec.composition_constraints:
            return ""
        return "[Composition] " + "; ".join(spec.composition_constraints)

    def _section_people_policy(self, spec):
        mode = spec.people_policy.mode
        if mode == "empty_required":
            return "[People] Empty environment. No people, no humans, no silhouettes."
        elif mode == "background_people_optional":
            return "[People] Tiny distant anonymous background groups optional; never central."
        else:  # background_groups_required
            return "[People] Background groups required, but tiny, distant, anonymous, scattered only."

    def _section_style_tags(self, tags):
        grouped = {}
        for t in tags:
            grouped.setdefault(t.dimension, []).append(t.value)
        lines = [f"{dim}: {', '.join(vals)}" for dim, vals in grouped.items()]
        return "[Style] " + " | ".join(lines)

    def _section_ban_clause(self, spec, forbidden):
        # 见 C03
        return self._profiles["background_negative_clauses"]["base"] + (
            self._profiles["background_negative_clauses"]["empty_required"]
            if spec.people_policy.mode == "empty_required" else ""
        )
```

落到 `backend/app/services/background_prompt_assembler_service.py`，由 `image_generation_service.generate_background` 调用。

## 风险与权衡

1. **deterministic 拼接可能不如 LLM 自然**：例如 `[Scene] ... [Environment] ...` 标签格式生硬。权衡：CogView-4 对带标签的 prompt 容忍度高，分段标签反而稳定 attention。
2. **某 section 内容空时如何处理**：例如 `composition_constraints=[]`。权衡：返回空字符串，`assemble()` 末尾 `s for s in sections if s` 过滤空段。

## 完成判据自检

- ✅ 围绕"deterministic assembler 9 段顺序"，不跑题到 enforce 函数（C02）。
- ✅ 引用 OpenAI image gen guide、Anthropic XML 标签、SD structured prompt。
- ✅ 落到 `background_prompt_assembler_service.py::assemble`。
- ✅ 符合哲学：deterministic，不引入新模型供应商，不引入 VLM 闭环。
