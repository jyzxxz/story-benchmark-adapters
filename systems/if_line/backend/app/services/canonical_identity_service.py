"""Canonical source-character identity helpers.

``visual_profile`` is the stable, versioned projection used to search the
generic portrait library.  A canonical identity is deliberately separate: it
records which existing character must be reproduced by image generation
without adding franchise-specific values to the library taxonomy.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable


CANONICAL_IDENTITY_SCHEMA_VERSION = "canonical_identity_v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(values: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(values, (list, tuple)):
        values = [values] if values else []
    result: list[str] = []
    for value in values:
        item = _text(value)
        if item and item not in result:
            result.append(item)
        if len(result) >= limit:
            break
    return result


def build_canonical_identity(
    *,
    franchise_name: str,
    canonical_name: str,
    identity_prompt_en: str,
    recognition_source: str,
    look_variant: str = "",
    fixed_features: Iterable[str] = (),
    canonical_outfit: str = "",
    asymmetric_traits: Iterable[str] = (),
    reference_image: str = "",
    rationale: str = "",
) -> dict[str, Any]:
    """Build the persisted identity layer for a confirmed source character."""
    return {
        "schema_version": CANONICAL_IDENTITY_SCHEMA_VERSION,
        "status": "canonical_confirmed",
        "recognition_source": _text(recognition_source),
        "franchise_name": _text(franchise_name),
        "canonical_name": _text(canonical_name),
        "look_variant": _text(look_variant),
        "fixed_features": _text_list(fixed_features),
        "canonical_outfit": _text(canonical_outfit),
        "asymmetric_traits": _text_list(asymmetric_traits),
        "identity_prompt_en": _text(identity_prompt_en),
        "reference_image": _text(reference_image),
        "rationale": _text(rationale),
    }


def normalize_canonical_identity(value: Any) -> dict[str, Any]:
    """Return a valid confirmed identity or an empty dict.

    Unknown fields are intentionally discarded so transient pose, emotion,
    injury and chapter-state data cannot leak into the fixed identity layer.
    """
    if not isinstance(value, dict):
        return {}
    identity = build_canonical_identity(
        franchise_name=value.get("franchise_name") or value.get("source_work"),
        canonical_name=value.get("canonical_name"),
        identity_prompt_en=value.get("identity_prompt_en") or value.get("prompt_en"),
        recognition_source=value.get("recognition_source") or "persisted",
        look_variant=value.get("look_variant"),
        fixed_features=value.get("fixed_features") or (),
        canonical_outfit=value.get("canonical_outfit"),
        asymmetric_traits=value.get("asymmetric_traits") or (),
        reference_image=value.get("reference_image"),
        rationale=value.get("rationale"),
    )
    if (
        value.get("status") not in {None, "", "canonical_confirmed"}
        or not identity["franchise_name"]
        or not identity["canonical_name"]
        or not identity["identity_prompt_en"]
    ):
        return {}
    return identity


def canonical_identity_fingerprint(identity: Any) -> str:
    """Fingerprint only fixed source identity, never moment-specific state."""
    normalized = normalize_canonical_identity(identity)
    if not normalized:
        return ""
    payload = {
        key: normalized[key]
        for key in (
            "schema_version",
            "franchise_name",
            "canonical_name",
            "look_variant",
            "fixed_features",
            "canonical_outfit",
            "asymmetric_traits",
            "identity_prompt_en",
            "reference_image",
        )
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "ci1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def canonical_identity_prompt(character: Any) -> str:
    if not isinstance(character, dict):
        return ""
    identity = normalize_canonical_identity(character.get("canonical_identity"))
    return _text(identity.get("identity_prompt_en"))


def canonical_identity_features(character: Any) -> tuple[str, ...]:
    if not isinstance(character, dict):
        return ()
    identity = normalize_canonical_identity(character.get("canonical_identity"))
    return tuple(identity.get("fixed_features") or ())


__all__ = [
    "CANONICAL_IDENTITY_SCHEMA_VERSION",
    "build_canonical_identity",
    "normalize_canonical_identity",
    "canonical_identity_fingerprint",
    "canonical_identity_prompt",
    "canonical_identity_features",
]
