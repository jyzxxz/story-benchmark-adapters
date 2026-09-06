"""
F07 — Stage_Keyframe_Identity_AR_Blueprint Section B: identity_master_resolver.

覆盖：
- resolve_identity_master_portrait 在 DB 命中已验收的 master → 返回 Asset
- 不命中 → 返回 None
- generate_or_resolve_identity_master_portraits 注入 generation_runner
  → 不调真实 LLM，可单测
- 不带 generation_runner 时返回 generation_failed（mock 路径）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.identity_master_resolver import (
    IdentityMasterResolution,
    generate_or_resolve_identity_master_portraits,
    resolve_identity_master_portrait,
    _is_identity_master,
    _contract_version_matches,
    _portrait_presentation_contract_matches,
)


def test_is_identity_master_true():
    params = {"is_identity_master": True, "identity_contract_version": "character-identity-v2"}
    assert _is_identity_master(params) is True


def test_is_identity_master_false():
    assert _is_identity_master({}) is False
    assert _is_identity_master({"is_identity_master": False}) is False


def test_contract_version_matches():
    assert _contract_version_matches(
        {"identity_contract_version": "character-identity-v2"},
        "character-identity-v2",
    ) is True
    assert _contract_version_matches(
        {"identity_contract_version": "v1"},
        "character-identity-v2",
    ) is False


def test_portrait_presentation_contract_requires_current_version_and_alpha():
    current = {
        "portrait_generation_contract_version": "portrait-natural-alpha-v3-age",
        "portrait_final_prompt_contract_version": "portrait-llm-authoritative-v3-subject-first",
        "prompt_source": "llm_rewriter",
        "requested_shot": "full_body",
        "portrait_alpha": {"passed": True},
    }
    assert _portrait_presentation_contract_matches(current) is True
    assert _portrait_presentation_contract_matches({}) is False
    assert _portrait_presentation_contract_matches(
        {**current, "portrait_alpha": {"passed": False}}
    ) is False


@pytest.mark.asyncio
async def test_resolve_rejects_legacy_master_and_accepts_current_presentation_contract():
    base_params = {
        "is_identity_master": True,
        "identity_contract_version": "character-identity-v2",
        "visual_fingerprint": "vf",
        "style_fingerprint": "sf",
        "portrait_final_prompt_contract_version": "portrait-llm-authoritative-v3-subject-first",
        "prompt_source": "llm_rewriter",
        "requested_shot": "full_body",
    }
    legacy = SimpleNamespace(
        id=1,
        emotion="neutral",
        outfit="default",
        pose="standing",
        generation_params=base_params,
    )
    current = SimpleNamespace(
        id=2,
        emotion="neutral",
        outfit="default",
        pose="standing",
        generation_params={
            **base_params,
            "portrait_generation_contract_version": "portrait-natural-alpha-v3-age",
            "portrait_alpha": {"passed": True},
        },
    )
    db = MagicMock()
    fake_q = MagicMock()
    fake_q.filter.return_value = fake_q
    fake_q.all.return_value = [legacy, current]
    db.query.return_value = fake_q
    db.execute = MagicMock()

    resolved = await resolve_identity_master_portrait(
        db,
        project_id=1,
        character_id="character",
        visual_fingerprint="vf",
        style_fingerprint="sf",
    )

    assert resolved is current


@pytest.mark.asyncio
async def test_resolve_returns_none_when_db_empty():
    """sync db.query 链返回空 → resolve_identity_master_portrait 返回 None。"""
    db = MagicMock()
    fake_q = MagicMock()
    fake_q.filter.return_value = fake_q
    fake_q.all.return_value = []
    db.query.return_value = fake_q
    # MagicMock 自动响应 hasattr — 显式声明 query 已存在 + execute 为同步
    db.execute = MagicMock()  # 不应被调用

    res = await resolve_identity_master_portrait(
        db, project_id=1, character_id="x",
        visual_fingerprint="vf", style_fingerprint="sf",
    )
    assert res is None


@pytest.mark.asyncio
async def test_generate_or_resolve_invokes_runner_when_missing():
    """没命中已存 master → 调 generation_runner 生成新 portrait。"""
    db = MagicMock()
    fake_q = MagicMock()
    fake_q.filter.return_value = fake_q
    fake_q.all.return_value = []
    db.query.return_value = fake_q
    db.execute = MagicMock()  # 不应被调用
    db.add = MagicMock()
    db.commit = MagicMock()
    db.flush = MagicMock()

    sentinel_asset = MagicMock()
    sentinel_asset.id = 99
    sentinel_asset.image_url = "/static/x.png"
    sentinel_asset.generation_params = {"reference_image_sha256": "sha"}

    runner = AsyncMock(return_value=sentinel_asset)

    chars = [{
        "name": "林夜",
        "gender": "male",
        "visual_profile": {},
        "visual_fingerprint": "vf-1",
        "visual_prompt_en": "young man",
    }]

    result = await generate_or_resolve_identity_master_portraits(
        db, project_id=1, characters=chars,
        style_fingerprint="sf-1",
        generation_runner=runner,
    )
    assert runner.await_count == 1
    assert len(result) == 1
    res: IdentityMasterResolution = list(result.values())[0]
    assert res.asset is sentinel_asset
    assert res.reason == "generated"
    assert res.contract.master_portrait_asset_id == 99
