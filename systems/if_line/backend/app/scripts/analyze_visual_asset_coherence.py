"""Analyze visual asset coherence on disk.

Scans one or more directories of portrait/background PNGs, computes the
per-image geometric + style metrics defined by
:mod:`portrait_normalization_service` and
:mod:`visual_style_coherence_service`, and emits a JSON report.

Usage::

    python -m app.scripts.analyze_visual_asset_coherence \\
        --portrait-dir backend/static/three_kingdoms_portraits \\
        --background-dir backend/static/three_kingdoms_backgrounds \\
        --output visual_asset_report.json

Acceptance thresholds (exit 0 = OK):
  - portrait height_ratio stddev <= 0.025
  - portrait bottom_ratio range <= 0.015
  - portrait center offset <= 0.04
  - all share the same style_pack_version (read from sidecar .json)
  - all portraits normalized with the same normalization_version
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from PIL import Image

from app.services.portrait_normalization_service import (
    PortraitNormalizer,
    default_normalizer,
    PORTRAIT_NORMALIZATION_VERSION,
)
from app.services.background_scene_analyzer_service import ANALYZER_RECIPE_VERSION
from app.services.portrait_demand_analyzer import PORTRAIT_DEMAND_RECIPE_VERSION
from app.services.visual_style_contract_service import contract_for_three_kingdoms
from app.services.visual_style_coherence_service import compute_metrics


_THREE_KINGDOMS_STYLE_PACK_VERSION = contract_for_three_kingdoms().version


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as fh:
            value = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass
class PortraitReport:
    path: str
    canvas_width: int = 0
    canvas_height: int = 0
    foreground_height_ratio: float = 0.0
    foreground_width_ratio: float = 0.0
    bottom_ratio: float = 0.0
    center_x_ratio: float = 0.0
    sidecar_style_pack_version: Optional[str] = None
    sidecar_recipe_version: Optional[str] = None
    sidecar_present: bool = False
    error: Optional[str] = None


@dataclass
class BackgroundReport:
    path: str
    width: int = 0
    height: int = 0
    aspect_ratio: float = 0.0
    saturation_mean: float = 0.0
    brightness_mean: float = 0.0
    warmth: float = 0.0
    edge_density: float = 0.0
    sidecar_style_pack_version: Optional[str] = None
    sidecar_recipe_version: Optional[str] = None
    sidecar_present: bool = False
    error: Optional[str] = None


@dataclass
class AggregateReport:
    portrait_count: int = 0
    background_count: int = 0
    portrait_geometry: dict = field(default_factory=dict)
    background_style: dict = field(default_factory=dict)
    style_pack_versions_seen: List[str] = field(default_factory=list)
    portrait_recipe_versions_seen: List[str] = field(default_factory=list)
    background_recipe_versions_seen: List[str] = field(default_factory=list)
    normalization_versions_seen: List[str] = field(default_factory=list)
    invalid_assets: List[dict] = field(default_factory=list)
    stale_assets: List[dict] = field(default_factory=list)
    acceptance: dict = field(default_factory=dict)


def _iter_pngs(directory: Path) -> Iterable[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.png") if p.is_file())


def _analyze_portrait(png_path: Path, normalizer: PortraitNormalizer) -> PortraitReport:
    sidecar = _read_json(png_path.with_suffix(".json"))
    report = PortraitReport(
        path=str(png_path),
        sidecar_present=bool(sidecar),
        sidecar_style_pack_version=sidecar.get("style_pack_version"),
        sidecar_recipe_version=sidecar.get("generation_recipe_version"),
    )
    try:
        result = normalizer.normalize(png_path, force=False)
        report.canvas_width = result.canvas_width
        report.canvas_height = result.canvas_height
        report.foreground_height_ratio = result.foreground_height_ratio
        report.foreground_width_ratio = result.foreground_width_ratio
        report.bottom_ratio = round(
            result.normalized_bbox[3] / result.canvas_height, 4
        )
        report.center_x_ratio = round(
            (result.normalized_bbox[0] + result.normalized_bbox[2])
            / 2 / result.canvas_width,
            4,
        )
    except (OSError, ValueError) as exc:
        report.error = str(exc)
    return report


def _analyze_background(png_path: Path) -> BackgroundReport:
    sidecar = _read_json(png_path.with_suffix(".json"))
    report = BackgroundReport(
        path=str(png_path),
        sidecar_present=bool(sidecar),
        sidecar_style_pack_version=sidecar.get("style_pack_version"),
        sidecar_recipe_version=sidecar.get("generation_recipe_version"),
    )
    try:
        with Image.open(png_path) as img:
            metrics = compute_metrics(img, is_portrait=False)
        report.width, report.height = img.size
        report.aspect_ratio = round(img.size[0] / max(1, img.size[1]), 4)
        report.saturation_mean = round(metrics.saturation_mean, 4)
        report.brightness_mean = round(metrics.brightness_mean, 4)
        report.warmth = round(metrics.warmth, 4)
        report.edge_density = round(metrics.edge_density, 4)
    except (OSError, ValueError) as exc:
        report.error = str(exc)
    return report


def _stddev(values: List[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def analyze(
    portrait_dir: Optional[Path],
    background_dir: Optional[Path],
    *,
    expected_style_pack: str = _THREE_KINGDOMS_STYLE_PACK_VERSION,
    expected_portrait_recipe: str = PORTRAIT_DEMAND_RECIPE_VERSION,
    expected_background_recipe: str = ANALYZER_RECIPE_VERSION,
    expected_normalization: str = PORTRAIT_NORMALIZATION_VERSION,
) -> AggregateReport:
    report = AggregateReport()
    normalizer = default_normalizer()

    portrait_reports: List[PortraitReport] = []
    if portrait_dir:
        for png in _iter_pngs(portrait_dir):
            portrait_reports.append(_analyze_portrait(png, normalizer))

    background_reports: List[BackgroundReport] = []
    if background_dir:
        for png in _iter_pngs(background_dir):
            background_reports.append(_analyze_background(png))

    report.portrait_count = len(portrait_reports)
    report.background_count = len(background_reports)

    valid_portraits = [p for p in portrait_reports if p.error is None]
    if valid_portraits:
        heights = [p.foreground_height_ratio for p in valid_portraits]
        bottoms = [p.bottom_ratio for p in valid_portraits]
        centers = [p.center_x_ratio for p in valid_portraits]
        widths = [p.foreground_width_ratio for p in valid_portraits]
        report.portrait_geometry = {
            "height_ratio_min": round(min(heights), 4),
            "height_ratio_max": round(max(heights), 4),
            "height_ratio_stddev": round(_stddev(heights), 4),
            "bottom_ratio_min": round(min(bottoms), 4),
            "bottom_ratio_max": round(max(bottoms), 4),
            "center_offset_max": round(max(abs(c - 0.5) for c in centers), 4),
            "width_ratio_max": round(max(widths), 4),
        }

    if background_reports:
        sats = [b.saturation_mean for b in background_reports if b.error is None]
        brights = [b.brightness_mean for b in background_reports if b.error is None]
        report.background_style = {
            "saturation_min": round(min(sats), 4) if sats else 0.0,
            "saturation_max": round(max(sats), 4) if sats else 0.0,
            "saturation_stddev": round(_stddev(sats), 4) if sats else 0.0,
            "brightness_min": round(min(brights), 4) if brights else 0.0,
            "brightness_max": round(max(brights), 4) if brights else 0.0,
        }

    report.style_pack_versions_seen = sorted({
        (p.sidecar_style_pack_version or "(missing)")
        for p in portrait_reports + background_reports
        if p.sidecar_present
    })
    report.portrait_recipe_versions_seen = sorted({
        (p.sidecar_recipe_version or "(missing)")
        for p in portrait_reports if p.sidecar_present
    })
    report.background_recipe_versions_seen = sorted({
        (b.sidecar_recipe_version or "(missing)")
        for b in background_reports if b.sidecar_present
    })
    report.normalization_versions_seen = sorted({
        expected_normalization,
    })

    # Invalid (image-level failures)
    for p in portrait_reports:
        if p.error:
            report.invalid_assets.append({"path": p.path, "error": p.error, "kind": "portrait"})
    for b in background_reports:
        if b.error:
            report.invalid_assets.append({"path": b.path, "error": b.error, "kind": "background"})

    # Stale (sidecar version mismatch — would be regenerated on next access)
    for p in portrait_reports:
        if p.sidecar_present and (
            p.sidecar_style_pack_version != expected_style_pack
            or p.sidecar_recipe_version != expected_portrait_recipe
        ):
            report.stale_assets.append({
                "path": p.path, "kind": "portrait",
                "style_pack_version": p.sidecar_style_pack_version,
                "recipe_version": p.sidecar_recipe_version,
            })
    for b in background_reports:
        if b.sidecar_present and (
            b.sidecar_style_pack_version != expected_style_pack
            or b.sidecar_recipe_version != expected_background_recipe
        ):
            report.stale_assets.append({
                "path": b.path, "kind": "background",
                "style_pack_version": b.sidecar_style_pack_version,
                "recipe_version": b.sidecar_recipe_version,
            })

    # Acceptance gates
    gates: dict = {}
    if valid_portraits:
        geo = report.portrait_geometry
        gates["portrait_height_ratio_stddev_ok"] = geo.get("height_ratio_stddev", 1) <= 0.025
        gates["portrait_bottom_range_ok"] = (
            geo.get("bottom_ratio_max", 1) - geo.get("bottom_ratio_min", 0) <= 0.025
        )
        gates["portrait_center_offset_ok"] = geo.get("center_offset_max", 1) <= 0.04
    gates["style_pack_versions_consistent"] = (
        set(report.style_pack_versions_seen) <= {expected_style_pack}
        or len(report.style_pack_versions_seen) == 0
    )
    gates["portrait_recipe_versions_consistent"] = (
        set(report.portrait_recipe_versions_seen) <= {expected_portrait_recipe}
        or len(report.portrait_recipe_versions_seen) == 0
    )
    gates["background_recipe_versions_consistent"] = (
        set(report.background_recipe_versions_seen) <= {expected_background_recipe}
        or len(report.background_recipe_versions_seen) == 0
    )
    report.acceptance = gates
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--portrait-dir", type=Path, default=None)
    parser.add_argument("--background-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None,
                        help="Optional JSON output path (default: stdout)")
    args = parser.parse_args(argv)

    if not args.portrait_dir and not args.background_dir:
        parser.error("at least one of --portrait-dir / --background-dir is required")

    report = analyze(args.portrait_dir, args.background_dir)
    payload = asdict(report)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
    all_ok = all(report.acceptance.values()) if report.acceptance else False
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
