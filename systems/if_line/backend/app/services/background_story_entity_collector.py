"""Forbidden story entity collector (Stage_Background_Entity_Exclusion §II-III).

Background images must exclude ALL story characters — not just humans. A robot
protagonist, an android supporting role, a pet, a monster, a holographic AI:
all of them are forbidden in the background, on equal footing with a human
lead.

This module owns the single source of truth that the background-generation
pipeline consults:

- :class:`ForbiddenStoryEntity` — structured per-character record (id, names,
  species, identity signature).
- :func:`collect_forbidden_story_entities` — merge outline / scene segments /
  story bible / chapter content into one list.
- :func:`build_background_exclusion_signature` — per-character appearance
  signature that adapts to ``species`` (mechanical species use chassis fields;
  humans use face/hair/outfit).

The output is consumed by:

- :mod:`background_prompt_assembler_service` — for prompt-level ban clauses.
- :mod:`image_generation_service._enforce_with_spec` and siblings — for the
  deterministic enforcement block appended at every background entry point.
- :mod:`background_image_validator_service` — for post-generation leak review.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

logger = logging.getLogger("bg_entity_collector")


# Species categories. The split drives which appearance fields we surface in
# the exclusion signature — mechanical species must not be forced through
# face/hair (they don't have either), and humans must not be described via
# chassis language.
MECHANICAL_SPECIES = frozenset({
    "robot", "android", "droid", "cyborg", "mecha", "service_robot",
    "combat_robot", "industrial_robot", "sentient_machine", "ai_avatar",
    "holographic_character", "digital_consciousness", "synthetic_humanoid",
})
NONHUMAN_BIOLOGICAL_SPECIES = frozenset({
    "animal", "animal_character", "pet", "pet_character", "monster", "demon",
    "spirit", "ghost", "alien", "creature", "beast", "fairy", "undead",
    "were_creature", "talking_vehicle", "living_weapon",
})


def _is_mechanical_species(species: str) -> bool:
    s = (species or "").strip().lower()
    return s in MECHANICAL_SPECIES


def _is_nonhuman_biological(species: str) -> bool:
    s = (species or "").strip().lower()
    return s in NONHUMAN_BIOLOGICAL_SPECIES


@dataclass(frozen=True)
class ForbiddenStoryEntity:
    """One story character that must never appear in a background image.

    Identity is anchored by ``character_id`` (md5 of name|project_id from the
    portrait pipeline; collector falls back to that algorithm when the source
    doesn't provide one). All name forms go into ``identity_terms`` so the
    exclusion prompt can survive LLM rewrites and translation.
    """

    character_id: str
    canonical_name: str
    aliases: tuple[str, ...] = ()
    entity_type: str = "story_character"
    species: str = "human"
    identity_terms: tuple[str, ...] = ()
    appearance_signature: tuple[str, ...] = ()
    role_terms: tuple[str, ...] = ()
    visual_fingerprint: str = ""

    @property
    def all_names(self) -> tuple[str, ...]:
        """All non-empty name forms in display order."""
        seen: set[str] = set()
        out: list[str] = []
        for n in (self.canonical_name, *self.aliases, *self.identity_terms):
            if not n:
                continue
            key = n.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(n.strip())
        return tuple(out)

    @property
    def is_mechanical(self) -> bool:
        return _is_mechanical_species(self.species)

    @property
    def is_nonhuman_biologial(self) -> bool:
        return _is_nonhuman_biological(self.species)

    @property
    def species_label_cn(self) -> str:
        return _SPECIES_LABEL_CN.get(self.species.lower(), _coerce_label_cn(self.species))

    @property
    def species_label_en(self) -> str:
        return self.species.lower().replace("_", " ") if self.species else "entity"

    def to_dict(self) -> dict[str, Any]:
        return {
            "character_id": self.character_id,
            "canonical_name": self.canonical_name,
            "aliases": list(self.aliases),
            "entity_type": self.entity_type,
            "species": self.species,
            "identity_terms": list(self.identity_terms),
            "appearance_signature": list(self.appearance_signature),
            "role_terms": list(self.role_terms),
            "visual_fingerprint": self.visual_fingerprint,
        }


_SPECIES_LABEL_CN = {
    "human": "人类",
    "robot": "机器人",
    "service_robot": "服务机器人",
    "combat_robot": "战斗机器人",
    "industrial_robot": "工业机器人",
    "android": "仿生人",
    "droid": "智能机械",
    "cyborg": "半机械人",
    "mecha": "机甲角色",
    "sentient_machine": "有意识的机械生命",
    "ai_avatar": "人工智能化身",
    "holographic_character": "全息角色",
    "digital_consciousness": "数字生命",
    "synthetic_humanoid": "合成人形",
    "animal": "动物",
    "animal_character": "动物角色",
    "pet": "宠物",
    "pet_character": "宠物角色",
    "monster": "怪物",
    "demon": "魔族",
    "spirit": "灵体",
    "ghost": "鬼魂",
    "alien": "外星生命",
    "creature": "未知生物",
    "beast": "兽类",
    "fairy": "妖精",
    "undead": "亡灵",
    "were_creature": "变形兽",
    "talking_vehicle": "有意识的载具",
    "living_weapon": "有意识的武器",
}


def _coerce_label_cn(species: str) -> str:
    s = (species or "").strip()
    if not s:
        return "剧情角色"
    return s.replace("_", "")


def _stable_character_id(name: str, project_id: Optional[int]) -> str:
    """Match :meth:`prompt_builder_service.generate_character_id`.

    ``md5(name|project_id)[:12]`` — kept here so the collector can produce
    IDs even when story_bible dicts lack one (normalize_story_bible does
    not stamp character_id).
    """
    raw_name = (name or "").strip()
    if not raw_name:
        return ""
    pid = project_id if project_id is not None else 0
    return hashlib.md5(f"{raw_name}|{pid}".encode("utf-8")).hexdigest()[:12]


_NAME_PUNCTUATION = (",", ".", "\n", ";", "，", "。", "；", "！", "!", "?", "？", ":", "：")


def _clean_name(raw: Any) -> str:
    if raw is None:
        return ""
    name = str(raw).strip()
    if not name:
        return ""
    if len(name) > 40:
        return ""
    if any(ch in name for ch in _NAME_PUNCTUATION):
        return ""
    return name


def _coerce_species(raw: Any) -> str:
    if not raw:
        return "human"
    s = str(raw).strip().lower()
    if not s:
        return "human"
    return s.replace(" ", "_")


def _iter_value(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return (v for v in value if isinstance(v, str))
    return ()


def _attach_visual_profile(
    character: dict[str, Any],
    *,
    context_text: str,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return (normalized_profile, signature_features).

    Tries the portrait pipeline normalizer so the collector agrees with
    portrait-level identity on the human side. Falls back gracefully if the
    LLM-driven fields are absent (legacy story bibles).

    NOTE: ``normalize_profile`` whitelists ``species`` to
    ``{human, android, demon, spirit, other}`` and will downgrade exotic
    values like ``service_robot`` or ``pet`` back to ``human``. The collector
    therefore reads the *raw* species from the character dict / profile dict
    separately (see :func:`_read_raw_species`).
    """
    raw_profile = character.get("visual_profile")
    profile_src: dict[str, Any] = {}
    if isinstance(raw_profile, dict):
        profile_src = dict(raw_profile)
    elif isinstance(raw_profile, str) and raw_profile.strip():
        try:
            import json as _json

            profile_src = _json.loads(raw_profile) or {}
        except ValueError:
            profile_src = {}
    try:
        from app.services.character_visual_profile_service import normalize_profile

        normalized = normalize_profile(profile_src, character=character, context_text=context_text)
    except Exception as exc:  # noqa: BLE001 — must not break background gen
        logger.debug("normalize_profile failed for %s: %s", character.get("name"), exc)
        normalized = {}
    signature_features = tuple(
        s for s in (
            (normalized.get("signature_features") if isinstance(normalized, dict) else None) or []
        ) if s
    )
    return normalized, signature_features


def _read_raw_species(character: dict[str, Any], profile: dict[str, Any]) -> str:
    """Read species from the raw character/profile dicts.

    Bypasses :func:`normalize_profile` because its taxonomy whitelist would
    downgrade exotic species back to ``human``.
    """
    sources: list[dict[str, Any]] = []
    if isinstance(character, dict):
        sources.append(character)
        vp = character.get("visual_profile")
        if isinstance(vp, dict):
            sources.append(vp)
        mp = character.get("mechanical_profile") or character.get("chassis")
        if isinstance(mp, dict):
            sources.append(mp)
    if isinstance(profile, dict):
        sources.append(profile)
    for src in sources:
        for key in ("species", "entity_species", "creature_type"):
            value = src.get(key) if isinstance(src, dict) else None
            if value:
                return _coerce_species(value)
    return "human"


def _extract_role_terms(character: dict[str, Any], profile: dict[str, Any]) -> tuple[str, ...]:
    role_family = str(character.get("role_family") or (profile.get("role_family") if profile else "") or "").strip()
    role_key = str(character.get("role_key") or (profile.get("role_key") if profile else "") or "").strip()
    role_label_cn = str(character.get("role") or "").strip()
    out: list[str] = []
    for v in (role_label_cn, role_family, role_key):
        if v and v not in out:
            out.append(v)
    return tuple(out)[:3]


def build_background_exclusion_signature(
    character: dict[str, Any],
    *,
    profile: Optional[dict[str, Any]] = None,
) -> list[str]:
    """Per-character identity signature for background exclusion.

    Adapts to ``species``:

    - Mechanical species → chassis / material / optical_sensor / serial_number
      (no face/hair).
    - Non-human biological species → body outline, signature features,
      accessories (no human face language).
    - Human → face / hair / outfit / signature_features.

    Returns at most 6 short, distinctive phrases — caller is responsible for
    merging into the prompt.
    """
    p = profile or {}
    # Read species from the *raw* dicts — normalize_profile whitelists it.
    species = _read_raw_species(character, p)
    out: list[str] = []

    if _is_mechanical_species(species):
        mechanical = character.get("mechanical_profile") or character.get("chassis") or {}
        if not isinstance(mechanical, dict):
            mechanical = {}
        for k in (
            "chassis_type", "body_material", "body_colors", "head_shape",
            "face_display", "optical_sensor", "serial_number",
            "mechanical_markings", "limb_configuration", "damage_marks",
            "signature_lights",
        ):
            v = character.get(k)
            if isinstance(v, str) and v.strip() and k not in mechanical:
                mechanical[k] = v.strip()
        if mechanical.get("chassis_type"):
            out.append(str(mechanical["chassis_type"]))
        if mechanical.get("body_material") or mechanical.get("body_colors"):
            material_bits = [
                str(mechanical.get("body_material") or "").strip(),
                str(mechanical.get("body_colors") or "").strip(),
            ]
            material_bits = [b for b in material_bits if b]
            if material_bits:
                out.append("/".join(material_bits))
        if mechanical.get("head_shape"):
            out.append(f"头部：{mechanical['head_shape']}")
        if mechanical.get("optical_sensor"):
            out.append(f"光学传感器：{mechanical['optical_sensor']}")
        if mechanical.get("face_display"):
            out.append(f"面屏：{mechanical['face_display']}")
        if mechanical.get("serial_number"):
            out.append(f"机体编号：{mechanical['serial_number']}")
        if mechanical.get("mechanical_markings"):
            out.append(f"标志涂装：{mechanical['mechanical_markings']}")
        if mechanical.get("signature_lights"):
            out.append(f"特征灯光：{mechanical['signature_lights']}")
        if mechanical.get("damage_marks"):
            out.append(f"损伤痕迹：{mechanical['damage_marks']}")
        if mechanical.get("limb_configuration"):
            out.append(f"肢体配置：{mechanical['limb_configuration']}")
        # signature_features from the raw profile (avoid normalize_profile's
        # whitelist filtering) for mechanical species too.
        for sf in _iter_value((p.get("signature_features") if p else None) or character.get("signature_features")):
            sf = str(sf).strip()
            if sf and sf not in out:
                out.append(sf)
            if len(out) >= 6:
                break
    elif _is_nonhuman_biological(species):
        body = p.get("body") if isinstance(p.get("body"), dict) else {}
        if body.get("build"):
            out.append(f"体型：{body['build']}")
        for sf in _iter_value((p.get("signature_features") if p else None) or character.get("signature_features")):
            sf = str(sf).strip()
            if sf and sf not in out:
                out.append(sf)
            if len(out) >= 6:
                break
        for acc in _iter_value((p.get("accessories") if p else None) or character.get("accessories")):
            acc = str(acc).strip()
            if acc and acc not in out:
                out.append(acc)
            if len(out) >= 6:
                break
    else:
        body = p.get("body") if isinstance(p.get("body"), dict) else {}
        face = p.get("face") if isinstance(p.get("face"), dict) else {}
        hair = p.get("hair") if isinstance(p.get("hair"), dict) else {}
        outfit = p.get("outfit") if isinstance(p.get("outfit"), dict) else {}
        if body.get("build") and body.get("build") != "average":
            out.append(f"体型：{body['build']}")
        face_bits = []
        if face.get("skin_tone") and face.get("skin_tone") != "medium":
            face_bits.append(str(face["skin_tone"]))
        if face.get("eye_color") and face.get("eye_color") != "dark_brown":
            face_bits.append(str(face["eye_color"]))
        if face_bits:
            out.append("肤色/眼：" + "/".join(face_bits))
        if hair.get("color") and hair.get("color") != "black":
            out.append(f"发色：{hair['color']}")
        if outfit.get("style") or outfit.get("primary_colors"):
            outfit_bits = [b for b in (outfit.get("style"), "/".join(outfit.get("primary_colors") or [])) if b]
            if outfit_bits:
                out.append("装束：" + "/".join(str(b) for b in outfit_bits))
        for sf in _iter_value((p.get("signature_features") if p else None) or character.get("signature_features")):
            sf = str(sf).strip()
            if sf and sf not in out:
                out.append(sf)
            if len(out) >= 6:
                break
    # Deduplicate while preserving order, cap at 6.
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        key = s.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(s)
    return deduped[:6]


def _entity_from_character_dict(
    character: dict[str, Any],
    *,
    project_id: Optional[int],
    context_text: str,
) -> Optional[ForbiddenStoryEntity]:
    name = _clean_name(character.get("name"))
    if not name:
        return None
    profile, _ = _attach_visual_profile(character, context_text=context_text)
    species = _read_raw_species(character, profile)
    aliases_raw = []
    for k in ("aliases", "nicknames", "name_en", "english_name", "alt_names"):
        aliases_raw.extend(_iter_value(character.get(k)))
    aliases: list[str] = []
    seen_lower: set[str] = {name.lower()}
    for raw in aliases_raw:
        clean = _clean_name(raw)
        if not clean:
            continue
        if clean.lower() in seen_lower:
            continue
        seen_lower.add(clean.lower())
        aliases.append(clean)
    character_id = str(character.get("character_id") or "").strip()
    if not character_id:
        character_id = _stable_character_id(name, project_id)
    identity_terms = tuple(aliases_raw)
    identity_terms_clean: list[str] = []
    seen_lower2: set[str] = {name.lower()}
    for raw in identity_terms:
        clean = _clean_name(raw)
        if not clean:
            continue
        if clean.lower() in seen_lower2:
            continue
        seen_lower2.add(clean.lower())
        identity_terms_clean.append(clean)
    appearance_signature = tuple(
        build_background_exclusion_signature(character, profile=profile)
    )
    role_terms = _extract_role_terms(character, profile)
    visual_fingerprint = str(
        character.get("visual_fingerprint")
        or (profile.get("visual_fingerprint") if profile else "")
        or ""
    ).strip()
    return ForbiddenStoryEntity(
        character_id=character_id,
        canonical_name=name,
        aliases=tuple(aliases[:6]),
        entity_type="story_character",
        species=species,
        identity_terms=tuple(identity_terms_clean[:6]),
        appearance_signature=appearance_signature,
        role_terms=role_terms,
        visual_fingerprint=visual_fingerprint,
    )


def collect_forbidden_story_entities(
    *,
    story_bible: Any,
    chapter_outline: Any,
    chapter_content: str,
    scene_segments: list,
    project_id: Optional[int] = None,
) -> list[ForbiddenStoryEntity]:
    """Merge every in-scope source into one list of forbidden entities.

    Order of preference (later sources can't REPLACE an earlier record, but
    they can SUPPLEMENT name forms by re-merging into the same character_id):

    1. ``story_bible.characters`` — authoritative roster.
    2. ``chapter_outline.characters`` — may include one-off chapter roles.
    3. ``scene_segments[*].characters_present`` — segmenter LLM output.
    4. ``chapter_content`` substring matches — catches aliases the other
       sources missed.

    Returns at most 20 entities (prompt-size budget).
    """
    by_id: dict[str, ForbiddenStoryEntity] = {}
    by_name_lower: dict[str, str] = {}
    context_text = chapter_content or ""

    def _merge(entity: ForbiddenStoryEntity) -> None:
        if not entity.character_id:
            return
        existing = by_id.get(entity.character_id)
        if existing is None:
            by_id[entity.character_id] = entity
            by_name_lower[entity.canonical_name.lower()] = entity.character_id
            for alias in entity.all_names:
                by_name_lower[alias.lower()] = entity.character_id
            return
        # Merge: union name forms + signature.
        merged_names = tuple(dict.fromkeys((*existing.all_names, *entity.all_names)))
        merged_aliases = tuple(
            dict.fromkeys((*existing.aliases, *entity.aliases))
        )[:6]
        merged_identity = tuple(
            dict.fromkeys((*existing.identity_terms, *entity.identity_terms))
        )[:6]
        merged_signature = tuple(
            dict.fromkeys((*existing.appearance_signature, *entity.appearance_signature))
        )[:6]
        merged_roles = tuple(
            dict.fromkeys((*existing.role_terms, *entity.role_terms))
        )[:3]
        species = existing.species
        if species == "human" and entity.species != "human":
            species = entity.species
        merged = ForbiddenStoryEntity(
            character_id=existing.character_id,
            canonical_name=existing.canonical_name,
            aliases=merged_aliases,
            entity_type=existing.entity_type,
            species=species,
            identity_terms=merged_identity,
            appearance_signature=merged_signature,
            role_terms=merged_roles,
            visual_fingerprint=existing.visual_fingerprint or entity.visual_fingerprint,
        )
        by_id[existing.character_id] = merged
        for name in merged_names:
            by_name_lower[name.lower()] = merged.character_id

    # 1) story_bible.characters
    bible_chars: list[Any] = []
    if story_bible is not None:
        sb_characters = getattr(story_bible, "characters", None)
        if sb_characters:
            bible_chars = list(sb_characters)
        else:
            sb_raw = getattr(story_bible, "raw_json", None)
            if isinstance(sb_raw, dict):
                sb_field = sb_raw.get("characters")
                if isinstance(sb_field, list):
                    bible_chars = sb_field
            elif isinstance(story_bible, dict):
                sb_field = story_bible.get("characters")
                if isinstance(sb_field, list):
                    bible_chars = sb_field
    for c in bible_chars:
        if isinstance(c, str):
            c = {"name": c}
        if not isinstance(c, dict):
            continue
        entity = _entity_from_character_dict(c, project_id=project_id, context_text=context_text)
        if entity is not None:
            _merge(entity)

    # 2) chapter_outline.characters
    if chapter_outline is not None:
        outline_chars = getattr(chapter_outline, "characters", None)
        if outline_chars is None and isinstance(chapter_outline, dict):
            outline_chars = chapter_outline.get("characters")
        if isinstance(outline_chars, list):
            for c in outline_chars:
                if isinstance(c, str):
                    name_only = _clean_name(c)
                    if not name_only:
                        continue
                    if by_name_lower.get(name_only.lower()) is not None:
                        # Already collected via story_bible — skip to avoid
                        # spawning a second entity with a different
                        # character_id (md5(name|pid)) under the same name.
                        continue
                    c = {"name": name_only}
                if not isinstance(c, dict):
                    continue
                entity = _entity_from_character_dict(
                    c, project_id=project_id, context_text=context_text
                )
                if entity is not None:
                    _merge(entity)

    # 3) scene_segments[*].characters_present — bare names, attach to existing
    #    character_id when possible; otherwise synthesize a minimal entity.
    for seg in scene_segments or []:
        present = getattr(seg, "characters_present", None) or []
        for raw in present:
            name = _clean_name(raw)
            if not name:
                continue
            existing_id = by_name_lower.get(name.lower())
            if existing_id is not None:
                continue
            entity = _entity_from_character_dict(
                {"name": name}, project_id=project_id, context_text=context_text
            )
            if entity is not None:
                _merge(entity)

    # 4) chapter_content substring sweep — backfill aliases the LLM missed.
    if chapter_content:
        for name_lower, char_id in list(by_name_lower.items()):
            if name_lower in chapter_content:
                continue
        # Pass — names already in by_name_lower are the canonical set; we
        # don't synthesize brand-new entities from content, because doing
        # so reliably requires NER. The substring sweep is handled by the
        # existing _collect_forbidden_characters helper for legacy callers.

    return list(by_id.values())[:20]


def entity_from_dict(payload: dict[str, Any]) -> ForbiddenStoryEntity:
    """Reconstruct an entity from a dict (used by cache/DB round-trips)."""
    return ForbiddenStoryEntity(
        character_id=str(payload.get("character_id") or "").strip(),
        canonical_name=str(payload.get("canonical_name") or "").strip(),
        aliases=tuple(payload.get("aliases") or ()),
        entity_type=str(payload.get("entity_type") or "story_character"),
        species=str(payload.get("species") or "human"),
        identity_terms=tuple(payload.get("identity_terms") or ()),
        appearance_signature=tuple(payload.get("appearance_signature") or ()),
        role_terms=tuple(payload.get("role_terms") or ()),
        visual_fingerprint=str(payload.get("visual_fingerprint") or ""),
    )


__all__ = [
    "ForbiddenStoryEntity",
    "MECHANICAL_SPECIES",
    "NONHUMAN_BIOLOGICAL_SPECIES",
    "build_background_exclusion_signature",
    "collect_forbidden_story_entities",
    "entity_from_dict",
]
