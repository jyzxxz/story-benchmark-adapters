"""tts_service.synthesize fallback 路由测试。

覆盖 dispatcher 行为（不真实调用供应商）：
- force_engine="minimax" 直连 minimax 路径
- 主供应商成功 → 直接返回（不 fallback）
- 主供应商失败 + allow_fallback=False → 透传主错误
- 主供应商失败 + allow_fallback=True + 错误在 eligible 范围 → 调 minimax
- 主供应商失败 + allow_fallback=True + 错误不在 eligible 范围 → 透传主错误
- 主供应商失败 + fallback 全局关闭 → 透传主错误
- minimax fallback 成功 → result.fallback_used=True / primary_engine / primary_error 填充
- minimax fallback 也失败 → result.error 合并两层错误
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import tts_service as tts_mod
from app.services.tts_service import TTSService


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(tts_mod, "TTS_ENABLED", True)
    return TTSService()


# ============================================================
# force_engine 直连 minimax
# ============================================================


@pytest.mark.asyncio
async def test_disabled_blocks_minimax_direct_and_primary_before_dispatch(svc, monkeypatch):
    monkeypatch.setattr(tts_mod, "TTS_ENABLED", False)

    with patch.object(svc, "_synthesize_with_minimax", new=AsyncMock()) as mock_mm, \
         patch.object(svc, "_synthesize_with_primary", new=AsyncMock()) as mock_primary:
        result = await svc.synthesize(
            text="hi",
            character_name="x",
            force_engine="minimax",
        )

    assert result == {
        "success": False,
        "error": "TTS 功能未启用",
        "error_code": "tts.feature_disabled",
    }
    mock_mm.assert_not_awaited()
    mock_primary.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_engine_minimax_skips_primary(svc, monkeypatch):
    """force_engine='minimax' 直接走 minimax 路径，不调主供应商。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")

    minimax_result = {
        "success": True,
        "audio_url": "/static/tts_cache/mm.mp3",
        "engine": "minimax",
        "model": "speech-2.8-hd",
    }
    with patch.object(svc, "_synthesize_with_minimax", new=AsyncMock(return_value=minimax_result)) as mock_mm, \
         patch.object(svc, "_synthesize_with_primary", new=AsyncMock()) as mock_primary:
        result = await svc.synthesize(
            text="hi", character_name="x",
            force_engine="minimax", allow_fallback=False,
        )

    assert result["success"] is True
    assert result["engine"] == "minimax"
    assert mock_mm.await_count == 1
    assert mock_primary.await_count == 0
    # force_engine + 主引擎非 minimax → fallback_used=True
    args, kwargs = mock_mm.call_args
    assert kwargs.get("fallback_used") is True


@pytest.mark.asyncio
async def test_primary_success_no_fallback(svc, monkeypatch):
    """主供应商成功 → 直接返回，fallback_used=False。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")

    primary_result = {"success": True, "audio_url": "/static/tts_cache/a.mp3"}
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock()) as mock_mm:
        result = await svc.synthesize(text="hi", character_name="x")

    assert result["success"] is True
    assert result["engine"] == "aliyun"
    assert result["fallback_used"] is False
    assert mock_mm.await_count == 0


# ============================================================
# allow_fallback 控制
# ============================================================

@pytest.mark.asyncio
async def test_primary_failure_allow_fallback_false_passthrough(svc, monkeypatch):
    """allow_fallback=False → 透传主错误，不调 minimax。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")

    primary_result = {"success": False, "error": "aliyun 429 too many requests"}
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock()) as mock_mm:
        result = await svc.synthesize(text="hi", character_name="x", allow_fallback=False)

    assert result["success"] is False
    assert "429" in result["error"]
    assert mock_mm.await_count == 0


@pytest.mark.asyncio
async def test_primary_failure_fallback_disabled_passthrough(svc, monkeypatch):
    """fallback 全局开关关闭 → 即使 allow_fallback=True 也不调 minimax。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", False)

    primary_result = {"success": False, "error": "aliyun 429 too many requests"}
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock()) as mock_mm:
        result = await svc.synthesize(text="hi", character_name="x", allow_fallback=True)

    assert result["success"] is False
    assert "429" in result["error"]
    assert mock_mm.await_count == 0


# ============================================================
# fallback 触发 + 成功
# ============================================================

@pytest.mark.asyncio
async def test_primary_eligible_failure_triggers_minimax_success(svc, monkeypatch):
    """主供应商 429 + fallback 开启 + allow_fallback=True → 调 minimax 并成功。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")

    primary_result = {"success": False, "error": "aliyun 429 too many requests"}
    minimax_result = {
        "success": True, "audio_url": "/static/tts_cache/mm.mp3",
        "engine": "minimax",
    }
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock(return_value=minimax_result)) as mock_mm:
        result = await svc.synthesize(text="hi", character_name="x", allow_fallback=True)

    assert result["success"] is True
    assert result["engine"] == "minimax"
    args, kwargs = mock_mm.call_args
    assert kwargs.get("fallback_used") is True
    assert kwargs.get("primary_engine") == "aliyun"
    assert "429" in kwargs.get("primary_error", "")


# ============================================================
# fallback 触发 + 失败
# ============================================================

@pytest.mark.asyncio
async def test_primary_eligible_failure_and_minimax_failure_returns_merged_error(svc, monkeypatch):
    """主供应商 + minimax 都失败 → 合并错误（_synthesize_with_minimax 内部处理合并）。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")

    primary_result = {"success": False, "error": "aliyun 429 too many requests"}
    minimax_result = {
        "success": False,
        "error": "primary=aliyun 429 too many requests; fallback=minimax 500",
    }
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock(return_value=minimax_result)):
        result = await svc.synthesize(text="hi", character_name="x", allow_fallback=True)

    assert result["success"] is False
    assert "primary" in result["error"]
    assert "minimax" in result["error"]


# ============================================================
# 非 eligible 错误不触发 fallback
# ============================================================

@pytest.mark.asyncio
async def test_primary_non_eligible_failure_skips_fallback(svc, monkeypatch):
    """文本超长（非 eligible）→ 即使 fallback 开启也不调 minimax。"""
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "aliyun")
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_FALLBACK_ENGINE", "minimax")

    primary_result = {"success": False, "error": "文本长度超过限制（最大 600 字）"}
    with patch.object(svc, "_synthesize_with_primary", new=AsyncMock(return_value=primary_result)), \
         patch.object(svc, "_synthesize_with_minimax", new=AsyncMock()) as mock_mm:
        result = await svc.synthesize(text="hi", character_name="x", allow_fallback=True)

    assert result["success"] is False
    assert mock_mm.await_count == 0


@pytest.mark.asyncio
async def test_minimax_primary_selects_gender_profile_and_returns_real_voice(
    svc,
    monkeypatch,
    tmp_path,
):
    """MiniMax 主引擎要从 profile 映射选女声，不能全部退到默认男声。"""
    from app.services import minimax_tts_provider as minimax_mod

    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "minimax")
    svc.output_dir = tmp_path
    provider = minimax_mod.minimax_tts_provider
    monkeypatch.setattr(provider, "api_key", "sk-test")
    monkeypatch.setattr(provider, "base_urls", ("https://api-bj.minimaxi.com/v1",))
    synthesize = AsyncMock(return_value=b"ID3-minimax-primary")
    monkeypatch.setattr(provider, "synthesize", synthesize)

    result = await svc.synthesize(
        text="这是女性角色的试听台词。",
        character_name="苏婉",
        character_voice="温柔少女女声",
        gender="female",
        age="young",
        emotion="happy",
    )

    assert result["success"] is True
    assert result["engine"] == "minimax"
    selected_voice = synthesize.await_args.kwargs["voice_id"]
    assert selected_voice == "female-shaonv"
    assert result["speaker"] == selected_voice
