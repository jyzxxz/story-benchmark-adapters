from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True)
class ModelCallResult(Generic[T]):
    data: T
    raw_text: str | None
    provider: str
    model: str
    provider_request_id: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    prompt_version: str

