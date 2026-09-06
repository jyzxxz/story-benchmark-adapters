from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    kind: str = Field(min_length=1, max_length=100)
    project_id: int | None = None
    source_refs: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    root_task_id: str | None
    parent_task_id: str | None
    project_id: int | None
    kind: str
    status: str
    stage: str | None
    progress: float
    source_refs: dict[str, Any]
    result_refs: dict[str, Any]
    attempt: int
    max_attempts: int
    error_code: str | None
    error_detail: str | None
    estimated_cost: Decimal
    reserved_cost: Decimal
    actual_cost: Decimal
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskAccepted(BaseModel):
    task_id: str
    status: str
    events_url: str
    created: bool


class RevisionHeadRead(BaseModel):
    revision_id: str | None
    lock_version: int = Field(ge=1)
    updated_at: datetime


class RevisionHeadUpdate(BaseModel):
    revision_id: str = Field(min_length=36, max_length=36)


class CandidateSetRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    story_path_id: str
    checkpoint_node_id: str
    chapter_revision_id: str
    state_snapshot_id: str
    revision_no: int = Field(ge=1)
    source_hash: str
    content_hash: str


class BranchCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_set_revision_id: str
    option_key: str
    preview_text: str
    state_delta: dict[str, Any]


class StoryPathPromotionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class StoryPathCreateFromCandidateRequest(StoryPathPromotionRequest):
    candidate_id: str = Field(min_length=36, max_length=36)


class StoryPathRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    parent_path_id: str | None
    fork_path_chapter_id: str | None
    fork_checkpoint_node_id: str | None
    fork_candidate_id: str | None
    base_state_snapshot_id: str | None
    title: str
    status: str
    lock_version: int = Field(ge=1)


class TaskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    event_type: str
    payload: dict[str, Any]
    created_at: datetime


class BibleRevisionCreate(BaseModel):
    content: dict[str, Any]
    activate: bool = True


class BibleRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    parent_revision_id: str | None
    revision_no: int
    source_hash: str
    content_hash: str
    content_json: dict[str, Any]
    status: str
    generation_task_id: str | None
    created_at: datetime


class OutlineChapterInput(BaseModel):
    chapter_index: int = Field(ge=1)
    title: str | None = None
    summary: str | None = None
    conflict: str | None = None
    characters: list[Any] = Field(default_factory=list)
    scene: str | None = None
    emotion: str | None = None
    visual_keywords: list[Any] = Field(default_factory=list)


class OutlineRevisionCreate(BaseModel):
    bible_revision_id: str | None = None
    chapters: list[OutlineChapterInput] = Field(min_length=1)
    activate: bool = True


class OutlineRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    bible_revision_id: str
    parent_revision_id: str | None
    revision_no: int
    source_hash: str
    content_hash: str
    status: str
    approved_at: datetime | None
    generation_task_id: str | None
    created_at: datetime
    chapters: list[OutlineChapterInput] = Field(default_factory=list)


class ChapterRevisionCreate(BaseModel):
    content: str = Field(min_length=1)
    bible_revision_id: str | None = None
    outline_revision_id: str | None = None
    state_snapshot_id: str | None = None
    activate: bool = True


class ChapterRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    chapter_index: int
    parent_revision_id: str | None
    bible_revision_id: str
    outline_revision_id: str
    state_snapshot_id: str | None
    revision_no: int
    source_hash: str
    content_hash: str
    content: str
    status: str
    generation_task_id: str | None
    created_at: datetime


class ChapterScriptGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_revision_id: str = Field(min_length=1, max_length=36)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChapterScriptRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    chapter_index: int
    chapter_revision_id: str
    bible_revision_id: str
    outline_revision_id: str
    parent_revision_id: str | None
    revision_no: int
    source_hash: str
    script_hash: str
    script_json: dict[str, Any]
    coverage_json: dict[str, Any]
    schema_version: str
    generator_version: str
    status: str
    generation_task_id: str | None
    created_at: datetime


class ScriptResourceSlotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    chapter_script_revision_id: str
    slot_key: str
    role: str
    scene_id: str
    paragraph_id: str | None
    character_id: str | None
    order_index: int
    required: bool
    status: str
    asset_id: int | None
    asset_version_id: str | None
    generation_task_id: str | None
    spec_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ScriptResourceSlotBindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_version_id: str = Field(min_length=1, max_length=36)


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str | None = Field(default=None, max_length=12_000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ChapterGenerationRequest(GenerationRequest):
    """Chapter generation with optional materialized-draft anchors.

    bible_revision_id / outline_revision_id 必须成对出现（resolver 契约）；
    不传时沿用当前 head，行为与旧版完全一致。
    """

    bible_revision_id: str | None = Field(default=None, min_length=36, max_length=36)
    outline_revision_id: str | None = Field(default=None, min_length=36, max_length=36)
    ancestor_revision_overrides: dict[str, str] | None = Field(default=None)


class ScriptResourceRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["portrait", "background", "keyframe"]


class ChapterBatchGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_indexes: list[int] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
