from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.application.visual_asset_service import _sanitize_image
from app.scripts.import_visual_asset_catalog import _file_metadata
from app.services import image_generation_service as image_module
from app.services.image_generation_service import (
    PORTRAIT_GENERATION_CONTRACT_VERSION,
    ImageGenerationService,
    PortraitTransparencyError,
)


def _png_bytes(mode: str, color: tuple[int, ...], size: tuple[int, int] = (96, 96)) -> bytes:
    image = Image.new(mode, size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_alpha_metrics_reject_channel_only_and_accept_effective_transparency(tmp_path: Path):
    service = ImageGenerationService()
    opaque_rgba = tmp_path / "opaque.png"
    opaque_rgba.write_bytes(_png_bytes("RGBA", (20, 40, 80, 255)))
    transparent = tmp_path / "transparent.png"
    image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(25, 75):
        for y in range(10, 95):
            image.putpixel((x, y), (120, 80, 60, 255))
    image.save(transparent)

    assert service._portrait_alpha_metrics(opaque_rgba)["passed"] is False
    metrics = service._portrait_alpha_metrics(transparent)
    assert metrics["passed"] is True
    assert metrics["transparent_fraction"] > 0.5
    assert metrics["opaque_fraction"] > 0.1


def test_remove_background_is_atomic_and_preserves_source(tmp_path: Path, monkeypatch):
    service = ImageGenerationService()
    path = tmp_path / "portrait.png"
    original = _png_bytes("RGB", (240, 240, 240))
    path.write_bytes(original)

    def fake_remove(_raw: bytes) -> bytes:
        image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
        for x in range(24, 72):
            for y in range(8, 92):
                image.putpixel((x, y), (50, 70, 90, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    monkeypatch.setitem(sys.modules, "rembg", SimpleNamespace(remove=fake_remove))
    result = service._remove_background_sync(path, keep_source=True)

    assert result == path
    assert service.portrait_has_effective_alpha(path) is True
    assert path.with_suffix(".png.source.png").read_bytes() == original


def test_remove_background_rejects_opaque_output_without_overwriting_source(
    tmp_path: Path, monkeypatch
):
    service = ImageGenerationService()
    path = tmp_path / "portrait.png"
    original = _png_bytes("RGB", (240, 240, 240))
    path.write_bytes(original)
    monkeypatch.setitem(
        sys.modules,
        "rembg",
        SimpleNamespace(remove=lambda _raw: _png_bytes("RGBA", (20, 40, 80, 255))),
    )

    with pytest.raises(PortraitTransparencyError):
        service._remove_background_sync(path, keep_source=True)

    assert path.read_bytes() == original
    assert not path.with_suffix(".png.source.png").exists()


def test_portrait_prompt_contract_removes_conflicts_and_stiff_pose():
    service = ImageGenerationService()
    prompt = service.enforce_portrait_prompt(
        "transparent full-body sprite, wearing default, standing pose, upright posture, "
        "half body shot, character centered",
        pose="standing",
        full_body=False,
    )

    lowered = prompt.lower()
    assert "portrait presentation contract" in lowered
    assert "transparent background contract" in lowered
    assert "weight shifted naturally" in lowered
    assert "wearing default" not in lowered
    assert "transparent full-body sprite" not in lowered
    assert "rigid symmetrical mannequin posture" in lowered
    assert service.enforce_portrait_prompt(prompt) == prompt


def test_portrait_prompt_contract_removes_opposite_crop_and_incomplete_marker():
    service = ImageGenerationService()
    prompt = service.enforce_portrait_prompt(
        "PORTRAIT PRESENTATION CONTRACT, full body shot, entire character visible "
        "from head to feet, vertical composition, quiet expression",
        full_body=False,
    )

    lowered = prompt.lower()
    assert lowered.count("portrait presentation contract") == 1
    assert "waist-up sprite" in lowered
    assert "full body shot" not in lowered
    assert "head to feet" not in lowered
    assert "vertical composition" not in lowered
    assert "transparent background contract" in lowered


def test_upload_alpha_metadata_requires_real_transparent_pixels():
    _, _, _, _, _, opaque_has_alpha = _sanitize_image(
        _png_bytes("RGBA", (20, 40, 80, 255))
    )
    _, _, _, _, _, transparent_has_alpha = _sanitize_image(
        _png_bytes("RGBA", (20, 40, 80, 128))
    )

    assert opaque_has_alpha is False
    assert transparent_has_alpha is True
    assert PORTRAIT_GENERATION_CONTRACT_VERSION == "portrait-natural-alpha-v3-age"


def test_catalog_portrait_alpha_rejects_single_transparent_noise_pixel(tmp_path: Path):
    noise = tmp_path / "noise.png"
    noise_image = Image.new("RGBA", (100, 100), (20, 40, 80, 255))
    noise_image.putpixel((0, 0), (0, 0, 0, 0))
    noise_image.save(noise)

    sprite = tmp_path / "sprite.png"
    sprite_image = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    for x in range(25, 75):
        for y in range(10, 95):
            sprite_image.putpixel((x, y), (20, 40, 80, 255))
    sprite_image.save(sprite)

    assert _file_metadata(noise)[-1] is False
    assert _file_metadata(sprite)[-1] is True


@pytest.mark.asyncio
async def test_generate_image_portrait_preserves_authoritative_prompt_alpha_and_cache(
    tmp_path: Path, monkeypatch
):
    service = ImageGenerationService()
    service.output_dir = tmp_path / "assets"
    service._init_output_dirs()
    provider_calls: list[str] = []
    crop_calls: list[Path] = []

    async def fake_provider(prompt, _width, _height, _seed, **_kwargs):
        provider_calls.append(prompt)
        return _png_bytes("RGB", (235, 235, 235), size=(1024, 1024))

    def fake_remove(_raw: bytes) -> bytes:
        image = Image.new("RGBA", (1024, 964), (0, 0, 0, 0))
        for x in range(300, 724):
            for y in range(80, 930):
                image.putpixel((x, y), (40, 60, 80, 255))
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    original_crop = service.crop_watermark

    def tracked_crop(path: Path):
        crop_calls.append(path)
        return original_crop(path)

    monkeypatch.setattr(image_module, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(image_module, "AI_IMAGE_API_KEY", "fake-key")
    monkeypatch.setattr(image_module, "AI_IMAGE_WATERMARK", True)
    monkeypatch.setattr(image_module, "api_key_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(service, "_call_cogview_api", fake_provider)
    monkeypatch.setattr(service, "crop_watermark", tracked_crop)
    monkeypatch.setitem(sys.modules, "rembg", SimpleNamespace(remove=fake_remove))

    final_prompt = (
        "Exactly one isolated character, a young woman in a gray hoodie, natural relaxed "
        "full-body stance with complete head, hands, and feet inside frame, clean visual-novel "
        "line art, soft neutral lighting, fully transparent background, no scenery, floor, "
        "cast shadow, furniture, text, or watermark."
    )
    first = await service.generate_image(
        prompt=final_prompt,
        asset_type="portrait",
        width=1024,
        height=1024,
        seed=7,
        authoritative_prompt=True,
    )
    second = await service.generate_image(
        prompt=final_prompt,
        asset_type="portrait",
        width=1024,
        height=1024,
        seed=7,
        authoritative_prompt=True,
    )

    assert first["success"] is True and first["portrait_alpha"]["passed"] is True
    assert second["success"] is True and second["cached"] is True
    assert len(provider_calls) == 1
    assert provider_calls[0] == final_prompt
    assert len(crop_calls) == 1
    output_path = Path(first["image_path"])
    assert service.portrait_has_effective_alpha(output_path) is True
    assert output_path.with_suffix(".png.source.png").is_file()
