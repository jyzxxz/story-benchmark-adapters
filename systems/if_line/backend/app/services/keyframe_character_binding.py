"""Keyframe character binding (Stage_Keyframe_Identity_AR_Blueprint Section B).

A keyframe can feature up to three characters. Each character must carry
its full identity data — not just ``name + appearance`` — so the
downstream prompt builder and reference-image generator can lock facial
identity, hair, gender, age, signature accessories and canonical outfit.

This module owns the :class:`KeyframeCharacterBinding` data structure plus
helpers to derive one from a :class:`CharacterIdentityContract` resolved
by :mod:`identity_master_resolver` and a moment's per-character state
(emotion / outfit / pose / action / injury / held_item) coming from the
keyframe moment selector.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.services.character_identity_contract import CharacterIdentityContract


VALID_POSITIONS = ("left", "center", "right", "background")
VALID_FRAME_ROLES = ("protagonist", "supporting", "background_extra")


@dataclass(frozen=True)
class KeyframeCharacterBinding:
    """Everything the keyframe pipeline needs to know about one character.

    Reference fields (``reference_asset_id`` / ``reference_image_url`` /
    ``reference_image_sha256``) come from the resolved identity master
    portrait and are mandatory before a keyframe can be generated. The
    ``requested_*`` fields encode the *dramatic state* of this character
    in this specific moment — they are the only aspects the prompt
    rewriter is allowed to vary.
    """

    # ---- identity (from CharacterIdentityContract) ----
    character_id: str
    character_name: str
    visual_fingerprint: str
    identity_contract_version: str
    identity_prompt: str
    gender_prompt: str
    identity_variant_id: str = ""
    age_group: str = "young_adult"
    age_contract: dict[str, Any] = field(default_factory=dict)

    # ---- moment-specific state ----
    requested_emotion: str = "neutral"
    requested_outfit: str = "default"
    requested_pose: str = "standing"
    requested_action: str = ""
    injury_state: str = "none"
    held_item: str = ""

    # ---- frame placement ----
    frame_role: str = "supporting"
    expected_position: str = "center"

    # ---- identity master reference (mandatory) ----
    reference_asset_id: int = 0
    reference_image_url: str = ""
    reference_image_sha256: str = ""

    # ---- extras carried verbatim into the rewriter payload ----
    visual_profile: dict[str, Any] = field(default_factory=dict)
    identity_anchor: str = ""
    canonical_outfit: dict[str, Any] = field(default_factory=dict)
    signature_features: tuple[str, ...] = ()
    accessories: tuple[str, ...] = ()
    canonical_identity: dict[str, Any] = field(default_factory=dict)
    canonical_identity_fingerprint: str = ""
    asymmetric_traits: tuple[str, ...] = ()

    def position_is_valid(self) -> bool:
        return (self.expected_position or "").strip().lower() in VALID_POSITIONS

    def role_is_valid(self) -> bool:
        return (self.frame_role or "").strip().lower() in VALID_FRAME_ROLES

    def has_reference(self) -> bool:
        return (
            bool(self.reference_asset_id)
            and bool(self.reference_image_url)
            and bool(self.reference_image_sha256)
        )

    def to_rewriter_payload(self, *, reference_image_index: int = 1) -> dict[str, Any]:
        """Blueprint §5 — turn the binding into the rewriter's input payload.

        ``reference_image_index`` is 1-based and matches the
        ``Image N is <name>`` blocks emitted by the prompt builder.
        """
        return {
            "character_id": self.character_id,
            "name": self.character_name,
            "visual_profile": dict(self.visual_profile) if self.visual_profile else {},
            "visual_fingerprint": self.visual_fingerprint,
            "canonical_identity": dict(self.canonical_identity) if self.canonical_identity else {},
            "canonical_identity_fingerprint": self.canonical_identity_fingerprint,
            "identity_anchor": self.identity_anchor,
            "gender_prompt": self.gender_prompt,
            "identity_contract_version": self.identity_contract_version,
            "identity_variant_id": self.identity_variant_id,
            "age_group": self.age_group,
            "age_contract": dict(self.age_contract) if self.age_contract else {},
            "signature_features": list(self.signature_features),
            "canonical_outfit": dict(self.canonical_outfit) if self.canonical_outfit else {},
            "accessories": list(self.accessories),
            "asymmetric_traits": list(self.asymmetric_traits),
            "requested_emotion": self.requested_emotion,
            "requested_outfit": self.requested_outfit,
            "requested_pose": self.requested_pose,
            "requested_action": self.requested_action,
            "injury_state": self.injury_state,
            "held_item": self.held_item,
            "frame_role": self.frame_role,
            "expected_position": self.expected_position,
            "reference_asset_id": self.reference_asset_id,
            "reference_image_url": self.reference_image_url,
            "reference_image_sha256": self.reference_image_sha256,
            "reference_image_index": reference_image_index,
        }


def binding_from_contract(
    contract: CharacterIdentityContract,
    *,
    requested_emotion: str = "neutral",
    requested_outfit: str = "default",
    requested_pose: str = "standing",
    requested_action: str = "",
    injury_state: str = "none",
    held_item: str = "",
    frame_role: str = "supporting",
    expected_position: str = "center",
) -> KeyframeCharacterBinding:
    """Derive a :class:`KeyframeCharacterBinding` from a resolved contract.

    The contract must already have its master portrait reference populated
    by :func:`identity_master_resolver.generate_or_resolve_identity_master_portraits`;
    otherwise the resulting binding will fail :meth:`has_reference`.
    """
    return KeyframeCharacterBinding(
        character_id=contract.character_id,
        character_name=contract.character_name,
        visual_fingerprint=contract.visual_fingerprint,
        identity_contract_version=contract.version,
        identity_prompt=contract.visual_prompt_en,
        gender_prompt=contract.gender_prompt,
        identity_variant_id=contract.identity_variant_id,
        age_group=contract.age_group,
        age_contract=dict(contract.age_contract) if contract.age_contract else {},
        requested_emotion=requested_emotion,
        requested_outfit=requested_outfit,
        requested_pose=requested_pose,
        requested_action=requested_action,
        injury_state=injury_state,
        held_item=held_item,
        frame_role=frame_role,
        expected_position=expected_position,
        reference_asset_id=contract.master_portrait_asset_id or 0,
        reference_image_url=contract.reference_image_url or "",
        reference_image_sha256=contract.reference_image_sha256 or "",
        visual_profile=dict(contract.visual_profile) if contract.visual_profile else {},
        identity_anchor=contract.identity_anchor,
        canonical_outfit=dict(contract.canonical_outfit) if contract.canonical_outfit else {},
        signature_features=tuple(contract.signature_features),
        accessories=tuple(contract.accessories),
        canonical_identity=(
            dict(contract.canonical_identity) if contract.canonical_identity else {}
        ),
        canonical_identity_fingerprint=contract.canonical_identity_fingerprint,
        asymmetric_traits=tuple(contract.asymmetric_traits),
    )


def pick_primary_bindings(
    bindings: Iterable[KeyframeCharacterBinding],
    *,
    max_count: int = 3,
) -> list[KeyframeCharacterBinding]:
    """Blueprint §7 — cap a keyframe to at most three primary characters.

    Sort priority:
    1. ``frame_role``: ``protagonist`` > ``supporting`` > ``background_extra``
    2. ``reference_asset_id`` (stable tiebreak)
    """
    role_weight = {"protagonist": 0, "supporting": 1, "background_extra": 2}

    def sort_key(b: KeyframeCharacterBinding) -> tuple[int, int, str]:
        return (
            role_weight.get((b.frame_role or "").strip().lower(), 3),
            -(b.reference_asset_id or 0),
            b.character_id,
        )

    return sorted(bindings, key=sort_key)[:max_count]


def keyframe_identity_seed(
    *,
    project_id: int,
    chapter_index: int,
    event_name: str,
    style_fingerprint: str,
    character_visual_fingerprints: list[str],
) -> int:
    """Blueprint §9 — deterministic seed for a keyframe identity attempt.

    Includes ``project_id / chapter_index / event_name / style_fingerprint``
    and the per-character visual fingerprints *sorted by character_id* so
    two keyframes that share the same identity inputs always reuse the
    same seed (and therefore the same noise trajectory), while any
    identity change forces a new seed.
    """
    sorted_fingerprints = sorted(
        fp for fp in (character_visual_fingerprints or []) if fp
    )
    payload = json.dumps(
        {
            "p": int(project_id),
            "c": int(chapter_index),
            "e": (event_name or "").strip(),
            "s": (style_fingerprint or "").strip(),
            "v": sorted_fingerprints,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    # 30-bit unsigned int — fits any image-model seed config we've seen.
    return int(digest[:8], 16)


__all__ = [
    "VALID_POSITIONS",
    "VALID_FRAME_ROLES",
    "KeyframeCharacterBinding",
    "binding_from_contract",
    "pick_primary_bindings",
    "keyframe_identity_seed",
]
