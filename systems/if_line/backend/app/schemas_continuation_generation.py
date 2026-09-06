from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContinuationCharacterIntent(BaseModel):
    # Providers may enrich scene characters with harmless descriptive fields
    # such as name or costume. Keep those additions instead of rejecting the
    # entire generated passage.
    model_config = ConfigDict(extra="allow")

    name: str | None = Field(default=None, max_length=128)
    description: str = Field(min_length=1, max_length=1000)
    identity_group: str | None = Field(default=None, max_length=128)
    expression: str | None = Field(default=None, max_length=64)
    pose: str | None = Field(default=None, max_length=64)


class ContinuationSceneIntent(BaseModel):
    model_config = ConfigDict(extra="allow")

    background: str = Field(min_length=1, max_length=2000)
    style: str | None = Field(default=None, max_length=64)
    taxonomy: dict[str, Any] = Field(default_factory=dict)
    characters: list[ContinuationCharacterIntent] = Field(default_factory=list, max_length=2)

    @field_validator("taxonomy", mode="before")
    @classmethod
    def _normalize_taxonomy(cls, value: Any) -> Any:
        # OpenAI-compatible providers occasionally summarize taxonomy as a
        # label instead of an object. Preserve that useful value in a stable
        # shape instead of discarding an otherwise valid continuation.
        if value is None:
            return {}
        if isinstance(value, str):
            return {"description": value.strip()}
        if isinstance(value, list):
            return {"items": value}
        return value


class GeneratedContinuation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    continuation_text: str = Field(min_length=1, max_length=12000)
    state_delta: dict[str, Any] = Field(default_factory=dict)
    scene_intent: ContinuationSceneIntent

    @field_validator("continuation_text", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class GeneratedDirectionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: str = Field(min_length=1, max_length=500)

    @field_validator("direction", mode="before")
    @classmethod
    def _strip_direction(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value
