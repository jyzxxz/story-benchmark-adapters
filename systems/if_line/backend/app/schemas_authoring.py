"""Request and response models for the StoryPath authoring API."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    characters: list[dict[str, Any]] = Field(default_factory=list)
    story_start: str = ""
    story_end: str = ""
    style: str = ""
    source_work: str | None = None
    pace: Literal["fast", "medium", "slow"] | None = None
    extra_requirements: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized


class ProjectPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    characters: list[dict[str, Any]] | None = None
    story_start: str | None = None
    story_end: str | None = None
    style: str | None = None
    source_work: str | None = None
    pace: Literal["fast", "medium", "slow"] | None = None
    extra_requirements: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_patch(self) -> "ProjectPatch":
        if not self.model_fields_set:
            raise ValueError("at least one project field is required")
        for field in {"title", "characters", "story_start", "story_end", "style"}:
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class BlockingItem(BaseModel):
    code: str
    story_path_id: str | None = None
    path_chapter_id: str | None = None
    detail: str | None = None


class AuthoringProjection(BaseModel):
    stage: Literal[
        "bible",
        "story_paths",
        "outlines",
        "chapters",
        "scripts",
        "vn_graphs",
        "ready",
        "blocked",
    ]
    blocking_items: list[BlockingItem]
    running_task_count: int = Field(ge=0)
    has_unpublished_changes: bool


class PublicationProjection(BaseModel):
    state: Literal["unpublished", "published", "changes_pending"]
    active_release_id: str | None
    published_at: datetime | None
    lock_version: int = Field(ge=1)


class ProjectRead(ProjectCreate):
    id: int
    root_story_path_id: str | None
    authoring: AuthoringProjection
    publication: PublicationProjection


class PublicationReadiness(BaseModel):
    ready: bool
    authoring_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    blocking_items: list[BlockingItem]


class BibleRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_revision_id: str | None = None
    content_json: dict[str, Any]


class BibleRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    parent_revision_id: str | None
    revision_no: int = Field(ge=1)
    source_hash: str
    content_hash: str
    content_json: dict[str, Any]
    created_at: datetime


class RevisionHead(BaseModel):
    revision_id: str | None
    lock_version: int = Field(ge=1)
    updated_at: datetime


class HeadUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str = Field(min_length=36, max_length=36)


class StoryPathCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=36, max_length=36)
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class StoryPathPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    status: Literal["active", "archived"] | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("title cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_patch(self) -> "StoryPathPatch":
        if not self.model_fields_set:
            raise ValueError("at least one StoryPath field is required")
        if any(getattr(self, field) is None for field in self.model_fields_set):
            raise ValueError("StoryPath patch fields cannot be null")
        return self


class StoryPath(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    parent_path_id: str | None
    fork_path_chapter_id: str | None
    fork_checkpoint_node_id: str | None
    fork_candidate_id: str | None
    base_state_snapshot_id: str | None
    title: str
    status: Literal["active", "archived"]
    lock_version: int = Field(ge=1)


class OutlineGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_count: int = Field(ge=1, le=500)
    instructions: str | None = Field(default=None, max_length=12_000)
    # 草稿物化锚点：不传时沿用 bible head（行为与旧版一致）。
    bible_revision_id: str | None = Field(default=None, min_length=36, max_length=36)


class OutlineChapterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    story_path_chapter_id: str | None = None
    display_index: int = Field(ge=1)
    title: str
    summary: str
    conflict: str | None = None
    characters: list[str] = Field(default_factory=list)
    scene: str | None = None
    emotion: str | None = None
    visual_keywords: list[str] = Field(default_factory=list)


class OutlineRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bible_revision_id: str = Field(min_length=36, max_length=36)
    parent_revision_id: str | None = None
    chapters: list[OutlineChapterInput] = Field(min_length=1, max_length=500)


class OutlineRevision(OutlineRevisionCreate):
    id: str
    story_path_id: str
    revision_no: int = Field(ge=1)
    source_hash: str
    content_hash: str
    status: str


class PathChapterCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    after_path_chapter_id: str | None = None


class PathChapter(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    story_path_id: str
    chapter_slot_id: str
    display_index: int = Field(ge=1)
    predecessor_path_chapter_id: str | None
    inherited_from_path_chapter_id: str | None
    current_revision_id: str | None
    status: Literal["active", "detached"]
    detached_at: datetime | None
    detached_by_outline_revision_id: str | None
    lock_version: int = Field(ge=1)


class ChapterBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_chapter_ids: list[str] = Field(min_length=1)
    instructions: str | None = Field(default=None, max_length=12_000)
    """可选物化草稿锚点：与单章生成同契约，成对出现；不传时沿用 head。"""
    bible_revision_id: str | None = Field(default=None, min_length=36, max_length=36)
    outline_revision_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "ChapterBatchRequest":
        if len(set(self.path_chapter_ids)) != len(self.path_chapter_ids):
            raise ValueError("path_chapter_ids must be unique")
        if (self.bible_revision_id is None) != (self.outline_revision_id is None):
            raise ValueError(
                "bible_revision_id and outline_revision_id must be provided together"
            )
        return self


class ChapterRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent_revision_id: str | None = None
    content: str = Field(min_length=1)


class ChapterRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_slot_id: str
    created_for_story_path_id: str
    parent_revision_id: str | None
    revision_no: int = Field(ge=1)
    context_manifest: dict[str, Any]
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str
