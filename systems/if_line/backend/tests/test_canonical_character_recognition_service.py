"""Tests for canonical_character_recognition_service.

Covers:
* Output schema validation (is_canonical / franchise / prompt_en bounds)
* English-only enforcement on prompt_en (CJK rejection)
* prompt_en length bounds (60-600)
* Cache stability — same payload reuses decision
* Graceful degradation when LLM disabled / API key missing
* recognize_sync wrapper works inside a running asyncio loop
* Integration with source_visual_profile_service.character_appearance:
  hardcoded Genshin profile still wins; LLM path activates for non-
  hardcoded franchises (e.g. One Piece / Ace).
* Integration with apply_character_overrides: LLM-identified canonical
  characters get visual_prompt_en + source_identity_locked=True written
  into the story bible.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.canonical_character_recognition_service import (
    CANONICAL_CHARACTER_RECOGNITION_ENABLED,
    CanonicalCharacterAnchor,
    CanonicalCharacterDecisionModel,
    CanonicalCharacterRecognitionService,
    canonical_character_recognition_service,
)
from app.services.source_visual_profile_service import (
    source_visual_profile_service,
)


# ----------------------------------------------------------------------
# Unit: schema validation
# ----------------------------------------------------------------------


def test_schema_accepts_canonical_payload():
    payload = {
        "is_canonical": True,
        "franchise_name": "One Piece",
        "canonical_name": "Portgas D. Ace",
        "prompt_en": (
            "canonical Portgas D. Ace from One Piece, tall lean young man, "
            "short black hair swept back, sharp dark eyes, open red-orange "
            "sleeveless vest, blue denim shorts, orange bead necklace, "
            "short knife at hip, purple Whitebeard skull cross tattoo on "
            "left chest; preserve the recognizable official character "
            "design and silhouette"
        ),
        "rationale": "Ace is the brother of Luffy in One Piece.",
    }
    model = CanonicalCharacterDecisionModel(**payload)
    assert model.is_canonical is True
    assert model.franchise_name == "One Piece"


def test_schema_accepts_non_canonical_payload():
    payload = {
        "is_canonical": False,
        "franchise_name": "",
        "canonical_name": "",
        "prompt_en": "",
        "rationale": "OC created for this story; no franchise match.",
    }
    model = CanonicalCharacterDecisionModel(**payload)
    assert model.is_canonical is False


# ----------------------------------------------------------------------
# Unit: _parse validation
# ----------------------------------------------------------------------


def test_parse_rejects_chinese_in_prompt_en():
    """English-only enforcement: prompt_en with CJK chars is rejected."""
    svc = CanonicalCharacterRecognitionService()
    raw = {
        "is_canonical": True,
        "franchise_name": "One Piece",
        "canonical_name": "Ace",
        "prompt_en": (
            "canonical Ace from One Piece, "
            "黑发短发向后梳, "
            "preserve the recognizable official character design and silhouette"
        ),
        "rationale": "ok",
    }
    assert svc._parse(raw) is None


def test_parse_rejects_too_short_prompt_en():
    svc = CanonicalCharacterRecognitionService()
    raw = {
        "is_canonical": True,
        "franchise_name": "One Piece",
        "canonical_name": "Ace",
        "prompt_en": "too short",  # < 60
        "rationale": "ok",
    }
    assert svc._parse(raw) is None


def test_parse_rejects_canonical_true_but_empty_franchise():
    svc = CanonicalCharacterRecognitionService()
    raw = {
        "is_canonical": True,
        "franchise_name": "",
        "canonical_name": "Ace",
        "prompt_en": "x" * 100,
        "rationale": "ok",
    }
    assert svc._parse(raw) is None


def test_parse_non_canonical_passes_with_empty_fields():
    svc = CanonicalCharacterRecognitionService()
    raw = {
        "is_canonical": False,
        "franchise_name": "",
        "canonical_name": "",
        "prompt_en": "",
        "rationale": "OC.",
    }
    decision = svc._parse(raw)
    assert decision is not None
    assert decision.is_canonical is False
    assert decision.prompt_en == ""


# ----------------------------------------------------------------------
# Unit: cache stability
# ----------------------------------------------------------------------


def test_cache_key_deterministic():
    svc = CanonicalCharacterRecognitionService()
    payload = {
        "character_name": "艾斯",
        "source_work": "海贼王",
        "worldview": "新世界",
        "style_rules": "",
        "fallback_appearance": "",
    }
    key1 = svc._cache_key(payload)
    key2 = svc._cache_key(dict(payload))
    assert key1 == key2
    assert len(key1) == 16


def test_cache_key_changes_on_input_change():
    svc = CanonicalCharacterRecognitionService()
    p1 = {"character_name": "艾斯", "source_work": "海贼王"}
    p2 = {"character_name": "鸣人", "source_work": "火影"}
    assert svc._cache_key(p1) != svc._cache_key(p2)


# ----------------------------------------------------------------------
# Integration: graceful fallback when LLM disabled
# ----------------------------------------------------------------------


def test_recognize_returns_none_when_disabled(monkeypatch):
    monkeypatch.setenv("CANONICAL_CHARACTER_RECOGNITION_ENABLED", "false")
    # The env var was read at module import; patch the module-level constant
    import app.services.canonical_character_recognition_service as mod
    monkeypatch.setattr(mod, "CANONICAL_CHARACTER_RECOGNITION_ENABLED", False)

    svc = mod.CanonicalCharacterRecognitionService()
    result = asyncio.run(
        svc.recognize(
            character_name="艾斯",
            source_work="海贼王",
            worldview="新世界",
        )
    )
    assert result is None


def test_recognize_returns_none_without_api_key(monkeypatch):
    """With ENABLED=true but no API key, recognize must return None and
    NOT make any LLM call."""
    import app.services.canonical_character_recognition_service as mod
    monkeypatch.setattr(mod, "CANONICAL_CHARACTER_RECOGNITION_ENABLED", True)
    monkeypatch.setattr(
        mod, "CANONICAL_CHARACTER_RECOGNITION_API_KEY", None
    )

    svc = mod.CanonicalCharacterRecognitionService()

    async def _explode(*a, **kw):
        raise AssertionError("LLM must not be called when API key missing")

    with patch.object(svc, "_call_llm", side_effect=_explode):
        result = asyncio.run(
            svc.recognize(
                character_name="艾斯",
                source_work="海贼王",
                worldview="新世界",
            )
        )
    assert result is None


# ----------------------------------------------------------------------
# Integration: recognize_sync works inside a running event loop
# ----------------------------------------------------------------------


def test_recognize_sync_works_inside_running_loop():
    """The sync wrapper must NOT deadlock when the caller is already
    inside an asyncio loop — it spins up its own worker thread."""

    async def driver():
        svc = CanonicalCharacterRecognitionService()
        fake_anchor = CanonicalCharacterAnchor(
            is_canonical=True,
            franchise_name="One Piece",
            canonical_name="Ace",
            prompt_en="canonical Ace from One Piece, " + ("x" * 80),
            rationale="ok",
        )
        async def _mock(**kw):
            return fake_anchor
        with patch.object(svc, "recognize", side_effect=_mock):
            return svc.recognize_sync(
                character_name="艾斯",
                source_work="海贼王",
                worldview="新世界",
            )

    result = asyncio.run(driver())
    assert result is not None
    assert result.is_canonical is True
    assert result.franchise_name == "One Piece"


# ----------------------------------------------------------------------
# Integration: source_visual_profile_service.character_appearance
# ----------------------------------------------------------------------


def test_character_appearance_llm_anchor_for_non_hardcoded_franchise(monkeypatch):
    """For 海贼王 / 艾斯 — the LLM recognizer produces an
    English official-design prompt, and character_appearance returns it."""
    fake_anchor = CanonicalCharacterAnchor(
        is_canonical=True,
        franchise_name="One Piece",
        canonical_name="Portgas D. Ace",
        prompt_en=(
            "canonical Portgas D. Ace from One Piece, tall lean young man, "
            "short black hair swept back, sharp dark eyes, open red-orange "
            "sleeveless vest, blue denim shorts, orange bead necklace, "
            "short knife at hip, purple Whitebeard skull cross tattoo on "
            "left chest; preserve the recognizable official character "
            "design and silhouette"
        ),
        rationale="Ace",
    )
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=fake_anchor,
    ):
        appearance = source_visual_profile_service.character_appearance(
            "",
            "艾斯",
            "fallback description",
            source_work="海贼王",
            worldview="新世界",
        )
    assert "Ace" in appearance
    assert "Whitebeard" in appearance
    assert "preserve the recognizable official character design" in appearance


def test_character_appearance_falls_back_when_llm_says_not_canonical(monkeypatch):
    """When the LLM says is_canonical=false (OC), character_appearance
    returns the user's fallback description unchanged."""
    fake_anchor = CanonicalCharacterAnchor(
        is_canonical=False,
        franchise_name="",
        canonical_name="",
        prompt_en="",
        rationale="OC",
    )
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=fake_anchor,
    ):
        appearance = source_visual_profile_service.character_appearance(
            "",
            "小明",
            "fallback-appearance-text",
            source_work="原创",
            worldview="校园",
        )
    assert appearance == "fallback-appearance-text"


def test_character_appearance_falls_back_when_llm_unavailable(monkeypatch):
    """When the LLM recognizer returns None (timeout / error / disabled),
    character_appearance falls back to fallback_appearance."""
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=None,
    ):
        appearance = source_visual_profile_service.character_appearance(
            "",
            "艾斯",
            "fallback-text",
            source_work="海贼王",
            worldview="新世界",
        )
    assert appearance == "fallback-text"


def test_character_appearance_legacy_callers_without_context_skip_llm():
    """Backward compatibility: callers that don't pass source_work /
    worldview must NOT trigger the LLM path and get the fallback."""
    async def _explode(*a, **kw):
        raise AssertionError("LLM must not be called without context")

    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        side_effect=_explode,
    ):
        appearance = source_visual_profile_service.character_appearance(
            "",
            "艾斯",
            "fallback-only",
        )
    assert appearance == "fallback-only"


# ----------------------------------------------------------------------
# Integration: apply_character_overrides writes identity lock for LLM hits
# ----------------------------------------------------------------------


def test_apply_character_overrides_locks_llm_canonical_character():
    """For a non-Genshin canonical character (海贼王 / 艾斯), the bible
    after apply_character_overrides must carry visual_prompt_en +
    source_identity_locked=True + canonical_franchise='One Piece'."""
    fake_anchor = CanonicalCharacterAnchor(
        is_canonical=True,
        franchise_name="One Piece",
        canonical_name="Portgas D. Ace",
        prompt_en=(
            "canonical Portgas D. Ace from One Piece, tall lean young man, "
            "short black hair swept back, sharp dark eyes, open red-orange "
            "sleeveless vest; preserve the recognizable official character "
            "design and silhouette"
        ),
        rationale="Ace",
        look_variant="Marineford era",
        fixed_features=("short black hair", "dark eyes"),
        canonical_outfit="open red-orange sleeveless vest and blue denim shorts",
        asymmetric_traits=("Whitebeard tattoo on his back",),
    )
    sb = {
        "source_work": "海贼王",
        "worldview": "顶上战争",
        "characters": [
            {"name": "艾斯", "appearance": "黑发向后梳"},
        ],
    }
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=fake_anchor,
    ):
        out = source_visual_profile_service.apply_character_overrides(sb, "")

    char = out["characters"][0]
    assert char["source_identity_locked"] is True
    assert char["canonical_identity"]["identity_prompt_en"] == fake_anchor.prompt_en
    assert char["canonical_identity"]["look_variant"] == "Marineford era"
    assert char["canonical_identity"]["fixed_features"] == ["short black hair", "dark eyes"]
    assert char["canonical_franchise"] == "One Piece"
    assert char["canonical_name_official"] == "Portgas D. Ace"
    assert char["visual_fingerprint"].startswith("cv1:")
    assert char["canonical_identity_fingerprint"].startswith("ci1:")
    assert char["visual_prompt_en"] != fake_anchor.prompt_en


def test_apply_character_overrides_preserves_oc_when_llm_says_not_canonical():
    """For an OC (LLM says is_canonical=false), apply_character_overrides
    must NOT inject any identity lock — the user description stays."""
    fake_anchor = CanonicalCharacterAnchor(
        is_canonical=False,
        franchise_name="",
        canonical_name="",
        prompt_en="",
        rationale="OC",
    )
    sb = {
        "source_work": "原创",
        "worldview": "校园",
        "characters": [
            {"name": "小明", "appearance": "黑发学生"},
        ],
    }
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=fake_anchor,
    ):
        out = source_visual_profile_service.apply_character_overrides(sb, "")

    char = out["characters"][0]
    assert "source_identity_locked" not in char
    assert char["appearance"] == "黑发学生"
