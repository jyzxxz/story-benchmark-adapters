"""VisualStyleCoherenceValidator — synthetic image tests.

No external API calls. Each test builds PIL images with known statistics,
runs the validator, and asserts the verdict matches expectations.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.services.visual_style_coherence_service import (
    VisualStyleCoherenceValidator,
    compute_metrics,
)


def _portrait(color: tuple[int, int, int], *, canvas: tuple[int, int] = (768, 1280),
              bbox: tuple[int, int, int, int] = (180, 60, 588, 1240)) -> Image.Image:
    """A solid-color portrait with transparent margins."""
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle(bbox, fill=(*color, 255))
    return img


def _background(color: tuple[int, int, int], *, canvas: tuple[int, int] = (1280, 720)) -> Image.Image:
    img = Image.new("RGB", canvas, color)
    # add bold texture so edge density is high enough not to look like a photograph
    draw = ImageDraw.Draw(img)
    for i in range(0, canvas[0], 24):
        draw.rectangle([(i, 0), (i + 8, canvas[1])], fill=_darken(color, 60))
    return img


def _darken(color: tuple[int, int, int], k: int) -> tuple[int, int, int]:
    return tuple(max(0, c - k) for c in color)  # type: ignore


# ============================================================
# Cross-image coherence
# ============================================================

def test_matching_low_saturation_pair_passes():
    portrait = _portrait((170, 80, 70))
    background = _background((150, 90, 80))
    v = VisualStyleCoherenceValidator(style_contract_version="three-kingdoms-ink-v2")
    verdict = v.validate(portrait=portrait, background=background)
    assert verdict.passed is True
    assert verdict.style_contract_version == "three-kingdoms-ink-v2"


def test_neon_saturated_background_fails():
    portrait = _portrait((170, 80, 70))
    # Pure saturated magenta background → saturation near 1.0
    background = _background((255, 0, 255))
    v = VisualStyleCoherenceValidator()
    verdict = v.validate(portrait=portrait, background=background)
    assert verdict.passed is False
    assert "background_neon_saturated" in verdict.failures


def test_opposite_color_temperature_fails_or_warns():
    portrait = _portrait((200, 60, 60))   # warm (high R, low B)
    background = _background((60, 60, 200))  # cold (low R, high B)
    v = VisualStyleCoherenceValidator()
    verdict = v.validate(portrait=portrait, background=background)
    # temperature_gap >= 0.45 hard-fails; smaller gap warn
    assert verdict.coherence["temperature_gap"] >= 0.25


# ============================================================
# Portrait-only metric (alpha mask must exclude transparent pixels)
# ============================================================

def test_alpha_mask_excludes_transparent_margin_in_portrait_metrics():
    portrait = _portrait((180, 60, 60))
    metrics = compute_metrics(portrait, is_portrait=True)
    # Mean RGB should be very close to (180, 60, 60) since transparent
    # pixels are masked out.
    assert 170 <= metrics.brightness_mean * 255 <= 200  # G channel dominates value mid-range
    # Warmth should be strongly positive (R >> B)
    assert metrics.warmth > 0.2


def test_background_metrics_include_all_pixels():
    background = _background((100, 130, 160))
    metrics = compute_metrics(background, is_portrait=False)
    # No mask — brightness_mean should be near HSV value (max RGB channel) / 255
    expected_v = max(100, 130, 160) / 255
    assert abs(metrics.brightness_mean - expected_v) < 0.10


# ============================================================
# Report shape
# ============================================================

def test_verdict_dict_roundtrip_is_serializable():
    portrait = _portrait((170, 80, 70))
    background = _background((150, 90, 80))
    v = VisualStyleCoherenceValidator()
    import json
    payload = v.validate(portrait=portrait, background=background).to_dict()
    text = json.dumps(payload)  # must not raise
    assert "coherence" in payload
    assert "palette_distance" in payload["coherence"]
