"""Deterministic visual style coherence validator.

A portrait and a background belong to the same art bible only if their
low-level image statistics (saturation, brightness, color temperature,
contrast, dominant-hue palette distance) sit in the same neighborhood. This
module computes those statistics and emits a coherence verdict.

Pure PIL — no LLM, no billed API. The hard-fail cases (``passed=False``) are
intentionally narrow:
  - background looks like a photograph (saturation + edge density signature)
  - background is neon-saturated (modern cyberpunk palette)
  - portrait looks like a 3D render (very high contrast + very smooth edges)
  - portrait vs. background color temperature is opposite (warm vs. cold)

Everything else (mild palette gaps, slight saturation differences) emits a
warning rather than failing — the contract is "same neighborhood", not
"identical".
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

from PIL import Image, ImageStat


# ============================================================
# Per-image style metrics
# ============================================================

@dataclass
class ImageStyleMetrics:
    saturation_mean: float          # [0, 1]
    brightness_mean: float          # [0, 1]
    warmth: float                   # [-1, 1] — R/B balance
    contrast: float                 # [0, 1] — normalized std of luma
    edge_density: float             # [0, 1] — fraction of high-gradient pixels
    palette_signature: Tuple[float, ...]  # quantized hue histogram (8 bins, normalized)

    def to_dict(self) -> dict:
        return {
            "saturation_mean": round(self.saturation_mean, 4),
            "brightness_mean": round(self.brightness_mean, 4),
            "warmth": round(self.warmth, 4),
            "contrast": round(self.contrast, 4),
            "edge_density": round(self.edge_density, 4),
            "palette_signature": [round(v, 4) for v in self.palette_signature],
        }


@dataclass
class CoherenceVerdict:
    passed: bool
    warnings: Tuple[str, ...]
    failures: Tuple[str, ...]
    portrait_metrics: ImageStyleMetrics
    background_metrics: ImageStyleMetrics
    coherence: dict
    style_contract_version: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "warnings": list(self.warnings),
            "failures": list(self.failures),
            "portrait_metrics": self.portrait_metrics.to_dict(),
            "background_metrics": self.background_metrics.to_dict(),
            "coherence": self.coherence,
            "style_contract_version": self.style_contract_version,
        }


# ============================================================
# Internal: compute metrics for a single image
# ============================================================

_PALETTE_BINS = 8
_NEON_SATURATION_THRESHOLD = 0.78
_PHOTO_EDGE_DENSITY_MIN = 0.05  # photos tend to have low edge density
_PHOTO_SATURATION_RANGE = (0.18, 0.55)  # photos often fall here
_3D_RENDER_CONTRAST_MIN = 0.32  # 3D CG has unusually high contrast
_HARD_PALETTE_DISTANCE = 0.55
_HARD_SATURATION_GAP = 0.32
_HARD_TEMPERATURE_GAP = 0.45
_HARD_CONTRAST_GAP = 0.30
_WARN_PALETTE_DISTANCE = 0.28
_WARN_SATURATION_GAP = 0.18
_WARN_TEMPERATURE_GAP = 0.28
_WARN_CONTRAST_GAP = 0.18


def _alpha_mask(image: Image.Image, threshold: int = 24) -> Optional[Image.Image]:
    if image.mode != "RGBA":
        return None
    alpha = image.getchannel("A")
    return alpha.point(lambda v: 255 if v >= threshold else 0)


def _quantize_hue_signature(rgb_image: Image.Image, mask: Optional[Image.Image] = None) -> Tuple[float, ...]:
    """Normalized 8-bin hue histogram in [0, 1]."""
    hsv = rgb_image.convert("HSV")
    hue = hsv.getchannel("H")
    histogram = hue.histogram(mask=mask) if mask is not None else hue.histogram()
    total = sum(histogram)
    if total <= 0:
        return tuple([0.0] * _PALETTE_BINS)
    bin_size = max(1, len(histogram) // _PALETTE_BINS)
    bins = [
        sum(histogram[i * bin_size:(i + 1) * bin_size]) / total
        for i in range(_PALETTE_BINS)
    ]
    # pad if histogram length didn't divide evenly
    while len(bins) < _PALETTE_BINS:
        bins.append(0.0)
    return tuple(bins[:_PALETTE_BINS])


def _edge_density(gray: Image.Image) -> float:
    """Fraction of pixels whose neighbor difference exceeds a small threshold.

    A crude edge detector — good enough to distinguish flat illustrations
    (low) from photographs / noisy renders (high).
    """
    import numpy as np
    arr = np.asarray(gray, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    dx = np.abs(arr[:, 1:] - arr[:, :-1])
    dy = np.abs(arr[1:, :] - arr[:-1, :])
    edge_threshold = 18.0
    edges = (dx > edge_threshold).sum() + (dy > edge_threshold).sum()
    total = dx.size + dy.size
    return float(edges) / max(1, total)


def compute_metrics(image: Image.Image, *, is_portrait: bool) -> ImageStyleMetrics:
    """Compute the deterministic style metrics for one image.

    For portraits (RGBA), only alpha-opaque pixels are sampled so transparent
    margins don't drag the statistics toward grey.
    """
    if is_portrait:
        rgba = image.convert("RGBA")
        mask = _alpha_mask(rgba)
        rgb = rgba.convert("RGB")
    else:
        rgb = image.convert("RGB")
        mask = None
    hsv = rgb.convert("HSV")

    if mask is not None:
        sat_stat = ImageStat.Stat(hsv.getchannel("S"), mask=mask)
        val_stat = ImageStat.Stat(hsv.getchannel("V"), mask=mask)
        rgb_stat = ImageStat.Stat(rgb, mask=mask)
    else:
        sat_stat = ImageStat.Stat(hsv.getchannel("S"))
        val_stat = ImageStat.Stat(hsv.getchannel("V"))
        rgb_stat = ImageStat.Stat(rgb)

    saturation_mean = (sat_stat.mean[0] or 0) / 255.0
    brightness_mean = (val_stat.mean[0] or 0) / 255.0

    # Warmth: (R - B) normalized to [-1, 1]
    r_mean = rgb_stat.mean[0] or 0
    b_mean = rgb_stat.mean[2] or 0
    warmth = ((r_mean - b_mean) / 255.0)

    # Contrast: std of brightness / 128 (so 0..~1 for typical images)
    brightness_std = (val_stat.stddev[0] or 0) / 255.0
    contrast = min(1.0, brightness_std * 2.0)

    # Edge density (mask not applicable; use full image)
    gray_for_edges = rgb.convert("L")
    edge_density = _edge_density(gray_for_edges)

    palette_sig = _quantize_hue_signature(rgb, mask=mask)
    return ImageStyleMetrics(
        saturation_mean=saturation_mean,
        brightness_mean=brightness_mean,
        warmth=warmth,
        contrast=contrast,
        edge_density=edge_density,
        palette_signature=palette_sig,
    )


# ============================================================
# Validator
# ============================================================

class VisualStyleCoherenceValidator:
    """Compare portrait vs. background metrics within a style contract."""

    def __init__(self, *, style_contract_version: Optional[str] = None) -> None:
        self.style_contract_version = style_contract_version

    def validate(
        self,
        *,
        portrait: Image.Image,
        background: Image.Image,
    ) -> CoherenceVerdict:
        p = compute_metrics(portrait, is_portrait=True)
        b = compute_metrics(background, is_portrait=False)

        palette_distance = self._palette_distance(p.palette_signature, b.palette_signature)
        saturation_gap = abs(p.saturation_mean - b.saturation_mean)
        temperature_gap = abs(p.warmth - b.warmth)
        contrast_gap = abs(p.contrast - b.contrast)

        coherence = {
            "palette_distance": round(palette_distance, 4),
            "saturation_gap": round(saturation_gap, 4),
            "temperature_gap": round(temperature_gap, 4),
            "contrast_gap": round(contrast_gap, 4),
        }

        failures: list[str] = []
        warnings: list[str] = []

        # Background-specific hard fails
        if b.saturation_mean >= _NEON_SATURATION_THRESHOLD:
            failures.append("background_neon_saturated")
        if (
            _PHOTO_SATURATION_RANGE[0] <= b.saturation_mean <= _PHOTO_SATURATION_RANGE[1]
            and b.edge_density < _PHOTO_EDGE_DENSITY_MIN
        ):
            failures.append("background_likely_photograph")
        # Portrait-specific hard fails
        if p.contrast >= _3D_RENDER_CONTRAST_MIN and p.edge_density < 0.04:
            failures.append("portrait_likely_3d_render")
        # Cross-image hard fails
        if palette_distance >= _HARD_PALETTE_DISTANCE:
            failures.append("palette_distance_too_large")
        if saturation_gap >= _HARD_SATURATION_GAP:
            failures.append("saturation_gap_too_large")
        if temperature_gap >= _HARD_TEMPERATURE_GAP:
            failures.append("temperature_opposite")
        if contrast_gap >= _HARD_CONTRAST_GAP:
            failures.append("contrast_gap_too_large")

        # Soft warnings
        if palette_distance >= _WARN_PALETTE_DISTANCE and "palette_distance_too_large" not in failures:
            warnings.append("palette_distance_warm")
        if saturation_gap >= _WARN_SATURATION_GAP and "saturation_gap_too_large" not in failures:
            warnings.append("saturation_gap_warm")
        if temperature_gap >= _WARN_TEMPERATURE_GAP and "temperature_opposite" not in failures:
            warnings.append("temperature_gap_warm")
        if contrast_gap >= _WARN_CONTRAST_GAP and "contrast_gap_too_large" not in failures:
            warnings.append("contrast_gap_warm")

        return CoherenceVerdict(
            passed=not failures,
            warnings=tuple(warnings),
            failures=tuple(failures),
            portrait_metrics=p,
            background_metrics=b,
            coherence=coherence,
            style_contract_version=self.style_contract_version,
        )

    @staticmethod
    def _palette_distance(a: Tuple[float, ...], b: Tuple[float, ...]) -> float:
        """Earth-mover-lite: sum of |CDF_a - CDF_b|, normalized to [0, 1]."""
        if not a or not b:
            return 0.0
        cdf_a = 0.0
        cdf_b = 0.0
        diff_sum = 0.0
        for x, y in zip(a, b):
            cdf_a += x
            cdf_b += y
            diff_sum += abs(cdf_a - cdf_b)
        return diff_sum / max(1, len(a))


__all__ = [
    "ImageStyleMetrics",
    "CoherenceVerdict",
    "VisualStyleCoherenceValidator",
    "compute_metrics",
]
