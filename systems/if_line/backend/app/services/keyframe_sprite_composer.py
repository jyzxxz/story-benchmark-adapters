"""Sprite composite fallback (Stage_Keyframe_Identity_AR_Blueprint D06).

When reference-image generation + retry has exhausted
``KEYFRAME_IDENTITY_MAX_RETRIES``, the keyframe cannot be shipped with a
possibly-wrong face. The blueprint mandates falling back to a
``sprite_composite`` render mode that pastes the already-validated
transparent portrait(s) onto the already-validated background.

Trade-offs the blueprint accepts:
* Characters are guaranteed to be the original portraits (no face drift).
* Dynamic action is sacrificed — pose is whatever the master portrait
  has.
* Position / scale / occlusion must be plausible.

Implementation uses PIL only — no external model calls, fully testable
without paid APIs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.services.image_generation_service import BACKEND_ROOT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SpritePlacement:
    """Where and how to paste one portrait onto the background."""

    character_name: str
    portrait_path: Path
    expected_position: str  # left / center / right
    scale: float = 1.0  # multiplier on portrait width
    vertical_anchor: float = 0.92  # 0 = top, 1 = bottom of background
    anchor_offset_px: int = 0  # horizontal offset from default slot


@dataclass
class SpriteCompositeResult:
    success: bool
    image_path: Optional[Path] = None
    image_url: Optional[str] = None
    error: Optional[str] = None
    placements: list[SpritePlacement] = None  # type: ignore[assignment]


def _resolve_local_image(image_url_or_path: str) -> Optional[Path]:
    """Best-effort: turn /static/.../foo.png or absolute path into a Path."""
    if not image_url_or_path:
        return None
    s = str(image_url_or_path).strip()
    if not s:
        return None
    lower = s.lower()
    if lower.startswith("/static/"):
        candidate = BACKEND_ROOT / s.lstrip("/")
        return candidate if candidate.exists() else None
    if lower.startswith(("http://", "https://")):
        # Localhost URLs — try to map back to disk
        if "localhost" in lower or "127.0.0.1" in lower:
            from urllib.parse import urlparse
            path = urlparse(s).path
            if path.startswith("/static/"):
                candidate = BACKEND_ROOT / path.lstrip("/")
                return candidate if candidate.exists() else None
        return None  # remote URL — cannot composite without download
    candidate = Path(s)
    if candidate.exists():
        return candidate
    candidate = BACKEND_ROOT / s.lstrip("./")
    return candidate if candidate.exists() else None


def _slot_x_center(position: str, bg_width: int, offset_px: int = 0) -> int:
    """Return the X center for a given expected_position slot."""
    p = (position or "center").strip().lower()
    if p == "left":
        base = int(bg_width * 0.28)
    elif p == "right":
        base = int(bg_width * 0.72)
    elif p == "background":
        base = int(bg_width * 0.5)
    else:  # center
        base = int(bg_width * 0.5)
    return max(0, min(bg_width, base + offset_px))


def composite_keyframe(
    *,
    background_image_url: str,
    placements: list[SpritePlacement],
    output_path: Path,
    image_base_url: str,
) -> SpriteCompositeResult:
    """D06 — paste validated transparent portraits onto a validated background.

    The output is saved to ``output_path`` and the corresponding
    ``image_base_url``-rooted URL is returned. Caller is responsible for
    creating the ``Asset`` row with ``render_mode=sprite_composite``.
    """
    try:
        from PIL import Image
    except ImportError:
        return SpriteCompositeResult(success=False, error="PIL_not_available")

    bg_path = _resolve_local_image(background_image_url)
    if bg_path is None or not bg_path.exists():
        return SpriteCompositeResult(
            success=False, error=f"background_not_found:{background_image_url}"
        )

    try:
        canvas = Image.open(bg_path).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        return SpriteCompositeResult(success=False, error=f"background_open_failed:{type(e).__name__}")

    bg_w, bg_h = canvas.size

    resolved_placements: list[SpritePlacement] = []
    for p in placements:
        local = _resolve_local_image(str(p.portrait_path))
        if local is None or not local.exists():
            logger.warning("[sprite-composite] portrait missing, skipping: %s", p.portrait_path)
            continue
        try:
            fg = Image.open(local).convert("RGBA")
        except Exception as e:  # noqa: BLE001
            logger.warning("[sprite-composite] portrait open failed: %s err=%s", local, e)
            continue

        # Scale portrait so its width = 30% of background width by default
        target_w = max(64, int(bg_w * 0.30 * max(0.2, p.scale)))
        scale_factor = target_w / fg.width
        target_h = max(64, int(fg.height * scale_factor))
        if target_w > bg_w or target_h > bg_h:
            # Clamp into the frame
            target_w = min(target_w, bg_w)
            target_h = min(target_h, bg_h)
        try:
            fg_resized = fg.resize((target_w, target_h), Image.LANCZOS)
        except Exception as e:  # noqa: BLE001
            logger.warning("[sprite-composite] resize failed: %s", e)
            continue

        slot_x = _slot_x_center(p.expected_position, bg_w, p.anchor_offset_px)
        slot_y = max(0, int(bg_h * p.vertical_anchor) - target_h)
        paste_x = max(0, min(bg_w - target_w, slot_x - target_w // 2))

        canvas.alpha_composite(fg_resized, (paste_x, slot_y))
        resolved_placements.append(p)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        canvas.convert("RGB").save(output_path, format="PNG")
    except Exception as e:  # noqa: BLE001
        return SpriteCompositeResult(success=False, error=f"save_failed:{type(e).__name__}")

    try:
        rel = (
            output_path.relative_to(BACKEND_ROOT)
            if output_path.is_absolute()
            else output_path
        )
        image_url = f"{image_base_url}/{rel.as_posix().lstrip('/')}"
    except ValueError:
        # output_path outside BACKEND_ROOT — fall back to absolute path as URL
        image_url = f"{image_base_url}/{output_path.as_posix().lstrip('/')}"

    return SpriteCompositeResult(
        success=True,
        image_path=output_path,
        image_url=image_url,
        placements=resolved_placements,
    )


__all__ = [
    "SpritePlacement",
    "SpriteCompositeResult",
    "composite_keyframe",
]
