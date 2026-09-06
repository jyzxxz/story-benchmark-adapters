from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from PIL import Image

from app.services.character_age_contract import (
    age_contract_from_profile,
    age_prompt_validation_errors,
    apply_age_contract_to_appearance,
    apply_age_contract_to_character,
    build_age_contract,
    identity_variant_id,
    normalize_age_group,
)
from app.services.character_identity_contract import build_identity_contract
from app.services.image_generation_service import ImageGenerationService
from app.services.prompt_builder_service import prompt_builder_service
from app.services.portrait_age_validator_service import (
    PortraitAgeValidation,
    PortraitAgeValidatorService,
)
from app.services.prompt_rewriter_service import RewrittenPrompt


def test_age_normalization_supports_stages_and_explicit_ages():
    assert normalize_age_group("少年") == "teen"
    assert normalize_age_group("22岁") == "young_adult"
    assert normalize_age_group("middle-aged") == "middle_aged"
    assert normalize_age_group("67 years old") == "senior"


def test_age_variant_changes_visual_fingerprint_but_not_canonical_character():
    base = {
        "name": "宇智波带土",
        "character_id": "8e69aea681f2",
        "gender": "male",
        "visual_profile": {
            "age_group": "teen",
            "face": {"shape": "round"},
            "hair": {"color": "black", "length": "short", "style": "spiky"},
        },
    }
    teen = apply_age_contract_to_character(base, build_age_contract("teen"))
    adult = apply_age_contract_to_character(base, build_age_contract("adult"))

    assert teen["character_id"] == adult["character_id"] == "8e69aea681f2"
    assert teen["visual_fingerprint"] != adult["visual_fingerprint"]
    assert identity_variant_id("8e69aea681f2", "teen") != identity_variant_id(
        "8e69aea681f2", "adult"
    )


def test_age_override_removes_conflicting_static_age_wording():
    contract = build_age_contract("middle_aged")
    appearance = apply_age_contract_to_appearance(
        "Obito, a teenage male with a round face and black spiky hair",
        contract,
    )
    assert "teenage" not in appearance.lower()
    assert "middle-aged" in appearance.lower()
    assert "black spiky hair" in appearance.lower()


def test_identity_contract_uses_resolved_age_contract_as_single_source_of_truth():
    age_contract = build_age_contract("middle_aged").to_dict()
    contract = build_identity_contract(
        project_id=21,
        character_id="8e69aea681f2",
        character_name="宇智波带土",
        gender="male",
        visual_profile={"age_group": "teen"},
        visual_fingerprint="vf-middle-aged",
        visual_prompt_en="middle-aged male",
        identity_anchor="identity anchor",
        gender_prompt="male character",
        version="character-identity-v2",
        age_contract=age_contract,
    )

    assert contract.age_group == "middle_aged"
    assert contract.age_contract["age_group"] == "middle_aged"
    assert contract.identity_variant_id == identity_variant_id(
        "8e69aea681f2", "middle_aged"
    )


def test_identity_contract_keeps_legacy_adult_default_consistent():
    contract = build_identity_contract(
        project_id=1,
        character_id="anonymous",
        character_name="匿名",
        gender="unknown",
        visual_profile={},
        visual_fingerprint="",
        visual_prompt_en="",
        identity_anchor="",
        gender_prompt="",
        version="character-identity-v2",
    )

    assert contract.age_group == "adult"
    assert contract.age_contract["age_group"] == "adult"
    assert contract.identity_variant_id == identity_variant_id("anonymous", "adult")


def test_prompt_age_contract_is_fail_closed():
    contract = build_age_contract("teen")
    assert age_prompt_validation_errors("an adult male character", contract)
    assert not age_prompt_validation_errors(
        "a teenage male with adolescent facial proportions",
        contract,
    )


def test_middle_aged_contract_uses_unambiguous_visible_anime_cues():
    contract = build_age_contract("middle_aged")

    assert "50-59" in contract.age_range_hint
    assert any("crow's feet" in trait for trait in contract.age_visual_traits)
    assert any("gray at the temples" in trait for trait in contract.age_visual_traits)
    assert any("wrinkle-free youthful" in trait for trait in contract.forbidden_age_traits)


def test_portrait_builder_isolates_age_stage_seed_and_fingerprint():
    prompts = prompt_builder_service.build_portrait_prompts(
        {
            "worldview": "忍者世界",
            "characters": [{
                "name": "宇智波带土",
                "gender": "male",
                "appearance": "teenage male with a round face and black spiky hair",
                "visual_profile": {
                    "age_group": "teen",
                    "face": {"shape": "round"},
                    "hair": {"color": "black", "length": "short", "style": "spiky"},
                },
            }],
        },
        project_id=21,
        variations=[
            {"emotion": "neutral", "outfit": "default", "pose": "standing", "age_group": "teen"},
            {"emotion": "neutral", "outfit": "default", "pose": "standing", "age_group": "middle_aged"},
        ],
    )

    assert len({p["character_id"] for p in prompts}) == 1
    assert {p["age_group"] for p in prompts} == {"teen", "middle_aged"}
    assert len({p["identity_variant_id"] for p in prompts}) == 2
    assert len({p["visual_fingerprint"] for p in prompts}) == 2
    assert len({p["seed"] for p in prompts}) == 2


def _png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 32), (128, 128, 128)).save(output, format="PNG")
    return output.getvalue()


@pytest.mark.asyncio
async def test_vlm_age_validator_uses_visible_result(tmp_path, monkeypatch):
    path = tmp_path / "portrait.png"
    path.write_bytes(_png_bytes())
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=(
            '{"observed_age_group":"teen","estimated_age_range":"14-16",'
            '"confidence":0.93,"evidence":["youthful jawline"],"violations":[]}'
        )))]
    )
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    validator = PortraitAgeValidatorService(vlm_client=client)
    monkeypatch.setattr(
        "app.services.portrait_age_validator_service.api_key_available",
        lambda *_args, **_kwargs: True,
    )

    result = await validator.validate(
        image_path=path,
        age_contract=build_age_contract("teen"),
        character_name="宇智波带土",
    )

    assert result.passed is True
    assert result.observed_age_group == "teen"
    assert result.confidence == pytest.approx(0.93)


@pytest.mark.asyncio
async def test_generate_portrait_retries_after_age_mismatch(monkeypatch):
    service = ImageGenerationService()
    first_prompt = (
        "Exactly one isolated character, a teenage male approximately 13-17 years old, "
        "adolescent facial proportions, black spiky hair; full-body composition with head, "
        "hands and feet inside frame; clean cel-shaded anime art, fully transparent background, "
        "no scenery, floor, cast shadow, furniture, decorative frame, text, or watermark."
    )
    repaired_prompt = first_prompt.replace(
        "adolescent facial proportions",
        "strongly visible adolescent facial proportions and a youthful jawline",
    )
    generate_image = AsyncMock(side_effect=[
        {"success": True, "image_url": "/static/assets/portraits/age-1.png", "seed": 1},
        {"success": True, "image_url": "/static/assets/portraits/age-2.png", "seed": 1},
    ])
    monkeypatch.setattr(service, "generate_image", generate_image)

    failed = PortraitAgeValidation(
        passed=False,
        expected_age_group="teen",
        observed_age_group="young_adult",
        confidence=0.89,
        evidence=("mature jawline",),
        violations=("observed_age_group=young_adult; expected=teen",),
    )
    passed = PortraitAgeValidation(
        passed=True,
        expected_age_group="teen",
        observed_age_group="teen",
        estimated_age_range="14-17",
        confidence=0.91,
        evidence=("youthful jawline",),
    )
    validate = AsyncMock(side_effect=[failed, passed])
    monkeypatch.setattr(
        "app.services.portrait_age_validator_service.portrait_age_validator_service.validate",
        validate,
    )
    rewrite = AsyncMock(return_value=RewrittenPrompt(final_prompt=repaired_prompt))
    monkeypatch.setattr(
        "app.services.prompt_rewriter_service.prompt_rewriter_service.rewrite",
        rewrite,
    )

    result = await service.generate_portrait(
        character_id="8e69aea681f2",
        character_name="宇智波带土",
        appearance_prompt="round face, black spiky hair",
        remove_bg=False,
        full_body=True,
        final_prompt=first_prompt,
        final_prompt_source="llm_rewriter",
        age_contract=build_age_contract("teen").to_dict(),
    )

    assert result["success"] is True
    assert generate_image.await_count == 2
    assert validate.await_count == 2
    assert rewrite.await_count == 1
    assert result["prompt"] == repaired_prompt
    assert result["age_validation"]["passed"] is True
    assert len(result["age_validation_attempts"]) == 2
