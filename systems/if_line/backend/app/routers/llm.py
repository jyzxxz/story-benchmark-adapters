"""Read-only LLM provider hints for the BYOK dialog.

The endpoint returns the server-resolved text-LLM base URL and model in both
raw and masked forms so the frontend can show placeholders without leaking the
full provider hostname.  Only redacted strings are exposed; no secrets.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_BASE_URL,
    DEFAULT_TEXT_LLM_MODEL,
    text_llm_base_url,
)


router = APIRouter()


def _mask_url(raw: str) -> str:
    """Keep scheme + path, replace the host's middle with asterisks.

    ``https://api.deepseek.com/v1`` -> ``https://a**********m/v1``
    """
    if not raw:
        return ""
    parsed = urlparse(raw)
    host = parsed.hostname or ""
    if not host:
        return raw
    if len(host) <= 2:
        masked_host = "*" * len(host)
    else:
        masked_host = host[0] + "*" * (len(host) - 2) + host[-1]
    netloc = masked_host
    if parsed.port:
        netloc = f"{masked_host}:{parsed.port}"
    if parsed.username:
        netloc = f"{parsed.username}@{netloc}"
    return f"{parsed.scheme}://{netloc}{parsed.path}"


def _mask_model(raw: str) -> str:
    """Keep the first and last character, mask the middle."""
    if not raw:
        return ""
    if len(raw) <= 2:
        return "*" * len(raw)
    return raw[0] + "*" * (len(raw) - 2) + raw[-1]


# GET /api/llm/byok-defaults
@router.get("/byok-defaults")
def byok_defaults() -> Any:
    base_url = text_llm_base_url(default=DEFAULT_TEXT_LLM_BASE_URL)
    model = os.getenv("LLM_MODEL", DEFAULT_TEXT_LLM_MODEL)
    return JSONResponse(
        {
            "base_url": base_url,
            "model": model,
            "base_url_masked": _mask_url(base_url),
            "model_masked": _mask_model(model),
        },
        headers={"Cache-Control": "no-store"},
    )
