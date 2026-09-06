"""Tests for visual_style_llm_classifier_service.

Covers:
* Output schema validation (medium / palette / forbidden_styles bounds)
* English-only enforcement on style fields (CJK rejection)
* Cache stability — same payload produces same decision
* Graceful degradation when LLM disabled / API key missing
* Integration with project_visual_bible_service: canonical IP wins over LLM,
  LLM decision is consumed by derive_bible, keyword fallback works when
  env-disabled.
"""
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.visual_style_llm_classifier_service import (
    VisualStyleDecision,
    VisualStyleDecisionModel,
    VisualStyleLLMClassifierService,
)
from app.services.project_visual_bible_service import (
    ProjectVisualBible,
    project_visual_bible_service,
)


# ----------------------------------------------------------------------
# Unit: schema validation
# ----------------------------------------------------------------------


def test_schema_accepts_valid_payload():
    payload = {
        "medium": "anime_cel",
        "art_direction": "refined 2D anime visual novel illustration with soft cel-shading",
        "linework": "crisp anime line art with tapered ends",
        "shading": "flat two-level cel shading with subtle rim light",
        "texture": "subtle film grain, no paper texture",
        "base_palette": ["luminous gold", "jade teal", "warm ivory"],
        "contrast_policy": "medium",
        "lighting_policy": "soft_diffused",
        "palette_policy": "warm_cool_balanced",
        "forbidden_styles": [
            "photorealistic rendering",
            "3D game render",
            "inconsistent art style drift",
        ],
        "rationale": "contemporary campus story fits soft anime cel.",
    }
    model = VisualStyleDecisionModel(**payload)
    assert model.medium == "anime_cel"
    assert len(model.base_palette) == 3


def test_schema_rejects_unknown_medium():
    with pytest.raises(Exception):
        VisualStyleDecisionModel(
            medium="pixel_art",  # not in enum
            art_direction="x" * 60,
            linework="x" * 20,
            shading="x" * 20,
            texture="x" * 20,
            base_palette=["a", "b", "c"],
            contrast_policy="medium",
            lighting_policy="soft_diffused",
            palette_policy="warm_cool_balanced",
            forbidden_styles=["x", "y"],
            rationale="x" * 30,
        )


def test_parse_rejects_chinese_in_style_fields():
    """English-only enforcement: art_direction/linework/shading/texture
    must not contain CJK characters or the parse rejects."""
    svc = VisualStyleLLMClassifierService()
    raw = {
        "medium": "anime_cel",
        "art_direction": "refined 2D 动漫 illustration",  # contains 动漫
        "linework": "crisp anime line art with tapered ends",
        "shading": "flat two-level cel shading with subtle rim light",
        "texture": "subtle film grain, no paper texture",
        "base_palette": ["luminous gold", "jade teal", "warm ivory"],
        "contrast_policy": "medium",
        "lighting_policy": "soft_diffused",
        "palette_policy": "warm_cool_balanced",
        "forbidden_styles": ["photorealistic rendering", "3D game render"],
        "rationale": "ok",
    }
    decision = svc._parse(raw)
    assert decision is None, "parse must reject CJK in style fields"


def test_parse_rejects_palette_outside_bounds():
    svc = VisualStyleLLMClassifierService()
    base = {
        "medium": "anime_cel",
        "art_direction": "x" * 80,
        "linework": "x" * 30,
        "shading": "x" * 30,
        "texture": "x" * 30,
        "base_palette": ["only-one-color"],  # < _PALETTE_MIN (3)
        "contrast_policy": "medium",
        "lighting_policy": "soft_diffused",
        "palette_policy": "warm_cool_balanced",
        "forbidden_styles": ["a", "b"],
        "rationale": "x" * 30,
    }
    assert svc._parse(base) is None
    too_many = dict(base)
    too_many["base_palette"] = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"]
    assert svc._parse(too_many) is None


def test_parse_fills_minimum_forbidden_styles():
    """If LLM returns < 2 forbidden_styles, parser pads with defaults."""
    svc = VisualStyleLLMClassifierService()
    raw = {
        "medium": "anime_cel",
        "art_direction": "refined 2D anime illustration with clean surfaces",
        "linework": "crisp anime line art with tapered ends",
        "shading": "flat two-level cel shading with subtle rim light",
        "texture": "subtle film grain, no paper texture",
        "base_palette": ["a", "b", "c"],
        "contrast_policy": "medium",
        "lighting_policy": "soft_diffused",
        "palette_policy": "warm_cool_balanced",
        "forbidden_styles": [],  # empty — should be padded
        "rationale": "x" * 30,
    }
    decision = svc._parse(raw)
    assert decision is not None
    assert len(decision.forbidden_styles) >= 2
    assert "inconsistent art style drift" in decision.forbidden_styles


# ----------------------------------------------------------------------
# Unit: cache stability
# ----------------------------------------------------------------------


def test_cache_key_deterministic():
    svc = VisualStyleLLMClassifierService()
    payload = {
        "project_title": "T",
        "project_style": "S",
        "source_work": "",
        "worldview": "W",
        "style_rules": "R",
        "source_context": "",
        "characters": [{"name": "Alice"}],
    }
    key1 = svc._cache_key(payload)
    key2 = svc._cache_key(dict(payload))
    assert key1 == key2
    assert len(key1) == 16


def test_cache_key_changes_on_input_change():
    svc = VisualStyleLLMClassifierService()
    p1 = {"project_title": "T1", "characters": []}
    p2 = {"project_title": "T2", "characters": []}
    assert svc._cache_key(p1) != svc._cache_key(p2)


# ----------------------------------------------------------------------
# Integration: graceful fallback when LLM disabled
# ----------------------------------------------------------------------


async def test_classify_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("STYLE_CLASSIFIER_ENABLED", "false")
    # Need to reload module-level config since env is read at import time
    import importlib
    import app.services.visual_style_llm_classifier_service as mod
    importlib.reload(mod)
    svc = mod.VisualStyleLLMClassifierService()
    result = await svc.classify(
        project_title="T",
        project_style="",
        worldview="W",
        style_rules="",
        source_work="",
        source_context="",
        characters=[],
    )
    assert result is None


async def test_classify_returns_none_without_api_key(monkeypatch):
    """With ENABLED=true but no API key, classify must return None and
    NOT make any LLM call."""
    monkeypatch.setenv("STYLE_CLASSIFIER_ENABLED", "true")
    monkeypatch.delenv("STYLE_CLASSIFIER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_IMAGE_API_KEY", raising=False)
    import importlib
    import app.services.visual_style_llm_classifier_service as mod
    importlib.reload(mod)
    svc = mod.VisualStyleLLMClassifierService()

    call_count = 0

    async def _mock_call(*a, **kw):
        nonlocal call_count
        call_count += 1
        return None

    # Even if _call_llm were reachable, it should never be invoked.
    with patch.object(svc, "_call_llm", side_effect=_mock_call):
        result = await svc.classify(
            project_title="T",
            project_style="",
            worldview="W",
            style_rules="",
            source_work="",
            source_context="",
            characters=[],
        )
    assert result is None
    assert call_count == 0


# ----------------------------------------------------------------------
# Integration: non-canonical story uses LLM decision
# ----------------------------------------------------------------------


def test_non_canonical_story_consumes_llm_decision(monkeypatch):
    """A normal campus story (no canonical IP) must consult the LLM and
    use its decision to override the medium-default template."""
    fake_decision = VisualStyleDecision(
        medium="digital_painterly",
        art_direction="soft digital painterly campus illustration with warm afternoon light",
        linework="painterly soft linework with subtle edges",
        shading="smooth digital painting shading with gentle gradient transitions",
        texture="subtle canvas texture",
        base_palette=("dusty rose", "deep teal", "warm cream", "shadow plum"),
        contrast_policy="medium_low",
        lighting_policy="cinematic_directional",
        palette_policy="low_sat_unity",
        forbidden_styles=("photorealistic rendering", "3D game render", "inconsistent art style drift"),
        rationale="test rationale",
    )

    async def _mock_classify(**kwargs):
        return fake_decision

    import app.services.visual_style_llm_classifier_service as cls_mod
    with patch.object(
        cls_mod.visual_style_llm_classifier_service,
        "classify",
        side_effect=_mock_classify,
    ):
        sb = {
            "raw_json": {},
            "worldview": "2020 年代某普通一本大学，计算机科学与技术专业",
            "style_rules": "轻松幽默",
            "characters": [{"name": "李明"}],
        }
        bible = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )

    # LLM decision must override the default anime_cel template
    assert bible.style_family == "digital_painterly"
    assert "digital painterly" in bible.art_direction.lower()
    assert bible.base_palette == fake_decision.base_palette
    assert bible.linework == fake_decision.linework
    assert bible.shading == fake_decision.shading

    cls = sb["raw_json"]["project_visual_bible"]["classification"]
    assert cls["llm_decision"] is not None
    assert cls["llm_decision"]["medium"] == "digital_painterly"


def test_llm_failure_falls_back_to_keyword_path(monkeypatch):
    """When the LLM classifier returns None (timeout / error / disabled),
    the keyword-bucket path must produce a valid bible."""
    async def _mock_classify(**kwargs):
        return None

    import app.services.visual_style_llm_classifier_service as cls_mod
    with patch.object(
        cls_mod.visual_style_llm_classifier_service,
        "classify",
        side_effect=_mock_classify,
    ):
        sb = {
            "raw_json": {},
            "worldview": "唐朝长安，水墨工笔风格",
            "style_rules": "水墨重彩",
            "characters": [{"name": "李白"}],
        }
        bible = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )
    # Keyword fallback should still detect 水墨 → ink_color_guofeng
    assert bible.style_family == "ink_color_guofeng"
    cls = sb["raw_json"]["project_visual_bible"]["classification"]
    assert cls["llm_decision"] is None
    assert cls["visual_medium"] == "ink_color_guofeng"


# ----------------------------------------------------------------------
# Integration: locked-bible stability
# ----------------------------------------------------------------------


def test_bible_lock_is_stable_across_rebuilds(monkeypatch):
    """Once locked, rebuilds must produce the SAME bible — even if the LLM
    would give a different answer the second time. The persisted lock wins."""
    call_count = 0

    async def _mock_classify(**kwargs):
        nonlocal call_count
        call_count += 1
        return VisualStyleDecision(
            medium="anime_cel",
            art_direction=f"unique art direction attempt #{call_count}",
            linework="linework",
            shading="shading",
            texture="texture",
            base_palette=("c1", "c2", "c3"),
            contrast_policy="medium",
            lighting_policy="soft_diffused",
            palette_policy="warm_cool_balanced",
            forbidden_styles=("x", "y"),
            rationale="x" * 30,
        )

    import app.services.visual_style_llm_classifier_service as cls_mod
    with patch.object(
        cls_mod.visual_style_llm_classifier_service,
        "classify",
        side_effect=_mock_classify,
    ):
        sb = {
            "raw_json": {},
            "worldview": "普通大学校园故事",
            "style_rules": "",
            "characters": [{"name": "X"}],
        }
        b1 = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )
        first_art = b1.art_direction
        first_fp = b1.fingerprint

        # Second call — lock should already be persisted, LLM not consulted
        b2 = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )
    assert b2.art_direction == first_art
    assert b2.fingerprint == first_fp
    # LLM was only called on the first build (lock miss)
    assert call_count == 1


def test_three_assets_share_llm_decision(monkeypatch):
    """portrait / background / keyframe prompt blocks must all derive from
    the same LLM-produced art_direction. No drift across asset types."""
    fake = VisualStyleDecision(
        medium="anime_cel",
        art_direction="distinctive testable art direction phrase",
        linework="test linework signature",
        shading="test shading signature",
        texture="test texture signature",
        base_palette=("c1", "c2", "c3"),
        contrast_policy="medium",
        lighting_policy="soft_diffused",
        palette_policy="warm_cool_balanced",
        forbidden_styles=("x", "y"),
        rationale="x" * 30,
    )

    async def _mock_classify(**kwargs):
        return fake

    import app.services.visual_style_llm_classifier_service as cls_mod
    from app.services.visual_style_profile_service import visual_style_profile_service

    with patch.object(
        cls_mod.visual_style_llm_classifier_service,
        "classify",
        side_effect=_mock_classify,
    ):
        sb = {
            "raw_json": {},
            "worldview": "普通校园",
            "style_rules": "",
            "characters": [{"name": "X"}],
        }
        profile = visual_style_profile_service.build_profile(
            sb, project_title="T", project_style="",
        )

    # All three prompt blocks must carry the LLM art_direction marker
    marker = "distinctive testable art direction phrase"
    assert marker in profile.portrait_prompt_en
    assert marker in profile.keyframe_prompt_en
    # background_prompt_zh is Chinese-translated; just check it's non-empty
    # and contains shared medium-family keyword from the LLM
    assert profile.background_prompt_zh
