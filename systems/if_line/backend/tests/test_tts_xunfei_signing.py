"""
讯飞超拟人 TTS 单元测试（不依赖真实凭据）。

覆盖：
- _build_auth_url 生成 wss URL 格式正确（含 authorization/date/host 三个 query 参数）
- _build_auth_url 签名确定性（同输入 → 同输出）
- _build_request_frame 上行帧结构（header.app_id / parameter.<key>.voice / payload.text.text base64）
- _build_request_frame payload.text.text 解码后等于原文
- _map_xunfei_error 已知 code 命中映射
- _map_xunfei_error 未知 code 走通用文案
- _lookup_profile_extras 命中 profile → 返回 profile 扩展
- _lookup_profile_extras 未命中 → 返回默认值
- _xunfei_ws_run 合并多帧音频分片（mock WS）
- _xunfei_ws_run 处理业务错误码（code != 0 → XunfeiTTSError）
- _xunfei_ws_run 处理 ws 异常关闭
- _xunfei_ws_run 无音频返回 → XunfeiTTSError(-2)
- synthesize 缓存命中 → 不调 WS
- synthesize 业务错误 → 返回 success=False + 映射文案
- synthesize 超时 → 返回 success=False + "超时"
"""
import asyncio
import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import tts_service as tts_mod
from app.services.tts_service import (
    TTSService,
    XunfeiTTSError,
    _XUNFEI_ERROR_MAP,
)


# ----------------------- fixtures -----------------------

@pytest.fixture(autouse=True)
def _xunfei_creds(monkeypatch):
    """给所有测试注入假的讯飞凭据，避免 _call_xunfei_tts_api 早退。"""
    monkeypatch.setattr(tts_mod, "TTS_ENABLED", True)
    monkeypatch.setattr(tts_mod, "TTS_ENGINE", "xunfei")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_APPID", "test_appid")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_API_KEY", "test_api_key")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_API_SECRET", "test_api_secret")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_DOMAIN", "cbm01.cn-huabei-1.xf-yun.com")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_PATH", "/v1/private/medd90fec")
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_PARAM_KEY", "ora12")


@pytest.fixture
def service():
    return TTSService()


# ----------------------- _build_auth_url -----------------------

def test_build_auth_url_format(service):
    url = service._build_auth_url()
    assert url.startswith("wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/medd90fec?")
    assert "authorization=" in url
    assert "date=" in url
    assert "host=" in url


def test_build_auth_url_deterministic(service):
    """同样时间下，签名应确定性。固定 datetime.now 输出，验证签名一致。"""
    fixed_dt = MagicMock(wraps=lambda: None)
    with patch.object(tts_mod, "datetime") as mock_dt:
        # 用真实 datetime 但固定时间
        from datetime import datetime, timezone
        fixed = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)
        mock_dt.now.return_value = fixed
        url1 = service._build_auth_url()
        url2 = service._build_auth_url()
    assert url1 == url2, "same datetime should produce same auth url"


def test_build_auth_url_signature_changes_with_secret(service, monkeypatch):
    """改 APISecret → 签名应不同。"""
    url1 = service._build_auth_url()
    monkeypatch.setattr(tts_mod, "XUNFEI_TTS_API_SECRET", "different_secret")
    url2 = service._build_auth_url()
    assert url1 != url2


# ----------------------- _build_request_frame -----------------------

def test_build_request_frame_structure(service):
    frame = service._build_request_frame("你好", "x6_lingxiaoxuan_pro", {"tte": "calm", "speed": 50, "volume": 50, "pitch": 50})
    assert frame["header"]["app_id"] == "test_appid"
    assert frame["header"]["status"] == 2
    # oral 块必传（仅 x4 系列生效，但结构必须存在）
    assert frame["parameter"]["oral"]["oral_level"] == "mid"
    param = frame["parameter"]["tts"]
    assert param["vcn"] == "x6_lingxiaoxuan_pro"
    assert param["audio"]["encoding"] == "lame"
    assert param["audio"]["sample_rate"] == 24000
    text_payload = frame["payload"]["text"]
    assert text_payload["status"] == 2
    assert text_payload["encoding"] == "utf8"


def test_build_request_frame_text_base64_correct(service):
    original = "你好，这是测试语音。"
    frame = service._build_request_frame(original, "x6_lingxiaoxuan_pro", {"tte": "calm", "speed": 50, "volume": 50, "pitch": 50})
    text_b64 = frame["payload"]["text"]["text"]
    decoded = base64.b64decode(text_b64).decode("utf-8")
    assert decoded == original


# ----------------------- _map_xunfei_error -----------------------

def test_map_xunfei_error_known_codes(service):
    assert "鉴权失败" in service._map_xunfei_error(10043, "raw")
    assert "余额不足" in service._map_xunfei_error(10014, "raw")
    assert "音色不存在" in service._map_xunfei_error(10019, "raw")
    assert "未开通超拟人" in service._map_xunfei_error(10010, "raw")


def test_map_xunfei_error_unknown_code(service):
    msg = service._map_xunfei_error(99999, "weird error")
    assert "99999" in msg
    assert "weird error" in msg


# ----------------------- _lookup_profile_extras -----------------------

def test_lookup_profile_extras_hit(service):
    """voice_profiles 里有一条 speaker=xiaoyan emotion=happy 的 profile（带 tte/speed）。"""
    fake_profiles = [
        {"speaker": "xiaoyan", "emotion_prompt": "happy", "tte": "joyful", "speed": 60, "volume": 55, "pitch": 52},
    ]
    with patch.object(service, "voice_profiles", fake_profiles):
        extras = service._lookup_profile_extras("xiaoyan", "happy")
    assert extras["tte"] == "joyful"
    assert extras["speed"] == 60


def test_lookup_profile_extras_miss_returns_defaults(service):
    with patch.object(service, "voice_profiles", []):
        extras = service._lookup_profile_extras("unknown_vcn", "calm")
    assert extras["tte"] == "calm"
    assert extras["speed"] == tts_mod.XUNFEI_TTS_SPEED
    assert extras["volume"] == tts_mod.XUNFEI_TTS_VOLUME
    assert extras["pitch"] == tts_mod.XUNFEI_TTS_PITCH


# ----------------------- _xunfei_ws_run -----------------------

class _FakeWS:
    """模拟 aiohttp WS，按序 yield 一组 messages。"""

    def __init__(self, messages):
        self.messages = list(messages)
        self.sent = []
        self._idx = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._idx >= len(self.messages):
            raise StopAsyncIteration
        m = self.messages[self._idx]
        self._idx += 1
        return m

    async def send_json(self, frame):
        self.sent.append(frame)

    async def close(self):
        pass


def _text_msg(payload: dict):
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload))


def _make_audio_payload(audio_bytes: bytes, status: int) -> dict:
    return {
        "header": {"code": 0, "message": "success", "sid": "test"},
        "payload": {"audio": {"audio": base64.b64encode(audio_bytes).decode(), "status": status}},
    }


@pytest.mark.asyncio
async def test_xunfei_ws_run_merges_audio_chunks(service):
    """3 帧分片（status 1/1/2）→ 合并后等于原始字节。"""
    chunk1, chunk2, chunk3 = b"\x00\x01", b"\x02\x03\x04", b"\x05"
    fake_ws = _FakeWS([
        _text_msg(_make_audio_payload(chunk1, 1)),
        _text_msg(_make_audio_payload(chunk2, 1)),
        _text_msg(_make_audio_payload(chunk3, 2)),
    ])

    fake_session = MagicMock()
    fake_session.ws_connect = MagicMock(return_value=_FakeWS_ctx(fake_ws))
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(aiohttp, "ClientSession", return_value=fake_session):
        result = await service._xunfei_ws_run("wss://fake", {"any": "frame"})

    assert result == chunk1 + chunk2 + chunk3
    assert fake_ws.sent == [{"any": "frame"}]


class _FakeWS_ctx:
    """async with session.ws_connect(...) as ws 的辅助。"""
    def __init__(self, ws):
        self.ws = ws
    async def __aenter__(self):
        return self.ws
    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_xunfei_ws_run_handles_business_error(service):
    """下行帧 code=10043 → 抛 XunfeiTTSError(code=10043)。"""
    err_msg = {
        "header": {"code": 10043, "message": "auth failed", "sid": "x"},
        "payload": {},
    }
    fake_ws = _FakeWS([_text_msg(err_msg)])
    fake_session = MagicMock()
    fake_session.ws_connect = MagicMock(return_value=_FakeWS_ctx(fake_ws))
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(aiohttp, "ClientSession", return_value=fake_session):
        with pytest.raises(XunfeiTTSError) as exc_info:
            await service._xunfei_ws_run("wss://fake", {})

    assert exc_info.value.code == 10043


@pytest.mark.asyncio
async def test_xunfei_ws_run_handles_unexpected_close(service):
    """WS 异常关闭（CLOSED 类型）→ 抛 XunfeiTTSError。"""
    fake_ws = _FakeWS([SimpleNamespace(type=aiohttp.WSMsgType.CLOSED, data=None)])
    fake_session = MagicMock()
    fake_session.ws_connect = MagicMock(return_value=_FakeWS_ctx(fake_ws))
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(aiohttp, "ClientSession", return_value=fake_session):
        with pytest.raises(XunfeiTTSError):
            await service._xunfei_ws_run("wss://fake", {})


@pytest.mark.asyncio
async def test_xunfei_ws_run_no_audio_raises(service):
    """正常 code=0 但所有帧 audio 都为空 → 抛 XunfeiTTSError(-2)。"""
    empty_payload = {
        "header": {"code": 0, "message": "ok"},
        "payload": {"audio": {"audio": "", "status": 2}},
    }
    fake_ws = _FakeWS([_text_msg(empty_payload)])
    fake_session = MagicMock()
    fake_session.ws_connect = MagicMock(return_value=_FakeWS_ctx(fake_ws))
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=False)

    with patch.object(aiohttp, "ClientSession", return_value=fake_session):
        with pytest.raises(XunfeiTTSError) as exc_info:
            await service._xunfei_ws_run("wss://fake", {})
    assert exc_info.value.code == -2


# ----------------------- synthesize 集成（不调真实 WS）-----------------------

@pytest.mark.asyncio
async def test_synthesize_caches_audio(service, tmp_path, monkeypatch):
    """第一次合成 → 写盘；第二次同参数 → 命中缓存，不调 WS。"""
    monkeypatch.setattr(tts_mod, "TTS_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(tts_mod, "TTS_AUDIO_BASE_URL", "/static/tts_cache")
    service.output_dir = tmp_path
    service.voice_profiles = []

    call_count = 0

    async def fake_call(text, vcn, extras):
        nonlocal call_count
        call_count += 1
        return b"FAKE_MP3_BYTES"

    with patch.object(service, "_call_xunfei_tts_api", side_effect=fake_call):
        r1 = await service.synthesize("你好", text="你好") if False else None
        # 修正调用：synthesize 第一个位置参数就是 text
        r1 = await service.synthesize("你好", speaker="xiaoyan", emotion_prompt="calm")
        r2 = await service.synthesize("你好", speaker="xiaoyan", emotion_prompt="calm")

    assert r1["success"] is True
    assert r1["cached"] is False
    assert r2["cached"] is True
    assert call_count == 1, "second call should hit cache, not WS"


@pytest.mark.asyncio
async def test_synthesize_business_error_returns_mapped_message(service, tmp_path, monkeypatch):
    """_call_xunfei_tts_api 抛 XunfeiTTSError(10043) → synthesize 返回 success=False + 鉴权文案。"""
    monkeypatch.setattr(tts_mod, "TTS_OUTPUT_DIR", str(tmp_path))
    service.output_dir = tmp_path
    service.voice_profiles = []

    async def boom(text, vcn, extras):
        raise XunfeiTTSError(10043, "signature mismatch")

    with patch.object(service, "_call_xunfei_tts_api", side_effect=boom):
        result = await service.synthesize("你好", speaker="xiaoyan", emotion_prompt="calm")

    assert result["success"] is False
    assert "鉴权失败" in result["error"]


@pytest.mark.asyncio
async def test_synthesize_timeout_returns_timeout_message(service, tmp_path, monkeypatch):
    monkeypatch.setattr(tts_mod, "TTS_OUTPUT_DIR", str(tmp_path))
    service.output_dir = tmp_path
    service.voice_profiles = []

    async def slow(text, vcn, extras):
        raise asyncio.TimeoutError()

    with patch.object(service, "_call_xunfei_tts_api", side_effect=slow):
        result = await service.synthesize("你好", speaker="xiaoyan", emotion_prompt="calm")

    assert result["success"] is False
    assert "超时" in result["error"]


# ----------------------- 配置完整性 -----------------------

def test_xunfei_error_map_has_critical_codes():
    """关键错误码都在映射里。"""
    for code in [10005, 10010, 10014, 10019, 10043, 10065]:
        assert code in _XUNFEI_ERROR_MAP
