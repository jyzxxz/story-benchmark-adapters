"""
F02 — Stage_Keyframe_Identity_AR_Blueprint Section B: KeyframeCharacterBinding.

覆盖：
- binding_from_contract 把 contract 字段映射到 binding
- pick_primary_bindings 按 frame_role 优先级排序（protagonist > supporting > extra）
- keyframe_identity_seed 对相同输入确定性输出
- to_rewriter_payload 包含 reference_image_index
- has_reference 判定（asset_id + url + sha）
- E05 — requested_emotion / requested_outfit / requested_pose 字段写入
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
from app.services.keyframe_character_binding import (
    KeyframeCharacterBinding,
    binding_from_contract,
    pick_primary_bindings,
    keyframe_identity_seed,
)


def _contract_factory(name: str = "林夜", asset_id: int = 1) -> CharacterIdentityContract:
    c = build_identity_contract(
        project_id=1,
        character_id=f"id-{name}",
        character_name=name,
        gender="male",
        visual_profile={"outfit": {"top": "玄色长袍"}},
        visual_fingerprint=f"vf-{name}",
        visual_prompt_en=f"{name} in black robe",
        identity_anchor=f"anchor-{name}",
        gender_prompt="male",
        version="character-identity-v2",
    )
    return c.with_master_reference(
        asset_id=asset_id,
        reference_image_url=f"/static/p{name}.png",
        reference_image_sha256=f"sha-{name}",
    )


def test_binding_from_contract_carries_reference():
    c = _contract_factory()
    b = binding_from_contract(c)
    assert b.character_id == "id-林夜"
    assert b.character_name == "林夜"
    assert b.reference_asset_id == 1
    assert b.reference_image_url == "/static/p林夜.png"
    assert b.reference_image_sha256 == "sha-林夜"
    assert b.has_reference() is True


def test_binding_without_reference():
    c = build_identity_contract(
        project_id=1, character_id="x", character_name="x", gender="male",
        visual_profile={}, visual_fingerprint="", visual_prompt_en="",
        identity_anchor="", gender_prompt="", version="character-identity-v2",
    )
    b = binding_from_contract(c)
    assert b.has_reference() is False


def test_binding_e05_clothing_state():
    c = _contract_factory()
    b = binding_from_contract(
        c,
        requested_emotion="愤怒",
        requested_outfit="破损甲胄",
        requested_pose="挥剑",
        requested_action="斩落",
        injury_state="左臂渗血",
        held_item="寒霜剑",
    )
    assert b.requested_emotion == "愤怒"
    assert b.requested_outfit == "破损甲胄"
    assert b.requested_pose == "挥剑"
    assert b.requested_action == "斩落"
    assert b.injury_state == "左臂渗血"
    assert b.held_item == "寒霜剑"


def test_pick_primary_bindings_sorts_by_role():
    b_pro = binding_from_contract(_contract_factory("主角"))
    b_supp = binding_from_contract(_contract_factory("配角"))
    b_extra = binding_from_contract(_contract_factory("路人"))
    b_pro = KeyframeCharacterBinding(**{**b_pro.__dict__, "frame_role": "protagonist"})
    b_supp = KeyframeCharacterBinding(**{**b_supp.__dict__, "frame_role": "supporting"})
    b_extra = KeyframeCharacterBinding(**{**b_extra.__dict__, "frame_role": "background_extra"})

    picked = pick_primary_bindings([b_extra, b_pro, b_supp])
    assert picked[0].frame_role == "protagonist"
    assert picked[1].frame_role == "supporting"
    assert picked[2].frame_role == "background_extra"


def test_pick_primary_bindings_caps_at_three():
    bindings = [binding_from_contract(_contract_factory(f"c{i}"), ) for i in range(5)]
    picked = pick_primary_bindings(bindings, max_count=3)
    assert len(picked) == 3


def test_keyframe_identity_seed_deterministic():
    args = dict(
        project_id=1,
        chapter_index=2,
        event_name="朝堂惊变",
        style_fingerprint="sf-1",
        character_visual_fingerprints=["vf-a", "vf-b"],
    )
    s1 = keyframe_identity_seed(**args)
    s2 = keyframe_identity_seed(**args)
    assert s1 == s2
    assert isinstance(s1, int)
    assert s1 >= 0


def test_keyframe_identity_seed_changes_with_vfp_order():
    """不同 visual_fingerprint 列表必须产生不同 seed。"""
    base = dict(project_id=1, chapter_index=2, event_name="evt", style_fingerprint="sf")
    s1 = keyframe_identity_seed(character_visual_fingerprints=["a", "b"], **base)
    s2 = keyframe_identity_seed(character_visual_fingerprints=["a", "c"], **base)
    assert s1 != s2


def test_to_rewriter_payload_includes_reference_index():
    c = _contract_factory()
    b = binding_from_contract(c)
    payload = b.to_rewriter_payload(reference_image_index=2)
    assert payload.get("reference_image_index") == 2
    assert "character_id" in payload
