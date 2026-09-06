"""Deterministic asset-prompt assembly — shared style block injection.

Replaces the previous design where :class:`RewrittenPrompt.style` was a free-form
LLM output. Under the project-visual-bible contract:

* The LLM only rewrites ``subject / details / lighting / composition``.
* ``style`` is NEVER produced by the LLM. The backend deterministically
  injects the locked :class:`ProjectVisualBible` shared block before the
  asset-role block and the content block.

Three asset types share byte-identical shared blocks (lines ``[PROJECT STYLE
SIGNATURE]`` ... ``[FORBIDDEN STYLE]``). Only the ASSET ROLE block, the
CONTENT block, and — for backgrounds only — the SCENE VARIATION block differ.

This module is also the home of ``scene_visual_seed`` — a stable per-scene
seed derived from (project_id, fingerprint, scene_selector, variant).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple

from app.services.project_visual_bible_service import ProjectVisualBible


VERSION = "shared-style-prompt-v2"


ASSET_PORTRAIT = "portrait"
ASSET_BACKGROUND = "background"
ASSET_KEYFRAME = "keyframe"

VALID_ASSET_TYPES = (ASSET_PORTRAIT, ASSET_BACKGROUND, ASSET_KEYFRAME)


@dataclass(frozen=True)
class SceneVisualTreatment:
    """Per-scene variation that backgrounds may shift within a locked bible.

    Project-level ``art_style / medium / linework / shading / texture /
    base_palette`` are LOCKED in :class:`ProjectVisualBible` and cannot move.
    Only the four fields below may vary per scene. ``accent_palette`` MUST be a
    constrained derivative of ``bible.base_palette`` — never a brand-new palette.
    """

    camera: str = ""
    atmosphere: str = ""
    accent_palette: Tuple[str, ...] = field(default_factory=tuple)
    time_of_day: str = ""

    def to_dict(self) -> dict:
        return {
            "camera": self.camera,
            "atmosphere": self.atmosphere,
            "accent_palette": list(self.accent_palette),
            "time_of_day": self.time_of_day,
        }


def scene_visual_seed(
    project_id: int,
    style_fingerprint: str,
    scene_selector: str,
    variant: int = 0,
) -> int:
    """Stable 32-bit positive seed per (project, fingerprint, scene, variant).

    Same inputs ⇒ same seed across processes / rebuilds. ``variant`` lets
    retries or explicit "regenerate with different composition" requests
    produce a different seed deterministically.
    """
    payload = f"{project_id}|{style_fingerprint}|{scene_selector}|{variant}"
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def build_shared_block(bible: ProjectVisualBible) -> str:
    """The 8-line shared block that every asset prompt starts with.

    Byte-identical for all asset types within the same project. Test F2 asserts
    on this exact string.
    """
    palette = ", ".join(bible.base_palette)
    forbidden = ", ".join(bible.forbidden_styles)
    return (
        f"[PROJECT STYLE SIGNATURE] {bible.version} / fingerprint={bible.fingerprint}\n"
        f"[SHARED MEDIUM] {bible.medium}\n"
        f"[SHARED ART DIRECTION] {bible.art_direction}\n"
        f"[SHARED LINEWORK] {bible.linework}\n"
        f"[SHARED SHADING] {bible.shading}\n"
        f"[SHARED TEXTURE] {bible.texture}\n"
        f"[SHARED BASE PALETTE] {palette} ({bible.palette_policy})\n"
        f"[FORBIDDEN STYLE] {forbidden}"
    )


def build_asset_role_block(bible: ProjectVisualBible, asset_type: str) -> str:
    """Asset-specific role block — the only place where asset-type differences
    surface in the non-content portion of the prompt."""
    role_label = asset_type.upper()
    if asset_type == ASSET_PORTRAIT:
        body = bible.portrait_role
    elif asset_type == ASSET_BACKGROUND:
        body = bible.background_role
    elif asset_type == ASSET_KEYFRAME:
        body = bible.keyframe_role
    else:
        raise ValueError(f"unknown asset_type: {asset_type}")
    return f"[ASSET ROLE: {role_label}]\n{body}"


def build_content_block(
    subject: str,
    details: Iterable[str],
    lighting: str = "",
    composition: str = "",
) -> str:
    """Content block from the LLM rewriter — the only block where per-asset
    descriptive text is allowed to vary."""
    parts = [subject.strip()]
    details = [d.strip() for d in details if d and d.strip()]
    if details:
        parts.append("details: " + "; ".join(details))
    if lighting:
        parts.append("lighting: " + lighting.strip())
    if composition:
        parts.append("composition: " + composition.strip())
    return "[CONTENT] " + ", ".join(parts)


def build_scene_variation_block(
    camera: str = "",
    atmosphere: str = "",
    accent_palette: Optional[Iterable[str]] = None,
    time_of_day: str = "",
) -> str:
    """Background-only block — per-scene variation. Field values must stay
    within the project's locked palette_family; callers enforce that."""
    parts = []
    if camera:
        parts.append(f"camera={camera}")
    if atmosphere:
        parts.append(f"atmosphere={atmosphere}")
    if accent_palette:
        parts.append("accent_palette=" + "/".join(accent_palette))
    if time_of_day:
        parts.append(f"time_of_day={time_of_day}")
    if not parts:
        return ""
    return "[SCENE VARIATION] " + ", ".join(parts)


def build_asset_prompt(
    *,
    visual_bible: ProjectVisualBible,
    asset_type: str,
    subject: str,
    details: Optional[Iterable[str]] = None,
    lighting: str = "",
    composition: str = "",
    scene_camera: str = "",
    scene_atmosphere: str = "",
    scene_accent_palette: Optional[Iterable[str]] = None,
    scene_time_of_day: str = "",
) -> str:
    """Top-level deterministic assembler.

    Output layout::

        <shared block 8 lines>

        <asset role block>

        <content block>

        <scene variation block, backgrounds only>

    For ``asset_type == "background"`` the SCENE VARIATION block is always
    appended (even if empty, to mark the section). For portrait / keyframe it
    is omitted entirely.
    """
    if asset_type not in VALID_ASSET_TYPES:
        raise ValueError(f"unknown asset_type: {asset_type}")

    shared = build_shared_block(visual_bible)
    role = build_asset_role_block(visual_bible, asset_type)
    content = build_content_block(
        subject=subject,
        details=details or (),
        lighting=lighting,
        composition=composition,
    )

    sections = [shared, "", role, "", content]

    if asset_type == ASSET_BACKGROUND:
        scene_block = build_scene_variation_block(
            camera=scene_camera,
            atmosphere=scene_atmosphere,
            accent_palette=scene_accent_palette,
            time_of_day=scene_time_of_day,
        )
        sections.append("")
        sections.append(scene_block if scene_block else "[SCENE VARIATION] (default)")

    return "\n".join(sections)


__all__ = [
    "VERSION",
    "ASSET_PORTRAIT",
    "ASSET_BACKGROUND",
    "ASSET_KEYFRAME",
    "VALID_ASSET_TYPES",
    "SceneVisualTreatment",
    "scene_visual_seed",
    "build_shared_block",
    "build_asset_role_block",
    "build_content_block",
    "build_scene_variation_block",
    "build_asset_prompt",
]
