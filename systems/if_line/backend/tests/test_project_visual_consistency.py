"""project-visual-bible-v2 §F4 — project visual consistency tests.

Decision 2 (2026-07-17): single-image deviation is warning-only, NEVER
auto-rejects. The group-level evaluator flags statistical outliers but
``GroupConsistencyReport.rejected`` must always be False; the caller
decides what to do with the outlier list.

These tests use synthetic StyleDeviationReport values so they don't
depend on PIL image fixtures (decision: test fixtures stay out of git).
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.background_image_validator_service import (
    BACKGROUND_VALIDATION_VERSION,
    StyleDeviationReport,
    background_image_validator_service,
)
from app.services.project_visual_consistency_service import (
    ProjectBaseline,
    project_visual_consistency_service,
)


def _deviation(saturation=0.3, contrast=0.5, edge_density=0.06, palette_distance=0.2, warnings=None):
    return StyleDeviationReport(
        saturation=saturation,
        contrast=contrast,
        edge_density=edge_density,
        palette_distance_to_baseline=palette_distance,
        warnings=warnings or [],
    )


# ----------------------------------------------------------------------
# F4-A: single-image evaluate_against_bible — never rejects
# ----------------------------------------------------------------------

def test_evaluate_against_bible_never_rejects_even_on_egregious_deviation():
    """Decision 2: even a wildly off image gets ``rejected=False``.
    Only warnings are recorded."""
    from app.services.project_visual_bible_service import (
        project_visual_bible_service,
    )
    sb = {"raw_json": {}, "worldview": "x", "style_rules": "", "characters": []}
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")

    # Simulate a wildly off baseline (cel-shading project, image looks like neon photo)
    baseline = {
        "saturation_mean": 0.3,
        "contrast": 0.5,
        "edge_density": 0.06,
        "palette_signature": [0.4, 0.1, 0.1, 0.1, 0.1, 0.1, 0.05, 0.05],
    }

    class _FakePath:
        def __init__(self, metrics):
            self._m = metrics
        # The validator will try PIL.Image.open and fall back to None.
        # We can't easily inject metrics without PIL fixtures, so instead
        # we directly call the metric-based code path.

    # Direct path: bypass compute_style_metrics and test the gap logic
    # by monkeypatching.
    import app.services.background_image_validator_service as mod

    def fake_compute(self, image_path):
        from app.services.visual_style_coherence_service import ImageStyleMetrics
        m = image_path  # we pass metrics directly as the "image_path"
        if isinstance(m, ImageStyleMetrics):
            return m
        return None

    original = mod.BackgroundImageValidatorService.compute_style_metrics
    mod.BackgroundImageValidatorService.compute_style_metrics = fake_compute
    try:
        from app.services.visual_style_coherence_service import ImageStyleMetrics
        fake_metrics = ImageStyleMetrics(
            saturation_mean=0.95, brightness_mean=0.6, warmth=0.0,
            contrast=0.92, edge_density=0.5,
            palette_signature=(0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.3),
        )
        report = background_image_validator_service.evaluate_against_bible(
            fake_metrics,  # injected via fake_compute
            bible=bible,
            project_baseline=baseline,
        )
        assert report.rejected is False
        assert len(report.warnings) > 0
        assert "saturation_gap" in report.reason
    finally:
        mod.BackgroundImageValidatorService.compute_style_metrics = original


def test_validation_version_bumped():
    assert BACKGROUND_VALIDATION_VERSION == "project-coherence-v2"


# ----------------------------------------------------------------------
# F4-B: group-level outlier detection
# ----------------------------------------------------------------------

def test_evaluate_group_flags_statistical_outlier():
    deviations = {
        "normal1": _deviation(0.30, 0.50, 0.06, 0.20),
        "normal2": _deviation(0.32, 0.48, 0.05, 0.25),
        "normal3": _deviation(0.28, 0.52, 0.07, 0.18),
        "normal4": _deviation(0.31, 0.49, 0.055, 0.22),
        "outlier": _deviation(
            0.95, 0.92, 0.50, 0.85,
            warnings=["saturation_gap=0.5", "palette_distance=0.85"],
        ),
    }
    report = project_visual_consistency_service.evaluate_group(deviations)
    assert "outlier" in report.outliers
    assert all(n not in report.outliers for n in ("normal1", "normal2", "normal3", "normal4"))
    # Decision 2: group-level also never rejects
    assert report.rejected is False


def test_evaluate_group_no_outliers_when_all_consistent():
    deviations = {
        f"bg{i}": _deviation(0.30 + i * 0.005, 0.50, 0.06, 0.20)
        for i in range(6)
    }
    report = project_visual_consistency_service.evaluate_group(deviations)
    assert report.outliers == []
    assert report.rejected is False


def test_evaluate_group_small_group_skips_stdev_test():
    """Below MIN_GROUP_FOR_STDEV, any per-image warning flags the asset
    directly (statistical test is meaningless on tiny groups)."""
    deviations = {
        "a": _deviation(warnings=["palette_distance=0.6"]),
    }
    report = project_visual_consistency_service.evaluate_group(deviations)
    assert "a" in report.outliers
    assert "mad_test=off" in report.reason


def test_reject_outlier_predicate_pure():
    """reject_outlier is a pure predicate; performs no side effect."""
    deviations = {
        "normal": _deviation(0.30, 0.50, 0.06, 0.20),
        "outlier": _deviation(0.95, 0.92, 0.50, 0.85, warnings=["x"]),
        "normal2": _deviation(0.31, 0.49, 0.06, 0.20),
        "normal3": _deviation(0.29, 0.51, 0.07, 0.18),
        "normal4": _deviation(0.30, 0.50, 0.05, 0.21),
    }
    report = project_visual_consistency_service.evaluate_group(deviations)
    assert project_visual_consistency_service.reject_outlier(report, "outlier") is True
    assert project_visual_consistency_service.reject_outlier(report, "normal") is False


def test_compute_project_baseline_returns_none_on_empty():
    """Missing images → None (caller treats as 'no baseline available')."""
    result = project_visual_consistency_service.compute_project_baseline([])
    assert result is None
