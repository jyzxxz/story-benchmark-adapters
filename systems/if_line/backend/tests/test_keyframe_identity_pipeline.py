"""
F08 — Stage_Keyframe_Identity_AR_Blueprint integration:

测试 _generate_keyframe_with_identity_pipeline 的核心走通路径：
- bindings_with_ref 为空 → 走 text_only_no_refs 路径，调 legacy generate_keyframe
- bindings_with_ref 非空，generate_keyframe_with_references 成功 + validator stub pass
  → 返回 success=True，render_mode 来自 wrapper
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.asset_management_service import AssetManagementService
from app.services.keyframe_character_binding import (
    KeyframeCharacterBinding,
    binding_from_contract,
)
from app.services.character_identity_contract import (
    build_identity_contract,
)
from app.services.keyframe_scene_validator_service import KeyframeSceneValidation


def _binding(name: str = "甲") -> KeyframeCharacterBinding:
    c = build_identity_contract(
        project_id=1, character_id=f"id-{name}", character_name=name,
        gender="male", visual_profile={}, visual_fingerprint=f"vf-{name}",
        visual_prompt_en="", identity_anchor="", gender_prompt="male",
        version="character-identity-v2",
    ).with_master_reference(
        asset_id=1, reference_image_url=f"/static/{name}.png",
        reference_image_sha256=f"sha-{name}",
    )
    return binding_from_contract(c)


def _prompt_with_bindings(bindings) -> dict:
    return {
        "event_name": "evt",
        "scene_description": "scene",
        "final_prompt": "fp",
        "style_fingerprint": "sf-v1",
        "emotion": "intense",
        "action": "挥剑",
        "character_bindings": bindings,
    }


@pytest.mark.asyncio
async def test_no_bindings_text_only_no_refs(monkeypatch):
    """bindings_with_ref 为空 → 走 legacy 路径，schema_version 标 text_only_no_refs。"""
    svc = AssetManagementService.__new__(AssetManagementService)

    from app.services.image_generation_service import image_generation_service
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe",
        AsyncMock(return_value={
            "success": True,
            "image_url": "/static/kf.png",
            "prompt": "fp",
            "generation_params": {"schema_version": "legacy"},
        }),
    )

    result = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data=_prompt_with_bindings([]),
        bible=None, style_fingerprint="sf-v1",
    )
    assert result["success"] is True
    gp = result["generation_params"]
    assert gp["render_mode"] == "text_only_no_refs"
    assert gp["schema_version"] == "keyframe-identity-v2"


@pytest.mark.asyncio
async def test_bindings_with_ref_stub_pass(monkeypatch):
    """有 binding + 成功 reference 生成 + validator stub pass → render_mode=reference."""
    svc = AssetManagementService.__new__(AssetManagementService)

    async def fake_generate_with_refs(**kwargs):
        return {
            "success": True,
            "image_url": "/static/kf1.png",
            "image_path": "/tmp/kf1.png",
            "generation_params": {
                "schema_version": "keyframe-identity-v2",
                "render_mode": "reference",
                "model": "qwen-image-2",
                "seed": kwargs.get("seed", 0),
            },
        }

    from app.services.image_generation_service import image_generation_service
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe_with_references",
        fake_generate_with_refs,
    )

    result = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data=_prompt_with_bindings([_binding()]),
        bible=None, style_fingerprint="sf-v1",
    )
    assert result["success"] is True
    gp = result["generation_params"]
    assert gp["render_mode"] in ("reference", "sprite_composite", "text_only_degraded")
    assert gp["schema_version"] == "keyframe-identity-v2"


@pytest.mark.asyncio
async def test_missing_required_bar_retries_with_scene_contract(monkeypatch):
    svc = AssetManagementService.__new__(AssetManagementService)
    generated_prompts = []

    from app.core import config as config_module
    monkeypatch.setattr(
        config_module,
        "get_settings",
        lambda: MagicMock(
            keyframe_identity_max_retries=1,
            keyframe_allow_text_only_fallback=False,
            character_identity_contract_version="character-identity-v2",
        ),
    )

    async def fake_generate_with_refs(**kwargs):
        generated_prompts.append(kwargs["final_prompt"])
        return {
            "success": True,
            "image_url": "/static/kf-bar.png",
            "image_path": "/tmp/kf-bar.png",
            "generation_params": {"render_mode": "reference"},
        }

    from app.services.image_generation_service import image_generation_service
    from app.services.keyframe_scene_validator_service import KeyframeSceneValidatorService

    monkeypatch.setattr(
        image_generation_service,
        "generate_keyframe_with_references",
        fake_generate_with_refs,
    )
    monkeypatch.setattr(
        KeyframeSceneValidatorService,
        "validate",
        AsyncMock(side_effect=[
            KeyframeSceneValidation(
                passed=False,
                required_elements=("bar_counter",),
                missing_elements=("bar_counter",),
                reasons=("missing_scene_element:bar_counter",),
            ),
            KeyframeSceneValidation(
                passed=True,
                required_elements=("bar_counter",),
            ),
        ]),
    )
    prompt = _prompt_with_bindings([_binding()])
    prompt.update({
        "event_name": "林默独自站在吧台后",
        "scene_description": "林默站在吧台后，陈叔从门口离开",
        "final_prompt": "Lin Mo stands behind bar while Uncle Chen exits.",
    })

    result = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1,
        chapter_index=1,
        prompt_data=prompt,
        bible=None,
        style_fingerprint="sf-v1",
    )

    assert result["success"] is True
    assert len(generated_prompts) == 2
    assert "MANDATORY SCENE GEOMETRY CONTRACT" in generated_prompts[0]
    assert "previous image omitted required scene elements: bar_counter" in generated_prompts[1]
    assert result["generation_params"]["scene_validation"]["passed"] is True


def test_keyframe_paid_retry_defaults_to_zero_and_is_hard_capped():
    from pydantic import ValidationError

    from app.core.config import AppSettings

    assert AppSettings(_env_file=None).keyframe_identity_max_retries == 0
    assert AppSettings(
        _env_file=None,
        keyframe_identity_max_retries=1,
    ).keyframe_identity_max_retries == 1
    with pytest.raises(ValidationError):
        AppSettings(_env_file=None, keyframe_identity_max_retries=2)


@pytest.mark.asyncio
async def test_default_identity_failure_uses_only_one_paid_generation(monkeypatch):
    from app.core import config as config_module
    from app.core.config import AppSettings
    from app.services.image_generation_service import image_generation_service
    from app.services.keyframe_identity_validator_service import (
        KeyframeIdentityValidationResult,
        KeyframeIdentityValidator,
    )

    settings = AppSettings(_env_file=None)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    provider_calls = []

    async def fake_generate_with_refs(**kwargs):
        provider_calls.append(kwargs)
        return {
            "success": True,
            "image_url": "/static/kf-failed-identity.png",
            "image_path": "/tmp/kf-failed-identity.png",
            "generation_params": {"render_mode": "reference"},
        }

    monkeypatch.setattr(
        image_generation_service,
        "generate_keyframe_with_references",
        fake_generate_with_refs,
    )
    monkeypatch.setattr(
        KeyframeIdentityValidator,
        "validate",
        AsyncMock(
            return_value=KeyframeIdentityValidationResult(
                passed=False,
                reasons=["face_similarity_below_threshold"],
            )
        ),
    )

    svc = AssetManagementService.__new__(AssetManagementService)
    result = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1,
        chapter_index=1,
        prompt_data=_prompt_with_bindings([_binding()]),
        bible=None,
        style_fingerprint="sf-v1",
    )

    assert result["success"] is False
    assert len(provider_calls) == 1
    assert result["identity_retry_count"] == 0
    assert result["paid_generation_attempts"] == 1
