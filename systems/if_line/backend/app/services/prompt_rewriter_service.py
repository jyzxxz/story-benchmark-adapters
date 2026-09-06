"""
Prompt Rewriter Service (Stage_Prompt_AR D01/D02/D03/D04/D06/D07/D08/D09/D12/D13/D17/D18)

把 prompt_builder_service 产出的结构化字段（中文为主）通过 LLM 重写成信息密度高、
视觉可解析、CogView-4 友好的 prompt。Portrait 路径由 LLM 直接返回唯一的
``final_prompt``；后端只校验、不再追加景别或画风片段，避免相反指令被拼到一起。

设计哲学见 Docs/researches/Stage_Prompt_AR/00_philosophy.md。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas import BackgroundPromptData

from pydantic import BaseModel, Field, PrivateAttr, ValidationError

from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.character_age_contract import age_prompt_validation_errors
from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_MODEL,
    resolve_request_model,
    structured_json_request_options,
    text_llm_api_key,
    text_llm_base_url,
    text_llm_model,
)

logger = logging.getLogger("prompt_rewriter")

AssetType = Literal["portrait", "background", "keyframe"]

PROMPT_REWRITER_ENABLED = os.getenv("PROMPT_REWRITER_ENABLED", "true").lower() == "true"
REWRITER_MODEL = text_llm_model("PROMPT_REWRITER_MODEL", default=DEFAULT_TEXT_LLM_MODEL)
REWRITER_API_KEY = text_llm_api_key("REWRITER_API_KEY")
REWRITER_BASE_URL = text_llm_base_url("REWRITER_BASE_URL")
REWRITER_TIMEOUT = float(os.getenv("REWRITER_TIMEOUT", "20.0"))
REWRITER_MAX_CONCURRENCY = int(os.getenv("REWRITER_MAX_CONCURRENCY", "5"))
PORTRAIT_REWRITER_MAX_REPAIRS = int(os.getenv("PORTRAIT_REWRITER_MAX_REPAIRS", "2"))
PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION = "portrait-llm-authoritative-v3-subject-first"

_TARGET_TOKEN_BUDGET = {
    "portrait": 140,
    "background": 120,
    "keyframe": 150,
}

_CJK_RE = re.compile(r"[一-鿿]")

_PORTRAIT_FULL_BODY_RE = re.compile(
    r"\b(?:full[- ]body|head[- ]to[- ](?:toe|feet)|head to (?:toe|feet)|"
    r"entire (?:body|character))\b",
    re.IGNORECASE,
)
_PORTRAIT_PARTIAL_BODY_RE = re.compile(
    r"\b(?:half[- ]body|waist[- ]up|from (?:the )?waist up|"
    r"knees?[- ]up|from (?:the )?knees? up|three[- ]quarter body)\b",
    re.IGNORECASE,
)
_PORTRAIT_TRANSPARENT_RE = re.compile(
    r"\b(?:fully\s+)?transparent(?:\s+rgba|\s+alpha(?:-channel)?)?\s+background\b",
    re.IGNORECASE,
)
_PORTRAIT_SINGLE_CHARACTER_RE = re.compile(
    r"\b(?:exactly one|one isolated|single)\s+(?:isolated\s+)?character\b",
    re.IGNORECASE,
)


def _has_chinese(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _approx_token_count(text: str) -> int:
    """Rough token estimate: 1 token ~ 4 chars for English."""
    return max(1, len(text or "") // 4)


def _short_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def validate_portrait_final_prompt(
    prompt: str,
    requested_shot: str,
    subject_opening: str = "",
) -> List[str]:
    """Validate the LLM-authored portrait prompt without rewriting it.

    The provider prompt is authoritative LLM output, so validation must reject
    contradictions instead of repairing them by concatenating backend clauses.
    """
    text = re.sub(r"\s+", " ", (prompt or "").strip())
    errors: List[str] = []
    if len(text) < 40:
        errors.append("portrait final_prompt too short")
        return errors
    if _has_chinese(text):
        errors.append("portrait final_prompt contains Chinese characters")
    required_opening = re.sub(r"\s+", " ", (subject_opening or "").strip())
    if required_opening and not text.casefold().startswith(required_opening.casefold()):
        errors.append(
            "portrait final_prompt must start exactly with subject_opening: "
            f"{required_opening}"
        )

    shot = (requested_shot or "full_body").strip().lower().replace("-", "_")
    has_full_body = bool(_PORTRAIT_FULL_BODY_RE.search(text))
    has_partial_body = bool(_PORTRAIT_PARTIAL_BODY_RE.search(text))
    if shot == "full_body":
        if not has_full_body:
            errors.append("full_body prompt is missing a full-body framing instruction")
        if has_partial_body:
            errors.append("full_body prompt contains a partial-body framing instruction")
    elif shot == "half_body":
        if not has_partial_body:
            errors.append("half_body prompt is missing a waist-up framing instruction")
        if has_full_body:
            errors.append("half_body prompt contains a full-body framing instruction")
    else:
        errors.append(f"unsupported portrait shot: {requested_shot}")

    if not _PORTRAIT_TRANSPARENT_RE.search(text):
        errors.append("portrait prompt is missing a transparent-background instruction")
    if not _PORTRAIT_SINGLE_CHARACTER_RE.search(text):
        errors.append("portrait prompt is missing an exactly-one-character instruction")
    return errors


class RewrittenPrompt(BaseModel):
    """D03 — LLM Rewriter 的统一输出 schema。

    Portrait 使用 ``final_prompt`` 作为大模型一次性融合后的最终输出。
    Background/keyframe 仍使用下方结构化字段，保持现有链路兼容。

    Background/keyframe 的兼容结构仍可通过 ``style_locked`` 丢弃独立
    ``style`` 字段；portrait 的画风已经融合在 ``final_prompt`` 内。
    """

    final_prompt: str = Field(
        default="",
        description="Portrait 专用：LLM 已融合所有约束的最终供应商 Prompt",
    )
    subject: str = Field(default="", description="主体描述，人物/场景/事件，必须英文，60-150 字符")
    details: List[str] = Field(default_factory=list, description="视觉细节列表，每项 5-15 词，英文")
    lighting: str = Field(default="", description="光照与氛围，英文")
    composition: str = Field(default="", description="构图、景别、镜头，英文")
    style: str = Field(default="", description="[DEPRECATED] 画风字段 — 由后端 ProjectVisualBible 注入，LLM 输出会被丢弃")
    negative_minimal: List[str] = Field(default_factory=list, description="最小化负面提示，<=5 词")
    style_locked: bool = Field(
        default=True,
        description="True → style 字段不进入最终 prompt；False → 兼容旧行为（仅测试用）",
    )
    # Backend-owned keyframe lock. A private attribute keeps untrusted LLM JSON
    # from supplying this prefix while allowing trusted Chinese character names.
    _trusted_identity_lock: str = PrivateAttr(default="")

    def to_cogview_prompt(
        self,
        *,
        locked_style: str = "",
        use_final_prompt: bool = True,
    ) -> str:
        """
        D09 — 序列化为 CogView-4 友好的单行 prompt。
        词序: 主体 → 风格 → 细节 → 光照 → 构图 → 否定（D09 推荐顺序，主体优先）。

        CN-LEAK-GUARD (2026-06-15): 若任何字段含中文字符（说明 rewriter 违反了
        LANGUAGE RULE 把中文剧情原样吐回），整段字段被丢弃，避免污染 CogView-4。
        """
        if self.final_prompt and use_final_prompt:
            return self.final_prompt.strip()

        def _en_only(s: str) -> str:
            """去掉含中文的字段值。若 s 内有中文字符，返回空串。"""
            if not s:
                return ""
            if re.search(r"[一-鿿]", s):
                return ""
            return s.strip()
        parts: List[str] = []
        trusted_identity_lock = self._trusted_identity_lock.strip()
        if trusted_identity_lock:
            parts.append(
                trusted_identity_lock + "\n\nIDENTITY-PRESERVING SCENE:"
            )
        en_subject = _en_only(self.subject)
        if en_subject:
            parts.append(en_subject)
        trusted_style = locked_style.strip()
        if trusted_style:
            parts.append(trusted_style)
        # With no trusted style, style_locked=False keeps the explicit legacy
        # compatibility path used by old callers and tests.
        elif not self.style_locked:
            en_style = _en_only(self.style)
            if en_style:
                parts.append(en_style)
        if self.details:
            en_details = [_en_only(d) for d in self.details if d]
            en_details = [d for d in en_details if d]
            if en_details:
                parts.append(", ".join(en_details))
        en_lighting = _en_only(self.lighting)
        if en_lighting:
            parts.append(en_lighting)
        en_composition = _en_only(self.composition)
        if en_composition:
            parts.append(en_composition)
        if self.negative_minimal:
            neg_items = [_en_only(w) for w in self.negative_minimal if w]
            neg_items = [w for w in neg_items if w]
            if neg_items:
                # 去掉前缀 "no "（如果 LLM 已自带）
                cleaned_neg = []
                for w in neg_items:
                    w2 = w.strip()
                    if w2.lower().startswith("no "):
                        w2 = w2[3:].strip()
                    cleaned_neg.append(w2)
                neg = ", ".join(f"no {w}" for w in cleaned_neg)
                parts.append(neg)
        text = ", ".join(p for p in parts if p)
        return re.sub(r"\s+", " ", text).strip()

    def to_background_cogview_prompt(self, *, locked_style: str = "") -> str:
        """
        背景图专用序列化。

        背景 prompt 已改为中文输出，不能复用 to_cogview_prompt() 的英文字段过滤。
        词序保持一致：主体 → 风格 → 细节 → 光照 → 构图 → 空场/禁令。
        """
        parts: List[str] = []
        if self.subject:
            parts.append(self.subject.strip())
        trusted_style = locked_style.strip()
        if trusted_style:
            parts.append(trusted_style)
        elif not self.style_locked and self.style:
            parts.append(self.style.strip())
        if self.details:
            details = [d.strip() for d in self.details if d and d.strip()]
            if details:
                parts.append("，".join(details))
        if self.lighting:
            parts.append(self.lighting.strip())
        if self.composition:
            parts.append(self.composition.strip())

        negatives = [w.strip() for w in self.negative_minimal if w and w.strip()]
        if negatives:
            parts.append("避免：" + "、".join(negatives))
        parts.append(
            "视觉小说背景图，地点建立镜头，画面主体只能是环境、建筑、景观、道具、光线、天气和空间氛围，"
            "场景必须完全空无一人，不要文字，不要水印"
        )

        text = "，".join(p for p in parts if p)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def to_cogview_prompt_from_background_data(data: "BackgroundPromptData") -> str:
        """
        L3.30 — schema-only 路径：从 BackgroundPromptData 直接拼 CogView-4 友好 prompt。

        - 不访问 outline / summary（schema-only）
        - 不允许 plot/character 泄漏（BackgroundPromptData validator 已保证）
        - 输出中文背景 prompt，避免前端和生成日志混入英文约束
        - 输出顺序：environment → architecture/props → lighting/weather → camera → ban
        """
        parts: List[str] = []
        if getattr(data, "environment_description", None):
            parts.append(data.environment_description.strip())
        if data.style_tags:
            parts.append("风格标签：" + "，".join(t for t in data.style_tags if t))
        if data.camera_shot_type:
            cam_phrase_map = {
                "establishing_wide": "广角环境建立镜头",
                "environment_medium": "35mm环境广角镜头",
                "high_angle_distant": "高机位远景",
                "interior_wide": "室内广角镜头",
                "telephoto_compressed": "长焦压缩远景",
                "low_angle_perspective": "低机位纵深",
            }
            parts.append(cam_phrase_map.get(data.camera_shot_type, "环境建立镜头"))
        parts.append(
            "视觉小说背景图，地点建立镜头，环境是画面主体；"
            "不要人物、人体、剪影、人脸、人像照片或背景人物，场景完全空无一人"
        )
        chars = [c for c in (data.forbidden_characters or []) if c]
        if chars:
            parts.append(f"不得出现命名角色：{'、'.join(chars[:8])}")

        text = "，".join(p for p in parts if p)
        return re.sub(r"\s+", " ", text).strip()

    def guardrail_check(
        self,
        asset_type: AssetType,
        *,
        provider_prompt: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        D12 — 输出后置校验。返回 (ok, errors)。
        BG-ENFORCE — 对 background 强制 negative_minimal 必须含"主体化"否定词
        （防止本章角色成为画面主体）。背景图最终由 assembler/enforcer
        统一强制完全无人。
        """
        errors: List[str] = []
        primary_text = (
            self.final_prompt or self.subject
            if asset_type == "portrait"
            else self.subject
        )
        if not primary_text or len(primary_text) < 20:
            errors.append("subject too short or empty")
        if asset_type != "background" and (_has_chinese(primary_text) or _has_chinese(self.style)):
            errors.append("chinese characters leaked into subject/style")
        budget = _TARGET_TOKEN_BUDGET.get(asset_type, 120)
        if provider_prompt is None:
            full = (
                self.to_background_cogview_prompt()
                if asset_type == "background"
                else self.to_cogview_prompt(
                    use_final_prompt=asset_type == "portrait",
                )
            )
        else:
            full = provider_prompt
        if _approx_token_count(full) > budget * 2:
            errors.append(f"prompt too long: ~{_approx_token_count(full)} tokens > {budget*2}")
        if len(self.negative_minimal) > 5:
            errors.append(f"negative_minimal too long: {len(self.negative_minimal)} > 5")
        # BG-ENFORCE — background 必须 negative_minimal 含至少 2 个"主体化"否定词
        # 这些词防止本章角色被画成主体（portrait/hero/full-body/...）。
        if asset_type == "background":
            main_char_words = {
                "named characters", "named character", "main character",
                "portrait", "hero shot", "heroine shot",
                "close-up face", "closeup face",
                "full-body character", "full body character", "full-body",
                "half-body character", "half body character", "half-body",
                "detailed costume", "character design",
                "命名角色", "主要角色", "人物主体", "肖像", "英雄式人物镜头",
                "面部特写", "全身人物", "半身人物", "服装特写", "人物设计",
            }
            neg_joined = ", ".join(self.negative_minimal).lower()
            hits = sum(1 for w in main_char_words if w in neg_joined)
            if hits < 2:
                errors.append(
                    f"background negative_minimal must include >=2 main-character-class words "
                    f"(named characters / main character / portrait / hero shot / close-up face / "
                    f"full-body / half-body / detailed costume); got {self.negative_minimal}"
                )
        return (len(errors) == 0, errors)


@dataclass
class RewriteStats:
    """D18 — 单次 rewrite 的监控指标。"""
    asset_type: AssetType
    success: bool
    cached: bool
    fallback: bool
    duration_s: float
    input_chars: int
    output_chars: int
    approx_tokens: int
    error: str = ""


@dataclass
class _CacheEntry:
    output: RewrittenPrompt
    ts: float


class PromptRewriterService:
    """
    D01 — 统一的 LLM Rewriter 接口。
    三个 builder (portrait/background/keyframe) 共用此服务。

    关键特性:
    - D04: LLM/JSON 解析失败 → 返回 None；portrait 调用方 fail closed，
      background/keyframe 保留各自兼容降级策略
    - D06: 输入 hash 缓存（进程内 + 跨进程可选）
    - D07: rewrite_many 批量调用，减少 round-trip
    - D08: 每个 asset_type 有自己的 token budget
    - D09: to_cogview_prompt 词序固定，主体优先
    - D12: 输出 guardrail 校验，失败视为 fallback
    - D17: PROMPT_REWRITER_ENABLED 全局开关
    - D18: 每次 rewrite 记录 RewriteStats，便于监控
    """

    def __init__(self) -> None:
        self._cache: Dict[str, _CacheEntry] = {}
        self._cache_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(REWRITER_MAX_CONCURRENCY)
        self._stats: List[RewriteStats] = []
        self._stats_lock = asyncio.Lock()
        self._client: Optional[Any] = None
        self._profiles: Dict[str, Any] = self._load_profiles()

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=REWRITER_API_KEY,
                pool_env=("REWRITER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=REWRITER_BASE_URL,
            )
        return self._client

    def _load_profiles(self) -> Dict[str, Any]:
        path = Path(__file__).parent.parent / "config" / "image_generation_profiles.json"
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("load profiles failed: %s", e)
            return {}

    def _cache_key(self, asset_type: AssetType, fields: Dict[str, Any]) -> str:
        # Project-visual-bible 契约：cache key 必须包含 visual_bible 字段，
        # 否则 bible 重建后旧 cache 仍会命中，导致共享块过期。
        visual_bible_version = fields.get("visual_bible_version") or ""
        visual_bible_fingerprint = fields.get("visual_bible_fingerprint") or ""
        asset_prompt_contract_version = fields.get("asset_prompt_contract_version") or ""
        payload = {
            "asset_type": asset_type,
            "fields": _stable(fields),
            "visual_bible_version": visual_bible_version,
            "visual_bible_fingerprint": visual_bible_fingerprint,
            "asset_prompt_contract_version": asset_prompt_contract_version,
            "portrait_final_prompt_contract_version": (
                PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
                if asset_type == "portrait"
                else ""
            ),
        }
        return _short_hash(payload)

    def _serialize_provider_prompt(
        self,
        asset_type: AssetType,
        output: RewrittenPrompt,
        fields: Dict[str, Any],
    ) -> str:
        """Serialize exactly what the asset builder sends to its provider."""
        locked_style = str(fields.get("visual_style_prompt") or "").strip()
        if asset_type == "portrait":
            return output.to_cogview_prompt(use_final_prompt=True)
        if asset_type == "background":
            return output.to_background_cogview_prompt(
                locked_style=locked_style,
            )
        return output.to_cogview_prompt(
            locked_style=locked_style,
            use_final_prompt=False,
        )

    async def rewrite(
        self,
        asset_type: AssetType,
        fields: Dict[str, Any],
        force_refresh: bool = False,
    ) -> Optional[RewrittenPrompt]:
        """
        把结构化字段重写为 RewrittenPrompt。失败返回 None。

        Args:
            asset_type: portrait/background/keyframe
            fields: 依赖 asset_type，参考 A01/B01/C01 研究文档
            force_refresh: 跳过缓存
        """
        if not PROMPT_REWRITER_ENABLED:
            return None
        if not api_key_available(
            REWRITER_API_KEY,
            pool_env=("REWRITER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info("rewriter disabled: API key not configured, fallback")
            return None

        cache_key = self._cache_key(asset_type, fields)
        if not force_refresh:
            async with self._cache_lock:
                entry = self._cache.get(cache_key)
                if entry is not None:
                    cached_prompt = self._serialize_provider_prompt(
                        asset_type,
                        entry.output,
                        fields,
                    )
                    await self._record(RewriteStats(
                        asset_type=asset_type, success=True, cached=True,
                        fallback=False, duration_s=0.0,
                        input_chars=len(json.dumps(fields, ensure_ascii=False)),
                        output_chars=len(cached_prompt),
                        approx_tokens=_approx_token_count(cached_prompt),
                    ))
                    return entry.output

        start = time.time()
        try:
            async with self._semaphore:
                output = await asyncio.wait_for(
                    self._call_llm(asset_type, fields),
                    timeout=REWRITER_TIMEOUT,
                )
        except asyncio.TimeoutError:
            await self._record(RewriteStats(
                asset_type=asset_type, success=False, cached=False,
                fallback=True, duration_s=time.time() - start,
                input_chars=len(json.dumps(fields, ensure_ascii=False)),
                output_chars=0, approx_tokens=0, error="timeout",
            ))
            return None
        except Exception as e:
            logger.warning("rewrite failed (%s): %s", asset_type, e)
            await self._record(RewriteStats(
                asset_type=asset_type, success=False, cached=False,
                fallback=True, duration_s=time.time() - start,
                input_chars=len(json.dumps(fields, ensure_ascii=False)),
                output_chars=0, approx_tokens=0, error=str(e),
            ))
            return None

        if output is None:
            return None

        output, errors = self._validate_output(asset_type, output, fields)

        # Portrait prompts are never repaired by concatenating backend text.
        # Give the LLM a bounded number of feedback-guided rewrites, then fail
        # closed if framing/transparency/single-character/age rules still fail.
        repair_attempt = 0
        while (
            errors
            and asset_type == "portrait"
            and repair_attempt < PORTRAIT_REWRITER_MAX_REPAIRS
        ):
            repair_attempt += 1
            repair_fields = dict(fields)
            repair_fields["previous_invalid_output"] = output.to_cogview_prompt()
            repair_fields["validation_feedback"] = errors
            repair_fields["repair_attempt"] = repair_attempt
            try:
                async with self._semaphore:
                    repaired = await asyncio.wait_for(
                        self._call_llm(asset_type, repair_fields),
                        timeout=REWRITER_TIMEOUT,
                    )
            except Exception as exc:
                logger.warning("portrait rewrite repair failed: %s", exc)
                repaired = None
            if repaired is None:
                break
            output, errors = self._validate_output(asset_type, repaired, fields)

        # B06 — keyframe 身份漂移守卫。LLM 改写后必须保留 CHARACTER IDENTITY LOCK，
        # 否则脸/发/性别/年龄/身份都会被自由发挥掉。检测到漂移时回退到
        # deterministic fallback：把 LOCK 块原文拼在 LLM 输出前面。
        if (
            not errors
            and asset_type == "keyframe"
            and self._has_identity_drift(output, fields)
        ):
            logger.info("[rewriter] keyframe identity drift detected, applying lock fallback")
            output = self._apply_identity_lock_fallback(output, fields)
            # The trusted lock changes the provider payload after the first
            # validation. Revalidate so an oversized backend lock fails closed.
            output, errors = self._validate_output(asset_type, output, fields)

        if errors:
            logger.warning("guardrail failed (%s): %s", asset_type, errors)
            await self._record(RewriteStats(
                asset_type=asset_type, success=False, cached=False,
                fallback=True, duration_s=time.time() - start,
                input_chars=len(json.dumps(fields, ensure_ascii=False)),
                output_chars=0, approx_tokens=0, error=";".join(errors),
            ))
            return None

        async with self._cache_lock:
            self._cache[cache_key] = _CacheEntry(output=output, ts=time.time())

        serialized_output = self._serialize_provider_prompt(
            asset_type,
            output,
            fields,
        )
        await self._record(RewriteStats(
            asset_type=asset_type, success=True, cached=False,
            fallback=False, duration_s=time.time() - start,
            input_chars=len(json.dumps(fields, ensure_ascii=False)),
            output_chars=len(serialized_output),
            approx_tokens=_approx_token_count(serialized_output),
        ))
        return output

    def _validate_output(
        self,
        asset_type: AssetType,
        output: RewrittenPrompt,
        fields: Dict[str, Any],
    ) -> Tuple[RewrittenPrompt, List[str]]:
        """Validate one LLM response without modifying portrait prose."""
        def _guardrail(current: RewrittenPrompt) -> Tuple[bool, List[str]]:
            return current.guardrail_check(
                asset_type,
                provider_prompt=self._serialize_provider_prompt(
                    asset_type,
                    current,
                    fields,
                ),
            )

        ok, errors = _guardrail(output)
        if not ok and any("negative_minimal too long" in e for e in errors):
            output = self._trim_negative_minimal(output, limit=5)
            ok, errors = _guardrail(output)

        # Keyframes keep their legacy deterministic size repair. Portrait
        # final_prompt is authoritative LLM prose and must never be truncated
        # or augmented by backend string operations.
        if not ok and asset_type == "keyframe" and any(
            "prompt too long" in e for e in errors
        ):
            output = self._truncate_keyframe_to_budget(output, fields)
            ok, errors = _guardrail(output)

        if asset_type == "portrait":
            if not output.final_prompt.strip():
                errors = list(errors) + ["portrait LLM response is missing final_prompt"]
            else:
                errors = list(errors) + validate_portrait_final_prompt(
                    output.final_prompt,
                    str(fields.get("shot") or "full_body"),
                    str(fields.get("subject_opening") or ""),
                )
                errors = list(errors) + age_prompt_validation_errors(
                    output.final_prompt,
                    fields.get("age_contract"),
                )
        return output, list(dict.fromkeys(errors))

    async def rewrite_many(
        self,
        asset_type: AssetType,
        batch: List[Dict[str, Any]],
    ) -> List[Optional[RewrittenPrompt]]:
        """
        D07 — 批量 rewrite。当前实现 = 并发 rewrite。
        未来可以改成单次 LLM 调用让模型一次输出多结果。
        """
        if not batch:
            return []
        tasks = [self.rewrite(asset_type, fields) for fields in batch]
        return await asyncio.gather(*tasks, return_exceptions=False)

    async def _call_llm(
        self,
        asset_type: AssetType,
        fields: Dict[str, Any],
    ) -> Optional[RewrittenPrompt]:
        """
        D02 — 调用 LLM，使用 asset_type 专属 system prompt + few-shot。
        返回 RewrittenPrompt 或 None（解析失败时）。
        """
        system_prompt = self._build_system_prompt(asset_type)
        user_prompt = self._build_user_prompt(asset_type, fields)
        client = self._get_client()
        model = resolve_request_model(REWRITER_MODEL)

        # DeepSeek V4 enables thinking by default and counts reasoning tokens
        # against max_tokens; a single rewrite can take >20s and trip
        # REWRITER_TIMEOUT. structured_json_request_options disables thinking
        # for api.deepseek.com and is a no-op for other endpoints.
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
            **structured_json_request_options(model),
        )
        raw = response.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("rewriter returned non-JSON: %s", raw[:200])
            return None
        try:
            return RewrittenPrompt(**data)
        except ValidationError as e:
            logger.warning("rewriter output schema mismatch: %s", e)
            return None

    def _build_system_prompt(self, asset_type: AssetType) -> str:
        """
        D02 — asset_type 专属 system prompt，含 few-shot。
        从 profiles["rewriter"][asset_type] 读取模板，缺失时用默认。
        """
        if asset_type == "background":
            return _BACKGROUND_SYSTEM_PROMPT_ZH
        rewriter_cfg = self._profiles.get("rewriter", {})
        cfg = rewriter_cfg.get(asset_type, {})
        template = cfg.get("system_prompt")
        if template:
            return template
        if asset_type == "portrait":
            return _PORTRAIT_SYSTEM_PROMPT
        return _DEFAULT_SYSTEM_PROMPT.format(asset_type=asset_type)

    def _build_user_prompt(self, asset_type: AssetType, fields: Dict[str, Any]) -> str:
        budget = _TARGET_TOKEN_BUDGET.get(asset_type, 120)
        profile = fields.get("visual_style_profile") if isinstance(fields.get("visual_style_profile"), dict) else {}
        style_prompt = (
            fields.get("visual_style_prompt")
            or profile.get("portrait_prompt_en")
            or profile.get("background_prompt_zh")
            or profile.get("keyframe_prompt_en")
            or ""
        )
        style_fingerprint = fields.get("style_fingerprint") or profile.get("fingerprint") or ""
        project_genre = fields.get("project_genre") or profile.get("project_genre") or fields.get("genre") or ""
        visual_bible_version = fields.get("visual_bible_version") or ""
        style_lock_block = ""
        if style_prompt or style_fingerprint or project_genre or visual_bible_version:
            style_output_rule = (
                "- Integrate the locked medium, linework, shading, texture, palette, and forbidden styles "
                "directly into the single `final_prompt`. Do not output metadata labels or a separate style block. "
                "The backend will send your `final_prompt` verbatim and will NOT append style text.\n"
                if asset_type == "portrait"
                else "- DO NOT emit any style/medium vocabulary in the `style` field. Leave `style` empty. "
                "The backend will inject the shared style block deterministically.\n"
            )
            style_lock_block = (
                "\nPROJECT VISUAL BIBLE HARD CONSTRAINT (shared-style-prompt-v2):\n"
                f"- visual_bible_version: {visual_bible_version or '(none)'}\n"
                f"- style_fingerprint: {style_fingerprint or '(none)'}\n"
                f"- project_genre (setting only, NOT medium): {project_genre or '(unspecified)'}\n"
                f"- project_visual_bible (locked): {style_prompt or '(unspecified)'}\n"
                "- The medium / linework / shading / texture / palette / forbidden list above are LOCKED "
                "for the entire project. Do NOT switch to photorealism, oil painting, watercolor, sketch, "
                "or any other art family. Genre (modern / sci-fi / historical / ...) influences ONLY setting, "
                "costume, and technology vocabulary — it MUST NOT change the rendering medium.\n"
                "- Emotion, lighting, and composition may change. Rendering style, line quality, color family, "
                "and character identity must stay consistent.\n"
                f"{style_output_rule}"
            )
        if asset_type == "background":
            return (
                "请把下面结构化字段改写成 CogView-4 友好的中文背景图提示词 JSON。"
                f"目标长度约 {budget} tokens。只输出 JSON，不要解释。\n\n"
                "硬规则：背景图必须完全无人；不要人物、人体、剪影、路人、守卫、士兵、侍从、人群、"
                "远景人物、背影、人形轮廓、命名角色、肖像、英雄式人物镜头、全身或半身人物强调、"
                "面部特写、服装特写。只保留地点、建筑、景观、道具、光线、天气、材质和空间氛围。\n\n"
                f"{style_lock_block}\n"
                f"FIELDS:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
                "输出 schema: {\"subject\": str, \"details\": [str], \"lighting\": str, "
                "\"composition\": str, \"style\": str, \"negative_minimal\": [str]}"
            )
        extra_rules = ""
        if asset_type == "portrait":
            requested_shot = str(fields.get("shot") or "full_body").strip().lower().replace("-", "_")
            shot_rule = (
                "full-body composition; show the entire character from head to feet, with head, hands, "
                "clothing silhouette, accessories, and feet fully inside the frame"
                if requested_shot == "full_body"
                else "half-body waist-up composition with head, shoulders, and visible hands inside the frame"
            )
            extra_rules = (
                "\nPORTRAIT FINAL PROMPT CONTRACT:\n"
                f"- requested_shot: {requested_shot}\n"
                f"- Keep final_prompt concise: target {budget}-{int(budget * 1.5)} approximate tokens; "
                f"hard maximum {budget * 8} ASCII characters. Merge repeated constraints instead of restating them.\n"
                "- Include the exact literal phrase `exactly one isolated character`; do not paraphrase or omit it.\n"
                f"- Use exactly this framing and no other crop instruction: {shot_rule}.\n"
                "- Write one coherent English `final_prompt` containing every useful character identity, actual "
                "clothing, expression, pose, body-mechanics, lighting, composition, project visual style, and "
                "negative constraint. Resolve duplicate wording instead of listing competing instructions.\n"
                "- State that this is exactly one isolated character on a fully transparent background with no "
                "scenery, floor, cast shadow, furniture, decorative frame, text, or watermark.\n"
                "- Never mention both full-body and half-body/waist-up/knees-up framing.\n"
                "- Do not refer to FIELDS, a visual bible, a contract, a fingerprint, or the rewrite process.\n"
                "- Output only JSON with one key: {\"final_prompt\": \"...\"}.\n"
            )
            subject_opening = str(fields.get("subject_opening") or "").strip()
            if subject_opening:
                extra_rules += (
                    "\nPORTRAIT SUBJECT OPENING HARD CONSTRAINT:\n"
                    f"- subject_opening: {subject_opening}\n"
                    "- The first words of final_prompt MUST be exactly the supplied subject_opening. "
                    "Put `exactly one isolated character` immediately after this opening, never before it.\n"
                )
            gender = (fields.get("gender") or "").strip() if isinstance(fields.get("gender"), str) else fields.get("gender")
            gender_prompt = (fields.get("gender_prompt") or "").strip() if isinstance(fields.get("gender_prompt"), str) else fields.get("gender_prompt")
            if gender or gender_prompt:
                extra_rules += (
                    "\nPORTRAIT GENDER HARD CONSTRAINT:\n"
                    f"- gender: {gender or '(unspecified)'}\n"
                    f"- gender_prompt: {gender_prompt or '(none)'}\n"
                    "- Preserve this identity exactly. Put the gender anchor in subject/details. "
                    "Do not make male characters feminine or female characters masculine.\n"
                )
            portrait_identity = fields.get("portrait_identity")
            if isinstance(portrait_identity, dict) and portrait_identity.get("kind") == "canonical":
                extra_rules += (
                    "\nCANONICAL SOURCE IDENTITY HARD CONSTRAINT:\n"
                    f"- portrait_identity: {json.dumps(portrait_identity, ensure_ascii=False)}\n"
                    "- This confirmed source identity is authoritative for face, hair, eyes, official-era outfit, "
                    "fixed features, and side-specific traits. No generic gallery identity is supplied or implied.\n"
                    "- Do not copy current emotion, pose, injury, bandages, or temporary clothing into the fixed "
                    "identity; apply them only from the corresponding variation fields.\n"
                )
            elif isinstance(portrait_identity, dict) and portrait_identity.get("kind") == "generic":
                extra_rules += (
                    "\nGENERIC PORTRAIT IDENTITY HARD CONSTRAINT:\n"
                    f"- portrait_identity: {json.dumps(portrait_identity, ensure_ascii=False)}\n"
                    "- This gallery-compatible visual profile is the only available appearance identity. "
                    "Do not infer a canonical franchise character or add source-specific features.\n"
                )
            age_contract = fields.get("age_contract")
            if isinstance(age_contract, dict) and age_contract.get("age_group"):
                extra_rules += (
                    "\nPORTRAIT APPARENT AGE HARD CONSTRAINT:\n"
                    f"- age_group: {age_contract.get('age_group')}\n"
                    f"- age_range_hint: {age_contract.get('age_range_hint') or '(none)'}\n"
                    f"- required_visual_traits: {json.dumps(age_contract.get('age_visual_traits') or [], ensure_ascii=False)}\n"
                    f"- forbidden_age_traits: {json.dumps(age_contract.get('forbidden_age_traits') or [], ensure_ascii=False)}\n"
                    f"- required_age_anchor: {age_contract.get('prompt_anchor') or '(none)'}\n"
                    "- Make the age stage explicitly visible in face, jaw, and body proportions and state it "
                    "naturally in final_prompt. Preserve identity across age stages, but do not copy the face age "
                    "or body age from another stage.\n"
                )
            validation_feedback = fields.get("validation_feedback")
            if validation_feedback:
                extra_rules += (
                    "\nPREVIOUS OUTPUT REJECTED. Correct every issue below; do not copy the conflicting wording:\n- "
                    + "\n- ".join(str(item) for item in validation_feedback)
                    + "\n- Compression is mandatory when length failed: keep only essential identity, visible age, "
                    "outfit, pose, locked style, framing, transparency, and negative constraints.\n"
                )
        elif asset_type == "keyframe":
            # B05 — 关键帧 rewriter 必须看到完整身份字段，但只能改场景/动作/构图/光线/位置，
            # 不得改脸/发/性别/年龄/身份。把 CHARACTER IDENTITY LOCK 块原文塞进 user prompt，
            # 让 LLM 看到锁，再让 _has_identity_drift 后置校验兜底。
            identity_lock = fields.get("character_identity_lock") or ""
            if identity_lock:
                extra_rules = (
                    "\nKEYFRAME IDENTITY HARD CONSTRAINT (do not paraphrase, do not drop):\n"
                    f"{identity_lock}\n"
                )
            # 把每个角色的 visual_fingerprint / canonical_outfit / signature_features 显式列出，
            # 让 LLM 在改写动作/构图时知道哪些字段是 lock，哪些是 free。
            lock_summary_lines = []
            for c in (fields.get("characters") or [])[:3]:
                if not isinstance(c, dict):
                    continue
                name = c.get("name") or c.get("character_id") or "?"
                fp = c.get("visual_fingerprint") or "(none)"
                canonical_fp = c.get("canonical_identity_fingerprint") or "(none)"
                outfit = c.get("canonical_outfit") or {}
                sig = c.get("signature_features") or []
                asymmetry = c.get("asymmetric_traits") or []
                pos = c.get("expected_position") or "center"
                age_group = c.get("age_group") or "(unspecified)"
                variant_id = c.get("identity_variant_id") or "(none)"
                lock_summary_lines.append(
                    f"- {name} (pos={pos}, age={age_group}, variant={variant_id}, "
                    f"vf={fp}, canonical_fp={canonical_fp}): "
                    f"outfit={json.dumps(outfit, ensure_ascii=False)}, "
                    f"signature={list(sig)}, asymmetry={list(asymmetry)}"
                )
            if lock_summary_lines:
                extra_rules += (
                    "\nLOCKED PER-CHARACTER IDENTITY (rewriter MUST keep these verbatim):\n"
                    + "\n".join(lock_summary_lines) + "\n"
                )
        if asset_type == "portrait":
            return (
                "Rewrite all structured inputs into the one authoritative English prompt that will be sent "
                "verbatim to the image model. Output JSON only.\n"
                f"{style_lock_block}{extra_rules}\n"
                f"FIELDS:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
                "Output schema: {\"final_prompt\": str}"
            )
        return (
            f"Rewrite the following structured fields into a single CogView-4 friendly "
            f"English image prompt for a {asset_type} asset. "
            f"Target length ~{budget} tokens. Output JSON only.\n"
            f"{style_lock_block}{extra_rules}\n"
            f"FIELDS:\n{json.dumps(fields, ensure_ascii=False, indent=2)}\n\n"
            f"Output schema: {{\"subject\": str, \"details\": [str], \"lighting\": str, "
            f"\"composition\": str, \"style\": str, \"negative_minimal\": [str]}}"
        )

    async def _record(self, stats: RewriteStats) -> None:
        async with self._stats_lock:
            self._stats.append(stats)
            if len(self._stats) > 1000:
                self._stats = self._stats[-500:]

    # B06 — 关键帧身份漂移守卫 ============================================
    # 检测 LLM 输出是否丢失了身份字段；丢失则把 LOCK 块原文前置到 subject，
    # 保证最终 prompt 始终带身份锁。这只做"加 lock"，不会修改 LLM 已经生成的
    # 场景/动作/构图部分。
    _IDENTITY_DRIFT_KEYWORDS = (
        "redesign", "new character", "different face", "different hairstyle",
        "swapped", "merge ", "blended", "wrong gender",
    )

    def _has_identity_drift(self, output: RewrittenPrompt, fields: Dict[str, Any]) -> bool:
        """检测 LLM 改写是否漂移出身份锁。

        判定（任一即视为漂移）：
        1. LLM 输出包含明确"重新设计角色"的禁用词；
        2. 输入里有 LOCK 块 / 角色 binding，但 LLM 输出 subject + details
           里没有任何一个角色名出现；
        3. 输入里有 character_identity_lock，但 LLM 输出根本没出现
           "identity" / "preserve" / "same face" 之类的承诺词。
        """
        if not output:
            return False
        # ``final_prompt`` is authoritative only for portraits.  Keyframe
        # callers send the structured fields, so drift validation must inspect
        # that exact representation as well.
        text = self._serialize_provider_prompt(
            "keyframe",
            output,
            fields,
        ).lower()
        if not text:
            return True  # 空 prompt 必漂移

        for kw in self._IDENTITY_DRIFT_KEYWORDS:
            if kw in text:
                return True

        chars = fields.get("characters") or []
        names = [
            (c.get("name") or "").strip().lower()
            for c in chars
            if isinstance(c, dict) and (c.get("name") or "").strip()
        ]
        if names and not any(n in text for n in names):
            return True

        lock = fields.get("character_identity_lock") or ""
        if lock.strip():
            commitment_tokens = ("identity", "preserve", "same face", "same hairstyle", "lock")
            if not any(tok in text for tok in commitment_tokens):
                return True

        return False

    def _apply_identity_lock_fallback(
        self,
        output: RewrittenPrompt,
        fields: Dict[str, Any],
    ) -> RewrittenPrompt:
        """漂移兜底：把 CHARACTER IDENTITY LOCK 块原文前置到 subject。

        保留 LLM 已经写好的场景/动作/构图/光线，只在最前面贴一段确定性
        身份锁，确保最终 cogview prompt 始终带身份约束。
        """
        lock = (fields.get("character_identity_lock") or "").strip()
        if not lock:
            return output
        new_output = output.model_copy(deep=True)
        new_output._trusted_identity_lock = lock
        return new_output

    def _truncate_keyframe_to_budget(
        self,
        output: RewrittenPrompt,
        fields: Dict[str, Any],
    ) -> RewrittenPrompt:
        """keyframe prompt 超长时按优先级压缩到 ~280 tokens（hard budget 150*2=300）。

        保留顺序：subject(场景/事件/角色) → composition → lighting → style →
        details；details 按「含角色名/动作动词」优先级保留前 N 条，再按字符预算
        截断 subject。其余字段（negative_minimal、style）只在仍超长时砍掉。
        """
        budget = _TARGET_TOKEN_BUDGET.get("keyframe", 150)
        target_tokens = budget * 2 - 16  # 留 16 token 余量，目标 ~284
        max_chars = target_tokens * 4

        truncated = output.model_copy(deep=True)

        def _total_chars() -> int:
            return len(
                self._serialize_provider_prompt(
                    "keyframe",
                    truncated,
                    fields,
                )
            )

        # 1. 砍掉一半 details（按角色名/动词优先保留）
        if _total_chars() > max_chars and truncated.details:
            kept: List[str] = []
            for d in truncated.details:
                if not d:
                    continue
                # 含角色名或动词的细节优先
                priority = any(k in d.lower() for k in ("fight", "embrace", "fall", "run", "cry", "look"))
                if priority or len(kept) < 2:
                    kept.append(d)
                if len(kept) >= 3:
                    break
            truncated.details = kept

        # 2. 砍 lighting
        if _total_chars() > max_chars:
            truncated.lighting = ""

        # 3. 砍 style
        if _total_chars() > max_chars:
            truncated.style = ""

        # 4. 硬截断 subject
        if _total_chars() > max_chars and truncated.subject:
            overflow = _total_chars() - max_chars
            truncated.subject = truncated.subject[:max(20, len(truncated.subject) - overflow)].rstrip()

        return truncated

    def _trim_negative_minimal(self, output: RewrittenPrompt, limit: int = 5) -> RewrittenPrompt:
        """LLM 偶尔返回 6-7 个 negative_minimal；硬剪到 ``limit`` 条。

        保持顺序：保前 ``limit`` 项即可，因为前几项通常是 LLM 优先级最高
        的禁令（text/watermark/background scene/...）。
        """
        if len(output.negative_minimal) <= limit:
            return output
        trimmed = output.model_copy(deep=True)
        trimmed.negative_minimal = output.negative_minimal[:limit]
        return trimmed

    async def get_stats_snapshot(self) -> Dict[str, Any]:
        """D18 — 返回监控指标快照。"""
        async with self._stats_lock:
            stats = list(self._stats)
        success = [s for s in stats if s.success]
        failed = [s for s in stats if not s.success]
        cached = [s for s in stats if s.cached]
        avg_dur = sum(s.duration_s for s in stats) / len(stats) if stats else 0
        avg_tokens = sum(s.approx_tokens for s in success) / len(success) if success else 0
        return {
            "total": len(stats),
            "success": len(success),
            "failed": len(failed),
            "cached": len(cached),
            "success_rate": round(len(success) / max(1, len(stats)), 3),
            "cache_hit_rate": round(len(cached) / max(1, len(stats)), 3),
            "avg_duration_s": round(avg_dur, 3),
            "avg_output_tokens": round(avg_tokens, 1),
            "enabled": PROMPT_REWRITER_ENABLED,
            "model": REWRITER_MODEL,
        }


_PORTRAIT_SYSTEM_PROMPT = (
    "You are the final prompt author for a visual-novel portrait. Output JSON only: "
    "{\"final_prompt\": str}. The final_prompt is sent verbatim to the image model. "
    "Integrate every supplied identity, gender, apparent-age, clothing, emotion, pose, lighting, requested-shot, "
    "transparent-background, and locked-style constraint into one coherent English prompt. "
    "When subject_opening is supplied, final_prompt MUST begin with it exactly. "
    "Use exactly the requested full_body or half_body framing, never both. The final_prompt MUST contain "
    "the exact literal phrase 'exactly one isolated character' and must place that character on a fully "
    "transparent background. Include no metadata labels or rewrite commentary."
)


_DEFAULT_SYSTEM_PROMPT = (
    "You are a prompt rewriter for CogView-4, a Chinese-English bilingual text-to-image model.\n"
    "Your job: take structured narrative fields (which may be in Chinese) and rewrite them into "
    "a single information-dense, visually-parseable, ENGLISH-ONLY image prompt for a {asset_type} asset.\n\n"
    "Rules:\n"
    "1. Output language MUST be English. Translate any Chinese input to English visual descriptions.\n"
    "2. Output MUST be a single JSON object with keys: subject, details, lighting, composition, style, negative_minimal.\n"
    "3. subject: 60-150 characters, the main subject (character / environment / event) with key visual traits.\n"
    "4. details: 3-6 visual detail strings, each 5-15 words. Include position, props, textures.\n"
    "5. lighting: 5-15 words on lighting and atmosphere.\n"
    "6. composition: 5-15 words on shot type and angle (e.g. 'half body shot, character centered, three-quarter angle').\n"
    "7. style: ALWAYS EMPTY. Do NOT emit any art-style, medium, or era vocabulary. The backend injects the "
    "project-locked visual bible (medium / linework / shading / texture / palette / forbidden) deterministically. "
    "Any style you emit will be discarded.\n"
    "8. negative_minimal: 2-5 short words of what to avoid (e.g. ['text', 'watermark']). Never use long negative strings.\n"
    "9. Do NOT mention the source language. Do NOT include 'as described', 'according to'. Pure visual description only.\n"
    "10. For background assets, NEVER mention clothing, hanfu, or character apparel.\n"
    "11. For keyframe assets, explicitly describe character positions (left/center/right) and interactions.\n"
    "12. Preserve any project visual style constraint exactly — but only by NOT contradicting it; do not restate it in style.\n"
)

_BACKGROUND_SYSTEM_PROMPT_ZH = (
    "你是 CogView-4 的视觉小说背景图提示词改写器，只处理 BACKGROUND 资产。"
    "输出必须是中文 JSON，键为 subject、details、lighting、composition、style、negative_minimal。\n\n"
    "背景图硬规则：所有背景必须完全空无一人。不得出现主角、命名角色、匿名人物、路人、守卫、士兵、侍从、"
    "商贩、人群、观众、旁观者、旅人、远景小人、剪影、背影、人形轮廓或任何人物照片。"
    "即使是集市、街道、城门、宫门、军营、战场、码头、酒馆、宴会厅、节日广场，也要画成空场建立镜头。\n\n"
    "保留内容：地点、建筑、景观、道具、材质、光线、天气、色彩、空间氛围。"
    "移除内容：角色姓名、角色动作、表情、服装、对白、人物关系、人物中心构图。\n\n"
    "重要 — 画风字段（style）：必须留空。项目的整体画风（动画 cel / 水墨设色 / 写实概念图等）"
    "由后端 ProjectVisualBible 锁定并确定性地注入到最终 prompt；LLM 不得自行选择或修改媒介、"
    "不得根据'现代/科幻/古风/奇幻'题材切换写实摄影、油画或动画媒介。即使你在 style 字段里写入了内容，"
    "也会被后端丢弃。你只需关注场景内容、构图、光照与负面约束。\n\n"
    "输出要求：subject 用 60-150 个中文字符描述纯空间；details 给 3-6 条环境细节；lighting 描述光线；"
    "composition 描述 16:9 环境建立镜头；style 必须为空串；negative_minimal 给 2-5 个短词，必须包含"
    "命名角色、人物主体、肖像、面部特写、全身人物、半身人物、服装特写中的至少两个。"
)


def _stable(value: Any) -> Any:
    """Make value JSON-stable (sorted keys) for hashing."""
    if isinstance(value, dict):
        return {k: _stable(value[k]) for k in sorted(value.keys())}
    if isinstance(value, (list, tuple)):
        return [_stable(v) for v in value]
    return value


# 全局实例
prompt_rewriter_service = PromptRewriterService()
