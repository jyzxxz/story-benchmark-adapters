from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import StreamingResponse

from app.core.config import AppSettings
from app.core.middleware import install_http_middleware
from app.services.api_key_pool import (
    BYOK_BASE_URL_HEADER,
    BYOK_HEADER,
    BYOK_MODEL_HEADER,
    RequestApiKeyMiddleware,
    get_request_api_key,
    get_request_base_url,
    get_request_model,
)


@pytest.fixture(autouse=True)
def _disable_runtime_log_sinks(monkeypatch: pytest.MonkeyPatch):
    from app.core import middleware as middleware_module

    monkeypatch.setattr(middleware_module.xlog, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(middleware_module.xlog, "error", lambda *_args, **_kwargs: None)


def _test_app(tmp_path: Path) -> FastAPI:
    app = FastAPI()
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        static_dir=tmp_path / "static",
        metrics_enabled=False,
    )
    install_http_middleware(app, settings)

    @app.get("/inspect-byok")
    async def inspect_byok():
        return {"byok_present": get_request_api_key() is not None}

    @app.get("/inspect-byok-header")
    async def inspect_byok_header(request: Request):
        return {
            "byok_present": get_request_api_key() is not None,
            "header_visible_downstream": BYOK_HEADER.lower() in request.headers,
        }

    @app.get("/inspect-byok-all")
    async def inspect_byok_all(request: Request):
        all_byok_headers = {
            BYOK_HEADER.lower(),
            BYOK_BASE_URL_HEADER.lower(),
            BYOK_MODEL_HEADER.lower(),
        }
        visible = {name.lower() for name in request.headers.keys()} & all_byok_headers
        return {
            "api_key_present": get_request_api_key() is not None,
            "base_url_present": get_request_base_url() is not None,
            "model_present": get_request_model() is not None,
            "base_url": get_request_base_url(),
            "model": get_request_model(),
            "visible_downstream": sorted(visible),
        }

    @app.get("/stream-byok")
    async def stream_byok():
        async def chunks():
            yield b"present" if get_request_api_key() else b"missing"
            await asyncio.sleep(0)
            yield b"|present" if get_request_api_key() else b"|missing"

        return StreamingResponse(chunks(), media_type="text/plain")

    @app.get("/fail-byok")
    async def fail_byok():
        assert get_request_api_key() is not None
        raise RuntimeError("route failed without embedding the credential")

    return app


def test_byok_header_is_request_scoped_and_not_reflected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    from app.core import middleware as middleware_module

    messages: list[str] = []

    def capture(_request_id, message, *args):
        messages.append(message % args if args else message)

    monkeypatch.setattr(middleware_module.xlog, "info", capture)
    monkeypatch.setattr(
        middleware_module.xlog,
        "error",
        lambda request_id, exc, message, *args: capture(request_id, message, *args),
    )
    secret = "user-owned-key-that-must-never-be-reflected"

    with TestClient(_test_app(tmp_path)) as client:
        response = client.get("/inspect-byok", headers={BYOK_HEADER: secret})
        following_request = client.get("/inspect-byok")

    assert response.status_code == 200
    assert response.json() == {"byok_present": True}
    assert following_request.json() == {"byok_present": False}
    assert get_request_api_key() is None

    serialized_headers = "\n".join(f"{name}: {value}" for name, value in response.headers.items())
    assert secret not in response.text
    assert secret not in serialized_headers
    assert secret not in "\n".join(messages)


def test_byok_capture_is_the_outermost_user_middleware(tmp_path: Path):
    app = _test_app(tmp_path)

    assert app.user_middleware[0].cls is RequestApiKeyMiddleware


def test_byok_header_is_removed_before_router_and_context_survives_stream_body(tmp_path: Path):
    secret = "stream-request-secret"
    with TestClient(_test_app(tmp_path)) as client:
        inspected = client.get("/inspect-byok-header", headers={BYOK_HEADER: secret})
        streamed = client.get("/stream-byok", headers={BYOK_HEADER: secret})

    assert inspected.status_code == 200
    assert inspected.json() == {
        "byok_present": True,
        "header_visible_downstream": False,
    }
    assert streamed.status_code == 200
    assert streamed.text == "present|present"
    assert secret not in streamed.text
    assert secret not in "\n".join(streamed.headers.values())
    assert get_request_api_key() is None


def test_request_context_is_revoked_when_route_raises(tmp_path: Path):
    with TestClient(_test_app(tmp_path)) as client:
        with pytest.raises(RuntimeError, match="route failed"):
            client.get("/fail-byok", headers={BYOK_HEADER: "exception-request-secret"})

    assert get_request_api_key() is None


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        "\t",
        "x" * 10_000,
    ],
)
def test_invalid_byok_header_is_rejected_before_reaching_route(tmp_path: Path, value: str):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get("/inspect-byok", headers={BYOK_HEADER: value})

    assert response.status_code in {400, 431}
    assert response.json()["detail"] == "Invalid LLM API key header"
    if value.strip():
        assert value.strip() not in response.text
    assert get_request_api_key() is None


def test_duplicate_byok_headers_are_rejected(tmp_path: Path):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok",
            headers=[
                (BYOK_HEADER, "first-user-key"),
                (BYOK_HEADER, "second-user-key"),
            ],
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid LLM API key header"
    assert "first-user-key" not in response.text
    assert "second-user-key" not in response.text
    assert get_request_api_key() is None


def test_legacy_routing_headers_are_ignored_without_a_byok_key(tmp_path: Path):
    """Headers we do not capture must not produce a BYOK lease."""
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok",
            headers={
                "X-OpenAI-API-Key": "legacy-key-must-not-be-consumed",
            },
        )

    assert response.status_code == 200
    assert response.json() == {"byok_present": False}
    assert get_request_api_key() is None


def test_byok_base_url_and_model_are_captured_and_stripped(tmp_path: Path):
    secret = "user-owned-key"
    base_url = "https://api.deepseek.com/v1"
    model = "deepseek-chat"
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={
                BYOK_HEADER: secret,
                BYOK_BASE_URL_HEADER: base_url,
                BYOK_MODEL_HEADER: model,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key_present"] is True
    assert body["base_url_present"] is True
    assert body["model_present"] is True
    assert body["base_url"] == base_url
    assert body["model"] == model
    assert body["visible_downstream"] == []
    assert get_request_api_key() is None
    assert get_request_base_url() is None
    assert get_request_model() is None


def test_byok_base_url_and_model_are_optional(tmp_path: Path):
    secret = "user-owned-key"
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={BYOK_HEADER: secret},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["api_key_present"] is True
    assert body["base_url_present"] is False
    assert body["model_present"] is False
    assert body["base_url"] is None
    assert body["model"] is None
    assert body["visible_downstream"] == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1",
        "http://127.0.0.1:8080",
        "http://localhost",
        "http://localhost:8080",
        "http://10.1.2.3",
        "http://192.168.0.1",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]",
        "http://metadata.google.internal",
        "ftp://example.com",
        "javascript:alert(1)",
        "not-a-url",
        "",
    ],
)
def test_invalid_byok_base_url_is_rejected(tmp_path: Path, base_url: str):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={
                BYOK_HEADER: "valid-key",
                BYOK_BASE_URL_HEADER: base_url,
            },
        )

    assert response.status_code in {400, 431}
    assert response.json()["detail"] == "Invalid LLM API key header"
    assert get_request_base_url() is None


def test_oversize_byok_base_url_is_rejected(tmp_path: Path):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={
                BYOK_HEADER: "valid-key",
                BYOK_BASE_URL_HEADER: "https://example.com/" + ("a" * 600),
            },
        )

    assert response.status_code == 431
    assert get_request_base_url() is None


def test_duplicate_byok_base_url_headers_are_rejected(tmp_path: Path):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers=[
                (BYOK_HEADER, "valid-key"),
                (BYOK_BASE_URL_HEADER, "https://a.example.com"),
                (BYOK_BASE_URL_HEADER, "https://b.example.com"),
            ],
        )

    assert response.status_code == 400
    assert get_request_base_url() is None


@pytest.mark.parametrize(
    "model",
    [
        "glm,4",
        "gpt 4",
        "model\nname",
        "with;semicolon",
        "",
        "x" * 300,
    ],
)
def test_invalid_byok_model_is_rejected(tmp_path: Path, model: str):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={
                BYOK_HEADER: "valid-key",
                BYOK_MODEL_HEADER: model,
            },
        )

    assert response.status_code in {400, 431}
    assert response.json()["detail"] == "Invalid LLM API key header"
    assert get_request_model() is None


def test_duplicate_byok_model_headers_are_rejected(tmp_path: Path):
    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers=[
                (BYOK_HEADER, "valid-key"),
                (BYOK_MODEL_HEADER, "glm-4"),
                (BYOK_MODEL_HEADER, "glm-4.6"),
            ],
        )

    assert response.status_code == 400
    assert get_request_model() is None


def test_valid_base_url_resolution_is_required(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An unroutable hostname is treated the same as a private IP."""
    import socket as socket_module

    def _raise(*_args, **_kwargs):
        raise OSError("dns hard fail")

    monkeypatch.setattr(socket_module, "getaddrinfo", _raise)

    with TestClient(_test_app(tmp_path)) as client:
        response = client.get(
            "/inspect-byok-all",
            headers={
                BYOK_HEADER: "valid-key",
                BYOK_BASE_URL_HEADER: "https://this-host-does-not-resolve.invalid",
            },
        )

    assert response.status_code == 400
    assert get_request_base_url() is None
