"""Schemas for branch, artifact, graph, and publication replacement APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CandidateSetGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_revision_id: str = Field(min_length=36, max_length=36)
    state_snapshot_id: str = Field(min_length=36, max_length=36)
    candidate_count: int = Field(ge=2, le=4)
    instructions: str | None = Field(default=None, max_length=12_000)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if "\x00" in normalized:
            raise ValueError("instructions contain an invalid character")
        return normalized or None


class CandidateSetRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    story_path_id: str
    checkpoint_node_id: str
    chapter_revision_id: str
    state_snapshot_id: str
    revision_no: int = Field(ge=1)
    source_hash: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class BranchCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    candidate_set_revision_id: str
    option_key: str
    preview_text: str
    state_delta: dict[str, Any]


class ScriptRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    chapter_revision_id: str
    revision_no: int = Field(ge=1)
    source_hash: str
    script_hash: str
    script_json: dict[str, Any]


class ScriptRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    script_json: dict[str, Any]
    parent_revision_id: str | None = Field(default=None, min_length=36, max_length=36)
    auto_register_characters: bool = True


class ScriptRevisionCreated(ScriptRevision):
    appended_character_names: list[str] | None = None


class ResourceSlot(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    script_revision_id: str = Field(validation_alias="chapter_script_revision_id")
    role: str
    required: bool
    status: Literal["planned", "generating", "bound", "failed"]
    asset_id: int | None
    asset_version_id: str | None
    lock_version: int = Field(ge=1)


class ResourceRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["portrait", "background", "keyframe"]


class ResourceSlotBind(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_version_id: str = Field(min_length=36, max_length=36)


class VNGraphCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    compiler_version: str | None = Field(default=None, min_length=1, max_length=32)
    schema_version: str | None = Field(default=None, min_length=1, max_length=32)


class VNGraphRevision(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    script_revision_id: str
    binding_manifest: dict[str, Any]
    binding_manifest_hash: str
    graph_hash: str
    graph_json: dict[str, Any]


class VNGraphRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_json: dict[str, Any]
    parent_revision_id: str | None = Field(default=None, min_length=36, max_length=36)


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_authoring_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_notes: str | None = Field(default=None, max_length=2_000)


class FinalizePublishRequest(BaseModel):
    """固化发布请求：物化草稿修订 id 集（只含有草稿的 subject）。

    各 dict 键：outline→story_path_id；chapter/script/graph→path_chapter_id。
    不传的类别沿用当前 head，行为等同直接 publish。
    """

    model_config = ConfigDict(extra="forbid")

    bible_revision_id: str | None = Field(default=None, min_length=36, max_length=36)
    outline_revision_ids: dict[str, str] = Field(default_factory=dict)
    chapter_revision_ids: dict[str, str] = Field(default_factory=dict)
    script_revision_ids: dict[str, str] = Field(default_factory=dict)
    graph_revision_ids: dict[str, str] = Field(default_factory=dict)
    release_notes: str | None = Field(default=None, max_length=2_000)


class Release(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    version: int = Field(ge=1)
    status: Literal["published", "superseded", "withdrawn"]
    manifest_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    authoring_fingerprint: str | None = Field(pattern=r"^[0-9a-f]{64}$")
    release_notes: str | None
    published_at: datetime | None
    withdrawn_at: datetime | None


class ReleaseManifest(BaseModel):
    release_id: str
    version: int
    manifest_hash: str
    manifest: dict[str, Any]


class PublicProject(BaseModel):
    id: int
    title: str
    summary: str
    cover_url: str | None
    release_id: str
    release_version: int
    manifest_hash: str
    published_at: datetime


__all__ = [
    "BranchCandidate",
    "CandidateSetGenerationRequest",
    "CandidateSetRevision",
    "FinalizePublishRequest",
    "PublicProject",
    "PublishRequest",
    "Release",
    "ReleaseManifest",
    "ResourceRenderRequest",
    "ResourceSlot",
    "ResourceSlotBind",
    "ScriptRevision",
    "ScriptRevisionCreated",
    "ScriptRevisionCreate",
    "VNGraphCompileRequest",
    "VNGraphRevision",
    "VNGraphRevisionCreate",
]
