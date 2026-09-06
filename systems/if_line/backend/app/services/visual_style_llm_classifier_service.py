"""LLM-driven visual style classifier.

Replaces the hardcoded ``_MEDIUM_KEYWORDS`` keyword-bucket path in
:mod:`project_visual_bible_service`. Given the project's story bible +
project metadata, the LLM produces a COMPLETE art-direction block
(medium, art_direction, linework, shading, texture, palette, contrast,
lighting, palette_policy, forbidden_styles) — not just a medium label.

Design contract
---------------
* Canonical IP profiles (``source_visual_profile_service``) always win.
  ``detect()`` upstream returns a non-None profile → caller skips this
  classifier entirely. This service never sees Genshin / canonical-IP
  projects.
* Output is a :class:`VisualStyleDecision` matching the fields consumed
  by :class:`ProjectVisualBible`. The caller drops it into
  ``_DEFAULT_ANIME_CEL`` via ``replace()`` so portrait / background /
  keyframe share ONE locked style block.
* Determinism: temperature is pinned low (0.2). The same input payload
  sha256 reuses a process-level cache entry, so locked projects do not
  pay extra LLM calls on every ``build_for_project``.
* Failure isolation: LLM timeout / API-key missing / validation error /
  env-disabled → returns ``None`` and the caller falls back to the
  keyword-bucket path. The classifier NEVER raises.
* BYOK-aware: routes through ``text_llm_config`` + ``PooledAsyncOpenAI``
  so per-request X-LLM-Model / X-LLM-Base-Url headers propagate.
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
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from app.services.api_key_pool import PooledAsyncOpenAI, api_key_available
from app.services.text_llm_config import (
    DEFAULT_TEXT_LLM_MODEL,
    resolve_request_model,
    text_llm_api_key,
    text_llm_base_url,
    text_llm_model,
)

logger = logging.getLogger("visual_style_llm_classifier")

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

STYLE_CLASSIFIER_ENABLED = os.getenv(
    "STYLE_CLASSIFIER_ENABLED", "true"
).lower() == "true"
STYLE_CLASSIFIER_MODEL = text_llm_model(
    "STYLE_CLASSIFIER_MODEL", default=DEFAULT_TEXT_LLM_MODEL
)
STYLE_CLASSIFIER_API_KEY = text_llm_api_key("STYLE_CLASSIFIER_API_KEY")
STYLE_CLASSIFIER_BASE_URL = text_llm_base_url("STYLE_CLASSIFIER_BASE_URL")
STYLE_CLASSIFIER_TIMEOUT = float(os.getenv("STYLE_CLASSIFIER_TIMEOUT", "12.0"))
STYLE_CLASSIFIER_MAX_CONCURRENCY = int(
    os.getenv("STYLE_CLASSIFIER_MAX_CONCURRENCY", "2")
)
STYLE_CLASSIFIER_TEMPERATURE = float(
    os.getenv("STYLE_CLASSIFIER_TEMPERATURE", "0.2")
)
# Inputs larger than this get truncated before being sent. CogView art
# direction is decided by a few strong signals, not by 10k-char novel chapters.
_MAX_INPUT_CHARS = 4000

# Canonical medium enum — same vocabulary as project_visual_bible_service.
# The LLM is forced to pick one of these. Anything else → validation fail.
_VALID_MEDIUMS: Tuple[str, ...] = (
    "anime_cel",
    "ink_color_guofeng",
    "digital_painterly",
    "concept_art_realism",
)

# Palette cap: 4-6 colors keep CogView-4 attention focused. Too many colors
# produce rainbow mush.
_PALETTE_MIN = 3
_PALETTE_MAX = 6

_CJK_RE = re.compile(r"[一-鿿]")


# ----------------------------------------------------------------------
# Output schema
# ----------------------------------------------------------------------


class VisualStyleDecisionModel(BaseModel):
    """Pydantic schema for the LLM's JSON output."""

    medium: Literal["anime_cel", "ink_color_guofeng", "digital_painterly", "concept_art_realism"] = Field(
        description=(
            "One of: anime_cel, ink_color_guofeng, digital_painterly, "
            "concept_art_realism. Pick the SINGLE most fitting visual "
            "rendering family for the project."
        )
    )
    art_direction: str = Field(
        description=(
            "One-sentence English art-direction brief, 60-200 chars. "
            "Describe the visual language cohesively — NEVER mention the "
            "narrative genre as a style. Bad: 'historical Chinese style'. "
            "Good: 'refined 2D anime with luminous painterly environments, "
            "soft cel-shaded characters, layered regional costume detailing'."
        )
    )
    linework: str = Field(
        description=(
            "English linework description, 20-80 chars. Examples: 'crisp "
            "anime line art with tapered ends', 'restrained brush-like "
            "ink linework'."
        )
    )
    shading: str = Field(
        description=(
            "English shading description, 20-80 chars. Examples: 'flat "
            "two-level cel shading with subtle rim light', 'realistic "
            "form shading with controlled ambient occlusion'."
        )
    )
    texture: str = Field(
        description=(
            "English texture description, 20-80 chars. Examples: 'subtle "
            "film grain, no paper texture', 'very subtle paper grain'."
        )
    )
    base_palette: List[str] = Field(
        description=(
            "3-6 English color names forming a cohesive palette. Examples: "
            "['luminous elemental gold', 'jade teal', 'warm ivory']. "
            "Do NOT use hex codes."
        )
    )
    contrast_policy: str = Field(
        description=(
            "One of: low / medium_low / medium / cinematic. "
            "Maps to the project's locked contrast level."
        )
    )
    lighting_policy: str = Field(
        description=(
            "One of: soft_diffused / natural_window / cinematic_directional "
            "/ golden_hour / moonlit / candlelit."
        )
    )
    palette_policy: str = Field(
        description=(
            "One of: warm_cool_balanced / low_sat_unity / luminous_elemental_harmony "
            "/ warm_classical / cool_neon / muted_earth."
        )
    )
    forbidden_styles: List[str] = Field(
        description=(
            "4-10 short English phrases naming art styles that would "
            "BETRAY the chosen direction. Include 'photorealistic "
            "rendering' and '3D game render' unless medium is "
            "concept_art_realism. Include 'inconsistent art style drift' "
            "always."
        )
    )
    rationale: str = Field(
        description=(
            "Short English explanation, 30-100 chars, of WHY this style "
            "fits the story. Used for audit logging only."
        )
    )


@dataclass(frozen=True)
class VisualStyleDecision:
    """Caller-facing dataclass — mirrors ProjectVisualBible locked fields."""

    medium: str
    art_direction: str
    linework: str
    shading: str
    texture: str
    base_palette: Tuple[str, ...]
    contrast_policy: str
    lighting_policy: str
    palette_policy: str
    forbidden_styles: Tuple[str, ...]
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------


class VisualStyleLLMClassifierService:
    """Async LLM classifier with process-level cache + graceful fallback."""

    def __init__(self) -> None:
        self._cache: Dict[str, VisualStyleDecision] = {}
        self._cache_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(STYLE_CLASSIFIER_MAX_CONCURRENCY)
        self._client: Optional[Any] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def classify(
        self,
        *,
        project_title: str,
        project_style: str,
        worldview: str,
        style_rules: str,
        source_work: str,
        source_context: str,
        characters: List[Dict[str, Any]],
    ) -> Optional[VisualStyleDecision]:
        """Return a :class:`VisualStyleDecision` or ``None`` on any failure.

        ``None`` instructs the caller to fall back to the keyword-bucket
        path. This method never raises.
        """
        if not STYLE_CLASSIFIER_ENABLED:
            return None
        if not api_key_available(
            STYLE_CLASSIFIER_API_KEY,
            pool_env=("STYLE_CLASSIFIER_API_KEYS", "OPENAI_API_KEYS"),
        ):
            logger.info(
                "[style-classifier] disabled: API key not configured, "
                "fallback to keyword path"
            )
            return None

        payload = self._build_payload(
            project_title=project_title,
            project_style=project_style,
            worldview=worldview,
            style_rules=style_rules,
            source_work=source_work,
            source_context=source_context,
            characters=characters,
        )
        cache_key = self._cache_key(payload)
        async with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        start = time.time()
        try:
            async with self._semaphore:
                raw = await asyncio.wait_for(
                    self._call_llm(payload), timeout=STYLE_CLASSIFIER_TIMEOUT
                )
        except asyncio.TimeoutError:
            logger.warning("[style-classifier] timeout after %.1fs", STYLE_CLASSIFIER_TIMEOUT)
            return None
        except Exception as e:
            logger.warning("[style-classifier] LLM call failed: %s", e)
            return None

        if not raw:
            return None

        decision = self._parse(raw)
        if decision is None:
            return None

        elapsed_ms = int((time.time() - start) * 1000)
        logger.info(
            "[style-classifier] ok medium=%s palette=%d rationale=%r elapsed_ms=%d",
            decision.medium, len(decision.base_palette), decision.rationale[:80], elapsed_ms,
        )

        async with self._cache_lock:
            self._cache[cache_key] = decision
        return decision

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = PooledAsyncOpenAI(
                api_key=STYLE_CLASSIFIER_API_KEY,
                pool_env=("STYLE_CLASSIFIER_API_KEYS", "OPENAI_API_KEYS"),
                allow_byok=True,
                base_url=STYLE_CLASSIFIER_BASE_URL,
            )
        return self._client

    def _build_payload(
        self,
        *,
        project_title: str,
        project_style: str,
        worldview: str,
        style_rules: str,
        source_work: str,
        source_context: str,
        characters: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Flatten inputs into the LLM payload. Truncate aggressively.

        Character names + appearance lines carry the strongest style
        signal (e.g. 'hanfu' vs 'spacesuit' vs 'Teyvat robes'); we keep
        them in full. Worldview/style_rules get truncated to keep the
        prompt under ~4k chars.
        """
        def _short(s: Any, limit: int) -> str:
            text = str(s or "").strip()
            return text if len(text) <= limit else text[:limit] + "…"

        # Characters: keep only name + appearance + outfit, drop verbose
        # backstory. Limit to 8 characters max to bound prompt size.
        chars_compact: List[Dict[str, str]] = []
        for c in (characters or [])[:8]:
            if isinstance(c, str):
                chars_compact.append({"name": c})
                continue
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or c.get("name_cn") or "").strip()
            if not name:
                continue
            chars_compact.append({
                "name": name,
                "appearance": _short(c.get("appearance"), 200),
                "outfit": _short(c.get("outfit"), 80),
            })

        return {
            "project_title": _short(project_title, 200),
            "project_style": _short(project_style, 400),
            "source_work": _short(source_work, 120),
            "worldview": _short(worldview, 1200),
            "style_rules": _short(style_rules, 600),
            "source_context": _short(source_context, 600),
            "characters": chars_compact,
        }

    def _cache_key(self, payload: Dict[str, Any]) -> str:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _system_prompt(self) -> str:
        return (
            "You are a senior visual director for a 2D visual-novel studio. "
            "Given the project's title, worldview, style rules and character roster, "
            "decide the COHESIVE visual art direction that will be locked across "
            "PORTRAIT sprites, BACKGROUND environments and KEYFRAME CGs.\n\n"
            "HARD RULES:\n"
            "1. Output strict JSON matching the schema. No markdown, no comments.\n"
            "2. `medium` MUST be one of: anime_cel, ink_color_guofeng, "
            "digital_painterly, concept_art_realism. Pick exactly one.\n"
            "3. All textual fields are ENGLISH. Translate any Chinese input.\n"
            "4. Never confuse narrative genre (sci-fi / historical / fantasy) with "
            "rendering medium. A sci-fi story can be anime_cel. A historical story "
            "can be digital_painterly. Pick the medium that best fits the studio's "
            "intent in `project_style` / `style_rules`.\n"
            "5. If the user explicitly named a source franchise in `source_work` "
            "(e.g. '原神', 'Genshin Impact'), ADAPT to that franchise's recognizable "
            "visual language — do NOT pick generic genre art.\n"
            "6. `art_direction` is the SINGLE most important field. It must read as "
            "a cohesive style brief, not a genre label.\n"
            "7. `base_palette`: 3-6 cohesive English color names, no hex.\n"
            "8. `forbidden_styles`: list art families that would betray the chosen "
            "direction (e.g. photorealistic rendering, 3D game render, inconsistent "
            "art style drift).\n"
            "9. If `project_style` or `style_rules` explicitly name a style (水墨 / "
            "厚涂 / 写实 / cel-shaded), honor it as a hard constraint.\n"
            "10. `rationale` is for audit logging — explain in one sentence why "
            "this style fits.\n"
        )

    def _user_prompt(self, payload: Dict[str, Any]) -> str:
        return (
            "Decide the locked visual art direction for this project.\n\n"
            f"PROJECT INPUT:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
            "OUTPUT (JSON only):\n"
            "{\n"
            '  "medium": "anime_cel | ink_color_guofeng | digital_painterly | concept_art_realism",\n'
            '  "art_direction": "<60-200 char English cohesive style brief>",\n'
            '  "linework": "<20-80 char English>",\n'
            '  "shading": "<20-80 char English>",\n'
            '  "texture": "<20-80 char English>",\n'
            '  "base_palette": ["<color1>", "<color2>", "..."],\n'
            '  "contrast_policy": "low | medium_low | medium | cinematic",\n'
            '  "lighting_policy": "soft_diffused | natural_window | cinematic_directional | golden_hour | moonlit | candlelit",\n'
            '  "palette_policy": "warm_cool_balanced | low_sat_unity | luminous_elemental_harmony | warm_classical | cool_neon | muted_earth",\n'
            '  "forbidden_styles": ["<phrase1>", "<phrase2>", "..."],\n'
            '  "rationale": "<30-100 char English explanation>"\n'
            "}"
        )

    async def _call_llm(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        client = self._get_client()
        response = await client.chat.completions.create(
            model=resolve_request_model(STYLE_CLASSIFIER_MODEL),
            messages=[
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": self._user_prompt(payload)},
            ],
            temperature=STYLE_CLASSIFIER_TEMPERATURE,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        raw = raw.strip()
        if not raw:
            logger.warning("[style-classifier] empty LLM response")
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning("[style-classifier] non-JSON response: %s | raw=%s", e, raw[:200])
            return None
        if not isinstance(data, dict):
            logger.warning("[style-classifier] non-object JSON: %s", type(data).__name__)
            return None
        return data

    def _parse(self, raw: Dict[str, Any]) -> Optional[VisualStyleDecision]:
        try:
            model = VisualStyleDecisionModel(**raw)
        except ValidationError as e:
            logger.warning("[style-classifier] schema validation failed: %s", e)
            return None

        # Normalize + post-validate
        if model.medium not in _VALID_MEDIUMS:
            logger.warning(
                "[style-classifier] medium=%r not in %s — reject",
                model.medium, _VALID_MEDIUMS,
            )
            return None

        palette = tuple(str(p).strip() for p in model.base_palette if str(p).strip())
        if not (_PALETTE_MIN <= len(palette) <= _PALETTE_MAX):
            logger.warning(
                "[style-classifier] palette size=%d outside [%d,%d] — reject",
                len(palette), _PALETTE_MIN, _PALETTE_MAX,
            )
            return None

        # Reject Chinese leaking into the English-only fields — those would
        # confuse CogView-4 which expects English style vocabulary.
        def _en_only(s: str) -> str:
            return "" if _CJK_RE.search(s or "") else (s or "").strip()

        art_direction = _en_only(model.art_direction)
        linework = _en_only(model.linework)
        shading = _en_only(model.shading)
        texture = _en_only(model.texture)
        if not (art_direction and linework and shading and texture):
            logger.warning(
                "[style-classifier] English style fields empty or contain CJK — reject"
            )
            return None

        forbidden = tuple(
            _en_only(p) for p in model.forbidden_styles if _en_only(p)
        )
        if len(forbidden) < 2:
            forbidden = forbidden + (
                "photorealistic rendering",
                "inconsistent art style drift",
            )

        # Palette items: allow CJK here (they get translated downstream
        # if needed) but most LLMs will produce English color names anyway.

        return VisualStyleDecision(
            medium=model.medium,
            art_direction=art_direction,
            linework=linework,
            shading=shading,
            texture=texture,
            base_palette=palette,
            contrast_policy=model.contrast_policy.strip(),
            lighting_policy=model.lighting_policy.strip(),
            palette_policy=model.palette_policy.strip(),
            forbidden_styles=forbidden,
            rationale=str(model.rationale or "").strip(),
        )


visual_style_llm_classifier_service = VisualStyleLLMClassifierService()


__all__ = [
    "VisualStyleDecision",
    "VisualStyleDecisionModel",
    "VisualStyleLLMClassifierService",
    "visual_style_llm_classifier_service",
    "STYLE_CLASSIFIER_ENABLED",
]
