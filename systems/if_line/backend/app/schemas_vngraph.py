from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.vn_graph_compiler import (
    VNGRAPH_COMPILER_VERSION,
    VNGRAPH_SCHEMA_VERSION,
    VNGRAPH_TACHI_POLICY_VERSION,
)


class VNGraphCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    script_revision_id: str | None = Field(default=None, min_length=1, max_length=36)
    schema_version: str = Field(default=VNGRAPH_SCHEMA_VERSION, min_length=1, max_length=32)
    compiler_version: str = Field(default=VNGRAPH_COMPILER_VERSION, min_length=1, max_length=32)
    tachi_policy_version: str = Field(
        default=VNGRAPH_TACHI_POLICY_VERSION,
        min_length=1,
        max_length=32,
    )

    @model_validator(mode="after")
    def require_revision_source(self):
        if not self.script_revision_id and not self.chapter_revision_id:
            raise ValueError("必须提供 script_revision_id")
        return self


class VNGraphRevisionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: int
    chapter_index: int
    chapter_revision_id: str
    script_revision_id: str
    parent_revision_id: str | None
    revision_no: int
    binding_manifest: dict[str, Any]
    binding_manifest_hash: str
    graph_hash: str
    graph_json: dict[str, Any]
    schema_version: str
    compiler_version: str
    tachi_policy_version: str
    status: str
    generation_task_id: str | None
    created_at: datetime
