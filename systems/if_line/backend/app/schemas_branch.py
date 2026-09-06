from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_STATE_JSON_BYTES = 64 * 1024
MAX_STATE_DEPTH = 16


def _validate_state(value: dict[str, Any]) -> dict[str, Any]:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_STATE_DEPTH:
            raise ValueError("状态对象嵌套过深")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
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
        raise ValueError("状态对象必须是有效 JSON") from exc
    if len(encoded) > MAX_STATE_JSON_BYTES:
        raise ValueError("状态对象不能超过 64 KiB")
    return value


class ReadingSessionCreate(BaseModel):
    project_id: int = Field(ge=1)
    release_id: str = Field(min_length=1, max_length=64)
    initial_state: dict[str, Any] = Field(default_factory=dict)

    _bounded_initial_state = field_validator("initial_state")(_validate_state)


class ReadingSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: int
    project_id: int
    release_id: str
    head_node_id: str | None
    state_snapshot_id: str | None
    selected_continuation_id: str | None
    parent_session_id: str | None
    forked_from_decision_id: str | None
    lock_version: int
    status: str
    created_at: datetime
    updated_at: datetime


class ChoiceCreate(BaseModel):
    checkpoint_node_id: str = Field(min_length=1, max_length=64)
    option_key: str = Field(min_length=1, max_length=128)

    @field_validator("option_key")
    @classmethod
    def normalize_option_key(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("option_key 不能为空")
        return value


class ChoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    checkpoint_node_id: str
    option_key: str
    candidate_id: str
    previous_node_id: str | None
    result_node_id: str | None
    previous_snapshot_id: str | None
    result_snapshot_id: str | None
    result_revision_id: str | None
    undone_at: datetime | None
    created_at: datetime


class ChoiceResult(BaseModel):
    decision: ChoiceRead
    session: ReadingSessionRead
    created: bool


class ForkCreate(BaseModel):
    decision_id: str | None = Field(default=None, max_length=64)


class BookmarkCreate(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    label: str | None = Field(default=None, max_length=200)


class BookmarkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    node_id: str
    label: str | None
    created_at: datetime
