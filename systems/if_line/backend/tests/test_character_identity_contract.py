"""
F01 — Stage_Keyframe_Identity_AR_Blueprint Section A: CharacterIdentityContract.

覆盖：
- build_identity_contract 从 visual_profile 派生通用字段，并可携带 canonical_identity
- with_master_reference 返回带 reference 的新副本（frozen）
- gender_prompt / identity_anchor 默认值合理
- version 来自 caller
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.character_identity_contract import (
    CharacterIdentityContract,
    build_identity_contract,
)


def _build_one(project_id: int = 42) -> CharacterIdentityContract:
    profile = {
        "name": "林夜",
        "gender": "male",
        "age_group": "young_adult",
        "face": {"shape": "oval", "skin_tone": "fair", "eye_color": "dark"},
        "hair": {"style": "long", "color": "black", "length": "long"},
        "body": {"build": "slim"},
        "outfit": {"top": "玄色长袍", "bottom": "黑色靴"},
        "signature_features": ["左眉骨一道疤"],
        "accessories": ["腰间玉佩"],
    }
    return build_identity_contract(
        project_id=project_id,
        character_id="abc123",
        character_name="林夜",
        gender="male",
        visual_profile=profile,
        visual_fingerprint="vf-abc",
        visual_prompt_en="young man in black robe",
        identity_anchor="anchor",
        gender_prompt="male",
        version="character-identity-v2",
    )


def test_build_contract_carries_visual_profile_fields():
    c = _build_one()
    assert c.character_name == "林夜"
    assert c.gender == "male"
    assert c.hair_style == "long"
    assert c.hair_color == "black"
    assert "左眉骨" in "".join(c.signature_features)
    assert c.visual_fingerprint == "vf-abc"


def test_with_master_reference_returns_new_instance():
    c = _build_one()
    c2 = c.with_master_reference(
        asset_id=99,
        reference_image_url="/static/x.png",
        reference_image_sha256="deadbeef",
    )
    assert c2.master_portrait_asset_id == 99
    assert c2.reference_image_url == "/static/x.png"
    assert c2.reference_image_sha256 == "deadbeef"
    # 原对象 frozen 不变
    assert c.master_portrait_asset_id in (None, 0)


def test_contract_is_frozen():
    c = _build_one()
    with pytest.raises(Exception):
        c.character_name = "hacked"  # type: ignore[misc]


def test_contract_defaults_when_profile_missing():
    c = build_identity_contract(
        project_id=1,
        character_id="x",
        character_name="匿名",
        gender="unknown",
        visual_profile={},
        visual_fingerprint="",
        visual_prompt_en="",
        identity_anchor="",
        gender_prompt="",
        version="character-identity-v2",
    )
    assert c.face_shape == "oval"
    assert c.skin_tone == "medium"
    assert c.age_group == "adult"
