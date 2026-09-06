from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.routers import llm as llm_router


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(llm_router.router, prefix="/api/llm")
    return app


def test_byok_defaults_returns_masked_fields(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")

    client = TestClient(_app())
    response = client.get("/api/llm/byok-defaults")

    assert response.status_code == 200
    body = response.json()
    assert body["base_url"] == "https://api.deepseek.com"
    assert body["model"] == "deepseek-chat"
    # Mask keeps the first and last character of the host (no path here).
    assert body["base_url_masked"].startswith("https://a")
    assert body["base_url_masked"].endswith("m")
    assert "*" in body["base_url_masked"]
    # Model mask keeps first + last char.
    assert body["model_masked"].startswith("d")
    assert body["model_masked"].endswith("t")
    assert "*" in body["model_masked"]
    # Masked values must differ from raw ones.
    assert body["base_url_masked"] != body["base_url"]
    assert body["model_masked"] != body["model"]


def test_byok_defaults_preserves_path_in_mask(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")

    client = TestClient(_app())
    response = client.get("/api/llm/byok-defaults")

    assert response.status_code == 200
    body = response.json()
    assert body["base_url_masked"].endswith("/v1")


def test_byok_defaults_is_cache_no_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    client = TestClient(_app())
    response = client.get("/api/llm/byok-defaults")

    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    assert response.json()["base_url"] == "https://api.deepseek.com"
    assert response.json()["model"] == "deepseek-v4-flash"


def test_byok_defaults_works_unauthenticated(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o")

    client = TestClient(_app())
    # No auth header of any kind; the endpoint is intentionally public because
    # it only exposes masked hints.
    response = client.get("/api/llm/byok-defaults")
    assert response.status_code == 200
    assert response.json()["model"] == "gpt-4o"
