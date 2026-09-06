#!/usr/bin/env python3
"""
TTS 配置测试脚本
用于验证当前 TTS 配置是否正确。

用法:
    python test_tts_config.py
"""
import os
import sys
from pathlib import Path

# 添加 backend 到路径
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from dotenv import load_dotenv
load_dotenv(backend_path / ".env")


def test_config():
    """测试 TTS 配置"""
    print("=" * 50)
    print("TTS 配置测试")
    print("=" * 50)

    # 检查环境变量
    print("\n1. 环境变量检查:")
    engine = os.getenv("TTS_ENGINE", "xunfei")
    common_vars = {
        "TTS_ENABLED": os.getenv("TTS_ENABLED"),
        "TTS_ENGINE": engine,
    }
    if engine == "aliyun":
        required_vars = {
            **common_vars,
            "DASHSCOPE_API_KEY": (
                os.getenv("DASHSCOPE_API_KEY")
                or os.getenv("ALIYUN_DASHSCOPE_API_KEY")
                or os.getenv("ALIYUN_API_KEY")
            ),
            "ALIYUN_TTS_BASE_URL": os.getenv("ALIYUN_TTS_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"),
            "TTS_DEFAULT_SPEAKER": os.getenv("TTS_DEFAULT_SPEAKER"),
        }
    else:
        required_vars = {
            **common_vars,
            "XUNFEI_TTS_APPID": os.getenv("XUNFEI_TTS_APPID"),
            "XUNFEI_TTS_API_KEY": os.getenv("XUNFEI_TTS_API_KEY"),
            "XUNFEI_TTS_API_SECRET": os.getenv("XUNFEI_TTS_API_SECRET"),
            "XUNFEI_TTS_DOMAIN": os.getenv("XUNFEI_TTS_DOMAIN", "cbm01.cn-huabei-1.xf-yun.com"),
            "XUNFEI_TTS_PATH": os.getenv("XUNFEI_TTS_PATH", "/v1/private/medd90fec"),
        }

    all_configured = True
    for key, value in required_vars.items():
        status = "✓" if value else "✗"
        if value:
            # 隐藏敏感信息
            if "SECRET" in key or "KEY" in key or "APPID" in key:
                display_value = value[:6] + "..." if len(value) > 6 else "***"
            else:
                display_value = value
            print(f"  {status} {key}: {display_value}")
        else:
            print(f"  {status} {key}: 未设置")
            all_configured = False

    if not all_configured:
        print("\n❌ 配置不完整，请检查 backend/.env 文件")
        return False

    if engine not in {"aliyun", "xunfei"}:
        print(f"\n⚠️  TTS_ENGINE={engine}，当前后端支持 'aliyun' 或 'xunfei'")

    print("\n2. 测试 TTS 服务加载:")
    try:
        from app.services.tts_service import tts_service, TTS_ENABLED, TTS_ENGINE

        print(f"  ✓ TTS 服务已加载")
        print(f"  ✓ 引擎: {TTS_ENGINE}")
        print(f"  ✓ 状态: {'启用' if TTS_ENABLED else '禁用'}")

        # 测试音色匹配
        print("\n3. 测试音色匹配:")
        test_cases = [
            {"gender": "male", "age": "young", "emotion": "calm"},
            {"gender": "female", "age": "young", "emotion": "happy"},
            {"gender": "male", "age": "middle", "emotion": "angry"},
            {"gender": "male", "age": "老", "emotion": "calm"},
        ]

        for i, case in enumerate(test_cases, 1):
            speaker, emotion = tts_service.match_voice_profile(**case)
            extras = tts_service._lookup_profile_extras(speaker, emotion)
            print(f"  测试 {i}: {case}")
            print(f"    → speaker={speaker}, emotion={emotion}, tte={extras['tte']}, speed={extras['speed']}")

        print("\n✅ 配置测试通过！")
        return True

    except Exception as e:
        print(f"\n❌ 加载失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_synthesis():
    """测试实际语音合成（需真实凭据 + 网络访问 TTS 服务）"""
    print("\n" + "=" * 50)
    print("测试语音合成")
    print("=" * 50)

    try:
        import asyncio
        from app.services.tts_service import tts_service

        async def synthesize():
            result = await tts_service.synthesize(
                text="这是测试语音，用于验证当前 TTS 配置。",
                gender="female",
                age="young",
                emotion="happy"
            )
            return result

        result = asyncio.run(synthesize())

        if result["success"]:
            print(f"\n✅ 合成成功！")
            print(f"  音频URL: {result['audio_url']}")
            print(f"  音色: {result['speaker']}")
            print(f"  缓存: {'是' if result['cached'] else '否'}")
        else:
            print(f"\n❌ 合成失败: {result['error']}")
            print("\n常见原因：")
            print("  - 凭据错误或地域不匹配 → 检查 API Key、Workspace/Endpoint")
            print("  - 音色与模型不匹配 → 检查 tts_voice_profiles.json 中 model/voice")
            print("  - 账户余额或权限不足 → 检查对应云控制台")

    except Exception as e:
        print(f"\n❌ 合成测试异常: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    if test_config():
        test_synthesis()
    else:
        print("\n请先完成配置，然后重新运行测试。")
        print("配置指南: docs/Xunfei_TTS_Setup.md")
