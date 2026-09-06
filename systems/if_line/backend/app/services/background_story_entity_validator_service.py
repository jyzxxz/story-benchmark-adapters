"""Background story-entity leak validator (Stage_Background_Entity_Exclusion §XII).

Post-generation validation that the background image does not contain any
forbidden story character — including non-human species (robots, animals,
monsters, spirits, holograms).

The validator uses a vision-language model (GLM-4V by default) to inspect
the generated background image against the structured forbidden-entity
list. It outputs a :class:`BackgroundStoryEntityValidation` that the
background-generation pipeline uses to decide retry vs. quarantine.

Design notes
------------

- The validator NEVER calls the image-generation API. It only consumes an
  already-generated image plus the structured entity list.
- Identity reference portraits (the ``identity_master`` Asset rows) are
  surfaced to the VLM via a single textual description per character so it
  has signal beyond names — image embedding comparison is intentionally out
  of scope for this iteration (the user explicitly chose prompt-strengthen +
  VLM only, see Stage_Background_Entity_Exclusion decision matrix).
- All call sites are mockable via ``generation_runner`` / ``vlm_client``
  kwargs so unit tests don't need real API keys.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("bg_entity_validator")


@dataclass(frozen=True)
class StoryEntityLeak:
    """One detected leak of a forbidden entity into the background.

    ``forbidden_character_id`` / ``forbidden_name`` are populated when the
    leak can be attributed to a specific entity in the input list. When the
    VLM only reports "a humanoid robot is present" without naming it, both
    stay None and ``detected_entity_type`` carries the species hint.
    """

    detected_entity_type: str
    forbidden_character_id: Optional[str] = None
    forbidden_name: Optional[str] = None
    identity_similarity: Optional[float] = None
    evidence: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0
    indirect_representation: bool = False
    detail_region: Optional[tuple[float, float, float, float]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "detected_entity_type": self.detected_entity_type,
            "forbidden_character_id": self.forbidden_character_id,
            "forbidden_name": self.forbidden_name,
            "identity_similarity": self.identity_similarity,
            "evidence": list(self.evidence),
            "confidence": self.confidence,
            "indirect_representation": self.indirect_representation,
            "detail_region": (
                list(self.detail_region) if self.detail_region else None
            ),
        }


@dataclass(frozen=True)
class BackgroundStoryEntityValidation:
    """Aggregate result for one background image.

    ``passed`` is the single bit the caller cares about. ``reasons`` carries
    human-readable strings for logging / quarantine metadata.
    """

    passed: bool
    detected_entity_count: int = 0
    matched_forbidden_entities: tuple[StoryEntityLeak, ...] = field(default_factory=tuple)
    generic_environment_entities: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reasons: tuple[str, ...] = field(default_factory=tuple)
    raw_vlm_response: Optional[dict[str, Any]] = None

    @property
    def quarantinable(self) -> bool:
        """True when the leak is severe enough to quarantine (vs. retry).

        Hard attribution (a forbidden entity was identified by name or
        serial number) → quarantine. Generic "looks like a person/robot"
        without ID match → retry-eligible.
        """
        return any(
            leak.forbidden_character_id or leak.forbidden_name
            for leak in self.matched_forbidden_entities
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "detected_entity_count": self.detected_entity_count,
            "matched_forbidden_entities": [
                leak.to_dict() for leak in self.matched_forbidden_entities
            ],
            "generic_environment_entities": [
                dict(geo) for geo in self.generic_environment_entities
            ],
            "reasons": list(self.reasons),
        }


def _format_entity_lines(entities: list[dict[str, Any]]) -> str:
    """Render forbidden entities as VLM-readable text lines."""
    if not entities:
        return "（无）"
    lines = []
    for i, e in enumerate(entities, start=1):
        names = [e.get("canonical_name")] + list(e.get("aliases") or [])
        names = [str(n).strip() for n in names if n]
        species = e.get("species") or "human"
        sig = "; ".join(e.get("appearance_signature") or [])
        lines.append(
            f"{i}. 名称: {', '.join(names) or '(unknown)'} | 物种: {species} | "
            f"身份特征: {sig or '(none)'}"
        )
    return "\n".join(lines)


def _build_vlm_prompt(entities: list[dict[str, Any]]) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for the leak-detection VLM call."""
    entity_lines = _format_entity_lines(entities)
    system_prompt = (
        "你是背景图剧情角色泄漏验收器。你的任务是判断一张视觉小说背景图是否"
        "违反了「禁入剧情角色实体」规则。\n"
        "禁入依据是「剧情角色身份」，不是物种本身——即：\n"
        "- 故事主角、命名角色（无论是人类、机器人、仿生人、动物、怪物、灵体、\n"
        "  外星生命、AI 全息、数字生命等）都不得直接出现；\n"
        "- 也不得通过剪影、倒影、海报、雕像、屏幕画面、全息影像、备用机体、\n"
        "  复制体或高度相似的实体间接出现；\n"
        "- 但场景合理需要的普通无人格机械设备、工业机械臂、传送带、普通无人\n"
        "  载具、无关动物可以保留，前提是它们不复制任何被禁入角色的身份特征。\n"
        "输出严格 JSON，不要 markdown 围栏。"
    )
    user_prompt = f"""判断这张背景图，对照下面的禁入剧情角色实体清单：

{entity_lines}

需要逐项检查：
1. 是否出现上述清单中的任何角色（直接出现）？
2. 是否出现清单中角色的剪影 / 倒影 / 海报 / 雕像 / 屏幕画面 / 全息影像 / 残骸 / 备用机体 / 复制体？
3. 是否出现任何人物、人脸、人体轮廓、人形主体、人群、照片人像、屏幕人像？
4. 是否出现明显的「机器人 / 仿生人 / 机械生命」主体？若有，外观是否与上面任一机器人角色高度相似？
5. 是否出现明显的「动物 / 怪物 / 灵体」主体？若有，外观是否与上面任一非人类角色高度相似？
6. 场景中是否还有普通的无人格工业机械、传送带、无人载具、无关动物？

输出 JSON:
{{
  "forbidden_entity_matches": [
    {{
      "forbidden_character_id": "<id from list, or null>",
      "forbidden_name": "<name, or null>",
      "detected_entity_type": "<human|robot|android|animal|monster|spirit|hologram|creature|other>",
      "indirect_representation": <true if silhouette/reflection/poster/etc>,
      "identity_similarity": 0.0-1.0,
      "confidence": 0.0-1.0,
      "evidence": ["<短句描述>"]
    }}
  ],
  "generic_environment_entities": [
    {{"type": "<industrial_arm|conveyor|vehicle|animal|other>", "description": "<短句>"}}
  ],
  "any_human_present": true|false,
  "any_character_like_subject_present": true|false,
  "reasons": ["<短句>"]
}}

判定规则：
- 只要 forbidden_entity_matches 中有一条 confidence >= 0.5 且 identity_similarity >= 0.5，
  即视为发生剧情角色泄漏。
- indirect_representation=true 也算泄漏。
- 仅当 generic_environment_entities 中的项目与清单角色身份特征不重合时才视为合法。
"""
    return system_prompt, user_prompt


class BackgroundStoryEntityValidatorService:
    """Detect leaks of forbidden story entities in a background image.

    Public entry point: :meth:`validate`. The service tries to call a VLM
    (OpenAI-compatible chat completions endpoint with image input). When
    the VLM is unavailable (no API key, network error, etc.) the validator
    returns a *pass* with a reason explaining the bypass — same posture as
    the existing background validator, so production traffic isn't blocked
    when the optional VLM path is off.
    """

    def __init__(
        self,
        *,
        config: Optional[dict[str, Any]] = None,
        vlm_client: Any = None,
    ) -> None:
        self._config = config or {}
        # When tests inject a client, _vlm_client takes precedence over
        # the lazy-initialized production client.
        self._vlm_client_override = vlm_client

    def _get_client(self) -> Any:
        if self._vlm_client_override is not None:
            return self._vlm_client_override
        from app.services.image_generation_service import (
            BG_VISION_API_KEY, BG_VISION_BASE_URL,
        )
        from app.services.background_image_validator_service import (
            BackgroundImageValidatorService,
        )
        # Reuse the existing helper so we share the API-key pool & base URL.
        return BackgroundImageValidatorService()._get_async_vision_client(
            BG_VISION_API_KEY, BG_VISION_BASE_URL,
        )

    async def validate(
        self,
        image_path: Path,
        entities: list[dict[str, Any]],
        *,
        timeout: float = 12.0,
    ) -> BackgroundStoryEntityValidation:
        """Validate one background image against the forbidden-entity list.

        Empty entity list → automatic pass (no rule to break).
        Image missing → quarantine (caller decides retry vs. drop).
        """
        if not entities:
            return BackgroundStoryEntityValidation(
                passed=True,
                detected_entity_count=0,
                reasons=("no forbidden entities registered",),
            )
        if not image_path or not Path(image_path).exists():
            return BackgroundStoryEntityValidation(
                passed=False,
                detected_entity_count=0,
                reasons=(f"image not found: {image_path}",),
            )

        try:
            with open(image_path, "rb") as f:
                img_bytes = f.read()
        except OSError as exc:
            return BackgroundStoryEntityValidation(
                passed=False,
                detected_entity_count=0,
                reasons=(f"image read failed: {exc}",),
            )
        b64 = base64.b64encode(img_bytes).decode("ascii")
        suffix = Path(image_path).suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        data_url = f"data:{mime};base64,{b64}"

        system_prompt, user_prompt = _build_vlm_prompt(entities)
        try:
            client = self._get_client()
            from app.services.image_generation_service import BG_VISION_MODEL
            import asyncio

            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=BG_VISION_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": [
                            {"type": "text", "text": user_prompt},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ]},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                ),
                timeout=timeout,
            )
            content = resp.choices[0].message.content or "{}"
            data = _loose_json(content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("entity VLM validation failed: %s", exc)
            return BackgroundStoryEntityValidation(
                passed=True,
                detected_entity_count=0,
                reasons=(f"VLM unavailable, defaulting to pass: {exc}",),
            )

        return _interpret_vlm_result(data, entities)


def _loose_json(content: str) -> dict[str, Any]:
    """Tolerant JSON parse — strips code fences and trailing commas."""
    import json as _json
    import re as _re

    if not content:
        return {}
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = _re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = _re.sub(r"\n?```$", "", stripped)
    try:
        return _json.loads(stripped) or {}
    except ValueError:
        # Try to extract the first {...} block.
        match = _re.search(r"\{.*\}", stripped, flags=_re.DOTALL)
        if match:
            try:
                return _json.loads(match.group(0)) or {}
            except ValueError:
                return {}
        return {}


def _interpret_vlm_result(
    data: dict[str, Any],
    entities: list[dict[str, Any]],
) -> BackgroundStoryEntityValidation:
    """Convert VLM JSON into a BackgroundStoryEntityValidation.

    A leak is registered when ``confidence >= 0.5`` and either:
    - ``identity_similarity >= 0.5`` for a specific forbidden_character_id, OR
    - ``indirect_representation=True`` for any forbidden entity, OR
    - ``detected_entity_type == 'human'`` and ``any_human_present=True``.
    """
    matches_raw = data.get("forbidden_entity_matches") or []
    if not isinstance(matches_raw, list):
        matches_raw = []
    generic_raw = data.get("generic_environment_entities") or []
    if not isinstance(generic_raw, list):
        generic_raw = []
    reasons_raw = data.get("reasons") or []
    if not isinstance(reasons_raw, list):
        reasons_raw = [str(reasons_raw)]

    matched: list[StoryEntityLeak] = []
    for m in matches_raw:
        if not isinstance(m, dict):
            continue
        confidence = float(m.get("confidence") or 0.0)
        similarity = m.get("identity_similarity")
        similarity_f = float(similarity) if similarity is not None else None
        indirect = bool(m.get("indirect_representation"))
        detected_type = str(m.get("detected_entity_type") or "other").lower()
        evidence = tuple(
            str(e) for e in (m.get("evidence") or []) if isinstance(e, str)
        )
        leak = StoryEntityLeak(
            detected_entity_type=detected_type,
            forbidden_character_id=(
                str(m.get("forbidden_character_id")) if m.get("forbidden_character_id") else None
            ),
            forbidden_name=(
                str(m.get("forbidden_name")) if m.get("forbidden_name") else None
            ),
            identity_similarity=similarity_f,
            evidence=evidence,
            confidence=confidence,
            indirect_representation=indirect,
        )
        is_leak = (
            confidence >= 0.5
            and (
                (leak.identity_similarity is not None and leak.identity_similarity >= 0.5)
                or indirect
            )
        )
        if is_leak:
            matched.append(leak)
    generic: list[dict[str, Any]] = []
    for g in generic_raw:
        if isinstance(g, dict):
            generic.append(dict(g))

    any_human = bool(data.get("any_human_present"))
    if any_human:
        # Synthesize a leak entry so callers see "human subject detected"
        # even when the VLM didn't attribute it to a specific character.
        if not any(l.detected_entity_type == "human" for l in matched):
            matched.append(StoryEntityLeak(
                detected_entity_type="human",
                confidence=0.8,
                evidence=("VLM reported any_human_present=True",),
            ))

    passed = not matched
    return BackgroundStoryEntityValidation(
        passed=passed,
        detected_entity_count=len(matched),
        matched_forbidden_entities=tuple(matched),
        generic_environment_entities=tuple(generic),
        reasons=tuple(str(r) for r in reasons_raw) or (
            () if passed else ("forbidden story entity leak detected",)
        ),
        raw_vlm_response=data,
    )


__all__ = [
    "BackgroundStoryEntityValidation",
    "BackgroundStoryEntityValidatorService",
    "StoryEntityLeak",
]
