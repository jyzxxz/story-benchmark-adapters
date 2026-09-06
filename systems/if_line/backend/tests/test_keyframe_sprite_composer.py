"""
F04 — Stage_Keyframe_Identity_AR_Blueprint D06: keyframe_sprite_composer.

覆盖：
- background_not_found → 失败
- 缺少 PIL → graceful failure（不强制 mock，跳过）
- 两张 portrait 按 expected_position 摆位
- 缺失的 portrait 被跳过，不阻塞整体
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.keyframe_sprite_composer import (
    SpritePlacement,
    composite_keyframe,
    _slot_x_center,
)


def _make_png(path: Path, w: int = 256, h: int = 256, rgba: tuple = (255, 0, 0, 255)):
    from PIL import Image
    img = Image.new("RGBA", (w, h), rgba)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def _make_transparent_png(path: Path, w: int = 128, h: int = 256):
    from PIL import Image
    img = Image.new("RGBA", (w, h), (0, 255, 0, 200))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def test_background_not_found_returns_failure(tmp_path):
    out = tmp_path / "out.png"
    result = composite_keyframe(
        background_image_url="/static/__nonexistent__.png",
        placements=[],
        output_path=out,
        image_base_url="http://localhost/static",
    )
    assert result.success is False
    assert "background_not_found" in (result.error or "")


def test_slot_x_center_left():
    assert _slot_x_center("left", 1000) == 280


def test_slot_x_center_right():
    assert _slot_x_center("right", 1000) == 720


def test_slot_x_center_center():
    assert _slot_x_center("center", 1000) == 500


def test_slot_x_center_unknown_defaults_center():
    assert _slot_x_center("", 1000) == 500


def test_composite_two_placements(tmp_path):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("PIL not installed")

    # arrange: one background PNG on disk under BACKEND_ROOT/static-like path
    bg_path = tmp_path / "bg.png"
    _make_png(bg_path, 1024, 576, (0, 0, 128, 255))

    p1_path = tmp_path / "char1.png"
    _make_transparent_png(p1_path, 128, 256)
    p2_path = tmp_path / "char2.png"
    _make_transparent_png(p2_path, 128, 256)

    out = tmp_path / "kf_out.png"

    # composite_keyframe resolves /static/... under BACKEND_ROOT/static;
    # we feed it an absolute path via background_image_url.
    result = composite_keyframe(
        background_image_url=str(bg_path),
        placements=[
            SpritePlacement(
                character_name="甲", portrait_path=p1_path,
                expected_position="left",
            ),
            SpritePlacement(
                character_name="乙", portrait_path=p2_path,
                expected_position="right",
            ),
        ],
        output_path=out,
        image_base_url="http://localhost/static",
    )
    assert result.success is True
    assert out.exists()
    assert len(result.placements) == 2


def test_composite_skips_missing_portrait(tmp_path):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pytest.skip("PIL not installed")

    bg_path = tmp_path / "bg.png"
    _make_png(bg_path, 1024, 576, (0, 0, 128, 255))

    real_png = tmp_path / "real.png"
    _make_transparent_png(real_png, 128, 256)

    out = tmp_path / "kf_skip.png"
    result = composite_keyframe(
        background_image_url=str(bg_path),
        placements=[
            SpritePlacement(
                character_name="甲", portrait_path=real_png,
                expected_position="left",
            ),
            SpritePlacement(
                character_name="乙",
                portrait_path=tmp_path / "missing.png",
                expected_position="right",
            ),
        ],
        output_path=out,
        image_base_url="http://localhost/static",
    )
    assert result.success is True
    assert len(result.placements) == 1
