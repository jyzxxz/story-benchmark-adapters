"""Segment-authoritative background pipeline: enrich_segments keeps 1:1 count."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService
from app.services.scene_segmenter_service import SceneSegment


def _minimal_spec(name: str, scene_id: str = "s1") -> BackgroundSceneSpec:
    return BackgroundSceneSpec(
        scene_id=scene_id,
        scene_name=name,
        scene_selector=f"private_interior__night__{scene_id}__empty_required__default__01",
        scene_type="private_interior",
        environment_description=(
            f"{name}空无一人。室内灯光冷白，桌椅与设备静默排列，"
            f"窗外夜色浓稠，空气里浮着灰尘与电子元件的淡淡焦香气味。"
        ),
        lighting="冷白顶光",
        atmosphere="沉寂",
        time_of_day="night",
        camera_shot_type="interior_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
    )


def test_align_specs_to_segments_preserves_count_and_ids():
    svc = BackgroundSceneAnalyzerService()
    segments = [
        SceneSegment(segment_id=1, location="实验室", mood="night", scene_fingerprint="fp1"),
        SceneSegment(segment_id=2, location="沙漠", mood="day", scene_fingerprint="fp2"),
        SceneSegment(segment_id=3, location="车内", mood="dusk", scene_fingerprint="fp3"),
    ]
    # Analyzer only returned one scene — classic under-count bug.
    analyzer_specs = [_minimal_spec("实验室")]

    aligned = svc._align_specs_to_segments(
        segments,
        analyzer_specs,
        outline={"scene": "实验室", "summary": "多场景章节"},
        forbidden_characters=["张三"],
        forbidden_entities=[],
    )

    assert len(aligned) == 3
    assert [s.segment_id for s in aligned] == [1, 2, 3]
    assert [s.segment_location for s in aligned] == ["实验室", "沙漠", "车内"]
    assert [s.scene_fingerprint for s in aligned] == ["fp1", "fp2", "fp3"]
    # First keeps enriched description; others get fallback but still valid.
    assert "实验室" in aligned[0].environment_description
    assert len(aligned[1].environment_description) >= 40
    assert len(aligned[2].environment_description) >= 40


def test_enrich_segments_skips_llm_and_returns_all_segments(monkeypatch):
    svc = BackgroundSceneAnalyzerService()
    monkeypatch.setattr(
        "app.services.background_scene_analyzer_service.ANALYZER_ENABLED",
        False,
    )

    segments = [
        SceneSegment(segment_id=1, location="实验室", mood="night"),
        SceneSegment(segment_id=2, location="沙漠", mood="day"),
        SceneSegment(segment_id=3, location="车内", mood="dusk"),
    ]

    specs = asyncio.run(
        svc.enrich_segments(
            segments,
            chapter_index=1,
            chapter_content="短",  # also below LLM threshold
            chapter_outline={"scene": "实验室", "summary": "多场景"},
            story_bible={"characters": []},
            use_cache=False,
        )
    )

    assert len(specs) == 3
    assert [s.segment_id for s in specs] == [1, 2, 3]
    assert [s.scene_name for s in specs] == ["实验室", "沙漠", "车内"]


def test_dedup_scenes_keeps_distinct_segment_ids():
    from app.services.asset_management_service import AssetManagementService

    specs = [
        _minimal_spec("实验室", "a"),
        _minimal_spec("沙漠", "b"),
        _minimal_spec("车内", "c"),
    ]
    specs[0].segment_id = 1
    specs[1].segment_id = 2
    specs[2].segment_id = 3
    # Force identical fingerprints aside from segment_id
    for s in specs:
        s.scene_type = "private_interior"
        s.time_of_day = "night"
        s.weather = None
        s.people_policy = PeoplePolicy(mode="empty_required", rationale="test-dedup")

    deduped = AssetManagementService._dedup_scenes(None, specs)  # type: ignore[arg-type]
    assert len(deduped) == 3
    assert [s.segment_id for s in deduped] == [1, 2, 3]
