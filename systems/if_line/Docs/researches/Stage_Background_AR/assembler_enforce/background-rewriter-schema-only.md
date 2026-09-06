# C07 — background-rewriter-schema-only

## 背景

新链路要求 rewriter **只接收结构化 scene_spec 字段，不能看 outline.summary / segment_summary**。历史教训：rewriter 看到 summary 就把剧情翻译进 prompt。

`prompt_builder_service.build_background_prompts_async` 当前用 summary 兜底（"如果 scene 为空就用 summary 找关键词"）——这条兜底路径必须切除。

## SOTA 实践

**Anthropic "least privilege prompt context"**（[`docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-specific`](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/be-specific)）：

> 只给模型必需的最少上下文。多余信息会触发不必要的联想。

**OpenAI "context window discipline"**（[`platform.openai.com/docs/guides/prompt-engineering`](https://platform.openai.com/docs/guides/prompt-engineering)）：

> 如果某字段不参与决策，不要传给 LLM。

**Pydantic strict mode**（[`docs.pydantic.dev/latest/concepts/strict_mode/`](https://docs.pydantic.dev/latest/concepts/strict_mode/)）：用 schema 限制输入字段，禁止隐式多余字段。

## 落地建议

`prompt_builder_service.build_background_prompts_async` 改造：

```python
async def build_background_prompts_async(
    self,
    chapter_index: int,
    outline: ChapterOutline,
    chapter_content: str,
    story_bible: StoryBible,
    genre: str,
    specs: list[BackgroundSceneSpec],  # 新参数：由 analyzer 产出
) -> list[BackgroundPromptData]:
    prompts = []
    for spec in specs:
        # 不再从 outline.summary 兜底；spec 已含 environment_description
        env_hint = spec.environment_description
        # 若 env_hint 为空（fallback 路径），调 _distill_env_hint_from_outline（已存在）
        if not env_hint:
            env_hint = await self._distill_env_hint_from_outline(outline, spec.scene_type)
            spec.environment_description = env_hint

        # 构造 prompt_data：只传 spec 字段，不传 summary
        prompt_data = BackgroundPromptData(
            scene_id=spec.scene_id,
            scene_selector=spec.scene_selector,
            scene_name=spec.scene_name,
            environment_description=spec.environment_description,
            architecture=spec.architecture,
            props=spec.props,
            lighting=spec.lighting,
            weather=spec.weather,
            time_of_day=spec.time_of_day,
            atmosphere=spec.atmosphere,
            camera_shot_type=spec.camera_shot_type,
            composition_constraints=spec.composition_constraints,
            people_policy=spec.people_policy.mode,
            style_tags=spec.style_tags,
            forbidden_characters=spec.forbidden_characters,
            # 不传：summary, scene_summary, plot_text, dialogue
        )
        prompts.append(prompt_data)
    return prompts
```

**rewriter 入口**：

```python
# prompt_rewriter_service.to_cogview_prompt 改造
def to_cogview_prompt(self, prompt_data: BackgroundPromptData) -> str:
    # 只读 prompt_data 的 schema 字段，禁止访问 outline / summary / chapter_content
    sections = [
        prompt_data.scene_name,
        prompt_data.environment_description,
        ...
    ]
    return " ".join(sections)
```

**移除现有 summary 兜底**（`prompt_builder_service.py`）：

```python
# 旧代码（删除）：
# if not scene_text:
#     scene_text = outline.summary[:60]  # 剧情污染源
# if not visual_keywords:
#     visual_keywords = self._extract_keywords_from_summary(outline.summary)
```

## 风险与权衡

1. **spec.environment_description 为空时怎么办**：analyzer 失败 fallback 时 description 留空。权衡：用 `_distill_env_hint_from_outline` 兜底（已存在，调用 GLM 蒸馏 env_hint），不让 summary 进入。
2. **rewriter signature 改变**：旧调用方传 outline.summary，新签名拒收。权衡：build_background_prompts_async 的 caller（asset_management_service）必须先调 analyzer 拿 spec，再调 builder。

## 完成判据自检

- ✅ 围绕"rewriter schema-only 改造"，不跑题到 enforce 或 assembler。
- ✅ 引用 Anthropic least privilege、OpenAI context discipline、Pydantic strict mode。
- ✅ 落到 `prompt_builder_service.build_background_prompts_async` 和 `prompt_rewriter_service.to_cogview_prompt`。
- ✅ 符合哲学：切断剧情污染源，不引入新模型供应商。
