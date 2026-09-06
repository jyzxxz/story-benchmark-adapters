"""Identity master portrait resolver (Stage_Keyframe_Identity_AR_Blueprint Section A).

The keyframe identity pipeline cannot regenerate a character's face from
scratch each time — it must reuse a single, validated, master portrait as
the identity reference. This module owns that selection and generation.

Selection rules (blueprint §3, 7 constraints)
---------------------------------------------
1. ``character_id`` matches
2. ``visual_fingerprint`` matches
3. ``style_fingerprint`` matches
4. prefer ``emotion=neutral`` / ``outfit=default`` / ``pose=standing``
5. status must be ``completed`` and pass portrait validation
6. must not be stale / failed / quarantined
7. if none found, *generate* one before allowing keyframes to start

The resolver is intentionally split into a pure DB query
(:func:`resolve_identity_master_portrait`) and a side-effecting orchestrator
(:func:`generate_or_resolve_identity_master_portraits`) that triggers portrait
generation when no master exists, so the keyframe scheduler can call the
cheap path repeatedly without spawning generations.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import Asset
from app.services.character_identity_contract import (
    CharacterIdentityContract,
    build_identity_contract,
)
from app.services.canonical_identity_service import canonical_identity_prompt

logger = logging.getLogger(__name__)

IDENTITY_MASTER_PORTRAIT_EMOTION = "neutral"
IDENTITY_MASTER_PORTRAIT_OUTFIT = "default"
IDENTITY_MASTER_PORTRAIT_POSE = "standing"


@dataclass(frozen=True)
class IdentityMasterResolution:
    """Outcome of resolving one character's identity master portrait.

    ``asset`` is ``None`` when the master could not be resolved *and* no
    fallback generation was requested — callers must treat that as a hard
    block on keyframe generation (blueprint §8).
    """

    contract: CharacterIdentityContract
    asset: Optional[Asset]
    reason: str  # "hit" / "generated" / "missing" / "generation_failed" / "quality_failed"


def _asset_generation_params(asset: Asset) -> dict[str, Any]:
    params = asset.generation_params if isinstance(asset.generation_params, dict) else {}
    return dict(params)


def _is_stale_or_blocked(params: dict[str, Any]) -> bool:
    """Blueprint §3 rule 6 — reject stale / failed / quarantined portraits."""
    if params.get("is_quarantined"):
        return True
    if params.get("stale"):
        return True
    if params.get("identity_validation_failed"):
        return True
    return False


def _is_identity_master(params: dict[str, Any]) -> bool:
    """A portrait is a valid identity master only if it carries the marker.

    Legacy portraits produced before Stage_Keyframe_Identity_AR did not set
    ``is_identity_master``, so they are *excluded* — the resolver would
    rather generate a fresh master than anchor keyframes on an unverified
    portrait.
    """
    return bool(params.get("is_identity_master"))


def _contract_version_matches(params: dict[str, Any], expected_version: str) -> bool:
    actual = (params.get("identity_contract_version") or "").strip()
    if not actual:
        return False
    return actual == expected_version


def _portrait_presentation_contract_matches(params: dict[str, Any]) -> bool:
    """Reject masters created before the alpha and LLM-prompt contracts.

    A locally repaired alpha mask makes a legacy image usable as a PNG, but it
    does not prove that the original generation used the current pose contract.
    Such a master must be regenerated before it can anchor new keyframes.
    """
    from app.services.image_generation_service import (
        PORTRAIT_GENERATION_CONTRACT_VERSION,
    )
    from app.services.prompt_rewriter_service import (
        PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION,
    )

    alpha = params.get("portrait_alpha")
    return bool(
        params.get("portrait_generation_contract_version")
        == PORTRAIT_GENERATION_CONTRACT_VERSION
        and params.get("portrait_final_prompt_contract_version")
        == PORTRAIT_FINAL_PROMPT_CONTRACT_VERSION
        and params.get("prompt_source") == "llm_rewriter"
        and params.get("requested_shot") == "full_body"
        and isinstance(alpha, dict)
        and alpha.get("passed") is True
    )


def _fingerprint_match(actual: Optional[str], expected: Optional[str]) -> bool:
    if not expected:
        return False
    return (actual or "").strip() == expected.strip()


def _master_preference_score(asset: Asset, params: dict[str, Any]) -> int:
    """Higher is better. Used to pick among multiple masters for one character.

    Perfect match: emotion=neutral + outfit=default + pose=standing. We
    reward closeness to that canonical triple so a portrait uploaded as
    ``emotion=smile, outfit=default, pose=standing`` is still preferred over
    ``emotion=angry, outfit=battle, pose=jumping`` if no perfect match
    exists.
    """
    score = 0
    if (asset.emotion or "").strip().lower() == IDENTITY_MASTER_PORTRAIT_EMOTION:
        score += 4
    if (asset.outfit or "").strip().lower() in {"default", "", "canonical"}:
        score += 2
    if (asset.pose or "").strip().lower() == IDENTITY_MASTER_PORTRAIT_POSE:
        score += 2
    # Prefer more recent assets as tiebreak — higher id wins.
    return score + (asset.id or 0) / 1_000_000


async def resolve_identity_master_portrait(
    db: AsyncSession,
    *,
    project_id: int,
    character_id: str,
    visual_fingerprint: str,
    style_fingerprint: str,
    identity_variant_id: str = "",
    age_group: str = "",
    canonical_identity_fingerprint: str = "",
) -> Optional[Asset]:
    """Find an existing identity master portrait for one character.

    Pure DB query — never generates. Returns ``None`` if no portrait passes
    all 7 selection rules from blueprint §3.

    ``db`` may be either an ``AsyncSession`` (``await db.execute(...)``) or
    a synchronous ``Session`` (``db.query(...).all()``) — the project's
    ``asset_management_service`` runs on a sync session, while tests may
    inject an async one. The dispatcher below auto-detects.
    """
    expected_contract_version = (
        get_settings().character_identity_contract_version or "character-identity-v2"
    )

    candidates: Iterable[Asset]
    if _db_is_async(db):
        stmt = (
            select(Asset)
            .where(
                Asset.project_id == project_id,
                Asset.asset_type == "portrait",
                Asset.character_id == character_id,
                Asset.status == "completed",
            )
        )
        result = await db.execute(stmt)
        candidates = list(result.scalars())
    else:
        candidates = (
            db.query(Asset)
            .filter(
                Asset.project_id == project_id,
                Asset.asset_type == "portrait",
                Asset.character_id == character_id,
                Asset.status == "completed",
            )
            .all()
        )

    best: Optional[Asset] = None
    best_score: int = -1
    for asset in candidates:
        params = _asset_generation_params(asset)
        if _is_stale_or_blocked(params):
            continue
        if not _is_identity_master(params):
            continue
        if not _contract_version_matches(params, expected_contract_version):
            continue
        if not _portrait_presentation_contract_matches(params):
            continue
        if not _fingerprint_match(params.get("visual_fingerprint"), visual_fingerprint):
            continue
        if canonical_identity_fingerprint and not _fingerprint_match(
            params.get("canonical_identity_fingerprint"),
            canonical_identity_fingerprint,
        ):
            continue
        if not _fingerprint_match(params.get("style_fingerprint"), style_fingerprint):
            continue
        if identity_variant_id and params.get("identity_variant_id") != identity_variant_id:
            continue
        if age_group and params.get("age_group") != age_group:
            continue
        age_validation = params.get("age_validation")
        if age_group and (
            not isinstance(age_validation, dict)
            or not bool(age_validation.get("passed"))
        ):
            continue
        score = _master_preference_score(asset, params)
        if score > best_score:
            best = asset
            best_score = score
    return best


def _db_is_async(db: Any) -> bool:
    """Heuristic: AsyncSession exposes ``execute`` returning an awaitable;
    sync Session exposes ``query`` and ``execute`` returning a ChunkedIteratorResult.
    The cleanest discriminator is the class name or the absence of ``query``.
    """
    if db is None:
        return False
    if hasattr(db, "query") and not hasattr(db.__class__, "execute_aclose"):
        # Sync SQLAlchemy Session has .query() — AsyncSession does not.
        # AsyncSession.execute returns Coroutine; sync Session.execute returns
        # ChunkedIteratorResult. Cheapest robust check: presence of .query.
        return False
    return True


def _stamp_master_params(
    base_params: dict[str, Any],
    *,
    visual_fingerprint: str,
    style_fingerprint: str,
    reference_image_sha256: Optional[str],
    identity_variant_id: str = "",
    age_group: str = "",
    age_contract: Optional[dict[str, Any]] = None,
    age_validation: Optional[dict[str, Any]] = None,
    identity_validation: Optional[dict[str, Any]] = None,
    canonical_identity_fingerprint: str = "",
) -> dict[str, Any]:
    """Blueprint §3 — write identity-master schema fields into generation_params."""
    out = dict(base_params)
    out.update(
        {
            "identity_contract_version": (
                get_settings().character_identity_contract_version
                or "character-identity-v2"
            ),
            "is_identity_master": True,
            "visual_fingerprint": visual_fingerprint,
            "canonical_identity_fingerprint": canonical_identity_fingerprint,
            "style_fingerprint": style_fingerprint,
            "reference_image_sha256": reference_image_sha256,
            "identity_validation": dict(identity_validation or {}),
            "identity_variant_id": identity_variant_id,
            "age_group": age_group,
            "age_contract": dict(age_contract or {}),
            "age_validation": dict(age_validation or {}),
        }
    )
    return out


async def _build_contract_for_character(
    db: AsyncSession,
    *,
    project_id: int,
    character: dict[str, Any],
    style_fingerprint: str,
    identity_anchor: str,
) -> CharacterIdentityContract:
    """Assemble a :class:`CharacterIdentityContract` from a story-bible char dict.

    ``character`` is expected to already be normalized by
    :mod:`character_visual_profile_service.normalize_character`, so
    ``visual_profile`` / ``visual_fingerprint`` / ``visual_prompt_en`` are
    guaranteed present. ``gender_prompt`` is derived locally because the
    portrait service lives in :mod:`image_generation_service` and we want
    to keep this module import-light.
    """
    from app.services.image_generation_service import ImageGenerationService

    name = str(character.get("name") or "").strip()
    character_id = str(character.get("character_id") or "").strip()
    # normalize_story_bible does not stamp character_id onto each character
    # dict, so a missing/empty value here would otherwise produce a master
    # portrait whose character_id is "" — which the keyframe binding builder
    # (which uses prompt_builder_service.generate_character_id = md5(name|pid)
    # [:12]) could never look up. Fall back to the same algorithm so both
    # sides agree.
    if not character_id and name:
        import hashlib as _hashlib

        character_id = _hashlib.md5(f"{name}|{project_id}".encode("utf-8")).hexdigest()[:12]
    visual_profile = character.get("visual_profile") or {}
    visual_fingerprint = str(character.get("visual_fingerprint") or "").strip()
    visual_prompt_en = canonical_identity_prompt(character) or str(
        character.get("visual_prompt_en") or ""
    ).strip()
    gender = str(character.get("gender") or "").strip()

    # gender_prompt is derived deterministically by the same helper the
    # portrait pipeline already uses, so the master portrait and the
    # contract cannot drift.
    svc = ImageGenerationService()
    gender_prompt = svc._build_portrait_gender_anchor(gender)  # noqa: SLF001

    return build_identity_contract(
        project_id=project_id,
        character_id=character_id,
        character_name=name,
        gender=gender,
        visual_profile=visual_profile,
        visual_fingerprint=visual_fingerprint,
        visual_prompt_en=visual_prompt_en,
        identity_anchor=identity_anchor,
        gender_prompt=gender_prompt,
        version=get_settings().character_identity_contract_version,
        age_contract=(
            dict(character.get("age_contract"))
            if isinstance(character.get("age_contract"), dict)
            else None
        ),
        canonical_identity=(
            dict(character.get("canonical_identity"))
            if isinstance(character.get("canonical_identity"), dict)
            else None
        ),
        canonical_identity_fingerprint=str(
            character.get("canonical_identity_fingerprint") or ""
        ).strip(),
    )


async def _generate_master_portrait(
    db: AsyncSession,
    *,
    contract: CharacterIdentityContract,
    appearance_prompt: str,
    genre: Optional[str],
    style_fingerprint: str,
    visual_style_prompt: Optional[str],
    style_contract: Any = None,
    generation_runner: Any = None,
) -> Optional[Asset]:
    """Call the portrait generation pipeline to produce a fresh master.

    ``generation_runner`` is an ``async callable(project_id, contract, ...)``
    that returns the new :class:`Asset`. Default behaviour delegates to
    :class:`ImageGenerationService.generate_portrait` and persists a new
    master Asset row with ``is_identity_master=True``. Tests can inject a
    stub to avoid touching the real image API.
    """
    if generation_runner is None:
        from app.services.image_generation_service import ImageGenerationService

        svc = ImageGenerationService()
        result = await svc.generate_portrait(
            character_id=contract.character_id,
            character_name=contract.character_name,
            appearance_prompt=appearance_prompt or contract.visual_prompt_en,
            emotion=IDENTITY_MASTER_PORTRAIT_EMOTION,
            outfit=IDENTITY_MASTER_PORTRAIT_OUTFIT,
            pose=IDENTITY_MASTER_PORTRAIT_POSE,
            genre=genre,
            seed=None,
            # Keep the provider source as a private identity reference while the
            # Asset itself remains a transparent VN presentation sprite.
            remove_bg=True,
            full_body=True,
            gender=contract.gender or None,
            gender_prompt=contract.gender_prompt or None,
            age_contract=contract.age_contract or None,
            visual_style_prompt=visual_style_prompt,
            style_contract=style_contract,
        )
        if not result or not result.get("success"):
            logger.warning(
                "[identity-master] portrait generation failed project_id=%d character_id=%s err=%s",
                contract.project_id,
                contract.character_id,
                (result or {}).get("error"),
            )
            return None
        image_url = result.get("presentation_url") or result.get("image_url")
        if not image_url:
            return None
        params = _stamp_master_params(
            dict(result.get("generation_params") or {}),
            visual_fingerprint=contract.visual_fingerprint,
            canonical_identity_fingerprint=contract.canonical_identity_fingerprint,
            style_fingerprint=style_fingerprint,
            reference_image_sha256=(
                result.get("source_image_sha256")
                or dict(result.get("generation_params") or {}).get("source_image_sha256")
            ),
            identity_variant_id=contract.identity_variant_id,
            age_group=contract.age_group,
            age_contract=contract.age_contract,
            age_validation=result.get("age_validation") or {},
            identity_validation=result.get("identity_validation") or {},
        )
        asset = Asset(
            project_id=contract.project_id,
            asset_type="portrait",
            target_name=contract.character_name,
            character_id=contract.character_id,
            emotion=IDENTITY_MASTER_PORTRAIT_EMOTION,
            outfit=IDENTITY_MASTER_PORTRAIT_OUTFIT,
            pose=IDENTITY_MASTER_PORTRAIT_POSE,
            prompt=result.get("prompt") or result.get("final_prompt") or appearance_prompt,
            image_url=image_url,
            status="completed",
            seed=result.get("seed"),
            generation_params=params,
            genre=genre,
        )
        db.add(asset)
        if _db_is_async(db):
            await db.flush()
        else:
            db.flush()
            db.commit()
        return asset

    # Test path — caller supplies the runner so the real image API is never hit.
    return await generation_runner(db=db, contract=contract, style_fingerprint=style_fingerprint)


async def generate_or_resolve_identity_master_portraits(
    db: AsyncSession,
    *,
    project_id: int,
    characters: list[dict[str, Any]],
    style_fingerprint: str,
    visual_style_prompt: Optional[str] = None,
    genre: Optional[str] = None,
    style_contract: Any = None,
    generation_runner: Any = None,
) -> dict[str, IdentityMasterResolution]:
    """Resolve (or generate) one identity master portrait per character.

    Returns a mapping ``character_id -> IdentityMasterResolution``. Callers
    (e.g. the keyframe scheduler) must check each entry's ``asset`` and
    refuse to schedule keyframes for any character whose asset is ``None``
    (blueprint §8 hard block).
    """
    from app.services.visual_style_profile_service import (
        VisualStyleProfileService,
    )

    anchor_service = VisualStyleProfileService()
    resolutions: dict[str, IdentityMasterResolution] = {}

    for char in characters:
        contract = await _build_contract_for_character(
            db,
            project_id=project_id,
            character=char,
            style_fingerprint=style_fingerprint,
            identity_anchor=anchor_service.character_visual_anchor(
                str(char.get("name") or ""),
                canonical_identity_prompt(char)
                or str(char.get("appearance") or char.get("visual_description_cn") or ""),
                str(char.get("gender") or ""),
            ),
        )

        existing = await resolve_identity_master_portrait(
            db,
            project_id=project_id,
            character_id=contract.character_id,
            visual_fingerprint=contract.visual_fingerprint,
            style_fingerprint=style_fingerprint,
            identity_variant_id=contract.identity_variant_id,
            age_group=contract.age_group,
            canonical_identity_fingerprint=contract.canonical_identity_fingerprint,
        )
        if existing is not None:
            existing_params = _asset_generation_params(existing)
            resolved = contract.with_master_reference(
                asset_id=existing.id,
                reference_image_url=(
                    existing_params.get("source_image_url")
                    or existing_params.get("identity_reference_url")
                    or existing.image_url
                ),
                reference_image_sha256=existing_params.get("reference_image_sha256"),
            )
            resolutions[contract.character_id] = IdentityMasterResolution(
                contract=resolved, asset=existing, reason="hit"
            )
            continue

        # Stage_Keyframe_Identity_AR_Blueprint §3 rule 7 — generate a fresh master.
        asset = await _generate_master_portrait(
            db,
            contract=contract,
            appearance_prompt=contract.visual_prompt_en,
            genre=genre,
            style_fingerprint=style_fingerprint,
            visual_style_prompt=visual_style_prompt,
            style_contract=style_contract,
            generation_runner=generation_runner,
        )
        if asset is None:
            resolutions[contract.character_id] = IdentityMasterResolution(
                contract=contract, asset=None, reason="generation_failed"
            )
            continue

        asset_params = _asset_generation_params(asset)
        reference_sha = asset_params.get("reference_image_sha256")
        resolved = contract.with_master_reference(
            asset_id=asset.id,
            reference_image_url=(
                asset_params.get("source_image_url")
                or asset_params.get("identity_reference_url")
                or asset.image_url
            ),
            reference_image_sha256=reference_sha,
        )
        resolutions[contract.character_id] = IdentityMasterResolution(
            contract=resolved, asset=asset, reason="generated"
        )

    return resolutions


__all__ = [
    "IDENTITY_MASTER_PORTRAIT_EMOTION",
    "IDENTITY_MASTER_PORTRAIT_OUTFIT",
    "IDENTITY_MASTER_PORTRAIT_POSE",
    "IdentityMasterResolution",
    "resolve_identity_master_portrait",
    "generate_or_resolve_identity_master_portraits",
]
