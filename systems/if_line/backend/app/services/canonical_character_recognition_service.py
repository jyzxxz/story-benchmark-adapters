"""LLM-driven canonical-character recognition.

Purpose
-------
For fan-fiction projects (海贼王 / 火影 / 原神 / ...) the user expects a
portrait of 艾斯 to LOOK LIKE 艾斯 in the original anime, not a generic
"hot-blooded shounen boy with black hair". Every franchise — including
Genshin — is now resolved by this LLM service; the legacy hardcoded
``_GENSHIN`` block has been removed.

This service answers:

    Given source_work = "海贼王" and character_name = "艾斯",
    is this a canonical character from a well-known existing anime/manga/
    game/film franchise? If YES, return an English appearance prompt that
    faithfully locks the portrait to the original character's official
    design (silhouette, hair, outfit, signature features).

Design contract
---------------
* Every source work is treated identically — there is no hardcoded
  franchise bypass. (See
  :mod:`source_visual_profile_service.character_appearance`.)
* Output is a :class:`CanonicalCharacterAnchor` with a ready-to-use English
  ``prompt_en`` plus source-only identity fields.  It never extends or
  replaces the generic portrait-library ``visual_profile`` taxonomy.
* Determinism: temperature pinned to 0.2; same input sha256 reuses a
  process-level cache entry.
* Failure isolation: LLM disabled / API key missing / timeout /
  validation error → returns ``None``. The caller falls back to
  ``fallback_appearance``. This service NEVER raises.
* BYOK-aware: routes through ``text_llm_config`` + ``PooledAsyncOpenAI``
  so per-request X-LLM-Model / X-LLM-Base-Url headers propagate.
* English-only enforcement: the ``prompt_en`` field must not contain
  CJK characters — CogView-4 expects English style vocabulary.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_MODEL,
    resolve_request_model,
    text_llm_api_key,
    text_llm_base_url,
    text_llm_model,
)

logger = logging.getLogger("canonical_character_recognition")

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

CANONICAL_CHARACTER_RECOGNITION_ENABLED = os.getenv(
    "CANONICAL_CHARACTER_RECOGNITION_ENABLED", "true"
).lower() == "true"
CANONICAL_CHARACTER_RECOGNITION_MODEL = text_llm_model(
    "CANONICAL_CHARACTER_RECOGNITION_MODEL", default=DEFAULT_TEXT_LLM_MODEL
)
CANONICAL_CHARACTER_RECOGNITION_API_KEY = text_llm_api_key(
    "CANONICAL_CHARACTER_RECOGNITION_API_KEY"
)
CANONICAL_CHARACTER_RECOGNITION_BASE_URL = text_llm_base_url(
    "CANONICAL_CHARACTER_RECOGNITION_BASE_URL"
)
CANONICAL_CHARACTER_RECOGNITION_TIMEOUT = float(
    os.getenv("CANONICAL_CHARACTER_RECOGNITION_TIMEOUT", "40.0")
)
CANONICAL_CHARACTER_RECOGNITION_MAX_CONCURRENCY = int(
    os.getenv("CANONICAL_CHARACTER_RECOGNITION_MAX_CONCURRENCY", "2")
)
CANONICAL_CHARACTER_RECOGNITION_TEMPERATURE = float(
    os.getenv("CANONICAL_CHARACTER_RECOGNITION_TEMPERATURE", "0.2")
)

# Bound input length — character name + worldview + style rules give the
# LLM enough context to decide canonicity without sending the full novel.
_MAX_INPUT_CHARS = 2000

_CJK_RE = re.compile(r"[一-鿿]")
_PROMPT_EN_MIN_CHARS = 60
_PROMPT_EN_MAX_CHARS = 600


# ----------------------------------------------------------------------
# Output schema
# ----------------------------------------------------------------------


class CanonicalCharacterDecisionModel(BaseModel):
    """Pydantic schema for the LLM's JSON output."""

    is_canonical: bool = Field(
        description=(
            "true if the character is a recognizable character from a "
            "well-known existing anime / manga / game / film / novel "
            "franchise. false if the character is an original character "
            "created for this story, or if you cannot confidently identify "
            "the franchise."
        )
    )
    franchise_name: str = Field(
        description=(
            "English franchise name when is_canonical=true. Example: "
            "'One Piece', 'Naruto', 'Genshin Impact'. Empty string when "
            "is_canonical=false."
        )
    )
    canonical_name: str = Field(
        description=(
            "The character's official name in the franchise when "
            "is_canonical=true. Empty string when is_canonical=false."
        )
    )
    prompt_en: str = Field(
        description=(
            "When is_canonical=true: a DETAILED English appearance prompt "
            "60-600 chars that faithfully locks the portrait to the "
            "character's official design. Must include: body type, "
            "hairstyle/color, eye color, signature outfit, iconic "
            "accessories, distinguishing features (scars / tattoos / "
            "weapons). Prefix with 'canonical <Name> from <Franchise>, '. "
            "End with '; preserve the recognizable official character "
            "design and silhouette'. When is_canonical=false: empty string."
        )
    )
    rationale: str = Field(
        description=(
            "Short English explanation, 20-150 chars, of WHY this is or "
            "is not a canonical character. Used for audit logging only."
        )
    )
    look_variant: str = Field(
        default="",
        description=(
            "Official visual era/version selected from the supplied story context, "
            "for example 'young Obito during the Kannabi Bridge mission'."
        ),
    )
    fixed_features: List[str] = Field(
        default_factory=list,
        description="Stable body, face, hair, eye and iconic accessory traits.",
    )
    canonical_outfit: str = Field(
        default="",
        description="The stable official outfit for the selected visual era.",
    )
    asymmetric_traits: List[str] = Field(
        default_factory=list,
        description="Side-specific scars, marks, missing limbs or accessories.",
    )


@dataclass(frozen=True)
class CanonicalCharacterAnchor:
    """Caller-facing dataclass — the locked identity anchor for one character."""

    is_canonical: bool
    franchise_name: str
    canonical_name: str
    prompt_en: str
    rationale: str = ""
    look_variant: str = ""
    fixed_features: Tuple[str, ...] = ()
    canonical_outfit: str = ""
    asymmetric_traits: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------


class CanonicalCharacterRecognitionService:
    """Async LLM canonical-character recognizer with process-level cache."""

    def __init__(self) -> None:
        self._cache: Dict[str, Optional[CanonicalCharacterAnchor]] = {}
        self._cache_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(
            CANONICAL_CHARACTER_RECOGNITION_MAX_CONCURRENCY
        )
        self._client: Optional[Any] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def recognize(
        self,
        *,
        character_name: str,
        source_work: str,
        worldview: str = "",
        style_rules: str = "",
        fallback_appearance: str = "",
    ) -> Optional[CanonicalCharacterAnchor]:
        """Return a :class:`CanonicalCharacterAnchor` or ``None`` on failure.

        ``None`` instructs the caller to fall back to
        ``fallback_appearance``. This method never raises.
        """
        name = (character_name or "").strip()
        if not name:
            return None

        if not CANONICAL_CHARACTER_RECOGNITION_ENABLED:
            return None
        if not api_key_available(
            CANONICAL_CHARACTER_RECOGNITION_API_KEY,
            pool_env=(
                "CANONICAL_CHARACTER_RECOGNITION_API_KEYS",
                "OPENAI_API_KEYS",
            ),
        ):
            logger.info(
                "[canonical-char] disabled: API key not configured, "
                "fallback to user appearance"
            )
            return None

        payload = self._build_payload(
            character_name=name,
            source_work=source_work,
            worldview=worldview,
            style_rules=style_rules,
            fallback_appearance=fallback_appearance,
        )
        cache_key = self._cache_key(payload)
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            # negative cache: we store None too once we have a result, but
            # use a sentinel to distinguish "never queried" from "queried + None"

        start = time.time()
        try:
            async with self._semaphore:
                raw = await asyncio.wait_for(
                    self._call_llm(payload), timeout=CANONICAL_CHARACTER_RECOGNITION_TIMEOUT
                )
        except asyncio.TimeoutError:
            logger.warning(
                "[canonical-char] timeout after %.1fs",
                CANONICAL_CHARACTER_RECOGNITION_TIMEOUT,
            )
            return None
        except Exception as e:
            logger.warning("[canonical-char] LLM call failed: %s", e)
            return None

        if not raw:
            return None

        decision = self._parse(raw)
        if decision is None:
            return None

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "[canonical-char] ok is_canonical=%s franchise=%s name=%s "
            "prompt_len=%d elapsed_ms=%d",
            decision.is_canonical,
            decision.franchise_name,
            decision.canonical_name,
            len(decision.prompt_en),
            elapsed_ms,
        )

        async with self._cache_lock:
            self._cache[cache_key] = decision
        return decision

    # ------------------------------------------------------------------
    # Synchronous wrapper — for callers that run inside a sync
    # build_for_project / infer_classification path.
    # ------------------------------------------------------------------

    def recognize_sync(
        self,
        *,
        character_name: str,
        source_work: str,
        worldview: str = "",
        style_rules: str = "",
        fallback_appearance: str = "",
        timeout: float = 20.0,
    ) -> Optional[CanonicalCharacterAnchor]:
        """Sync wrapper for use inside sync code paths.

        Uses a dedicated worker thread (with its own event loop) so this
        is safe to call from inside a running asyncio loop — the caller's
        loop is NOT reused, avoiding the classic "loop is already running"
        deadlock.
        """
        import concurrent.futures

        coro = self.recognize(
            character_name=character_name,
            source_work=source_work,
            worldview=worldview,
            style_rules=style_rules,
            fallback_appearance=fallback_appearance,
        )
        try:
            # If we're NOT inside a running loop, asyncio.run is fine.
            asyncio.get_running_loop()
            in_loop = True
        except RuntimeError:
            in_loop = False

        if not in_loop:
            return asyncio.run(coro)

        # We're inside a running loop — spin up a worker thread with its
        # own loop so we can block on the result without deadlocking.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning(
                    "[canonical-char] recognize_sync timeout after %.1fs", timeout
                )
                return None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=CANONICAL_CHARACTER_RECOGNITION_API_KEY,
                pool_env=(
                    "CANONICAL_CHARACTER_RECOGNITION_API_KEYS",
                    "OPENAI_API_KEYS",
                ),
                allow_byok=True,
                base_url=CANONICAL_CHARACTER_RECOGNITION_BASE_URL,
            )
        return self._client

    def _build_payload(
        self,
        *,
        character_name: str,
        source_work: str,
        worldview: str,
        style_rules: str,
        fallback_appearance: str,
    ) -> Dict[str, Any]:
        def _short(s: Any, limit: int) -> str:
            text = str(s or "").strip()
            return text if len(text) <= limit else text[:limit] + "…"

        return {
            "character_name": _short(character_name, 100),
            "source_work": _short(source_work, 200),
            "worldview": _short(worldview, 800),
            "style_rules": _short(style_rules, 400),
            "fallback_appearance": _short(fallback_appearance, 600),
        }

    def _cache_key(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _system_prompt(self) -> str:
        return (
            "You are an expert otaku / visual-novel character director with "
            "encyclopedic knowledge of anime, manga, game, film and novel "
            "franchises worldwide.\n\n"
            "Your job: decide whether a named character is a CANONICAL "
            "character from an existing well-known franchise, and if so, "
            "produce a DETAILED English appearance prompt that locks a "
            "portrait painter to that character's official design.\n\n"
            "HARD RULES:\n"
            "1. Output strict JSON matching the schema. No markdown, no "
            "comments.\n"
            "2. Set is_canonical=true ONLY when you are confident the "
            "character is recognizable from an existing franchise. When "
            "in doubt, return is_canonical=false.\n"
            "3. When is_canonical=true, `prompt_en` MUST:\n"
            "   - Be ENGLISH only. Translate any Chinese input.\n"
            "   - Start with 'canonical <Name> from <Franchise>, '.\n"
            "   - Include: body type, height/build, hair color+style, eye "
            "color, signature outfit, iconic accessories, distinguishing "
            "features (scars / tattoos / weapons / birthmarks).\n"
            "   - End with '; preserve the recognizable official character "
            "design and silhouette'.\n"
            "   - Be 60-600 chars. Detailed enough that a painter who has "
            "never seen the franchise could still reproduce the character.\n"
            "4. When is_canonical=false, leave franchise_name / "
            "canonical_name / prompt_en as empty strings.\n"
            "5. The user's `fallback_appearance` field is a hint about "
            "how the user described this character — use it to disambiguate "
            "WHICH official version (e.g. pre-timeskip vs post-timeskip) "
            "when relevant, but DO NOT just echo it back. If canonical, "
            "describe the OFFICIAL design, not the user's fan description.\n"
            "   Resolve one explicit `look_variant` from the story period. "
            "Put only cross-scene fixed traits in `fixed_features`, "
            "`canonical_outfit`, and `asymmetric_traits`; never include current "
            "emotion, pose, injury, bandages, or temporary clothing.\n"
            "6. NEVER confuse original characters (OCs) with canonical "
            "ones. If `source_work` is empty or '原创' or 'original', "
            "strongly lean is_canonical=false.\n"
            "7. `rationale` is for audit logging — one sentence explaining "
            "your decision.\n"
        )

    def _user_prompt(self, payload: Dict[str, Any]) -> str:
        return (
            "Decide whether this character is canonical.\n\n"
            f"INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "OUTPUT (JSON only):\n"
            "{\n"
            '  "is_canonical": true | false,\n'
            '  "franchise_name": "<English franchise or empty>",\n'
            '  "canonical_name": "<official name or empty>",\n'
            '  "prompt_en": "<60-600 char English appearance prompt or empty>",\n'
            '  "rationale": "<20-150 char English explanation>",\n'
            '  "look_variant": "<official era/version or empty>",\n'
            '  "fixed_features": ["<stable English visual trait>"],\n'
            '  "canonical_outfit": "<stable official outfit in English or empty>",\n'
            '  "asymmetric_traits": ["<side-specific stable English trait>"]\n'
            "}"
        )

    async def _call_llm(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        response = await client.chat.completions.create(
            model=resolve_request_model(CANONICAL_CHARACTER_RECOGNITION_MODEL),
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(payload)},
            ],
            temperature=CANONICAL_CHARACTER_RECOGNITION_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        if not raw:
            logger.warning("[canonical-char] empty LLM response")
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(
                "[canonical-char] non-JSON response: %s | raw=%s", e, raw[:200]
            )
            return None
        if not isinstance(data, dict):
            logger.warning(
                "[canonical-char] non-object JSON: %s", type(data).__name__
            )
            return None
        return data

    def _parse(self, raw: Dict[str, Any]) -> Optional[CanonicalCharacterAnchor]:
        try:
            model = CanonicalCharacterDecisionModel(**raw)
        except ValidationError as e:
            logger.warning("[canonical-char] schema validation failed: %s", e)
            return None

        if not model.is_canonical:
            return CanonicalCharacterAnchor(
                is_canonical=False,
                franchise_name="",
                canonical_name="",
                prompt_en="",
                rationale=str(model.rationale or "").strip(),
            )

        prompt_en = (model.prompt_en or "").strip()
        # English-only enforcement: reject CJK leaking into the prompt.
        if _CJK_RE.search(prompt_en):
            logger.warning(
                "[canonical-char] prompt_en contains CJK — reject (would confuse CogView)"
            )
            return None
        if not (_PROMPT_EN_MIN_CHARS <= len(prompt_en) <= _PROMPT_EN_MAX_CHARS):
            logger.warning(
                "[canonical-char] prompt_en length=%d outside [%d,%d] — reject",
                len(prompt_en),
                _PROMPT_EN_MIN_CHARS,
                _PROMPT_EN_MAX_CHARS,
            )
            return None

        franchise = (model.franchise_name or "").strip()
        canonical = (model.canonical_name or "").strip()
        if not franchise or not canonical:
            logger.warning(
                "[canonical-char] is_canonical=true but franchise/canonical_name empty — reject"
            )
            return None

        return CanonicalCharacterAnchor(
            is_canonical=True,
            franchise_name=franchise,
            canonical_name=canonical,
            prompt_en=prompt_en,
            rationale=str(model.rationale or "").strip(),
            look_variant=str(model.look_variant or "").strip(),
            fixed_features=tuple(
                str(item).strip() for item in model.fixed_features if str(item).strip()
            ),
            canonical_outfit=str(model.canonical_outfit or "").strip(),
            asymmetric_traits=tuple(
                str(item).strip() for item in model.asymmetric_traits if str(item).strip()
            ),
        )


canonical_character_recognition_service = CanonicalCharacterRecognitionService()


__all__ = [
    "CanonicalCharacterAnchor",
    "CanonicalCharacterDecisionModel",
    "CanonicalCharacterRecognitionService",
    "canonical_character_recognition_service",
    "CANONICAL_CHARACTER_RECOGNITION_ENABLED",
]
