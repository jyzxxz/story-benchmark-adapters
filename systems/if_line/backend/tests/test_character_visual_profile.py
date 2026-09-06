from pathlib import Path

from app.services.character_visual_profile_service import (
    SCHEMA_VERSION,
    fingerprint,
    load_library_portraits,
    match_library,
    normalize_character,
    normalize_profile,
    normalize_story_bible,
)
from app.services.prompt_builder_service import prompt_builder_service


LIBRARY = (
    Path(__file__).resolve().parents[2]
    / "static/assets/universal_visual_novel_latest/planned_assets/portrait"
)


def _detective_profile():
    return {
        "schema_version": SCHEMA_VERSION,
        "world_style": "modern",
        "species": "human",
        "age_group": "young_adult",
        "gender_presentation": "masculine",
        "role_family": "professional",
        "role_key": "detective",
        "body": {"height": "tall", "build": "lean"},
        "face": {"shape": "angular", "skin_tone": "light", "eye_color": "dark_brown"},
        "hair": {"color": "black", "length": "short", "style": "messy"},
        "outfit": {"style": "business", "primary_colors": ["black", "charcoal"], "items": ["long_coat"]},
        "signature_features": ["thin_scar_left_brow"],
        "accessories": ["wristwatch"],
        "visual_temperament": ["reserved", "serious"],
    }


def test_profile_projections_are_complete_and_deterministic():
    first = normalize_character({"name": "林夜", "gender": "male", "visual_profile": _detective_profile()})
    second = normalize_character({"name": "林夜", "gender": "male", "visual_profile": _detective_profile()})

    assert first == second
    assert first["appearance"] == first["visual_description_cn"]
    assert "侦探" in first["visual_description_cn"]
    assert "detective" in first["visual_prompt_en"]
    assert first["visual_fingerprint"].startswith("cv1:")
    assert first["visual_fingerprint"] == fingerprint(first["visual_profile"])


def test_invalid_llm_values_are_normalized_to_registered_defaults():
    profile = normalize_profile({
        "world_style": "unsupported-era",
        "species": "unknown-creature",
        "age_group": "young",
        "gender_presentation": "male",
        "role_key": "not-in-registry",
        "outfit": {"primary_colors": ["ultraviolet", "red"]},
    })

    assert profile["schema_version"] == SCHEMA_VERSION
    assert profile["world_style"] == "modern"
    assert profile["species"] == "human"
    assert profile["age_group"] == "young_adult"
    assert profile["gender_presentation"] == "masculine"
    assert profile["role_key"] == "civilian"
    assert profile["outfit"]["primary_colors"] == ["red"]


def test_legacy_story_bible_is_upgraded_without_losing_character_fields():
    bible = normalize_story_bible({
        "worldview": "现代都市悬疑",
        "characters": [{"name": "周警官", "gender": "male", "role": "刑警", "personality": "冷静"}],
    })
    character = bible["characters"][0]

    assert character["name"] == "周警官"
    assert character["personality"] == "冷静"
    assert character["visual_profile_source"] == "legacy_inferred"
    assert character["visual_profile"]["role_key"] == "detective"
    assert bible["character_visual_schema_version"] == SCHEMA_VERSION


def test_existing_library_parses_to_94_base_portraits_and_matches_detective():
    portraits = load_library_portraits(LIBRARY)
    matches = match_library(_detective_profile(), portraits, emotion="thinking", limit=3)

    assert len(portraits) == 94
    assert matches[0]["base_key"] == "portrait_modern_detective_m"
    assert matches[0]["selected_emotion"] == "thinking"
    assert matches[0]["emotion_fallback"] == "exact"
    assert matches[0]["confidence"] == "high"
    assert matches[0]["score"] >= matches[1]["score"]


def test_library_match_never_blocks_and_explains_low_confidence():
    portraits = load_library_portraits(LIBRARY)
    unusual = normalize_profile({
        "world_style": "fantasy",
        "species": "spirit",
        "age_group": "toddler",
        "gender_presentation": "androgynous",
        "role_key": "pilot",
        "role_family": "scifi_crew",
    })
    matches = match_library(unusual, portraits, emotion="cry", limit=1)

    assert len(matches) == 1
    assert matches[0]["asset_key"]
    assert matches[0]["confidence"] in {"medium", "low"}
    assert matches[0]["conflicts"]
    assert matches[0]["emotion_fallback"] in {"similar", "neutral_fallback", "first_available"}


def test_prompt_builder_carries_canonical_profile_across_emotion_variants():
    bible = {
        "worldview": "现代都市悬疑",
        "characters": [{"name": "林夜", "gender": "male", "visual_profile": _detective_profile()}],
    }
    prompts = prompt_builder_service.build_portrait_prompts(
        bible,
        project_id=7,
        variations=[
            {"emotion": "neutral", "outfit": "default", "pose": "standing"},
            {"emotion": "angry", "outfit": "default", "pose": "combat"},
        ],
    )

    assert len(prompts) == 2
    assert prompts[0]["character_visual_profile"] == prompts[1]["character_visual_profile"]
    assert prompts[0]["visual_fingerprint"] == prompts[1]["visual_fingerprint"]
    assert "detective" in prompts[0]["appearance_prompt"]

