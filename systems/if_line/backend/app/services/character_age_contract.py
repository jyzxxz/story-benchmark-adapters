"""Shared character age-stage contract for portraits and keyframes.

The canonical character id continues to identify the story person.  Age is a
separate identity variant so a flashback and a present-day scene can share
aliases and dialogue state without sharing portraits, seeds, or master images.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable


AGE_CONTRACT_VERSION = "character-age-v1"
VALID_AGE_GROUPS = (
    "toddler",
    "child",
    "teen",
    "young_adult",
    "adult",
    "middle_aged",
    "senior",
)


@dataclass(frozen=True)
class AgeStageSpec:
    age_range_hint: str
    prompt_label: str
    visual_traits: tuple[str, ...]
    forbidden_traits: tuple[str, ...]
    prompt_tokens: tuple[str, ...]


# This is a visual taxonomy, not project-specific classification logic.  The
# same table is used by demand extraction, prompt validation, cache identity,
# keyframe locks, and post-generation visual validation.
AGE_STAGE_SPECS: dict[str, AgeStageSpec] = {
    "toddler": AgeStageSpec(
        "approximately 1-3 years old",
        "toddler",
        ("toddler facial proportions", "very small toddler body proportions", "soft rounded jawline"),
        ("school-age proportions", "adolescent build", "adult facial structure", "wrinkles"),
        ("toddler", "1-3 years old"),
    ),
    "child": AgeStageSpec(
        "approximately 4-12 years old",
        "child",
        ("child facial proportions", "youthful round jawline", "pre-adolescent body proportions"),
        ("toddler proportions", "mature adult facial structure", "middle-aged wrinkles"),
        ("child", "4-12 years old"),
    ),
    "teen": AgeStageSpec(
        "approximately 13-17 years old",
        "teenage",
        ("adolescent facial proportions", "youthful jawline", "teenage body proportions"),
        ("childlike pre-adolescent proportions", "mature adult facial structure", "middle-aged wrinkles"),
        ("teenage", "teenager", "13-17 years old", "adolescent"),
    ),
    "young_adult": AgeStageSpec(
        "approximately 18-29 years old",
        "young adult",
        ("young-adult facial proportions", "defined but youthful jawline", "fully grown young-adult build"),
        ("child or teenage proportions", "middle-aged wrinkles", "elderly facial structure"),
        ("young adult", "18-29 years old"),
    ),
    "adult": AgeStageSpec(
        "approximately 30-39 years old",
        "adult",
        ("mature adult facial proportions", "defined adult jawline", "fully grown adult build"),
        ("child or teenage proportions", "elderly wrinkles", "frail senior build"),
        ("adult", "30-39 years old"),
    ),
    "middle_aged": AgeStageSpec(
        "visibly approximately 50-59 years old",
        "middle-aged",
        (
            "clearly visible crow's feet at both eyes, deep forehead lines, and nasolabial folds",
            "mature heavier jaw and lower-face structure",
            "slight gray at the temples while preserving the natural hair color",
            "middle-aged adult body proportions",
        ),
        (
            "teenage facial proportions",
            "completely smooth wrinkle-free youthful face",
            "frail elderly proportions",
        ),
        ("middle-aged", "middle aged", "50-59 years old"),
    ),
    "senior": AgeStageSpec(
        "approximately 60 years old or older",
        "senior",
        ("elderly facial structure", "visible age lines and wrinkles", "senior body proportions"),
        ("child or teenage proportions", "completely wrinkle-free young-adult face"),
        ("senior", "elderly", "60 years old or older"),
    ),
}


_ALIASES = {
    "infant": "toddler",
    "baby": "toddler",
    "幼儿": "toddler",
    "婴儿": "toddler",
    "kid": "child",
    "儿童": "child",
    "孩童": "child",
    "小孩": "child",
    "少年": "teen",
    "少女": "teen",
    "青少年": "teen",
    "teenager": "teen",
    "teenage": "teen",
    "adolescent": "teen",
    "青年": "young_adult",
    "年轻人": "young_adult",
    "youngadult": "young_adult",
    "young": "young_adult",
    "成年": "adult",
    "成年人": "adult",
    "middleage": "middle_aged",
    "middleaged": "middle_aged",
    "中年": "middle_aged",
    "老年": "senior",
    "老人": "senior",
    "elderly": "senior",
    "old": "senior",
}


@dataclass(frozen=True)
class CharacterAgeContract:
    version: str
    age_group: str
    age_range_hint: str
    age_visual_traits: tuple[str, ...]
    forbidden_age_traits: tuple[str, ...]
    age_source: str
    source_excerpt: str = ""

    @property
    def prompt_anchor(self) -> str:
        spec = AGE_STAGE_SPECS[self.age_group]
        return ", ".join((
            f"{spec.prompt_label} character {self.age_range_hint}",
            *self.age_visual_traits,
        ))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["age_visual_traits"] = list(self.age_visual_traits)
        out["forbidden_age_traits"] = list(self.forbidden_age_traits)
        out["prompt_anchor"] = self.prompt_anchor
        return out


def normalize_age_group(value: Any, default: str = "young_adult") -> str:
    """Normalize enum labels, natural-language stages, and explicit ages."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default if default in VALID_AGE_GROUPS else "young_adult"
    normalized = re.sub(r"[\s\-_/]+", "_", raw)
    if normalized in VALID_AGE_GROUPS:
        return normalized
    compact = normalized.replace("_", "")
    if raw in _ALIASES:
        return _ALIASES[raw]
    if compact in _ALIASES:
        return _ALIASES[compact]

    # Explicit ages are stronger evidence than stage adjectives.
    match = re.search(r"(?<!\d)(\d{1,3})(?:\s*(?:岁|years?\s*old|y/?o))?(?!\d)", raw)
    if match:
        age = int(match.group(1))
        if age <= 3:
            return "toddler"
        if age <= 12:
            return "child"
        if age <= 17:
            return "teen"
        if age <= 29:
            return "young_adult"
        if age <= 39:
            return "adult"
        if age <= 59:
            return "middle_aged"
        return "senior"
    return default if default in VALID_AGE_GROUPS else "young_adult"


def infer_age_group_from_text(text: str, default: str = "young_adult") -> str:
    """Conservative fallback when structured LLM output is unavailable.

    Strong, local age evidence wins.  Downstream services never repeat these
    patterns; they consume the resulting contract only.
    """
    value = str(text or "")
    explicit = re.search(r"(?<!\d)(\d{1,3})\s*(?:岁|years?\s*old|y/?o)(?!\d)", value, re.I)
    if explicit:
        return normalize_age_group(explicit.group(0), default)
    patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("toddler", ("婴儿时期", "幼儿时期", "婴儿", "幼儿", "toddler", "infant")),
        ("child", ("童年时期", "儿童时期", "孩童时期", "小时候", "childhood", "as a child")),
        ("teen", ("少年时期", "少女时期", "青少年时期", "中学时期", "teenage", "adolescent")),
        ("middle_aged", ("中年时期", "middle-aged", "middle aged")),
        ("senior", ("老年时期", "晚年", "年迈", "elderly", "senior years")),
        ("young_adult", ("青年时期", "年轻时期", "young adult")),
        ("adult", ("成年时期", "成年后", "as an adult")),
    )
    lowered = value.lower()
    for age_group, markers in patterns:
        if any(marker.lower() in lowered for marker in markers):
            return age_group
    return normalize_age_group(default)


def build_age_contract(
    age_group: Any,
    *,
    fallback_age_group: str = "young_adult",
    age_source: str = "character_profile",
    source_excerpt: str = "",
) -> CharacterAgeContract:
    normalized = normalize_age_group(age_group, fallback_age_group)
    spec = AGE_STAGE_SPECS[normalized]
    return CharacterAgeContract(
        version=AGE_CONTRACT_VERSION,
        age_group=normalized,
        age_range_hint=spec.age_range_hint,
        age_visual_traits=spec.visual_traits,
        forbidden_age_traits=spec.forbidden_traits,
        age_source=str(age_source or "character_profile"),
        source_excerpt=str(source_excerpt or "")[:300],
    )


def age_contract_from_profile(
    profile: Any,
    *,
    override: Any = None,
    age_source: str = "character_profile",
    source_excerpt: str = "",
) -> CharacterAgeContract:
    data = profile if isinstance(profile, dict) else {}
    static_group = normalize_age_group(data.get("age_group"))
    requested = override if str(override or "").strip() else static_group
    return build_age_contract(
        requested,
        fallback_age_group=static_group,
        age_source=age_source if str(override or "").strip() else "character_profile",
        source_excerpt=source_excerpt,
    )


def identity_variant_id(character_id: str, age_group: Any) -> str:
    normalized = normalize_age_group(age_group)
    digest = hashlib.sha256(
        f"{character_id}|{AGE_CONTRACT_VERSION}|{normalized}".encode("utf-8")
    ).hexdigest()[:12]
    return f"{character_id}:{normalized}:{digest}"


def apply_age_contract_to_character(
    character: dict[str, Any],
    contract: CharacterAgeContract,
) -> dict[str, Any]:
    """Return a normalized character record with a stage-specific fingerprint."""
    from app.services.character_visual_profile_service import normalize_character

    variant = copy.deepcopy(character or {})
    profile = copy.deepcopy(variant.get("visual_profile") or {})
    profile["age_group"] = contract.age_group
    variant["visual_profile"] = profile
    normalized = normalize_character(variant)
    normalized["age_contract"] = contract.to_dict()
    normalized["identity_variant_id"] = identity_variant_id(
        str(normalized.get("character_id") or ""),
        contract.age_group,
    )
    return normalized


def apply_age_contract_to_appearance(
    appearance: str,
    contract: CharacterAgeContract,
) -> str:
    """Replace stale age wording while preserving all non-age identity detail."""
    text = str(appearance or "").strip()
    stage_pattern = re.compile(
        r"\b(?:toddler|child|teenage|teenager|teen|adolescent|young[- ]adult|"
        r"middle[- ]aged|elderly|senior|adult)\b",
        re.IGNORECASE,
    )
    text = stage_pattern.sub("", text)
    text = re.sub(
        r"\b(?:approximately\s+)?\d{1,3}(?:\s*[-–]\s*\d{1,3})?\s+years?\s+old(?:\s+or\s+older)?\b",
        "",
        text,
        flags=re.IGNORECASE,
    )
    for marker in ("幼儿", "婴儿", "儿童", "孩童", "青少年", "少年", "少女", "青年", "中年", "老年", "年迈"):
        text = text.replace(marker, "")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*,\s*,+", ", ", text).strip(" ,;、")
    return f"{text}; {contract.prompt_anchor}" if text else contract.prompt_anchor


def age_prompt_has_contract(prompt: str, contract: CharacterAgeContract | dict[str, Any]) -> bool:
    data = contract.to_dict() if isinstance(contract, CharacterAgeContract) else dict(contract or {})
    group = normalize_age_group(data.get("age_group"))
    lowered = re.sub(r"\s+", " ", str(prompt or "").lower().replace("–", "-").replace("—", "-"))
    return any(token in lowered for token in AGE_STAGE_SPECS[group].prompt_tokens)


def age_prompt_validation_errors(
    prompt: str,
    contract: CharacterAgeContract | dict[str, Any] | None,
) -> list[str]:
    if not contract:
        return []
    data = contract.to_dict() if isinstance(contract, CharacterAgeContract) else dict(contract)
    group = normalize_age_group(data.get("age_group"))
    if age_prompt_has_contract(prompt, data):
        return []
    return [
        f"final_prompt is missing the required apparent age stage {group}; "
        f"include one of {list(AGE_STAGE_SPECS[group].prompt_tokens)}"
    ]


def age_contract_fingerprint(contract: CharacterAgeContract | dict[str, Any]) -> str:
    data = contract.to_dict() if isinstance(contract, CharacterAgeContract) else dict(contract or {})
    stable = {
        "version": data.get("version") or AGE_CONTRACT_VERSION,
        "age_group": normalize_age_group(data.get("age_group")),
        "age_range_hint": data.get("age_range_hint") or "",
        "age_visual_traits": list(data.get("age_visual_traits") or []),
        "forbidden_age_traits": list(data.get("forbidden_age_traits") or []),
    }
    payload = json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def unique_age_groups(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(normalize_age_group(value) for value in values))


__all__ = [
    "AGE_CONTRACT_VERSION",
    "AGE_STAGE_SPECS",
    "VALID_AGE_GROUPS",
    "CharacterAgeContract",
    "age_contract_fingerprint",
    "age_contract_from_profile",
    "age_prompt_has_contract",
    "age_prompt_validation_errors",
    "apply_age_contract_to_appearance",
    "apply_age_contract_to_character",
    "build_age_contract",
    "identity_variant_id",
    "infer_age_group_from_text",
    "normalize_age_group",
    "unique_age_groups",
]
