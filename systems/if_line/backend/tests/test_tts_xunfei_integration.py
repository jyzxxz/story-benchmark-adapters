"""
讯飞超拟人 TTS 集成测试（需真实凭据 + 网络访问讯飞服务）。

默认跳过；本地手动跑：
    pytest -m integration tests/test_tts_xunfei_integration.py -v

需在 backend/.env 配置真实 XUNFEI_TTS_APPID / API_KEY / API_SECRET，
并在讯飞控制台开通对应 vcn 发音人权限。
"""
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")


# 没真实凭据 → 整个文件跳过
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("TTS_ENGINE", "xunfei").lower() != "xunfei"
        or not os.getenv("XUNFEI_TTS_APPID")
        or os.getenv("XUNFEI_TTS_APPID") == "***",
        reason="需启用 xunfei 引擎并配置真实 XUNFEI_TTS_APPID/API_KEY/API_SECRET",
    ),
]


@pytest.mark.asyncio
async def test_real_synthesize_short_text():
    """单条短文本合成 → 返回非空 mp3 字节并落盘。"""
    from app.services.tts_service import tts_service, TTS_OUTPUT_DIR

    result = await tts_service.synthesize(
        text="你好，这是讯飞超拟人 TTS 的集成测试。",
        speaker="x6_lingxiaoxuan_pro",
        emotion_prompt="calm",
    )
    assert result["success"], f"合成失败: {result.get('error')}"
    assert result["audio_url"]
    assert result["audio_url"].endswith(".mp3")

    # 校验文件真实存在且 > 0
    filename = result["audio_url"].rsplit("/", 1)[-1]
    file_path = Path(TTS_OUTPUT_DIR) / filename
    assert file_path.exists(), f"音频文件未落盘: {file_path}"
    assert file_path.stat().st_size > 0, "音频文件大小为 0"


@pytest.mark.asyncio
async def test_real_synthesize_caches_second_call():
    """同参数第二次调用 → cached=True。

    用独立文本避免与其他 integration 测试共享缓存污染。
    """
    from app.services.tts_service import tts_service

    text = f"缓存测试：第二次调用应命中缓存（{uuid4().hex}）。"
    r1 = await tts_service.synthesize(text=text, speaker="x6_lingxiaoxuan_pro", emotion_prompt="calm")
    r2 = await tts_service.synthesize(text=text, speaker="x6_lingxiaoxuan_pro", emotion_prompt="calm")

    assert r1["success"] and r2["success"]
    assert r1["cached"] is False
    assert r2["cached"] is True
    assert r1["audio_url"] == r2["audio_url"]


@pytest.mark.asyncio
async def test_real_synthesize_with_vcn_from_profile():
    """通过 match_voice_profile 走完整链路（不指定 speaker）。

    male + young + '冷静青年' 关键词命中 male_calm_young profile，
    其 vcn 为 x6_lingfeiyi_pro（聆飞逸）。
    """
    from app.services.tts_service import tts_service

    result = await tts_service.synthesize(
        text="他冷冷地看着对方，眼神锐利如刀。",
        gender="male",
        age="young",
        emotion="calm",
        character_voice="冷静青年",
    )
    assert result["success"], f"合成失败: {result.get('error')}"
    assert result["speaker"] == "x6_lingfeiyi_pro"
