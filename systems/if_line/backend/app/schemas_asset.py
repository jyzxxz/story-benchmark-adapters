from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AssetType = Literal["portrait", "background", "keyframe", "audio"]
AssetSourceKind = Literal[
    "story_bible_revision",
    "outline_revision",
    "chapter_revision",
    "chapter_script_revision",
    "vn_graph_revision",
    "branch_candidate",
    "reading_continuation",
    "upload",
    "library",
]


class AssetRenderSpec(BaseModel):
    """Provider-neutral render inputs used to derive the immutable cache key."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=12000)
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    prompt_template_version: str = Field(min_length=1, max_length=64)
    negative_prompt: str = Field(default="", max_length=6000)
    negative_prompt_version: str = Field(default="none_v1", min_length=1, max_length=64)
    seed: int | None = None
    width: int | None = Field(default=None, ge=64, le=8192)
    height: int | None = Field(default=None, ge=64, le=8192)
    style_pack_version: str = Field(default="default_v1", min_length=1, max_length=64)
    identity_version: str | None = Field(default=None, max_length=64)
    postprocess_version: str = Field(default="none_v1", min_length=1, max_length=64)
    validator_version: str = Field(default="none_v1", min_length=1, max_length=64)
    extra_parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt", "provider", "model", "prompt_template_version", mode="before")
    @classmethod
    def _strip_required_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class AssetPlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_type: AssetType
    logical_key: str = Field(min_length=1, max_length=200)
    target_name: str = Field(min_length=1, max_length=200)
    chapter_index: int | None = Field(default=None, ge=1)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    render_spec: AssetRenderSpec

    @field_validator("logical_key", "target_name", mode="before")
    @classmethod
    def _strip_identity_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class AssetPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: AssetSourceKind
    source_revision_id: str = Field(min_length=1, max_length=64)
    items: list[AssetPlanItem] = Field(min_length=1, max_length=100)


class AssetVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    asset_id: int
    source_kind: str | None
    source_revision_id: str | None
    source_hash: str | None
    generation_task_id: str | None
    version_no: int
    cache_key: str
    provider: str | None
    model: str | None
    prompt_hash: str
    prompt_version: str
    negative_prompt_hash: str | None
    seed: int | None
    width: int | None
    height: int | None
    postprocess_version: str | None
    validator_version: str | None
    quality_score: float | None
    safety_status: str
    rights_metadata: dict[str, Any]
    asset_spec: dict[str, Any] = Field(default_factory=dict, validation_alias="asset_spec_json")
    render_spec: dict[str, Any] = Field(default_factory=dict, validation_alias="render_spec_json")
    render_spec_hash: str | None
    media_url: str | None = None
    media_type: str | None = None
    storage_status: str | None = None
    binding_count: int = 0
    is_in_use: bool = False
    created_at: datetime


class AssetRead(BaseModel):
    id: int
    project_id: int
    asset_type: AssetType
    target_name: str
    status: str
    logical_key: str
    taxonomy: dict[str, Any]
    chapter_indices: list[int] = Field(default_factory=list)
    version_count: int = 0
    latest_version_id: str | None = None
    versions: list[AssetVersionRead] = Field(default_factory=list)
    archived_at: datetime | None = None
    created_at: datetime | None
    updated_at: datetime | None


class AssetPlanRead(BaseModel):
    plan_key: str
    source_kind: AssetSourceKind
    source_revision_id: str
    created_count: int
    reused_count: int
    items: list[AssetRead]


class AssetPage(BaseModel):
    items: list[AssetRead]
    total: int
    limit: int
    offset: int


class AssetPlanRenderRead(BaseModel):
    plan_key: str
    parent_task_id: str
    child_task_ids: list[str]
    created_child_count: int


class AssetBindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    source_kind: str
    source_id: str
    node_key: str | None
    segment_key: str | None
    role: str
    asset_version_id: str
    order_index: int
    required: bool
    created_at: datetime
    deleted_at: datetime | None
