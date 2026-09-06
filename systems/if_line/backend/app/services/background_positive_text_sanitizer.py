"""Deterministic sanitization for positive background-prompt sections."""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


_ZH_FORBIDDEN_SUBJECT_TERMS = (
    "遗体",
    "躯体",
    "尸体",
    "尸骸",
    "遗骸",
    "尸骨",
    "骸骨",
    "死者",
    "身体",
    "人形",
    "人物",
    "身影",
    "剪影",
    "背影",
    "面容",
    "人脸",
    "皮肤",
    "瞳孔",
    "身着",
    "穿着",
    "衣着",
    "衣物",
    "衣服",
    "服装",
    "装束",
    "袍服",
    "碎袍",
)
_EN_FORBIDDEN_SUBJECT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:corpses?|bodies|body|remains|human|person|people|"
    r"figures?|silhouettes?|faces?|skin|eyes?|wearing|dressed|clothing|clothes|outfit)"
    r"(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
_CLAUSE_SEPARATOR_RE = re.compile(r"([，,。；;！？!?\n]+)")


def forbidden_story_names(
    forbidden_characters: Sequence[str] | None,
    forbidden_entities: Iterable[Mapping[str, Any]] | None,
) -> tuple[str, ...]:
    """Collect canonical names and aliases used by positive-text sanitization."""
    names: list[str] = []
    for name in forbidden_characters or ():
        names.append(str(name or "").strip())
    for entity in forbidden_entities or ():
        if not isinstance(entity, Mapping):
            continue
        for key in ("canonical_name", "name", "name_cn", "english_name", "name_en"):
            names.append(str(entity.get(key) or "").strip())
        for key in ("aliases", "nicknames"):
            values = entity.get(key) or ()
            if isinstance(values, (list, tuple, set)):
                names.extend(str(value or "").strip() for value in values)

    # One-character names and pronouns are too ambiguous for substring removal.
    # Canonical cast names in this pipeline are at least two characters long.
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if len(name) < 2 or key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return tuple(unique)


def _contains_name(text: str, names: Sequence[str]) -> bool:
    folded = text.casefold()
    for name in names:
        candidate = name.casefold()
        if re.search(r"[A-Za-z0-9]", candidate):
            if re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(candidate)}(?![A-Za-z0-9_])",
                folded,
            ):
                return True
        elif candidate in folded:
            return True
    return False


def contains_forbidden_background_subject(
    text: str,
    forbidden_names: Sequence[str] = (),
) -> bool:
    """Return whether a positive prompt fragment depicts a body or cast member."""
    value = str(text or "")
    return (
        any(term in value for term in _ZH_FORBIDDEN_SUBJECT_TERMS)
        or bool(_EN_FORBIDDEN_SUBJECT_RE.search(value))
        or _contains_name(value, forbidden_names)
    )


def sanitize_positive_background_text(
    text: str | None,
    *,
    forbidden_names: Sequence[str] = (),
) -> str:
    """Drop contaminated clauses while preserving adjacent environment detail."""
    value = str(text or "").strip()
    if not value:
        return ""

    pieces = _CLAUSE_SEPARATOR_RE.split(value)
    cleaned: list[str] = []
    for index in range(0, len(pieces), 2):
        clause = pieces[index].strip()
        separator = pieces[index + 1] if index + 1 < len(pieces) else ""
        if not clause or contains_forbidden_background_subject(clause, forbidden_names):
            continue
        cleaned.append(clause)
        if separator:
            cleaned.append(separator)

    return "".join(cleaned).strip(" \t\r\n，,。；;！？!?")


__all__ = [
    "contains_forbidden_background_subject",
    "forbidden_story_names",
    "sanitize_positive_background_text",
]
