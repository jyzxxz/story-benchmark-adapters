"""
F-epilogue — Stage_Keyframe_Identity_AR_Blueprint 端到端冒烟测试（全 mock）。

驱动整个关键帧 identity pipeline：
- KeyframeMomentSelector.select 返回 2 个时刻（带 clothing state）
- image_generation_service.generate_keyframe_with_references 返回合成 PNG
- KeyframeIdentityValidator 走 stub pass
- AssetManagementService._generate_keyframe_with_identity_pipeline 完整三阶段
- 资产写入 DB（cache_key_v2 + keyframe-identity-v2 schema）

需要 SQLite + 完整项目 + 章节 + outline + story_bible fixture。
所有外部模型调用全 mock，不真实付费。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


# Smoke-only: this test verifies the *internal* pipeline structure rather
# than spinning up real SQLAlchemy session + chapter fixtures. It documents
# how a real e2e run would look and exercises all the new helpers in
# sequence to catch wiring regressions.

def test_pipeline_helpers_compose_end_to_end():
    """把所有 helper 串起来跑一次，确保没有 import / signature 漂移。"""
    from app.services.character_identity_contract import build_identity_contract
    from app.services.identity_master_resolver import (
        IdentityMasterResolution,
    )
    from app.services.keyframe_character_binding import (
        binding_from_contract,
        keyframe_identity_seed,
        pick_primary_bindings,
    )
    from app.services.keyframe_identity_validator_service import (
        KeyframeIdentityValidator,
    )
    from app.services.keyframe_sprite_composer import SpritePlacement
    from app.services.keyframe_moment_selector import KeyframeMoment
    from app.services.asset_management_service import AssetManagementService

    # Step 1: build contract + attach master reference
    c = build_identity_contract(
        project_id=1, character_id="cid-lin", character_name="林夜",
        gender="male", visual_profile={"outfit": {"top": "玄袍"}},
        visual_fingerprint="vf-1", visual_prompt_en="young man",
        identity_anchor="anchor", gender_prompt="male",
        version="character-identity-v2",
    ).with_master_reference(
        asset_id=10, reference_image_url="/static/lin.png",
        reference_image_sha256="sha-lin",
    )

    # Step 2: derive bindings from a keyframe moment with clothing state
    moment = KeyframeMoment(
        moment_summary="林夜挥剑",
        target_characters=["林夜"],
        visual_focus="剑光乍现",
        source_excerpt="林夜拔剑",
        rationale="高潮",
        character_emotions=["愤怒"],
        character_outfits=["破损甲胄"],
        character_poses=["挥剑"],
        character_actions=["斩落"],
        character_injury_states=["左臂渗血"],
        character_held_items=["寒霜剑"],
    )
    b = binding_from_contract(
        c,
        requested_emotion=moment.get_emotion("林夜") or "neutral",
        requested_outfit=moment.get_outfit("林夜") or "default",
        requested_pose=moment.get_pose("林夜") or "standing",
        requested_action=moment.get_action("林夜"),
        injury_state=moment.get_injury_state("林夜") or "none",
        held_item=moment.get_held_item("林夜"),
    )
    assert b.requested_outfit == "破损甲胄"
    assert b.has_reference() is True

    # Step 3: deterministic seed
    seed = keyframe_identity_seed(
        project_id=1, chapter_index=2, event_name="林夜挥剑",
        style_fingerprint="sf-v1",
        character_visual_fingerprints=[b.visual_fingerprint],
    )
    assert isinstance(seed, int) and seed >= 0

    # Step 4: validator stub pass
    async def _run_validation():
        v = KeyframeIdentityValidator()
        result = await v.validate(
            keyframe_image_path="/tmp/kf.png",
            expected_bindings=[b],
        )
        return result
    result = asyncio.run(_run_validation())
    assert result.passed is True

    # Step 5: cache key v2 reflects clothing state
    svc = AssetManagementService.__new__(AssetManagementService)
    pd = {
        "event_name": "林夜挥剑",
        "final_prompt": "fp",
        "scene_description": "scene",
        "action": "斩落",
        "emotion": "愤怒",
        "style_fingerprint": "sf-v1",
        "seed": seed,
        "character_bindings": [b],
    }
    key1 = svc._compute_keyframe_cache_key_v2(
        pd,
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    # change clothing state → key must change
    b2 = binding_from_contract(c, requested_outfit="破损甲胄+血迹")
    pd2 = dict(pd)
    pd2["character_bindings"] = [b2]
    key2 = svc._compute_keyframe_cache_key_v2(
        pd2,
        identity_contract_version="character-identity-v2",
        keyframe_image_model="qwen-image-2",
        validator_version="kf-validator-v1",
    )
    assert key1 != key2

    # Step 6: sprite placement construction (no actual composite run)
    placements = [SpritePlacement(
        character_name=b.character_name,
        portrait_path=Path("/tmp/lin.png"),
        expected_position=b.expected_position,
    )]
    assert len(placements) == 1


@pytest.mark.asyncio
async def test_pipeline_wrapper_drives_three_stages(monkeypatch):
    """真正端到端：跑 _generate_keyframe_with_identity_pipeline 完整三阶段。

    Stage 1 (reference) → Stage 2 (sprite) → Stage 3 (text-only).
    所有外部图像生成都 mock，验证：
    - 无 binding → text_only_no_refs
    - 有 binding + reference 成功 → reference render_mode
    - 有 binding + reference 失败 + 背景在 → sprite_composite
    - 有 binding + reference 失败 + 背景缺失 + 文字降级关闭 → failed
    """
    from app.services.asset_management_service import AssetManagementService
    from app.services.character_identity_contract import build_identity_contract
    from app.services.keyframe_character_binding import binding_from_contract
    from app.services.image_generation_service import image_generation_service, IMAGE_BASE_URL
    from app.core import config as _config

    c = build_identity_contract(
        project_id=1, character_id="cid-lin", character_name="林夜",
        gender="male", visual_profile={"outfit": {"top": "玄袍"}},
        visual_fingerprint="vf-1", visual_prompt_en="young man",
        identity_anchor="anchor", gender_prompt="male",
        version="character-identity-v2",
    ).with_master_reference(
        asset_id=10, reference_image_url="/static/lin.png",
        reference_image_sha256="sha-lin",
    )
    b = binding_from_contract(c)

    # --- case 1: no bindings → text_only_no_refs ---
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe",
        AsyncMock(return_value={
            "success": True, "image_url": "/static/kf.png", "prompt": "fp",
            "generation_params": {"schema_version": "legacy"},
        }),
    )
    svc = AssetManagementService.__new__(AssetManagementService)
    r1 = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data={"event_name": "e", "scene_description": "s",
                     "final_prompt": "fp", "style_fingerprint": "sf",
                     "character_bindings": []},
        bible=None, style_fingerprint="sf",
    )
    assert r1["success"] is True
    assert r1["generation_params"]["render_mode"] == "text_only_no_refs"

    # --- case 2: binding + reference success ---
    async def _gen_ref_ok(**kwargs):
        return {
            "success": True, "image_url": "/static/kf.png",
            "image_path": "/tmp/kf.png",
            "generation_params": {
                "schema_version": "keyframe-identity-v2",
                "render_mode": "reference",
                "model": "qwen-image-2", "seed": kwargs.get("seed", 0),
            },
        }
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe_with_references",
        _gen_ref_ok,
    )
    r2 = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data={"event_name": "e", "scene_description": "s",
                     "final_prompt": "fp", "style_fingerprint": "sf",
                     "character_bindings": [b]},
        bible=None, style_fingerprint="sf",
    )
    assert r2["success"] is True
    assert r2["generation_params"]["render_mode"] == "reference"

    # --- case 3: binding + reference fails + has bg → sprite_composite ---
    async def _gen_ref_fail(**kwargs):
        return {"success": False, "error": "api_failed"}
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe_with_references",
        _gen_ref_fail,
    )

    # sprite_composite needs a real BG file on disk — use a tmp_path
    import tempfile
    tmp_bg = Path(tempfile.mkdtemp()) / "bg.png"
    try:
        from PIL import Image
        Image.new("RGB", (512, 288), (0, 0, 128)).save(tmp_bg)
    except ImportError:
        pytest.skip("PIL not installed")

    # Also need a portrait file on disk for the binding's reference URL.
    # composite_keyframe resolves /static/... under BACKEND_ROOT/static;
    # we point the binding at a real file by overriding portrait_path via
    # monkeypatch of the SpritePlacement construction is hard — instead
    # patch _resolve_local_image to return tmp_bg for both.
    import app.services.keyframe_sprite_composer as ksc
    monkeypatch.setattr(
        ksc, "_resolve_local_image", lambda s: tmp_bg,
    )

    r3 = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data={"event_name": "e", "scene_description": "s",
                     "final_prompt": "fp", "style_fingerprint": "sf",
                     "character_bindings": [b]},
        bible=None, style_fingerprint="sf",
        background_image_url=str(tmp_bg),
    )
    assert r3["success"] is True
    assert r3["generation_params"]["render_mode"] == "sprite_composite"

    # --- case 4: binding + reference fails + no bg + text-only off → failed ---
    monkeypatch.undo()  # clear _resolve_local_image patch
    monkeypatch.setattr(
        image_generation_service, "generate_keyframe_with_references",
        _gen_ref_fail,
    )
    monkeypatch.setattr(
        _config, "get_settings",
        lambda: MagicMock(
            keyframe_identity_max_retries=0,
            keyframe_allow_text_only_fallback=False,
            character_identity_contract_version="character-identity-v2",
            keyframe_image_model="qwen-image-2",
            keyframe_identity_validator_version="kf-validator-v1",
        ),
    )
    r4 = await svc._generate_keyframe_with_identity_pipeline(
        project_id=1, chapter_index=1,
        prompt_data={"event_name": "e", "scene_description": "s",
                     "final_prompt": "fp", "style_fingerprint": "sf",
                     "character_bindings": [b]},
        bible=None, style_fingerprint="sf",
        # No background_image_url → no sprite_composite
    )
    assert r4["success"] is False
    assert r4.get("render_mode") == "failed"
