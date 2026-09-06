"""Canonical public-library facet tag extraction.

The normalization rules in this module are shared by the catalog importer,
runtime search, and the one-time Alembic backfill.  Keep them deterministic:
``library_tags(category, value)`` is a global identity across catalog versions.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from collections.abc import Iterable, Mapping
from typing import Any


FACET_TAXONOMY_CATEGORIES: tuple[str, ...] = (
    "genre",
    "location",
    "time",
    "weather",
    "atmosphere",
    "camera",
    "age",
    "gender",
    "outfit",
)
FACET_DEDICATED_CATEGORIES: tuple[str, ...] = (
    "style",
    "identity_group",
    "expression",
    "pose",
)
FACET_CATEGORIES: frozenset[str] = frozenset(
    (*FACET_TAXONOMY_CATEGORIES, *FACET_DEDICATED_CATEGORIES)
)
MAX_SELECTED_TAGS = 50

_WHITESPACE_RE = re.compile(r"\s+")
_CATEGORY_SEPARATOR_RE = re.compile(r"[-\s]+")
_CATEGORY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


@dataclass(frozen=True, slots=True)
class FacetTagSpec:
    category: str
    value: str
    display_name: str


def _clean_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(
        " ", unicodedata.normalize("NFKC", str(value or "")).strip()
    )


def normalize_facet_category(value: Any) -> str:
    normalized = _CATEGORY_SEPARATOR_RE.sub("_", _clean_text(value).casefold())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    if not _CATEGORY_RE.fullmatch(normalized):
        raise ValueError(
            "facet category must start with a letter and contain only "
            "lowercase letters, digits, and underscores"
        )
    return normalized


def normalize_facet_value(value: Any) -> str:
    normalized = _clean_text(value).casefold()
    if not normalized:
        raise ValueError("facet value cannot be empty")
    if len(normalized) > 255:
        raise ValueError("facet value cannot exceed 255 characters")
    return normalized


def normalize_display_name(value: Any) -> str:
    normalized = _clean_text(value)
    if not normalized:
        raise ValueError("facet display_name cannot be empty")
    if len(normalized) > 255:
        raise ValueError("facet display_name cannot exceed 255 characters")
    return normalized


def equivalent_display_names(left: str, right: str) -> bool:
    """Compare display labels after their storage normalization."""

    return normalize_display_name(left) == normalize_display_name(right)


def _facet_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if _clean_text(value) else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [item for item in value if isinstance(item, str) and _clean_text(item)]
    return []


def _put_tag(
    target: dict[str, dict[str, str]],
    *,
    category: Any,
    value: Any,
    display_name: Any | None = None,
) -> None:
    normalized_category = normalize_facet_category(category)
    normalized_value = normalize_facet_value(value)
    normalized_display = normalize_display_name(
        value if display_name in (None, "") else display_name
    )
    values = target.setdefault(normalized_category, {})
    existing = values.get(normalized_value)
    if existing is not None and not equivalent_display_names(existing, normalized_display):
        raise ValueError(
            "conflicting display_name for facet tag "
            f"{normalized_category}={normalized_value!r}"
        )
    values.setdefault(normalized_value, normalized_display)


def extract_library_facet_tags(
    *,
    taxonomy: Mapping[str, Any] | None,
    style: Any = None,
    identity_group: Any = None,
    expression: Any = None,
    pose: Any = None,
    explicit_facet_tags: Iterable[Mapping[str, Any]] | None = None,
) -> list[FacetTagSpec]:
    """Extract the authoritative structured facets for one library asset.

    Dedicated columns replace same-category taxonomy values.  An explicit
    ``facet_tags`` category replaces every automatically derived value for that
    category, while categories omitted by the explicit list remain derived.
    """

    grouped: dict[str, dict[str, str]] = {}
    taxonomy_values = dict(taxonomy or {})
    for category in FACET_TAXONOMY_CATEGORIES:
        for raw_value in _facet_values(taxonomy_values.get(category)):
            _put_tag(grouped, category=category, value=raw_value)

    dedicated = {
        "style": style,
        "identity_group": identity_group,
        "expression": expression,
        "pose": pose,
    }
    for category, raw_value in dedicated.items():
        values = _facet_values(raw_value)
        if not values:
            continue
        grouped[category] = {}
        for item in values:
            _put_tag(grouped, category=category, value=item)

    explicit_by_category: dict[str, dict[str, str]] = {}
    for entry in explicit_facet_tags or ():
        if not isinstance(entry, Mapping):
            raise ValueError("facet_tags entries must be objects")
        if "category" not in entry or "value" not in entry:
            raise ValueError("facet_tags entries require category and value")
        _put_tag(
            explicit_by_category,
            category=entry["category"],
            value=entry["value"],
            display_name=entry.get("display_name"),
        )
    for category, values in explicit_by_category.items():
        grouped[category] = values

    return [
        FacetTagSpec(category=category, value=value, display_name=display_name)
        for category in sorted(grouped)
        for value, display_name in sorted(grouped[category].items())
    ]


def normalize_required_facets(
    facets: Mapping[str, Any] | None,
) -> list[FacetTagSpec]:
    """Normalize trusted hard-facet input without restricting categories."""

    grouped: dict[str, dict[str, str]] = {}
    for category, raw_value in (facets or {}).items():
        for value in _facet_values(raw_value):
            _put_tag(grouped, category=category, value=value)
    return [
        FacetTagSpec(category=category, value=value, display_name=display_name)
        for category in sorted(grouped)
        for value, display_name in sorted(grouped[category].items())
    ]
