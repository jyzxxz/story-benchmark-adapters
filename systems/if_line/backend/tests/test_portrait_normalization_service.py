"""Portrait normalization service — synthetic PIL image tests.

No real generation APIs; no billed calls. Every test builds a small RGBA PNG
in a tmp_path, runs the normalizer, and asserts on the geometry result + the
bytes written to disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from app.services.portrait_normalization_service import (
    CANONICAL_PORTRAIT_WIDTH,
    CANONICAL_PORTRAIT_HEIGHT,
    MAX_FOREGROUND_WIDTH_RATIO,
    PORTRAIT_NORMALIZATION_VERSION,
    PortraitGeometryValidator,
    PortraitNormalizer,
)


def _save_foreground(
    path: Path,
    *,
    canvas_w: int,
    canvas_h: int,
    bbox: tuple[int, int, int, int],
    color: tuple[int, int, int, int] = (180, 60, 60, 255),
) -> None:
    """Write a transparent PNG with a solid-color foreground rectangle."""
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle(bbox, fill=color)
    img.save(path)


# ============================================================
# Geometry
# ============================================================

def test_different_input_sizes_all_become_canonical_canvas(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    for size in [(1024, 1536), (800, 1200), (768, 1280), (1536, 2048)]:
        src = tmp_path / f"src_{size[0]}x{size[1]}.png"
        _save_foreground(
            src, canvas_w=size[0], canvas_h=size[1],
            bbox=(size[0] // 4, size[1] // 8, size[0] * 3 // 4, size[1] * 7 // 8),
        )
        result = norm.normalize(src, force=True)
        assert result.canvas_width == CANONICAL_PORTRAIT_WIDTH
        assert result.canvas_height == CANONICAL_PORTRAIT_HEIGHT


def test_small_foreground_scaled_to_target_height(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "small_fg.png"
    # 1024x1536 canvas, foreground only 55% height
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(256, 400, 768, 1244),  # 512w x 844h ≈ 55% height
    )
    result = norm.normalize(src)
    assert result.limiting_axis in {"height", "width"}
    # After normalization, the foreground should fill ~80%+ of canvas height
    # (safety pad slightly reduces from 90% target).
    assert result.foreground_height_ratio >= 0.80


def test_large_foreground_not_scaled_beyond_canvas(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "big_fg.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(20, 20, 1004, 1516),  # 98% x 98% — fills almost the whole canvas
    )
    result = norm.normalize(src)
    assert result.foreground_height_ratio <= 0.92
    assert result.foreground_width_ratio <= MAX_FOREGROUND_WIDTH_RATIO + 0.01


def test_wide_battle_pose_is_width_limited(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "wide.png"
    # 1024x1536, foreground very wide and short (simulates a weapon sprite)
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(20, 600, 1004, 1100),  # 98% wide x 32% tall
    )
    result = norm.normalize(src)
    assert result.limiting_axis == "width"
    assert result.foreground_width_ratio <= MAX_FOREGROUND_WIDTH_RATIO + 0.02


def test_feet_align_near_bottom(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "feet.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(300, 200, 724, 1400),
    )
    result = norm.normalize(src)
    bottom_y = result.normalized_bbox[3]
    bottom_ratio = bottom_y / result.canvas_height
    assert 0.96 <= bottom_ratio <= 0.985, f"bottom out of range: {bottom_ratio}"


def test_foreground_horizontally_centered(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "off_center.png"
    # foreground in upper-left corner — normalization should re-center
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(50, 200, 400, 1400),
    )
    result = norm.normalize(src)
    center_x = (result.normalized_bbox[0] + result.normalized_bbox[2]) / 2
    center_ratio = center_x / result.canvas_width
    assert abs(center_ratio - 0.5) <= 0.04, f"not centered: {center_ratio}"


def test_transparent_background_preserved(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "transparent.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(400, 200, 624, 1400),
    )
    result = norm.normalize(src)
    with Image.open(result.output_path) as out:
        assert out.mode == "RGBA"
        # Top-left pixel of the canvas must be fully transparent
        topleft = out.getpixel((0, 0))
        assert topleft[3] == 0


def test_head_and_feet_not_clipped(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "tall.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(400, 100, 624, 1530),  # almost full height — head near top
    )
    result = norm.normalize(src)
    # bbox top must be > 0 (no clipping) — there must be headroom
    assert result.normalized_bbox[1] > 0


def test_empty_alpha_image_fails(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "empty.png"
    Image.new("RGBA", (1024, 1536), (0, 0, 0, 0)).save(src)
    with pytest.raises(ValueError, match="empty_foreground"):
        norm.normalize(src)


def test_non_rgba_input_is_converted(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "rgb.png"
    img = Image.new("RGB", (1024, 1536), (180, 60, 60))
    img.save(src)
    result = norm.normalize(src)
    # RGB → RGBA on read; foreground is the entire canvas, so it gets scaled
    # down to fit. The result should still be a valid canonical canvas.
    assert result.canvas_width == CANONICAL_PORTRAIT_WIDTH


def test_atomic_replace_uses_temp_file(tmp_path: Path, monkeypatch):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "src.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(300, 200, 724, 1400),
    )
    norm.normalize(src)
    # The cache dir should contain exactly the cache file + sidecar (no .tmp)
    cache_files = list((tmp_path / "cache").rglob("*.tmp"))
    assert cache_files == [], f"stray temp files: {cache_files}"


def test_idempotent_multiple_calls_same_result(tmp_path: Path):
    norm = PortraitNormalizer(tmp_path / "cache")
    src = tmp_path / "idem.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(300, 200, 724, 1400),
    )
    r1 = norm.normalize(src)
    r2 = norm.normalize(src)
    r3 = norm.normalize(src)
    assert r1.output_path == r2.output_path == r3.output_path
    # Cached sidecar should match the in-memory result
    sidecar_path = r1.output_path.with_suffix(".json")
    payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert payload["normalization_version"] == PORTRAIT_NORMALIZATION_VERSION
    assert payload["foreground_height_ratio"] == r2.foreground_height_ratio


def test_version_bump_invalidates_cache(tmp_path: Path):
    src = tmp_path / "v1_src.png"
    _save_foreground(
        src, canvas_w=1024, canvas_h=1536,
        bbox=(300, 200, 724, 1400),
    )
    norm_v1 = PortraitNormalizer(
        tmp_path / "cache", normalization_version="canonical-canvas-v1",
    )
    norm_v2 = PortraitNormalizer(
        tmp_path / "cache", normalization_version="canonical-canvas-v2",
    )
    r1 = norm_v1.normalize(src)
    r2 = norm_v2.normalize(src)
    # Different versions → different output paths
    assert r1.output_path != r2.output_path


def test_geometry_validator_rejects_too_short(tmp_path: Path):
    """Synthesize an absurdly wide+short result; validator must reject."""
    from app.services.portrait_normalization_service import PortraitNormalizationResult
    fake = PortraitNormalizationResult(
        canvas_width=CANONICAL_PORTRAIT_WIDTH,
        canvas_height=CANONICAL_PORTRAIT_HEIGHT,
        original_bbox=(0, 0, 1024, 100),
        normalized_bbox=(50, 1100, 718, 1240),  # tiny height
        foreground_height_ratio=0.109,
        foreground_width_ratio=0.869,
        foreground_coverage=0.09,
        scale_factor=0.1,
        offset_x=50,
        offset_y=1100,
        limiting_axis="height",
    )
    v = PortraitGeometryValidator.validate(fake)
    assert not v.passed
