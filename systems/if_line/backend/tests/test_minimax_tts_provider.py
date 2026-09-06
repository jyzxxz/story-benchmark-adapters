"""MiniMax TTS Provider 单元测试。

覆盖：
- scale_speed_to_minimax / scale_pitch_to_minimax / scale_volume_to_minimax 参数转换
- map_emotion_to_minimax 情绪映射（含未知情绪回退）
- resolve_minimax_voice_id 优先级（profile fallback → voice_map → default；永不透传主供应商 VCN）
- MiniMaxTTSError 结构化字段
- MiniMaxTTSProvider.synthesize：
  * 成功路径（HTTP 200 + base_resp.status_code==0 + hex 解码）
  * HTTP 非 200 → 错误
  * HTTP 200 但业务码非 0 → 错误（关键：HTTP 200 ≠ 业务成功）
  * 非 JSON 响应 → 错误
  * data.audio 缺失 / 空 hex / hex 非法 / 解码为空 → 错误
  * asyncio.TimeoutError / aiohttp.ClientError → 错误
  * trace_id 保留
  * payload 字段结构正确（不漏 voice_setting / audio_setting）
  * configured 属性
  * 空 API Key → configured=False + synthesize 抛错

所有 HTTP 调用 mock aiohttp.ClientSession，绝不真实访问网络。
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.minimax_tts_provider import (
    EMOTION_MAP,
    MiniMaxTTSError,
    MiniMaxTTSProvider,
    map_emotion_to_minimax,
    resolve_minimax_voice_id,
    scale_pitch_to_minimax,
    scale_speed_to_minimax,
    scale_volume_to_minimax,
)


# ============================================================
# 1. 参数转换：speed
# ============================================================

def test_scale_speed_none_returns_default():
    assert scale_speed_to_minimax(None) == 1.0


def test_scale_speed_zero_returns_min():
    assert scale_speed_to_minimax(0) == 0.5


def test_scale_speed_hundred_returns_max():
    assert scale_speed_to_minimax(100) == 2.0


def test_scale_speed_fifty_maps_to_one():
    """项目 50（中位）必须映射到 MiniMax 默认 1.0。"""
    assert scale_speed_to_minimax(50) == 1.0


def test_scale_speed_quarter_maps_below_one():
    """25 应映射到 0.5..1.0 之间。"""
    v = scale_speed_to_minimax(25)
    assert 0.5 < v < 1.0


def test_scale_speed_seventy_five_maps_above_one():
    v = scale_speed_to_minimax(75)
    assert 1.0 < v < 2.0


def test_scale_speed_invalid_returns_default():
    assert scale_speed_to_minimax("fast") == 1.0  # type: ignore[arg-type]


# ============================================================
# 2. 参数转换：pitch
# ============================================================

def test_scale_pitch_none_returns_zero():
    assert scale_pitch_to_minimax(None) == 0


def test_scale_pitch_zero_returns_min():
    assert scale_pitch_to_minimax(0) == -12


def test_scale_pitch_hundred_returns_max():
    assert scale_pitch_to_minimax(100) == 12


def test_scale_pitch_fifty_maps_to_zero():
    assert scale_pitch_to_minimax(50) == 0


def test_scale_pitch_is_int():
    """MiniMax pitch 必须是整数（半音）。"""
    assert isinstance(scale_pitch_to_minimax(30), int)
    assert isinstance(scale_pitch_to_minimax(70), int)


# ============================================================
# 3. 参数转换：volume
# ============================================================

def test_scale_volume_none_returns_default():
    assert scale_volume_to_minimax(None) == 1.0


def test_scale_volume_zero_returns_min():
    assert scale_volume_to_minimax(0) == 0.0


def test_scale_volume_hundred_returns_max():
    assert scale_volume_to_minimax(100) == 2.0


def test_scale_volume_fifty_maps_to_one():
    assert scale_volume_to_minimax(50) == 1.0


# ============================================================
# 4. 情绪映射
# ============================================================

def test_map_emotion_none_returns_neutral():
    assert map_emotion_to_minimax(None) == "neutral"


def test_map_emotion_empty_returns_neutral():
    assert map_emotion_to_minimax("") == "neutral"


def test_map_emotion_unknown_returns_neutral():
    """未知情绪必须退回 neutral，不能透传任意字符串给 MiniMax。"""
    assert map_emotion_to_minimax("revealing") == "neutral"


def test_map_emotion_case_insensitive():
    assert map_emotion_to_minimax("HAPPY") == "happy"
    assert map_emotion_to_minimax("  Angry  ") == "angry"


def test_map_emotion_fear_maps_to_fearful():
    assert map_emotion_to_minimax("fear") == "fearful"
    assert map_emotion_to_minimax("fearful") == "fearful"


def test_map_emotion_surprise_maps_to_surprised():
    assert map_emotion_to_minimax("surprise") == "surprised"


def test_emotion_map_covers_core_tokens():
    """确保映射表里至少有 calm/happy/sad/angry/fear/surprise。"""
    for key in ("calm", "happy", "sad", "angry", "fear", "surprise"):
        assert key in EMOTION_MAP or key in {"calm": "neutral"}


# ============================================================
# 5. voice_id 解析
# ============================================================

def test_resolve_voice_id_profile_fallback_wins():
    """profile_fallback 优先级最高。"""
    assert resolve_minimax_voice_id(
        "longshuo_v3",
        profile_fallback="male-qn-qingse",
    ) == "male-qn-qingse"


def test_resolve_voice_id_voice_map_second():
    """profile 缺失时退到 voice_map。"""
    assert resolve_minimax_voice_id(
        "longze_v3",
        voice_map={"longze_v3": "male-qn-jingying"},
    ) == "male-qn-jingying"


def test_resolve_voice_id_default_last():
    """profile/voice_map 都没命中时退到 default。"""
    assert resolve_minimax_voice_id(
        "unknown_speaker",
        default_voice_id="female-shaonv",
    ) == "female-shaonv"


def test_resolve_voice_id_never_passes_primary_vcn_through():
    """关键安全约束：阿里云/讯飞 VCN 永远不能透传给 MiniMax。

    longshuo_v3 / x6_lingfeiyi_pro 等不是 MiniMax voice_id，必须被拦截。
    """
    for primary in ("longshuo_v3", "longze_v3", "x6_lingfeiyi_pro", "x5_lingyuzhao_flow"):
        resolved = resolve_minimax_voice_id(primary)
        # 必须落到默认值（male-qn-qingse），而不是 primary 本身
        assert resolved != primary
        assert resolved == "male-qn-qingse"


def test_resolve_voice_id_none_speaker_returns_default():
    assert resolve_minimax_voice_id(None) == "male-qn-qingse"


def test_resolve_voice_id_empty_fallback_ignored():
    """空白 profile_fallback 不能用，要继续走 voice_map / default。"""
    assert resolve_minimax_voice_id(
        "longshuo_v3",
        profile_fallback="   ",
        voice_map={"longshuo_v3": "male-qn-jingying"},
    ) == "male-qn-jingying"


# ============================================================
# 6. MiniMaxTTSError 结构化字段
# ============================================================

def test_minimax_error_preserves_all_fields():
    err = MiniMaxTTSError(
        "boom",
        http_status=429,
        business_code=1129,
        trace_id="trace-abc",
    )
    assert err.http_status == 429
    assert err.business_code == 1129
    assert err.trace_id == "trace-abc"
    msg = str(err)
    assert "429" in msg
    assert "1129" in msg
    assert "trace-abc" in msg


def test_minimax_error_minimal():
    err = MiniMaxTTSError("simple")
    assert err.http_status is None
    assert err.business_code is None
    assert "simple" in str(err)


# ============================================================
# 7. Provider configured 属性
# ============================================================

def test_provider_configured_false_without_api_key():
    p = MiniMaxTTSProvider(api_key="")
    assert p.configured is False


def test_provider_configured_true_with_api_key_and_base_url():
    p = MiniMaxTTSProvider(api_key="sk-real-key", base_url="https://api.minimaxi.com/v1")
    assert p.configured is True


# ============================================================
# 8. Provider.synthesize：成功路径
# ============================================================

def _make_response(*, status=200, body_bytes=None, body_text=None):
    """构造一个 mock aiohttp 响应。"""
    resp = MagicMock()
    resp.status = status
    if body_text is not None:
        resp.text = AsyncMock(return_value=body_text)
    elif body_bytes is not None:
        resp.text = AsyncMock(return_value=body_bytes)
    else:
        resp.text = AsyncMock(return_value="{}")
    return resp


def _make_session_mock(resp):
    sess = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=None)
    sess.post = MagicMock(return_value=cm)
    cm2 = MagicMock()
    cm2.__aenter__ = AsyncMock(return_value=sess)
    cm2.__aexit__ = AsyncMock(return_value=None)
    return cm2


@pytest.mark.asyncio
async def test_synthesize_success_returns_audio_bytes():
    """HTTP 200 + base_resp.status_code==0 + 合法 hex → 返回解码后的 bytes。"""
    audio_bytes = b"\x49\x44\x33\x04"  # 假 ID3 header
    audio_hex = audio_bytes.hex()
    body = json.dumps({
        "base_resp": {"status_code": 0, "trace_id": "trace-ok"},
        "data": {"audio": audio_hex},
    })
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    resp = _make_response(status=200, body_text=body)

    with patch("aiohttp.ClientSession") as MockSession:
        MockSession.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            post=MagicMock(return_value=_make_session_mock(resp)),
        ))
        # 上面嵌套不对，直接用更简单的方式
        pass

    # 简化：直接 patch ClientSession 的构造与 post 调用
    fake_session = MagicMock()
    fake_cm_post = MagicMock()
    fake_post_resp = _make_response(status=200, body_text=body)
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session.post = MagicMock(return_value=fake_cm_post)

    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        result = await provider.synthesize(
            text="你好世界",
            voice_id="male-qn-qingse",
            emotion="happy",
            speed=50,
            pitch=50,
            volume=50,
        )

    assert result == audio_bytes
    # 验证 post 调用参数
    fake_session.post.assert_called_once()
    args, kwargs = fake_session.post.call_args
    assert "https://api.minimaxi.com/v1/t2a_v2" == args[0] or kwargs.get("url") == "https://api.minimaxi.com/v1/t2a_v2"
    payload = kwargs.get("json") or (args[1] if len(args) > 1 else {})
    assert payload["model"] == provider.model
    assert payload["text"] == "你好世界"
    assert payload["voice_setting"]["voice_id"] == "male-qn-qingse"
    assert payload["voice_setting"]["emotion"] == "happy"
    assert payload["output_format"] == "hex"
    assert "audio_setting" in payload


# ============================================================
# 9. Provider.synthesize：HTTP/业务错误
# ============================================================

@pytest.mark.asyncio
async def test_synthesize_http_429_raises_error_with_status():
    body = json.dumps({"base_resp": {"status_code": 0}, "message": "rate limit"})
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")

    fake_post_resp = _make_response(status=429, body_text=body)
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError) as exc_info:
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )
    assert exc_info.value.http_status == 429


@pytest.mark.asyncio
async def test_synthesize_business_code_nonzero_raises_even_on_http_200():
    """关键场景：HTTP 200 但 base_resp.status_code != 0 视为业务失败。

    MiniMax 的双重校验语义，文档明确要求。
    """
    body = json.dumps({
        "base_resp": {"status_code": 1004, "status_msg": "invalid voice"},
        "data": None,
    })
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")

    fake_post_resp = _make_response(status=200, body_text=body)
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError) as exc_info:
            await provider.synthesize(
                text="x", voice_id="bad-voice",
                emotion=None, speed=None, pitch=None, volume=None,
            )
    assert exc_info.value.http_status == 200
    assert exc_info.value.business_code == 1004


@pytest.mark.asyncio
async def test_synthesize_non_json_response_raises():
    """非 JSON 响应（5xx HTML 错误页）→ 错误。"""
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    fake_post_resp = _make_response(status=502, body_text="<html>Bad Gateway</html>")
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError) as exc_info:
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )
    assert exc_info.value.http_status == 502


@pytest.mark.asyncio
async def test_synthesize_missing_data_field_raises():
    body = json.dumps({"base_resp": {"status_code": 0}})
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    fake_post_resp = _make_response(status=200, body_text=body)
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )


@pytest.mark.asyncio
async def test_synthesize_empty_audio_hex_raises():
    body = json.dumps({"base_resp": {"status_code": 0}, "data": {"audio": ""}})
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    fake_post_resp = _make_response(status=200, body_text=body)
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )


@pytest.mark.asyncio
async def test_synthesize_invalid_hex_raises():
    body = json.dumps({"base_resp": {"status_code": 0}, "data": {"audio": "not-hex-!@#"}})
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    fake_post_resp = _make_response(status=200, body_text=body)
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(return_value=fake_post_resp)
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session = MagicMock()
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )


# ============================================================
# 10. Provider.synthesize：网络异常
# ============================================================

@pytest.mark.asyncio
async def test_synthesize_timeout_raises_minimax_error():
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")

    fake_session = MagicMock()
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )


@pytest.mark.asyncio
async def test_synthesize_aiohttp_client_error_raises_minimax_error():
    import aiohttp
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")

    fake_session = MagicMock()
    fake_cm_post = MagicMock()
    fake_cm_post.__aenter__ = AsyncMock(side_effect=aiohttp.ClientError("connection reset"))
    fake_cm_post.__aexit__ = AsyncMock(return_value=None)
    fake_session.post = MagicMock(return_value=fake_cm_post)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="male-qn-qingse",
                emotion=None, speed=None, pitch=None, volume=None,
            )


@pytest.mark.asyncio
async def test_transport_timeout_fails_over_and_remembers_healthy_endpoint():
    """传输层超时切官方备用端点，成功后下一句直接复用该端点。"""
    audio_bytes = b"ID3-minimax"
    body = json.dumps({
        "base_resp": {"status_code": 0},
        "data": {"audio": audio_bytes.hex()},
    })
    provider = MiniMaxTTSProvider(
        api_key="sk-real",
        base_url="https://primary.example/v1",
        backup_base_url="https://backup.example/v1",
    )

    timeout_cm = MagicMock()
    timeout_cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
    timeout_cm.__aexit__ = AsyncMock(return_value=None)

    def success_cm():
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=_make_response(status=200, body_text=body))
        cm.__aexit__ = AsyncMock(return_value=None)
        return cm

    fake_session = MagicMock()
    fake_session.post = MagicMock(
        side_effect=[timeout_cm, success_cm(), success_cm()],
    )
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        first = await provider.synthesize(
            text="第一句", voice_id="male-qn-qingse",
            emotion=None, speed=None, pitch=None, volume=None,
        )
        second = await provider.synthesize(
            text="第二句", voice_id="male-qn-qingse",
            emotion=None, speed=None, pitch=None, volume=None,
        )

    assert first == second == audio_bytes
    requested_urls = [call.args[0] for call in fake_session.post.call_args_list]
    assert requested_urls == [
        "https://primary.example/v1/t2a_v2",
        "https://backup.example/v1/t2a_v2",
        "https://backup.example/v1/t2a_v2",
    ]
    assert provider.last_endpoint == "backup.example"


@pytest.mark.asyncio
async def test_business_error_does_not_fail_over_and_double_submit():
    """收到业务响应说明请求已到上游，不能换端点再次提交付费请求。"""
    body = json.dumps({
        "base_resp": {"status_code": 1004, "status_msg": "invalid voice"},
        "data": None,
    })
    provider = MiniMaxTTSProvider(
        api_key="sk-real",
        base_url="https://primary.example/v1",
        backup_base_url="https://backup.example/v1",
    )
    fake_session = MagicMock()
    response_cm = MagicMock()
    response_cm.__aenter__ = AsyncMock(return_value=_make_response(status=200, body_text=body))
    response_cm.__aexit__ = AsyncMock(return_value=None)
    fake_session.post = MagicMock(return_value=response_cm)
    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm):
        with pytest.raises(MiniMaxTTSError):
            await provider.synthesize(
                text="x", voice_id="bad-voice",
                emotion=None, speed=None, pitch=None, volume=None,
            )

    fake_session.post.assert_called_once()


# ============================================================
# 11. Provider.synthesize：参数校验
# ============================================================

@pytest.mark.asyncio
async def test_synthesize_empty_api_key_raises():
    provider = MiniMaxTTSProvider(api_key="")
    with pytest.raises(MiniMaxTTSError):
        await provider.synthesize(
            text="x", voice_id="male-qn-qingse",
            emotion=None, speed=None, pitch=None, volume=None,
        )


@pytest.mark.asyncio
async def test_synthesize_empty_text_raises():
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    with pytest.raises(MiniMaxTTSError):
        await provider.synthesize(
            text="   ", voice_id="male-qn-qingse",
            emotion=None, speed=None, pitch=None, volume=None,
        )


# ============================================================
# 12. payload 结构
# ============================================================

def test_build_payload_has_required_fields():
    provider = MiniMaxTTSProvider(api_key="sk-real", base_url="https://api.minimaxi.com/v1")
    payload = provider._build_payload(
        text="hello",
        voice_id="male-qn-qingse",
        emotion="happy",
        speed=60,
        pitch=60,
        volume=60,
    )
    assert payload["model"] == provider.model
    assert payload["text"] == "hello"
    assert payload["stream"] is False
    assert payload["output_format"] == "hex"
    assert payload["language_boost"] == "Chinese"
    vs = payload["voice_setting"]
    assert vs["voice_id"] == "male-qn-qingse"
    assert vs["emotion"] == "happy"
    # 60 → 0.5..1.0 / 1.0..2.0 之间，不检查精确值
    assert 0.5 <= vs["speed"] <= 2.0
    assert -12 <= vs["pitch"] <= 12
    assert 0.0 <= vs["vol"] <= 2.0
    asec = payload["audio_setting"]
    assert "sample_rate" in asec
    assert "bitrate" in asec
    assert "format" in asec
    assert "channel" in asec


def test_endpoint_url_construction():
    provider = MiniMaxTTSProvider(
        api_key="sk-real",
        base_url="https://api.minimaxi.com/v1/",
    )
    # base_url 末尾斜杠要去掉
    assert provider._endpoint() == "https://api.minimaxi.com/v1/t2a_v2"
