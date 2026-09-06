"""Character identity contract (Stage_Keyframe_Identity_AR_Blueprint Section A).

A frozen, deterministic data structure that captures every field downstream
keyframe generation needs to lock a character's identity. It carries both the
gallery-compatible ``visual_profile`` projection and, when available, the
separate ``canonical_identity`` used to reproduce a confirmed source
character.

Why this exists
---------------
The legacy keyframe pipeline only carried ``{name, appearance}`` for each
character into image generation, so the model freely redesigned faces,
hairstyles, ages, genders and outfits on every keyframe. The contract pins
those fields once per ``(project_id, character_id, visual_fingerprint)``
triple and is the single source of truth for ``KeyframeCharacterBinding``
and the identity master portrait resolver.

Contract version
----------------
``CHARACTER_IDENTITY_CONTRACT_VERSION`` defaults to ``character-identity-v2``
and is read from ``Settings.character_identity_contract_version`` so that
schema bumps can invalidate stale ``generation_params`` blobs in one place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CharacterIdentityContract:
    """Deterministic identity snapshot for one character in one project.

    ``master_portrait_asset_id`` / ``reference_image_url`` /
    ``reference_image_sha256`` are populated by the identity master resolver
    once a portrait has been selected or generated; the contract itself stays
    usable without them (the resolver simply refuses to hand out a keyframe
    binding until they are set).
    """

    version: str

    project_id: int
    character_id: str
    character_name: str

    # ---- generic gallery identity inputs ----
    gender: str
    visual_profile: dict[str, Any]
    visual_fingerprint: str
    visual_prompt_en: str
    identity_anchor: str
    gender_prompt: str

    # ---- identity scalar fields (extracted from visual_profile for fast access) ----
    face_shape: str
    skin_tone: str
    eye_color: str
    hair_color: str
    hair_length: str
    hair_style: str
    age_group: str
    body_build: str

    # ---- effective source identity, with visual_profile fallback ----
    canonical_outfit: dict[str, Any] = field(default_factory=dict)
    signature_features: tuple[str, ...] = ()
    accessories: tuple[str, ...] = ()
    canonical_identity: dict[str, Any] = field(default_factory=dict)
    canonical_identity_fingerprint: str = ""
    asymmetric_traits: tuple[str, ...] = ()

    # ---- age-stage identity variant (canonical character id remains stable) ----
    identity_variant_id: str = ""
    age_contract: dict[str, Any] = field(default_factory=dict)

    # ---- resolved by identity_master_resolver (None until master portrait exists) ----
    master_portrait_asset_id: int | None = None
    reference_image_url: str | None = None
    reference_image_sha256: str | None = None

    def with_master_reference(
        self,
        *,
        asset_id: int,
        reference_image_url: str,
        reference_image_sha256: str,
    ) -> "CharacterIdentityContract":
        """Return a copy with the resolved master portrait reference fields set.

        The contract is frozen, so the resolver hands back a new instance
        once it has selected or generated the master portrait.
        """
        return CharacterIdentityContract(
            version=self.version,
            project_id=self.project_id,
            character_id=self.character_id,
            character_name=self.character_name,
            gender=self.gender,
            visual_profile=self.visual_profile,
            visual_fingerprint=self.visual_fingerprint,
            visual_prompt_en=self.visual_prompt_en,
            identity_anchor=self.identity_anchor,
            gender_prompt=self.gender_prompt,
            face_shape=self.face_shape,
            skin_tone=self.skin_tone,
            eye_color=self.eye_color,
            hair_color=self.hair_color,
            hair_length=self.hair_length,
            hair_style=self.hair_style,
            age_group=self.age_group,
            body_build=self.body_build,
            canonical_outfit=self.canonical_outfit,
            signature_features=self.signature_features,
            accessories=self.accessories,
            canonical_identity=self.canonical_identity,
            canonical_identity_fingerprint=self.canonical_identity_fingerprint,
            asymmetric_traits=self.asymmetric_traits,
            identity_variant_id=self.identity_variant_id,
            age_contract=self.age_contract,
            master_portrait_asset_id=asset_id,
            reference_image_url=reference_image_url,
            reference_image_sha256=reference_image_sha256,
        )


def _safe_str(value: Any, *, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _safe_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def build_identity_contract(
    *,
    project_id: int,
    character_id: str,
    character_name: str,
    gender: str,
    visual_profile: dict[str, Any],
    visual_fingerprint: str,
    visual_prompt_en: str,
    identity_anchor: str,
    gender_prompt: str,
    version: str,
    age_contract: dict[str, Any] | None = None,
    canonical_identity: dict[str, Any] | None = None,
    canonical_identity_fingerprint: str = "",
) -> CharacterIdentityContract:
    """Deterministically assemble a :class:`CharacterIdentityContract`.

    Generic scalar fields (``face_shape`` / ``skin_tone`` / ``eye_color`` /
    ``hair_*`` / ``age_group`` / ``body_build`` / ``accessories``) are
    derived from ``visual_profile`` and never re-inferred. Confirmed source
    outfit, fixed features, and asymmetry come from ``canonical_identity`` and
    otherwise fall back to the generic profile. The caller is responsible for
    ensuring ``visual_profile`` was already passed through
    :func:`character_visual_profile_service.normalize_profile`, which the
    normalizers in :mod:`character_visual_profile_service` always do.
    """
    profile = visual_profile or {}

    face = profile.get("face") if isinstance(profile.get("face"), dict) else {}
    hair = profile.get("hair") if isinstance(profile.get("hair"), dict) else {}
    body = profile.get("body") if isinstance(profile.get("body"), dict) else {}
    outfit = profile.get("outfit") if isinstance(profile.get("outfit"), dict) else {}

    from app.services.canonical_identity_service import normalize_canonical_identity

    source_identity = normalize_canonical_identity(canonical_identity)
    source_outfit = _safe_str(source_identity.get("canonical_outfit"))
    source_features = _safe_tuple(source_identity.get("fixed_features"))
    source_asymmetry = _safe_tuple(source_identity.get("asymmetric_traits"))

    from app.services.character_age_contract import (
        age_contract_from_profile,
        build_age_contract,
        identity_variant_id,
    )

    resolved_age_contract = (
        dict(age_contract)
        if isinstance(age_contract, dict)
        else (
            age_contract_from_profile(profile).to_dict()
            if profile.get("age_group")
            else build_age_contract("adult", age_source="legacy_identity_default").to_dict()
        )
    )
    variant_id = identity_variant_id(character_id, resolved_age_contract.get("age_group"))

    return CharacterIdentityContract(
        version=_safe_str(version, default="character-identity-v2"),
        project_id=int(project_id),
        character_id=_safe_str(character_id),
        character_name=_safe_str(character_name),
        gender=_safe_str(gender, default="unknown"),
        visual_profile=dict(profile),
        visual_fingerprint=_safe_str(visual_fingerprint),
        visual_prompt_en=_safe_str(visual_prompt_en),
        identity_anchor=_safe_str(identity_anchor),
        gender_prompt=_safe_str(gender_prompt),
        face_shape=_safe_str(face.get("shape"), default="oval"),
        skin_tone=_safe_str(face.get("skin_tone"), default="medium"),
        eye_color=_safe_str(face.get("eye_color"), default="dark_brown"),
        hair_color=_safe_str(hair.get("color"), default="black"),
        hair_length=_safe_str(hair.get("length"), default="short"),
        hair_style=_safe_str(hair.get("style"), default="neat"),
        age_group=_safe_str(
            resolved_age_contract.get("age_group"),
            default=_safe_str(profile.get("age_group"), default="young_adult"),
        ),
        body_build=_safe_str(body.get("build"), default="average"),
        canonical_outfit=(
            {"description": source_outfit}
            if source_outfit
            else dict(outfit) if isinstance(outfit, dict) else {}
        ),
        signature_features=source_features or _safe_tuple(profile.get("signature_features")),
        accessories=_safe_tuple(profile.get("accessories")),
        canonical_identity=source_identity,
        canonical_identity_fingerprint=_safe_str(canonical_identity_fingerprint),
        asymmetric_traits=source_asymmetry,
        identity_variant_id=variant_id,
        age_contract=resolved_age_contract,
    )


__all__ = [
    "CharacterIdentityContract",
    "build_identity_contract",
]
