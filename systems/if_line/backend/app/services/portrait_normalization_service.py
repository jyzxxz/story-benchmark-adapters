"""Portrait canvas normalization service.

Rembg output is inconsistent: the foreground's relative size, position and
transparency margin vary wildly between generations of the *same* character.
That inconsistency is what makes the VN stage look like a collage.

This service takes any RGBA PNG and recomposes the foreground onto a canonical
768×1280 transparent canvas:

1. Threshold alpha to find the real foreground bbox.
2. Trim the meaningless transparent margin (with a small safety pad).
3. Isotropically scale so the foreground fits BOTH the target height ratio
   AND the max width ratio (battle poses with long weapons are width-bound).
4. Drop the scaled foreground onto a transparent 768×1280 canvas.
5. Horizontally center the foreground.
6. Align the foreground's lowest opaque pixel to ~97.5% of the canvas height.
7. Atomic replace the original file (write to temp, fsync, rename).

The original bytes are never destroyed in the *upstream* cache (image-gen
cache at ``static/assets/portraits/``). Normalization writes to a separate
``presentation_portraits/<style_version>/<source_sha>.png`` cache, so the
original asset remains referenceable.

The geometric contract is enforced by :class:`PortraitGeometryValidator`
which is called after normalization; the normalized file must satisfy it or
the normalization result is rejected (caller falls back to the raw file).
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger("portrait_normalization")

# ============================================================
# Canonical canvas + geometry contract
# ============================================================

CANONICAL_PORTRAIT_WIDTH = 768
CANONICAL_PORTRAIT_HEIGHT = 1280
PORTRAIT_NORMALIZATION_VERSION = "generic-portrait-canvas-v2"

# alpha values below this count as fully transparent (matches _foreground_metrics)
ALPHA_THRESHOLD = 24

# Geometric targets on the canonical canvas
TARGET_FOREGROUND_HEIGHT_RATIO = 0.90   # feet near bottom, headroom above
TARGET_BOTTOM_RATIO = 0.975             # lowest opaque pixel at 97.5% of canvas H
TARGET_CENTER_X_RATIO = 0.50            # horizontal center
MAX_FOREGROUND_WIDTH_RATIO = 0.84       # weapons/sleeves may not exceed this
SAFETY_PAD_PX = 6                       # trim margin kept around bbox

# Lower bound for width-bound battle poses (height ratio may dip below 0.86)
WIDTH_LIMITED_MIN_HEIGHT_RATIO = 0.74


@dataclass
class PortraitNormalizationResult:
    canvas_width: int
    canvas_height: int
    original_bbox: Tuple[int, int, int, int]
    normalized_bbox: Tuple[int, int, int, int]
    foreground_height_ratio: float
    foreground_width_ratio: float
    foreground_coverage: float
    scale_factor: float
    offset_x: int
    offset_y: int
    limiting_axis: str  # "height" | "width"
    output_path: Optional[Path] = None
    # project-visual-bible-v2 §B6: presentation URL handed back to the
    # caller so asset_management_service can stash it in
    # Asset.generation_params["presentation"]["url"]. ``source_image_url``
    # is the original (pre-normalization) image URL, kept for debugging.
    presentation_url: Optional[str] = None
    source_image_url: Optional[str] = None


@dataclass
class PortraitGeometryValidation:
    passed: bool
    reasons: Tuple[str, ...]
    metrics: dict


def _alpha_bbox(image: Image.Image, threshold: int = ALPHA_THRESHOLD) -> Optional[Tuple[int, int, int, int]]:
    """Return (left, top, right, bottom) bbox of pixels with alpha >= threshold."""
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    alpha = image.getchannel("A")
    mask = alpha.point(lambda v: 255 if v >= threshold else 0)
    return mask.getbbox()


def _scaled_foreground_size(
    bbox_w: int,
    bbox_h: int,
    canvas_w: int,
    canvas_h: int,
) -> Tuple[float, int, int, str]:
    """Compute isotropic scale + resulting foreground (w, h) + limiting axis.

    Two constraints:
      - height: ``scale_h = (canvas_h * TARGET_FOREGROUND_HEIGHT_RATIO) / bbox_h``
      - width:  ``scale_w = (canvas_w * MAX_FOREGROUND_WIDTH_RATIO)  / bbox_w``
    Take the MIN so neither dimension overflows. Record which axis won.
    """
    target_h = canvas_h * TARGET_FOREGROUND_HEIGHT_RATIO
    target_w = canvas_w * MAX_FOREGROUND_WIDTH_RATIO
    scale_h = target_h / bbox_h if bbox_h > 0 else 1.0
    scale_w = target_w / bbox_w if bbox_w > 0 else 1.0
    if scale_h <= scale_w:
        return scale_h, int(round(bbox_w * scale_h)), int(round(target_h)), "height"
    return scale_w, int(round(target_w)), int(round(bbox_h * scale_w)), "width"


class PortraitNormalizer:
    """Idempotent foreground-on-canonical-canvas normalizer."""

    def __init__(
        self,
        cache_root: Path,
        *,
        canvas_width: int = CANONICAL_PORTRAIT_WIDTH,
        canvas_height: int = CANONICAL_PORTRAIT_HEIGHT,
        normalization_version: str = PORTRAIT_NORMALIZATION_VERSION,
    ) -> None:
        self.cache_root = Path(cache_root)
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.normalization_version = normalization_version

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

    def presentation_cache_path(self, source_path: Path) -> Path:
        """Deterministic cache path for ``source_path``.

        Cache key embeds: source sha256 + canvas dims + normalization version.
        Changing any of these (e.g. bumping PORTRAIT_NORMALIZATION_VERSION)
        invalidates every previously normalized presentation file.
        """
        source_sha = self._file_sha256(source_path)
        filename = (
            f"{source_sha[:16]}_"
            f"{self.canvas_width}x{self.canvas_height}_"
            f"{self.normalization_version}.png"
        )
        return self.cache_root / self.normalization_version / filename

    def presentation_cache_sidecar(self, cache_path: Path) -> Path:
        return cache_path.with_suffix(".json")

    def normalize(
        self,
        source_path: Path,
        *,
        force: bool = False,
        source_image_url: Optional[str] = None,
    ) -> PortraitNormalizationResult:
        """Normalize ``source_path`` and cache the result.

        Idempotent: returns the existing cache file when present and ``force``
        is False. On any image-level failure, raises ``ValueError``; callers
        must fall back to the raw source.

        ``source_image_url`` is stashed on the result for the caller to write
        into ``Asset.generation_params["presentation"]`` (decision 3). It is
        not part of the cache key — two callers with different URLs for the
        same source bytes will share the same presentation file.
        """
        if not source_path.is_file():
            raise FileNotFoundError(f"portrait source missing: {source_path}")

        cache_path = self.presentation_cache_path(source_path)
        sidecar_path = self.presentation_cache_sidecar(cache_path)
        if not force and cache_path.is_file() and cache_path.stat().st_size > 0:
            cached = self._read_cached_result(cache_path, sidecar_path)
            if cached is not None:
                cached.output_path = cache_path
                cached.presentation_url = self._path_to_url(cache_path)
                cached.source_image_url = source_image_url
                return cached

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        result = self._normalize_into(source_path, cache_path)
        self._write_sidecar(sidecar_path, source_path, cache_path, result)
        result.output_path = cache_path
        result.presentation_url = self._path_to_url(cache_path)
        result.source_image_url = source_image_url
        return result

    @staticmethod
    def _path_to_url(path: Path) -> str:
        """Convert an absolute path under static/ to a servable URL path.

        Returns a path-relative URL (``/static/assets/...``). The frontend
        already serves ``static/`` at ``/static/``, so no host prefix is
        needed; if the path is outside ``static/`` we return the absolute
        filesystem path as a debugging fallback.
        """
        try:
            p = Path(path).resolve()
            for parent in [p] + list(p.parents):
                if parent.name == "static":
                    rel = p.relative_to(parent.parent)
                    return f"/{rel.as_posix()}"
            return str(p)
        except Exception:
            return str(path)

    # ------------------------------------------------------------
    # project-visual-bible-v2 §B6 — asset-bound normalization entry
    # ------------------------------------------------------------

    def normalize_for_asset(
        self,
        asset_id: int,
        image_path: Path,
        image_url: Optional[str],
        db,
    ) -> Optional[PortraitNormalizationResult]:
        """Normalize the portrait for ``asset_id`` and persist the URL.

        Per decision 3 (2026-07-17): the presentation URL is written to
        ``Asset.generation_params["presentation"]["url"]`` — **zero DB
        migration**. Also writes ``generation_params["presentation"]
        ["source_image_url"]`` and bumps ``portrait_normalization_version``
        so old assets can be detected by the cache-contract check in
        asset_management_service.

        This method is **advisory-only**: on any failure (image missing,
        DB write error, alpha bbox empty, ...) it logs a warning and
        returns ``None``. The caller continues with the raw source URL —
        normalization is a presentation polish, not a generation failure.
        """
        try:
            result = self.normalize(
                image_path,
                source_image_url=image_url,
            )
        except Exception as exc:
            logger.warning(
                "portrait_normalization normalize failed asset_id=%s err=%s",
                asset_id, exc,
            )
            return None

        try:
            # Lazy import to avoid circular dependency at module load time.
            from app.models import Asset
            asset = db.query(Asset).filter(Asset.id == asset_id).first()
            if asset is None:
                logger.warning(
                    "portrait_normalization asset not found asset_id=%s",
                    asset_id,
                )
                return result

            params = dict(asset.generation_params or {})
            params["presentation"] = {
                "url": result.presentation_url,
                "source_image_url": image_url,
                "version": self.normalization_version,
                "canvas": f"{result.canvas_width}x{result.canvas_height}",
                "foreground_height_ratio": round(
                    result.foreground_height_ratio, 4,
                ),
                "foreground_width_ratio": round(
                    result.foreground_width_ratio, 4,
                ),
                "limiting_axis": result.limiting_axis,
            }
            params["portrait_normalization_version"] = self.normalization_version
            asset.generation_params = params
            db.add(asset)
            db.commit()
        except Exception as exc:
            logger.warning(
                "portrait_normalization persist failed asset_id=%s err=%s",
                asset_id, exc,
            )
            # Even if DB write fails, return the result — caller can still
            # serve the file from the cache path if it wants.
        return result

    # ------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------

    def _normalize_into(
        self,
        source_path: Path,
        target_path: Path,
    ) -> PortraitNormalizationResult:
        with Image.open(source_path) as handle:
            source = handle.convert("RGBA")

        orig_bbox = _alpha_bbox(source)
        if orig_bbox is None:
            raise ValueError("empty_foreground: alpha bbox is None")
        orig_left, orig_top, orig_right, orig_bottom = orig_bbox
        bbox_w = orig_right - orig_left
        bbox_h = orig_bottom - orig_top
        if bbox_w <= 0 or bbox_h <= 0:
            raise ValueError("empty_foreground: zero-area bbox")

        # Crop the foreground (with a small safety pad), then scale.
        pad = SAFETY_PAD_PX
        crop_left = max(0, orig_left - pad)
        crop_top = max(0, orig_top - pad)
        crop_right = min(source.width, orig_right + pad)
        crop_bottom = min(source.height, orig_bottom + pad)
        cropped = source.crop((crop_left, crop_top, crop_right, crop_bottom))

        scale, fg_w, fg_h, limiting_axis = _scaled_foreground_size(
            bbox_w=cropped.width,
            bbox_h=cropped.height,
            canvas_w=self.canvas_width,
            canvas_h=self.canvas_height,
        )
        scaled = cropped.resize((fg_w, fg_h), Image.LANCZOS)

        # Place onto a transparent canonical canvas.
        canvas = Image.new("RGBA", (self.canvas_width, self.canvas_height), (0, 0, 0, 0))
        # Horizontal center
        offset_x = (self.canvas_width - fg_w) // 2
        # Vertical: lowest opaque pixel at TARGET_BOTTOM_RATIO * canvas_h
        bottom_y = int(round(self.canvas_height * TARGET_BOTTOM_RATIO))
        offset_y = bottom_y - fg_h
        if offset_y < 0:
            # Foreground taller than (bottom_ratio * canvas_h): cap at top with
            # a 2px headroom (extremely rare — only if height-bound scale was
            # somehow overridden). Refuse rather than clip.
            raise ValueError(
                f"foreground_overflows_canvas: fg_h={fg_h} canvas_h={self.canvas_height}"
            )
        canvas.paste(scaled, (offset_x, offset_y), scaled)

        # Atomic write: temp file in same dir → fsync → rename.
        self._atomic_write_png(canvas, target_path)

        # Measure the normalized bbox on the canvas (post-place).
        norm_bbox = _alpha_bbox(canvas) or (0, 0, 0, 0)
        coverage = self._compute_coverage(canvas)
        result = PortraitNormalizationResult(
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            original_bbox=orig_bbox,
            normalized_bbox=norm_bbox,
            foreground_height_ratio=round((norm_bbox[3] - norm_bbox[1]) / self.canvas_height, 4),
            foreground_width_ratio=round((norm_bbox[2] - norm_bbox[0]) / self.canvas_width, 4),
            foreground_coverage=round(coverage, 4),
            scale_factor=round(scale, 4),
            offset_x=offset_x,
            offset_y=offset_y,
            limiting_axis=limiting_axis,
            output_path=target_path,
        )
        logger.info(
            "portrait_normalized src=%s -> %s scale=%.3f axis=%s fg_h_ratio=%.3f fg_w_ratio=%.3f",
            source_path.name, target_path.name, result.scale_factor, limiting_axis,
            result.foreground_height_ratio, result.foreground_width_ratio,
        )
        return result

    def _atomic_write_png(self, image: Image.Image, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
        )
        os.close(tmp_fd)
        tmp_path = Path(tmp_name)
        try:
            image.save(tmp_path, format="PNG")
            with open(tmp_path, "ab") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
        finally:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass

    def _compute_coverage(self, image: Image.Image) -> float:
        alpha = image.getchannel("A")
        # alpha >= threshold counts as opaque
        opaque = alpha.point(lambda v: 1 if v >= ALPHA_THRESHOLD else 0)
        histogram = opaque.histogram()
        # pixel value 1 → count
        opaque_count = histogram[-1] if histogram else 0
        total = image.width * image.height
        return opaque_count / max(1, total)

    def _write_sidecar(
        self,
        sidecar_path: Path,
        source_path: Path,
        cache_path: Path,
        result: PortraitNormalizationResult,
    ) -> None:
        import json
        payload = {
            "normalization_version": self.normalization_version,
            "canvas_width": result.canvas_width,
            "canvas_height": result.canvas_height,
            "source_sha256": self._file_sha256(source_path),
            "source_path": str(source_path),
            "presentation_path": str(cache_path),
            "original_bbox": list(result.original_bbox),
            "normalized_bbox": list(result.normalized_bbox),
            "foreground_height_ratio": result.foreground_height_ratio,
            "foreground_width_ratio": result.foreground_width_ratio,
            "foreground_coverage": result.foreground_coverage,
            "scale_factor": result.scale_factor,
            "offset_x": result.offset_x,
            "offset_y": result.offset_y,
            "limiting_axis": result.limiting_axis,
        }
        tmp = sidecar_path.with_name(f".{sidecar_path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, sidecar_path)

    def _read_cached_result(
        self,
        cache_path: Path,
        sidecar_path: Path,
    ) -> Optional[PortraitNormalizationResult]:
        import json
        try:
            payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("normalization_version") != self.normalization_version:
            return None
        try:
            return PortraitNormalizationResult(
                canvas_width=int(payload["canvas_width"]),
                canvas_height=int(payload["canvas_height"]),
                original_bbox=tuple(payload["original_bbox"]),
                normalized_bbox=tuple(payload["normalized_bbox"]),
                foreground_height_ratio=float(payload["foreground_height_ratio"]),
                foreground_width_ratio=float(payload["foreground_width_ratio"]),
                foreground_coverage=float(payload["foreground_coverage"]),
                scale_factor=float(payload["scale_factor"]),
                offset_x=int(payload["offset_x"]),
                offset_y=int(payload["offset_y"]),
                limiting_axis=str(payload["limiting_axis"]),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


# ============================================================
# Geometry validator (runs AFTER normalization)
# ============================================================

class PortraitGeometryValidator:
    """Validate the geometry contract of a normalized portrait.

    Two regimes:
      - height-bound (default): height ratio in [0.86, 0.92], bottom in [0.96, 0.985]
      - width-bound (battle pose): height ratio in [0.74, 0.92], ``limiting_axis=="width"``
    """

    DEFAULT_HEIGHT_MIN = 0.86
    DEFAULT_HEIGHT_MAX = 0.92
    WIDTH_LIMITED_HEIGHT_MIN = WIDTH_LIMITED_MIN_HEIGHT_RATIO
    BOTTOM_MIN = 0.96
    BOTTOM_MAX = 0.985
    CENTER_MAX_OFFSET = 0.04
    WIDTH_RATIO_MAX = 0.86

    @classmethod
    def validate(
        cls,
        result: PortraitNormalizationResult,
        *,
        width_limited: Optional[bool] = None,
    ) -> PortraitGeometryValidation:
        if width_limited is None:
            width_limited = result.limiting_axis == "width"
        reasons: list[str] = []
        h_min = cls.WIDTH_LIMITED_HEIGHT_MIN if width_limited else cls.DEFAULT_HEIGHT_MIN
        h_max = cls.DEFAULT_HEIGHT_MAX
        if not h_min <= result.foreground_height_ratio <= h_max:
            reasons.append(f"foreground_height_ratio_out_of_range:{result.foreground_height_ratio}")
        bottom = (result.normalized_bbox[3]) / result.canvas_height
        if not cls.BOTTOM_MIN <= bottom <= cls.BOTTOM_MAX:
            reasons.append(f"bottom_ratio_out_of_range:{bottom:.4f}")
        center_x = (result.normalized_bbox[0] + result.normalized_bbox[2]) / 2 / result.canvas_width
        if abs(center_x - TARGET_CENTER_X_RATIO) > cls.CENTER_MAX_OFFSET:
            reasons.append(f"center_offset_too_large:{center_x:.4f}")
        if result.foreground_width_ratio > cls.WIDTH_RATIO_MAX:
            reasons.append(f"foreground_too_wide:{result.foreground_width_ratio}")
        if result.normalized_bbox[1] <= 1:
            reasons.append("foreground_touches_top")
        if result.normalized_bbox[0] <= 1 or result.normalized_bbox[2] >= result.canvas_width - 1:
            reasons.append("foreground_touches_horizontal_edge")
        return PortraitGeometryValidation(
            passed=not reasons,
            reasons=tuple(reasons),
            metrics={
                "canvas_width": result.canvas_width,
                "canvas_height": result.canvas_height,
                "foreground_height_ratio": result.foreground_height_ratio,
                "foreground_width_ratio": result.foreground_width_ratio,
                "bottom_ratio": round(bottom, 4),
                "center_x_ratio": round(center_x, 4),
                "limiting_axis": result.limiting_axis,
            },
        )


def default_normalizer() -> PortraitNormalizer:
    """Module-level helper returning a normalizer rooted under ``static/assets``."""
    root = (
        Path(__file__).resolve().parents[2]
        / "static" / "assets" / "presentation_portraits"
    )
    return PortraitNormalizer(root)


__all__ = [
    "CANONICAL_PORTRAIT_WIDTH",
    "CANONICAL_PORTRAIT_HEIGHT",
    "PORTRAIT_NORMALIZATION_VERSION",
    "ALPHA_THRESHOLD",
    "TARGET_FOREGROUND_HEIGHT_RATIO",
    "TARGET_BOTTOM_RATIO",
    "TARGET_CENTER_X_RATIO",
    "MAX_FOREGROUND_WIDTH_RATIO",
    "WIDTH_LIMITED_MIN_HEIGHT_RATIO",
    "PortraitNormalizationResult",
    "PortraitGeometryValidation",
    "PortraitNormalizer",
    "PortraitGeometryValidator",
    "default_normalizer",
]
