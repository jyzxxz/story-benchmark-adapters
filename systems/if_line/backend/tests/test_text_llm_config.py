import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def test_text_llm_config_prefers_openai_over_ai_image(monkeypatch):
    monkeypatch.delenv("REWRITER_API_KEY", raising=False)
    monkeypatch.delenv("REWRITER_BASE_URL", raising=False)
    monkeypatch.delenv("PROMPT_REWRITER_MODEL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-text")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("AI_IMAGE_API_KEY", "zhipu-image-key")
    monkeypatch.setenv("AI_IMAGE_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    monkeypatch.setenv("AI_IMAGE_MODEL", "cogview-4")

    from app.services.text_llm_config import text_llm_api_key, text_llm_base_url, text_llm_model

    assert text_llm_api_key("REWRITER_API_KEY") == "sk-text"
    assert text_llm_base_url("REWRITER_BASE_URL") == "https://api.deepseek.com"
    assert text_llm_model("PROMPT_REWRITER_MODEL", default="glm-4-flash") == "deepseek-v4-pro"


def test_text_llm_config_allows_module_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-text")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("BG_ANALYZER_API_KEY", "sk-bg")
    monkeypatch.setenv("BG_ANALYZER_BASE_URL", "https://bg.example/v1")
    monkeypatch.setenv("BG_ANALYZER_MODEL", "bg-model")

    from app.services.text_llm_config import text_llm_api_key, text_llm_base_url, text_llm_model

    assert text_llm_api_key("BG_ANALYZER_API_KEY") == "sk-bg"
    assert text_llm_base_url("BG_ANALYZER_BASE_URL") == "https://bg.example/v1"
    assert text_llm_model("BG_ANALYZER_MODEL", default="glm-4-plus") == "bg-model"


def test_text_llm_config_defaults_to_deepseek(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("AI_IMAGE_API_KEY", "image-key-must-not-leak")
    monkeypatch.setenv("AI_IMAGE_BASE_URL", "https://image-provider.example/v1")

    from app.services.text_llm_config import (
        DEFAULT_TEXT_LLM_BASE_URL,
        DEFAULT_TEXT_LLM_MODEL,
        text_llm_api_key,
        text_llm_base_url,
    )

    assert DEFAULT_TEXT_LLM_BASE_URL == "https://api.deepseek.com"
    assert DEFAULT_TEXT_LLM_MODEL == "deepseek-v4-flash"
    assert text_llm_api_key() == ""
    assert text_llm_base_url() == DEFAULT_TEXT_LLM_BASE_URL


def test_resolve_request_model_falls_back_to_default():
    from app.services.api_key_pool import request_byok_context
    from app.services.text_llm_config import resolve_request_model

    # Outside any request scope, default must be returned untouched.
    assert resolve_request_model("fallback-model") == "fallback-model"

    # Inside a scope with no model override, default still wins.
    with request_byok_context("byok-key"):
        assert resolve_request_model("fallback-model") == "fallback-model"


def test_resolve_request_model_uses_request_override():
    from app.services.api_key_pool import request_byok_context
    from app.services.text_llm_config import resolve_request_model

    with request_byok_context("byok-key", model="glm-4.6"):
        assert resolve_request_model("fallback-model") == "glm-4.6"

    # And clears after the context exits.
    assert resolve_request_model("fallback-model") == "fallback-model"


def test_structured_json_request_disables_thinking_only_for_deepseek():
    from app.services.text_llm_config import structured_json_request_options

    assert structured_json_request_options(
        "deepseek-v4-flash",
        base_url="https://api.deepseek.com/v1",
    ) == {"extra_body": {"thinking": {"type": "disabled"}}}
    assert structured_json_request_options(
        "gpt-5",
        base_url="https://api.openai.com/v1",
    ) == {}


def test_structured_json_request_honors_byok_base_url(monkeypatch):
    from app.services.api_key_pool import request_byok_context
    from app.services.text_llm_config import structured_json_request_options

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    with request_byok_context(
        "byok-key",
        base_url="https://provider.example/v1",
        model="provider-json-model",
    ):
        assert structured_json_request_options("provider-json-model") == {}
