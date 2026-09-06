from pathlib import Path
from unittest.mock import patch

from app.services.canonical_character_recognition_service import (
    CanonicalCharacterAnchor,
    canonical_character_recognition_service,
)
from app.services.character_visual_profile_service import (
    fingerprint,
    load_library_portraits,
    load_v2_library_portraits,
    match_library,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.portrait_prompt_identity_service import (
    build_portrait_rewriter_identity,
)
from app.services.source_visual_profile_service import source_visual_profile_service


ASSET_ROOT = Path(__file__).resolve().parents[2] / "static/assets"


def _obito_gallery_profile() -> dict:
    return {
        "schema_version": "character_visual_v1",
        "world_style": "fantasy",
        "species": "human",
        "age_group": "teen",
        "gender_presentation": "masculine",
        "role_family": "student",
        "role_key": "student",
        "body": {"height": "average", "build": "lean"},
        "face": {"shape": "round", "skin_tone": "light", "eye_color": "black"},
        "hair": {"color": "black", "length": "short", "style": "spiky"},
        "outfit": {
            "style": "sportswear",
            "primary_colors": ["orange", "blue"],
            "items": ["jacket", "trousers"],
        },
        "signature_features": [],
        "accessories": [],
        "visual_temperament": ["energetic"],
    }


def _obito_anchor() -> CanonicalCharacterAnchor:
    return CanonicalCharacterAnchor(
        is_canonical=True,
        franchise_name="Naruto",
        canonical_name="Obito Uchiha",
        prompt_en=(
            "canonical young Obito Uchiha from Naruto, slim teenage boy with short spiky black hair "
            "and dark eyes, orange goggles above a Konoha forehead protector, orange-and-blue "
            "high-collared jacket, blue trousers and shinobi sandals; preserve the recognizable "
            "official character design and silhouette"
        ),
        rationale="Obito is a canonical Naruto character and the context selects his youth era.",
        look_variant="young Obito during the Kannabi Bridge mission",
        fixed_features=(
            "slim teenage male build",
            "short spiky black hair and dark eyes",
            "orange goggles and Konoha forehead protector",
        ),
        canonical_outfit=(
            "orange-and-blue high-collared jacket, blue trousers and shinobi sandals"
        ),
        asymmetric_traits=(),
    )


def _story_bible() -> dict:
    return {
        "source_work": "火影忍者",
        "worldview": "神无毗桥任务期间的忍界战争，宇智波带土仍是少年。",
        "style_rules": "忠于火影忍者少年时期的官方动画设计。",
        "characters": [
            {
                "name": "宇智波带土",
                "gender": "male",
                "visual_profile": _obito_gallery_profile(),
            }
        ],
    }


def test_gallery_assets_and_taxonomy_contract_remain_complete():
    old = load_library_portraits(
        ASSET_ROOT / "universal_visual_novel_latest/planned_assets/portrait"
    )
    v2 = load_v2_library_portraits(
        ASSET_ROOT / "universal_visual_novel_v2_working"
    )

    assert len(old) == 94
    assert len(v2) == 61


def test_canonical_identity_is_separate_from_gallery_profile_and_fingerprint():
    original_profile = _obito_gallery_profile()
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=_obito_anchor(),
    ):
        enriched = source_visual_profile_service.apply_detected_character_identities(
            _story_bible()
        )

    char = enriched["characters"][0]
    assert char["visual_profile"] == original_profile
    assert char["visual_fingerprint"] == fingerprint(original_profile)
    assert char["visual_fingerprint"].startswith("cv1:")
    assert char["canonical_identity_fingerprint"].startswith("ci1:")
    assert char["canonical_identity"]["canonical_name"] == "Obito Uchiha"
    assert char["canonical_identity"]["look_variant"] == (
        "young Obito during the Kannabi Bridge mission"
    )
    assert "injury" not in char["canonical_identity"]
    assert "pose" not in char["canonical_identity"]
    assert "emotion" not in char["canonical_identity"]


def test_library_match_uses_only_generic_profile():
    portraits = [
        *load_library_portraits(
            ASSET_ROOT / "universal_visual_novel_latest/planned_assets/portrait"
        ),
        *load_v2_library_portraits(
            ASSET_ROOT / "universal_visual_novel_v2_working"
        ),
    ]
    profile = _obito_gallery_profile()
    before = match_library(profile, portraits, limit=5)

    character_with_unrelated_identity = {
        "visual_profile": profile,
        "canonical_identity": {
            "franchise_name": "A franchise value the library does not know",
            "canonical_name": "A source-only character name",
        },
    }
    after = match_library(character_with_unrelated_identity["visual_profile"], portraits, limit=5)

    assert [(item["base_key"], item["score"]) for item in after] == [
        (item["base_key"], item["score"]) for item in before
    ]


def test_portrait_generation_prefers_persisted_identity_without_second_recognition():
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=_obito_anchor(),
    ):
        enriched = source_visual_profile_service.apply_detected_character_identities(
            _story_bible()
        )

    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        side_effect=AssertionError("persisted canonical_identity must prevent a second call"),
    ):
        prompts = prompt_builder_service.build_portrait_prompts(
            enriched,
            project_id=9024,
            variations=[
                {"emotion": "neutral", "outfit": "default", "pose": "standing"},
                {"emotion": "sad", "outfit": "damaged", "pose": "lying"},
            ],
        )

    assert len(prompts) == 2
    assert all("canonical young Obito Uchiha" in item["appearance_prompt"] for item in prompts)
    assert all("Konoha forehead protector" in item["appearance_prompt"] for item in prompts)
    assert all("sportswear" not in item["appearance_prompt"] for item in prompts)
    assert all(item["canonical_identity"] for item in prompts)
    assert len({item["canonical_identity_fingerprint"] for item in prompts}) == 1
    assert len({item["visual_fingerprint"] for item in prompts}) == 1
    assert prompts[0]["character_visual_profile"] == prompts[1]["character_visual_profile"]


def test_recognition_failure_falls_back_to_visual_profile():
    story = _story_bible()
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=None,
    ):
        enriched = source_visual_profile_service.apply_detected_character_identities(story)
        prompts = prompt_builder_service.build_portrait_prompts(
            enriched,
            project_id=9025,
            variations=[{"emotion": "neutral", "outfit": "default", "pose": "standing"}],
        )

    char = enriched["characters"][0]
    assert "canonical_identity" not in char
    assert prompts[0]["canonical_identity"] is None
    assert "student" in prompts[0]["appearance_prompt"]
    assert prompts[0]["visual_fingerprint"].startswith("cv1:")


def test_canonical_rewriter_identity_excludes_generic_gallery_profile():
    with patch.object(
        canonical_character_recognition_service,
        "recognize_sync",
        return_value=_obito_anchor(),
    ):
        enriched = source_visual_profile_service.apply_detected_character_identities(
            _story_bible()
        )
    prompt = prompt_builder_service.build_portrait_prompts(
        enriched,
        project_id=9026,
        variations=[{"emotion": "neutral", "outfit": "default", "pose": "standing"}],
    )[0]

    selected = build_portrait_rewriter_identity(prompt)

    assert selected["subject_opening"] == "A teenage male Obito Uchiha from Naruto"
    assert selected["portrait_identity"]["kind"] == "canonical"
    assert "identity" in selected["portrait_identity"]
    assert "visual_profile" not in selected["portrait_identity"]


def test_generic_rewriter_identity_excludes_canonical_source_fields():
    prompt = {
        "age_group": "teen",
        "gender": "male",
        "character_visual_profile": _obito_gallery_profile(),
    }

    selected = build_portrait_rewriter_identity(prompt)

    assert selected["subject_opening"] == "A teenage male character"
    assert selected["portrait_identity"]["kind"] == "generic"
    assert selected["portrait_identity"]["visual_profile"] == _obito_gallery_profile()
    assert "identity" not in selected["portrait_identity"]
