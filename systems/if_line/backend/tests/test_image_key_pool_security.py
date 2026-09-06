from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from app.services import image_generation_service as image_module
from app.services.api_key_pool import PooledAsyncOpenAI, request_api_key_context
from app.services.background_image_validator_service import BackgroundImageValidatorService
from app.services.image_generation_service import ImageGenerationService


class FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return self.body


class FakeSession:
    def __init__(self, responses: deque[FakeResponse], authorization_headers: list[str]) -> None:
        self.responses = responses
        self.authorization_headers = authorization_headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def post(self, _url: str, *, headers: dict[str, str], **_kwargs: Any) -> FakeResponse:
        self.authorization_headers.append(headers["Authorization"])
        return self.responses.popleft()


def _install_fake_aiohttp(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[FakeResponse],
) -> list[str]:
    import aiohttp

    authorization_headers: list[str] = []
    queued = deque(responses)
    monkeypatch.setattr(
        aiohttp,
        "ClientSession",
        lambda: FakeSession(queued, authorization_headers),
    )
    return authorization_headers


@pytest.mark.asyncio
async def test_qwen_image_pool_switches_on_retryable_failure_and_ignores_text_byok(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_IMAGE_API_KEYS", "image-platform-a,image-platform-b")
    monkeypatch.setattr(image_module, "AI_IMAGE_API_KEY", "")
    service = ImageGenerationService()
    attempted_headers = _install_fake_aiohttp(
        monkeypatch,
        [
            FakeResponse(429, '{"message":"rate limited"}'),
            FakeResponse(
                200,
                '{"output":{"choices":[{"message":{"content":['
                '{"image":"https://images.invalid/generated.png"}'
                ']}}]}}',
            ),
        ],
    )

    async def fake_download(url: str) -> bytes:
        assert url == "https://images.invalid/generated.png"
        return b"image-bytes"

    monkeypatch.setattr(service, "_download_image_url", fake_download)

    with request_api_key_context("text-byok-must-not-reach-image-provider"):
        result = await service._call_qwen_image_api("prompt", 1024, 1024, None)

    assert result == b"image-bytes"
    assert attempted_headers == [
        "Bearer image-platform-a",
        "Bearer image-platform-b",
    ]


@pytest.mark.asyncio
async def test_qwen_nonretryable_rejection_does_not_try_another_key(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("AI_IMAGE_API_KEYS", "image-platform-a,image-platform-b")
    monkeypatch.setattr(image_module, "AI_IMAGE_API_KEY", "")
    service = ImageGenerationService()
    attempted_headers = _install_fake_aiohttp(
        monkeypatch,
        [FakeResponse(400, '{"message":"bad request"}')],
    )

    with pytest.raises(RuntimeError, match="Image provider request failed"):
        await service._call_qwen_image_api("bad prompt", 1024, 1024, None)

    assert attempted_headers == ["Bearer image-platform-a"]


@pytest.mark.asyncio
async def test_qwen_provider_error_never_embeds_platform_key(
    monkeypatch: pytest.MonkeyPatch,
):
    platform_key = "image-platform-secret-must-be-redacted"
    monkeypatch.setenv("AI_IMAGE_API_KEYS", platform_key)
    monkeypatch.setattr(image_module, "AI_IMAGE_API_KEY", "")
    service = ImageGenerationService()
    attempted_headers = _install_fake_aiohttp(
        monkeypatch,
        [
            FakeResponse(
                401,
                '{"code":"InvalidApiKey","message":"rejected credential '
                + platform_key
                + '"}',
            )
        ],
    )

    with pytest.raises(Exception) as caught:
        await service._call_qwen_image_api("prompt", 1024, 1024, None)

    assert attempted_headers == [f"Bearer {platform_key}"]
    assert platform_key not in str(caught.value)
    assert platform_key not in repr(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_vision_pool_explicitly_disables_text_byok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("BG_VISION_API_KEYS", "vision-platform-key")
    service = BackgroundImageValidatorService()

    sync_client = service._get_sync_vision_client("", "https://vision.invalid/v1")
    async_client = service._get_async_vision_client("", "https://vision.invalid/v1")

    assert sync_client.allow_byok is False
    assert async_client.allow_byok is False
    assert sync_client.pool.size == 1
    assert async_client.pool.size == 1
    with request_api_key_context("text-byok-must-not-be-used-for-vision"):
        assert [lease.api_key for lease in sync_client.pool.get_candidates(None)] == [
            "vision-platform-key"
        ]


@pytest.mark.asyncio
async def test_openai_compatible_images_generate_uses_image_pool_not_text_byok():
    used_keys: list[str] = []

    class Images:
        async def generate(self, **_kwargs: Any):
            return {"ok": True}

    class Client:
        def __init__(self, api_key: str) -> None:
            used_keys.append(api_key)
            self.images = Images()

    facade = PooledAsyncOpenAI(
        api_keys=["image-platform-key"],
        allow_byok=False,
        _client_factory=lambda *, api_key, **_kwargs: Client(api_key),
    )

    with request_api_key_context("text-byok-must-not-reach-images-generate"):
        result = await facade.images.generate(model="image-model", prompt="scene")

    assert result == {"ok": True}
    assert used_keys == ["image-platform-key"]
