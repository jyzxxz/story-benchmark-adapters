"""Fan-work source style: LLM-driven recognition regression tests.

The hardcoded ``_GENSHIN`` registry has been removed.  Every source work —
including 原神 — is now resolved by the LLM canonical-character recognizer
and the LLM visual-style classifier.  These tests mock both LLM services
and verify the plumbing still produces a locked bible + locked character
identity, without depending on a live LLM call.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.models import Project, StoryBible
from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.asset_management_service import AssetManagementService
from app.services.canonical_character_recognition_service import (
    CanonicalCharacterAnchor,
    canonical_character_recognition_service,
)
from app.services.project_visual_bible_service import (
    SCHEMA_KEY,
    VERSION,
    project_visual_bible_service,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.source_visual_profile_service import source_visual_profile_service
from app.services.visual_style_profile_service import visual_style_profile_service


def _genshin_story_bible() -> dict:
    return {
        "raw_json": {},
        "worldview": "提瓦特大陆由七神与元素力塑造，故事发生在璃月。",
        "style_rules": "轻松幽默",
        "source_work": "原神",
        "characters": [
            {
                "name": "钟离",
                "gender": "male",
                # This reproduces project 16's bad generic gallery projection.
                # It must remain intact for library compatibility while the
                # separate source identity wins during AI generation.
                "visual_profile": {
                    "schema_version": "character_visual_v1",
                    "world_style": "historical",
                    "species": "human",
                    "age_group": "adult",
                    "gender_presentation": "masculine",
                    "role_family": "leadership",
                    "role_key": "mentor",
                    "body": {"height": "tall", "build": "athletic"},
                    "face": {"shape": "square", "skin_tone": "light", "eye_color": "gold"},
                    "hair": {"color": "brown", "length": "long", "style": "ponytail"},
                    "outfit": {
                        "style": "historical_robe",
                        "primary_colors": ["gold", "black"],
                        "items": ["robe", "cape"],
                    },
                    "signature_features": ["eyepatch"],
                    "accessories": [],
                    "visual_temperament": ["calm", "confident"],
                },
            },
            {
                "name": "陆明",
                "gender": "male",
                "appearance": "young man in a plain green jacket and trousers",
            },
        ],
    }


def _zhongli_llm_anchor() -> CanonicalCharacterAnchor:
    """Simulates the LLM recognizing 钟离 as a canonical Genshin character."""
    return CanonicalCharacterAnchor(
        is_canonical=True,
        franchise_name="Genshin Impact",
        canonical_name="Zhongli",
        prompt_en=(
            "canonical Zhongli from Genshin Impact, tall elegant adult man, "
            "amber-gold eyes with geometric pupils, layered dark brown hair, "
            "long tailored black-brown coat with gold geo patterns, no eyepatch; "
            "preserve his recognizable official character design and silhouette"
        ),
        rationale="Zhongli is a canonical Genshin Impact character.",
        look_variant="adult official playable design",
        fixed_features=(
            "tall elegant adult male silhouette",
            "amber-gold eyes with geometric pupils",
        ),
        canonical_outfit="long tailored black-brown coat with gold geo patterns",
    )


# ---------------------------------------------------------------------------
# detect() — no longer matches a registry; echoes explicit source_work.
# ---------------------------------------------------------------------------

def test_detect_echoes_explicit_source_work():
    detection = source_visual_profile_service.detect(explicit_source_work="原神")
    assert detection.source_work == "原神"
    assert detection.confidence == 1.0
    assert detection.explicit is True
    assert detection.profile is None  # no hardcoded profile anymore


def test_detect_empty_when_no_source_work():
    detection = source_visual_profile_service.detect()
    assert detection.source_work == ""
    assert detection.profile is None


# ---------------------------------------------------------------------------
# infer_classification — no hardcoded profile bypass; genre from keywords.
# ---------------------------------------------------------------------------

def test_infer_classification_has_no_source_profile_id():
    """Without a hardcoded registry, source_profile_id is always empty and
    genre is inferred from keyword signals, not a profile override."""
    sb = _genshin_story_bible()
    # Add a fantasy keyword so genre matching picks it up — previously the
    # hardcoded Genshin profile forced fantasy regardless of text.
    sb["worldview"] = "提瓦特大陆是一个充满魔法与神话的奇幻世界。"
    classification = project_visual_bible_service.infer_classification(
        sb,
        project_title="提瓦提大陆的神",
        project_style="轻松幽默",
        source_context="提瓦特钟离假死，岩神之位动摇",
    )
    assert classification.source_profile_id == ""
    # Genre comes from keyword matching on "奇幻/魔法/神话" signals.
    assert classification.narrative_genre == "fantasy"


# ---------------------------------------------------------------------------
# build_for_project — explicit source_work still appends fidelity directive.
# ---------------------------------------------------------------------------

def test_explicit_source_work_keeps_named_source_fidelity():
    sb = {
        "raw_json": {},
        "worldview": "奇幻世界",
        "style_rules": "",
        "characters": [],
    }
    bible = project_visual_bible_service.build_for_project(
        sb,
        source_work="Example Franchise",
    )
    assert bible.source_work == "Example Franchise"
    assert bible.source_profile_id == ""
    assert "Example Franchise" in bible.art_direction
    assert "generic genre art" in " ".join(bible.forbidden_styles)


def test_genshin_source_work_appends_fidelity_directive():
    """原神 is no longer special-cased — it gets the same generic source
    fidelity directive as any other named source work."""
    sb = _genshin_story_bible()
    bible = project_visual_bible_service.build_for_project(
        sb,
        source_work="原神",
        source_context="钟离作为岩神守护璃月",
    )
    assert bible.source_profile_id == ""
    assert bible.source_work == "原神"
    assert "原神" in bible.art_direction
    assert "generic genre art" in " ".join(bible.forbidden_styles)


# ---------------------------------------------------------------------------
# apply_character_overrides — LLM recognizer locks canonical identity.
# ---------------------------------------------------------------------------

def test_llm_recognized_zhongli_locks_identity():
    """When the LLM recognizes 钟离 as canonical, the identity is locked
    with recognition_source='llm' (no longer 'hardcoded:genshin-impact')."""
    sb = _genshin_story_bible()
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=_zhongli_llm_anchor(),
    ):
        overridden = source_visual_profile_service.apply_character_overrides(sb, "")
    zhongli = overridden["characters"][0]

    assert zhongli["source_identity_locked"] is True
    # The generic gallery visual_profile is preserved (eyepatch stays in the
    # library projection) while the LLM identity wins during generation.
    assert zhongli["visual_profile"]["signature_features"] == ["eyepatch"]
    identity_prompt = zhongli["canonical_identity"]["identity_prompt_en"]
    assert "no eyepatch" in identity_prompt
    assert "amber-gold eyes" in identity_prompt
    assert zhongli["canonical_identity_fingerprint"].startswith("ci1:")
    # recognition_source is now 'llm', not 'hardcoded:...'
    assert zhongli["canonical_identity"]["recognition_source"] == "llm"


def test_non_canonical_character_keeps_original_appearance():
    """When the LLM says is_canonical=False (OC), no identity is locked."""
    sb = _genshin_story_bible()
    fake_oc = CanonicalCharacterAnchor(
        is_canonical=False, franchise_name="", canonical_name="",
        prompt_en="", rationale="OC",
    )
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=fake_oc,
    ):
        overridden = source_visual_profile_service.apply_character_overrides(sb, "")
    zhongli = overridden["characters"][0]
    assert "source_identity_locked" not in zhongli


# ---------------------------------------------------------------------------
# Portrait prompts — LLM identity flows through prompt_builder.
# ---------------------------------------------------------------------------

def test_portrait_prompt_uses_llm_identity_for_canonical_character():
    sb = _genshin_story_bible()
    profile = visual_style_profile_service.build_profile(
        sb,
        project_title="提瓦提大陆的神",
        source_context="提瓦特钟离假死",
    )
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=_zhongli_llm_anchor(),
    ):
        overridden = source_visual_profile_service.apply_character_overrides(
            sb, profile.source_profile_id,
        )
    prompts = prompt_builder_service.build_portrait_prompts(
        overridden,
        project_id=16,
        variations=[{"emotion": "neutral", "outfit": "default", "pose": "standing"}],
        visual_style_profile=profile,
    )
    by_name = {prompt["character_name"]: prompt for prompt in prompts}

    assert "canonical Zhongli" in by_name["钟离"]["appearance_prompt"]
    assert "no eyepatch" in by_name["钟离"]["appearance_prompt"]
    assert by_name["钟离"]["style_fingerprint"] == profile.fingerprint
    assert by_name["陆明"]["style_fingerprint"] == profile.fingerprint


# ---------------------------------------------------------------------------
# Asset manager persistence — locked bible + fingerprint.
# ---------------------------------------------------------------------------

def test_asset_manager_persists_locked_bible_and_fingerprint(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'source-style.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    project = Project(
        title="提瓦提大陆的神",
        characters=["钟离"],
        story_start="提瓦特钟离假死",
        story_end="岩神契约迎来终局",
        style="轻松幽默",
        source_work="原神",
        pace="fast",
        extra_requirements="",
        status="chapter_generating",
    )
    db.add(project)
    db.flush()
    story_bible = StoryBible(
        project_id=project.id,
        worldview="提瓦特大陆由元素力统治",
        characters=_genshin_story_bible()["characters"],
        style_rules="轻松幽默",
        raw_json={
            "worldview": "提瓦特大陆由元素力统治",
            "characters": _genshin_story_bible()["characters"],
            "style_rules": "轻松幽默",
        },
    )
    db.add(story_bible)
    db.commit()

    service = AssetManagementService(db)
    bible = service._ensure_visual_bible_locked(project.id, story_bible)
    profile = service._build_visual_style_profile(project.id, story_bible)
    db.commit()
    db.expire_all()

    try:
        persisted = db.query(StoryBible).filter(StoryBible.project_id == project.id).one()
        lock = persisted.raw_json[SCHEMA_KEY]
        assert lock["schema_version"] == VERSION
        # source_profile_id is now always empty (no hardcoded registry).
        assert lock["bible"]["source_profile_id"] == ""
        assert lock["bible"]["fingerprint"] == bible.fingerprint
        assert profile.fingerprint == bible.fingerprint
    finally:
        db.close()
        engine.dispose()
