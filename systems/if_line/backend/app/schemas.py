"""
Pydantic Schemas
"""
from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Dict, Any, Literal
from datetime import datetime
from urllib.parse import urlparse


def _normalize_avatar_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return value
    normalized = value.strip()
    if not normalized:
        return None

    parsed = urlparse(normalized)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return normalized
    if (
        normalized.startswith("/static/avatars/")
        and "\\" not in normalized
        and "/../" not in normalized
        and not normalized.endswith("/..")
    ):
        return normalized
    raise ValueError("头像地址必须是 http(s) URL 或 /static/avatars/ 路径")


# Auth Schemas
class UserRegisterRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=8, max_length=128)
    display_name: Optional[str] = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        normalized = (value or "").strip().lower()
        if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
            raise ValueError("邮箱格式不正确")
        return normalized

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("显示名不能为空")
        return normalized


class UserLoginRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        return (value or "").strip().lower()


class UserUpdateRequest(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=100)
    avatar_url: Optional[str] = Field(default=None, max_length=500)

    @field_validator("display_name")
    @classmethod
    def _validate_display_name(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("显示名不能为空")
        return normalized

    @field_validator("avatar_url")
    @classmethod
    def _validate_avatar_url(cls, value: Optional[str]) -> Optional[str]:
        return _normalize_avatar_url(value)


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: str
    avatar_url: Optional[str]
    quota_total: int
    quota_daily: int
    quota_used_total: int
    quota_used_daily: int
    quota_reset_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    user: UserResponse


# Project Schemas
class ProjectCreate(BaseModel):
    title: str
    characters: Optional[List[str]] = []
    story_start: str
    story_end: str
    style: Optional[str] = None
    source_work: Optional[str] = None
    pace: Optional[str] = "medium"  # fast / medium / slow
    extra_requirements: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    owner_id: Optional[int] = None
    title: str
    characters: Optional[List[str]]
    story_start: str
    story_end: str
    style: Optional[str]
    source_work: Optional[str] = None
    pace: Optional[str]
    extra_requirements: Optional[str]
    status: str
    visibility: str
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    # 时间统计字段
    outline_approved_at: Optional[datetime]
    first_chapter_generated_at: Optional[datetime]
    total_chapter_generate_time: Optional[float]
    total_asset_generate_time: Optional[float]

    class Config:
        from_attributes = True


class PublicProjectResponse(BaseModel):
    id: int
    title: str
    characters: Optional[List[str]]
    story_start: str
    style: Optional[str]
    source_work: Optional[str] = None
    pace: Optional[str]
    status: str
    visibility: str
    published_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ProjectStatusResponse(BaseModel):
    id: int
    title: str
    status: str
    current_step: Optional[str] = None
    completed_steps: List[str] = []
    pending_steps: List[str] = []


# Social / Notification Schemas
class ProjectSocialSummary(BaseModel):
    project_id: int
    like_count: int
    comment_count: int
    liked_by_me: bool


class ProjectLikeResponse(BaseModel):
    project_id: int
    liked: bool
    like_count: int


class ProjectCommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)

    @field_validator("body")
    @classmethod
    def _normalize_body(cls, value: str) -> str:
        normalized = (value or "").strip()
        if not normalized:
            raise ValueError("评论内容不能为空")
        return normalized


class ProjectCommentResponse(BaseModel):
    id: int
    project_id: int
    user_id: int
    display_name: str
    avatar_url: Optional[str]
    body: str
    created_at: datetime
    updated_at: datetime


class NotificationResponse(BaseModel):
    id: int
    actor_user_id: Optional[int]
    actor_display_name: Optional[str]
    actor_avatar_url: Optional[str]
    project_id: Optional[int]
    comment_id: Optional[int]
    notification_type: str
    title: str
    body: Optional[str]
    payload: Optional[Dict[str, Any]]
    read_at: Optional[datetime]
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: List[NotificationResponse]
    unread_count: int


class UserQuotaResponse(BaseModel):
    user_id: int
    quota_total: int
    quota_daily: int
    quota_used_total: int
    quota_used_daily: int
    quota_remaining_total: int
    quota_remaining_daily: int
    quota_reset_at: Optional[datetime]


class UserUsageLedgerEntryResponse(BaseModel):
    id: str
    reservation_id: Optional[str]
    task_id: Optional[str]
    task_kind: Optional[str]
    task_status: Optional[str]
    project_id: Optional[int]
    entry_type: str
    amount: float
    currency: str
    reservation_status: Optional[str]
    event_metadata: Dict[str, Any]
    created_at: datetime


# Story Bible Schemas
class StoryBibleCreate(BaseModel):
    worldview: Optional[str] = None
    characters: Optional[List[Dict[str, Any]]] = []
    character_relations: Optional[str] = None
    main_conflict: Optional[str] = None
    emotional_line: Optional[str] = None
    style_rules: Optional[str] = None
    ending_constraints: Optional[str] = None
    forbidden_points: Optional[List[str]] = []
    writing_notes: Optional[List[str]] = []


class StoryBibleResponse(BaseModel):
    id: int
    project_id: int
    worldview: Optional[str]
    characters: Optional[List[Dict[str, Any]]]
    character_relations: Optional[str]
    main_conflict: Optional[str]
    emotional_line: Optional[str]
    style_rules: Optional[str]
    ending_constraints: Optional[str]
    forbidden_points: Optional[List[str]]
    writing_notes: Optional[List[str]]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StoryBibleUpdate(BaseModel):
    worldview: Optional[str] = None
    characters: Optional[List[Dict[str, Any]]] = None
    character_relations: Optional[str] = None
    main_conflict: Optional[str] = None
    emotional_line: Optional[str] = None
    style_rules: Optional[str] = None
    ending_constraints: Optional[str] = None
    forbidden_points: Optional[List[str]] = None
    writing_notes: Optional[List[str]] = None


# Chapter Outline Schemas
class ChapterOutlineCreate(BaseModel):
    chapter_index: int
    title: str
    summary: str
    conflict: Optional[str] = None
    characters: Optional[List[str]] = []
    scene: Optional[str] = None
    emotion: Optional[str] = None
    visual_keywords: Optional[List[str]] = []

    @model_validator(mode="before")
    @classmethod
    def _normalize_scene_emotion(cls, data: Any) -> Any:
        """
        LLM prompt 要求返回 `scene_locations: [...]` 和 `emotion_shift: "..."`,
        但下游字段是 `scene: str` / `emotion: str`。这里做归一化:
        - 如果已经给了 scene/emotion 直接保留
        - 否则从 scene_locations / emotion_shift / core_conflict / summary 兜底
        """
        if not isinstance(data, dict):
            return data

        # scene 兜底链
        if not data.get("scene"):
            scene_locations = data.get("scene_locations") or []
            # 过滤掉空字符串/None，取第一个非空
            first_loc = next(
                (str(s).strip() for s in scene_locations
                 if s and isinstance(s, str) and s.strip()),
                None,
            ) if isinstance(scene_locations, list) else None
            if first_loc:
                data["scene"] = first_loc
            elif data.get("core_conflict"):
                data["scene"] = str(data["core_conflict"]).strip()[:60]
            elif data.get("summary"):
                data["scene"] = str(data["summary"]).strip()[:60]

        # emotion 兜底链
        if not data.get("emotion"):
            emotion_shift = data.get("emotion_shift")
            if emotion_shift and isinstance(emotion_shift, str) and emotion_shift.strip():
                data["emotion"] = emotion_shift.strip()[:60]
            elif data.get("summary"):
                data["emotion"] = str(data["summary"]).strip()[:60]

        return data


class ChapterCreateRequest(BaseModel):
    chapter_index: Optional[int] = Field(default=None, ge=1)
    title: str = Field(default="", max_length=200)
    summary: str = ""
    conflict: Optional[str] = ""
    characters: Optional[List[str]] = []
    scene: Optional[str] = None
    emotion: Optional[str] = None
    visual_keywords: Optional[List[str]] = []


class ChapterOutlineResponse(BaseModel):
    id: int
    project_id: int
    chapter_index: int
    title: str
    summary: str
    conflict: Optional[str]
    characters: Optional[List[str]]
    scene: Optional[str]
    emotion: Optional[str]
    visual_keywords: Optional[List[str]]
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class OutlineListResponse(BaseModel):
    chapters: List[ChapterOutlineResponse]


class OutlineReviseRequest(BaseModel):
    feedback: str


# Chapter Content Schemas
class ChapterContentResponse(BaseModel):
    id: int
    project_id: int
    chapter_index: int
    content: str
    version: int
    status: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChapterGenerateRequest(BaseModel):
    word_count_min: Optional[int] = 3000
    word_count_max: Optional[int] = 4500


class ChapterWorkflowRequest(BaseModel):
    chapter_index: Optional[int] = Field(default=None, ge=1)
    title: str = Field(default="", max_length=200)
    summary: str = ""
    conflict: Optional[str] = ""
    characters: Optional[List[str]] = []
    scene: Optional[str] = None
    emotion: Optional[str] = None
    visual_keywords: Optional[List[str]] = []
    create_workspace: bool = False
    generate_content: bool = True
    generate_asset_prompts: bool = False
    asset_prompt_genre: Optional[str] = None
    generate_resources: bool = False
    resource_types: Optional[List[Literal["background", "portrait", "keyframe", "voice"]]] = None
    background_moods: Optional[List[str]] = None
    portrait_batch_size: int = Field(default=5, ge=1, le=50)
    portrait_auto_demand: bool = True
    word_count_min: Optional[int] = 3000
    word_count_max: Optional[int] = 4500


class ChapterWorkflowResponse(BaseModel):
    ok: bool
    status: str = "completed"
    project_id: int
    chapter_index: Optional[int]
    steps: List[Dict[str, Any]]
    events: List[Dict[str, Any]] = []
    recoverable_failures: List[Dict[str, Any]] = []
    next_actions: List[Dict[str, Any]] = []
    workspace: Optional[Dict[str, Any]] = None
    content: Optional[Dict[str, Any]] = None
    asset_prompts: Optional[Dict[str, Any]] = None
    resources: Optional[Dict[str, Any]] = None


# Asset Schemas
class AssetCreate(BaseModel):
    chapter_index: Optional[int] = None
    asset_type: str
    target_name: str
    prompt: str


class AssetResponse(BaseModel):
    id: int
    project_id: int
    chapter_index: Optional[int]
    asset_type: str
    target_name: str
    prompt: str
    description_cn: Optional[str] = None  # 中文描述
    image_url: Optional[str]
    status: str
    genre: Optional[str] = None           # 画风类型
    emotion: Optional[str] = None         # 情绪（角色立绘）
    outfit: Optional[str] = None          # 服装（角色立绘）
    pose: Optional[str] = None            # 姿态（角色立绘）
    mood: Optional[str] = None            # 氛围（背景图）
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AssetListResponse(BaseModel):
    character_prompts: List[AssetResponse]
    background_prompts: List[AssetResponse]
    keyframe_prompts: List[AssetResponse]


class ChapterCreateResponse(BaseModel):
    project_id: int
    chapter_index: int
    outline: ChapterOutlineResponse
    content: ChapterContentResponse


# Workflow Schemas
class WorkflowRunResponse(BaseModel):
    id: int
    project_id: int
    current_step: str
    input_json: Optional[Dict[str, Any]]
    output_json: Optional[Dict[str, Any]]
    status: str
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WorkflowRunListResponse(BaseModel):
    runs: List[WorkflowRunResponse]


# LLM Output Schemas
class LLMStoryBibleOutput(BaseModel):
    worldview: str
    characters: List[Dict[str, Any]]
    character_relations: str
    main_conflict: str
    emotional_line: str
    style_rules: str
    ending_constraints: str
    forbidden_points: List[str]
    writing_notes: List[str]


class LLMChapterOutlineOutput(BaseModel):
    chapters: List[ChapterOutlineCreate]


class LLMSingleChapterOutlineOutput(BaseModel):
    chapter: ChapterOutlineCreate
    planning_notes: Optional[str] = ""
    continuity_check: Optional[str] = ""


class LLMChapterContentOutput(BaseModel):
    chapter_index: int
    title: str
    content: str
    ending_hook: Optional[str] = ""
    appearing_characters: Optional[List[str]] = []
    main_scene: Optional[str] = ""
    emotion: Optional[str] = ""


class LLMAssetPromptOutput(BaseModel):
    detected_genre: Optional[str] = None  # 检测到的画风类型
    character_prompts: List[Dict[str, Any]]
    background_prompts: List[Dict[str, Any]]
    keyframe_prompts: List[Dict[str, Any]]


class CharacterPrompt(BaseModel):
    name: str
    prompt: str
    transparent_background: bool = True


class BackgroundPrompt(BaseModel):
    scene: str
    prompt: str


class KeyframePrompt(BaseModel):
    name: str
    prompt: str


class GenerationStatResponse(BaseModel):
    id: int
    project_id: int
    generation_type: str
    asset_type: Optional[str]
    chapter_index: Optional[int]
    asset_id: Optional[int]
    target_name: Optional[str]
    variation_info: Optional[Dict[str, Any]]
    start_time: datetime
    end_time: Optional[datetime]
    duration_seconds: Optional[float]
    status: str
    error_message: Optional[str]

    class Config:
        from_attributes = True


class GenerationStatsSummary(BaseModel):
    """生成统计摘要"""
    total_chapters: int
    total_chapter_time: float  # 秒
    avg_chapter_time: float  # 秒
    total_portraits: int
    total_portrait_time: float
    avg_portrait_time: float
    total_backgrounds: int
    total_background_time: float
    avg_background_time: float
    total_keyframes: int
    total_keyframe_time: float
    avg_keyframe_time: float
    total_assets: int
    total_asset_time: float  # 秒
    avg_asset_time: float  # 秒
    first_chapter_time: Optional[float]  # 从大纲确认到第一章生成完成的时间


class ProjectStatsResponse(BaseModel):
    """项目统计响应"""
    project_id: int
    project_title: str
    status: str
    outline_approved_at: Optional[datetime]
    first_chapter_generated_at: Optional[datetime]
    first_chapter_duration: Optional[float]  # 秒
    # 章节统计
    total_chapters_generated: int
    total_chapter_time: float
    # 立绘统计
    total_portraits_generated: int
    total_portrait_time: float
    avg_portrait_time: float
    # 背景图统计
    total_backgrounds_generated: int
    total_background_time: float
    avg_background_time: float
    # 关键帧统计
    total_keyframes_generated: int
    total_keyframe_time: float
    avg_keyframe_time: float
    # 总素材统计
    total_assets_generated: int
    total_asset_time: float
    # 详细记录
    generation_stats: List[GenerationStatResponse]
    portrait_stats: Optional[List[GenerationStatResponse]] = None
    background_stats: Optional[List[GenerationStatResponse]] = None
    keyframe_stats: Optional[List[GenerationStatResponse]] = None


class AssetTimeBreakdownResponse(BaseModel):
    """素材时间分解响应"""
    portrait_by_emotion: List[Dict[str, Any]]
    background_by_mood: List[Dict[str, Any]]
    background_by_chapter: List[Dict[str, Any]]

# ============================================================
# Stage_Background_AR Schemas (L2.01 - L2.10)
# ============================================================

# L2.10: Custom exceptions
class PlotContaminationError(Exception):
    """背景 prompt 中检测到剧情/人物/对白内容（应只含环境信息）"""
    pass


class StyleCoverageError(Exception):
    """style_tags 维度覆盖不足（至少 3/5 维度）"""
    pass


class StyleCharacterLeakError(Exception):
    """style_tags 中检测到角色/动作/对白相关词"""
    pass


# L2.01: PeoplePolicy
class PeoplePolicy(BaseModel):
    """人物准入策略（背景图中人物的允许程度）"""
    mode: Literal["empty_required", "background_people_optional", "background_groups_required"]
    rationale: str = Field(
        ...,
        min_length=4,
        max_length=500,
        description="为什么选这个 mode（基于文本证据，如关键词/场景类型）"
    )

    @field_validator("rationale")
    @classmethod
    def rationale_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("rationale 不能为空")
        return v.strip()


# L2.02: StyleTag
class StyleTag(BaseModel):
    """单个风格标签（必须来自受控词表）"""
    dimension: Literal["art_style", "color_palette", "lens_or_camera_feel", "texture_or_rendering", "mood"]
    value: str = Field(..., min_length=1, max_length=80)

    @field_validator("value")
    @classmethod
    def value_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("style tag value 不能为空")
        return v.strip()


# L2.03: BackgroundSceneSpec
class BackgroundSceneSpec(BaseModel):
    """单个背景场景的结构化规格（用于环境图生成的唯一数据源）"""
    scene_id: str = Field(..., min_length=1, max_length=64)
    scene_name: str = Field(..., min_length=1, max_length=120, description="中文场景名，可读")

    scene_selector: str = Field(
        ...,
        min_length=4,
        max_length=128,
        description="机器可用的 ASCII slug；用于 vn_graph 背景匹配",
    )

    segment_id: Optional[int] = Field(
        default=None,
        ge=1,
        description=(
            "稳定场景指纹：取自共享 SceneSegmenterService 的 segment_id。"
            "vngraph BackGroundNode 与 background Asset 都写入同一个值，"
            "让 assembler 用指纹而不是 LLM 文本命名做绑定。"
        ),
    )

    segment_location: Optional[str] = Field(
        default=None,
        max_length=120,
        description="共享 segmenter 给出的 location（中文短词）。用于跨模块名称对齐。",
    )

    # Stage_VNGraph_Assembly_AR — 背景定位 anchor 字段（全部 Optional，老 spec
    # dict 仍能校验通过）。装配阶段按 segment_id → start_marker → source_excerpt
    # → location_keyword → event_signature → proportional 的顺序匹配 paragraph。
    # 落库时也写 top-level（asset_management_service._persist_background_asset），
    # assembler 直接从 generation_params 读，不依赖嵌套 scene_spec 反序列化。
    segment_start_marker: Optional[str] = Field(
        default=None,
        max_length=200,
        description="共享 segmenter 给出的 start_marker；用于 paragraph 锚定。",
    )
    segment_end_marker: Optional[str] = Field(
        default=None,
        max_length=200,
        description="共享 segmenter 给出的 end_marker；用于 paragraph 锚定。",
    )
    source_excerpt: Optional[str] = Field(
        default=None,
        max_length=600,
        description=(
            "支持该场景的最具代表性的原文短句。装配阶段按精确/相似度匹配 "
            "paragraph 文本，是 vngraph 背景挂载的高优先级锚点。"
        ),
    )
    scene_fingerprint: Optional[str] = Field(
        default=None,
        max_length=200,
        description="场景指纹；同名场景在不同章节也可通过该值区分。",
    )
    event_signature: Optional[str] = Field(
        default=None,
        max_length=300,
        description="事件签名（场景内核心事件的稳定 token 串）；用于 paragraph 关键词命中。",
    )

    scene_type: Literal[
        "public_commerce", "civic_military", "public_hall",
        "private_interior", "wilderness_dreamscape", "abandoned_void"
    ] = Field(..., description="场景大类，对应 background_scene_taxonomy.categories")

    split_reason: Optional[str] = Field(
        None, max_length=500,
        description="为什么这是一个独立场景（非首场景必填）：地点/时间/天气/人群/物理状态变化"
    )

    evidence_spans: List[str] = Field(
        default_factory=list,
        max_length=10,
        description="支持该场景的原文短句（最多 10 条）"
    )

    environment_description: str = Field(
        ...,
        min_length=40,
        max_length=1500,
        description="详细环境描述（中文）—— 只写环境，不写剧情"
    )

    architecture: Optional[str] = Field(None, max_length=500)
    props: Optional[str] = Field(None, max_length=500)
    lighting: str = Field(..., min_length=2, max_length=300)
    weather: Optional[str] = Field(None, max_length=200)
    time_of_day: Literal[
        "dawn", "morning", "noon", "afternoon", "dusk", "evening", "night", "late_night", "unknown"
    ] = "unknown"
    atmosphere: str = Field(..., min_length=2, max_length=300)

    camera_shot_type: Literal[
        "establishing_wide", "environment_medium", "high_angle_distant",
        "interior_wide", "telephoto_compressed", "low_angle_perspective"
    ]

    composition_constraints: Optional[str] = Field(None, max_length=500)

    people_policy: PeoplePolicy

    style_tags: List[StyleTag] = Field(
        default_factory=list,
        max_length=10,
        description="风格标签（3-5 个，覆盖 5 维度中至少 3 个）"
    )

    forbidden_characters: List[str] = Field(
        default_factory=list,
        description="禁用命名角色（中文名 + 英文别名）"
    )

    forbidden_entities: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Stage_Background_Entity_Exclusion — 禁用剧情角色实体（结构化）。"
            "每个 entry 是 ForbiddenStoryEntity.to_dict() 的序列化形式："
            "{character_id, canonical_name, aliases, entity_type, species, "
            "identity_terms, appearance_signature, role_terms, visual_fingerprint}。"
            "背景生成链路必须以此为唯一权威禁入清单；forbidden_characters 字段"
            "保留作为 legacy 字符串回退。"
        ),
    )

    @field_validator("scene_selector")
    @classmethod
    def selector_pattern(cls, v: str) -> str:
        """scene_selector 必须是 ASCII slug（小写字母/数字/下划线/连字符）"""
        import re
        if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", v):
            raise ValueError(
                "scene_selector 必须以 ASCII 小写字母或数字开头，"
                "只能含 [a-z0-9_-]"
            )
        return v

    @model_validator(mode="after")
    def style_tag_coverage(self) -> "BackgroundSceneSpec":
        """style_tags 必须覆盖至少 3 个维度（如果非空）"""
        if self.style_tags:
            dims = {t.dimension for t in self.style_tags}
            if len(dims) < 3:
                raise ValueError(
                    f"style_tags 覆盖维度不足：{len(dims)}/5，至少 3 维"
                )
        return self

    @model_validator(mode="after")
    def no_plot_in_environment(self) -> "BackgroundSceneSpec":
        """environment_description 不能含剧情类关键词"""
        forbidden = ["他说", "她说", "心想", "暗想", "拥抱", "亲吻", "决定去",
                     "于是", "然后", "忽然", "突然"]
        hit = [w for w in forbidden if w in self.environment_description]
        if hit:
            raise PlotContaminationError(
                f"environment_description 含剧情词: {hit}"
            )
        return self


# L2.04: BBox
class BBox(BaseModel):
    """边界框（来自 bbox 检测器，如 YOLOv8n）"""
    x1: float = Field(..., ge=0.0, le=1.0, description="归一化左上 x")
    y1: float = Field(..., ge=0.0, le=1.0, description="归一化左上 y")
    x2: float = Field(..., ge=0.0, le=1.0, description="归一化右下 x")
    y2: float = Field(..., ge=0.0, le=1.0, description="归一化右下 y")
    conf: float = Field(..., ge=0.0, le=1.0, description="置信度")
    label: str = Field(default="person", max_length=40)

    @model_validator(mode="after")
    def geometry_valid(self) -> "BBox":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError(f"BBox 退化：x1={self.x1}, y1={self.y1}, x2={self.x2}, y2={self.y2}")
        return self

    @property
    def area(self) -> float:
        return max(0.0, (self.x2 - self.x1) * (self.y2 - self.y1))

    def in_center_region(self, cx1: float, cy1: float, cx2: float, cy2: float) -> bool:
        """是否与中心区域相交"""
        return not (self.x2 < cx1 or self.x1 > cx2 or self.y2 < cy1 or self.y1 > cy2)

    def center_area_within(self, cx1: float, cy1: float, cx2: float, cy2: float) -> float:
        """在中心区域内的面积"""
        ix1 = max(self.x1, cx1); iy1 = max(self.y1, cy1)
        ix2 = min(self.x2, cx2); iy2 = min(self.y2, cy2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        return (ix2 - ix1) * (iy2 - iy1)


# L2.05: ValidationResult
class ValidationResult(BaseModel):
    """生成图验收结果（bbox + VLM 混合）"""
    passed: bool
    stage: Literal["bbox", "vlm_named_character", "vlm_human_subject", "combined", "entity_leak"]
    reason: str = Field(default="", min_length=0, max_length=500)
    is_hard_fail: bool = Field(
        default=False,
        description="hard fail 不可通过简单重试解决（如命名角色命中）"
    )
    bbox_person_count: int = Field(default=0, ge=0)
    bbox_max_conf: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox_max_area_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    bbox_max_center_area_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    vlm_named_character_score: float = Field(default=0.0, ge=0.0, le=1.0)
    vlm_human_subject_score: float = Field(default=0.0, ge=0.0, le=1.0)


# L2.06: GenerationResult
class GenerationResult(BaseModel):
    """单次背景图生成结果（含重试/fallback 信息）"""
    status: Literal["completed", "failed", "skipped", "quarantined"]
    image_path: Optional[str] = None
    image_bytes_b64: Optional[str] = None
    final_prompt: Optional[str] = None
    retry_count: int = Field(default=0, ge=0, le=10)
    fallback_type: Optional[Literal[
        "none", "escalation", "safety_fallback", "scene_aware_safety"
    ]] = "none"
    reason: Optional[str] = None
    scene_selector: Optional[str] = None
    validation_results: List[ValidationResult] = Field(default_factory=list)


# L2.07: BackgroundPromptData
class BackgroundPromptData(BaseModel):
    """schema-only 背景图 prompt 数据（明确不含 summary/plot）"""
    scene_name: str = Field(..., min_length=1, max_length=120)
    scene_selector: str = Field(..., min_length=4, max_length=128)
    environment_description: str = Field(..., min_length=1, max_length=1500)
    architecture: Optional[str] = None
    props: Optional[str] = None
    lighting: str
    weather: Optional[str] = None
    time_of_day: str = "unknown"
    atmosphere: str
    camera_shot_type: str
    composition_constraints: Optional[str] = None
    people_policy_mode: str
    style_tags: List[str] = Field(default_factory=list)
    forbidden_characters: List[str] = Field(default_factory=list)
    forbidden_entities: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("environment_description")
    @classmethod
    def no_plot_in_env(cls, v: str) -> str:
        """环境描述中不能出现剧情类关键词（轻量校验）"""
        forbidden = ["他说", "她说", "想了想", "决定", "突然", "于是", "忽然"]
        hit = [w for w in forbidden if w in v]
        if hit:
            raise ValueError(f"environment_description 含剧情词: {hit}")
        return v


# Stage schemas end
