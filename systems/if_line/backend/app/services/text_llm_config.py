"""Shared environment resolution for text-only LLM calls.

Image generation uses ``AI_IMAGE_*`` and may point at CogView. Text analysis,
rewriting, segmentation, and classification should prefer the project's text
LLM settings instead.
"""
import os
from typing import Any, Optional
from urllib.parse import urlparse


DEFAULT_TEXT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_TEXT_LLM_MODEL = "deepseek-v4-flash"


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def text_llm_model(*specific_names: str, default: str) -> str:
    """Resolve a text model name."""
    return _first_env(
        *specific_names,
        "PROMPT_REWRITER_MODEL",
        "LLM_MODEL",
        default=default,
    )


def text_llm_api_key(*specific_names: str) -> str:
    """Resolve a text LLM API key.

    Image-provider credentials are deliberately excluded: a missing text key
    must fail closed instead of consuming an unrelated image resource pack.
    """
    return _first_env(
        *specific_names,
        "OPENAI_API_KEY",
        default="",
    )


def text_llm_base_url(*specific_names: str, default: Optional[str] = None) -> str:
    """Resolve a text LLM base URL."""
    return _first_env(
        *specific_names,
        "OPENAI_BASE_URL",
        default=default or DEFAULT_TEXT_LLM_BASE_URL,
    )


def resolve_request_model(default: str) -> str:
    """Return the BYOK model override for the current request, if any.

    Falls back to ``default`` when no BYOK model header was supplied.  Kept here
    (rather than in ``api_key_pool``) so text-only modules have a single import
    for both env-resolution and request-scoped overrides.
    """

    from app.services.api_key_pool import get_request_model

    override = get_request_model()
    return override or default


def structured_json_request_options(
    model: str,
    *,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Disable DeepSeek thinking for bounded machine-readable responses.

    DeepSeek V4 enables thinking by default and counts reasoning tokens against
    ``max_tokens``. Short JSON tasks can otherwise finish with a truncated or
    empty ``content`` value. Keep this provider-specific option away from
    arbitrary OpenAI-compatible endpoints.
    """

    from app.services.api_key_pool import get_request_base_url

    resolved_url = base_url or get_request_base_url() or text_llm_base_url()
    hostname = (urlparse(resolved_url).hostname or "").lower()
    if hostname == "api.deepseek.com" and model.strip():
        return {"extra_body": {"thinking": {"type": "disabled"}}}
    return {}
