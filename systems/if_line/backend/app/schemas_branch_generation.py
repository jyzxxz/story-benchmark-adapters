from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_INSTRUCTIONS_CHARS = 1000
MAX_PREVIEW_CHARS = 1200
MAX_PREVIEW_BYTES = 4096
MAX_STATE_DELTA_BYTES = 16 * 1024
MAX_STATE_DELTA_DEPTH = 8
MAX_STATE_DELTA_NODES = 256
OPTION_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_json_object(
    value: dict[str, Any],
    *,
    max_bytes: int,
    max_depth: int,
    max_nodes: int,
) -> dict[str, Any]:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > max_nodes:
            raise ValueError("JSON 对象节点过多")
        if depth > max_depth:
            raise ValueError("JSON 对象嵌套过深")
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str) or not key or len(key) > 64:
                    raise ValueError("状态键必须是 1-64 个字符的字符串")
                if key.startswith("__") or key.startswith("$"):
                    raise ValueError("状态键不能使用保留前缀")
                stack.append((item, depth + 1))
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("状态差分必须是有效 JSON") from exc
    if len(encoded) > max_bytes:
        raise ValueError(f"状态差分不能超过 {max_bytes} bytes")
    return value


class BranchCandidateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkpoint_node_id: str = Field(min_length=1, max_length=36)
    chapter_revision_id: str = Field(min_length=1, max_length=36)
    state_snapshot_id: str | None = Field(default=None, min_length=1, max_length=36)
    candidate_count: int = Field(default=3, ge=2, le=4)
    instructions: str = Field(default="", max_length=MAX_INSTRUCTIONS_CHARS)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str) -> str:
        value = value.strip()
        if "\x00" in value:
            raise ValueError("instructions 包含非法字符")
        return value


class BranchGenerationAccepted(BaseModel):
    task_id: str
    status: str
    events_url: str
    created: bool
    source_hash: str
    candidate_count: int


class GeneratedBranchCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_key: str = Field(min_length=1, max_length=64)
    preview_text: str = Field(min_length=1, max_length=MAX_PREVIEW_CHARS)
    state_delta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("option_key")
    @classmethod
    def normalize_option_key(cls, value: str) -> str:
        value = value.strip().lower()
        if not OPTION_KEY_RE.fullmatch(value):
            raise ValueError("option_key 只能包含小写字母、数字、下划线和短横线")
        return value

    @field_validator("preview_text")
    @classmethod
    def validate_preview_text(cls, value: str) -> str:
        value = value.strip()
        if not value or "\x00" in value:
            raise ValueError("preview_text 不能为空或包含非法字符")
        if len(value.encode("utf-8")) > MAX_PREVIEW_BYTES:
            raise ValueError("preview_text 不能超过 4096 bytes")
        return value

    @field_validator("state_delta")
    @classmethod
    def validate_state_delta(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_json_object(
            value,
            max_bytes=MAX_STATE_DELTA_BYTES,
            max_depth=MAX_STATE_DELTA_DEPTH,
            max_nodes=MAX_STATE_DELTA_NODES,
        )


class GeneratedBranchCandidateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[GeneratedBranchCandidate] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_unique_options(self) -> "GeneratedBranchCandidateBatch":
        option_keys = [candidate.option_key for candidate in self.candidates]
        if len(option_keys) != len(set(option_keys)):
            raise ValueError("候选 option_key 不能重复")
        total_preview_bytes = sum(len(item.preview_text.encode("utf-8")) for item in self.candidates)
        if total_preview_bytes > 12 * 1024:
            raise ValueError("候选预览文本总量过大")
        return self


class GeneratedBranchCandidateRead(BaseModel):
    id: str
    option_key: str
    preview_node_id: str
    preview_text: str
    state_delta: dict[str, Any]
    candidate_status: str
    source_hash: str
