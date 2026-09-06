from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from app.services import image_generation_service as image_module
from app.services.image_generation_service import (
    ImageGenerationService,
    ImagePostprocessError,
)


def _png_bytes(size=(640, 640)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (30, 70, 110)).save(output, format="PNG")
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
async def test_generate_image_rejects_invalid_provider_bytes_before_publish(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    _enable_generation(monkeypatch)

    async def invalid_provider(*_args, **_kwargs):
        return b"<html>upstream gateway error</html>"

    monkeypatch.setattr(service, "_call_cogview_api", invalid_provider)

    result = await service.generate_image(
        prompt="empty environment",
        asset_type="background",
        width=1024,
        height=1024,
        skip_cache=True,
    )

    assert result["success"] is False
    assert "undecodable image data" in result["error"]
    assert list((service.output_dir / "backgrounds").iterdir()) == []


@pytest.mark.asyncio
async def test_generate_background_reports_postprocess_failure_and_removes_artifact(
    tmp_path: Path,
    monkeypatch,
):
    service = _service(tmp_path)
    _enable_generation(monkeypatch)
    monkeypatch.setattr(image_module, "AI_IMAGE_WATERMARK", True)

    async def valid_provider(*_args, **_kwargs):
        return _png_bytes()

    def failed_postprocess(_path: Path, _target_size):
        raise ImagePostprocessError("forced postprocess failure")

    monkeypatch.setattr(service, "_call_cogview_api", valid_provider)
    monkeypatch.setattr(
        service,
        "_postprocess_background_preserve_aspect",
        failed_postprocess,
    )

    result = await service.generate_background(
        scene_name="empty courtyard",
        scene_description="an empty courtyard at noon",
        final_prompt="wide empty courtyard, no people",
    )

    assert result["success"] is False
    assert "背景图后处理失败" in result["error"]
    assert "forced postprocess failure" in result["error"]
    assert list((service.output_dir / "backgrounds").iterdir()) == []


def test_crop_watermark_raises_instead_of_swallowing_decode_error(
    tmp_path: Path,
):
    service = _service(tmp_path)
    image_path = service.output_dir / "backgrounds" / "broken.png"
    invalid_bytes = b"not an image"
    image_path.write_bytes(invalid_bytes)

    with pytest.raises(ImagePostprocessError):
        service.crop_watermark(image_path)

    assert image_path.read_bytes() == invalid_bytes
    assert not list(image_path.parent.glob(".*.crop.tmp"))
