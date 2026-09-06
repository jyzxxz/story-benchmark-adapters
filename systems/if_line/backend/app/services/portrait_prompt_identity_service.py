"""Select the single identity source exposed to the portrait prompt rewriter."""
from __future__ import annotations

import re
from typing import Any, Mapping

from app.services.canonical_identity_service import normalize_canonical_identity
from app.services.character_age_contract import AGE_STAGE_SPECS, normalize_age_group


_GENDER_LABELS = {
    "male": "male",
    "男": "male",
    "masculine": "male",
    "female": "female",
    "女": "female",
    "feminine": "female",
    "nonbinary": "nonbinary",
    "non-binary": "nonbinary",
}


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _subject_opening(
    *,
    age_group: Any,
    gender: Any,
    canonical_name: str = "",
    franchise_name: str = "",
) -> str:
    age_label = AGE_STAGE_SPECS[normalize_age_group(age_group)].prompt_label
    gender_label = _GENDER_LABELS.get(_text(gender).lower(), "")
    tokens = ["A", age_label]
    if gender_label:
        tokens.append(gender_label)
    if canonical_name and franchise_name:
        tokens.extend((canonical_name, "from", franchise_name))
    else:
        tokens.append("character")
    return _text(" ".join(tokens))


def build_portrait_rewriter_identity(fields: Mapping[str, Any]) -> dict[str, Any]:
    """Return one rewriter identity payload and its required opening phrase.

    Both source identities remain available on the persisted prompt record for
    their separate gallery and generation contracts. Only this selected payload
    is exposed to the LLM that authors the final image prompt.
    """
    canonical = normalize_canonical_identity(fields.get("canonical_identity"))
    age_contract = fields.get("age_contract")
    age_group = (
        age_contract.get("age_group")
        if isinstance(age_contract, dict) and age_contract.get("age_group")
        else fields.get("age_group")
    )
    if canonical:
        return {
            "portrait_identity": {
                "kind": "canonical",
                "identity": canonical,
            },
            "subject_opening": _subject_opening(
                age_group=age_group,
                gender=fields.get("gender"),
                canonical_name=_text(canonical.get("canonical_name")),
                franchise_name=_text(canonical.get("franchise_name")),
            ),
        }

    profile = fields.get("character_visual_profile")
    generic_identity: dict[str, Any] = {"kind": "generic"}
    if isinstance(profile, dict) and profile:
        generic_identity["visual_profile"] = dict(profile)
    else:
        generic_identity["appearance_prompt"] = _text(
            fields.get("appearance_prompt") or fields.get("appearance_zh")
        )
    return {
        "portrait_identity": generic_identity,
        "subject_opening": _subject_opening(
            age_group=age_group,
            gender=fields.get("gender"),
        ),
    }


__all__ = ["build_portrait_rewriter_identity"]
