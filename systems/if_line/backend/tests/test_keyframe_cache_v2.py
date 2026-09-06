"""
F05 — Stage_Keyframe_Identity_AR_Blueprint Section E03/E04: keyframe cache v2.

覆盖：
- _compute_keyframe_cache_key_v2 对相同输入确定性输出
- 改 prompt / character_id / reference_sha → key 变化
- _is_keyframe_stale 检测 7 类 stale 原因
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

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


def _binding(name: str = "甲", sha: str = "sha-x") -> KeyframeCharacterBinding:
    c = build_identity_contract(
        project_id=1, character_id=f"id-{name}", character_name=name,
        gender="male", visual_profile={}, visual_fingerprint=f"vf-{name}",
        visual_prompt_en="", identity_anchor="", gender_prompt="male",
        version="character-identity-v2",
    ).with_master_reference(
        asset_id=1, reference_image_url=f"/static/{name}.png", reference_image_sha256=sha,
    )
    return binding_from_contract(c)


def _prompt_data(event: str = "evt", final_prompt: str = "p1", bindings=None) -> dict:
    return {
        "event_name": event,
        "final_prompt": final_prompt,
        "scene_description": "scene",
        "action": "挥剑",
        "emotion": "intense",
        "style_fingerprint": "sf-v1",
        "seed": 12345,
        "character_bindings": bindings or [_binding()],
    }


def test_cache_key_v2_deterministic():
    svc = AssetManagementService.__new__(AssetManagementService)
    args = dict(
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    k1 = svc._compute_keyframe_cache_key_v2(_prompt_data(), **args)
    k2 = svc._compute_keyframe_cache_key_v2(_prompt_data(), **args)
    assert k1 == k2
    assert isinstance(k1, str) and len(k1) == 64


def test_cache_key_v2_changes_on_prompt_change():
    svc = AssetManagementService.__new__(AssetManagementService)
    args = dict(
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    k1 = svc._compute_keyframe_cache_key_v2(_prompt_data(final_prompt="p1"), **args)
    k2 = svc._compute_keyframe_cache_key_v2(_prompt_data(final_prompt="p2"), **args)
    assert k1 != k2


def test_cache_key_v2_changes_on_reference_sha():
    svc = AssetManagementService.__new__(AssetManagementService)
    args = dict(
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    b1 = _binding("甲", sha="sha-1")
    b2 = _binding("甲", sha="sha-2")
    k1 = svc._compute_keyframe_cache_key_v2(_prompt_data(bindings=[b1]), **args)
    k2 = svc._compute_keyframe_cache_key_v2(_prompt_data(bindings=[b2]), **args)
    assert k1 != k2


def test_cache_key_v2_changes_on_clothing_state():
    svc = AssetManagementService.__new__(AssetManagementService)
    args = dict(
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    b1 = _binding("甲")
    b2 = KeyframeCharacterBinding(**{
        **_binding("甲").__dict__,
        "requested_outfit": "破损甲胄",
    })
    k1 = svc._compute_keyframe_cache_key_v2(_prompt_data(bindings=[b1]), **args)
    k2 = svc._compute_keyframe_cache_key_v2(_prompt_data(bindings=[b2]), **args)
    assert k1 != k2


def _fake_asset(generation_params: dict) -> MagicMock:
    asset = MagicMock()
    asset.generation_params = generation_params
    return asset


def test_stale_cache_key_mismatch():
    svc = AssetManagementService.__new__(AssetManagementService)
    asset = _fake_asset({"cache_key_v2": "old-key"})
    reason = svc._is_keyframe_stale(
        asset, _prompt_data(),
        expected_cache_key="new-key",
        expected_style_fingerprint="sf-v1",
        expected_contract_version="character-identity-v2",
        expected_model="qwen-image-2",
        expected_validator_version="kf-validator-v1",
    )
    assert reason == "cache_key_v2_mismatch"


def test_stale_style_fingerprint():
    svc = AssetManagementService.__new__(AssetManagementService)
    asset = _fake_asset({
        "cache_key_v2": None,
        "style_fingerprint": "old-sf",
    })
    reason = svc._is_keyframe_stale(
        asset, _prompt_data(),
        expected_cache_key=None,
        expected_style_fingerprint="new-sf",
        expected_contract_version="character-identity-v2",
        expected_model="",
        expected_validator_version="kf-validator-v1",
    )
    assert reason == "style_fingerprint_changed"


def test_stale_contract_version():
    svc = AssetManagementService.__new__(AssetManagementService)
    asset = _fake_asset({
        "cache_key_v2": None,
        "style_fingerprint": "sf-v1",
        "identity_contract_version": "character-identity-v1",
    })
    reason = svc._is_keyframe_stale(
        asset, _prompt_data(),
        expected_cache_key=None,
        expected_style_fingerprint="sf-v1",
        expected_contract_version="character-identity-v2",
        expected_model="",
        expected_validator_version="kf-validator-v1",
    )
    assert reason == "identity_contract_version_bumped"


def test_stale_master_portrait_regenerated():
    """存储的 binding sha 与 live binding sha 不一致 → master_portrait_regenerated."""
    svc = AssetManagementService.__new__(AssetManagementService)
    live_binding = _binding("甲", sha="new-sha")
    pd = _prompt_data(bindings=[live_binding])
    asset = _fake_asset({
        "cache_key_v2": None,
        "style_fingerprint": "sf-v1",
        "identity_contract_version": "character-identity-v2",
        "model": "",
        "identity_validator_version": "kf-validator-v1",
        "character_bindings": [
            {"character_id": "id-甲", "reference_image_sha256": "old-sha"},
        ],
    })
    reason = svc._is_keyframe_stale(
        asset, pd,
        expected_cache_key=None,
        expected_style_fingerprint="sf-v1",
        expected_contract_version="character-identity-v2",
        expected_model="",
        expected_validator_version="kf-validator-v1",
    )
    assert reason == "master_portrait_regenerated"


def test_not_stale_returns_none():
    svc = AssetManagementService.__new__(AssetManagementService)
    live = _binding("甲", sha="sha-x")
    pd = _prompt_data(bindings=[live])
    asset = _fake_asset({
        "cache_key_v2": None,
        "style_fingerprint": "sf-v1",
        "identity_contract_version": "character-identity-v2",
        "model": "",
        "identity_validator_version": "kf-validator-v1",
        "character_bindings": [
            {"character_id": "id-甲", "reference_image_sha256": "sha-x"},
        ],
    })
    reason = svc._is_keyframe_stale(
        asset, pd,
        expected_cache_key=None,
        expected_style_fingerprint="sf-v1",
        expected_contract_version="character-identity-v2",
        expected_model="",
        expected_validator_version="kf-validator-v1",
    )
    assert reason is None
