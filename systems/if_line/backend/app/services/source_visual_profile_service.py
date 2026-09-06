"""Source-work visual profiles for fan-created stories.

The project visual bible owns the shared rendering contract.  This module
adds the source-work layer: it detects an explicitly named source work and
supplies concrete art-direction and canonical character anchors via the LLM
canonical-character recognizer.  Unknown but explicitly named source works
still receive a generic source-fidelity instruction instead of silently
degrading to a genre default.

There is no hardcoded franchise registry — every IP (Genshin, Honkai, One
Piece, …) is treated identically and resolved by the LLM services:
``visual_style_llm_classifier_service`` for art direction and
``canonical_character_recognition_service`` for character identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple

# Lazy import — circular dependency guard. The recognition service imports
# api_key_pool / text_llm_config which are heavy; importing lazily inside
# character_appearance() would mask import errors. Importing at module load
# is safe here because canonical_character_recognition_service does NOT
# import this module.
from app.services.canonical_character_recognition_service import (
    CANONICAL_CHARACTER_RECOGNITION_ENABLED,
    canonical_character_recognition_service,
)
from app.services.canonical_identity_service import (
    build_canonical_identity,
    canonical_identity_fingerprint,
    canonical_identity_prompt,
    normalize_canonical_identity,
)
from app.services.character_visual_profile_service import normalize_character


@dataclass(frozen=True)
class SourceDetection:
    """Result of detecting a named source work.

    ``profile`` was removed when the hardcoded franchise registry was
    deleted; detection now only carries the raw ``source_work`` string and
    confidence so downstream code can still know *that* a source was named,
    even though art-direction / character identity is delegated to the LLM.
    """
    profile: None = None
    source_work: str = ""
    confidence: float = 0.0
    evidence: Tuple[str, ...] = ()
    explicit: bool = False


class SourceVisualProfileService:
    """Detect an explicitly named source work and anchor canonical identities.

    All franchise-specific decisions (art direction, character appearance)
    are delegated to the LLM services.  This service only wires the
    plumbing: detect → resolve per-character identity via
    ``canonical_character_recognition_service`` → persist via
    ``canonical_identity_service``.
    """

    def detect(self, *, explicit_source_work: str = "", corpus: str = "") -> SourceDetection:
        """Return a SourceDetection for an explicitly named source work.

        When the caller passes ``explicit_source_work`` (the value the author
        filled in the project form), it is echoed back at full confidence.
        The ``corpus`` argument is accepted for backwards compatibility but
        no longer used — there is no signal table to match against.
        """
        explicit = (explicit_source_work or "").strip()
        if explicit:
            return SourceDetection(
                source_work=explicit,
                confidence=1.0,
                evidence=(explicit,),
                explicit=True,
            )
        return SourceDetection()

    def character_appearance(
        self,
        source_profile_id: str,
        character_name: str,
        fallback_appearance: str,
        *,
        source_work: str = "",
        worldview: str = "",
        style_rules: str = "",
    ) -> str:
        """Return the appearance prompt for a character.

        ``source_profile_id`` is kept in the signature for backwards
        compatibility but is no longer consulted — there is no hardcoded
        profile registry.  The LLM canonical-character recognizer is the
        single source of canonical appearance.  It needs at least a
        character_name + (source_work OR worldview) to make a confident
        decision; when unavailable / says is_canonical=false the bare
        fallback_appearance is returned.
        """
        # The LLM canonical-character recognizer is the single path.  It
        # needs at least a character_name + (source_work OR worldview)
        # to make a confident decision.
        if (
            character_name
            and (source_work or worldview)
            and CANONICAL_CHARACTER_RECOGNITION_ENABLED
        ):
            try:
                anchor = canonical_character_recognition_service.recognize_sync(
                    character_name=character_name,
                    source_work=source_work,
                    worldview=worldview,
                    style_rules=style_rules,
                    fallback_appearance=fallback_appearance,
                )
            except Exception:
                # Failure isolation: never break the portrait pipeline.
                anchor = None
            if anchor is not None and anchor.is_canonical and anchor.prompt_en:
                return anchor.prompt_en

        return str(fallback_appearance or "").strip()

    def effective_character_appearance(
        self,
        character: Dict[str, Any],
        source_profile_id: str,
        fallback_appearance: str,
        *,
        source_work: str = "",
        worldview: str = "",
        style_rules: str = "",
    ) -> str:
        """Use a persisted source identity before any recognition fallback."""
        identity_prompt = canonical_identity_prompt(character)
        if identity_prompt:
            return identity_prompt
        fallback_appearance = self._prepend_story_era_hint(
            character, str(fallback_appearance or "")
        )
        return self.character_appearance(
            source_profile_id,
            str(character.get("name") or ""),
            fallback_appearance,
            source_work=source_work,
            worldview=worldview,
            style_rules=style_rules,
        )

    _ERA_HINT_LABELS = {
        "toddler": "幼儿期",
        "child": "儿童期",
        "teen": "少年期",
        "young_adult": "青年期",
        "adult": "成年期",
        "middle_aged": "中年期",
        "senior": "老年期",
    }

    @classmethod
    def _prepend_story_era_hint(
        cls, character: Dict[str, Any], fallback_appearance: str
    ) -> str:
        """Prepend the structured age_group as an era hint for the recognizer.

        The canonical-character recognizer only sees free-text fields; without
        this hint the story's age stage (e.g. a teen-era prequel) is buried in
        generic profile boilerplate and the LLM locks the most iconic — often
        adult — official look_variant.
        """
        profile = character.get("visual_profile")
        age_group = ""
        if isinstance(profile, dict):
            age_group = str(profile.get("age_group") or "").strip()
        if not age_group:
            return fallback_appearance
        label = cls._ERA_HINT_LABELS.get(age_group, age_group)
        hint = (
            f"[story-era hint] 本故事中该角色处于{label}（{age_group}）；"
            "请选择官方对应时期/年代造型（look_variant），不要默认最经典的成年版。"
        )
        return f"{hint}\n{fallback_appearance}" if fallback_appearance else hint

    @staticmethod
    def _attach_canonical_identity(
        character: Dict[str, Any],
        identity: Dict[str, Any],
    ) -> Dict[str, Any]:
        char = dict(character)
        normalized = normalize_canonical_identity(identity)
        if not normalized:
            return char
        char["canonical_identity"] = normalized
        char["canonical_identity_fingerprint"] = canonical_identity_fingerprint(normalized)
        char["source_identity_locked"] = True
        char["canonical_franchise"] = normalized["franchise_name"]
        char["canonical_name_official"] = normalized["canonical_name"]
        return char

    def apply_detected_character_identities(
        self,
        story_bible: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Detect an explicitly named source work, then persist per-character
        canonical identities via the LLM recognizer."""
        out = dict(story_bible or {})
        detection = self.detect(
            explicit_source_work=str(out.get("source_work") or ""),
        )
        # source_profile_id is always "" now; apply_character_overrides
        # routes every character through the LLM recognizer.
        return self.apply_character_overrides(out, "", detected=detection)

    def apply_character_overrides(
        self,
        story_bible: Dict[str, Any],
        source_profile_id: str,
        *,
        detected: "SourceDetection | None" = None,
    ) -> Dict[str, Any]:
        """Attach source identity without changing the library taxonomy contract.

        ``visual_profile`` and ``visual_fingerprint`` remain the generic
        library projection.  Confirmed official appearance data is stored
        separately in ``canonical_identity`` and receives its own fingerprint.

        ``source_profile_id`` is accepted for backwards compatibility but
        ignored — there is no hardcoded profile to look up.  Every character
        is routed through the LLM canonical-character recognizer; when the
        LLM says the character is canonical (``is_canonical=True``), its
        official-design prompt is persisted as a locked identity.
        """
        out = dict(story_bible or {})
        source_work = str(out.get("source_work") or "")
        worldview = str(out.get("worldview") or "")
        style_rules = str(out.get("style_rules") or "")
        characters = []
        for raw in out.get("characters") or []:
            char = {"name": raw} if isinstance(raw, str) else dict(raw) if isinstance(raw, dict) else None
            if char is None:
                continue
            existing_identity = normalize_canonical_identity(char.get("canonical_identity"))
            if existing_identity:
                # Recompute only the generic gallery projection. This also
                # migrates old llm-v1/source-v1 overloaded visual fingerprints.
                char = normalize_character(char, context_text=f"{worldview} {style_rules}")
                characters.append(self._attach_canonical_identity(char, existing_identity))
                continue

            # Route every character through the LLM canonical-character
            # recognizer.  Failure isolation: any LLM error → fall through
            # to plain OC handling (no identity lock attached).
            anchor = None
            if (
                char.get("name")
                and (source_work or worldview)
                and CANONICAL_CHARACTER_RECOGNITION_ENABLED
            ):
                try:
                    anchor = canonical_character_recognition_service.recognize_sync(
                        character_name=str(char.get("name") or ""),
                        source_work=source_work,
                        worldview=worldview,
                        style_rules=style_rules,
                        fallback_appearance=str(char.get("appearance") or ""),
                    )
                except Exception:
                    anchor = None

            if anchor is not None and anchor.is_canonical and anchor.prompt_en:
                char = normalize_character(char, context_text=f"{worldview} {style_rules}")
                identity = build_canonical_identity(
                    franchise_name=anchor.franchise_name,
                    canonical_name=anchor.canonical_name,
                    identity_prompt_en=anchor.prompt_en,
                    recognition_source="llm",
                    look_variant=anchor.look_variant,
                    fixed_features=anchor.fixed_features,
                    canonical_outfit=anchor.canonical_outfit,
                    asymmetric_traits=anchor.asymmetric_traits,
                    reference_image="",
                    rationale=anchor.rationale,
                )
                char = self._attach_canonical_identity(char, identity)

            characters.append(char)
        out["characters"] = characters
        return out


source_visual_profile_service = SourceVisualProfileService()


__all__ = [
    "SourceDetection",
    "SourceVisualProfileService",
    "source_visual_profile_service",
]
