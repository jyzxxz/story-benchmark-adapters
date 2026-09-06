import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import image_generation_service as mod
from app.services.image_generation_service import ImageGenerationService


def test_qwen_endpoint_accepts_workspace_root(monkeypatch):
    monkeypatch.setattr(
        mod,
        "AI_IMAGE_BASE_URL",
        "https://workspace-id.cn-beijing.maas.aliyuncs.com",
    )

    svc = ImageGenerationService()
    endpoint = svc._build_qwen_image_endpoint()

    assert endpoint == (
        "https://workspace-id.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )


def test_qwen_parameters_use_star_size_and_seed(monkeypatch):
    monkeypatch.setattr(mod, "AI_IMAGE_NEGATIVE_PROMPT", "low quality")
    monkeypatch.setattr(mod, "AI_IMAGE_PROMPT_EXTEND", True)
    monkeypatch.setattr(mod, "AI_IMAGE_WATERMARK", False)

    svc = ImageGenerationService()
    parameters = svc._build_qwen_image_parameters(1920, 1080, 1234)

    assert parameters["size"] == "1920*1080"
    assert parameters["seed"] == 1234
    assert parameters["n"] == 1
    assert parameters["prompt_extend"] is True
    assert parameters["watermark"] is False
    assert parameters["negative_prompt"] == "low quality"


def test_qwen_authoritative_prompt_disables_provider_prompt_mutation(monkeypatch):
    monkeypatch.setattr(mod, "AI_IMAGE_NEGATIVE_PROMPT", "low quality")
    monkeypatch.setattr(mod, "AI_IMAGE_PROMPT_EXTEND", True)

    svc = ImageGenerationService()
    parameters = svc._build_qwen_image_parameters(
        768,
        1280,
        1234,
        exact_prompt=True,
    )

    assert parameters["prompt_extend"] is False
    assert "negative_prompt" not in parameters


def test_extract_qwen_image_url():
    svc = ImageGenerationService()
    data = {
        "output": {
            "choices": [
                {
                    "message": {
                        "content": [
                            {
                                "image": "https://example.test/image.png"
                            }
                        ]
                    }
                }
            ]
        }
    }

    assert svc._extract_qwen_image_url(data) == "https://example.test/image.png"


@pytest.mark.asyncio
async def test_call_cogview_api_dispatches_qwen(monkeypatch):
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key")
    monkeypatch.setattr(mod, "AI_IMAGE_MODEL", "qwen-image-2.0")

    svc = ImageGenerationService()
    calls = {}

    async def fake_qwen(prompt, width, height, seed):
        calls["args"] = (prompt, width, height, seed)
        return b"png"

    svc._call_qwen_image_api = fake_qwen

    image = await svc._call_cogview_api("test image", 1024, 1024, 7)

    assert image == b"png"
    assert calls["args"] == ("test image", 1024, 1024, 7)
