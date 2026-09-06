from app.services.llm_service import LLMService


def test_llm_service_defaults_to_deepseek_and_has_no_mock_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEYS", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    service = LLMService()

    assert service.model == "deepseek-v4-flash"
    assert service.client._client_kwargs["base_url"] == "https://api.deepseek.com"
    assert service.client.pool.size == 0
