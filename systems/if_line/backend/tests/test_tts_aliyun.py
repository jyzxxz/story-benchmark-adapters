"""
阿里云百炼 TTS 单元测试（不依赖真实凭据和网络）。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.tts_service import TTSService


def test_aliyun_options_select_cosyvoice_models():
    svc = TTSService()

    v3 = svc._build_aliyun_options("longshuo_v3", {"speed": 50, "pitch": 50, "volume": 50})
    assert v3["provider"] == "cosyvoice"
    assert v3["model"] == "cosyvoice-v3-flash"
    assert v3["audio_format"] == "mp3"
    assert v3["sample_rate"] == 24000

    v2 = svc._build_aliyun_options("longyingtian", {"speed": 50, "pitch": 52, "volume": 50})
    assert v2["provider"] == "cosyvoice"
    assert v2["model"] == "cosyvoice-v2"


def test_aliyun_options_select_qwen_for_chelsie():
    svc = TTSService()

    options = svc._build_aliyun_options(
        "Chelsie",
        {"speed": 52, "pitch": 54, "volume": 50, "language_type": "Chinese"},
    )
    assert options["provider"] == "qwen"
    assert options["model"] == "qwen3-tts-flash"
    assert options["audio_format"] == "wav"
    assert options["sample_rate"] is None
    assert options["language_type"] == "Chinese"


def test_cache_key_separates_engine_model_and_format():
    svc = TTSService()
    base = ("你好", "林夜", "冷静", "longshuo_v3", "calm")

    k1 = svc._get_cache_key(
        *base,
        speed=50,
        pitch=50,
        volume=50,
        engine="aliyun",
        model="cosyvoice-v3-flash",
        audio_format="mp3",
        sample_rate=24000,
    )
    k2 = svc._get_cache_key(
        *base,
        speed=50,
        pitch=50,
        volume=50,
        engine="aliyun",
        model="cosyvoice-v2",
        audio_format="mp3",
        sample_rate=24000,
    )
    k3 = svc._get_cache_key(
        *base,
        speed=50,
        pitch=50,
        volume=50,
        engine="aliyun",
        model="cosyvoice-v3-flash",
        audio_format="wav",
        sample_rate=24000,
    )
    assert k1 != k2
    assert k1 != k3
