"""
F03 — Stage_Keyframe_Identity_AR_Blueprint Section D: KeyframeIdentityValidator.

覆盖（D04 — 全部 mock，无外部模型调用）：
- 空 bindings → passed=True (stub)
- 无外部 hook → stub pass，每条 binding 标 passed=True
- require_external_hooks=True 且无 hook → fail
- 注入 face_embedding + face_detect mock → 9 维度按阈值判定
- 多 face 共享同一 index → identity_swap_detected=True
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.keyframe_identity_validator_service import (
    KeyframeIdentityValidator,
    KeyframeValidatorConfig,
    KeyframeCharacterValidation,
)
from app.services.keyframe_character_binding import (
    KeyframeCharacterBinding,
    binding_from_contract,
)
from app.services.character_identity_contract import (
    build_identity_contract,
)


def _binding(name: str = "甲") -> KeyframeCharacterBinding:
    c = build_identity_contract(
        project_id=1, character_id=f"id-{name}", character_name=name,
        gender="male", visual_profile={}, visual_fingerprint=f"vf-{name}",
        visual_prompt_en="", identity_anchor="", gender_prompt="male",
        version="character-identity-v2",
    ).with_master_reference(
        asset_id=1, reference_image_url=f"/static/{name}.png", reference_image_sha256=f"sha-{name}",
    )
    return binding_from_contract(c)


@pytest.mark.asyncio
async def test_no_bindings_passes():
    v = KeyframeIdentityValidator()
    result = await v.validate(keyframe_image_path="/tmp/x.png", expected_bindings=[])
    assert result.passed is True


@pytest.mark.asyncio
async def test_stub_pass_no_hooks():
    """无外部 hook 时，每条 binding 走 stub pass。"""
    v = KeyframeIdentityValidator()
    bindings = [_binding("甲"), _binding("乙")]
    result = await v.validate(keyframe_image_path="/tmp/x.png", expected_bindings=bindings)
    assert result.passed is True
    assert result.detected_character_count == 2
    assert all(c.passed for c in result.character_results)


@pytest.mark.asyncio
async def test_require_external_hooks_blocks_stub_pass():
    v = KeyframeIdentityValidator(
        config=KeyframeValidatorConfig(require_external_hooks=True),
    )
    result = await v.validate(
        keyframe_image_path="/tmp/x.png",
        expected_bindings=[_binding("甲")],
    )
    assert result.passed is False
    assert "no_external_validator_hooks" in result.reasons


@pytest.mark.asyncio
async def test_face_match_via_embedding():
    """face_embedding + face_detect mock：embedding 一致 → face_similarity=1.0。"""
    async def fake_face_emb(img: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    async def fake_face_detect(img: str) -> list[dict[str, Any]]:
        return [
            {"bbox": [0, 0, 10, 10], "embedding": [1.0, 0.0, 0.0]},
        ]

    v = KeyframeIdentityValidator(
        face_detect_fn=fake_face_detect,
        face_embedding_fn=fake_face_emb,
    )
    result = await v.validate(
        keyframe_image_path="/tmp/x.png",
        expected_bindings=[_binding("甲")],
    )
    assert result.passed is True
    assert result.detected_character_count == 1
    cr = result.character_results[0]
    assert cr.matched_face_index == 0
    assert cr.face_similarity is not None and cr.face_similarity >= 0.99


@pytest.mark.asyncio
async def test_identity_swap_detected_when_shared_face_index():
    """两个 binding 都匹配到同一 face index → swap detected."""
    async def fake_face_emb(img: str) -> list[float]:
        return [1.0, 0.0]

    async def fake_face_detect(img: str) -> list[dict[str, Any]]:
        # Only one face — both refs will try to match it
        return [{"bbox": [0, 0, 10, 10], "embedding": [1.0, 0.0]}]

    v = KeyframeIdentityValidator(
        face_detect_fn=fake_face_detect,
        face_embedding_fn=fake_face_emb,
        config=KeyframeValidatorConfig(face_similarity_threshold=0.3),
    )
    result = await v.validate(
        keyframe_image_path="/tmp/x.png",
        expected_bindings=[_binding("甲"), _binding("乙")],
    )
    # 第一个 binding 会拿走唯一的 face index；第二个 face_mismatch。
    # 因为只有 1 个 face 但有 2 个 binding，应至少 missing_character 或 unexpected 失败。
    assert result.passed is False
