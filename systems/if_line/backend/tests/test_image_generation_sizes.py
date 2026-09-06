import base64
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.services import image_generation_service as image_module
from app.services.image_generation_service import (
    ImageGenerationService,
    SIZE_PRESETS,
)


def _png_bytes(size: tuple[int, int]) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (35, 75, 115)).save(output, format="PNG")
    return output.getvalue()


def _service(tmp_path: Path) -> ImageGenerationService:
    service = ImageGenerationService()
    service.output_dir = tmp_path / "assets"
    service._init_output_dirs()
    return service


def _enable_generation(monkeypatch) -> None:
    monkeypatch.setattr(image_module, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(image_module, "AI_IMAGE_API_KEY", "fake-image-key")
    monkeypatch.setattr(image_module, "api_key_available", lambda *_args, **_kwargs: True)


@pytest.mark.asyncio
async def test_generate_image_reports_decoded_provider_dimensions(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    _enable_generation(monkeypatch)

    async def provider(*_args, **_kwargs):
        return _png_bytes((800, 600))

    monkeypatch.setattr(service, "_call_cogview_api", provider)

    result = await service.generate_image(
        prompt="cinematic frame",
        asset_type="keyframe",
        width=1536,
        height=864,
        skip_cache=True,
    )

    assert result["success"] is True
    assert result["size"] == [800, 600]
    with Image.open(result["image_path"]) as generated:
        assert generated.size == (800, 600)


@pytest.mark.asyncio
async def test_legacy_background_normalizes_provider_ratio_and_metadata(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    _enable_generation(monkeypatch)
    monkeypatch.setattr(image_module, "AI_IMAGE_WATERMARK", False)

    async def provider(*_args, **_kwargs):
        return _png_bytes((1200, 800))

    monkeypatch.setattr(service, "_call_cogview_api", provider)

    result = await service.generate_background(
        scene_name="empty station",
        scene_description="an empty station platform at night",
        final_prompt="wide empty station platform, cinematic environment",
    )

    assert result["success"] is True
    assert result["size"] == list(SIZE_PRESETS["background"])
    with Image.open(result["image_path"]) as generated:
        assert generated.size == SIZE_PRESETS["background"]


@pytest.mark.asyncio
async def test_openai_generation_maps_output_target_to_supported_request_size(
    tmp_path: Path,
):
    service = _service(tmp_path)
    calls = []

    class FakeImages:
        async def generate(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(_png_bytes((900, 600))).decode("ascii")
                    )
                ]
            )

    service._openai_image_client = SimpleNamespace(images=FakeImages())

    image_data = await service._call_openai_compatible_image_api(
        "wide establishing shot",
        *SIZE_PRESETS["background"],
    )

    assert image_data
    assert calls[0]["size"] == "1536x1024"


def test_letterbox_preserves_foreground_aspect_ratio():
    calls = {"resize": [], "paste": []}

    class FakeImage:
        mode = "RGB"

        def __init__(self, size):
            self.size = size

        def resize(self, size, _resampling):
            calls["resize"].append((self.size, size))
            return FakeImage(size)

        def filter(self, _filter):
            return self

        def convert(self, _mode):
            return self

        def paste(self, foreground, position):
            calls["paste"].append((foreground.size, position))

    result = ImageGenerationService()._letterbox(
        FakeImage((1200, 800)),
        (1600, 900),
    )

    assert result.size == (1600, 900)
    assert ((1200, 800), (1350, 900)) in calls["resize"]
    assert calls["paste"] == [((1350, 900), (125, 0))]


def test_landscape_postprocess_keeps_bottom_pixels_when_watermark_disabled(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(image_module, "AI_IMAGE_WATERMARK", False)
    service.profiles.setdefault("post_processing", {})["watermark_crop_px"] = 60
    image_path = service.output_dir / "backgrounds" / "no-watermark.png"
    image = Image.new("RGB", SIZE_PRESETS["background"], (20, 40, 80))
    image.paste((240, 20, 20), (0, image.height - 60, image.width, image.height))
    image.save(image_path)

    service._postprocess_background_preserve_aspect(
        image_path,
        SIZE_PRESETS["background"],
    )

    with Image.open(image_path) as processed:
        assert processed.size == SIZE_PRESETS["background"]
        assert processed.getpixel((processed.width // 2, processed.height - 1)) == (240, 20, 20)


def test_landscape_postprocess_crops_bottom_when_watermark_enabled(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    monkeypatch.setattr(image_module, "AI_IMAGE_WATERMARK", True)
    service.profiles.setdefault("post_processing", {})["watermark_crop_px"] = 60
    image_path = service.output_dir / "backgrounds" / "with-watermark.png"
    Image.new("RGB", SIZE_PRESETS["background"], (20, 40, 80)).save(image_path)
    letterbox_inputs = []
    original_letterbox = service._letterbox

    def tracked_letterbox(image, target):
        letterbox_inputs.append(image.size)
        return original_letterbox(image, target)

    monkeypatch.setattr(service, "_letterbox", tracked_letterbox)

    service._postprocess_background_preserve_aspect(
        image_path,
        SIZE_PRESETS["background"],
    )

    assert letterbox_inputs == [
        (SIZE_PRESETS["background"][0], SIZE_PRESETS["background"][1] - 60)
    ]
    with Image.open(image_path) as processed:
        assert processed.size == SIZE_PRESETS["background"]
