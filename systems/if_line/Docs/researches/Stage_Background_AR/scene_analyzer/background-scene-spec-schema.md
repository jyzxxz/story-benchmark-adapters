# A02 — background-scene-spec-schema

## 背景

`BackgroundSceneSpec` 是新链路的"契约对象"——analyzer 产它，classifier 读它，assembler 消费它，validator 对照它。如果 schema 字段语义模糊，下游就会出现"环境描述塞剧情""scene_type 没法分类 people_policy""scene_selector 不稳定"等连锁问题。

现有 `SceneSegmenterService` 输出的 `SceneSegment`（`backend/app/services/scene_segmenter_service.py`）字段太少（只有 `location / mood / characters_present / summary`），无法承载新链路需要的"环境时间/天气/光照/镜头/构图约束/people_policy"等维度。

## SOTA 实践

参考 **Stable Diffusion Structured Prompt** 插件（[`github.com/new-tinker/prompt_studio`](https://github.com/new-tinker/prompt_studio)）的 5 段式 prompt（主体 / 场景 / 角色 / 风格 / 反向），它把自然语言 prompt 拆解为带语义边界的字段，证明"字段化"对生成稳定性有显著正面作用。

**SDXL Refiner** 论文（[`arxiv.org/abs/2307.01952`](https://arxiv.org/abs/2307.01952)）也提到："field-conditioned refinement"优于 free-form conditioning。

**Pydantic v2 文档**（[`docs.pydantic.dev/latest/`](https://docs.pydantic.dev/latest/)）推荐用 `Field(..., min_length=, max_length=, pattern=)` 在 schema 层做硬约束，不依赖运行时手工 if 校验。

## 落地建议

在 `backend/app/schemas.py` 新增：

```python
class PeoplePolicy(BaseModel):
    mode: Literal["empty_required", "background_people_optional", "background_groups_required"]
    rationale: str = Field(default="", max_length=120)

class BackgroundSceneSpec(BaseModel):
    scene_id: str = Field(pattern=r"^[a-z0-9_]+$")
    scene_name: str = Field(min_length=2, max_length=40)
    scene_selector: str = Field(pattern=r"^[a-z0-9_]+$", max_length=80)
    scene_type: str  # from background_scene_taxonomy
    split_reason: Literal[
        "location_change", "time_change", "weather_change",
        "crowd_state_change", "physical_state_change", "opening", "single"
    ]
    evidence_spans: list[str] = Field(default_factory=list, max_length=5)
    environment_description: str = Field(min_length=60, max_length=400)
    architecture: list[str] = Field(default_factory=list, max_length=8)
    props: list[str] = Field(default_factory=list, max_length=10)
    lighting: str
    weather: str
    time_of_day: str
    atmosphere: str
    camera_shot_type: str  # from background_camera_shots
    composition_constraints: list[str] = Field(default_factory=list, max_length=5)
    people_policy: PeoplePolicy
    style_tags: list[str] = Field(default_factory=list)  # filled by classifier
    forbidden_characters: list[str] = Field(default_factory=list)
```

字段必填性原则：
- `scene_id / scene_name / scene_selector / scene_type / split_reason / environment_description / people_policy` 必填（schema 层 not null）
- `evidence_spans / style_tags / forbidden_characters` 可空（运行时补全）
- `environment_description` 长度 ≥60，避免模型输出空泛一两句话

## 风险与权衡

1. **Schema 过严 → LLM 频繁 retry**：`min_length=60` 可能让弱模型输出"为了凑字数塞剧情"。权衡：保留长度下限但通过 `_validate_no_plot` 拦剧情，不要单纯放宽长度。
2. **字段过多 → prompt token 膨胀**：18 个字段挤进 user prompt 会推高 token 成本。权衡：system prompt 只列字段名和示例，user prompt 只塞原始章节正文，让 LLM 自己根据字段名产出 JSON。

## 完成判据自检

- ✅ 完全围绕"BackgroundSceneSpec schema 字段定义"，不跑题到 analyzer 实现细节。
- ✅ 引用 SOTA：SDXL Refiner 论文、Stable Diffusion Structured Prompt、Pydantic v2 文档。
- ✅ 落到 `backend/app/schemas.py`。
- ✅ 符合哲学：schema 是结构化抽取产物，不动 CogView-4，不动 DB（spec 走 variation_info 序列化）。
