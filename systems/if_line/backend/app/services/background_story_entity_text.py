"""Story-entity exclusion text builder (Stage_Background_Entity_Exclusion §V-VII).

Single source of truth for the bilingual "no story characters in background"
text block. Both :mod:`background_prompt_assembler_service` and the three
enforce entry points in :mod:`image_generation_service` import these so the
exclusion language is identical everywhere.

The block is deliberately deterministic — no LLM rewrite — so sanitize retry
and safety-fallback paths can re-inject the same text without drift.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from app.services.background_story_entity_collector import ForbiddenStoryEntity


# Stage_Background_Entity_Exclusion — cache version stamp. Bumping this
# constant invalidates every cached background image generated under the
# previous exclusion regime, because the string is appended to the
# assembled prompt (and therefore changes the cache key).
#
# Bump rules:
# - species-coverage expansion (new entity type) → bump
# - validator threshold change (BG-V2 spec) → bump
# - prompt-block rewrite (header/tail text) → bump
# - alias/signature rendering change → bump
BACKGROUND_ENTITY_EXCLUSION_VERSION = "story-entity-exclusion-v3"


# Bilingual headers — pinned to spec §V (zh) and §V (en). Both are always
# emitted together because the image model's training corpus is unknown;
# emitting both maximizes the chance the rule survives translation layers.
STORY_ENTITY_EXCLUSION_HEADER_CN = (
    "【背景角色实体禁入规则——最高优先级】\n"
    "本图是纯环境背景图。不得出现本项目中的任何剧情角色或命名实体。\n"
    "无论该角色是人类、机器人、仿生人、机械生命、机甲、动物、怪物、灵体、\n"
    "外星生命、人工智能化身、全息角色、数字生命或其他非人类实体，\n"
    "均不得进入背景画面。不得出现主角、反派、配角、命名 NPC、具有人格的机器、\n"
    "角色宠物、角色载具或反复出现的剧情生物。\n"
    "禁入依据是「剧情角色身份」，而不是角色所属物种。\n"
    "场景需要时可以出现普通的无人格机械设备、工业装置或无人设施，\n"
    "但它们不得复制、影射或近似任何被禁入角色的外观特征。"
)

STORY_ENTITY_EXCLUSION_HEADER_EN = (
    "BACKGROUND ROLE EXCLUSION — HIGHEST PRIORITY\n"
    "This image is a pure environment background: no bodies, no corpses, no remains.\n"
    "Do not depict any story character or named entity from this project,\n"
    "regardless of whether the entity is human, robotic, mechanical,\n"
    "android, cyborg, animal, monster, spirit, alien, holographic,\n"
    "digital, supernatural, or otherwise non-human.\n"
    "No protagonist, antagonist, supporting character, named NPC,\n"
    "sentient machine, AI avatar, character vehicle, pet character,\n"
    "or recurring creature may appear.\n"
    "The exclusion is based on story identity, not biological species.\n"
    "Generic environmental machinery and non-sentient equipment may appear\n"
    "only when required by the location, but they must not resemble,\n"
    "duplicate, reference, or imply any forbidden story character."
)


def _entity_view(entity: Any) -> ForbiddenStoryEntity:
    """Accept either a dict (from BackgroundSceneSpec.forbidden_entities) or
    a :class:`ForbiddenStoryEntity`. Returns a normalized entity."""
    if isinstance(entity, ForbiddenStoryEntity):
        return entity
    if isinstance(entity, Mapping):
        from app.services.background_story_entity_collector import entity_from_dict

        return entity_from_dict(dict(entity))
    raise TypeError(f"Unsupported entity type: {type(entity).__name__}")


def _format_one_entity_cn(entity: ForbiddenStoryEntity, index: int) -> str:
    """Per-entity Chinese forbidden block."""
    names = entity.all_names
    name_line = names[0] if names else entity.canonical_name
    aliases = names[1:] if len(names) > 1 else ()
    lines = [f"{index}. {name_line}（{entity.species_label_cn}）"]
    if aliases:
        lines.append(f"   别名/英文名：{', '.join(aliases)}")
    if entity.role_terms:
        lines.append(f"   角色定位：{', '.join(entity.role_terms)}")
    if entity.appearance_signature:
        lines.append("   身份外观特征：")
        for sig in entity.appearance_signature:
            lines.append(f"     - {sig}")
    return "\n".join(lines)


def _format_one_entity_en(entity: ForbiddenStoryEntity, index: int) -> str:
    """Per-entity English forbidden block."""
    names = entity.all_names
    name_line = names[0] if names else entity.canonical_name
    aliases = names[1:] if len(names) > 1 else ()
    species_label = entity.species_label_en or "story character"
    lines = [f"{index}. {name_line} (type: {species_label})"]
    if aliases:
        lines.append(f"   aliases: {', '.join(aliases)}")
    if entity.role_terms:
        lines.append(f"   role tags: {', '.join(entity.role_terms)}")
    if entity.appearance_signature:
        lines.append("   identity features:")
        for sig in entity.appearance_signature:
            lines.append(f"     - {sig}")
    return "\n".join(lines)


FORMAT_STORY_ENTITY_EXCLUSION_TAIL_CN = (
    "禁止直接出现以上角色；也禁止通过剪影、倒影、全息投影、屏幕画面、海报、\n"
    "雕像、影子、照片、肖像、残骸、备用机体、复制体或高度相似的普通机器人\n"
    "或普通生物间接表现这些角色。"
)

FORMAT_STORY_ENTITY_EXCLUSION_TAIL_EN = (
    "Do not show these entities directly. Do not show silhouettes, reflections,\n"
    "holograms, screens, posters, statues, shadows, photos, portraits, damaged\n"
    "bodies, spare copies, mass-produced duplicates, or lookalike machines /\n"
    "creatures representing them."
)


def format_story_entity_exclusion(
    entities: Iterable[Any],
    *,
    max_entities: int = 12,
) -> str:
    """Render the bilingual forbidden-entity block.

    ``entities`` may be either ``list[ForbiddenStoryEntity]`` (live caller) or
    ``list[dict]`` (from ``BackgroundSceneSpec.forbidden_entities`` after a
    Pydantic round-trip).
    """
    materialized: list[ForbiddenStoryEntity] = []
    for raw in entities:
        try:
            materialized.append(_entity_view(raw))
        except (TypeError, ValueError):
            continue
    materialized = materialized[:max_entities]
    if not materialized:
        return ""
    blocks: list[str] = [
        "【本项目禁入剧情角色实体清单】",
    ]
    for i, entity in enumerate(materialized, start=1):
        blocks.append(_format_one_entity_cn(entity, i))
    blocks.append(FORMAT_STORY_ENTITY_EXCLUSION_TAIL_CN)

    blocks.append("FORBIDDEN STORY ENTITIES (do not depict):")
    for i, entity in enumerate(materialized, start=1):
        blocks.append(_format_one_entity_en(entity, i))
    blocks.append(FORMAT_STORY_ENTITY_EXCLUSION_TAIL_EN)
    return "\n\n".join(blocks).strip()


def render_full_exclusion_block(
    *,
    entities: Sequence[Any] | None,
    legacy_names: Sequence[str] | None = None,
) -> str:
    """Full block: bilingual headers + per-entity list (or legacy fallback).

    Used by the three ``_enforce_*`` entry points in image_generation_service
    so they don't have to assemble the text themselves.
    """
    parts = [
        STORY_ENTITY_EXCLUSION_HEADER_CN,
        STORY_ENTITY_EXCLUSION_HEADER_EN,
        f"[entity-exclusion-version: {BACKGROUND_ENTITY_EXCLUSION_VERSION}]",
    ]
    entity_block = format_story_entity_exclusion(entities or [])
    if entity_block:
        parts.append(entity_block)
    elif legacy_names:
        # Legacy fallback — keep the old behavior when no structured entities
        # were collected (e.g. legacy path or pre-migration data).
        clean = [str(n).strip() for n in legacy_names if str(n).strip()]
        if clean:
            parts.append(
                "禁用命名角色（永远禁止出现在背景图中）：\n"
                f"- {', '.join(clean)}"
            )
            parts.append(
                "Do not depict these named characters in the background:\n"
                f"- {', '.join(clean)}"
            )
    return "\n\n".join(parts).strip()


__all__ = [
    "BACKGROUND_ENTITY_EXCLUSION_VERSION",
    "STORY_ENTITY_EXCLUSION_HEADER_CN",
    "STORY_ENTITY_EXCLUSION_HEADER_EN",
    "FORMAT_STORY_ENTITY_EXCLUSION_TAIL_CN",
    "FORMAT_STORY_ENTITY_EXCLUSION_TAIL_EN",
    "format_story_entity_exclusion",
    "render_full_exclusion_block",
]
