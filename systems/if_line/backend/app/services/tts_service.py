"""
TTS 服务层。

对外保持 ``TTSService.synthesize`` 一个入口，内部按 ``TTS_ENGINE`` 分流：
- ``minimax``：MiniMax 同步 T2A v2（默认）
- ``aliyun``：阿里云百炼 Model Studio 非实时 TTS HTTP API
- ``xunfei``：科大讯飞超拟人语音合成 WebSocket（保留旧实现）
"""
import os
import json
import hashlib
import asyncio
import aiohttp
import base64
import hmac
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime, timezone
from urllib.parse import urlencode
from dotenv import load_dotenv
from app.core.config import get_settings
from app.observability import provider_span
from app.utils import logging as xlog

load_dotenv()

# TTS 运行开关统一由 AppSettings 解析；不要在 legacy 服务里维护另一套默认值。
_tts_settings = get_settings()
TTS_ENABLED = _tts_settings.tts_enabled
TTS_ENGINE = _tts_settings.tts_engine.strip().lower()
TTS_OUTPUT_DIR = os.getenv("TTS_OUTPUT_DIR", "static/tts_cache")
TTS_AUDIO_BASE_URL = os.getenv("TTS_AUDIO_BASE_URL", "/static/tts_cache")
TTS_MAX_TEXT_LENGTH = int(
    os.getenv(
        "TTS_MAX_TEXT_LENGTH",
        "600" if TTS_ENGINE in {"aliyun", "minimax"} else "200",
    )
)
TTS_AUDIO_FORMAT = os.getenv("TTS_AUDIO_FORMAT", "mp3")

# 阿里云百炼 Model Studio 配置
DASHSCOPE_API_KEY = (
    os.getenv("DASHSCOPE_API_KEY")
    or os.getenv("ALIYUN_DASHSCOPE_API_KEY")
    or os.getenv("ALIYUN_API_KEY")
    or ""
)
ALIYUN_TTS_WORKSPACE_ID = os.getenv("ALIYUN_TTS_WORKSPACE_ID", "")
ALIYUN_TTS_REGION = os.getenv("ALIYUN_TTS_REGION", "cn-beijing")
_ALIYUN_TTS_BASE_URL_ENV = os.getenv("ALIYUN_TTS_BASE_URL", "").rstrip("/")
if _ALIYUN_TTS_BASE_URL_ENV:
    ALIYUN_TTS_BASE_URL = _ALIYUN_TTS_BASE_URL_ENV
elif ALIYUN_TTS_WORKSPACE_ID:
    ALIYUN_TTS_BASE_URL = f"https://{ALIYUN_TTS_WORKSPACE_ID}.{ALIYUN_TTS_REGION}.maas.aliyuncs.com/api/v1"
else:
    # 官方建议使用 Workspace 专属域名；未配置 workspace 时保留兼容域名。
    ALIYUN_TTS_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"

ALIYUN_TTS_COSYVOICE_MODEL = os.getenv("ALIYUN_TTS_COSYVOICE_MODEL", "cosyvoice-v3-flash")
ALIYUN_TTS_QWEN_MODEL = os.getenv("ALIYUN_TTS_QWEN_MODEL", "qwen3-tts-flash")
ALIYUN_TTS_AUDIO_FORMAT = os.getenv("ALIYUN_TTS_AUDIO_FORMAT", TTS_AUDIO_FORMAT)
ALIYUN_TTS_QWEN_AUDIO_FORMAT = os.getenv("ALIYUN_TTS_QWEN_AUDIO_FORMAT", "wav")
ALIYUN_TTS_SAMPLE_RATE = int(os.getenv("ALIYUN_TTS_SAMPLE_RATE", "24000"))
ALIYUN_TTS_TIMEOUT = float(os.getenv("ALIYUN_TTS_TIMEOUT", "60"))
ALIYUN_TTS_CONNECT_TIMEOUT = float(os.getenv("ALIYUN_TTS_CONNECT_TIMEOUT", "10"))
ALIYUN_TTS_LANGUAGE_TYPE = os.getenv("ALIYUN_TTS_LANGUAGE_TYPE", "Chinese")
ALIYUN_TTS_ENABLE_SSML = os.getenv("ALIYUN_TTS_ENABLE_SSML", "false").lower() == "true"

_ALIYUN_QWEN_VOICES = {
    "chelsie",
}

_ALIYUN_COSYVOICE_MODEL_OVERRIDES: Dict[str, str] = {
    "longshuo_v3": "cosyvoice-v3-flash",
    "longze_v3": "cosyvoice-v3-flash",
    "longanlang_v3": "cosyvoice-v3-flash",
    "longshao_v2": "cosyvoice-v2",
    "longyingtian": "cosyvoice-v2",
}


def _resolve_tts_output_dir(path_value: str) -> Path:
    """Resolve TTS cache path consistently under backend/.

    ``TTS_OUTPUT_DIR=static/tts_cache`` used to depend on the process cwd:
    running from repo root wrote ``./static/tts_cache``, while uvicorn from
    ``backend/`` wrote ``backend/static/tts_cache``. The FastAPI static mount
    serves ``backend/static``, so relative paths should resolve from backend root.
    """
    path = Path(path_value)
    if path.is_absolute():
        return path
    backend_root = Path(__file__).resolve().parents[2]
    return backend_root / path

# 讯飞超拟人配置
XUNFEI_TTS_APPID = os.getenv("XUNFEI_TTS_APPID", "")
XUNFEI_TTS_API_KEY = os.getenv("XUNFEI_TTS_API_KEY", "")
XUNFEI_TTS_API_SECRET = os.getenv("XUNFEI_TTS_API_SECRET", "")
XUNFEI_TTS_DOMAIN = os.getenv("XUNFEI_TTS_DOMAIN", "cbm01.cn-huabei-1.xf-yun.com")
XUNFEI_TTS_PATH = os.getenv("XUNFEI_TTS_PATH", "/v1/private/mcd9m97e6")
XUNFEI_TTS_PARAM_KEY = os.getenv("XUNFEI_TTS_PARAM_KEY", "tts")
XUNFEI_TTS_AUE = os.getenv("XUNFEI_TTS_AUE", "lame")
XUNFEI_TTS_AUF = os.getenv("XUNFEI_TTS_AUF", "audio/L16;rate=16000")
XUNFEI_TTS_SPEED = int(os.getenv("XUNFEI_TTS_SPEED", "50"))
XUNFEI_TTS_VOLUME = int(os.getenv("XUNFEI_TTS_VOLUME", "50"))
XUNFEI_TTS_PITCH = int(os.getenv("XUNFEI_TTS_PITCH", "50"))
XUNFEI_TTS_TIMEOUT = float(os.getenv("XUNFEI_TTS_TIMEOUT", "30"))
XUNFEI_TTS_WS_CONNECT_TIMEOUT = float(os.getenv("XUNFEI_TTS_WS_CONNECT_TIMEOUT", "10"))

# 默认音色。MiniMax 作为主引擎时不能沿用阿里云/讯飞的 speaker ID。
_ENGINE_DEFAULT_SPEAKERS = {
    "aliyun": "longshuo_v3",
    "minimax": "male-qn-qingse",
    "xunfei": "x6_lingxiaoxuan_pro",
}
TTS_DEFAULT_SPEAKER = os.getenv(
    "TTS_DEFAULT_SPEAKER",
    _ENGINE_DEFAULT_SPEAKERS.get(TTS_ENGINE, "male-qn-qingse"),
)

# ===== Fallback 配置 =====
# 主供应商（aliyun / xunfei）失败后，按 TTS_FALLBACK_ENGINE 切到备用 TTS。
# 当前仅支持 minimax。enable=false 时所有 fallback 行为关闭，与历史行为一致。
TTS_FALLBACK_ENABLED = _tts_settings.tts_fallback_enabled
TTS_FALLBACK_ENGINE = _tts_settings.tts_fallback_engine.strip().lower()
# 主引擎 == fallback 引擎时不触发 fallback（避免自循环）
_FALLBACK_MINIMAX = "minimax"

# 并发限制
_tts_semaphore = asyncio.Semaphore(2)

# ---------------------------------------------------------------------------
# RPM token bucket —— 全局每分钟请求数节流
# ---------------------------------------------------------------------------
# 背景：MiniMax / 阿里云 / 讯飞 都有账号级 RPM 限制，超过后 MiniMax 返回
# HTTP 200 + base_resp.code=1002 rate limit exceeded(RPM)。瞬时并发 semaphore
# 不控制每分钟总量，因此一整章 ~80 行会瞬间打爆窗口。
#
# 实现：滑动窗口 token bucket。一个 RPM 配额 = ``TTS_RPM_LIMIT`` 个 token，
# 每个请求消耗 1 个，60s 滑动窗口自动回收。请求进入时若 token 不足，
# 等到最早一个 in-flight 请求满 60s 后释放再继续。
TTS_RPM_LIMIT = int(os.getenv("TTS_RPM_LIMIT", "0"))  # 0 = 关闭节流
TTS_RPM_WINDOW_SECONDS = float(os.getenv("TTS_RPM_WINDOW_SECONDS", "60.0"))


class _RPMTokenBucket:
    """滑动窗口 RPM 节流器（单 event loop，无锁）。

    ``_timestamps`` 是 deque[monotonic_time]，每个元素代表一个已消耗 token
    的发放时刻。``acquire()`` 把过期（> window）的时刻从左端 pop 掉，然后
    判断剩余数量：``< limit`` 直接消费 1 个，否则 sleep 到队首时刻 + window。
    """

    __slots__ = ("_limit", "_window", "_timestamps", "_enabled")

    def __init__(self, limit: int, window: float) -> None:
        self._limit = max(0, limit)
        self._window = max(1.0, window)
        self._timestamps: "asyncio.Queue[float]" = asyncio.Queue()
        self._enabled = limit > 0

    async def acquire(self) -> None:
        if not self._enabled:
            return
        import time as _time
        now = _time.monotonic()
        # 清掉窗口外的时间戳
        cutoff = now - self._window
        while not self._timestamps.empty():
            oldest = self._timestamps.get_nowait()
            if oldest > cutoff:
                # 还在窗口内，放回去并停止清理
                self._timestamps.put_nowait(oldest)
                break
        if self._timestamps.qsize() < self._limit:
            self._timestamps.put_nowait(_time.monotonic())
            return
        # 队列已满，等到最早一个时刻过期
        oldest = await self._timestamps.get()
        wait = (oldest + self._window) - _time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._timestamps.put_nowait(_time.monotonic())


_rpm_bucket = _RPMTokenBucket(TTS_RPM_LIMIT, TTS_RPM_WINDOW_SECONDS)

# 讯飞错误码 → 中文文案
_XUNFEI_ERROR_MAP: Dict[int, str] = {
    10005: "讯飞 APPID 无效",
    10006: "讯飞请求参数错误",
    10010: "讯飞引擎拒绝（vcn 未开通超拟人）",
    10014: "讯飞账户余额不足",
    10019: "讯飞音色不存在",
    10043: "讯飞鉴权失败（APISecret 错或服务器时间偏移 >5min）",
    10065: "讯飞 QPS 超限",
    10163: "讯飞请求帧协议错（字段缺失或值非法）",
    11200: "讯飞功能未授权或授权到期（联系商务开通超拟人服务）",
    11201: "讯飞授权会话总量超限或日流控超限（联系商务提升配额）",
}


class XunfeiTTSError(Exception):
    """讯飞 TTS 业务错误，携带 code 用于映射文案。"""

    def __init__(self, code: int, message: str):
        self.code = code
        super().__init__(f"[{code}] {message}")


class AliyunTTSError(Exception):
    """阿里云百炼 TTS 业务错误。"""

    def __init__(self, message: str, status: Optional[int] = None, request_id: Optional[str] = None):
        self.status = status
        self.request_id = request_id
        prefix = f"[{status}] " if status is not None else ""
        suffix = f" request_id={request_id}" if request_id else ""
        super().__init__(f"{prefix}{message}{suffix}")


# ===== Fallback 判定 =====
#
# 判断是否应该切到 MiniMax 兜底。**不**仅依赖字符串包含 —— 优先看异常类型 /
# HTTP 状态 / 业务码。
#
# 允许兜底的错误：
# - 请求超时 / 网络异常（asyncio.TimeoutError, aiohttp.ClientError）
# - HTTP 429 / 5xx
# - 主供应商音色不存在 / 未授权（aliyun 418、xunfei vcn 类）
# - 主供应商返回空音频
# - 主供应商配置缺失但 MiniMax 已配
#
# 默认不兜底：
# - 文本为空 / 超长
# - 参数校验失败
# - 本地 IO / DB 异常
# - 用户主动请求未配置音色

_FALLBACK_TRANSIENT_TOKENS = (
    "timeout", "超时", "timed out",
    "429", "too many requests", "qps",
    "500", "502", "503", "504",
    "gateway", "bad gateway", "service unavailable", "internal error", "internal server",
    "connection", "网络", "connection error", "client error",
)

_FALLBACK_VOICE_INVALID_TOKENS = (
    "voice not found", "vcn", "音色", "speaker", "no such voice",
    "未授权", "unauthorized voice", "418",
)

_FALLBACK_EMPTY_AUDIO_TOKENS = (
    "空音频", "no audio", "empty audio", "returned empty", "audio is empty",
    "缺少", "missing audio",
)

# 这些关键词出现时，明显属于本地代码错误或参数错，禁止 fallback
_FALLBACK_FORBIDDEN_TOKENS = (
    "文本不能为空", "text cannot be empty", "empty text",
    "文本长度超过", "text too long", "max length",
    "persist failed", "落库", "database", "integrity",
    "unsupported tts_engine", "不支持的 tts_engine",
    "本地", "local file", "permission denied", "enospc",
)


def _is_fallback_allowed_error(err: Any) -> bool:
    """主供应商失败时，判断是否允许进入 MiniMax fallback。

    优先看结构化字段（异常类型 / AliyunTTSError.status / XunfeiTTSError.code），
    再看错误文案关键词。明显属于本地错误（文本超长 / DB 失败 / 参数校验）
    一定拒绝 fallback。
    """
    if err is None:
        return False

    # 结构化优先
    if isinstance(err, asyncio.TimeoutError):
        return True
    try:
        import aiohttp as _aio
        if isinstance(err, _aio.ClientError):
            return True
    except Exception:
        pass
    if isinstance(err, AliyunTTSError):
        if err.status is not None and (err.status == 429 or err.status >= 500):
            return True
        # aliyun 418 = voice not found / 未授权
        if err.status == 418:
            return True
    if isinstance(err, XunfeiTTSError):
        # 讯飞：10010/10019/11200 是音色 / 授权类，10065 是 QPS，10014 余额
        if err.code in (10010, 10019, 11200, 11201, 10065):
            return True

    # 退到字符串判断
    text = str(err).lower()
    if not text:
        return False
    if any(tok in text for tok in _FALLBACK_FORBIDDEN_TOKENS):
        return False
    if any(tok in text for tok in _FALLBACK_VOICE_INVALID_TOKENS):
        return True
    if any(tok in text for tok in _FALLBACK_TRANSIENT_TOKENS):
        return True
    if any(tok in text for tok in _FALLBACK_EMPTY_AUDIO_TOKENS):
        return True
    return False


def _should_use_minimax_fallback(error: Any) -> bool:
    """对外暴露的统一判断：错误是否允许进入 MiniMax 兜底。

    与 ``_is_fallback_allowed_error`` 的差别：本函数额外校验 fallback 开关
    和引擎配置（``TTS_FALLBACK_ENABLED`` / ``TTS_FALLBACK_ENGINE``），让 caller
    只传 error 一个参数即可。
    """
    if not TTS_FALLBACK_ENABLED:
        return False
    if TTS_FALLBACK_ENGINE != _FALLBACK_MINIMAX:
        return False
    if TTS_ENGINE == _FALLBACK_MINIMAX:
        # 主引擎本身就是 minimax，没必要 fallback
        return False
    return _is_fallback_allowed_error(error)


class TTSService:
    """TTS 服务类，封装项目统一语音合成入口。"""

    def __init__(self):
        self.voice_profiles = self._load_voice_profiles()
        self.output_dir = _resolve_tts_output_dir(TTS_OUTPUT_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _load_voice_profiles(self) -> list:
        """加载音色配置文件"""
        config_path = Path(__file__).parent.parent / "config" / "tts_voice_profiles.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            xlog.error(0, e, "[tts] load voice profiles failed path=%s", config_path)
            return []

    def _get_cache_key(
        self,
        text: str,
        character_name: str,
        character_voice: str,
        speaker: str,
        emotion: str,
        speed: Optional[int] = None,
        pitch: Optional[int] = None,
        volume: Optional[int] = None,
        engine: Optional[str] = None,
        model: Optional[str] = None,
        audio_format: Optional[str] = None,
        sample_rate: Optional[int] = None,
        language_type: Optional[str] = None,
    ) -> str:
        """生成缓存 key。

        speed/pitch/volume 进 key 是为了让项目级绑定下，共用 vcn 但参数不同
        的两个角色不会命中对方的旧音频。engine/model/format 进 key 是为了
        切换阿里云模型或音频格式时不复用旧缓存。
        """
        cache_data = (
            f"{text}|{character_name}|{character_voice}|{speaker}|{emotion}"
            f"|s={speed}|p={pitch}|v={volume}"
            f"|engine={engine or TTS_ENGINE}|model={model or ''}"
            f"|fmt={audio_format or TTS_AUDIO_FORMAT}|sr={sample_rate or ''}"
            f"|lang={language_type or ''}"
        )
        return hashlib.md5(cache_data.encode("utf-8")).hexdigest()

    def _get_cache_path(self, cache_key: str, audio_format: Optional[str] = None) -> Path:
        """获取缓存文件路径"""
        return self.output_dir / f"{cache_key}.{audio_format or TTS_AUDIO_FORMAT}"

    def _profile_engine(self, profile: Dict[str, Any]) -> str:
        """返回 profile 所属 TTS 引擎。旧配置没有 engine 字段，按讯飞处理。"""
        engine = (profile.get("engine") or "").lower()
        if engine:
            return engine
        speaker = (profile.get("speaker") or profile.get("vcn") or "").lower()
        if speaker.startswith("long") or speaker in _ALIYUN_QWEN_VOICES:
            return "aliyun"
        return "xunfei"

    def match_voice_profile(
        self,
        character_voice: Optional[str] = None,
        gender: Optional[str] = None,
        age: Optional[str] = None,
        emotion: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        根据角色 voice 字段匹配音色配置
        返回 (speaker, emotion)
        """
        # 默认值
        default_speaker = TTS_DEFAULT_SPEAKER
        default_emotion = "calm"

        if not self.voice_profiles:
            return default_speaker, default_emotion

        # 标准化输入
        gender = (gender or "").lower()
        age_group = self._normalize_age(age)
        voice_lower = (character_voice or "").lower()
        emotion_lower = (emotion or "").lower()

        # 计算匹配分数
        best_match = None
        best_score = 0

        for profile in self.voice_profiles:
            if self._profile_engine(profile) != TTS_ENGINE:
                continue
            # gender 硬过滤：已知 gender 时绝不跨性别匹配，避免 style_keywords 追平 gender 导致女角色用男声。
            if gender and (profile.get("gender") or "").lower() != gender:
                continue

            score = 0

            # 性别匹配（已硬过滤保证命中，保留分数便于排序一致性）
            if gender and profile.get("gender", "").lower() == gender:
                score += 30

            # 年龄匹配
            if age_group and profile.get("age_group", "").lower() == age_group:
                score += 20

            # voice 字段关键词匹配
            style_keywords = profile.get("style_keywords", [])
            for keyword in style_keywords:
                if keyword in voice_lower:
                    score += 10

            # emotion 匹配
            profile_emotion = profile.get("emotion_prompt", "").lower()
            if emotion_lower and emotion_lower == profile_emotion:
                score += 15

            if score > best_score:
                best_score = score
                best_match = profile

        if best_match:
            return best_match.get("speaker", default_speaker), best_match.get("emotion_prompt", default_emotion)

        if gender:
            xlog.warn(
                0,
                "[tts] no voice profile matched engine=%s gender=%s age=%s; pool may be thin, falling back to default speaker=%s",
                TTS_ENGINE, gender, age_group or "", default_speaker,
            )
        return default_speaker, default_emotion

    def _match_minimax_voice_profile(
        self,
        *,
        character_voice: Optional[str],
        gender: Optional[str],
        age: Optional[str],
        emotion: Optional[str],
    ) -> Optional[str]:
        """从现有角色 profile 的 MiniMax 映射中挑主引擎音色。

        配置文件历史上按阿里云/讯飞 speaker 组织，但每条 profile 已携带
        ``fallback_voice_ids.minimax``。MiniMax 成为主引擎后直接对这些映射做
        同一套性别/年龄/风格/情绪评分，避免所有角色落到默认男声。
        """
        normalized_gender = (gender or "").lower()
        age_group = self._normalize_age(age)
        voice_lower = (character_voice or "").lower()
        emotion_lower = (emotion or "").lower()
        best_voice_id: Optional[str] = None
        best_score = 0

        for profile in self.voice_profiles:
            fallback_ids = profile.get("fallback_voice_ids") or {}
            if not isinstance(fallback_ids, dict):
                continue
            voice_id = fallback_ids.get(_FALLBACK_MINIMAX)
            if not isinstance(voice_id, str) or not voice_id.strip():
                continue
            profile_gender = (profile.get("gender") or "").lower()
            if normalized_gender and profile_gender != normalized_gender:
                continue

            score = 0
            if normalized_gender and profile_gender == normalized_gender:
                score += 30
            if age_group and (profile.get("age_group") or "").lower() == age_group:
                score += 20
            for keyword in profile.get("style_keywords") or []:
                if str(keyword).lower() in voice_lower:
                    score += 10
            if emotion_lower and (profile.get("emotion_prompt") or "").lower() == emotion_lower:
                score += 15
            if score > best_score:
                best_score = score
                best_voice_id = voice_id.strip()

        return best_voice_id

    def _known_minimax_voice_ids(self) -> set[str]:
        """返回可安全接受为显式 MiniMax speaker 的配置内音色集合。"""
        from app.services.minimax_tts_provider import (
            MINIMAX_TTS_DEFAULT_VOICE_ID,
            MINIMAX_TTS_VOICE_MAP,
        )

        voice_ids = {MINIMAX_TTS_DEFAULT_VOICE_ID}
        voice_ids.update(
            value.strip()
            for value in MINIMAX_TTS_VOICE_MAP.values()
            if isinstance(value, str) and value.strip()
        )
        for profile in self.voice_profiles:
            fallback_ids = profile.get("fallback_voice_ids") or {}
            if not isinstance(fallback_ids, dict):
                continue
            value = fallback_ids.get(_FALLBACK_MINIMAX)
            if isinstance(value, str) and value.strip():
                voice_ids.add(value.strip())
        voice_ids.update(
            value.strip()
            for value in os.getenv("TTS_ALLOWED_SPEAKERS", "").split(",")
            if value.strip()
        )
        return voice_ids

    def _normalize_age(self, age: Optional[str]) -> str:
        """标准化年龄分组"""
        if not age:
            return ""

        age_lower = age.lower()

        if any(kw in age_lower for kw in ["老", "elder", "old", "senior"]):
            return "elderly"
        elif any(kw in age_lower for kw in ["中", "middle", "中年"]):
            return "middle"
        elif any(kw in age_lower for kw in ["少", "青", "young", "少年", "青年"]):
            return "young"

        return ""

    async def synthesize(
        self,
        text: str,
        character_name: Optional[str] = None,
        character_voice: Optional[str] = None,
        gender: Optional[str] = None,
        age: Optional[str] = None,
        emotion: Optional[str] = None,
        speaker: Optional[str] = None,
        emotion_prompt: Optional[str] = None,
        speed: Optional[int] = None,
        pitch: Optional[int] = None,
        volume: Optional[int] = None,
        *,
        allow_fallback: bool = True,
        force_engine: Optional[str] = None,
        minimax_voice_id: Optional[str] = None,
        minimax_speed: Optional[int] = None,
        minimax_pitch: Optional[int] = None,
        minimax_volume: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        合成语音

        Args:
            text: 要合成的文本
            character_name: 角色名
            character_voice: 角色卡里的 voice 字段
            gender: 性别
            age: 年龄
            emotion: 情绪
            speaker: 直接指定音色（可选，优先级最高）
            emotion_prompt: 直接指定情绪（可选）
            speed: 语速 0-100（可选，未传时从 profile 反查）
            pitch: 语调 0-100（可选）
            volume: 音量 0-100（可选）
            allow_fallback: 主供应商失败后是否允许切到 fallback 引擎。
                默认 True。章节配音循环里会显式传 False，避免每次重试都
                触发 MiniMax 双计费。
            force_engine: 强制使用指定引擎（``"aliyun"`` / ``"xunfei"`` /
                ``"minimax"``），跳过 ``TTS_ENGINE`` 默认值。章节配音在
                重试耗尽后会显式传 ``force_engine="minimax"`` 走兜底路径。

        Returns:
            {
                "success": bool,
                "audio_url": str,
                "cached": bool,
                "speaker": str,
                "emotion_prompt": str,
                "engine": str,            # 实际使用的引擎
                "model": str,             # 实际使用的模型（minimax 必有）
                "fallback_used": bool,    # 是否经过 fallback
                "primary_engine": str,    # fallback 时是原主引擎；否则 == engine
                "primary_error": str,     # fallback 时填主供应商错误；否则空
                "error": str (if failed)
            }
        """
        # Check before force_engine dispatch. Otherwise a direct MiniMax request
        # bypasses the legacy guard in _synthesize_with_primary.
        if not TTS_ENABLED:
            xlog.warn(0, "[tts] synthesize service rejected disabled")
            return {
                "success": False,
                "error": "TTS 功能未启用",
                "error_code": "tts.feature_disabled",
            }

        # 解析目标引擎：force_engine > TTS_ENGINE
        resolved_engine = (force_engine or TTS_ENGINE or "").lower()

        # ===== MiniMax 直连分支（force_engine="minimax" 或 TTS_ENGINE=minimax）=====
        if resolved_engine == _FALLBACK_MINIMAX:
            return await self._synthesize_with_minimax(
                text=text,
                character_name=character_name,
                character_voice=character_voice,
                gender=gender,
                age=age,
                emotion=emotion,
                speaker=speaker,
                emotion_prompt=emotion_prompt,
                speed=speed,
                pitch=pitch,
                volume=volume,
                primary_engine=TTS_ENGINE,
                primary_error=None,
                fallback_used=force_engine == _FALLBACK_MINIMAX and TTS_ENGINE != _FALLBACK_MINIMAX,
                override_voice_id=minimax_voice_id,
                override_speed=minimax_speed,
                override_pitch=minimax_pitch,
                override_volume=minimax_volume,
            )

        # ===== 主供应商分支（aliyun / xunfei）=====
        primary_result = await self._synthesize_with_primary(
            text=text,
            character_name=character_name,
            character_voice=character_voice,
            gender=gender,
            age=age,
            emotion=emotion,
            speaker=speaker,
            emotion_prompt=emotion_prompt,
            speed=speed,
            pitch=pitch,
            volume=volume,
            engine=resolved_engine or TTS_ENGINE,
        )

        if primary_result.get("success"):
            # 主供应商成功 → 直接返回，加 engine 字段
            primary_result.setdefault("engine", resolved_engine or TTS_ENGINE)
            primary_result.setdefault("fallback_used", False)
            primary_result.setdefault("primary_engine", primary_result["engine"])
            primary_result.setdefault("primary_error", "")
            return primary_result

        # 主供应商失败
        if not allow_fallback:
            # caller 明确要求不 fallback（章节配音重试循环内部）
            primary_result.setdefault("engine", resolved_engine or TTS_ENGINE)
            primary_result.setdefault("fallback_used", False)
            primary_result.setdefault("primary_engine", primary_result["engine"])
            primary_result.setdefault("primary_error", "")
            return primary_result

        primary_error = primary_result.get("error")
        if not _should_use_minimax_fallback(primary_error):
            # 不在允许兜底的错误范围内 → 透传主供应商结果
            primary_result.setdefault("engine", resolved_engine or TTS_ENGINE)
            primary_result.setdefault("fallback_used", False)
            primary_result.setdefault("primary_engine", primary_result["engine"])
            primary_result.setdefault("primary_error", "")
            return primary_result

        # ===== MiniMax fallback 分支 =====
        xlog.info(
            0,
            "[tts] synthesize primary failed, trying minimax fallback speaker=%s err=%s",
            speaker or "", (primary_error or "")[:200],
        )
        return await self._synthesize_with_minimax(
            text=text,
            character_name=character_name,
            character_voice=character_voice,
            gender=gender,
            age=age,
            emotion=emotion,
            speaker=speaker,
            emotion_prompt=emotion_prompt,
            speed=speed,
            pitch=pitch,
            volume=volume,
            primary_engine=resolved_engine or TTS_ENGINE,
            primary_error=primary_error,
            fallback_used=True,
            override_voice_id=minimax_voice_id,
            override_speed=minimax_speed,
            override_pitch=minimax_pitch,
            override_volume=minimax_volume,
        )

    async def _synthesize_with_primary(
        self,
        *,
        text: str,
        character_name: Optional[str],
        character_voice: Optional[str],
        gender: Optional[str],
        age: Optional[str],
        emotion: Optional[str],
        speaker: Optional[str],
        emotion_prompt: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        volume: Optional[int],
        engine: str,
    ) -> Dict[str, Any]:
        """主供应商路径（aliyun / xunfei）。保留原有 synthesize 主体逻辑。"""
        xlog.info(0, "[tts] synthesize service start text_chars=%d character=%s speaker=%s", len(text or ""), character_name or "", speaker or "")
        if not TTS_ENABLED:
            xlog.warn(0, "[tts] synthesize service rejected disabled")
            return {
                "success": False,
                "error": "TTS 功能未启用"
            }

        # 文本校验
        if not text or not text.strip():
            xlog.warn(0, "[tts] synthesize service rejected empty text")
            return {
                "success": False,
                "error": "文本不能为空"
            }

        text = text.strip()

        if len(text) > TTS_MAX_TEXT_LENGTH:
            xlog.warn(0, "[tts] synthesize service rejected text too long text_chars=%d max=%d", len(text), TTS_MAX_TEXT_LENGTH)
            return {
                "success": False,
                "error": f"文本长度超过限制（最大 {TTS_MAX_TEXT_LENGTH} 字）"
            }

        # 匹配音色
        if not speaker or not emotion_prompt:
            matched_speaker, matched_emotion = self.match_voice_profile(
                character_voice=character_voice,
                gender=gender,
                age=age,
                emotion=emotion
            )
            speaker = speaker or matched_speaker
            emotion_prompt = emotion_prompt or matched_emotion

        # speed/pitch/volume：入参优先，未传则从 profile 反查默认值
        profile_extras = self._lookup_profile_extras(speaker, emotion_prompt)
        final_speed = speed if speed is not None else profile_extras["speed"]
        final_pitch = pitch if pitch is not None else profile_extras["pitch"]
        final_volume = volume if volume is not None else profile_extras["volume"]
        extras = {
            "tte": profile_extras["tte"],
            "speed": final_speed,
            "pitch": final_pitch,
            "volume": final_volume,
            "rate": profile_extras.get("rate"),
            "pitch_rate": profile_extras.get("pitch_rate"),
            "model": profile_extras.get("model"),
            "audio_format": profile_extras.get("audio_format"),
            "sample_rate": profile_extras.get("sample_rate"),
            "language_type": profile_extras.get("language_type"),
        }
        if engine == "aliyun":
            request_options = self._build_aliyun_options(speaker, extras)
            cache_audio_format = request_options["audio_format"]
            cache_model = request_options["model"]
            cache_sample_rate = request_options.get("sample_rate")
            cache_language_type = request_options.get("language_type")
        else:
            request_options = {}
            cache_audio_format = TTS_AUDIO_FORMAT
            cache_model = ""
            cache_sample_rate = None
            cache_language_type = None

        # 检查缓存（speed/pitch/volume 进 key，避免同 vcn 不同参数撞缓存）
        cache_key = self._get_cache_key(
            text, character_name or "", character_voice or "",
            speaker, emotion_prompt,
            speed=final_speed, pitch=final_pitch, volume=final_volume,
            engine=engine,
            model=cache_model,
            audio_format=cache_audio_format,
            sample_rate=cache_sample_rate,
            language_type=cache_language_type,
        )
        cache_path = self._get_cache_path(cache_key, cache_audio_format)

        if cache_path.exists():
            xlog.info(0, "[tts] synthesize cache hit speaker=%s emotion=%s file=%s", speaker, emotion_prompt, cache_path.name)
            return {
                "success": True,
                "audio_url": f"{TTS_AUDIO_BASE_URL}/{cache_path.name}",
                "cached": True,
                "speaker": speaker,
                "emotion_prompt": emotion_prompt
            }

        # 并发限制
        await _rpm_bucket.acquire()
        async with _tts_semaphore:
            try:
                if engine == "aliyun":
                    audio_data = await self._call_aliyun_tts_api(text, speaker, extras, request_options)
                elif engine == "xunfei":
                    audio_data = await self._call_xunfei_tts_api(text, speaker, extras)
                else:
                    return {
                        "success": False,
                        "error": f"不支持的 TTS_ENGINE: {engine}"
                    }

                if audio_data is None:
                    xlog.warn(0, "[tts] synthesize api returned empty engine=%s speaker=%s", engine, speaker)
                    return {
                        "success": False,
                        "error": f"{engine} TTS 返回空音频"
                    }

                # 保存音频文件
                with open(cache_path, "wb") as f:
                    f.write(audio_data)

                xlog.info(
                    0,
                    "[tts] synthesize service ok engine=%s speaker=%s emotion=%s bytes=%d file=%s",
                    engine, speaker, emotion_prompt, len(audio_data), cache_path.name,
                )
                return {
                    "success": True,
                    "audio_url": f"{TTS_AUDIO_BASE_URL}/{cache_path.name}",
                    "cached": False,
                    "speaker": speaker,
                    "emotion_prompt": emotion_prompt
                }

            except AliyunTTSError as e:
                xlog.warn(0, "[tts] synthesize aliyun error speaker=%s err=%s", speaker, e)
                return {
                    "success": False,
                    "error": f"阿里云 TTS 错误: {str(e)}"
                }
            except XunfeiTTSError as e:
                xlog.warn(0, "[tts] synthesize xunfei business error code=%d speaker=%s", e.code, speaker)
                return {
                    "success": False,
                    "error": self._map_xunfei_error(e.code, str(e))
                }
            except asyncio.TimeoutError:
                xlog.warn(0, "[tts] synthesize timeout engine=%s speaker=%s", engine, speaker)
                return {
                    "success": False,
                    "error": f"{engine} TTS 请求超时"
                }
            except Exception as e:
                xlog.error(0, e, "[tts] synthesize service exception speaker=%s", speaker)
                return {
                    "success": False,
                    "error": f"TTS 合成失败: {str(e)}"
                }

    async def _synthesize_with_minimax(
        self,
        *,
        text: str,
        character_name: Optional[str],
        character_voice: Optional[str],
        gender: Optional[str],
        age: Optional[str],
        emotion: Optional[str],
        speaker: Optional[str],
        emotion_prompt: Optional[str],
        speed: Optional[int],
        pitch: Optional[int],
        volume: Optional[int],
        primary_engine: str,
        primary_error: Optional[str],
        fallback_used: bool,
        override_voice_id: Optional[str] = None,
        override_speed: Optional[int] = None,
        override_pitch: Optional[int] = None,
        override_volume: Optional[int] = None,
    ) -> Dict[str, Any]:
        """MiniMax 同步合成路径。

        缓存键独立（engine=minimax），绝不与主供应商缓存冲突。MiniMax
        成功后用 MiniMax 的 voice_id / 模型 / 音频参数落 cache，下次直接
        命中。
        """
        from app.services.minimax_tts_provider import (
            MiniMaxTTSError,
            MINIMAX_TTS_DEFAULT_VOICE_ID,
            MINIMAX_TTS_MODEL,
            minimax_tts_provider,
            resolve_minimax_voice_id,
        )

        provider = minimax_tts_provider
        if not provider.configured:
            xlog.warn(0, "[tts] minimax unavailable: API key not configured")
            return {
                "success": False,
                "error": primary_error or "MiniMax API Key 未配置",
                "engine": _FALLBACK_MINIMAX,
                "model": MINIMAX_TTS_MODEL,
                "fallback_used": fallback_used,
                "primary_engine": primary_engine,
                "primary_error": primary_error or "",
            }

        # 文本基础校验（fallback 不放宽主供应商已经拒绝的文本）
        if not text or not text.strip():
            return {
                "success": False,
                "error": "文本不能为空",
                "engine": _FALLBACK_MINIMAX,
                "model": MINIMAX_TTS_MODEL,
                "fallback_used": fallback_used,
                "primary_engine": primary_engine,
                "primary_error": primary_error or "",
            }
        text_stripped = text.strip()
        if len(text_stripped) > TTS_MAX_TEXT_LENGTH:
            return {
                "success": False,
                "error": f"文本长度超过限制（最大 {TTS_MAX_TEXT_LENGTH} 字）",
                "engine": _FALLBACK_MINIMAX,
                "model": MINIMAX_TTS_MODEL,
                "fallback_used": fallback_used,
                "primary_engine": primary_engine,
                "primary_error": primary_error or "",
            }

        # 默认参数：跟主供应商走的 emotion_prompt 优先；speed/pitch/volume
        # 未传时回退 50（MiniMax scale 50 = 默认值 1.0/0/1.0）
        resolved_emotion = emotion_prompt or emotion or "calm"
        # binding 独占字段优先（per-character minimax 槽），其次主供应商透传，最后 50
        resolved_speed = override_speed if override_speed is not None else (
            speed if speed is not None else 50
        )
        resolved_pitch = override_pitch if override_pitch is not None else (
            pitch if pitch is not None else 50
        )
        resolved_volume = override_volume if override_volume is not None else (
            volume if volume is not None else 50
        )

        # 解析 MiniMax voice_id
        # 优先级：1) binding override（每角色独占） 2) profile.fallback_voice_ids.minimax
        #         3) voice_map（环境变量） 4) default
        if override_voice_id:
            minimax_voice_id = override_voice_id.strip() or self._lookup_minimax_default_fallback(speaker)
        elif primary_engine == _FALLBACK_MINIMAX:
            explicit_speaker = (speaker or "").strip()
            if explicit_speaker and explicit_speaker in self._known_minimax_voice_ids():
                minimax_voice_id = explicit_speaker
            else:
                minimax_voice_id = self._match_minimax_voice_profile(
                    character_voice=character_voice,
                    gender=gender,
                    age=age,
                    emotion=emotion_prompt or emotion,
                ) or self._lookup_minimax_default_fallback(speaker)
        else:
            profile_fallback = self._lookup_minimax_profile_fallback(speaker)
            minimax_voice_id = resolve_minimax_voice_id(
                primary_speaker=speaker,
                profile_fallback=profile_fallback,
            )

        # 构造 MiniMax 专用 cache key
        cache_audio_format = provider.audio_format
        cache_key = self._get_minimax_cache_key(
            text=text_stripped,
            character_name=character_name or "",
            character_voice=character_voice or "",
            voice_id=minimax_voice_id,
            emotion=resolved_emotion,
            speed=resolved_speed,
            pitch=resolved_pitch,
            volume=resolved_volume,
            model=provider.model,
            audio_format=cache_audio_format,
            sample_rate=provider.sample_rate,
        )
        cache_path = self._get_cache_path(cache_key, cache_audio_format)
        if cache_path.exists():
            xlog.info(
                0,
                "[tts] minimax cache hit voice=%s emotion=%s file=%s",
                minimax_voice_id, resolved_emotion, cache_path.name,
            )
            return {
                "success": True,
                "audio_url": f"{TTS_AUDIO_BASE_URL}/{cache_path.name}",
                "cached": True,
                "speaker": minimax_voice_id,
                "emotion_prompt": resolved_emotion,
                "engine": _FALLBACK_MINIMAX,
                "model": provider.model,
                "fallback_used": fallback_used,
                "primary_engine": primary_engine,
                "primary_error": primary_error or "",
            }

        await _rpm_bucket.acquire()
        async with _tts_semaphore:
            try:
                audio_bytes = await provider.synthesize(
                    text=text_stripped,
                    voice_id=minimax_voice_id,
                    emotion=resolved_emotion,
                    speed=resolved_speed,
                    pitch=resolved_pitch,
                    volume=resolved_volume,
                )
            except MiniMaxTTSError as e:
                xlog.warn(
                    0,
                    "[tts] minimax error voice=%s http=%s code=%s trace=%s msg=%s",
                    minimax_voice_id, e.http_status, e.business_code, e.trace_id or "-", str(e)[:200],
                )
                # fallback 失败时把两个供应商的错误合并返回
                merged_error = (
                    f"primary={primary_error}; fallback=minimax {str(e)}"
                ) if primary_error else f"minimax {str(e)}"
                return {
                    "success": False,
                    "error": merged_error,
                    "engine": _FALLBACK_MINIMAX,
                    "model": provider.model,
                    "fallback_used": fallback_used,
                    "primary_engine": primary_engine,
                    "primary_error": primary_error or "",
                }
            except asyncio.TimeoutError:
                xlog.warn(0, "[tts] minimax timeout voice=%s", minimax_voice_id)
                merged_error = (
                    f"primary={primary_error}; fallback=minimax timeout"
                ) if primary_error else "minimax timeout"
                return {
                    "success": False,
                    "error": merged_error,
                    "engine": _FALLBACK_MINIMAX,
                    "model": provider.model,
                    "fallback_used": fallback_used,
                    "primary_engine": primary_engine,
                    "primary_error": primary_error or "",
                }
            except Exception as e:
                xlog.error(0, e, "[tts] minimax exception voice=%s", minimax_voice_id)
                merged_error = (
                    f"primary={primary_error}; fallback=minimax {e}"
                ) if primary_error else f"minimax {e}"
                return {
                    "success": False,
                    "error": merged_error,
                    "engine": _FALLBACK_MINIMAX,
                    "model": provider.model,
                    "fallback_used": fallback_used,
                    "primary_engine": primary_engine,
                    "primary_error": primary_error or "",
                }

        if not audio_bytes:
            return {
                "success": False,
                "error": f"primary={primary_error}; fallback=minimax empty audio" if primary_error else "minimax empty audio",
                "engine": _FALLBACK_MINIMAX,
                "model": provider.model,
                "fallback_used": fallback_used,
                "primary_engine": primary_engine,
                "primary_error": primary_error or "",
            }

        with open(cache_path, "wb") as f:
            f.write(audio_bytes)

        xlog.info(
            0,
            "[tts] minimax ok voice=%s emotion=%s bytes=%d file=%s",
            minimax_voice_id, resolved_emotion, len(audio_bytes), cache_path.name,
        )
        return {
            "success": True,
            "audio_url": f"{TTS_AUDIO_BASE_URL}/{cache_path.name}",
            "cached": False,
            "speaker": minimax_voice_id,
            "emotion_prompt": resolved_emotion,
            "engine": _FALLBACK_MINIMAX,
            "model": provider.model,
            "fallback_used": fallback_used,
            "primary_engine": primary_engine,
            "primary_error": primary_error or "",
        }

    def _lookup_minimax_default_fallback(self, speaker: Optional[str]) -> str:
        """override_voice_id 为空字符串时的兜底：走 profile/default 链路。

        与 ``resolve_minimax_voice_id`` 等价，但返回值非空。
        """
        profile_fallback = self._lookup_minimax_profile_fallback(speaker)
        return resolve_minimax_voice_id(
            primary_speaker=speaker,
            profile_fallback=profile_fallback,
        )

    def _lookup_minimax_profile_fallback(self, speaker: Optional[str]) -> Optional[str]:
        """按主供应商 speaker 反查 profile.fallback_voice_ids.minimax。

        profile 配置示例（tts_voice_profiles.json）：
            {
              "speaker": "longshuo_v3",
              ...,
              "fallback_voice_ids": { "minimax": "male-qn-qingse" }
            }

        未配置返回 None（让 provider 退到 default voice_id）。
        """
        if not speaker:
            return None
        for p in self.voice_profiles:
            if not isinstance(p, dict):
                continue
            if (p.get("speaker") or p.get("vcn") or "") != speaker:
                continue
            fb = p.get("fallback_voice_ids") or {}
            if isinstance(fb, dict):
                v = fb.get(_FALLBACK_MINIMAX)
                if v and isinstance(v, str):
                    return v.strip()
            return None
        return None

    def _get_minimax_cache_key(
        self,
        *,
        text: str,
        character_name: str,
        character_voice: str,
        voice_id: str,
        emotion: str,
        speed: Optional[int],
        pitch: Optional[int],
        volume: Optional[int],
        model: str,
        audio_format: str,
        sample_rate: int,
    ) -> str:
        """MiniMax 专用 cache key —— 与主供应商 key 隔离。

        主供应商 cache key 用的 ``character_voice`` 是 aliyun/xunfei vcn，
        MiniMax 不能复用同一个 voice_id 字段，否则会把 minimax 音频伪装成
        aliyun 缓存。这里独立用 ``engine=minimax`` + ``minimax_voice_id``
        做指纹。
        """
        cache_data = (
            f"{text}|{character_name}|{character_voice}|mm={voice_id}"
            f"|emotion={emotion}"
            f"|s={speed}|p={pitch}|v={volume}"
            f"|engine={_FALLBACK_MINIMAX}|model={model or ''}"
            f"|fmt={audio_format}|sr={sample_rate}"
        )
        return hashlib.md5(cache_data.encode("utf-8")).hexdigest()

    def _lookup_profile_extras(self, speaker: str, emotion: str) -> Dict[str, Any]:
        """按 speaker+emotion 反查 profile，拿扩展参数。

        profile 里没显式配置的字段回退到模块级默认。
        找不到匹配 profile 时返回默认参数。
        """
        for p in self.voice_profiles:
            if p.get("speaker") == speaker and (p.get("emotion_prompt", "") or "").lower() == (emotion or "").lower():
                return {
                    "tte": p.get("tte", emotion or "calm"),
                    "speed": int(p.get("speed", XUNFEI_TTS_SPEED)),
                    "volume": int(p.get("volume", XUNFEI_TTS_VOLUME)),
                    "pitch": int(p.get("pitch", XUNFEI_TTS_PITCH)),
                    "rate": float(p["rate"]) if p.get("rate") is not None else None,
                    "pitch_rate": float(p["pitch_rate"]) if p.get("pitch_rate") is not None else None,
                    "model": p.get("model"),
                    "audio_format": p.get("audio_format"),
                    "sample_rate": int(p["sample_rate"]) if p.get("sample_rate") is not None else None,
                    "language_type": p.get("language_type"),
                }
        return {
            "tte": emotion or "calm",
            "speed": XUNFEI_TTS_SPEED,
            "volume": XUNFEI_TTS_VOLUME,
            "pitch": XUNFEI_TTS_PITCH,
            "rate": None,
            "pitch_rate": None,
            "model": None,
            "audio_format": None,
            "sample_rate": None,
            "language_type": None,
        }

    def _is_aliyun_qwen_voice(self, speaker: str) -> bool:
        return (speaker or "").lower() in _ALIYUN_QWEN_VOICES

    def _select_aliyun_model(self, speaker: str, extras: Dict[str, Any]) -> str:
        """根据音色选择阿里云模型。profile.model 优先，其次内置映射。"""
        if extras.get("model"):
            return str(extras["model"])
        speaker_key = (speaker or "").lower()
        if self._is_aliyun_qwen_voice(speaker):
            return ALIYUN_TTS_QWEN_MODEL
        if speaker_key in _ALIYUN_COSYVOICE_MODEL_OVERRIDES:
            return _ALIYUN_COSYVOICE_MODEL_OVERRIDES[speaker_key]
        if speaker_key.endswith("_v3"):
            return "cosyvoice-v3-flash"
        if speaker_key.endswith("_v2"):
            return "cosyvoice-v2"
        return ALIYUN_TTS_COSYVOICE_MODEL

    @staticmethod
    def _scale_0_100_to_aliyun_rate(value: Optional[int]) -> float:
        """把旧 0-100 参数转换为阿里云 [0.5, 2.0] 的 rate/pitch。"""
        if value is None:
            return 1.0
        value = max(0, min(100, int(value)))
        if value <= 50:
            return round(0.5 + (value / 50) * 0.5, 2)
        return round(1.0 + ((value - 50) / 50) * 1.0, 2)

    @staticmethod
    def _looks_like_ssml(text: str) -> bool:
        return text.lstrip().lower().startswith("<speak")

    def _build_aliyun_options(self, speaker: str, extras: Dict[str, Any]) -> Dict[str, Any]:
        """构造阿里云请求选项，供缓存 key 和实际请求共用。"""
        is_qwen = self._is_aliyun_qwen_voice(speaker)
        model = self._select_aliyun_model(speaker, extras)
        if is_qwen:
            audio_format = extras.get("audio_format") or ALIYUN_TTS_QWEN_AUDIO_FORMAT
            sample_rate = None
        else:
            audio_format = extras.get("audio_format") or ALIYUN_TTS_AUDIO_FORMAT
            sample_rate = int(extras.get("sample_rate") or ALIYUN_TTS_SAMPLE_RATE)
        return {
            "provider": "qwen" if is_qwen else "cosyvoice",
            "model": model,
            "audio_format": audio_format,
            "sample_rate": sample_rate,
            "volume": max(0, min(100, int(extras.get("volume", 50)))),
            "rate": extras.get("rate") or self._scale_0_100_to_aliyun_rate(extras.get("speed")),
            "pitch": extras.get("pitch_rate") or self._scale_0_100_to_aliyun_rate(extras.get("pitch")),
            "language_type": extras.get("language_type") or ALIYUN_TTS_LANGUAGE_TYPE,
        }

    def _aliyun_url(self, endpoint: str) -> str:
        return f"{ALIYUN_TTS_BASE_URL.rstrip('/')}/{endpoint.lstrip('/')}"

    async def _call_aliyun_tts_api(
        self,
        text: str,
        speaker: str,
        extras: Dict[str, Any],
        options: Optional[Dict[str, Any]] = None,
    ) -> Optional[bytes]:
        """调用阿里云百炼非实时 TTS HTTP API，并下载临时音频 URL。"""
        if not DASHSCOPE_API_KEY:
            xlog.warn(0, "[tts] aliyun config incomplete: DASHSCOPE_API_KEY missing")
            return None

        options = options or self._build_aliyun_options(speaker, extras)
        if options["provider"] == "qwen":
            endpoint = "services/aigc/multimodal-generation/generation"
            payload = {
                "model": options["model"],
                "input": {
                    "text": text,
                    "voice": speaker,
                    "language_type": options["language_type"],
                },
            }
        else:
            endpoint = "services/audio/tts/SpeechSynthesizer"
            payload_input = {
                "text": text,
                "voice": speaker,
                "format": options["audio_format"],
                "sample_rate": options["sample_rate"],
                "volume": options["volume"],
                "rate": options["rate"],
                "pitch": options["pitch"],
            }
            if ALIYUN_TTS_ENABLE_SSML or self._looks_like_ssml(text):
                payload_input["enable_ssml"] = True
            payload = {
                "model": options["model"],
                "input": payload_input,
            }

        timeout = aiohttp.ClientTimeout(
            total=ALIYUN_TTS_TIMEOUT,
            connect=ALIYUN_TTS_CONNECT_TIMEOUT,
        )
        headers = {
            "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
            "Content-Type": "application/json",
        }
        url = self._aliyun_url(endpoint)
        xlog.info(
            0,
            "[tts] aliyun http start provider=%s model=%s voice=%s text_chars=%d",
            options["provider"], options["model"], speaker, len(text),
        )

        async with aiohttp.ClientSession(timeout=timeout) as session:
            with provider_span(
                "tts aliyun.synthesize",
                {
                    "gen_ai.system": "aliyun-dashscope",
                    "gen_ai.operation.name": "tts.synthesize",
                    "tts.provider": options["provider"],
                    "tts.model": options["model"],
                    "tts.voice_id": speaker,
                    "tts.text_chars": len(text),
                },
            ) as span:
                async with session.post(url, headers=headers, json=payload) as resp:
                    body_text = await resp.text()
                    if span is not None:
                        span.set_attribute("http.response.status_code", resp.status)
                try:
                    data = json.loads(body_text) if body_text else {}
                except json.JSONDecodeError as e:
                    raise AliyunTTSError(
                        f"响应不是 JSON: {body_text[:200]}",
                        status=resp.status,
                    ) from e

                status_code = int(data.get("status_code") or resp.status)
                if span is not None:
                    span.set_attribute("tts.provider.status_code", status_code)
                    request_id = data.get("request_id")
                    if request_id:
                        span.set_attribute("tts.provider.request_id", str(request_id))
                if resp.status >= 400 or status_code >= 400:
                    raise AliyunTTSError(
                        data.get("message") or data.get("code") or body_text[:200] or "request failed",
                        status=status_code,
                        request_id=data.get("request_id"),
                    )

                audio = data.get("output", {}).get("audio", {}) or {}
                audio_b64 = audio.get("data") or ""
                if audio_b64:
                    return base64.b64decode(audio_b64)

                audio_url = audio.get("url")
                if not audio_url:
                    raise AliyunTTSError(
                        f"响应缺少 output.audio.url: {body_text[:200]}",
                        status=status_code,
                        request_id=data.get("request_id"),
                    )
                return await self._download_aliyun_audio(audio_url, data.get("request_id"))

    async def _download_aliyun_audio(
        self,
        audio_url: str,
        request_id: Optional[str] = None,
    ) -> bytes:
        """下载阿里云返回的 24 小时临时音频 URL，落本地缓存前使用。"""
        timeout = aiohttp.ClientTimeout(
            total=ALIYUN_TTS_TIMEOUT,
            connect=ALIYUN_TTS_CONNECT_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            with provider_span(
                "tts aliyun.download_audio",
                {
                    "gen_ai.system": "aliyun-dashscope",
                    "gen_ai.operation.name": "tts.download_audio",
                    "tts.provider": "aliyun",
                    "tts.provider.request_id": request_id or "",
                },
            ) as span:
                async with session.get(audio_url) as resp:
                    if span is not None:
                        span.set_attribute("http.response.status_code", resp.status)
                    if resp.status >= 400:
                        body = await resp.text()
                        raise AliyunTTSError(
                            f"音频下载失败: {body[:200]}",
                            status=resp.status,
                            request_id=request_id,
                        )
                    audio_data = await resp.read()
                    if not audio_data:
                        raise AliyunTTSError("音频下载结果为空", status=resp.status, request_id=request_id)
                    if span is not None:
                        span.set_attribute("tts.audio.bytes", len(audio_data))
                    return audio_data

    def _map_xunfei_error(self, code: int, raw: str) -> str:
        """讯飞业务错误码 → 中文文案，未知 code 给通用文案。"""
        return _XUNFEI_ERROR_MAP.get(code, f"讯飞 TTS 错误 [{code}] {raw}")

    def _build_auth_url(self) -> str:
        """生成讯飞 WebSocket 鉴权 URL（HMAC-SHA256 签名）。

        签名原串格式（讯飞规范）：
            host: {DOMAIN}\\ndate: {GMT date}\\nGET {PATH} HTTP/1.1

        Returns:
            wss://{DOMAIN}{PATH}?authorization=...&date=...&host=...
        """
        date = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        signature_origin = f"host: {XUNFEI_TTS_DOMAIN}\ndate: {date}\nGET {XUNFEI_TTS_PATH} HTTP/1.1"
        signature_sha = hmac.new(
            XUNFEI_TTS_API_SECRET.encode("utf-8"),
            signature_origin.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        signature = base64.b64encode(signature_sha).decode("utf-8")
        authorization_origin = (
            f'api_key="{XUNFEI_TTS_API_KEY}", '
            f'algorithm="hmac-sha256", '
            f'headers="host date request-line", '
            f'signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
        params = {"authorization": authorization, "date": date, "host": XUNFEI_TTS_DOMAIN}
        return f"wss://{XUNFEI_TTS_DOMAIN}{XUNFEI_TTS_PATH}?{urlencode(params)}"

    def _build_request_frame(self, text: str, vcn: str, extras: Dict[str, Any]) -> dict:
        """构造讯飞 WS 上行 JSON 帧（单帧 status=2，一帧结束）。

        协议（cbm01 / mcd9m97e6 接入点，超拟人语音合成）：
        - header 必含 status（与 payload.text.status 一致）
        - parameter.oral 必传（仅 x4 系列生效，其他 vcn 字段被忽略但结构必须存在）
        - parameter.tts.vcn 必须是控制台已开通权限的发音人
        - parameter.tts.audio 推荐 lame / 24000 / 16 / 1
        - payload.text.status 只接受 0/1/2（一次性合成直接传 2）
        """
        text_b64 = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        return {
            "header": {
                "app_id": XUNFEI_TTS_APPID,
                "status": 2,
            },
            "parameter": {
                "oral": {
                    "oral_level": "mid",
                    "spark_assist": 0,
                    "stop_split": 0,
                    "remain": 0,
                },
                "tts": {
                    "vcn": vcn,
                    "speed": extras["speed"],
                    "volume": extras["volume"],
                    "pitch": extras["pitch"],
                    "bgs": 0,
                    "reg": 0,
                    "rdn": 0,
                    "rhy": 0,
                    "audio": {
                        "encoding": XUNFEI_TTS_AUE,
                        "sample_rate": 24000,
                        "channels": 1,
                        "bit_depth": 16,
                        "frame_size": 0,
                    },
                }
            },
            "payload": {
                "text": {
                    "encoding": "utf8",
                    "compress": "raw",
                    "format": "plain",
                    "status": 2,
                    "seq": 0,
                    "text": text_b64,
                }
            },
        }

    async def _call_xunfei_tts_api(
        self,
        text: str,
        vcn: str,
        extras: Dict[str, Any],
    ) -> Optional[bytes]:
        """调用讯飞超拟人 TTS：建 WS、发帧、收帧、合并音频。"""
        if not XUNFEI_TTS_APPID or not XUNFEI_TTS_API_KEY or not XUNFEI_TTS_API_SECRET:
            xlog.warn(0, "[tts] xunfei config incomplete")
            return None

        auth_url = self._build_auth_url()
        frame = self._build_request_frame(text, vcn, extras)

        xlog.info(0, "[tts] xunfei ws start vcn=%s text_chars=%d", vcn, len(text))

        try:
            return await asyncio.wait_for(
                self._xunfei_ws_run(auth_url, frame),
                timeout=XUNFEI_TTS_TIMEOUT,
            )
        except XunfeiTTSError:
            raise
        except asyncio.TimeoutError:
            xlog.warn(0, "[tts] xunfei ws timeout vcn=%s", vcn)
            raise
        except Exception as e:
            xlog.error(0, e, "[tts] xunfei ws exception vcn=%s", vcn)
            raise

    async def _xunfei_ws_run(self, auth_url: str, frame: dict) -> bytes:
        """单次 WS 收发循环：发请求帧，累积下行音频分片，直到 status==2。"""
        timeout = aiohttp.ClientTimeout(
            total=XUNFEI_TTS_TIMEOUT,
            connect=XUNFEI_TTS_WS_CONNECT_TIMEOUT,
        )
        with provider_span(
            "tts xunfei.websocket",
            {
                "gen_ai.system": "xunfei",
                "gen_ai.operation.name": "tts.synthesize",
                "tts.provider": "xunfei",
                "server.address": XUNFEI_TTS_DOMAIN,
                "url.path": XUNFEI_TTS_PATH,
            },
        ) as span:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.ws_connect(auth_url) as ws:
                    await ws.send_json(frame)
                    chunks: List[bytes] = []
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            header = data.get("header", {})
                            code = header.get("code", -1)
                            if code != 0:
                                if span is not None:
                                    span.set_attribute("tts.provider.status_code", code)
                                raise XunfeiTTSError(code, header.get("message", ""))
                            audio = data.get("payload", {}).get("audio", {})
                            audio_b64 = audio.get("audio", "")
                            status = audio.get("status", 0)
                            if audio_b64:
                                chunks.append(base64.b64decode(audio_b64))
                            if status == 2:
                                break
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            raise XunfeiTTSError(-1, f"ws closed unexpectedly type={msg.type}")
                    if not chunks:
                        raise XunfeiTTSError(-2, "no audio returned")
                    audio_data = b"".join(chunks)
                    if span is not None:
                        span.set_attribute("tts.audio.bytes", len(audio_data))
                    return audio_data


    def clear_cache(self, max_age_hours: int = 24 * 7) -> int:
        """清理过期缓存文件"""
        import time

        cleared = 0
        now = time.time()
        max_age_seconds = max_age_hours * 3600

        for suffix in {"mp3", "wav", "pcm", "opus", TTS_AUDIO_FORMAT, ALIYUN_TTS_AUDIO_FORMAT, ALIYUN_TTS_QWEN_AUDIO_FORMAT}:
            for file_path in self.output_dir.glob(f"*.{suffix}"):
                if now - file_path.stat().st_mtime > max_age_seconds:
                    file_path.unlink()
                    cleared += 1

        xlog.info(0, "[tts] cache clear done cleared=%d max_age_hours=%d", cleared, max_age_hours)
        return cleared


# 全局实例
tts_service = TTSService()
