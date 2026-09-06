"""
MiniMax 同步语音合成 Provider。

职责单一：
- 读取 MiniMax 配置（API Key / Base URL / Model / 音频参数 / 默认音色）
- 构造同步 T2A 请求，POST 到 ``{base_url}/t2a_v2``
- 检查 HTTP 状态码 + 业务 ``base_resp.status_code``
- 把 ``data.audio`` 的 hex 解码为 bytes
- 失败时抛出结构化的 ``MiniMaxTTSError``（含 http_status / business_code / trace_id）

不做：
- 音色映射（在 tts_service 里做）
- 缓存（在 tts_service 里做）
- Asset 落库（在 chapter_voice_service / router 里做）

脱敏日志：不输出 API Key / Authorization / 完整 hex / 完整响应正文 / 长文本。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import aiohttp

from app.observability import provider_span

logger = logging.getLogger("minimax_tts_provider")


# ----------------------- 配置 -----------------------

MINIMAX_TTS_API_KEY = os.getenv("MINIMAX_TTS_API_KEY", "")
# MiniMax 中国站文档同时提供通用域名和北京备用域名。传输层异常时自动
# 故障转移，避免单个域名的 DNS/网络抖动让短文本也耗尽整段请求超时。
MINIMAX_TTS_BASE_URL = os.getenv(
    "MINIMAX_TTS_BASE_URL",
    "https://api.minimaxi.com/v1",
).rstrip("/")
MINIMAX_TTS_BACKUP_BASE_URL = os.getenv(
    "MINIMAX_TTS_BACKUP_BASE_URL",
    "https://api-bj.minimaxi.com/v1",
).rstrip("/")
MINIMAX_TTS_MODEL = os.getenv("MINIMAX_TTS_MODEL", "speech-2.8-hd")
MINIMAX_TTS_TIMEOUT = float(os.getenv("MINIMAX_TTS_TIMEOUT", "60"))
MINIMAX_TTS_CONNECT_TIMEOUT = float(os.getenv("MINIMAX_TTS_CONNECT_TIMEOUT", "20"))
MINIMAX_TTS_AUDIO_FORMAT = os.getenv("MINIMAX_TTS_AUDIO_FORMAT", "mp3")
MINIMAX_TTS_SAMPLE_RATE = int(os.getenv("MINIMAX_TTS_SAMPLE_RATE", "32000"))
MINIMAX_TTS_BITRATE = int(os.getenv("MINIMAX_TTS_BITRATE", "128000"))
MINIMAX_TTS_CHANNEL = int(os.getenv("MINIMAX_TTS_CHANNEL", "1"))
MINIMAX_TTS_LANGUAGE_BOOST = os.getenv("MINIMAX_TTS_LANGUAGE_BOOST", "Chinese")
MINIMAX_TTS_DEFAULT_VOICE_ID = os.getenv("MINIMAX_TTS_DEFAULT_VOICE_ID", "male-qn-qingse")

# voice_id 映射 JSON（从主音色 → MiniMax voice_id）。解析失败回退空 dict。
_VOICE_MAP_RAW = os.getenv("MINIMAX_TTS_VOICE_MAP_JSON", "{}")
try:
    MINIMAX_TTS_VOICE_MAP: Dict[str, str] = json.loads(_VOICE_MAP_RAW) if _VOICE_MAP_RAW else {}
    if not isinstance(MINIMAX_TTS_VOICE_MAP, dict):
        logger.warning(
            "minimax_tts MINIMAX_TTS_VOICE_MAP_JSON not a dict, fallback to empty map",
        )
        MINIMAX_TTS_VOICE_MAP = {}
except (json.JSONDecodeError, ValueError) as exc:
    logger.warning(
        "minimax_tts MINIMAX_TTS_VOICE_MAP_JSON parse failed (%s), fallback to empty map",
        exc,
    )
    MINIMAX_TTS_VOICE_MAP = {}


# ----------------------- 异常 -----------------------

class MiniMaxTTSError(Exception):
    """MiniMax 业务错误，携带结构化字段方便上层 fallback 判断。"""

    def __init__(
        self,
        message: str,
        *,
        http_status: Optional[int] = None,
        business_code: Optional[int] = None,
        trace_id: Optional[str] = None,
    ):
        self.http_status = http_status
        self.business_code = business_code
        self.trace_id = trace_id
        parts = []
        if http_status is not None:
            parts.append(f"http={http_status}")
        if business_code is not None:
            parts.append(f"code={business_code}")
        if trace_id:
            parts.append(f"trace={trace_id}")
        prefix = f"[{' '.join(parts)}] " if parts else ""
        super().__init__(f"{prefix}{message}")


# ----------------------- 参数转换 -----------------------
#
# 项目内部用 0-100 的 speed/pitch/volume（与阿里云 / 讯飞对齐）。MiniMax 官方
# 当前文档给出的范围：
#   - speed: [0.5, 2.0]，步进 0.1，默认 1.0
#   - pitch: [-12, 12]，整数，默认 0
#   - vol:   [0, 10]，步进 0.1，默认 1.0（部分文档写作 0-2；保守用 [0, 2]）
# pitch 取值范围 [-12, 12] 在官方 SDK 示例中明确出现（半音为单位）；vol 的
# 0-10 范围来自 voice_setting 默认示例。任何一项无法确定时回退默认值，不
# 猜测危险值。
#
# 转换原则：项目里 50 是「中位/默认」值，必须映射到 MiniMax 默认；0/100
# 映射到两端。

_MINIMAX_SPEED_MIN = 0.5
_MINIMAX_SPEED_MAX = 2.0
_MINIMAX_PITCH_MIN = -12
_MINIMAX_PITCH_MAX = 12
_MINIMAX_VOL_MIN = 0.0
_MINIMAX_VOL_MAX = 2.0  # 保守上限，避免部分文档写 10 时被错传成 10 倍音量


def scale_speed_to_minimax(value: Optional[int]) -> float:
    """项目 0-100 → MiniMax [0.5, 2.0]。50 映射到 1.0。None 返回默认 1.0。"""
    if value is None:
        return 1.0
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 1.0
    if v <= 0:
        return _MINIMAX_SPEED_MIN
    if v >= 100:
        return _MINIMAX_SPEED_MAX
    if v == 50:
        return 1.0
    if v < 50:
        # 0..50 → 0.5..1.0
        return round(_MINIMAX_SPEED_MIN + (v / 50.0) * (1.0 - _MINIMAX_SPEED_MIN), 2)
    # 50..100 → 1.0..2.0
    return round(1.0 + ((v - 50) / 50.0) * (_MINIMAX_SPEED_MAX - 1.0), 2)


def scale_pitch_to_minimax(value: Optional[int]) -> int:
    """项目 0-100 → MiniMax [-12, 12]（半音）。50 映射到 0。None 返回 0。"""
    if value is None:
        return 0
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    if v <= 0:
        return _MINIMAX_PITCH_MIN
    if v >= 100:
        return _MINIMAX_PITCH_MAX
    if v == 50:
        return 0
    if v < 50:
        # 0..50 → -12..0
        return int(round(_MINIMAX_PITCH_MIN + (v / 50.0) * (0 - _MINIMAX_PITCH_MIN)))
    # 50..100 → 0..12
    return int(round(((v - 50) / 50.0) * _MINIMAX_PITCH_MAX))


def scale_volume_to_minimax(value: Optional[int]) -> float:
    """项目 0-100 → MiniMax [0, 2.0]。50 映射到 1.0。None 返回 1.0。"""
    if value is None:
        return 1.0
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 1.0
    if v <= 0:
        return _MINIMAX_VOL_MIN
    if v >= 100:
        return _MINIMAX_VOL_MAX
    if v == 50:
        return 1.0
    if v < 50:
        return round(_MINIMAX_VOL_MIN + (v / 50.0) * (1.0 - _MINIMAX_VOL_MIN), 2)
    return round(1.0 + ((v - 50) / 50.0) * (_MINIMAX_VOL_MAX - 1.0), 2)


# ----------------------- 情绪映射 -----------------------
#
# 项目内部情绪标签（与 TTS/VoiceLine 契约对齐）映射到 MiniMax
# 当前官方接受的 emotion 字符串。任何未知情绪都退回 neutral，禁止把项目
# 内部任意字符串直接传给 MiniMax。

EMOTION_MAP: Dict[str, str] = {
    "calm": "neutral",
    "neutral": "neutral",
    "happy": "happy",
    "excited": "happy",
    "sad": "sad",
    "angry": "angry",
    "fear": "fearful",
    "fearful": "fearful",
    "surprise": "surprised",
    "surprised": "surprised",
}

_MINIMAX_DEFAULT_EMOTION = "neutral"


def map_emotion_to_minimax(value: Optional[str]) -> str:
    """项目情绪 → MiniMax emotion。未知/空 → neutral。"""
    if not value:
        return _MINIMAX_DEFAULT_EMOTION
    return EMOTION_MAP.get(str(value).strip().lower(), _MINIMAX_DEFAULT_EMOTION)


# ----------------------- voice_id 解析 -----------------------

def resolve_minimax_voice_id(
    primary_speaker: Optional[str],
    *,
    profile_fallback: Optional[str] = None,
    voice_map: Optional[Dict[str, str]] = None,
    default_voice_id: Optional[str] = None,
) -> str:
    """根据主供应商音色解析 MiniMax voice_id。

    优先级：
    1. profile 中的 ``fallback_voice_ids.minimax``（由 caller 通过
       ``profile_fallback`` 传入）
    2. ``voice_map``（默认取 ``MINIMAX_TTS_VOICE_MAP``）
    3. ``default_voice_id``（默认取 ``MINIMAX_TTS_DEFAULT_VOICE_ID``）

    永不返回 None：找不到就退到默认。主音色 ID（longshuo_v3 等）即使没有
    显式映射也**不会**被透传给 MiniMax —— 一定走 default。
    """
    if profile_fallback:
        v = profile_fallback.strip()
        if v:
            return v
    vm = voice_map if voice_map is not None else MINIMAX_TTS_VOICE_MAP
    if primary_speaker and isinstance(vm, dict):
        v = vm.get(primary_speaker)
        if v and isinstance(v, str) and v.strip():
            return v.strip()
    return (default_voice_id or MINIMAX_TTS_DEFAULT_VOICE_ID).strip() or "male-qn-qingse"


# ----------------------- Provider -----------------------

class MiniMaxTTSProvider:
    """MiniMax 同步 T2A v2 Provider。

    设计要点：
    - 状态码 / 业务码双校验：HTTP 200 + ``base_resp.status_code == 0`` 才算成功
    - ``data.audio`` 必须是合法 hex，``bytes.fromhex`` 解码后非空
    - 429 / 5xx / 超时 / 网络异常都抛 ``MiniMaxTTSError``，由上层判断 fallback
    - 日志只记录 model / voice_id / 字符数 / 耗时 / trace_id / 状态码 / 简短错误
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        backup_base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[float] = None,
        connect_timeout: Optional[float] = None,
        audio_format: Optional[str] = None,
        sample_rate: Optional[int] = None,
        bitrate: Optional[int] = None,
        channel: Optional[int] = None,
        language_boost: Optional[str] = None,
    ) -> None:
        resolved_api_key = MINIMAX_TTS_API_KEY if api_key is None else api_key
        resolved_base_url = MINIMAX_TTS_BASE_URL if base_url is None else base_url
        if backup_base_url is not None:
            resolved_backup_base_url = backup_base_url
        elif base_url is None:
            resolved_backup_base_url = MINIMAX_TTS_BACKUP_BASE_URL
        else:
            # 显式注入单一 endpoint 的调用方（尤其是测试/私有网关）不应被隐式
            # 转发到公共备用域名；生产默认配置才自动启用官方双端点。
            resolved_backup_base_url = ""
        self.api_key = (resolved_api_key or "").strip()
        self.base_url = (resolved_base_url or "").rstrip("/")
        endpoints = [self.base_url, (resolved_backup_base_url or "").rstrip("/")]
        self.base_urls = tuple(dict.fromkeys(endpoint for endpoint in endpoints if endpoint))
        self._active_base_url = self.base_url
        self.last_endpoint: Optional[str] = None
        self.model = (model or MINIMAX_TTS_MODEL or "speech-2.8-hd").strip()
        self.timeout = float(timeout if timeout is not None else MINIMAX_TTS_TIMEOUT)
        self.connect_timeout = float(
            connect_timeout if connect_timeout is not None else MINIMAX_TTS_CONNECT_TIMEOUT
        )
        self.audio_format = (audio_format or MINIMAX_TTS_AUDIO_FORMAT or "mp3").strip()
        self.sample_rate = int(sample_rate if sample_rate is not None else MINIMAX_TTS_SAMPLE_RATE)
        self.bitrate = int(bitrate if bitrate is not None else MINIMAX_TTS_BITRATE)
        self.channel = int(channel if channel is not None else MINIMAX_TTS_CHANNEL)
        self.language_boost = (language_boost or MINIMAX_TTS_LANGUAGE_BOOST or "Chinese").strip()

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_urls)

    def _endpoint(self, base_url: Optional[str] = None) -> str:
        return f"{(base_url or self._active_base_url).rstrip('/')}/t2a_v2"

    def _candidate_base_urls(self) -> tuple[str, ...]:
        """优先复用最近成功的 endpoint，避免每条台词重复等待故障端点。"""
        if self._active_base_url not in self.base_urls:
            return self.base_urls
        return (
            self._active_base_url,
            *(endpoint for endpoint in self.base_urls if endpoint != self._active_base_url),
        )

    @staticmethod
    def _endpoint_label(base_url: str) -> str:
        """日志只保留主机名，不暴露自定义网关路径或查询参数。"""
        return urlsplit(base_url).hostname or "unknown"

    def _build_payload(
        self,
        *,
        text: str,
        voice_id: str,
        emotion: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        volume: Optional[int],
    ) -> Dict[str, Any]:
        return {
            "model": self.model,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": scale_speed_to_minimax(speed),
                "vol": scale_volume_to_minimax(volume),
                "pitch": scale_pitch_to_minimax(pitch),
                "emotion": map_emotion_to_minimax(emotion),
            },
            "audio_setting": {
                "sample_rate": self.sample_rate,
                "bitrate": self.bitrate,
                "format": self.audio_format,
                "channel": self.channel,
            },
            "language_boost": self.language_boost,
            "subtitle_enable": False,
            "output_format": "hex",
        }

    async def synthesize(
        self,
        *,
        text: str,
        voice_id: str,
        emotion: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        volume: Optional[int],
    ) -> bytes:
        """同步合成并返回音频 bytes。

        任何失败都抛 ``MiniMaxTTSError``；上层 ``tts_service`` 会判断是否进入
        下游 fallback。
        """
        if not self.api_key:
            raise MiniMaxTTSError("MiniMax API Key 未配置")
        if not text or not text.strip():
            raise MiniMaxTTSError("文本不能为空")
        if not voice_id:
            voice_id = MINIMAX_TTS_DEFAULT_VOICE_ID

        payload = self._build_payload(
            text=text,
            voice_id=voice_id,
            emotion=emotion,
            speed=speed,
            pitch=pitch,
            volume=volume,
        )
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(
            total=self.timeout,
            connect=self.connect_timeout,
        )

        logger.info(
            "minimax_tts request start model=%s voice=%s text_chars=%d",
            self.model, voice_id, len(text),
        )

        candidate_base_urls = self._candidate_base_urls()
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for endpoint_index, base_url in enumerate(candidate_base_urls):
                endpoint_label = self._endpoint_label(base_url)
                try:
                    with provider_span(
                        "tts minimax.synthesize",
                        {
                            "gen_ai.system": "minimax",
                            "gen_ai.operation.name": "tts.synthesize",
                            "tts.provider": "minimax",
                            "tts.model": self.model,
                            "tts.voice_id": voice_id,
                            "tts.text_chars": len(text),
                            "server.address": self._endpoint_label(base_url),
                        },
                    ) as span:
                        async with session.post(
                            self._endpoint(base_url),
                            headers=headers,
                            json=payload,
                        ) as resp:
                            status = resp.status
                            if span is not None:
                                span.set_attribute("http.response.status_code", status)
                            body_text = await resp.text()
                except (asyncio.TimeoutError, aiohttp.ClientError) as exc:
                    has_next_endpoint = endpoint_index + 1 < len(candidate_base_urls)
                    if has_next_endpoint:
                        logger.warning(
                            "minimax_tts endpoint unavailable endpoint=%s error=%s; trying backup",
                            endpoint_label,
                            exc.__class__.__name__,
                        )
                        continue
                    if isinstance(exc, asyncio.TimeoutError):
                        raise MiniMaxTTSError(
                            f"MiniMax 请求超时 endpoint={endpoint_label}"
                        ) from exc
                    raise MiniMaxTTSError(
                        f"MiniMax 网络异常 endpoint={endpoint_label}: {exc.__class__.__name__}"
                    ) from exc

                # 非 JSON 响应一律视为错误（可能是 5xx HTML 错误页）。只对传输
                # 失败切 endpoint；收到响应后的 HTTP/业务错误不重复提交付费请求。
                try:
                    data = json.loads(body_text) if body_text else None
                except json.JSONDecodeError as exc:
                    snippet = (body_text or "")[:120]
                    raise MiniMaxTTSError(
                        f"MiniMax 响应不是 JSON: {snippet}",
                        http_status=status,
                    ) from exc

                if not isinstance(data, dict):
                    raise MiniMaxTTSError(
                        "MiniMax 响应体为空或非对象",
                        http_status=status,
                    )

                trace_id = (
                    data.get("base_resp", {}).get("trace_id")
                    or data.get("trace_id")
                    or data.get("request_id")
                ) if isinstance(data.get("base_resp"), dict) else data.get("trace_id")

                # 业务码校验：HTTP 200 也可能业务失败
                base_resp = data.get("base_resp") or {}
                business_code = int(base_resp.get("status_code", -1)) if isinstance(base_resp, dict) else -1

                if status >= 400 or business_code != 0:
                    msg = (
                        base_resp.get("status_msg")
                        or data.get("message")
                        or data.get("msg")
                        or "MiniMax 业务失败"
                    ) if isinstance(base_resp, dict) else (data.get("message") or "MiniMax 业务失败")
                    raise MiniMaxTTSError(
                        str(msg)[:200],
                        http_status=status,
                        business_code=business_code,
                        trace_id=trace_id,
                    )

                audio_block = data.get("data")
                if not isinstance(audio_block, dict):
                    raise MiniMaxTTSError(
                        "MiniMax 响应缺少 data 字段",
                        http_status=status,
                        business_code=business_code,
                        trace_id=trace_id,
                    )
                audio_hex = audio_block.get("audio")
                if not audio_hex:
                    raise MiniMaxTTSError(
                        "MiniMax 返回空音频",
                        http_status=status,
                        business_code=business_code,
                        trace_id=trace_id,
                    )
                try:
                    audio_bytes = bytes.fromhex(audio_hex)
                except (ValueError, TypeError) as exc:
                    raise MiniMaxTTSError(
                        "MiniMax audio hex 解码失败",
                        http_status=status,
                        business_code=business_code,
                        trace_id=trace_id,
                    ) from exc
                if not audio_bytes:
                    raise MiniMaxTTSError(
                        "MiniMax 解码后音频为空",
                        http_status=status,
                        business_code=business_code,
                        trace_id=trace_id,
                    )

                self._active_base_url = base_url
                self.last_endpoint = endpoint_label
                logger.info(
                    "minimax_tts ok model=%s voice=%s bytes=%d endpoint=%s trace=%s",
                    self.model, voice_id, len(audio_bytes), endpoint_label, trace_id or "-",
                )
                return audio_bytes

        raise MiniMaxTTSError("MiniMax 没有可用的请求端点")


# 模块级单例（与 tts_service 风格一致）
minimax_tts_provider = MiniMaxTTSProvider()
