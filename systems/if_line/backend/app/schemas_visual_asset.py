from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


VisualAssetType = Literal["portrait", "background", "keyframe"]
AssetSelectionMethod = Literal[
    "manual_upload", "agent", "direct_generate", "manual_library", "system_generate"
]
AssetOrigin = Literal["upload", "library", "ai_generated"]
AssetActionMode = Literal["direct_generate", "agent_compose", "library_select", "upload"]
OwnershipScope = Literal["session", "personal", "project"]


class VisualAssetUploadRead(BaseModel):
    action_id: str
    asset_id: int
    asset_version_id: str
    storage_object_id: str
    asset_type: VisualAssetType
    target_name: str
    selection_method: Literal["manual_upload"] = "manual_upload"
    origin: Literal["upload"] = "upload"
    ownership_scope: OwnershipScope
    media_url: str
    media_type: str
    sha256: str
    width: int
    height: int
    has_alpha: bool
    status: str
    reused_storage_object: bool = False


class LibraryAssetRead(BaseModel):
    id: str
    catalog_version: str
    stable_key: str
    asset_type: VisualAssetType
    storage_object_id: str
    media_url: str
    sha256: str
    description_cn: str
    description_en: str
    tags: list[str]
    taxonomy: dict[str, Any]
    identity_group: str | None
    expression: str | None
    pose: str | None
    style: str | None
    width: int | None = None
    height: int | None = None
    has_alpha: bool | None = None
    quality_score: float
    score: float | None = None
    confidence: Literal["high", "medium", "low"] | None = None
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    fallback_reason: str | None = None


class LibraryAssetPage(BaseModel):
    items: list[LibraryAssetRead]
    total: int
    limit: int
    offset: int
    catalog_version: str
    matcher_version: str


class LibraryFacetOption(BaseModel):
    id: str
    value: str
    display_name: str
    count: int = Field(ge=0)
    selected: bool


class LibraryFacetGroup(BaseModel):
    category: str
    options: list[LibraryFacetOption]


class LibraryFacetResponse(BaseModel):
    catalog_version: str
    asset_type: VisualAssetType
    selected_tag_ids: list[str]
    total: int = Field(ge=0)
    facets: list[LibraryFacetGroup]


class AssetActionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_slot_id: str | None = Field(default=None, max_length=64)
    vn_graph_revision_id: str | None = Field(default=None, max_length=64)
    source_kind: Literal[
        "story_bible_revision",
        "outline_revision",
        "chapter_revision",
        "chapter_script_revision",
        "vn_graph_revision",
        "branch_candidate",
        "reading_continuation",
    ] | None = None
    source_id: str | None = Field(default=None, min_length=1, max_length=64)
    chapter_index: int | None = Field(default=None, ge=1)
    node_index: int | None = Field(default=None, ge=0)
    node_key: str | None = Field(default=None, max_length=128)
    segment_key: str | None = Field(default=None, max_length=128)
    asset_slot: Literal["BackgroundImage", "TachiIamge", "IllustrationImage"] | None = None
    role: VisualAssetType | None = None
    graph_revision_id: str | None = Field(default=None, max_length=64)
    graph_hash: str | None = Field(default=None, min_length=64, max_length=64)


class AssetActionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AssetActionMode
    target: AssetActionTarget
    input: dict[str, Any] = Field(default_factory=dict)


class AssetActionAsset(BaseModel):
    asset_id: int
    asset_version_id: str
    storage_object_id: str
    asset_type: VisualAssetType
    media_url: str
    sha256: str
    description_cn: str = ""
    tags: list[str] = Field(default_factory=list)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    width: int | None = None
    height: int | None = None


class AssetActionRead(BaseModel):
    action_id: str
    status: str
    stage: str
    progress: float
    selection_method: str
    origin: str | None
    task_id: str | None
    events_url: str | None
    assets: list[AssetActionAsset]
    scene_manifest: dict[str, Any] | None
    vngraph_patch: list[dict[str, Any]]
    base_graph_hash: str | None
    result_graph_hash: str | None
    requires_confirmation: bool
    error: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class AssetActionConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_json: dict[str, Any] | None = None
    result_graph_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ReadingDirectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = Field(min_length=1, max_length=4000)
    visual_mode: Literal["user_upload", "system_generate"]
    uploaded_asset_version_ids: list[str] = Field(default_factory=list, max_length=3)
    max_generated_assets: int = Field(default=3, ge=0, le=3)
    parent_continuation_id: str | None = Field(default=None, max_length=64)

    @field_validator("direction", mode="before")
    @classmethod
    def _strip_direction(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReadingDirectionSuggestionRead(BaseModel):
    direction: str
    prompt_version: str


class ReadingImageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=4000)
    width: int = Field(default=1024, ge=512, le=2048)
    height: int = Field(default=1024, ge=512, le=2048)

    @field_validator("prompt", mode="before")
    @classmethod
    def _strip_prompt(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReadingContinuationRead(BaseModel):
    id: str
    session_id: str
    parent_continuation_id: str | None
    parent_node_id: str | None
    direction: str
    visual_mode: str
    status: str
    continuation_text: str
    state_delta: dict[str, Any]
    scene_manifest: dict[str, Any] | None
    vngraph_patch: list[dict[str, Any]]
    uploaded_asset_version_ids: list[str]
    frozen_asset_version_ids: list[str]
    task_id: str | None
    asset_action_id: str | None
    base_session_lock_version: int
    error: dict[str, Any] | None
    confirmed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PublicContinuationNodeRead(BaseModel):
    id: str
    parent_continuation_id: str | None
    direction: str
    continuation_text: str
    chapter_number: int
    depth: int
    selection_count: int
    assets: list[AssetActionAsset] = Field(default_factory=list)
    confirmed_at: datetime


class PublicContinuationTreeRead(BaseModel):
    project_id: int
    release_id: str
    chapter_number: int
    nodes: list[PublicContinuationNodeRead]


class ContinuationConfirmResult(BaseModel):
    continuation: ReadingContinuationRead
    session_lock_version: int
