"""project-visual-bible-v2 §B6 — project-level visual consistency.

Single-image deviation (``BackgroundImageValidatorService.evaluate_against_bible``)
catches egregious outliers but cannot detect *drift* — a project where every
background drifts 10% off the locked bible, so none of them trigger the
per-image threshold but the project as a whole still looks incoherent.

This service closes that gap:

* ``compute_project_baseline`` — mean over a reference set of background
  images, used as the per-project baseline that ``evaluate_against_bible``
  compares against.
* ``evaluate_group`` — given a dict of {asset_id: StyleDeviationReport},
  flag assets whose deviation exceeds the group threshold AND whose
  deviation is also a statistical outlier within the group (≥ 2σ).
* ``reject_outlier`` — pure predicate. Returns True iff the given asset is
  a sustained statistical outlier. Per decision 2 (2026-07-17) the caller
  decides whether to actually delete / regenerate; this layer never performs
  destructive action.

Pure stdlib + PIL — no LLM, no billed API.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from app.services.background_image_validator_service import (
    BACKGROUND_VALIDATION_VERSION,
    StyleDeviationReport,
    background_image_validator_service,
)
from app.services.visual_style_coherence_service import ImageStyleMetrics

logger = logging.getLogger("project_visual_consistency")


@dataclass(frozen=True)
class ProjectBaseline:
    """Mean style signature for a project's reference background set.

    ``palette_signature`` is the element-wise mean of each image's
    8-bin hue histogram, so it stays in the same space as
    ``ImageStyleMetrics.palette_signature``.
    """

    saturation_mean: float
    contrast: float
    edge_density: float
    palette_signature: Tuple[float, ...]
    sample_size: int
    version: str = BACKGROUND_VALIDATION_VERSION

    def to_dict(self) -> dict:
        return {
            "saturation_mean": round(self.saturation_mean, 4),
            "contrast": round(self.contrast, 4),
            "edge_density": round(self.edge_density, 4),
            "palette_signature": [round(v, 4) for v in self.palette_signature],
            "sample_size": self.sample_size,
            "version": self.version,
        }


@dataclass
class GroupConsistencyReport:
    """project-visual-bible-v2 §F5 — group-level consistency verdict.

    ``outliers`` lists asset ids that satisfy BOTH:
      * per-image deviation exceeds the warning threshold (decision 2: only
        recorded, never auto-rejected by the validator), AND
      * the deviation is a statistical outlier within the group (≥ 2σ from
        the group mean, or group has fewer than 4 samples in which case the
        statistical test is skipped and only the threshold check applies).

    ``rejected`` is always False — this layer is advisory only. Callers
    (asset_management_service, scheduled cleanup tasks) decide what to do
    with the outlier list.
    """

    project_baseline: Optional[ProjectBaseline]
    deviations: Dict[str, StyleDeviationReport] = field(default_factory=dict)
    outliers: List[str] = field(default_factory=list)
    group_stats: Dict[str, float] = field(default_factory=dict)
    rejected: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "project_baseline": (
                self.project_baseline.to_dict() if self.project_baseline else None
            ),
            "deviations": {k: v.to_dict() for k, v in self.deviations.items()},
            "outliers": list(self.outliers),
            "group_stats": dict(self.group_stats),
            "rejected": self.rejected,
            "reason": self.reason,
            "version": BACKGROUND_VALIDATION_VERSION,
        }


class ProjectVisualConsistencyService:
    """Project-level + group-level visual consistency evaluator."""

    # Statistical outlier detection uses median + MAD (median absolute
    # deviation) rather than mean + stdev. The group is typically small
    # (5-20 backgrounds per chapter), and the very outlier we're trying to
    # detect would inflate stdev and mask itself under mean+stdev. MAD is
    # robust to up to 50% contamination.
    OUTLIER_MAD_MULTIPLIER = 3.5  # ≈ 2σ under normal distribution
    # Minimum group size for the MAD test to apply. Below this we only use
    # the per-image warning threshold (statistical test is meaningless
    # with too few samples).
    MIN_GROUP_FOR_STDEV = 4

    def compute_project_baseline(
        self,
        image_paths: Iterable[Path],
    ) -> Optional[ProjectBaseline]:
        """Mean over reference images → ProjectBaseline.

        Returns ``None`` if no image in ``image_paths`` yields usable
        metrics (all unreadable / not PNG). Caller treats as "no baseline
        available" rather than rejecting.
        """
        paths = [Path(p) for p in image_paths if p]
        metrics_list: List[ImageStyleMetrics] = []
        for p in paths:
            m = background_image_validator_service.compute_style_metrics(p)
            if m is not None:
                metrics_list.append(m)

        if not metrics_list:
            return None

        sat = statistics.fmean(m.saturation_mean for m in metrics_list)
        con = statistics.fmean(m.contrast for m in metrics_list)
        edge = statistics.fmean(m.edge_density for m in metrics_list)

        palette_len = len(metrics_list[0].palette_signature) or 1
        palette_mean = tuple(
            statistics.fmean(m.palette_signature[i] for m in metrics_list)
            for i in range(palette_len)
        )
        return ProjectBaseline(
            saturation_mean=sat,
            contrast=con,
            edge_density=edge,
            palette_signature=palette_mean,
            sample_size=len(metrics_list),
        )

    def evaluate_group(
        self,
        deviations: Dict[str, StyleDeviationReport],
        project_baseline: Optional[ProjectBaseline] = None,
    ) -> GroupConsistencyReport:
        """Flag statistical outliers within the group.

        ``deviations`` is a mapping {asset_id: StyleDeviationReport}. The
        reports can come from
        ``BackgroundImageValidatorService.evaluate_against_bible`` (single
        image vs baseline) or any equivalent source.

        Per decision 2: this method NEVER rejects. The returned report has
        ``rejected=False``; callers inspect ``outliers`` to decide.
        """
        report = GroupConsistencyReport(project_baseline=project_baseline)

        # Record raw deviations.
        report.deviations = dict(deviations)

        if not deviations:
            report.reason = "empty deviation set"
            return report

        # Group stats — for transparency / debugging.
        sats = [
            d.saturation for d in deviations.values()
            if d.saturation is not None
        ]
        cons = [
            d.contrast for d in deviations.values()
            if d.contrast is not None
        ]
        edges = [
            d.edge_density for d in deviations.values()
            if d.edge_density is not None
        ]
        dists = [
            d.palette_distance_to_baseline for d in deviations.values()
            if d.palette_distance_to_baseline is not None
        ]
        report.group_stats = {
            "saturation_median": round(statistics.median(sats), 4) if sats else 0.0,
            "saturation_mad": round(self._mad(sats), 4),
            "contrast_median": round(statistics.median(cons), 4) if cons else 0.0,
            "contrast_mad": round(self._mad(cons), 4),
            "edge_density_median": round(statistics.median(edges), 4) if edges else 0.0,
            "edge_density_mad": round(self._mad(edges), 4),
            "palette_distance_median": round(statistics.median(dists), 4) if dists else 0.0,
            "palette_distance_mad": round(self._mad(dists), 4),
            "sample_size": len(deviations),
        }

        # An asset is an outlier if (a) it already triggered a per-image
        # warning AND (b) at least one of its dimensions is
        # ≥ OUTLIER_MAD_MULTIPLIER × MAD from the group median.
        use_stdev = len(deviations) >= self.MIN_GROUP_FOR_STDEV

        for asset_id, dev in deviations.items():
            if not dev.warnings:
                continue
            if not use_stdev:
                # Small group — per-image warning alone suffices.
                report.outliers.append(asset_id)
                continue

            if self._is_statistical_outlier(dev.saturation, sats) or \
               self._is_statistical_outlier(dev.contrast, cons) or \
               self._is_statistical_outlier(dev.edge_density, edges) or \
               self._is_statistical_outlier(dev.palette_distance_to_baseline, dists):
                report.outliers.append(asset_id)

        report.reason = (
            f"{len(report.outliers)} outlier(s) of {len(deviations)} "
            f"(mad_test={'on' if use_stdev else 'off'})"
        )
        return report

    @staticmethod
    def _mad(values: List[float]) -> float:
        """Median Absolute Deviation (robust scale estimator)."""
        if len(values) < 2:
            return 0.0
        try:
            med = statistics.median(values)
            return statistics.median([abs(v - med) for v in values])
        except statistics.StatisticsError:
            return 0.0

    def _is_statistical_outlier(
        self,
        value: Optional[float],
        population: List[float],
    ) -> bool:
        """≥ OUTLIER_MAD_MULTIPLIER × MAD from the population median?"""
        if value is None or len(population) < self.MIN_GROUP_FOR_STDEV:
            return False
        try:
            median = statistics.median(population)
            mad = self._mad(population)
        except statistics.StatisticsError:
            return False
        # Use 1e-6 floor for MAD to avoid divide-by-zero when all values
        # are identical (in which case any non-median value is a 100%
        # outlier and should be flagged).
        if mad < 1e-6:
            return abs(value - median) > 1e-6
        return abs(value - median) >= self.OUTLIER_MAD_MULTIPLIER * mad

    def reject_outlier(
        self,
        report: GroupConsistencyReport,
        asset_id: str,
    ) -> bool:
        """project-visual-bible-v2 §F5 — pure predicate.

        Returns True iff ``asset_id`` is in ``report.outliers``. This method
        performs NO destructive action — the caller decides whether to
        delete, regenerate, or merely log. Per decision 2, even when this
        returns True the recommended action is "log + flag in DB", not
        automatic deletion.
        """
        return asset_id in report.outliers


project_visual_consistency_service = ProjectVisualConsistencyService()


__all__ = [
    "BACKGROUND_VALIDATION_VERSION",
    "ProjectBaseline",
    "GroupConsistencyReport",
    "ProjectVisualConsistencyService",
    "project_visual_consistency_service",
]
