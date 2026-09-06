"""
图像生成服务 - Qwen-Image + rembg 背景去除
"""
import os
import json
import hashlib
import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass, field as _dc_field
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List, TYPE_CHECKING
from datetime import datetime
from dotenv import load_dotenv
from app.services.api_key_pool import (
    ApiKeyPool,
    ApiKeyPoolUnavailable,
    PooledAsyncOpenAI,
    SanitizedProviderError,
    api_key_available,
    configured_api_keys,
)
from app.services.visual_style_contract_service import VisualStyleContract
from app.utils import logging as xlog
from app.core.config import get_settings
from app.observability import provider_span

BACKEND_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger("image_generation")

if TYPE_CHECKING:
    from app.schemas import BackgroundSceneSpec, ValidationResult, GenerationResult

load_dotenv()


def _provider_error_category(err: Exception) -> str:
    if isinstance(err, SanitizedProviderError):
        return err.category
    return type(err).__name__


def _provider_error_retryable(err: Exception) -> str:
    if isinstance(err, SanitizedProviderError):
        return str(err.retryable).lower()
    return "unknown"


# 图像生成配置
DEFAULT_QWEN_IMAGE_ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_QWEN_IMAGE_NEGATIVE_PROMPT = (
    "低分辨率，低画质，肢体畸形，手指畸形，画面过饱和，"
    "蜡像感，人脸无细节，过度光滑，画面具有AI感，构图混乱，文字模糊，扭曲，水印，"
    "僵硬站姿，完全对称的木偶姿势，T形站姿，证件照姿势，人体重心不稳，关节不自然"
)
IMAGE_GENERATION_ENABLED = os.getenv("IMAGE_GENERATION_ENABLED", "true").lower() == "true"
AI_IMAGE_API_KEY = os.getenv("AI_IMAGE_API_KEY") or os.getenv("DASHSCOPE_API_KEY", "")
AI_IMAGE_BASE_URL = os.getenv("AI_IMAGE_BASE_URL", DEFAULT_QWEN_IMAGE_ENDPOINT)
AI_IMAGE_MODEL = os.getenv("AI_IMAGE_MODEL", "qwen-image-2.0")
AI_IMAGE_NEGATIVE_PROMPT = os.getenv("AI_IMAGE_NEGATIVE_PROMPT", DEFAULT_QWEN_IMAGE_NEGATIVE_PROMPT)
AI_IMAGE_PROMPT_EXTEND = os.getenv("AI_IMAGE_PROMPT_EXTEND", "true").lower() not in ("0", "false", "no", "off")
AI_IMAGE_WATERMARK = os.getenv("AI_IMAGE_WATERMARK", "false").lower() in ("1", "true", "yes", "on")
LANDSCAPE_POSTPROCESS_CONTRACT_VERSION = (
    "aspect-preserve-v3-watermark-crop"
    if AI_IMAGE_WATERMARK
    else "aspect-preserve-v3-watermark-off"
)

# Legacy validator config kept for compatibility with older tests/tools.
# Background generation reads this to optionally enable bbox-only person check
# in v2 path. Default false: opt-in via BG_VALIDATE_ENABLED=true after P0 prompt
# rewrite is verified.
BG_VALIDATE_ENABLED = os.getenv("BG_VALIDATE_ENABLED", "false").lower() == "true"
BG_VISION_API_KEY = (
    os.getenv("BG_VISION_API_KEY")
    or os.getenv("AI_IMAGE_API_KEY")
    or os.getenv("OPENAI_API_KEY", "")
)
BG_VISION_BASE_URL = (
    os.getenv("BG_VISION_BASE_URL")
    or os.getenv("AI_IMAGE_BASE_URL")
    or os.getenv("OPENAI_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
)
BG_VISION_MODEL = (
    os.getenv("BG_VISION_MODEL")
    or os.getenv("VISION_MODEL")
    or "glm-4v-plus"  # 智谱视觉模型
)
# 验收失败后最多重试次数（不含首次）
BG_VALIDATE_MAX_RETRIES = int(os.getenv("BG_VALIDATE_MAX_RETRIES", "3"))

# Stage_Background_Entity_Exclusion — VLM 剧情角色泄漏验收开关
# 当 spec.forbidden_entities 非空时，对每张产出的背景图调用
# BackgroundStoryEntityValidatorService 检查是否泄漏了禁入实体。
# 检测到泄漏时，按 spec §XIV 进行 escalate 重试；超出上限则将
# GenerationResult.status 置为 quarantined 并附带 reason。
BG_ENTITY_VALIDATE_ENABLED = (
    os.getenv("BG_ENTITY_VALIDATE_ENABLED", "true").lower() == "true"
)
BG_ENTITY_VALIDATE_MAX_RETRIES = int(os.getenv("BG_ENTITY_VALIDATE_MAX_RETRIES", "2"))

# 存储配置
IMAGE_OUTPUT_DIR = os.getenv("IMAGE_OUTPUT_DIR", "static/assets")
IMAGE_BASE_URL = os.getenv("IMAGE_BASE_URL", "/static/assets")

# 图像尺寸约束（Qwen-Image 2.0 总像素上限与旧 CogView 路径一致）
MIN_SIZE = 512
MAX_SIZE = 2880
SIZE_MULTIPLE = 32
MAX_PIXELS = 4194304

# 并发限制
# Key 上限 15 并发，留 3-5 给重试/瞬时尖刺，默认 10。
# 多 worker 部署时实际并发 = 本值 × worker 数，需按 key 上限等比下调每进程配额。
_image_semaphore = asyncio.Semaphore(int(os.getenv("IMAGE_CONCURRENCY", "10")))

PORTRAIT_GENERATION_CONTRACT_VERSION = "portrait-natural-alpha-v3-age"
PORTRAIT_PROVIDER_PROMPT_CONTRACT_VERSION = "portrait-provider-exact-v1"
_PORTRAIT_CONTRACT_MARKER = "PORTRAIT PRESENTATION CONTRACT"


class PortraitTransparencyError(RuntimeError):
    """Raised when a portrait cannot satisfy the transparent PNG contract."""


class InvalidGeneratedImageError(RuntimeError):
    """Raised when provider bytes are not a fully decodable image."""


class ImagePostprocessError(RuntimeError):
    """Raised when a generated image cannot be post-processed safely."""


class _ImageProviderError(RuntimeError):
    def __init__(
        self,
        *,
        category: str,
        status_code: int,
        content_filter: bool = False,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.content_filter = content_filter
        super().__init__(f"Image provider request failed ({category}, status={status_code})")


def _is_provider_content_filter_message(value: str) -> bool:
    text = value or ""
    lowered = text.lower()
    return (
        "1301" in text
        or "contentfilter" in lowered
        or "datainspection" in lowered
        or "sensitive" in lowered
        or "敏感内容" in text
    )


def _image_provider_category(status_code: int) -> str:
    if status_code in (401, 403):
        return "authentication"
    if status_code == 429:
        return "rate_limited"
    if status_code >= 500:
        return "provider_unavailable"
    return "request_rejected"

# 默认尺寸预设
# gpt-image-2（toklens）实测完全无视 size 参数，每次返回 ~1k 的随机尺寸/比例。
# 因此这里的尺寸含义是「letterbox 归一化的输出目标」，而非发给 provider 的请求尺寸。
# 横版取 16:9 的 ~1k（1536x864）：不放大、贴合 VN 屏幕，且与 prompt 里
# “16:9宽幅环境建立镜头”构图词方向一致，letterbox 填充最少。立绘保持竖版/方形。
SIZE_PRESETS = {
    "portrait": (1024, 1024),       # 1:1 立绘
    "portrait_full": (1024, 1536),  # 2:3 全身立绘（竖版）
    "background": (1536, 864),      # 16:9 背景（letterbox 输出目标）
    "keyframe": (1536, 864),        # 16:9 关键帧（同 background）
}


class ImageGenerationService:
    """图像生成服务类"""

    def __init__(self):
        self.output_dir = Path(IMAGE_OUTPUT_DIR)
        self._init_output_dirs()
        self.profiles = self._load_profiles()
        self._image_key_pool = ApiKeyPool(
            configured_api_keys(pool_env="AI_IMAGE_API_KEYS", fallback_key=AI_IMAGE_API_KEY)
        )
        self._openai_image_client: Optional[PooledAsyncOpenAI] = None

    def _init_output_dirs(self):
        """初始化输出目录"""
        for subdir in ["portraits", "backgrounds", "keyframes"]:
            (self.output_dir / subdir).mkdir(parents=True, exist_ok=True)

    def _load_profiles(self) -> Dict[str, Any]:
        """加载图像生成配置"""
        config_path = Path(__file__).parent.parent / "config" / "image_generation_profiles.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            xlog.error(0, e, "[image] load profiles failed path=%s", config_path)
            return {}

    def _validate_size(self, width: int, height: int) -> Tuple[int, int]:
        """验证并修正尺寸"""
        # 确保是 32 的倍数
        width = max(MIN_SIZE, min(MAX_SIZE, (width // SIZE_MULTIPLE) * SIZE_MULTIPLE))
        height = max(MIN_SIZE, min(MAX_SIZE, (height // SIZE_MULTIPLE) * SIZE_MULTIPLE))

        # 确保总像素不超过限制
        while width * height > MAX_PIXELS:
            width = int(width * 0.9 // SIZE_MULTIPLE) * SIZE_MULTIPLE
            height = int(height * 0.9 // SIZE_MULTIPLE) * SIZE_MULTIPLE

        return width, height

    def _get_cache_key(
        self,
        prompt: str,
        asset_type: str,
        seed: Optional[int] = None,
        size: Tuple[int, int] = (1024, 1024)
    ) -> str:
        """生成缓存 key"""
        cache_data = f"{prompt}|{asset_type}|{seed}|{size[0]}x{size[1]}"
        if asset_type == "portrait":
            cache_data += f"|{PORTRAIT_PROVIDER_PROMPT_CONTRACT_VERSION}"
        else:
            # Watermark changes both provider bytes and landscape postprocessing.
            # Version it so deployments do not reuse images cropped under the old
            # unconditional behavior after AI_IMAGE_WATERMARK is disabled.
            cache_data += (
                f"|{LANDSCAPE_POSTPROCESS_CONTRACT_VERSION}"
                f"|watermark={int(AI_IMAGE_WATERMARK)}"
            )
        return hashlib.md5(cache_data.encode("utf-8")).hexdigest()

    def _get_cache_path(self, cache_key: str, asset_type: str) -> Path:
        """获取缓存文件路径"""
        subdir = {
            "portrait": "portraits",
            "background": "backgrounds",
            "keyframe": "keyframes"
        }.get(asset_type, "keyframes")
        return self.output_dir / subdir / f"{cache_key}.png"

    @staticmethod
    def _file_sha256(path: Optional[Path]) -> str:
        """计算文件 sha256 hex digest；文件不存在或读失败返回 ""。

        用于给 portrait Asset.generation_params.reference_image_sha256 注入
        真实值，下游 identity_master_resolver → KeyframeCharacterBinding 才能通
        过 ``has_reference()`` 校验，keyframe 才会走 reference-based 生成路径
        而非 text-only fallback。
        """
        if not path:
            return ""
        try:
            import hashlib as _hashlib
            h = _hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            return ""

    async def generate_image(
        self,
        prompt: str,
        asset_type: str,
        width: int = 1024,
        height: int = 1024,
        seed: Optional[int] = None,
        skip_cache: bool = False,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
        ensure_portrait_alpha: Optional[bool] = None,
        authoritative_prompt: bool = False,
    ) -> Dict[str, Any]:
        """
        生成图像

        Args:
            forbidden_characters: 仅 background 用。在 sanitize retry / safety fallback 路径
                重新构建 prompt 时仍然需要保持 BG-ENFORCE 约束。
            scene_name / scene_description: 仅 background 用。透传给 _call_cogview_api
                的 sanitize retry / safety fallback，确保 human_atmosphere 判断一致。
            authoritative_prompt: 仅供已通过一致性校验的 LLM final_prompt 使用；
                portrait 低层调用没有此标记会被拒绝。

        Returns:
            {
                "success": bool,
                "image_url": str,
                "cached": bool,
                "seed": int,
                "size": [width, height],
                "error": str (if failed)
            }
        """
        if asset_type == "portrait":
            if not authoritative_prompt:
                return {
                    "success": False,
                    "error": (
                        "portrait 必须通过 generate_portrait 的 LLM final_prompt "
                        "校验后生成"
                    ),
                }
            if ensure_portrait_alpha is None:
                ensure_portrait_alpha = True
        else:
            ensure_portrait_alpha = False

        if not IMAGE_GENERATION_ENABLED:
            return {"success": False, "error": "图像生成功能未启用"}

        if not api_key_available(
            AI_IMAGE_API_KEY,
            pool_env="AI_IMAGE_API_KEYS",
            allow_byok=False,
        ):
            return {"success": False, "error": "图像生成 API Key 未配置"}

        # 验证尺寸
        width, height = self._validate_size(width, height)

        # 检查缓存
        cache_key = self._get_cache_key(prompt, asset_type, seed, (width, height))
        cache_path = self._get_cache_path(cache_key, asset_type)

        if not skip_cache and cache_path.exists():
            if not self._is_valid_image_file(cache_path):
                xlog.warn(0, "[image] invalid cache ignored asset_type=%s path=%s", asset_type, cache_path)
                try:
                    cache_path.unlink()
                except Exception:
                    pass
            else:
                alpha_metrics: Optional[Dict[str, Any]] = None
                if asset_type == "portrait" and ensure_portrait_alpha:
                    try:
                        if not self.portrait_has_effective_alpha(cache_path):
                            await self.remove_background(cache_path, keep_source=True)
                        alpha_metrics = self._portrait_alpha_metrics(cache_path)
                        self._assert_portrait_transparency(alpha_metrics, cache_path)
                    except Exception as exc:
                        xlog.error(0, exc, "[image] cached portrait alpha repair failed path=%s", cache_path)
                        return {
                            "success": False,
                            "error": f"立绘透明背景处理失败: {exc}",
                            "image_path": str(cache_path),
                            "cached": True,
                        }
                cached_result: Dict[str, Any] = {
                    "success": True,
                    "image_url": f"{IMAGE_BASE_URL}/{asset_type}s/{cache_path.name}",
                    "image_path": str(cache_path),
                    "cached": True,
                    "seed": seed,
                    "size": self._actual_image_size(cache_path) or [width, height],
                    "prompt": prompt,
                }
                if alpha_metrics is not None:
                    cached_result["portrait_alpha"] = alpha_metrics
                    cached_result["portrait_generation_contract_version"] = PORTRAIT_GENERATION_CONTRACT_VERSION
                return cached_result

        # 并发限制
        async with _image_semaphore:
            try:
                # 调用图像生成 API。_call_cogview_api 名称保留用于兼容旧测试和 mock。
                bg_kwargs = {} if asset_type != "background" else {
                    "forbidden_characters": forbidden_characters,
                    "scene_name": scene_name,
                    "scene_description": scene_description,
                    "forbidden_entities": forbidden_entities,
                }
                provider_kwargs = dict(bg_kwargs)
                if authoritative_prompt:
                    # A content-filter retry would mutate the LLM-authored
                    # prompt. Provider-side prompt extension and the global
                    # negative prompt would also add backend-authored semantics.
                    # Disable all three so the image request is governed only
                    # by the validated LLM final_prompt.
                    provider_kwargs["retry_with_sanitized"] = False
                    provider_kwargs["authoritative_prompt"] = True
                image_data = await self._call_cogview_api(
                    prompt, width, height, seed, **provider_kwargs,
                )

                if image_data is None:
                    return {"success": False, "error": "图像生成 API 调用失败"}

                # 在覆盖缓存前完整解码响应，并通过临时文件原子落盘。
                await asyncio.to_thread(
                    self._write_valid_image_bytes,
                    cache_path,
                    image_data,
                )

                alpha_metrics: Optional[Dict[str, Any]] = None
                if asset_type == "portrait" and ensure_portrait_alpha:
                    try:
                        # The configured provider currently has watermark disabled. Only
                        # crop when it was explicitly requested, and only on a fresh file.
                        if AI_IMAGE_WATERMARK:
                            await asyncio.to_thread(self.crop_watermark, cache_path)
                        if not self.portrait_has_effective_alpha(cache_path):
                            await self.remove_background(cache_path, keep_source=True)
                        alpha_metrics = self._portrait_alpha_metrics(cache_path)
                        self._assert_portrait_transparency(alpha_metrics, cache_path)
                    except Exception as exc:
                        xlog.error(0, exc, "[image] portrait alpha postprocess failed path=%s", cache_path)
                        return {
                            "success": False,
                            "error": f"立绘透明背景处理失败: {exc}",
                            "image_path": str(cache_path),
                            "cached": False,
                        }

                generated_result: Dict[str, Any] = {
                    "success": True,
                    "image_url": f"{IMAGE_BASE_URL}/{asset_type}s/{cache_path.name}",
                    "image_path": str(cache_path),
                    "cached": False,
                    "seed": seed,
                    "size": self._actual_image_size(cache_path) or [width, height],
                    "prompt": prompt,
                }
                if alpha_metrics is not None:
                    generated_result["portrait_alpha"] = alpha_metrics
                    generated_result["portrait_generation_contract_version"] = PORTRAIT_GENERATION_CONTRACT_VERSION
                return generated_result

            except Exception as e:
                xlog.error(0, e, "[image] generate failed asset_type=%s", asset_type)
                return {"success": False, "error": f"图像生成失败: {str(e)}"}

    def _sanitize_prompt(self, prompt: str) -> str:
        """
        清理 prompt 中的敏感词，避免触发 CogView-4 内容审核。

        用正则做大小写不敏感整词替换；同时覆盖中英文。
        命中但无显式替换的，统一替换为 'object' / 通用温和词。
        """
        import re

        # (pattern, replacement) — pattern 用 re.IGNORECASE 做整词匹配
        # 按长度降序排，避免 "sword" 先被 "word" 子串吃掉之类的问题
        sensitive_pairs: list[tuple[str, str]] = [
            # 暴力 - 英文
            (r"\bkill(?:ing|er|s)?\b", "defeat"),
            (r"\bmurder(?:er|ed|ing)?\b", "conflict"),
            (r"\bdeath\b", "end"),
            (r"\bdead\b", "still"),
            (r"\bblood(?:y)?\b", "crimson"),
            (r"\bgore\b", "drama"),
            (r"\bviolent\b", "dramatic"),
            (r"\bviolence\b", "drama"),
            (r"\bweapon(?:s|ry)?\b", "equipment"),
            (r"\bguns?\b", "device"),
            (r"\bswords?\b", "blade"),
            (r"\bblades?\b", "blade"),
            (r"\bknives\b", "tools"),
            (r"\bknife\b", "tool"),
            (r"\bfight(?:ing|s)?\b", "confrontation"),
            (r"\bcombat(?:ant)?\b", "encounter"),
            (r"\bwars?\b", "conflict"),
            (r"\bwartime\b", "troubled times"),
            (r"\bbattles?\b", "struggle"),
            (r"\bbattlefield\b", "open field"),
            (r"\bassassin(?:ate|ation)?\b", "agent"),
            (r"\battack(?:er|ing|s)?\b", "approach"),
            (r"\bassault\b", "clash"),
            (r"\bbehead(?:ing)?\b", "fall"),
            (r"\bcorpse\b", "figure"),
            (r"\bclash(?:es|ing)?\b", "encounter"),
            (r"\bsoldiers?\b", "guard"),
            (r"\bwarriors?\b", "fighter"),
            (r"\barmy\b", "force"),
            (r"\barmy\b", "force"),
            (r"\btroops?\b", "group"),
            (r"\barchers?\b", "scout"),
            (r"\bsiege\b", "blockade"),
            (r"\binvasion\b", "incursion"),
            (r"\bslaughter\b", "struggle"),
            (r"\bmassacre\b", "tragedy"),
            (r"\bexecut(?:e|ion)\b", "judgment"),
            (r"\bwound(?:ed|s)?\b", "injury"),
            (r"\bstab(?:bing|bed)?\b", "thrust"),
            (r"\bshoot(?:ing)?\b", "act"),
            # 敏感场景
            (r"\btortur(?:e|ed|ing)\b", "torment"),
            (r"\bpain(?:ful)?\b", "strain"),
            (r"\bsuffering\b", "hardship"),
            (r"\bdying\b", "fading"),
            (r"\bsuicide\b", "despair"),
            # 过度暴露
            (r"\bnude\b", "unclothed"),
            (r"\bnaked\b", "unclothed"),
            (r"\bporn(?:ography)?\b", "art"),
            (r"\bsexual\b", "romantic"),
            (r"\bexplicit\b", "detailed"),
            # 其他
            (r"\bdrug(?:s)?\b", "substance"),
            (r"\bpoison(?:ous)?\b", "toxin"),
            (r"\bterrorist\b", "extremist"),
            (r"\bbomb(?:ing)?\b", "blast"),
            (r"\bexplosion\b", "burst"),
            # 暴力 - 中文（直接整串替换）
            ("战场", "前沿阵地"),
            ("战争", "纷争"),
            ("战斗", "交锋"),
            ("厮杀", "交锋"),
            ("杀戮", "激战"),
            ("杀", "伐"),
            ("血", "朱红"),
            ("尸", "倒影"),
            ("剑", "刃"),
            ("刀", "刃"),
            ("枪", "矛"),
            ("箭", "矢"),
            ("刺杀", "突击"),
            ("暗杀", "袭击"),
            ("处死", "判决"),
            ("死亡", "终结"),
            ("死", "殁"),
            ("亡", "逝"),
            ("屠", "伐"),
            ("刑", "律"),
            ("叛", "异"),
            ("淫", "艳"),
            ("裸", "袒"),
        ]

        sanitized = prompt
        changed = False
        for pattern, replacement in sensitive_pairs:
            new_san, n = re.subn(pattern, replacement, sanitized, flags=re.IGNORECASE)
            if n > 0:
                sanitized = new_san
                changed = True
                print(f"[ImageGen] 敏感词替换: '{pattern}' -> '{replacement}' (x{n})")

        if changed:
            xlog.info(0, "[image] sanitize preview head=%s", sanitized[:200])
        return sanitized

    def _is_content_filter_error(self, error: Exception) -> bool:
        """检查是否为内容审核错误"""
        if isinstance(error, _ImageProviderError):
            return error.content_filter
        error_str = str(error)
        return _is_provider_content_filter_message(error_str)

    async def _call_cogview_api(
        self,
        prompt: str,
        width: int,
        height: int,
        seed: Optional[int] = None,
        retry_with_sanitized: bool = True,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
        authoritative_prompt: bool = False,
    ) -> Optional[bytes]:
        """
        调用图像生成 API。

        方法名保留旧称，兼容现有测试与 monkeypatch。qwen-image 系列走
        DashScope multimodal-generation HTTP 接口；其他模型继续走旧 OpenAI
        SDK 兼容 images.generate 分支。
        外部请求和 URL 下载都是网络 IO，使用 aiohttp/AsyncOpenAI 直接让出事件循环。
        rembg/Pillow 这类本地同步处理在各自调用点继续放到线程池。

        Args:
            forbidden_characters: 仅 background 用。sanitize retry 和 safety fallback
                路径重新构建 prompt 时仍要保持 BG-ENFORCE 约束。
            scene_name / scene_description: 仅 background 用。透传给 enforce，
                让 sanitize retry / safety fallback 的 human_atmosphere 判断一致。
            authoritative_prompt: 禁止供应商自动扩写、全局 negative_prompt 和
                内容审核后的字符串替换重试，保持 LLM final_prompt 原样生效。
        """
        try:
            if not api_key_available(
                AI_IMAGE_API_KEY,
                pool_env="AI_IMAGE_API_KEYS",
                allow_byok=False,
            ):
                xlog.warn(0, "[image] AI_IMAGE_API_KEY not configured")
                return None

            if self._is_qwen_image_model(AI_IMAGE_MODEL):
                if authoritative_prompt:
                    return await self._call_qwen_image_api(
                        prompt,
                        width,
                        height,
                        seed,
                        exact_prompt=True,
                    )
                return await self._call_qwen_image_api(prompt, width, height, seed)

            return await self._call_openai_compatible_image_api(prompt, width, height)

        except Exception as e:
            xlog.warn(
                0,
                "[image] provider request failed model=%s size=%dx%d category=%s retryable=%s prompt_bytes=%d err=%s",
                AI_IMAGE_MODEL,
                width,
                height,
                _provider_error_category(e),
                _provider_error_retryable(e),
                len(prompt.encode("utf-8")),
                str(e),
            )

            # 检查是否为内容审核错误，尝试用清理后的 prompt 重试
            if retry_with_sanitized and self._is_content_filter_error(e):
                xlog.warn(0, "[image] content filter error, retry with sanitized prompt")
                sanitized_prompt = self._sanitize_prompt(prompt)
                if sanitized_prompt != prompt:
                    xlog.warn(0, "[image] retry with sanitized prompt")
                    # BG-ENFORCE: sanitize 后必须重新过 enforce，保持环境主体约束 +
                    # BG-DEFAULT-NO-PEOPLE: 复用 human_atmosphere 判断
                    # Stage_Background_Entity_Exclusion: forbidden_entities 必须透传，
                    # 否则 sanitize 后重试会丢掉角色禁令，导致非人类角色泄漏进背景。
                    if (
                        forbidden_characters is not None
                        or scene_name is not None
                        or scene_description is not None
                        or forbidden_entities is not None
                    ):
                        sanitized_prompt = self._enforce_background_environment_focus(
                            sanitized_prompt,
                            forbidden_characters=forbidden_characters,
                            scene_name=scene_name,
                            scene_description=scene_description,
                            forbidden_entities=forbidden_entities,
                        )
                    try:
                        return await self._call_cogview_api(
                            sanitized_prompt, width, height, seed,
                            retry_with_sanitized=False,
                            forbidden_characters=forbidden_characters,
                            scene_name=scene_name,
                            scene_description=scene_description,
                            forbidden_entities=forbidden_entities,
                        )
                    except Exception as e2:
                        if not self._is_content_filter_error(e2):
                            raise
                        xlog.warn(0, "[image] sanitized prompt still filtered, fall back to landscape prompt")

                # 三级兜底：环境主体模板 prompt
                # 不再用"古风宫殿"写死，改为通用环境模板，避免机甲/科幻题材落到错风格
                safe_prompt = self._build_safety_fallback_background_prompt(
                    forbidden_characters,
                    scene_name=scene_name,
                    scene_description=scene_description,
                    genre=self.infer_genre(scene_description or scene_name or ""),
                    forbidden_entities=forbidden_entities,
                )
                xlog.warn(0, "[image] retry with landscape fallback prompt")
                return await self._call_cogview_api(
                    safe_prompt, width, height, seed,
                    retry_with_sanitized=False,
                    forbidden_characters=forbidden_characters,
                    scene_name=scene_name,
                    scene_description=scene_description,
                    forbidden_entities=forbidden_entities,
                )

            return None

    def _is_qwen_image_model(self, model: str) -> bool:
        return (model or "").strip().lower().startswith("qwen-image")

    # Stage_Keyframe_Identity_AR_Blueprint C02/C03/C04 ===========================

    def _to_sendable_image_data(self, image_url: str) -> Optional[str]:
        """C03 — 把本地 URL 转成可发送给外部图像服务的格式。

        蓝图 §7 硬约束：本地 only 路径（``/static/assets/...`` / ``localhost`` /
        ``127.0.0.1``）禁止直接传给外部服务。识别到这些前缀时，读取本地文件
        并编码为 ``data:image/png;base64,...``；已经是对外可访问 URL（http(s)://
        非 localhost）则原样返回。
        """
        if not image_url:
            return None
        url = str(image_url).strip()
        if not url:
            return None

        lower = url.lower()
        is_remote_http = lower.startswith(("http://", "https://")) and "localhost" not in lower and "127.0.0.1" not in lower
        if is_remote_http:
            return url

        # 本地路径解析
        local_path: Optional[Path] = None
        if lower.startswith("/static/"):
            # /static/assets/... → backend/static/assets/...
            local_path = BACKEND_ROOT / url.lstrip("/")
        elif lower.startswith(("http://localhost", "https://localhost", "http://127.0.0.1", "https://127.0.0.1")):
            # 从 URL 抽取 path 部分
            from urllib.parse import urlparse
            parsed = urlparse(url)
            local_path = BACKEND_ROOT / "static" / parsed.path.lstrip("/static/").lstrip("/")
        elif not lower.startswith(("http://", "https://")):
            # 可能是 repo 相对路径或绝对文件路径
            candidate = Path(url)
            local_path = candidate if candidate.is_absolute() else (BACKEND_ROOT / url.lstrip("./"))

        if local_path is None or not local_path.exists():
            xlog.warn(0, "[image] cannot resolve local image for external service url=%s", url)
            return None

        try:
            data_bytes = local_path.read_bytes()
        except OSError as e:
            xlog.warn(0, "[image] read local image failed path=%s err=%s", local_path, e)
            return None

        suffix = local_path.suffix.lower().lstrip(".")
        mime = "image/png" if suffix in {"png", ""} else f"image/{suffix}"
        encoded = base64.b64encode(data_bytes).decode("ascii")
        return f"data:{mime};base64,{encoded}"

    def _resolve_keyframe_reference_model(self) -> str:
        """C02 — 关键帧参考图专用模型；空时回退到全局 AI_IMAGE_MODEL。"""
        model = (get_settings().keyframe_image_model or "").strip()
        return model or AI_IMAGE_MODEL

    def _verify_multi_image_support(
        self,
        *,
        model: str,
        reference_count: int,
    ) -> tuple[bool, str]:
        """C02 — 调用图像编辑接口前核对多图输入支持情况。

        返回 ``(ok, reason)``。``ok=False`` 时调用方必须拒绝生成并走兜底，
        禁止静默回退到无参考图。当前实现是受控词表白名单：
        - qwen-image-2 / qwen-image-edit / qwen-image-plus / qwen-vl 走 DashScope
          ``/services/aigc/multimodal-generation/generation``。
        - gpt-image-1 / gpt-image-2 走 OpenAI ``/images/edits`` 多图编辑。
        其余模型（含未配置 / 不在白名单内）一律拒绝。
        """
        if reference_count <= 0:
            return True, "no_references_needed"
        m = (model or "").strip().lower()
        if not m:
            return False, "model_not_configured"
        max_refs = get_settings().keyframe_reference_max_images
        # qwen-image-2 / qwen-image-edit / qwen-image-plus 系列支持多图输入
        if any(token in m for token in ("qwen-image-2", "qwen-image-edit", "qwen-image-plus", "qwen2.5-vl", "qwen-vl")):
            if reference_count > max_refs:
                return False, f"too_many_references:{reference_count}"
            return True, "supported_qwen_multimodal"
        # gpt-image-1 / gpt-image-2 通过 OpenAI /images/edits 支持多图编辑
        if "gpt-image-" in m:
            if reference_count > max_refs:
                return False, f"too_many_references:{reference_count}"
            return True, "supported_openai_edits"
        return False, f"model_does_not_support_multi_image:{m}"

    def _is_openai_edits_model(self, model: str) -> bool:
        m = (model or "").strip().lower()
        return "gpt-image-" in m

    async def _call_qwen_image_with_references_api(
        self,
        *,
        prompt: str,
        reference_images: List[str],
        width: int,
        height: int,
        seed: Optional[int],
        model: str,
    ) -> Optional[bytes]:
        """C04 内部实现 — Qwen 多图参考生成接口。

        ``reference_images`` 已经是 sendable 格式（data URL 或对外 URL），
        通过 :meth:`_to_sendable_image_data` 转换。请求 payload 把所有参考图
        作为 ``content`` 数组里的 ``image`` 项，最后跟一个 ``text`` 项。
        """
        import aiohttp

        endpoint = self._build_qwen_image_endpoint()
        content: List[Dict[str, Any]] = [{"image": img} for img in reference_images]
        content.append({"text": prompt})
        payload = {
            "model": model,
            "input": {
                "messages": [
                    {"role": "user", "content": content},
                ]
            },
            "parameters": self._build_qwen_image_parameters(width, height, seed),
        }
        xlog.info(
            0,
            "[image] keyframe-with-refs request start model=%s refs=%d size=%dx%d",
            model,
            len(reference_images),
            width,
            height,
        )

        candidates = self._image_key_pool.get_candidates(None)
        if not candidates:
            raise ApiKeyPoolUnavailable()

        data: Optional[Dict[str, Any]] = None
        last_error: Optional[Exception] = None
        async with aiohttp.ClientSession() as session:
            for lease in candidates:
                try:
                    async with session.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {lease.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=180),
                    ) as response:
                        response_text = await response.text()
                        if response.status < 200 or response.status >= 300:
                            raise RuntimeError(
                                f"qwen-image-with-refs http {response.status}: {response_text[:200]}"
                            )
                        data = json.loads(response_text)
                    self._image_key_pool.report_success(lease)
                    break
                except Exception as e:
                    last_error = e
                    try:
                        self._image_key_pool.report_failure(lease)
                    except Exception:
                        pass
                    continue

        if data is None:
            if last_error is not None:
                raise last_error
            raise ApiKeyPoolUnavailable()

        image_url = self._extract_qwen_image_url(data)
        if not image_url:
            xlog.warn(0, "[image] qwen-image-with-refs response has no image url")
            return None
        return await self._download_image_url(image_url)

    async def _call_openai_image_edit_api(
        self,
        *,
        prompt: str,
        reference_images: List[str],
        width: int,
        height: int,
        seed: Optional[int],
        model: str,
    ) -> Optional[bytes]:
        """OpenAI ``/images/edits`` adapter for reference-image editing.

        ``reference_images`` are sendable forms (data URL or http URL) produced
        by :meth:`_to_sendable_image_data`. The endpoint is the OpenAI
        compatible ``/images/edits`` on :data:`AI_IMAGE_BASE_URL`. Each
        reference image is uploaded as a separate ``image`` multipart field;
        ``gpt-image-2`` accepts up to N reference images (gated by the
        capability whitelist upstream).
        """
        import aiohttp

        base_url = (AI_IMAGE_BASE_URL or "").rstrip("/")
        if not base_url:
            raise RuntimeError("AI_IMAGE_BASE_URL not configured")
        if base_url.endswith("/v1"):
            endpoint = f"{base_url}/images/edits"
        else:
            endpoint = f"{base_url}/v1/images/edits"

        size_str = self._openai_image_size_label(width, height)

        # Build multipart body with one image[] field per reference.
        boundary = "----ifline-kf-" + hashlib.sha1(
            f"{model}|{seed}|{time.time()}".encode()
        ).hexdigest()
        crlf = b"\r\n"
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(f"--{boundary}".encode())
            parts.append(
                f'Content-Disposition: form-data; name="{name}"'.encode()
            )
            parts.append(b"")
            parts.append(value.encode())

        add_field("model", model)
        add_field("prompt", prompt)
        add_field("n", "1")
        if size_str:
            add_field("size", size_str)

        for idx, ref in enumerate(reference_images):
            payload_bytes, mime, ext = self._decode_sendable_image(ref)
            if payload_bytes is None:
                xlog.warn(0, "[image] openai-edits cannot decode ref idx=%d", idx)
                continue
            filename = f"ref_{idx}.{ext}"
            parts.append(f"--{boundary}".encode())
            parts.append(
                (
                    f'Content-Disposition: form-data; name="image"; '
                    f'filename="{filename}"'
                ).encode()
            )
            parts.append(f"Content-Type: {mime}".encode())
            parts.append(b"")
            parts.append(payload_bytes)

        parts.append(f"--{boundary}--".encode())
        parts.append(b"")
        body = crlf.join(parts)

        candidates = self._image_key_pool.get_candidates(None)
        if not candidates:
            raise ApiKeyPoolUnavailable()

        xlog.info(
            0,
            "[image] openai-edits request start model=%s refs=%d size=%s endpoint=%s",
            model,
            len(reference_images),
            size_str or "auto",
            endpoint,
        )

        last_error: Optional[Exception] = None
        last_status = 0
        last_body = ""
        async with aiohttp.ClientSession() as session:
            for lease in candidates:
                try:
                    async with session.post(
                        endpoint,
                        headers={
                            "Authorization": f"Bearer {lease.api_key}",
                            "Content-Type": f"multipart/form-data; boundary={boundary}",
                        },
                        data=body,
                        timeout=aiohttp.ClientTimeout(total=300),
                    ) as response:
                        last_status = response.status
                        response_text = await response.text()
                        if response.status < 200 or response.status >= 300:
                            last_body = response_text[:600]
                            raise RuntimeError(
                                f"openai-image-edit http {response.status}: {response_text[:300]}"
                            )
                        try:
                            data = json.loads(response_text)
                        except json.JSONDecodeError as e:
                            last_body = response_text[:300]
                            raise RuntimeError(f"openai-image-edit non-json: {e}") from e
                        items = data.get("data") or []
                        if not items:
                            raise RuntimeError("openai-image-edit empty data")
                        item = items[0]
                        b64 = item.get("b64_json")
                        if b64:
                            try:
                                self._image_key_pool.report_success(lease)
                            except Exception:
                                pass
                            return base64.b64decode(b64)
                        url = item.get("url")
                        if url:
                            try:
                                self._image_key_pool.report_success(lease)
                            except Exception:
                                pass
                            return await self._download_image_url(url)
                        raise RuntimeError("openai-image-edit missing image payload")
                except Exception as e:
                    last_error = e
                    try:
                        self._image_key_pool.report_failure(lease)
                    except Exception:
                        pass
                    continue

        if last_error is not None:
            xlog.error(
                0,
                last_error,
                "[image] openai-edits failed model=%s status=%d body=%s",
                model,
                last_status,
                last_body,
            )
        return None

    @staticmethod
    def _decode_sendable_image(
        ref: str,
    ) -> Tuple[Optional[bytes], str, str]:
        """Decode a data URL / http URL reference into raw bytes + mime + ext.

        Returns ``(None, "", "")`` when the reference cannot be resolved
        synchronously (e.g. remote http URL that would need async download).
        The OpenAI edits adapter only accepts data URLs here; remote URLs
        should be pre-converted by ``_to_sendable_image_data`` upstream.
        """
        if not ref:
            return None, "", ""
        prefix = "data:"
        if ref.startswith(prefix):
            try:
                header, b64 = ref[len(prefix):].split(",", 1)
            except ValueError:
                return None, "", ""
            mime = "image/png"
            if ";" in header:
                mime = header.split(";", 1)[0] or mime
            ext = "png"
            if mime == "image/jpeg":
                ext = "jpg"
            elif mime == "image/webp":
                ext = "webp"
            try:
                return base64.b64decode(b64), mime, ext
            except Exception:
                return None, "", ""
        return None, "", ""

    @staticmethod
    def _openai_image_size_label(width: int, height: int) -> str:
        """Map (w, h) to the closest OpenAI ``size`` token. Returns "" for
        unknown shapes — gpt-image-2 will fall back to its default aspect."""
        sizes = [
            (1024, 1024, "1024x1024"),
            (1024, 1536, "1024x1536"),
            (1536, 1024, "1536x1024"),
            (1792, 1024, "1792x1024"),
            (1024, 1792, "1024x1792"),
        ]
        target_w = max(width, 1)
        target_h = max(height, 1)
        best = None
        best_diff = 1 << 30
        for w, h, label in sizes:
            diff = abs(w - target_w) + abs(h - target_h)
            if diff < best_diff:
                best_diff = diff
                best = label
        return best or ""

    async def generate_keyframe_with_references(
        self,
        *,
        event_name: str,
        scene_description: str,
        character_bindings: List["KeyframeCharacterBinding"],
        action: str,
        emotion: str,
        final_prompt: str,
        style_fingerprint: str,
        seed: Optional[int],
        width: int = 1024,
        height: int = 576,
    ) -> Dict[str, Any]:
        """C04 — 蓝图 §7：基于已通过立绘验收的参考图生成关键帧。

        与 ``generate_keyframe`` 的区别：
        * 调用图像编辑接口（qwen-image-2 / qwen-image-edit），传入 1-3 张参考图；
        * 本地图自动转 data URL（C03）；
        * 不命中多图能力时返回 ``success=False, error=multi_image_unsupported``，
          由上游决定是否走 sprite_composite 兜底；
        * 写入 ``generation_params.schema_version=keyframe-identity-v2``。
        """
        from app.services.keyframe_character_binding import (
            KeyframeCharacterBinding,
            pick_primary_bindings,
        )

        result: Dict[str, Any] = {
            "success": False,
            "event_name": event_name,
            "scene_description": scene_description,
            "render_mode": "generated_reference",
        }

        if not IMAGE_GENERATION_ENABLED:
            result["error"] = "image_generation_disabled"
            return result
        if not api_key_available(
            AI_IMAGE_API_KEY,
            pool_env="AI_IMAGE_API_KEYS",
            allow_byok=False,
        ):
            result["error"] = "image_api_key_not_configured"
            return result

        # C04 — 选主要 3 个角色
        primaries = pick_primary_bindings(
            [b for b in character_bindings if isinstance(b, KeyframeCharacterBinding)],
            max_count=get_settings().keyframe_reference_max_images,
        )

        # C02 — capability check
        model = self._resolve_keyframe_reference_model()
        ok, reason = self._verify_multi_image_support(model=model, reference_count=len(primaries))
        if not ok:
            xlog.warn(
                0,
                "[image] keyframe-with-refs unsupported model=%s refs=%d reason=%s",
                model,
                len(primaries),
                reason,
            )
            result["error"] = "multi_image_unsupported"
            result["reason"] = reason
            result["model"] = model
            return result

        # C03 — 转换本地参考图为 data URL
        sendable_refs: List[str] = []
        reference_image_sha_list: List[str] = []
        for idx, binding in enumerate(primaries, start=1):
            ref_url = binding.reference_image_url
            sendable = self._to_sendable_image_data(ref_url)
            if not sendable:
                xlog.warn(
                    0,
                    "[image] keyframe-with-refs cannot resolve ref url=%s char=%s",
                    ref_url,
                    binding.character_name,
                )
                continue
            sendable_refs.append(sendable)
            reference_image_sha_list.append(binding.reference_image_sha256 or "")

        if not sendable_refs:
            result["error"] = "no_resolvable_reference_images"
            return result

        width, height = self._validate_size(width, height)

        # C04 — 调用接口（按模型族分发：qwen 走 DashScope，gpt-image 走 OpenAI /images/edits）
        try:
            if self._is_openai_edits_model(model):
                image_bytes = await self._call_openai_image_edit_api(
                    prompt=final_prompt,
                    reference_images=sendable_refs,
                    width=width,
                    height=height,
                    seed=seed,
                    model=model,
                )
            else:
                image_bytes = await self._call_qwen_image_with_references_api(
                    prompt=final_prompt,
                    reference_images=sendable_refs,
                    width=width,
                    height=height,
                    seed=seed,
                    model=model,
                )
        except Exception as e:
            xlog.error(0, e, "[image] keyframe-with-refs call failed model=%s", model)
            result["error"] = f"api_call_failed:{type(e).__name__}"
            return result

        if not image_bytes:
            result["error"] = "empty_image_response"
            return result

        # 落盘到 keyframes 子目录
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_event = "".join(c if c.isalnum() else "_" for c in (event_name or "keyframe"))[:40]
        image_filename = f"kf_id_{timestamp}_{seed or 0}_{safe_event}.png"
        keyframes_dir = self.output_dir / "keyframes"
        keyframes_dir.mkdir(parents=True, exist_ok=True)
        image_path = keyframes_dir / image_filename
        try:
            image_path.write_bytes(image_bytes)
        except OSError as e:
            result["error"] = f"write_failed:{type(e).__name__}"
            return result

        # provider 可能忽略尺寸；按水印开关处理后等比归一化到请求画布。
        try:
            await asyncio.to_thread(
                self._postprocess_background_preserve_aspect,
                image_path,
                (width, height),
            )
        except Exception as e:
            xlog.warn(0, "[image] keyframe-with-refs postprocess failed path=%s err=%s", image_path, e)

        if self._actual_image_size(image_path) != [width, height]:
            result["error"] = "keyframe_postprocess_size_mismatch"
            try:
                image_path.unlink()
            except OSError:
                pass
            return result

        image_url = f"{IMAGE_BASE_URL}/keyframes/{image_filename}"

        result.update(
            {
                "success": True,
                "image_url": image_url,
                "image_path": str(image_path),
                "cached": False,
                "seed": seed,
                "size": self._actual_image_size(image_path) or [width, height],
                "model": model,
                "render_mode": "generated_reference",
                "reference_image_count": len(sendable_refs),
                "reference_image_sha_list": reference_image_sha_list,
                "character_bindings": [
                    {
                        "character_id": b.character_id,
                        "character_name": b.character_name,
                        "visual_fingerprint": b.visual_fingerprint,
                        "reference_asset_id": b.reference_asset_id,
                        "reference_image_sha256": b.reference_image_sha256,
                        "requested_emotion": b.requested_emotion,
                        "requested_outfit": b.requested_outfit,
                        "requested_pose": b.requested_pose,
                        "expected_position": b.expected_position,
                    }
                    for b in primaries
                ],
                "generation_params": {
                    "schema_version": "keyframe-identity-v2",
                    "render_mode": "generated_reference",
                    "model": model,
                    "seed": seed,
                    "style_fingerprint": style_fingerprint,
                    "identity_contract_version": get_settings().character_identity_contract_version,
                    "character_bindings": [
                        {
                            "character_id": b.character_id,
                            "character_name": b.character_name,
                            "visual_fingerprint": b.visual_fingerprint,
                            "reference_asset_id": b.reference_asset_id,
                            "reference_image_sha256": b.reference_image_sha256,
                            "requested_emotion": b.requested_emotion,
                            "requested_outfit": b.requested_outfit,
                            "requested_pose": b.requested_pose,
                            "expected_position": b.expected_position,
                        }
                        for b in primaries
                    ],
                    "reference_image_count": len(sendable_refs),
                    "identity_validation": {},
                    "identity_retry_count": 0,
                    "identity_fallback_used": False,
                    "final_prompt": final_prompt,
                },
            }
        )
        return result

    def _build_qwen_image_endpoint(self) -> str:
        base_url = (AI_IMAGE_BASE_URL or DEFAULT_QWEN_IMAGE_ENDPOINT).strip().rstrip("/")
        if not base_url:
            return DEFAULT_QWEN_IMAGE_ENDPOINT

        if base_url.endswith("/api/v1/services/aigc/multimodal-generation/generation"):
            return base_url

        if base_url.endswith("/api/v1"):
            return base_url + "/services/aigc/multimodal-generation/generation"

        if base_url.endswith("/compatible-mode/v1"):
            base_url = base_url[: -len("/compatible-mode/v1")]

        return base_url + "/api/v1/services/aigc/multimodal-generation/generation"

    def _build_qwen_image_parameters(
        self,
        width: int,
        height: int,
        seed: Optional[int],
        *,
        exact_prompt: bool = False,
    ) -> Dict[str, Any]:
        parameters: Dict[str, Any] = {
            "size": f"{width}*{height}",
            "n": 1,
            "prompt_extend": False if exact_prompt else AI_IMAGE_PROMPT_EXTEND,
            "watermark": AI_IMAGE_WATERMARK,
        }

        if AI_IMAGE_NEGATIVE_PROMPT and not exact_prompt:
            parameters["negative_prompt"] = AI_IMAGE_NEGATIVE_PROMPT

        if seed is not None:
            try:
                seed_value = int(seed)
            except (TypeError, ValueError):
                xlog.warn(0, "[image] qwen seed ignored invalid=%s", seed)
                seed_value = None

            if seed_value is not None:
                if 0 <= seed_value <= 2147483647:
                    parameters["seed"] = seed_value
                else:
                    xlog.warn(0, "[image] qwen seed ignored out_of_range=%s", seed)

        return parameters

    async def _call_qwen_image_api(
        self,
        prompt: str,
        width: int,
        height: int,
        seed: Optional[int],
        *,
        exact_prompt: bool = False,
    ) -> Optional[bytes]:
        import aiohttp

        endpoint = self._build_qwen_image_endpoint()
        payload = {
            "model": AI_IMAGE_MODEL,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"text": prompt}
                        ],
                    }
                ]
            },
            "parameters": self._build_qwen_image_parameters(
                width,
                height,
                seed,
                exact_prompt=exact_prompt,
            ),
        }
        xlog.info(
            0,
            "[image] request start provider=qwen model=%s size=%dx%d prompt_bytes=%d",
            AI_IMAGE_MODEL,
            width,
            height,
            len(prompt.encode("utf-8")),
        )

        candidates = self._image_key_pool.get_candidates(None)
        if not candidates:
            raise ApiKeyPoolUnavailable()

        data: Dict[str, Any] | None = None
        last_error: Exception | None = None
        async with aiohttp.ClientSession() as session:
            for lease in candidates:
                raw_error: Exception | None = None
                try:
                    with provider_span(
                        "image qwen.generate",
                        {
                            "gen_ai.system": "aliyun-dashscope",
                            "gen_ai.operation.name": "image.generate",
                            "image.provider": "qwen",
                            "image.model": AI_IMAGE_MODEL,
                            "image.width": width,
                            "image.height": height,
                            "image.credential_source": lease.source,
                        },
                    ) as span:
                        async with session.post(
                            endpoint,
                            headers={
                                "Authorization": f"Bearer {lease.api_key}",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=180),
                        ) as response:
                            response_text = await response.text()
                            if span is not None:
                                span.set_attribute("http.response.status_code", response.status)
                            if response.status < 200 or response.status >= 300:
                                raise _ImageProviderError(
                                    category=_image_provider_category(response.status),
                                    status_code=response.status,
                                    content_filter=_is_provider_content_filter_message(response_text),
                                )

                        try:
                            data = json.loads(response_text)
                        except json.JSONDecodeError:
                            raise _ImageProviderError(
                                category="invalid_response",
                                status_code=502,
                            ) from None

                        if data.get("code"):
                            message = data.get("message") or data.get("code")
                            # A 2xx provider-level rejection is normally prompt or
                            # parameter specific, so changing credentials would only
                            # duplicate cost and traffic.
                            raise _ImageProviderError(
                                category=(
                                    "content_filter"
                                    if _is_provider_content_filter_message(str(message))
                                    else "request_rejected"
                                ),
                                status_code=400,
                                content_filter=_is_provider_content_filter_message(str(message)),
                            )
                        if span is not None:
                            request_id = data.get("request_id") or data.get("requestId")
                            if request_id:
                                span.set_attribute("image.provider.request_id", str(request_id))
                except Exception as exc:
                    raw_error = exc

                if raw_error is not None:
                    decision = self._image_key_pool.report_failure(lease, raw_error)
                    last_error = _ImageProviderError(
                        category=decision.category,
                        status_code=decision.status_code or 502,
                        content_filter=self._is_content_filter_error(raw_error),
                    )
                    raw_error = None
                    if decision.retryable:
                        continue
                    raise last_error from None

                self._image_key_pool.report_success(lease)
                break

        if data is None:
            if last_error is not None:
                raise last_error
            raise ApiKeyPoolUnavailable()

        image_url = self._extract_qwen_image_url(data)
        if not image_url:
            xlog.warn(0, "[image] qwen response has no image url")
            return None

        return await self._download_image_url(image_url)

    async def _call_openai_compatible_image_api(
        self,
        prompt: str,
        width: int,
        height: int,
    ) -> Optional[bytes]:
        if self._openai_image_client is None:
            self._openai_image_client = PooledAsyncOpenAI(
                api_key=AI_IMAGE_API_KEY,
                pool_env="AI_IMAGE_API_KEYS",
                allow_byok=False,
                base_url=AI_IMAGE_BASE_URL,
            )
        client = self._openai_image_client

        provider_size = self._openai_image_size_label(width, height)
        params = {
            "model": AI_IMAGE_MODEL,
            "prompt": prompt,
            "size": provider_size,
            "n": 1
        }

        xlog.info(
            0,
            "[image] request start provider=openai_compatible model=%s "
            "target_size=%dx%d provider_size=%s prompt_bytes=%d",
            AI_IMAGE_MODEL,
            width,
            height,
            provider_size,
            len(prompt.encode("utf-8")),
        )

        with provider_span(
            "image openai_compatible.generate",
            {
                "gen_ai.system": "openai-compatible",
                "gen_ai.operation.name": "image.generate",
                "image.provider": "openai_compatible",
                "image.model": AI_IMAGE_MODEL,
                "image.width": width,
                "image.height": height,
                "image.provider.size": provider_size,
            },
        ):
            response = await client.images.generate(**params)

        xlog.debug(0, "[image] response debug type=%s", type(response))
        if response.data:
            xlog.debug(0, "[image] response debug data_count=%d", len(response.data))
            first_item = response.data[0]
            xlog.debug(0, "[image] response debug first_item_type=%s", type(first_item))
            xlog.debug(0, "[image] response debug has_b64_json=%s", hasattr(first_item, "b64_json"))
            if hasattr(first_item, "b64_json"):
                xlog.debug(0, "[image] response debug b64_json_is_none=%s", first_item.b64_json is None)
                if first_item.b64_json:
                    xlog.debug(0, "[image] response debug b64_json_len=%d", len(first_item.b64_json))
            if hasattr(first_item, "url"):
                xlog.debug(0, "[image] response debug url_prefix=%s", first_item.url[:50] if first_item.url else "None")

        if not response.data or len(response.data) == 0:
            xlog.warn(0, "[image] empty response data")
            return None

        first_data = response.data[0]

        if hasattr(first_data, "b64_json") and first_data.b64_json:
            image_data = base64.b64decode(first_data.b64_json)
            xlog.info(0, "[image] request ok source=b64_json bytes=%d", len(image_data))
            return image_data

        if hasattr(first_data, "url") and first_data.url:
            return await self._download_image_url(first_data.url)

        xlog.warn(0, "[image] response data has no b64_json or url")
        return None

    async def _download_image_url(self, image_url: str) -> Optional[bytes]:
        import aiohttp

        xlog.info(0, "[image] download start source=url")
        async with aiohttp.ClientSession() as session:
            with provider_span(
                "image download",
                {
                    "gen_ai.operation.name": "image.download",
                    "image.provider": "download_url",
                },
            ) as span:
                async with session.get(
                    image_url,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as img_response:
                    if span is not None:
                        span.set_attribute("http.response.status_code", img_response.status)
                    if img_response.status == 200:
                        content = await img_response.read()
                        if span is not None:
                            span.set_attribute("image.bytes", len(content))
                        xlog.info(0, "[image] download ok bytes=%d", len(content))
                        return content

                    xlog.warn(0, "[image] download failed status=%d", img_response.status)
                    return None

    def _extract_qwen_image_url(self, data: Dict[str, Any]) -> Optional[str]:
        choices = data.get("output", {}).get("choices", [])
        if not isinstance(choices, list):
            return None

        for choice in choices:
            if not isinstance(choice, dict):
                continue

            message = choice.get("message", {})
            content = message.get("content", [])
            if not isinstance(content, list):
                continue

            for item in content:
                if not isinstance(item, dict):
                    continue

                image_url = item.get("image")
                if isinstance(image_url, str) and image_url:
                    return image_url

        return None

    def _extract_error_message(self, response_text: str) -> str:
        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            return response_text[:500]

        code = data.get("code")
        message = data.get("message")
        if code and message:
            return f"{code}: {message}"

        if message:
            return str(message)

        if code:
            return str(code)

        return response_text[:500]

    def _build_safety_fallback_background_prompt(
        self,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        genre: Optional[str] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        BG-ENFORCE / BG-DEFAULT-NO-PEOPLE — 内容审核失败后的安全 fallback prompt。

        必须满足：
        - 环境主体（不能是 hero / portrait / character-focused）
        - 默认无人：除非 scene 类型明确允许公共活动陪体
        - 强制排除本章角色
        - 不含 hero/protagonist/full body/detailed costume 等会导致人物主体化的词
        - 2026-06-15: 按 genre 选 bg_style，不再硬编码 anime
        - Stage_Background_Entity_Exclusion: forbidden_entities 必须透传，
          fallback prompt 同样禁止所有剧情角色实体（包括机器人/动物/怪物）。
        """
        bg_style = self._get_genre_bg_style(genre)
        base = (
            "中性环境背景，地点建立镜头，"
            "16:9宽幅构图，广角环境远景，"
            "以建筑、空间和氛围作为画面主体，"
            "电影感光线，"
            f"{bg_style}, "
            "环境细节丰富，只呈现建筑、自然元素、道具、光线和氛围，"
            "不要文字，不要水印"
        )
        return self._enforce_background_environment_focus(
            base,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
            forbidden_entities=forbidden_entities,
        )

    def _scene_aware_safety_fallback(
        self,
        spec: "BackgroundSceneSpec",
    ):
        """
        Stage_Background_AR L5.01 — scene-aware safety fallback.

        validator hard_fail 时的最后兜底：用 spec 的环境字段构造一个
        「完全无人 + 强制 empty_required」的 safety prompt，并强制把
        people_policy.mode 设为 empty_required（safety 路径不允许任何人物）。

        Returns:
            (prompt, spec): prompt 是过 _enforce_background_environment_focus
            的 safety prompt; spec 是 people_policy.mode 被强制为 empty_required
            的副本（不 mutate 入参）。
        """
        import copy as _copy

        out_spec = _copy.deepcopy(spec)
        out_spec.people_policy.mode = "empty_required"

        base = (
            f"{spec.scene_name or 'scene'}. "
            f"{spec.environment_description or '空环境'}. "
            f"{spec.atmosphere or ''}".strip()
        )

        prompt = self._enforce_background_environment_focus(
            base,
            spec=out_spec,
            forbidden_characters=spec.forbidden_characters,
        )
        return prompt, out_spec

    def enforce_portrait_prompt(
        self,
        prompt: str,
        *,
        pose: Optional[str] = None,
        full_body: Optional[bool] = None,
    ) -> str:
        """Apply one deterministic presentation contract to every portrait entry point.

        The direct v2 renderer accepts free-form prompts and bypasses the structured
        portrait builder, so this guard belongs at the provider boundary. It is
        intentionally idempotent because structured portrait generation calls the
        same boundary after adding pose/framing context.
        """
        text = re.sub(r"\s+", " ", (prompt or "").strip())
        lowered = text.lower()
        has_complete_contract = all(
            fragment in lowered
            for fragment in (
                _PORTRAIT_CONTRACT_MARKER.lower(),
                "transparent background contract",
                "natural spine, shoulder, hip, elbow, wrist and hand mechanics",
                "avoid rigid symmetrical mannequin posture",
            )
        )
        if has_complete_contract:
            return text
        # A caller may accidentally (or deliberately) include the marker alone.
        # Remove an incomplete marker so the provider prompt contains one clear
        # authoritative contract instead of appearing guarded when it is not.
        text = re.sub(
            rf"\b{re.escape(_PORTRAIT_CONTRACT_MARKER)}\b\s*[:,]?",
            "",
            text,
            flags=re.IGNORECASE,
        )

        replacements = (
            (
                r"\bstanding pose\s*,?\s*upright posture\b",
                "relaxed standing pose with weight shifted naturally onto one leg, "
                "subtle shoulder and hip asymmetry, relaxed arms and hands",
            ),
            (
                r"\bseated pose\s*,?\s*sitting upright on a chair\s*,?\s*formal seated posture\b",
                "naturally seated pose with settled body weight, relaxed shoulders, "
                "subtle asymmetry in the torso and hands, no visible furniture",
            ),
            (
                r"\btransparent full[- ]body sprite\b",
                "transparent character sprite",
            ),
            (
                r"\bwearing\s+(?:a\s+)?default(?:\s+(?:outfit|clothes|attire))?\b\s*,?",
                "",
            ),
            (
                r"\bcharacter centered\b(?!\s+with a subtle off-axis torso turn)",
                "character centered with a subtle off-axis torso turn",
            ),
        )
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Structured portrait generation knows the requested crop. Remove the
        # opposite crop left behind by an LLM rewrite before adding the contract.
        if full_body is False:
            for pattern in (
                r"\bfull[- ]body shot\b",
                r"\bentire character visible from head to feet\b",
                r"\bhead to feet visible\b",
                r"\bvertical composition\b",
            ):
                text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        elif full_body is True:
            for pattern in (
                r"\bhalf[- ]body shot from waist up\b",
                r"\bwaist[- ]up shot\b",
            ):
                text = re.sub(pattern, "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s+,", ",", text)
        text = re.sub(r",\s*,+", ",", text)

        if full_body is True:
            framing = (
                "full body sprite with the entire head, hands, clothing silhouette, "
                "and feet inside frame"
            )
        elif full_body is False:
            framing = "waist-up sprite with both shoulders and any visible hands inside frame"
        else:
            framing = "preserve the requested portrait crop without clipping the head or hands"

        pose_hint = (pose or "").strip().lower()
        if pose_hint and pose_hint != "standing":
            pose_clause = f"perform the requested {pose_hint} action with believable balance"
        elif pose_hint == "standing":
            pose_clause = "use a relaxed contrapposto stance with believable balance"
        else:
            pose_clause = (
                "use believable weight distribution appropriate to the described action, "
                "or a relaxed contrapposto stance when no action is specified"
            )
        contract = (
            f"{_PORTRAIT_CONTRACT_MARKER}: exactly one isolated character; {framing}; "
            f"{pose_clause}; natural spine, shoulder, hip, elbow, wrist and hand mechanics; "
            "subtle facial asymmetry and a gaze motivated by the stated emotion; "
            "clean silhouette suitable for a visual-novel sprite. "
            "TRANSPARENT BACKGROUND CONTRACT: fully transparent RGBA background, clean alpha edges, "
            "no scenery, no floor, no cast shadow, no furniture, no decorative frame, no text. "
            "Avoid rigid symmetrical mannequin posture, T-pose, ID-photo pose, character turnaround sheet, "
            "stiff arms, fused fingers, duplicated limbs and cropped hands."
        )
        return re.sub(r"\s+", " ", f"{text}, {contract}" if text else contract).strip()

    @staticmethod
    def _portrait_alpha_metrics(image_path: Path) -> Dict[str, Any]:
        """Return pixel-level transparency metrics for a portrait PNG."""
        try:
            from PIL import Image

            with Image.open(image_path) as image:
                image.load()
                bands = image.getbands()
                metrics: Dict[str, Any] = {
                    "mode": image.mode,
                    "width": image.width,
                    "height": image.height,
                    "has_alpha_channel": "A" in bands,
                    "transparent_fraction": 0.0,
                    "opaque_fraction": 1.0,
                    "passed": False,
                }
                if "A" not in bands:
                    metrics["reason"] = "missing_alpha_channel"
                    return metrics
                alpha = image.getchannel("A")
                histogram = alpha.histogram()
                pixel_count = max(1, image.width * image.height)
                transparent_fraction = sum(histogram[:8]) / pixel_count
                opaque_fraction = sum(histogram[248:]) / pixel_count
                alpha_min, alpha_max = alpha.getextrema()
                passed = (
                    alpha_min < 255
                    and transparent_fraction >= 0.01
                    and opaque_fraction >= 0.01
                )
                metrics.update(
                    {
                        "alpha_min": int(alpha_min),
                        "alpha_max": int(alpha_max),
                        "transparent_fraction": round(transparent_fraction, 6),
                        "opaque_fraction": round(opaque_fraction, 6),
                        "passed": passed,
                        "reason": "ok" if passed else "ineffective_alpha_mask",
                    }
                )
                return metrics
        except Exception as exc:
            return {
                "passed": False,
                "reason": "image_decode_failed",
                "error": str(exc),
            }

    def portrait_has_effective_alpha(self, image_path: Path) -> bool:
        return bool(self._portrait_alpha_metrics(image_path).get("passed"))

    @staticmethod
    def _assert_portrait_transparency(metrics: Dict[str, Any], image_path: Path) -> None:
        if metrics.get("passed"):
            return
        raise PortraitTransparencyError(
            f"portrait has no effective transparent background: {image_path} "
            f"({metrics.get('reason') or 'unknown'})"
        )

    async def remove_background(self, image_path: Path, *, keep_source: bool = False) -> Path:
        """
        使用 rembg 去除背景

        Args:
            keep_source: Stage_Keyframe_Identity_AR_Blueprint A06 — 身份母版立绘
                必须保留原始图作为身份参考。启用时把原图复制到
                ``<image_path>.source.png`` 再覆盖，避免身份参考被透明背景
                处理覆盖。
        """
        return await asyncio.to_thread(self._remove_background_sync, image_path, keep_source)

    def _remove_background_sync(self, image_path: Path, keep_source: bool = False) -> Path:
        """Run rembg atomically and reject outputs without effective alpha."""
        temp_path = image_path.with_name(f".{image_path.name}.{os.getpid()}.alpha.tmp")
        try:
            from rembg import remove

            with open(image_path, "rb") as f:
                input_data = f.read()

            output_data = remove(input_data)
            if not isinstance(output_data, (bytes, bytearray)) or not output_data:
                raise PortraitTransparencyError("rembg returned empty or unsupported output")

            with open(temp_path, "wb") as f:
                f.write(output_data)
            metrics = self._portrait_alpha_metrics(temp_path)
            self._assert_portrait_transparency(metrics, temp_path)

            # A06 — 保留原图（用于身份参考），再写透明背景版本
            if keep_source:
                source_path = image_path.with_suffix(image_path.suffix + ".source.png")
                if not source_path.exists():
                    with open(source_path, "wb") as f:
                        f.write(input_data)

            os.replace(temp_path, image_path)
            return image_path

        except ImportError as exc:
            raise PortraitTransparencyError("rembg is not installed") from exc
        except Exception as e:
            xlog.error(0, e, "[image] remove background failed path=%s", image_path)
            if isinstance(e, PortraitTransparencyError):
                raise
            raise PortraitTransparencyError(str(e)) from e
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass

    def crop_watermark(self, image_path: Path, crop_bottom: int = 60) -> Path:
        """
        B11 — 裁剪底部水印。CogView-4 URL 返回的水印实测 ~50-60px 高，
        原 80px 会误裁画面下沿。改为 60px 默认。
        """
        temp_path = image_path.with_name(
            f".{image_path.name}.{os.getpid()}.{time.time_ns()}.crop.tmp"
        )
        try:
            from PIL import Image

            with Image.open(image_path) as img:
                image_format = img.format or "PNG"
                img.load()
                width, height = img.size
                if crop_bottom > 0 and height > crop_bottom + MIN_SIZE:
                    cropped = img.crop((0, 0, width, height - crop_bottom))
                    cropped.save(temp_path, format=image_format)
                else:
                    return image_path

            if not self._is_valid_image_file(temp_path):
                raise ImagePostprocessError("watermark crop produced an invalid image")
            os.replace(temp_path, image_path)
            return image_path

        except Exception as e:
            xlog.error(0, e, "[image] crop watermark failed path=%s", image_path)
            if isinstance(e, ImagePostprocessError):
                raise
            raise ImagePostprocessError(str(e)) from e
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    @staticmethod
    def _decoded_image_size(image_data: bytes) -> Tuple[int, int]:
        """Fully decode provider bytes and return sane dimensions."""

        if not isinstance(image_data, (bytes, bytearray)) or not image_data:
            raise InvalidGeneratedImageError("provider returned empty or unsupported image data")

        try:
            from io import BytesIO
            from PIL import Image

            raw = bytes(image_data)
            with Image.open(BytesIO(raw)) as image:
                image.verify()
            with Image.open(BytesIO(raw)) as image:
                image.load()
                width, height = image.size
        except Exception as exc:
            raise InvalidGeneratedImageError(
                f"provider returned undecodable image data: {exc}"
            ) from exc

        if width < MIN_SIZE or height < MIN_SIZE:
            raise InvalidGeneratedImageError(
                f"provider image is too small: {width}x{height}, minimum={MIN_SIZE}x{MIN_SIZE}"
            )
        return int(width), int(height)

    def _write_valid_image_bytes(self, image_path: Path, image_data: bytes) -> Path:
        """Validate provider bytes and atomically publish them to the cache path."""

        self._decoded_image_size(image_data)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = image_path.with_name(
            f".{image_path.name}.{os.getpid()}.{time.time_ns()}.download.tmp"
        )
        try:
            with open(temp_path, "xb") as output:
                output.write(image_data)
            if not self._is_valid_image_file(temp_path):
                raise InvalidGeneratedImageError("persisted provider image failed verification")
            os.replace(temp_path, image_path)
            return image_path
        finally:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except OSError:
                pass

    def _is_valid_image_file(self, image_path: Path) -> bool:
        """Return True only when Pillow can decode the image and dimensions are sane."""
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                img.verify()
            with Image.open(image_path) as img:
                width, height = img.size
            return width >= MIN_SIZE and height >= MIN_SIZE
        except Exception:
            return False

    def _actual_image_size(self, image_path: Path) -> Optional[List[int]]:
        """读取已落盘图像的真实解码尺寸（PIL）。失败返回 None，调用方回退到请求尺寸。

        result["size"] 必须报真实尺寸而非请求尺寸——provider 常忽略请求尺寸，
        报请求值会误导下游布局/合成。
        """
        try:
            from PIL import Image
            with Image.open(image_path) as img:
                w, h = img.size
            return [int(w), int(h)]
        except Exception:
            return None

    def _letterbox(self, img, target: Tuple[int, int]):
        """等比缩放 img 进 target 框，留白用「同图铺满 + 强模糊」填充。

        取代旧的 img.resize((tw, th), LANCZOS) 强制拉伸：provider 返回的宽高比
        与目标不一致时，拉伸会让背景横向变形；letterbox 保留比例，仅模糊填充留白。
        """
        from PIL import Image, ImageFilter
        target_w, target_h = target
        src_w, src_h = img.size
        if src_w <= 0 or src_h <= 0 or target_w <= 0 or target_h <= 0:
            return img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        # 前景：等比缩放到能放进 target 的最大尺寸
        scale = min(target_w / src_w, target_h / src_h)
        fg_w = max(1, int(round(src_w * scale)))
        fg_h = max(1, int(round(src_h * scale)))
        foreground = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
        # 背景：同一张图铺满 target 后强模糊，作为留白填充
        canvas = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=30))
        if canvas.mode != foreground.mode:
            canvas = canvas.convert(foreground.mode)
        canvas.paste(foreground, ((target_w - fg_w) // 2, (target_h - fg_h) // 2))
        return canvas

    def _postprocess_background_preserve_aspect(
        self,
        image_path: Path,
        target_size: Tuple[int, int] = SIZE_PRESETS["background"],
    ) -> Path:
        """
        Normalize landscape images to the requested aspect ratio. The bottom band is
        removed only when the provider watermark was explicitly enabled.
        """
        try:
            from io import BytesIO
            from PIL import Image

            crop_bottom = 0
            if AI_IMAGE_WATERMARK:
                crop_bottom = int(
                    self.profiles.get("post_processing", {}).get("watermark_crop_px")
                    or self.profiles.get("api_constraints", {}).get("watermark_crop_px")
                    or self.profiles.get("watermark_crop_px")
                    or 60
                )
            target_w, target_h = target_size
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                width, height = img.size
                if crop_bottom > 0 and height > crop_bottom + MIN_SIZE:
                    img = img.crop((0, 0, width, height - crop_bottom))
                if img.size != (target_w, target_h):
                    img = self._letterbox(img, (target_w, target_h))
                output = BytesIO()
                img.save(output, format="PNG")
            self._write_valid_image_bytes(image_path, output.getvalue())
            return image_path
        except Exception as e:
            xlog.error(0, e, "[image] background postprocess failed path=%s", image_path)
            return image_path

    def get_emotion_prompt(self, emotion: str) -> str:
        """获取情绪 prompt"""
        emotions = self.profiles.get("emotions", {})
        return emotions.get(emotion, {}).get("prompt", "neutral expression")

    def get_outfit_prompt(self, outfit: str, genre: str = "historical") -> str:
        """获取装束 prompt"""
        outfits = self.profiles.get("outfits", {})
        outfit_data = outfits.get(outfit, outfits.get("default", {}))
        return outfit_data.get(genre, outfit_data.get("historical", ""))

    def get_pose_prompt(self, pose: str) -> str:
        """获取姿态 prompt"""
        poses = self.profiles.get("poses", {})
        return poses.get(pose, {}).get("prompt", "standing pose")

    def get_mood_prompt(self, mood: str) -> str:
        """获取氛围 prompt"""
        moods = self.profiles.get("moods", {})
        return moods.get(mood, {}).get("prompt", "natural ambient lighting")

    def get_genre_style(self, genre: str) -> Tuple[str, str]:
        """获取题材风格 prompt 和禁止项"""
        genres = self.profiles.get("genre_families", {})
        genre_data = genres.get(genre, {})
        if not isinstance(genre_data, dict):
            genre_data = {}
        return (
            genre_data.get("style_prompt", ""),
            genre_data.get("forbidden", "")
        )

    def infer_genre(self, text: str) -> str:
        """从文本推断题材类型"""
        genres = self.profiles.get("genre_families", {})
        text_lower = text.lower()

        for genre_key, genre_data in genres.items():
            # profile JSON may carry non-dict sentinel entries (e.g.
            # "_deprecated": "Replaced by ...") — skip anything that
            # isn't a structured genre definition.
            if not isinstance(genre_data, dict):
                continue
            keywords = genre_data.get("keywords", [])
            for keyword in keywords:
                if keyword in text_lower:
                    return genre_key

        return "historical"  # 默认古风

    async def generate_portrait(
        self,
        character_id: str,
        character_name: str,
        appearance_prompt: str,
        emotion: str = "neutral",
        outfit: Optional[str] = None,
        pose: Optional[str] = None,
        genre: Optional[str] = None,
        seed: Optional[int] = None,
        remove_bg: bool = True,
        full_body: bool = True,
        canonical_identity: Optional[Dict[str, Any]] = None,
        final_prompt: Optional[str] = None,
        final_prompt_source: Optional[str] = None,
        gender: Optional[str] = None,
        gender_prompt: Optional[str] = None,
        age_contract: Optional[Dict[str, Any]] = None,
        visual_style_prompt: Optional[str] = None,
        style_fingerprint: Optional[str] = None,
        style_contract: Optional["VisualStyleContract"] = None,
    ) -> Dict[str, Any]:
        """
        生成角色立绘

        Args:
            character_id: 角色唯一标识
            character_name: 角色名称
            appearance_prompt: 外貌描述 prompt（英文）
            emotion: 情绪变体
            outfit: 装束变体
            pose: 姿态变体
            genre: 题材族
            seed: 种子值（用于一致性保证）
            remove_bg: 是否去除背景
            full_body: 是否全身像
            final_prompt: Prompt Rewriter 输出；仅当 final_prompt_source=llm_rewriter
                时作为权威 Prompt 原样发送，否则只作为重写草稿。
            gender / gender_prompt / age_contract: 作为结构化字段交给 Prompt Rewriter 融合，
                不会在最终 Prompt 后置拼接。
        """
        from app.services.prompt_rewriter_service import (
            PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION,
            prompt_rewriter_service,
            validate_portrait_final_prompt,
        )

        requested_shot = "full_body" if full_body else "half_body"
        gender_anchor = (gender_prompt or self._build_portrait_gender_anchor(gender)).strip()
        age_contract_data = (
            age_contract.to_dict()
            if hasattr(age_contract, "to_dict")
            else dict(age_contract or {})
        )
        from app.services.portrait_prompt_identity_service import (
            build_portrait_rewriter_identity,
        )

        identity_fields = build_portrait_rewriter_identity({
            "appearance_prompt": appearance_prompt,
            "age_contract": age_contract_data,
            "gender": gender,
            "canonical_identity": canonical_identity,
        })
        rewrite_fields = {
            "asset_type": "portrait",
            "character_id": character_id,
            **identity_fields,
            "gender": gender,
            "gender_prompt": gender_anchor,
            "age_group": age_contract_data.get("age_group"),
            "age_contract": age_contract_data or None,
            "emotion": emotion,
            "outfit": outfit,
            "pose": pose,
            "genre": genre,
            "visual_style_prompt": (visual_style_prompt or "").strip(),
            "style_fingerprint": (style_fingerprint or "").strip(),
            "shot": requested_shot,
            "draft_prompt": (final_prompt or "").strip(),
            "portrait_final_prompt_contract_version": (
                PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
            ),
        }

        # Only the prompt builder's explicit source stamp can mark supplied
        # prose as authoritative. Unstamped text is merely a draft and must be
        # rewritten by the LLM before it can reach the provider.
        supplied_prompt = (final_prompt or "").strip()
        full_prompt = (
            supplied_prompt
            if supplied_prompt and final_prompt_source == "llm_rewriter"
            else ""
        )
        if not full_prompt:
            if not genre:
                genre = self.infer_genre(appearance_prompt)
            style_input = (visual_style_prompt or "").strip()
            if style_contract is not None:
                contract_style = " ".join(
                    part
                    for part in (
                        style_contract.shared_art_direction,
                        style_contract.forbidden_clause,
                    )
                    if part
                )
                style_input = " ".join(part for part in (style_input, contract_style) if part)

            rewrite_fields["visual_style_prompt"] = style_input
            rewritten = await prompt_rewriter_service.rewrite("portrait", rewrite_fields)
            if rewritten is None:
                return {
                    "success": False,
                    "error": "立绘最终 Prompt 大模型重写失败，已停止生成以避免使用冲突模板",
                    "prompt_source": "llm_rewriter",
                }
            full_prompt = rewritten.to_cogview_prompt()

        from app.services.character_age_contract import age_prompt_validation_errors

        prompt_errors = validate_portrait_final_prompt(full_prompt, requested_shot)
        prompt_errors.extend(age_prompt_validation_errors(full_prompt, age_contract_data))
        if prompt_errors:
            return {
                "success": False,
                "error": "立绘最终 Prompt 一致性校验失败: " + "; ".join(prompt_errors),
                "prompt": full_prompt,
                "prompt_source": "llm_rewriter",
            }

        # Deterministically prefix the locked project style block (byte-identical
        # across every portrait) so rendering style cannot drift with rewriter
        # wording. Skipped when the rewriter already embedded the block verbatim.
        full_prompt = self._apply_visual_style_lock(full_prompt, visual_style_prompt)

        # 选择尺寸预设
        size_key = "portrait_full" if full_body else "portrait"
        width, height = SIZE_PRESETS.get(size_key, (1024, 1024))

        # Generate, then validate the visible age. A mismatch gets one
        # feedback-guided rewrite/regeneration by default; unchecked images do
        # not enter the asset table when strict validation is enabled.
        from app.services.portrait_age_validator_service import (
            PORTRAIT_AGE_VALIDATE_MAX_RETRIES,
            portrait_age_validator_service,
        )

        age_validation_attempts: List[Dict[str, Any]] = []
        result: Dict[str, Any] = {}
        max_age_retries = PORTRAIT_AGE_VALIDATE_MAX_RETRIES if age_contract_data else 0
        for age_attempt in range(max_age_retries + 1):
            result = await self.generate_image(
                prompt=full_prompt,
                asset_type="portrait",
                width=width,
                height=height,
                seed=seed,
                ensure_portrait_alpha=remove_bg,
                authoritative_prompt=True,
            )
            if not result["success"]:
                return result

            generated_filename = Path(result["image_url"]).name
            generated_path = self.output_dir / "portraits" / generated_filename
            validation_path = generated_path
            source_candidate = generated_path.with_suffix(generated_path.suffix + ".source.png")
            if source_candidate.exists():
                validation_path = source_candidate

            if not age_contract_data:
                break
            age_validation = await portrait_age_validator_service.validate(
                image_path=validation_path,
                age_contract=age_contract_data,
                character_name=character_name,
            )
            age_validation_attempts.append(age_validation.to_dict())
            if age_validation.passed:
                break
            if age_attempt >= max_age_retries:
                return {
                    **result,
                    "success": False,
                    "error": (
                        "立绘年龄视觉验收失败: "
                        + "; ".join(age_validation.violations or ("apparent age mismatch",))
                    ),
                    "prompt": full_prompt,
                    "age_contract": age_contract_data,
                    "age_validation": age_validation.to_dict(),
                    "age_validation_attempts": age_validation_attempts,
                }

            repair_fields = dict(rewrite_fields)
            repair_fields["previous_invalid_output"] = full_prompt
            repair_fields["validation_feedback"] = age_validation.rewrite_feedback()
            repaired = await prompt_rewriter_service.rewrite(
                "portrait",
                repair_fields,
                force_refresh=True,
            )
            if repaired is None:
                return {
                    **result,
                    "success": False,
                    "error": "立绘年龄视觉验收失败，且年龄反馈重写失败",
                    "age_contract": age_contract_data,
                    "age_validation": age_validation.to_dict(),
                    "age_validation_attempts": age_validation_attempts,
                }
            full_prompt = repaired.to_cogview_prompt()
            repair_errors = validate_portrait_final_prompt(full_prompt, requested_shot)
            repair_errors.extend(age_prompt_validation_errors(full_prompt, age_contract_data))
            if repair_errors:
                return {
                    **result,
                    "success": False,
                    "error": "年龄反馈重写后的 Prompt 校验失败: " + "; ".join(repair_errors),
                    "prompt": full_prompt,
                    "age_contract": age_contract_data,
                    "age_validation": age_validation.to_dict(),
                    "age_validation_attempts": age_validation_attempts,
                }

        # 后处理
        image_filename = Path(result["image_url"]).name
        image_path = self.output_dir / "portraits" / image_filename

        # generate_image owns the one-time crop/rembg/alpha-validation boundary.
        # Here we only derive the three identity-reference URLs.
        source_image_url: Optional[str] = None
        identity_reference_url: Optional[str] = None
        presentation_url: Optional[str] = None

        if not remove_bg:
            # 身份母版本身：原图即身份参考
            source_image_url = result.get("image_url")
            identity_reference_url = result.get("image_url")
            presentation_url = result.get("image_url")
        else:
            presentation_url = result.get("image_url")
            alpha_metrics = result.get("portrait_alpha") or self._portrait_alpha_metrics(image_path)
            try:
                self._assert_portrait_transparency(alpha_metrics, image_path)
            except PortraitTransparencyError as exc:
                return {
                    **result,
                    "success": False,
                    "error": f"立绘透明背景验收失败: {exc}",
                }
            source_path = image_path.with_suffix(image_path.suffix + ".source.png")
            if source_path.exists():
                source_image_url = f"{IMAGE_BASE_URL}/portraits/{source_path.name}"
            identity_reference_url = self._compose_identity_reference(image_path)
            xlog.info(0, "[image] portrait alpha validated path=%s identity_ref=%s", image_path, identity_reference_url)

        result["character_id"] = character_id
        result["character_name"] = character_name
        result["emotion"] = emotion
        result["outfit"] = outfit
        result["pose"] = pose
        result["genre"] = genre
        result["gender"] = gender
        result["age_group"] = age_contract_data.get("age_group")
        result["age_contract"] = age_contract_data or None
        result["age_validation"] = age_validation_attempts[-1] if age_validation_attempts else None
        result["age_validation_attempts"] = age_validation_attempts
        result["visual_style_prompt"] = visual_style_prompt
        result["prompt"] = full_prompt
        result["prompt_source"] = "llm_rewriter"
        result["portrait_final_prompt_contract_version"] = (
            PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
        )
        result["requested_shot"] = requested_shot
        # A06 — 身份参考三件套
        result["source_image_url"] = source_image_url
        result["identity_reference_url"] = identity_reference_url
        result["presentation_url"] = presentation_url

        # 身份参考三件套补充：source_image_sha256。下游 identity_master_resolver
        # 会把这个值落到 portrait Asset.generation_params.reference_image_sha256，
        # 再传给 KeyframeCharacterBinding.reference_image_sha256；只有该字段非空
        # binding.has_reference() 才返回 True，keyframe 才会走 reference-based
        # 生成（Stage 1）而非 text-only fallback。
        # 身份母版（remove_bg=False）image_path 就是身份参考图本身；普通立绘
        # remove_bg=True 时身份参考是 .source.png 备份，但母版永远是前者。
        sha_path = image_path
        if remove_bg:
            candidate = image_path.with_suffix(image_path.suffix + ".source.png")
            if candidate.exists():
                sha_path = candidate
        source_image_sha256 = self._file_sha256(sha_path)
        result["source_image_sha256"] = source_image_sha256

        # A06 — generation_params 也带上，方便 asset_management_service 持久化
        existing_params = dict(result.get("generation_params") or {})
        existing_params.update(
            {
                "source_image_url": source_image_url,
                "identity_reference_url": identity_reference_url,
                "presentation_url": presentation_url,
                "source_image_sha256": source_image_sha256,
                "portrait_alpha": result.get("portrait_alpha"),
                "portrait_generation_contract_version": PORTRAIT_GENERATION_CONTRACT_VERSION,
                "portrait_final_prompt_contract_version": (
                    PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
                ),
                "prompt_source": "llm_rewriter",
                "requested_shot": requested_shot,
                "age_group": age_contract_data.get("age_group"),
                "age_contract": age_contract_data or None,
                "age_validation": age_validation_attempts[-1] if age_validation_attempts else None,
                "age_validation_attempts": age_validation_attempts,
            }
        )
        result["generation_params"] = existing_params

        return result

    def _compose_identity_reference(self, transparent_png_path: Path) -> Optional[str]:
        """A06 — 合成身份参考图：透明立绘 + 中性纯色背景。

        身份参考图必须是 RGB（不能透明），否则外部视觉模型（qwen-image2 等）
        会拒绝或误判。把透明 PNG 合成到 ``#d8d8d8`` 浅灰背景上，输出到
        ``<name>.identity.png``，与立绘原图同目录。失败时返回 None，由上游
        决定是否阻断。
        """
        try:
            from PIL import Image
        except ImportError:
            xlog.warn(0, "[image] PIL not available, skip identity reference composition")
            return None

        try:
            foreground = Image.open(transparent_png_path).convert("RGBA")
        except Exception as e:
            xlog.error(0, e, "[image] identity reference foreground open failed path=%s", transparent_png_path)
            return None

        canvas = Image.new("RGBA", foreground.size, (216, 216, 216, 255))
        canvas.alpha_composite(foreground)
        rgb = canvas.convert("RGB")
        out_path = transparent_png_path.with_suffix(transparent_png_path.suffix + ".identity.png")
        rgb.save(out_path, format="PNG")
        return f"{IMAGE_BASE_URL}/portraits/{out_path.name}"

    def _apply_visual_style_lock(self, prompt: str, visual_style_prompt: Optional[str]) -> str:
        """Prefix final prompts with the project style lock when it is missing."""
        prompt = (prompt or "").strip()
        style = (visual_style_prompt or "").strip()
        if not style:
            return prompt
        if style.lower() in prompt.lower():
            return prompt
        separator = "，" if re.search(r"[一-鿿]", style) else ","
        return f"{style}{separator} {prompt}" if prompt else style

    def _build_portrait_gender_anchor(self, gender: Optional[str]) -> str:
        """把 male/female/中文性别转成最终 prompt 的稳定性别锚点。"""
        if not gender:
            return ""
        s = str(gender).strip().lower()
        compact = (
            s.replace(" ", "")
            .replace("-", "")
            .replace("_", "")
            .replace("/", "")
        )
        female_hits = {
            "female", "woman", "women", "girl", "feminine", "f",
            "女", "女性", "女人", "女子", "少女", "女孩", "女主", "女主角",
        }
        male_hits = {
            "male", "man", "men", "boy", "masculine", "m",
            "男", "男性", "男人", "男子", "少年", "青年", "男孩", "男主", "男主角",
        }
        if compact in female_hits or any(hit in compact for hit in female_hits if len(hit) > 1):
            return "clearly female character, feminine facial structure and body silhouette, not masculine"
        if compact in male_hits or any(hit in compact for hit in male_hits if len(hit) > 1):
            return "clearly male character, masculine facial structure and body silhouette, not feminine"
        return ""

    def _apply_portrait_gender_anchor(self, prompt: str, gender_anchor: str) -> str:
        """对 rewriter 结果补性别锚点；fallback prompt 已在构建时注入。"""
        prompt = (prompt or "").strip()
        anchor = (gender_anchor or "").strip()
        if not anchor:
            return prompt
        prompt_lower = prompt.lower()
        if "clearly male character" in prompt_lower or "clearly female character" in prompt_lower:
            return prompt
        return f"{anchor}, {prompt}" if prompt else anchor

    def _build_portrait_prompt(
        self,
        appearance_prompt: str,
        emotion: str,
        outfit: Optional[str],
        pose: Optional[str],
        style_prompt: str,
        forbidden: str,
        full_body: bool,
        gender_prompt: str = "",
        genre: Optional[str] = None,
    ) -> str:
        """
        构建立绘 prompt (fallback 路径，A02-A08 优化过)。
        - A07: 不再说 "simple solid background"，统一为 transparent background
        - A08: no_text 缩到 5 词以内并放尾部
        - A09: seed 由 caller 控制（CogView-4 不直接支持 prompt 内 seed）
        """

        # 1. 景别指令（A03 — NAI 规范）
        if full_body:
            shot = (
                "full body shot, entire character visible from head to feet, vertical composition, "
                "relaxed three-quarter body angle"
            )
        else:
            shot = (
                "half body shot from waist up, character centered with a subtle off-axis torso turn, "
                "three-quarter angle"
            )

        # 2. 风格
        style = f"{style_prompt}," if style_prompt else ""

        # 3. 情绪表情（A04 — emotion 前置）
        emotion_prompt = self.get_emotion_prompt(emotion)

        # 3.5 性别锚点：放在外貌前，避免 anime/galgame 模型默认偏女性。
        gender_text = f"{gender_prompt}," if gender_prompt else ""

        # 4. 装束（A05 — 用 profiles 里的具体英文，不直接传 outfit key）
        outfit_prompt = ""
        if outfit and outfit.strip().lower() not in {"default", "canonical"}:
            outfit_prompt = self.get_outfit_prompt(outfit, genre or "historical")
        outfit_text = f"wearing {outfit_prompt}," if outfit_prompt else ""

        # 5. 姿态（A06 — 用扩展后的 poses 词库）
        pose_text = self.get_pose_prompt(pose) if pose else self.get_pose_prompt("standing")

        # 6. 原有题材禁止项
        forbid = f", forbidden: {forbidden}" if forbidden else ""

        # A07 — 透明背景表述统一（去掉 "simple solid background" 冲突表述）
        # A08 — no_text 缩到 5 词放尾部
        full_prompt = f"""
        {emotion_prompt},
        {gender_text}
        {appearance_prompt},
        {shot},
        {style}
        {outfit_text}
        {pose_text},
        production-quality 2D visual novel character sprite, coherent linework and shading,
        detailed face and clothing folds, clean readable silhouette,
        transparent background, alpha channel, isolated character, clear edges,
        no scenery, no environment, no props{forbid},
        no text, no watermark
        """.strip().replace('\n', ' ').replace('  ', ' ')

        return full_prompt

    def _build_portrait_prompt_with_contract(
        self,
        *,
        appearance_prompt: str,
        emotion: str,
        outfit: Optional[str],
        pose: Optional[str],
        full_body: bool,
        gender_prompt: str,
        contract: "VisualStyleContract",
        genre: Optional[str] = None,
    ) -> str:
        """Contract-driven portrait prompt — single source of truth for art direction.

        Prompt ordering (matches the design contract):
        1. STYLE SIGNATURE + SHARED ART DIRECTION（与背景共享）
        2. CHARACTER IDENTITY（caller-supplied appearance + identity lock）
        3. EXPRESSION AND POSE
        4. PORTRAIT GEOMETRY CONTRACT（全身 + 头脚不裁）
        5. TRANSPARENT BACKGROUND CONTRACT
        6. FORBIDDEN STYLE

        Never appends ``galgame style`` / generic ``cel-shading`` defaults —
        the contract's vocabulary wins.
        """
        emotion_text = self.get_emotion_prompt(emotion)
        gender_text = f"{gender_prompt}," if gender_prompt else ""
        outfit_prompt = ""
        if outfit and outfit.strip().lower() not in {"default", "canonical"}:
            outfit_prompt = self.get_outfit_prompt(outfit, genre or "historical")
        outfit_text = f"wearing {outfit_prompt}," if outfit_prompt else ""
        pose_text = self.get_pose_prompt(pose) if pose else self.get_pose_prompt("standing")
        if full_body:
            geometry = (
                "PORTRAIT GEOMETRY CONTRACT: full body shot, entire character "
                "visible from head to feet, vertical composition, head never "
                "touches the top edge, feet never clipped, weapons and "
                "accessories fully preserved."
            )
        else:
            geometry = (
                "PORTRAIT GEOMETRY CONTRACT: half body shot from waist up, "
                "character centered with a subtle off-axis torso turn, three-quarter angle."
            )
        transparency = (
            "TRANSPARENT BACKGROUND CONTRACT: fully transparent RGBA alpha "
            "background, no scenery, no environment, no props, isolated "
            "character, clean edges."
        )
        forbidden_clause = contract.forbidden_clause
        forbidden_block = f" {forbidden_clause}" if forbidden_clause else ""
        prompt = (
            f"{contract.shared_art_direction} "
            f"CHARACTER IDENTITY: {appearance_prompt}. "
            f"EXPRESSION AND POSE: {emotion_text}, {gender_text} {outfit_text} {pose_text}. "
            f"{geometry} "
            f"{contract.portrait_role} "
            f"{transparency}{forbidden_block}, no text, no watermark."
        )
        return re.sub(r"\s{2,}", " ", prompt).strip()

    async def generate_background(
        self,
        scene_name: str,
        scene_description: str,
        mood: str = "day",
        genre: Optional[str] = None,
        style_override: Optional[str] = None,
        final_prompt: Optional[str] = None,
        forbidden_characters: Optional[List[str]] = None,
        visual_style_prompt: Optional[str] = None,
        style_contract: Optional["VisualStyleContract"] = None,
        allow_non_human_life: bool = False,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        生成背景图

        Args:
            scene_name: 场景名称
            scene_description: 场景描述（英文）
            mood: 氛围
            genre: 题材族
            style_override: 自定义风格
            final_prompt: B01 — 若传入则跳过 _build_background_prompt 直接使用
            forbidden_characters: BG-NOCAST — 本章出场角色名，会以强否定形式注入 prompt，
                防止背景里出现章节人物（中文角色名/英文译名都加）
        """
        if final_prompt:
            # rewriter 路径：直接用 final_prompt 作底
            base_prompt = self._apply_visual_style_lock(final_prompt, visual_style_prompt)
        elif style_contract is not None:
            # 契约驱动：不再附加 genre bg_style，避免与契约 ink-and-color 冲突。
            base_prompt = self._build_background_prompt_with_contract(
                scene_description=scene_description,
                mood=mood,
                contract=style_contract,
            )
        else:
            # 推断题材风格
            if not genre:
                genre = self.infer_genre(scene_description)

            style_prompt, _ = self.get_genre_style(genre)
            if style_override:
                style_prompt = style_override
            if visual_style_prompt:
                style_prompt = visual_style_prompt

            # 构建背景 prompt（仅环境描述，不含人物否定）
            # 注意：这里 scene_description 可能是中文剧情（outline 被污染）。
            # 但正常路径下，final_prompt 由 segmenter 蒸馏的 environment_hint_en 生成。
            # 走到这里说明 segmenter+rewriter 都失败了，是最后兜底 —— 接受可能的剧情残留，
            # 由后续 _enforce_background_environment_focus 删人物词，至少能出图。
            base_prompt = self._build_background_prompt(
                scene_description=scene_description,
                mood=mood,
                style_prompt=style_prompt,
                forbidden_characters=forbidden_characters,
                genre=genre,
            )

        # === BG-ENFORCE / BG-NO-HUMAN-SUBJECT ===
        # 不论 final_prompt 还是 fallback，最终都过统一约束函数。
        # 2026-06-14: 用户硬要求所有背景图强制完全无人。
        # _enforce_background_environment_focus 内部已忽略 allow_background_people 参数，
        # 永远走 unpopulated 分支。这里把 allow_human 永远设为 False 以匹配验收器策略。
        allow_human = False
        cast_names = self._normalize_cast_names(forbidden_characters)
        cast_preview = ", ".join(cast_names[:5]) if cast_names else "(none)"

        full_prompt = self._enforce_background_environment_focus(
            base_prompt,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
            allow_background_people=allow_human,
            allow_non_human_life=allow_non_human_life,
            escalation_level=0,
            forbidden_entities=forbidden_entities,
        )

        # 日志（不打印 API key；prompt 前 500 字便于调试）
        print(
            f"[ImageGen/BG] scene={scene_name!r} mood={mood} "
            f"allow_background_people={allow_human} (FORCED unpopulated) "
            f"forbidden_count={len(cast_names)} forbidden_preview=[{cast_preview}] "
            f"forbidden_entities={len(forbidden_entities or [])} "
            f"enforced=True (no_human_subject)"
        )
        print(f"[ImageGen/BG] final_prompt (full): {full_prompt}")

        width, height = SIZE_PRESETS["background"]

        result = await self._generate_background_once(
            prompt=full_prompt,
            width=width,
            height=height,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
            forbidden_entities=forbidden_entities,
        )

        if not result["success"]:
            print(f"[ImageGen/BG] generation failed: {result.get('error')}")
            return result

        image_filename = Path(result["image_url"]).name
        image_path = self.output_dir / "backgrounds" / image_filename
        try:
            await asyncio.to_thread(
                self._postprocess_background_preserve_aspect,
                image_path,
                SIZE_PRESETS["background"],
            )
        except Exception as exc:
            xlog.error(0, exc, "[image] background postprocess failed path=%s", image_path)
            try:
                image_path.unlink()
            except OSError:
                pass
            return {
                "success": False,
                "error": f"背景图后处理失败: {exc}",
                "image_path": str(image_path),
                "cached": False,
                "prompt": full_prompt,
            }

        actual_size = self._actual_image_size(image_path)
        if (
            not self._is_valid_image_file(image_path)
            or actual_size != list(SIZE_PRESETS["background"])
        ):
            try:
                image_path.unlink()
            except OSError:
                pass
            return {
                "success": False,
                "error": (
                    "背景图后处理后尺寸无效: "
                    f"actual={actual_size}, expected={list(SIZE_PRESETS['background'])}"
                ),
                "image_path": str(image_path),
                "cached": False,
                "prompt": full_prompt,
            }

        result["scene_name"] = scene_name
        result["mood"] = mood
        result["validation_attempts"] = 0
        result["validation_results"] = []
        result["validation_skipped"] = True
        result["prompt"] = full_prompt
        result["size"] = actual_size
        print(f"[ImageGen/BG] generated without validation scene={scene_name!r}")
        return result

    async def _generate_background_once(
        self,
        prompt: str,
        width: int,
        height: int,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        单次背景图生成（不验收，不重试）。
        generate_background 直接调用本函数后返回结果。
        """
        # skip_cache=True 避免重试时返回同一张缓存图
        return await self.generate_image(
            prompt=prompt,
            asset_type="background",
            width=width,
            height=height,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
            skip_cache=True,
            forbidden_entities=forbidden_entities,
        )

    async def generate_background_frozen_with_entity_validation(
        self,
        *,
        scene_name: str,
        scene_description: str,
        mood: str = "day",
        final_prompt: str,
        forbidden_characters: Optional[List[str]] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
        visual_style_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """脚本槽路径专用：冻结 prompt 背景生成 + VLM 剧情实体泄漏验收。

        与 generate_background_with_validation（spec 驱动、自建 prompt）不同，
        本方法保留调用方冻结的 final_prompt（render_spec 契约），只在其外
        复用 BackgroundStoryEntityValidatorService 的泄漏验收与 escalation
        重试。超出 BG_ENTITY_VALIDATE_MAX_RETRIES 返回 success=False
        （error=quarantined: forbidden_story_entity_leak: ...），由 worker
        将任务置失败，隔离图不入库、不进 VN manifest。
        """
        result = await self.generate_background(
            scene_name=scene_name,
            scene_description=scene_description,
            mood=mood,
            final_prompt=final_prompt,
            forbidden_characters=forbidden_characters,
            visual_style_prompt=visual_style_prompt,
        )
        if not result.get("success"):
            return result

        entities = [
            entity
            for entity in (forbidden_entities or [])
            if isinstance(entity, dict) and (
                entity.get("canonical_name") or entity.get("aliases")
            )
        ]
        if not (BG_ENTITY_VALIDATE_ENABLED and entities):
            return result

        from app.services.background_story_entity_validator_service import (
            BackgroundStoryEntityValidatorService,
        )
        validator = BackgroundStoryEntityValidatorService(
            config={"max_image_size": SIZE_PRESETS["background"]},
        )
        attempt = 0
        current_prompt = str(final_prompt)
        current_path = Path(str(result["image_path"]))
        result.setdefault("validation_results", [])
        while True:
            verdict = await validator.validate(current_path, entities)
            result["validation_results"].append(
                {
                    "stage": "entity_leak",
                    "attempt": attempt,
                    "passed": verdict.passed,
                    "detected_entity_count": verdict.detected_entity_count,
                    "reasons": list(verdict.reasons),
                }
            )
            print(
                f"[ImageGen/BG/frozen] scene={scene_name!r} entity-leak "
                f"attempt={attempt} passed={verdict.passed} "
                f"count={verdict.detected_entity_count}"
            )
            if verdict.passed:
                return result

            if attempt >= BG_ENTITY_VALIDATE_MAX_RETRIES:
                reasons_str = (
                    "; ".join(verdict.reasons) if verdict.reasons
                    else "forbidden story entity leak detected"
                )
                print(
                    f"[ImageGen/BG/frozen] QUARANTINED after {attempt} "
                    f"attempts: {reasons_str}"
                )
                return {
                    **result,
                    "success": False,
                    "error": f"quarantined: forbidden_story_entity_leak: {reasons_str}",
                    "image_path": str(current_path),
                }

            attempt += 1
            retry_prompt = self._enforce_background_environment_focus(
                current_prompt,
                forbidden_characters=forbidden_characters,
                scene_name=scene_name,
                scene_description=scene_description,
                escalation_level=1,
            )
            leak_names = [
                leak.forbidden_name or leak.detected_entity_type
                for leak in verdict.matched_forbidden_entities
            ]
            if leak_names:
                retry_prompt += (
                    "\n\n【重试指令】上一次生成的背景图中错误出现了 "
                    + "、".join(f"「{name}」" for name in leak_names[:5])
                    + "。请重新生成同一场景，保留地点/时间/天气/整体画风，"
                    "但彻底移除上述主体，并避免剪影、倒影、海报、雕像、屏幕画面"
                    "等间接表现。"
                )
            try:
                retry_path = await self._generate_background_once_with_prompt(
                    retry_prompt,
                    scene_name=scene_name,
                    forbidden_characters=forbidden_characters,
                    forbidden_entities=entities,
                )
            except Exception as exc:
                print(f"[ImageGen/BG/frozen] entity-leak retry exception: {exc}")
                retry_path = None
            if retry_path is None:
                return {
                    **result,
                    "success": False,
                    "error": (
                        "quarantined: forbidden_story_entity_leak: "
                        "retry generation returned no image"
                    ),
                    "image_path": str(current_path),
                }
            retry_path = await asyncio.to_thread(
                self._postprocess_background_preserve_aspect,
                retry_path,
                SIZE_PRESETS["background"],
            )
            current_prompt = retry_prompt
            current_path = retry_path
            result["image_path"] = str(retry_path)
            result["prompt"] = retry_prompt

    async def _generate_background_once_with_prompt(
        self,
        prompt: str,
        scene_name: Optional[str] = None,
        scene_selector: Optional[str] = None,
        width: int = SIZE_PRESETS["background"][0],
        height: int = SIZE_PRESETS["background"][1],
        forbidden_characters: Optional[List[str]] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Path]:
        """
        L3.26 helper — 直接接收 final prompt，返回生成的 image path（或 None）。

        forbidden_characters / forbidden_entities 透传给 generate_image，让 sanitize
        retry / safety fallback 路径仍能保持命名角色禁令（精简约束的初版 prompt
        不会被审核拒绝丢失角色约束）。
        """
        try:
            result = await self.generate_image(
                prompt=prompt,
                asset_type="background",
                width=width,
                height=height,
                scene_name=scene_name,
                scene_description=scene_selector,
                forbidden_characters=forbidden_characters,
                forbidden_entities=forbidden_entities,
                skip_cache=True,
            )
        except Exception as e:
            logger.error("generate_background_once_with_prompt failed: %s", e)
            return None
        if not isinstance(result, dict):
            return None
        # generate_image 现在返回 image_path（本地绝对路径）+ image_url（HTTP URL）
        p = result.get("image_path") or result.get("path")
        if p:
            return Path(p)
        url = result.get("image_url") or result.get("url")
        if url:
            logger.warning("_generate_background_once_with_prompt: image_path missing, only url=%s", url)
            return None
        return None

    def _build_background_prompt(
        self,
        scene_description: str,
        mood: str,
        style_prompt: str,
        forbidden_characters: Optional[List[str]] = None,
        genre: Optional[str] = None,
    ) -> str:
        """
        构建背景 prompt（fallback 路径，环境描述部分）。

        本函数只负责"环境描述主体"，**不**追加人物否定词。
        所有否定 / 比例 / 角色点名约束由 _enforce_background_environment_focus() 统一处理。
        调用方（generate_background）会保证最终送 CogView-4 前过 enforce。

        - B05: genre style 去掉服装词（背景不应有衣服）
        - B08: lighting 独立段
        - B07: 构图表述优化
        - 2026-06-15: 按 genre 选 bg_style，去掉硬编码的 Makoto Shinkai / galgame / anime
        """

        # B08 — mood 与 lighting 分离。mood 段保留天气/时间，lighting 段独立。
        mood_prompt = self.get_mood_prompt(mood)
        # 如果 mood 本身就含光照词（如 "golden hour"），不再额外加 lighting 段
        lighting_keywords = ["light", "sun", "moon", "candle", "glow", "shadow", "overcast", "rim"]
        if any(kw in mood_prompt.lower() for kw in lighting_keywords):
            lighting_clause = ""
        else:
            lighting_clause = f"，光线：{self.get_lighting_prompt(mood)}"

        # B05 — 移除 genre style 里的服装词
        clean_style = self._strip_clothing_words(style_prompt)
        # 按 genre 选 bg_style（无则用 historical 兜底）
        bg_style = self._get_genre_bg_style(genre)

        # 构建完整 prompt（不含人物否定，由 enforce 统一加）
        full_prompt = f"""
{scene_description},
{clean_style}, {bg_style},
{mood_prompt}{lighting_clause},
16:9宽幅环境建立镜头，
环境细节丰富，只呈现建筑、自然元素、道具、光线和氛围，
不要文字，不要水印
        """.strip().replace('\n', ' ').replace('  ', ' ')

        return full_prompt

    def _get_genre_bg_style(self, genre: Optional[str]) -> str:
        """按 genre 返回背景画风描述。无 genre 或未知 genre 用 historical 兜底。"""
        genres = self.profiles.get("genre_families", {})
        if genre and genre in genres and isinstance(genres[genre], dict):
            bg = genres[genre].get("bg_style", "")
            if bg:
                return bg
        # 兜底：historical
        historical = genres.get("historical", {})
        if isinstance(historical, dict):
            return historical.get(
                "bg_style",
                "中国古典绘画审美，手绘背景质感，环境细节丰富",
            )
        return "中国古典绘画审美，手绘背景质感，环境细节丰富"

    def _build_background_prompt_with_contract(
        self,
        *,
        scene_description: str,
        mood: str,
        contract: "VisualStyleContract",
    ) -> str:
        """Contract-driven background prompt.

        Shared art direction is identical to the portrait prompt (only the
        background role differs). Never appends ``genre bg_style`` or any
        conflicting photorealistic/oil-painting vocabulary.
        """
        mood_prompt = self.get_mood_prompt(mood)
        lighting_keywords = ["light", "sun", "moon", "candle", "glow", "shadow", "overcast", "rim"]
        if any(kw in mood_prompt.lower() for kw in lighting_keywords):
            lighting_clause = ""
        else:
            lighting_clause = f" Lighting: {self.get_lighting_prompt(mood)}."
        forbidden_clause = contract.forbidden_clause
        forbidden_block = f" {forbidden_clause}" if forbidden_clause else ""
        prompt = (
            f"{contract.shared_art_direction} "
            f"SCENE: {scene_description}. "
            f"Atmosphere: {mood_prompt}.{lighting_clause} "
            f"{contract.background_role} "
            f"Do not create a separate painterly, photorealistic or 3D "
            f"background style.{forbidden_block}, no text, no watermark."
        )
        return re.sub(r"\s{2,}", " ", prompt).strip()

    def _enforce_background_environment_focus(
        self,
        prompt: str,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        allow_background_people: Optional[bool] = None,
        allow_non_human_life: bool = False,
        escalation_level: int = 0,
        spec: Optional["BackgroundSceneSpec"] = None,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        BG-ENFORCE — 背景图统一约束函数。

        两种调用形态：
        1. （L3.19 新增）传 spec：使用 spec.people_policy / scene_type / camera_shot_type
           做分支式追加（schema 驱动）
        2. （旧版兼容）传 prompt + 字符串参数：转发给 _enforce_background_no_human_subject

        Stage_Background_Entity_Exclusion:
        - ``forbidden_entities`` 优先于 ``forbidden_characters``；两者可同时存在，
          entities 走结构化禁入文案，characters 仅作 legacy 字符串兜底。
        - 当 ``spec`` 已带 ``forbidden_entities`` 时，调用方可省略 ``forbidden_entities``
          参数；本函数会自动从 spec 取出（保证 sanitize-retry 等内部路径不丢禁令）。
        """
        # schema-driven path (L3.19-L3.22)
        if spec is not None:
            if forbidden_entities is None:
                forbidden_entities = list(spec.forbidden_entities or [])
            return self._enforce_with_spec(
                spec=spec,
                prompt=prompt,
                forbidden_characters=forbidden_characters or spec.forbidden_characters,
                escalation_level=escalation_level,
                forbidden_entities=forbidden_entities,
            )

        # legacy path
        return self._enforce_background_no_human_subject(
            prompt,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
            allow_background_people=allow_background_people,
            allow_non_human_life=allow_non_human_life,
            escalation_level=escalation_level,
            forbidden_entities=forbidden_entities,
        )

    def _enforce_with_spec(
        self,
        spec: "BackgroundSceneSpec",
        prompt: str,
        forbidden_characters: List[str],
        escalation_level: int = 0,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        L3.19-L3.22 + Stage_Background_Entity_Exclusion:
        schema 驱动的 enforce — 所有背景 prompt 的最后一道闸门。

        - mode 分支追加（empty_required / optional / groups_required）
        - idempotent 检测（已含 ban clause 不重复追加）
        - 命名角色禁令 → 升级为「剧情角色实体禁入」（中英双版本）
        - forbidden_entities 优先；空时回退到 forbidden_characters
        """
        # 找到 negative_clauses 配置
        neg = (self.profiles or {}).get("background_negative_clauses", {}) or {}

        # idempotent: 检测是否已含 base ban clause
        base_clause = neg.get("base", "")
        already_has_base = bool(base_clause) and base_clause[:80] in prompt

        parts: List[str] = []
        if not already_has_base and base_clause:
            parts.append(base_clause)

        # mode-specific
        mode = spec.people_policy.mode
        if mode == "empty_required":
            clause = neg.get("empty_required", "")
        elif mode == "background_people_optional":
            clause = neg.get("background_people_optional", "")
        elif mode == "background_groups_required":
            clause = neg.get("background_groups_required", "")
        else:
            clause = ""

        # idempotent 检测
        if clause and clause[:60] not in prompt:
            parts.append(clause)

        # establishing shot enforcement
        enforce = neg.get("establishing_shot_enforcement", "")
        if enforce and enforce[:60] not in prompt:
            parts.append(enforce)

        # escalation
        if escalation_level >= 1:
            esc = neg.get("escalation_level_1", "")
            if esc and esc[:60] not in prompt:
                parts.append(esc)

        # 剧情角色实体禁入（中英双版本结构化文案）。
        # Stage_Background_Entity_Exclusion: 即使没有 forbidden_entities，
        # 也要打 header（"no story characters by identity, not species"），
        # 因为单靠 "no humans" 无法覆盖机器人/动物/怪物角色。
        from app.services.background_story_entity_text import (
            render_full_exclusion_block,
        )

        entities_source = forbidden_entities
        if entities_source is None:
            entities_source = list(getattr(spec, "forbidden_entities", None) or [])
        if entities_source or forbidden_characters:
            block = render_full_exclusion_block(
                entities=entities_source or [],
                legacy_names=forbidden_characters or [],
            )
            if block and "BACKGROUND ROLE EXCLUSION" not in prompt:
                parts.append(block)
        else:
            # No cast info — still inject the header so the model knows
            # the rule applies regardless of species.
            from app.services.background_story_entity_text import (
                STORY_ENTITY_EXCLUSION_HEADER_CN,
                STORY_ENTITY_EXCLUSION_HEADER_EN,
            )

            header = f"{STORY_ENTITY_EXCLUSION_HEADER_CN}\n\n{STORY_ENTITY_EXCLUSION_HEADER_EN}"
            if "BACKGROUND ROLE EXCLUSION" not in prompt:
                parts.append(header)

        if not parts:
            return prompt
        return prompt.rstrip() + "\n\n" + "\n\n".join(parts).strip()

    @staticmethod
    def _format_named_cast_exclusion_spec(chars: List[str]) -> str:
        """L3.20: 中英双版本分段命名角色禁令"""
        import re as _re
        zh = [c for c in chars if c and _re.search(r"[一-鿿]", c)]
        en = [c for c in chars if c and not _re.search(r"[一-鿿]", c)]
        if not zh and not en:
            return ""
        lines = ["禁用命名角色（永远禁止出现在背景图中）："]
        if zh:
            lines.append(f"- 中文：{', '.join(zh)}")
        if en:
            lines.append(f"- 英文/别名：{', '.join(en)}")
        lines.append("画面中不得出现、提及或暗示以上任何角色。")
        return "\n".join(lines)

    # ============================================================
    # Stage_Background_AR L3.23-L3.28: scene-aware safety + retry + log
    # ============================================================

    async def _bbox_validate_background(
        self,
        image_path: Path,
        spec: "BackgroundSceneSpec",
        final_prompt: str,
    ) -> Tuple[str, str, Optional[Any]]:
        """
        P1-1 — bbox-only 人像验收。不触发 VLM。

        Returns:
            (verdict, reason, validation_result_obj)
            verdict ∈ {"hard_pass", "hard_fail", "suspicious", "skipped"}
            validation_result_obj 为 None 表示验收被跳过（bbox 不可用等）

        bbox 不可用（ultralytics 未装 / YOLO 权重下载失败）时返回 ("skipped", ...)
        让调用方直接接受图，不阻塞生产。
        """
        try:
            from app.services.background_image_validator_service import (
                BackgroundImageValidatorService,
            )
        except Exception as e:
            print(f"[ImageGen/BG/v2] validator import failed: {e}")
            return "skipped", f"validator import failed: {e}", None

        validator = BackgroundImageValidatorService(config=self.profiles)
        try:
            bboxes = await asyncio.to_thread(
                validator._detect_persons_bbox, image_path,
            )
        except Exception as e:
            print(f"[ImageGen/BG/v2] bbox detection exception: {e}")
            return "skipped", f"bbox exception: {e}", None

        # bbox 模型本身不可用（ultralytics 缺失 / 权重下载失败）
        if validator._bbox_unavailable_reason:
            return "skipped", validator._bbox_unavailable_reason, None

        verdict, reason = validator._bbox_verdict(bboxes, mode=spec.people_policy.mode)

        # 构造一个轻量 result 对象，记录到 validation_results
        try:
            from app.schemas import ValidationResult
            max_conf = max((b.conf for b in bboxes), default=0.0)
            max_area = max((getattr(b, "area", 0.0) for b in bboxes), default=0.0)
            v_result = ValidationResult(
                passed=(verdict == "hard_pass"),
                stage="bbox",
                reason=reason[:500],
                is_hard_fail=(verdict == "hard_fail"),
                bbox_person_count=len(bboxes),
                bbox_max_conf=max_conf,
                bbox_max_area_ratio=max_area,
            )
        except Exception as e:
            print(f"[ImageGen/BG/v2] ValidationResult build failed: {e}")
            v_result = {
                "method": "bbox_only",
                "verdict": verdict,
                "reason": reason,
                "bbox_count": len(bboxes),
            }
        return verdict, reason, v_result

    async def generate_background_with_validation(
        self,
        spec: "BackgroundSceneSpec",
        forbidden_characters: Optional[List[str]] = None,
        genre: str = "historical",
        log_ctx: Optional["GenerationLogContext"] = None,
        visual_style_profile: Optional[Dict[str, Any]] = None,
    ) -> "GenerationResult":
        """
        Stage_Background_AR — schema-driven 背景图生成入口。

        Pipeline:
        1. assembler.assemble(spec) → base prompt
        2. _enforce_background_environment_focus(spec) → 加环境焦点 + forbidden chars ban
        3. _generate_background_once_with_prompt → image_path
        4. 本地文件可解码后执行已启用的 bbox / 剧情实体泄漏验收
        5. 剧情实体泄漏会有限重试；验收未通过或重试产物无效则 quarantine
        """
        from app.schemas import GenerationResult
        from app.services.background_prompt_assembler_service import BackgroundPromptAssembler

        forbidden = forbidden_characters or spec.forbidden_characters
        assembler = BackgroundPromptAssembler(config=self.profiles)
        base_prompt = assembler.assemble(spec)
        if visual_style_profile:
            base_prompt = self._apply_visual_style_lock(
                base_prompt,
                visual_style_profile.get("background_prompt_zh"),
            )
        final_prompt = self._enforce_background_environment_focus(
            base_prompt,
            spec=spec,
            forbidden_characters=forbidden,
        )

        if log_ctx:
            log_ctx.scene_selector = spec.scene_selector
            log_ctx.add_event("generate_background_with_validation.start", {
                "scene_type": spec.scene_type,
                "people_policy": spec.people_policy.mode,
            })
            log_ctx.add_event("generate_background_with_validation.prompt", {
                "final_prompt_preview": final_prompt[:200],
            })

        print(f"[ImageGen/BG/v2] scene_selector={spec.scene_selector} scene_name={spec.scene_name!r}")
        print(f"[ImageGen/BG/v2] final_prompt (full): {final_prompt}")

        try:
            image_path = await self._generate_background_once_with_prompt(
                final_prompt,
                scene_name=spec.scene_name,
                scene_selector=spec.scene_selector,
                forbidden_characters=forbidden,
                forbidden_entities=list(spec.forbidden_entities or []),
            )
        except Exception as e:
            print(f"[ImageGen/BG/v2] generation exception: {e}")
            return GenerationResult(
                status="failed",
                final_prompt=final_prompt,
                reason=f"image generation exception: {e}",
                scene_selector=spec.scene_selector,
            )

        if image_path is None:
            print("[ImageGen/BG/v2] generation returned None")
            return GenerationResult(
                status="failed",
                final_prompt=final_prompt,
                reason="image generation returned None",
                scene_selector=spec.scene_selector,
            )

        image_path = Path(image_path)
        if image_path.exists():
            image_path = await asyncio.to_thread(
                self._postprocess_background_preserve_aspect,
                image_path,
                SIZE_PRESETS["background"],
            )
        if (
            not image_path.exists()
            or not self._is_valid_image_file(image_path)
            or self._actual_image_size(image_path) != list(SIZE_PRESETS["background"])
        ):
            print(f"[ImageGen/BG/v2] invalid generated background image: {image_path}")
            return GenerationResult(
                status="failed",
                image_path=str(image_path),
                final_prompt=final_prompt,
                reason=f"invalid generated background image: {image_path}",
                scene_selector=spec.scene_selector,
            )

        # === P1-1: bbox-only 人像验收 + 1 次重试 ===
        # 仅当 BG_VALIDATE_ENABLED=true 时启用。bbox 不可用（YOLO 未装/无网络）
        # 时跳过验收直接接受图，不阻塞生产。
        retry_count = 0
        validation_results: List[Any] = []
        if BG_VALIDATE_ENABLED:
            verdict, reason, v_result = await self._bbox_validate_background(
                image_path, spec, final_prompt,
            )
            if v_result is not None:
                validation_results.append(v_result)
            if log_ctx:
                log_ctx.add_event("bg_validation.bbox", {
                    "verdict": verdict,
                    "reason": reason,
                    "retry_count": retry_count,
                })
            print(f"[ImageGen/BG/v2] bbox verdict={verdict} reason={reason}")

            # hard_fail → 用 escalation_level_1 ban 重生成 1 次
            if verdict == "hard_fail":
                print("[ImageGen/BG/v2] hard_fail detected, retrying with escalation_level_1")
                retry_prompt = self._enforce_background_environment_focus(
                    final_prompt,
                    spec=spec,
                    forbidden_characters=forbidden,
                    escalation_level=1,
                )
                if log_ctx:
                    log_ctx.add_event("bg_retry.attempt", {
                        "reason": reason,
                        "prompt_preview": retry_prompt[:200],
                    })
                try:
                    retry_path = await self._generate_background_once_with_prompt(
                        retry_prompt,
                        scene_name=spec.scene_name,
                        scene_selector=spec.scene_selector,
                        forbidden_characters=forbidden,
                    )
                except Exception as e:
                    print(f"[ImageGen/BG/v2] retry generation exception: {e}")
                    retry_path = None

                if retry_path is not None:
                    retry_path = Path(retry_path)
                    if retry_path.exists():
                        retry_path = await asyncio.to_thread(
                            self._postprocess_background_preserve_aspect,
                            retry_path,
                            SIZE_PRESETS["background"],
                        )
                    if (
                        retry_path.exists()
                        and self._is_valid_image_file(retry_path)
                        and self._actual_image_size(retry_path)
                        == list(SIZE_PRESETS["background"])
                    ):
                        # 二次 bbox 检测
                        v2_verdict, v2_reason, v2_result = await self._bbox_validate_background(
                            retry_path, spec, retry_prompt,
                        )
                        if v2_result is not None:
                            validation_results.append(v2_result)
                        if log_ctx:
                            log_ctx.add_event("bg_validation.bbox_retry", {
                                "verdict": v2_verdict,
                                "reason": v2_reason,
                            })
                        print(f"[ImageGen/BG/v2] retry bbox verdict={v2_verdict} reason={v2_reason}")
                        if v2_verdict != "hard_fail":
                            # 重试成功，替换 image_path 和 final_prompt
                            image_path = retry_path
                            final_prompt = retry_prompt
                            retry_count = 1
                        else:
                            # 仍 fail，接受当前图（避免无限重试）
                            image_path = retry_path
                            final_prompt = retry_prompt
                            retry_count = 1
                            print("[ImageGen/BG/v2] retry still hard_fail, accepting image (bg_validation_failed_but_accepted)")
                            if log_ctx:
                                log_ctx.add_event("bg_validation_failed_but_accepted", {
                                    "reason": v2_reason,
                                })
        else:
            if log_ctx:
                log_ctx.add_event("bg_validation.skipped", {
                    "reason": "BG_VALIDATE_ENABLED=false",
                })

        # === Stage_Background_Entity_Exclusion: VLM 剧情角色泄漏验收 ===
        # 对每张产出的背景图调用 BackgroundStoryEntityValidatorService 检查
        # 是否泄漏了 forbidden_entities 中的任何剧情角色（含人类、机器人、
        # 动物、怪物、灵体等所有物种）。检测到泄漏时按 spec §XIV 重试，
        # 超出 BG_ENTITY_VALIDATE_MAX_RETRIES 后将 status 置为 quarantined。
        forbidden_entities = list(spec.forbidden_entities or [])
        if BG_ENTITY_VALIDATE_ENABLED and forbidden_entities:
            from app.services.background_story_entity_validator_service import (
                BackgroundStoryEntityValidatorService,
            )
            entity_validator = BackgroundStoryEntityValidatorService(
                config={"max_image_size": SIZE_PRESETS["background"]},
            )

            attempt = 0
            current_prompt = final_prompt
            current_path = image_path
            entity_leak_result: Optional[dict] = None
            while True:
                verdict = await entity_validator.validate(
                    current_path,
                    forbidden_entities,
                )
                verdict_dict = verdict.to_dict()
                if log_ctx:
                    log_ctx.add_event("bg_validation.entity_leak", {
                        "attempt": attempt,
                        "passed": verdict.passed,
                        "detected_count": verdict.detected_entity_count,
                        "quarantinable": verdict.quarantinable,
                        "reasons": list(verdict.reasons),
                    })
                print(
                    f"[ImageGen/BG/v2] entity-leak attempt={attempt} "
                    f"passed={verdict.passed} count={verdict.detected_entity_count}"
                )
                if verdict.passed:
                    entity_leak_result = {
                        "stage": "entity_leak",
                        "attempt": attempt,
                        "passed": True,
                        "detected_entity_count": 0,
                    }
                    break

                # 泄漏：构造 escalation 重试 prompt
                if attempt >= BG_ENTITY_VALIDATE_MAX_RETRIES:
                    # 超出上限 → quarantine
                    reasons_str = "; ".join(verdict.reasons) if verdict.reasons else "forbidden story entity leak detected"
                    if log_ctx:
                        log_ctx.add_event("bg_validation.entity_leak_quarantined", {
                            "attempt": attempt,
                            "reasons": list(verdict.reasons),
                            "matched": [
                                leak.to_dict() for leak in verdict.matched_forbidden_entities
                            ],
                        })
                    print(
                        f"[ImageGen/BG/v2] entity-leak QUARANTINED after {attempt} attempts: "
                        f"{reasons_str}"
                    )
                    return GenerationResult(
                        status="quarantined",
                        image_path=str(current_path),
                        final_prompt=current_prompt,
                        retry_count=attempt,
                        fallback_type="none",
                        scene_selector=spec.scene_selector,
                        reason=f"forbidden_story_entity_leak: {reasons_str}",
                        validation_results=validation_results + [{
                            "stage": "entity_leak",
                            "attempt": attempt,
                            "passed": False,
                            "quarantined": True,
                            "detected_entity_count": verdict.detected_entity_count,
                            "matched": [
                                leak.to_dict() for leak in verdict.matched_forbidden_entities
                            ],
                            "reasons": list(verdict.reasons),
                        }],
                    )

                # 还可重试 → 使用 escalation_level+1 + 具体泄漏提示重生成
                attempt += 1
                retry_prompt = self._enforce_background_environment_focus(
                    current_prompt,
                    spec=spec,
                    forbidden_characters=forbidden,
                    escalation_level=1,
                )
                # 在 ban clause 后追加本次检测到的具体泄漏描述
                leak_hints: List[str] = []
                for leak in verdict.matched_forbidden_entities:
                    if leak.forbidden_name:
                        leak_hints.append(
                            f"上一次生成的背景图中错误出现了禁入角色「{leak.forbidden_name}」"
                            f"（{leak.detected_entity_type}）"
                        )
                    else:
                        leak_hints.append(
                            f"上一次生成的背景图中错误出现了疑似"
                            f"{leak.detected_entity_type}主体（无具体身份）"
                        )
                if leak_hints:
                    retry_prompt = (
                        retry_prompt
                        + "\n\n【重试指令】\n"
                        + "\n".join(f"- {h}。请重新生成同一场景，保留地点/时间/天气/整体画风，"
                                    f"但彻底移除上述主体，并避免任何剪影、倒影、海报、雕像、"
                                    f"屏幕画面、全息影像、备用机体或复制体形式的间接表现。"
                                    for h in leak_hints)
                    )
                if log_ctx:
                    log_ctx.add_event("bg_retry.entity_leak", {
                        "attempt": attempt,
                        "prompt_preview": retry_prompt[:200],
                    })
                try:
                    retry_path = await self._generate_background_once_with_prompt(
                        retry_prompt,
                        scene_name=spec.scene_name,
                        scene_selector=spec.scene_selector,
                        forbidden_characters=forbidden,
                        forbidden_entities=forbidden_entities,
                    )
                except Exception as e:
                    print(f"[ImageGen/BG/v2] entity-leak retry exception: {e}")
                    retry_path = None

                if retry_path is None:
                    # 当前图已经被确认泄漏，重生成失败不能把已知坏图放行。
                    reason = "entity-leak retry generation returned no image"
                    return GenerationResult(
                        status="quarantined",
                        image_path=str(current_path),
                        final_prompt=current_prompt,
                        retry_count=attempt,
                        fallback_type="escalation",
                        scene_selector=spec.scene_selector,
                        reason=f"forbidden_story_entity_leak: {reason}",
                        validation_results=validation_results + [{
                            "stage": "entity_leak",
                            "passed": False,
                            "is_hard_fail": True,
                            "reason": reason,
                        }],
                    )

                retry_path = Path(retry_path)
                if retry_path.exists():
                    retry_path = await asyncio.to_thread(
                        self._postprocess_background_preserve_aspect,
                        retry_path,
                        SIZE_PRESETS["background"],
                    )
                if (
                    not retry_path.exists()
                    or not self._is_valid_image_file(retry_path)
                    or self._actual_image_size(retry_path)
                    != list(SIZE_PRESETS["background"])
                ):
                    reason = "entity-leak retry produced an invalid image"
                    return GenerationResult(
                        status="quarantined",
                        image_path=str(current_path),
                        final_prompt=current_prompt,
                        retry_count=attempt,
                        fallback_type="escalation",
                        scene_selector=spec.scene_selector,
                        reason=f"forbidden_story_entity_leak: {reason}",
                        validation_results=validation_results + [{
                            "stage": "entity_leak",
                            "passed": False,
                            "is_hard_fail": True,
                            "reason": reason,
                        }],
                    )

                current_path = retry_path
                current_prompt = retry_prompt
                retry_count = attempt

            if entity_leak_result is not None:
                validation_results.append(entity_leak_result)
            image_path = current_path
            final_prompt = current_prompt
        elif log_ctx:
            log_ctx.add_event("bg_validation.entity_leak.skipped", {
                "reason": (
                    "no forbidden_entities" if not forbidden_entities
                    else "BG_ENTITY_VALIDATE_ENABLED=false"
                ),
            })

        print(f"[ImageGen/BG/v2] completed scene_selector={spec.scene_selector} retry_count={retry_count}")
        return GenerationResult(
            status="completed",
            image_path=str(image_path),
            final_prompt=final_prompt,
            retry_count=retry_count,
            fallback_type="none",
            scene_selector=spec.scene_selector,
            validation_results=validation_results,
        )

    def _enforce_background_unpopulated_concise(
        self,
        prompt: str,
        forbidden_characters: Optional[List[str]] = None,
    ) -> str:
        """
        精简版「完全无人」约束（v2 用，替代 v1 的 _enforce_background_no_human_subject）。

        在 assembler 输出的 base prompt 末尾追加 3 行约束：
        1. 环境主体声明（establishing shot + 环境是主体）
        2. 注入正向环境主体声明（"画面 100% 由环境构成，无任何生物存在"）。
        3. 命名角色禁令（若有 forbidden_characters）

        不做中文剧情剥离 / 不做 risk-phrase 清洗（assembler + analyzer 已保证）。
        不做 escalation（验收器已删，无重试链路）。
        """
        if not prompt or not prompt.strip():
            return prompt

        parts: List[str] = [prompt.rstrip()]

        parts.append(
            "环境建立镜头，环境、建筑、景观、光线、天气和氛围是画面主体，"
            "整幅画面内无任何生物、角色、人形或生命体存在，呈现纯空环境"
        )

        clean_names = self._normalize_cast_names(forbidden_characters)
        if clean_names:
            names_str = ", ".join(clean_names)
            parts.append(
                f"画面中不得出现这些命名角色：{names_str}；场景为纯空环境"
            )

        return ", ".join(parts)

    # L3.28: GenerationLogContext dataclass
    @dataclass
    class GenerationLogContext:
        """标准化日志上下文（request_id 串联所有事件）"""
        request_id: str
        chapter_index: Optional[int] = None
        scene_selector: Optional[str] = None
        events: List[Dict[str, Any]] = _dc_field(default_factory=list)

        def add_event(self, event_type: str, payload: Optional[Dict[str, Any]] = None) -> None:
            self.events.append({
                "ts": datetime.utcnow().isoformat() + "Z",
                "type": event_type,
                "payload": payload or {},
            })

        def to_dict(self) -> Dict[str, Any]:
            return {
                "request_id": self.request_id,
                "chapter_index": self.chapter_index,
                "scene_selector": self.scene_selector,
                "events": self.events,
            }



    def _enforce_background_no_human_subject(
        self,
        prompt: str,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
        allow_background_people: Optional[bool] = None,
        allow_non_human_life: bool = False,
        escalation_level: int = 0,
        forbidden_entities: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        BG-NO-HUMAN-SUBJECT — 背景图最终 prompt 兜底函数（最高优先级）。

        所有送 CogView-4 的 background prompt **必须**过这一道，包括：
        - normal background prompt
        - rewriter 输出 final_prompt
        - fallback prompt
        - sanitize 之后 retry 的 prompt
        - safety fallback prompt
        - 历史批量编排间接路径

        Policy（用户硬要求，2026-06-14 升级）:
        - **所有背景图强制完全无人**。
        - 即使是集市/街道/军营/城门/宴会厅等公共场景，也**不**生成任何人物，
          包括路人、士兵、守卫、侍女、群众、剪影、背影、人形轮廓。
        - 人物绝对不能成为画面主体 / 视觉焦点 / 前景最大元素 / 中心构图。
        - 本章主要角色完全不能出现。

        说明：旧的 allow_background_people 参数保留以兼容调用点签名，
        但函数内部**忽略**该参数，永远走 "完全无人" 分支。

        做四件事：

        1. 清洗诱导"人物主体化"的风险短语（_strip_single_person_phrases）。

        2. 注入 environment focus 主句 + **强制 no-human-subject ban**。

        3. 永远写"画面 100% 由环境构成，无任何生物存在"硬规则。

        4. 本章角色点名排除。

        escalation_level: 验收失败重试时递增；每次追加更强的 ban 短语。
        """
        if not prompt or not prompt.strip():
            return prompt

        # 用户硬要求：忽略 allow_background_people 参数，永远 unpopulated
        # 保留 _detect_human_atmosphere 调用只为日志（让用户看到场景被检测过）
        try:
            _detected_human, _, _ = self._detect_human_atmosphere(
                scene_name, scene_description,
            )
        except Exception:
            _detected_human = False
        # 日志：覆盖原检测，永远走 unpopulated
        print(
            f"[ImageGen/BG] policy=enforced_unpopulated "
            f"(scene_detection={_detected_human} ignored) "
            f"所有背景图强制完全空场"
        )

        # === 0) CN-PLOT-STRIP: 主体段含中文剧情时，剥离含中文片段 ===
        # 2026-06-15 — 用户报：retry_prompt 主体段含整段中文剧情
        # （"苏墨赴约，宋雪以情报交易为名..."）。
        # 根因：rewriter 失败/未调时，outline 中文剧情作为 scene_description 进了
        # fallback builder，最终 base_prompt 主体段保留中文。
        # 现在中文安全背景 prompt 可直通；只有缺少安全锚点的中文剧情片段会被剥离。
        cleaned_prompt = self._strip_chinese_segments(prompt)

        # === 1) 清洗诱导"人物主体化"的风险短语 ===
        cleaned_prompt, was_modified = self._strip_single_person_phrases(cleaned_prompt)
        if was_modified:
            print("[ImageGen/BG] 已清理人物主体化风险短语")

        # === 2) 正向环境主体声明（恒定出现）===
        # 用正向环境陈述代替否定句，避免 CogView-4 的 CLIP 注意力被"人物/肖像"等 token 反向污染
        if allow_non_human_life:
            environment_focus_and_ban = (
                "视觉小说背景图，地点建立镜头，"
                "画面主体由建筑、景观、剧情必需的非人类动物、道具、光线、天气、材质和空间氛围构成，"
                "不得出现任何人类、人物、人脸、人形轮廓或角色替代物；"
                "只保留当前幕主指令明确要求的非人类动物，不得从历史回顾、比喻或前后文自行引入"
            )
        else:
            environment_focus_and_ban = (
                "视觉小说背景图，地点建立镜头，"
                "画面 100% 由建筑、景观、道具、光线、天气、材质和空间氛围构成，"
                "环境、空间与光影是唯一画面主体，"
                "整幅画面内无任何生物、角色、人形或生命体存在，呈现纯空环境"
            )

        # === 3) 空场景声明（用户硬要求：所有场景都必须是纯空环境）===
        crowd_clause = (
            "无人类场景声明：画面内不出现任何人类、角色、人脸、人形、剪影或背影；"
            "仅允许正文明确要求的非人类动物作为环境叙事元素"
            if allow_non_human_life
            else (
                "空场景声明：画面内只有静态环境元素，"
                "所有空间均未被任何生物、角色、人形或生命体占据，"
                "呈现纯粹的建筑或景观环境"
            )
        )

        # === 4) escalation（验收失败重试时强化空场景声明） ===
        escalation_clauses = (
            [
                "强化无人类场景：不得出现任何人类、角色、人脸、人形、剪影或背影",
                "再次强化无人类场景：所有人物均由前端立绘承担，背景不得绘制人类主体",
                "终极无人类场景声明：画面中不存在任何人类或人形，仅保留正文明确要求的非人类动物",
            ]
            if allow_non_human_life
            else [
                # level 1
                "强化空场景：画面是纯粹的建筑或景观环境，整幅画面内不出现任何生物、角色、人形或生命体存在",
                # level 2
                "再次强化空场景：画面 100% 由静态环境构成，整幅画面内无任何生命体、生物或人形",
                # level 3+
                "终极空场景声明：画面是纯环境画面，整幅画面内不出现任何生命体、生物、角色或人形存在",
            ]
        )
        escalation_clause = ""
        for lvl in range(min(escalation_level, len(escalation_clauses))):
            escalation_clause = escalation_clauses[lvl]

        # === 5) 本章角色点名排除 ===
        named_cast_exclusion = ""
        clean_names = self._normalize_cast_names(forbidden_characters)
        if clean_names:
            names_str = ", ".join(clean_names)
            named_cast_exclusion = (
                f"画面中不得出现这些命名角色：{names_str}；"
                f"场景为纯空环境，无任何角色或人形存在"
            )

        # === 6) Stage_Background_Entity_Exclusion: 剧情角色实体禁入 ===
        # legacy 路径同样要打这个块——它扩展了禁入范围到机器人/动物/怪物。
        # 没有结构化 entities 时也要打 header（规则本身是"无物种前提"）。
        from app.services.background_story_entity_text import (
            render_full_exclusion_block,
        )

        entity_block = render_full_exclusion_block(
            entities=forbidden_entities or [],
            legacy_names=clean_names,
        )

        # 拼接：清洗后的原始 prompt 在前，约束在后（CogView-4 对末尾 token 敏感）
        parts = [
            cleaned_prompt.strip().rstrip(","),
            environment_focus_and_ban,
            crowd_clause,
        ]
        if escalation_clause:
            parts.append(escalation_clause)
        if named_cast_exclusion:
            parts.append(named_cast_exclusion)
        if entity_block:
            parts.append(entity_block)

        return ", ".join(parts[:4]) + ("\n\n" + "\n\n".join(parts[4:]) if parts[4:] else "")

    # 场景类型词库 —— 决定是否允许"远景匿名人物"氛围
    # 优先级：先看 crowd_allowed（公共活动场景）→ 再看 unpopulated（私人/安静/自然/废墟）
    # 都没命中默认按"无人"处理（用户要求：默认优先生成无人环境图）
    _CROWD_ALLOWED_KEYWORDS: List[str] = [
        # English
        "market", "street", "city gate", "palace gate", "military camp",
        "battlefield", "harbor", "dock", "tavern", "inn", "banquet hall",
        "festival", "crowd", "army", "soldiers", "guards", "village square",
        "public square", "square", "fair", "bazaar", "courtyard gathering",
        "court hall", "throne room", "audience chamber", "procession",
        "parade", "caravan", "festival square", "market square",
        # 中文
        "城门", "宫门", "集市", "街道", "军营", "战场", "码头", "驿站",
        "酒楼", "宴会", "节日", "人群", "士兵", "守卫", "村口", "广场",
        "朝堂", "大殿", "议事厅", "市集", "街市", "酒馆", "客栈",
        "队伍", "游行", "庙会", "集市广场",
    ]

    _UNPOPULATED_KEYWORDS: List[str] = [
        # English
        "bedroom", "study room", "study", "secret room", "forest", "mountain",
        "riverbank", "river bank", "courtyard", "empty alley", "ruins",
        "dream space", "dreamscape", "cave", "temple interior", "quiet room",
        "night scene", "night scene alley", "landscape", "abandoned",
        "deserted", "empty room", "private room", "garden path", "void",
        "stillness", "solitude", "wilderness", "meadow", "field",
        "shore", "lake", "pond", "stream", "waterfall", "cliff", "valley",
        "clearing", "grove", "orchard", "wild field", "prison cell",
        # 中文
        "卧室", "书房", "密室", "山林", "河岸", "庭院", "空巷", "废墟",
        "梦境", "山洞", "寺庙内部", "寺庙", "夜景", "风景", "荒废", "无人",
        "寂静", "空房", "花园", "荒野", "田野", "湖", "溪", "瀑布", "悬崖",
        "山谷", "林间", "果园", "牢房", "静室", "禅房", "梦境空间",
        "荒地", "废园", "空地", "空山",
    ]

    def _detect_human_atmosphere(
        self,
        scene_name: Optional[str],
        scene_description: Optional[str],
    ) -> tuple[bool, list, list]:
        """
        BG-DEFAULT-NO-PEOPLE — 判断场景是否需要"远景匿名人物氛围"。

        优先级（与用户要求严格一致）:
        1. 命中 unpopulated 关键词 → False（强制无人）
        2. 命中 crowd_allowed 关键词 → True（允许小比例匿名陪体）
        3. 都没命中 → False（默认无人；宁可漏陪体，也不滥用路人）

        Returns:
            (allow_human, matched_crowd_kws, matched_unpopulated_kws)
        """
        text = " ".join(filter(None, [scene_name or "", scene_description or ""])).lower()
        if not text.strip():
            return False, [], []

        matched_unpopulated = [w for w in self._UNPOPULATED_KEYWORDS if w.lower() in text]
        matched_crowd = [w for w in self._CROWD_ALLOWED_KEYWORDS if w.lower() in text]

        # unpopulated 优先级最高（卧室里有士兵也不行；密室里有市集也不行）
        if matched_unpopulated:
            return False, matched_crowd, matched_unpopulated
        if matched_crowd:
            return True, matched_crowd, matched_unpopulated
        # 默认无人
        return False, [], []

    def _enforce_background_crowd_only_or_environment_focus(
        self,
        prompt: str,
        forbidden_characters: Optional[List[str]] = None,
        scene_name: Optional[str] = None,
        scene_description: Optional[str] = None,
    ) -> str:
        """
        BG-CROWD-ONLY（旧名 alias） — 实际逻辑在 _enforce_background_no_human_subject。
        保留以兼容现有测试 / 调用点。
        """
        return self._enforce_background_no_human_subject(
            prompt,
            forbidden_characters=forbidden_characters,
            scene_name=scene_name,
            scene_description=scene_description,
        )

    def _strip_chinese_segments(self, prompt: str) -> str:
        """
        CN-PLOT-STRIP (2026-06-15) — prompt 主体段含中文剧情时，剥离含中文字符的片段。

        用户报：retry_prompt 主体段含整段中文剧情（如"苏墨赴约，宋雪以情报交易为名..."）。
        根因：rewriter 失败/未调时，outline 中文剧情作为 scene_description 进了
        fallback builder，最终 base_prompt 主体段保留中文；_enforce 之前只追加约束
        不动主体段，导致中文剧情原样送进 CogView-4。

        做法：如果 prompt 已经带有明确的中文环境安全锚点，则原样保留；
        否则按英文逗号 + 中文逗号/句号/分号切分，丢弃任何含中文字符的片段，
        保留所有纯英文片段（style/lighting/composition 等）。
        """
        if not prompt:
            return prompt
        safe_zh_background_anchors = (
            "视觉小说背景图",
            "中性环境背景",
            "地点建立镜头",
            "环境建立镜头",
            "只呈现建筑",
            "只展示建筑",
            "只保留空环境",
            "画面主体只能是环境",
        )
        if any(anchor in prompt for anchor in safe_zh_background_anchors):
            return prompt
        import re as _re
        # 切分：英文逗号 / 中文逗号 / 句号 / 分号 / 感叹号 / 问号
        segments = _re.split(r"\s*[，,。！？；;]+\s*", prompt)
        kept = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # 含任何中文字符就丢弃
            if _re.search(r"[一-鿿]", seg):
                continue
            kept.append(seg)
        return ", ".join(kept)

    def _strip_single_person_phrases(self, prompt: str) -> tuple:
        """
        BG-CROWD-ONLY — 清洗诱导单独人物主体的风险短语。

        匹配模式（大小写不敏感，正则 \b 边界）：
        - "a man stands" / "a woman stands" / "a girl stands" / "a boy stands"
        - "a lone figure" / "a lone guard" / "a lone soldier" / "a solitary person"
        - "standing in the center" / "in the foreground"
        - "looking at camera" / "looking at the camera"
        - "full body" / "half body" / "portrait" / "hero shot"
        - "character focus" / "dramatic figure"

        替换策略：直接删除匹配片段，并在末尾追加 crowd-only 兜底标签。
        不试图改写成具体环境描述（环境信息由原 prompt 主体承担，这里只清除风险）。

        Returns:
            (cleaned_prompt, was_modified)
        """
        if not prompt:
            return prompt, False

        import re

        # 风险短语 → 替换（直接删除整段）
        # 用 re.IGNORECASE 做大小写不敏感整词匹配
        risk_patterns: list[tuple[str, str]] = [
            # 单独人物主体表达
            (r"\ba lone figure\b", ""),
            (r"\ba lone guard\b", ""),
            (r"\ba lone soldier\b", ""),
            (r"\ba solitary person\b", ""),
            (r"\ba solitary figure\b", ""),
            (r"\ba single figure\b", ""),
            (r"\ba single person\b", ""),
            # 单人站位（用户列表全量覆盖）
            (r"\ba man stands\b", ""),
            (r"\ba woman stands\b", ""),
            (r"\ba girl stands\b", ""),
            (r"\ba boy stands\b", ""),
            (r"\ba person stands\b", ""),
            (r"\ba guard stands\b", ""),
            (r"\ba soldier stands\b", ""),
            (r"\ba maid stands\b", ""),
            (r"\bstanding in the center\b", ""),
            (r"\bstanding at the center\b", ""),
            (r"\bin the center of the frame\b", ""),
            (r"\blooking at (?:the )?camera\b", ""),
            (r"\bfacing (?:the )?camera\b", ""),
            (r"\blooking into (?:the )?distance\b", ""),
            # 前景人物 / 中心构图
            (r"\ba (?:person|figure|man|woman|girl|boy|guard|soldier|attendant|maid|servant) in the foreground\b", ""),
            (r"\bin the foreground,?\s+a (?:person|figure|man|woman|girl|boy|guard|soldier)\b", ""),
            (r"\bin the foreground\b", ""),
            (r"\bin the center of the (?:frame|composition|image)\b", ""),
            (r"\bforeground (?:person|figure|man|woman|girl|boy)\b", ""),
            (r"\bcentral figure\b", ""),
            (r"\bcentral (?:person|character|man|woman|girl|boy)\b", ""),
            # 立绘/关键帧/人物主体诱导词
            (r"\bfull[\s-]?body (?:character|shot|portrait|illustration)\b", ""),
            (r"\bhalf[\s-]?body (?:character|shot|portrait|illustration)\b", ""),
            (r"\bfull body\b", ""),
            (r"\bhalf body\b", ""),
            (r"\bhero shot\b", ""),
            (r"\bcharacter focus\b", ""),
            (r"\bcharacter emphasis\b", ""),
            (r"\bdramatic (?:figure|pose)\b", ""),
            (r"\bdetailed clothing\b", ""),
            (r"\bdetailed costume\b", ""),
            (r"\bportrait[\s-]?style\b", ""),
            (r"\bcharacter design\b", ""),
            # 用户列表中遗漏的诱导词
            (r"\bsomeone standing\b", ""),
            (r"\ba figure watching\b", ""),
            (r"\ba lone silhouette\b", ""),
            (r"\bsilhouette in the foreground\b", ""),
            (r"\bperson in the center\b", ""),
            (r"\bmultiple figures in the foreground\b", ""),
            (r"\ba guard at the gate\b", ""),
            (r"\ba maid in the corridor\b", ""),
            (r"\ba soldier standing outside\b", ""),
            # 中文风险短语
            ("一个守卫站在", ""),  # 删除整段会留"门前"，但前面有 architecture 主词能 hold 住
            ("一个侍女站在", ""),
            ("一个士兵守", ""),
            ("一名守卫", ""),
            ("一名侍女", ""),
            ("一名士兵", ""),
            ("一个人站在", ""),
            ("孤独的身影", ""),
            ("独自站立", ""),
        ]

        cleaned = prompt
        modified = False
        for pattern, replacement in risk_patterns:
            new, n = re.subn(pattern, replacement, cleaned, flags=re.IGNORECASE)
            if n > 0:
                cleaned = new
                modified = True

        if not modified:
            return prompt, False

        # 压缩多余空格、逗号
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        cleaned = re.sub(r"\s*,\s*,+\s*", ", ", cleaned)
        cleaned = re.sub(r"^\s*,\s*|\s*,\s*$", "", cleaned)
        return cleaned.strip(), True

    def _normalize_cast_names(self, forbidden_characters: Optional[List[str]]) -> List[str]:
        """
        从 forbidden_characters 列表清洗出有效角色名（去重、过滤脏数据）。
        返回最多 8 个名字。
        """
        if not forbidden_characters:
            return []
        seen = set()
        clean_names = []
        for raw_name in forbidden_characters:
            if not raw_name:
                continue
            name = str(raw_name).strip()
            if not name or name.lower() in seen:
                continue
            if len(name) > 30:
                continue
            # 过滤明显是描述而不是名字的（含逗号/句号/换行）
            if any(ch in name for ch in [",", ".", "\n", ";", "，", "。", "；"]):
                continue
            seen.add(name.lower())
            clean_names.append(name)
        return clean_names[:8]

    def _build_cast_negation(self, forbidden_characters: Optional[List[str]]) -> str:
        """
        BG-NOCAST（旧版兼容）— 把角色名转成 "no <name>" 列表。
        新代码请用 _enforce_background_environment_focus。
        """
        clean_names = self._normalize_cast_names(forbidden_characters)
        if not clean_names:
            return ""
        return ", ".join(f"no {n}" for n in clean_names)

    def _strip_clothing_words(self, style_prompt: str) -> str:
        """
        B05 — 从 genre style_prompt 移除服装相关词。
        原 profiles 里 historical style 是 "Chinese historical style, hanfu, traditional Chinese clothing"
        ——hanfu/clothing 不该出现在背景 prompt 里。
        """
        if not style_prompt:
            return ""
        forbidden = ["hanfu", "clothing", "clothes", "apparel", "garment", "costume", "armor", "robe"]
        tokens = [t.strip() for t in style_prompt.split(",")]
        cleaned = [t for t in tokens if not any(w in t.lower() for w in forbidden)]
        return ", ".join(cleaned) if cleaned else style_prompt

    def get_lighting_prompt(self, mood: str) -> str:
        """
        B08 — 独立光照字段。优先用 lighting_presets[mood]，缺失时退到 mood_prompt。
        """
        presets = self.profiles.get("lighting_presets", {})
        if mood in presets:
            return presets[mood]
        if mood == "default":
            return presets.get("soft_diffused", "soft diffused lighting")
        return self.get_mood_prompt(mood)


    async def generate_keyframe(
        self,
        event_name: str,
        scene_description: str,
        characters: List[Dict[str, str]],
        action: str,
        emotion: str = "intense",
        genre: Optional[str] = None,
        final_prompt: Optional[str] = None,
        visual_style_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        生成关键帧

        Args:
            event_name: 事件名称
            scene_description: 场景描述
            characters: 角色列表 [{"name": "xxx", "appearance": "xxx"}]
            action: 动作描述
            emotion: 整体情绪
            genre: 题材族
            final_prompt: C01 — 若传入则跳过 _build_keyframe_prompt 直接使用
        """
        if final_prompt:
            full_prompt = self._apply_visual_style_lock(final_prompt, visual_style_prompt)
        else:
            if not genre:
                genre = self.infer_genre(scene_description)

            style_prompt, _ = self.get_genre_style(genre)
            if visual_style_prompt:
                style_prompt = visual_style_prompt

            # 构建关键帧 prompt
            full_prompt = self._build_keyframe_prompt(
                scene_description=scene_description,
                characters=characters,
                action=action,
                emotion=emotion,
                style_prompt=style_prompt
            )

        # 生成图像
        width, height = SIZE_PRESETS["keyframe"]
        result = await self.generate_image(
            prompt=full_prompt,
            asset_type="keyframe",
            width=width,
            height=height
        )

        if result["success"]:
            # provider 可能忽略尺寸；按水印开关处理后等比归一化到 16:9。
            image_filename = Path(result["image_url"]).name
            image_path = self.output_dir / "keyframes" / image_filename
            await asyncio.to_thread(
                self._postprocess_background_preserve_aspect,
                image_path,
                SIZE_PRESETS["keyframe"],
            )
            actual_size = self._actual_image_size(image_path)
            if actual_size != list(SIZE_PRESETS["keyframe"]):
                try:
                    image_path.unlink()
                except OSError:
                    pass
                return {
                    "success": False,
                    "error": (
                        "关键帧后处理后尺寸无效: "
                        f"actual={actual_size}, expected={list(SIZE_PRESETS['keyframe'])}"
                    ),
                }

            result["event_name"] = event_name
            result["prompt"] = full_prompt
            result["genre"] = genre
            result["visual_style_prompt"] = visual_style_prompt
            result["size"] = actual_size

        return result

    def _build_keyframe_prompt(
        self,
        scene_description: str,
        characters: List[Dict[str, str]],
        action: str,
        emotion: str,
        style_prompt: str
    ) -> str:
        """
        构建关键帧 prompt (fallback 路径，C03/C04/C05/C08/C09 优化过)。
        - C03: 多角色显式位置 (left/center/right)
        - C04: emotion 用 emotion_intensity_for_keyframe 词库
        - C05: 构图表述
        - C08: action 视觉强动词（caller 已用 action_verbs 增强）
        - C09: no_text 缩到尾部
        """

        # C03 — 多角色显式位置
        positions = ["on the left", "in the center", "on the right"]
        char_parts = []
        for i, char in enumerate(characters[:3]):
            appearance = char.get("appearance", "")
            name = char.get("name", f"character {i+1}")
            pos = positions[i] if i < len(positions) else "in the background"
            if appearance:
                char_parts.append(f"{name} ({pos}): {appearance}")
            else:
                char_parts.append(f"{name} ({pos})")
        chars_text = "; ".join(char_parts) if char_parts else "characters in frame"

        # C04 — 用 emotion_intensity_for_keyframe 词库
        emotion_intensity = self.profiles.get("emotion_intensity_for_keyframe", {})
        # 章节情绪 → 强度档
        intensity_key = self._map_emotion_to_intensity(emotion)
        emotion_text = emotion_intensity.get(intensity_key, "intense expression, dramatic")

        # C05 — 构图
        if len(characters) >= 2:
            composition = "medium-wide shot, two-character confrontation, slight low angle for tension"
        else:
            composition = "cinematic medium shot, dramatic angle"

        full_prompt = f"""
{chars_text},
{action},
{emotion_text},
{scene_description},
{style_prompt},
{composition},
dramatic lighting, depth of field, dynamic composition, action shot,
visual novel CG art, official game art quality,
no text, no watermark
        """.strip().replace('\n', ' ').replace('  ', ' ')

        return full_prompt

    def _map_emotion_to_intensity(self, emotion: str) -> str:
        """C04 — 把章节情绪映射到 emotion_intensity_for_keyframe 的强度档。"""
        mapping = {
            "intense": "intense_high",
            "neutral": "calm_serene",
            "calm": "calm_serene",
            "happy": "happy_warm",
            "sad": "sad_melancholy",
            "angry": "angry_cold",
            "fear": "fear_dread",
            "surprise": "surprise_shock",
        }
        return mapping.get(emotion, "intense_medium")



# 全局实例
image_generation_service = ImageGenerationService()
