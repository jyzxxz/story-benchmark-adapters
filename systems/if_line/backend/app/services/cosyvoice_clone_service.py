"""
CosyVoice 3 秒复刻 - HTTP 调用层。

负责把 ``tts_text + prompt_text + prompt_wav`` 通过 multipart 提交到
CosyVoice 的 FastAPI 服务（``/inference_zero_shot``），拿到 raw PCM 后
重新写成 wav。

注意：CosyVoice 当前 FastAPI 返回的是 raw PCM bytes，不带 wav header，
需要用 ``wave`` 模块手动加头。采样率由环境变量 ``COSYVOICE_SAMPLE_RATE``
控制，默认 22050。
"""
from __future__ import annotations

import os
import wave
from pathlib import Path
from typing import Optional

import httpx

from app.services import tts_service as tts_module
from app.utils import logging as xlog

COSYVOICE_BASE_URL = os.getenv("COSYVOICE_BASE_URL", "http://localhost:50000").rstrip("/")
COSYVOICE_TIMEOUT = float(os.getenv("COSYVOICE_TIMEOUT", "180"))
COSYVOICE_SAMPLE_RATE = int(os.getenv("COSYVOICE_SAMPLE_RATE", "22050"))
COSYVOICE_AUDIO_CHANNELS = int(os.getenv("COSYVOICE_AUDIO_CHANNELS", "1"))
COSYVOICE_AUDIO_SAMPLE_WIDTH = int(os.getenv("COSYVOICE_AUDIO_SAMPLE_WIDTH", "2"))  # 16bit
COSYVOICE_INFERENCE_PATH = os.getenv("COSYVOICE_INFERENCE_PATH", "/inference_zero_shot")


class CosyVoiceCloneError(Exception):
    """业务错误，message 已是中文，可直接抛给前端。"""


class CosyVoiceFeatureDisabledError(CosyVoiceCloneError):
    """Global TTS switch is disabled."""


def _inference_url() -> str:
    return f"{COSYVOICE_BASE_URL}{COSYVOICE_INFERENCE_PATH}"


def _write_pcm_as_wav(pcm_bytes: bytes, output_path: Path) -> None:
    """把 raw PCM bytes 写成 wav 文件。"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(COSYVOICE_AUDIO_CHANNELS)
        wf.setsampwidth(COSYVOICE_AUDIO_SAMPLE_WIDTH)
        wf.setframerate(COSYVOICE_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)


async def synthesize_zero_shot(
    tts_text: str,
    prompt_text: str,
    prompt_wav_path: str,
    output_wav_path: str | Path,
) -> str:
    """调用 CosyVoice ``/inference_zero_shot``，把音频写到 ``output_wav_path``。

    Args:
        tts_text: 要合成的目标文本
        prompt_text: 参考音频对应的原文
        prompt_wav_path: 参考音频文件路径
        output_wav_path: 输出 wav 路径

    Returns:
        output_wav_path 的字符串形式

    Raises:
        CosyVoiceCloneError: 服务未启动 / 返回非 200 / 推理失败 / 空音频
    """
    if not tts_module.TTS_ENABLED:
        raise CosyVoiceFeatureDisabledError("TTS 功能未启用")
    if not tts_text or not tts_text.strip():
        raise CosyVoiceCloneError("tts_text 不能为空")
    if not prompt_text or not prompt_text.strip():
        raise CosyVoiceCloneError("prompt_text 不能为空")
    prompt_wav = Path(prompt_wav_path)
    if not prompt_wav.exists():
        raise CosyVoiceCloneError("参考音频文件不存在")

    output_path = Path(output_wav_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "tts_text": tts_text,
        "prompt_text": prompt_text,
    }

    xlog.info(
        0,
        "[voice-clone] cosyvoice call start tts_chars=%d prompt_chars=%d ref=%s out=%s url=%s",
        len(tts_text), len(prompt_text), prompt_wav.name, output_path.name, _inference_url(),
    )

    try:
        async with httpx.AsyncClient(timeout=COSYVOICE_TIMEOUT) as client:
            with prompt_wav.open("rb") as prompt_file:
                files = {
                    "prompt_wav": (prompt_wav.name, prompt_file, "audio/wav"),
                }
                try:
                    resp = await client.post(_inference_url(), data=data, files=files)
                except httpx.ConnectError as e:
                    raise CosyVoiceCloneError("音色合成服务暂时不可用") from e
                except httpx.TimeoutException as e:
                    raise CosyVoiceCloneError("音色合成请求超时") from e
    except CosyVoiceCloneError:
        raise
    except httpx.HTTPError as e:
        raise CosyVoiceCloneError("音色合成服务暂时不可用") from e

    if resp.status_code != 200:
        xlog.warn(0, "[voice-clone] cosyvoice rejected request status=%d", resp.status_code)
        raise CosyVoiceCloneError("音色合成服务拒绝了请求")

    pcm_bytes = resp.content
    if not pcm_bytes:
        raise CosyVoiceCloneError("CosyVoice 返回了空音频，请检查 prompt_wav / prompt_text 是否一致")

    try:
        _write_pcm_as_wav(pcm_bytes, output_path)
    except Exception as e:
        xlog.error(0, e, "[voice-clone] failed to persist synthesized wav")
        raise CosyVoiceCloneError("合成音频保存失败") from e

    xlog.info(
        0,
        "[voice-clone] cosyvoice call ok pcm_bytes=%d sample_rate=%d out=%s",
        len(pcm_bytes), COSYVOICE_SAMPLE_RATE, output_path.name,
    )
    return str(output_path)
