"""Stage_Background_Entity_Exclusion §XVI — ForbiddenStoryEntity collector.

Verifies the collector covers humans + non-human species, merges across the
four sources, and round-trips through to_dict / entity_from_dict.

All tests are deterministic and never call any paid API.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.background_story_entity_collector import (
    ForbiddenStoryEntity,
    MECHANICAL_SPECIES,
    NONHUMAN_BIOLOGICAL_SPECIES,
    build_background_exclusion_signature,
    collect_forbidden_story_entities,
    entity_from_dict,
)


# ----------------------------- species coverage -----------------------------

def test_mechanical_species_set_includes_core_types():
    for s in ("robot", "android", "cyborg", "mecha", "service_robot", "droid"):
        assert s in MECHANICAL_SPECIES, f"{s} missing from MECHANICAL_SPECIES"


def test_nonhuman_biological_species_set_includes_core_types():
    for s in ("animal", "monster", "spirit", "ghost", "alien", "creature", "demon"):
        assert s in NONHUMAN_BIOLOGICAL_SPECIES, f"{s} missing from NONHUMAN_BIOLOGICAL_SPECIES"


# ----------------------------- species_label_cn -----------------------------

def test_species_label_cn_human():
    e = ForbiddenStoryEntity(
        character_id="cid1", canonical_name="林夜", species="human"
    )
    assert e.species_label_cn == "人类"


def test_species_label_cn_robot():
    e = ForbiddenStoryEntity(
        character_id="cid2", canonical_name="R-17", species="service_robot"
    )
    assert e.is_mechanical is True
    assert "机器人" in e.species_label_cn or "机械" in e.species_label_cn


def test_species_label_cn_monster():
    e = ForbiddenStoryEntity(
        character_id="cid3", canonical_name="黑影兽", species="monster"
    )
    assert e.is_nonhuman_biologial is True
    assert e.species_label_cn and isinstance(e.species_label_cn, str)


# ----------------------------- all_names / aliases -----------------------------

def test_all_names_merges_canonical_and_aliases():
    e = ForbiddenStoryEntity(
        character_id="cid",
        canonical_name="林夜",
        aliases=("Lin Ye", "夜哥"),
        species="human",
    )
    names = e.all_names
    assert "林夜" in names
    assert "Lin Ye" in names
    assert "夜哥" in names
    # canonical first
    assert names[0] == "林夜"


# ----------------------------- signature builder -----------------------------

def test_signature_for_mechanical_emphasizes_chassis_and_sensors():
    character = {
        "name": "R-17",
        "species": "service_robot",
        "mechanical_profile": {
            "body_material": "哑光黑合金外壳",
            "optical_sensor": "右眼红色单眼传感器",
            "serial_number": "R-17",
        },
    }
    sig = build_background_exclusion_signature(character, profile={})
    sig_text = "\n".join(sig)
    assert "哑光黑合金外壳" in sig_text
    assert "右眼红色单眼传感器" in sig_text


def test_signature_for_human_skips_default_average():
    character = {
        "name": "林夜",
        "species": "human",
    }
    profile = {
        "body": {"build": "average"},
        "hair": {"color": "black"},
        "outfit": {"style": "深灰色风衣", "primary_colors": ["深灰"]},
        "signature_features": ["左眉刀疤"],
    }
    sig = build_background_exclusion_signature(character, profile=profile)
    sig_text = "\n".join(sig)
    # 'average' build must be dropped, real signature must survive
    assert "深灰色风衣" in sig_text
    assert "左眉刀疤" in sig_text


def test_signature_for_nonhuman_biological_uses_body_features():
    character = {"name": "黑影兽", "species": "monster"}
    profile = {
        "body": {"build": "獸形，全身覆黑鳞"},
        "signature_features": ["额头蓝色独角"],
        "accessories": ["颈上挂着铜铃"],
    }
    sig = build_background_exclusion_signature(character, profile=profile)
    sig_text = "\n".join(sig)
    assert "黑鳞" in sig_text
    assert "蓝色独角" in sig_text


# ----------------------------- collector end-to-end -----------------------------

def test_collect_merges_bible_and_outline_and_content():
    story_bible = {
        "characters": [
            {
                "name": "林夜",
                "aliases": ["Lin Ye"],
                "species": "human",
                "visual_fingerprint": "fingerprint_lin",
                "visual_profile": {
                    "face": "棱角分明",
                    "hair": "黑色短发",
                    "outfit": "深灰色风衣",
                    "signature_features": ["左眉刀疤"],
                },
            },
            {
                "name": "R-17",
                "aliases": ["Robot-17"],
                "species": "service_robot",
                "mechanical_profile": {
                    "chassis_material": "哑光黑合金外壳",
                    "optical_sensor": "右眼红色单眼传感器",
                },
            },
        ]
    }
    outline = {
        "characters": [
            {"name": "苏晚晴", "species": "human"},
            {"name": "R-17"},  # duplicate — must merge, not duplicate
        ]
    }
    chapter_content = "R-17 在远处待命。"  # no new entities — substring sweep only backfills aliases
    entities = collect_forbidden_story_entities(
        story_bible=story_bible,
        chapter_outline=outline,
        chapter_content=chapter_content,
        scene_segments=[],
        project_id=42,
    )
    names = []
    for e in entities:
        names.extend(e.all_names)
    # Core cast
    assert "林夜" in names
    assert "Lin Ye" in names
    assert "苏晚晴" in names
    assert "R-17" in names
    # R-17 must appear only once (merged by character_id)
    r17 = [e for e in entities if "R-17" in e.all_names]
    assert len(r17) == 1
    assert r17[0].is_mechanical is True
    # Outline-provided names merge into the same character_id
    assert any("苏晚晴" in n for n in names)


def test_collect_assigns_stable_character_id_for_same_name():
    story_bible = {"characters": [{"name": "林夜", "species": "human"}]}
    e1 = collect_forbidden_story_entities(
        story_bible=story_bible, chapter_outline={}, chapter_content="",
        scene_segments=[], project_id=7,
    )
    e2 = collect_forbidden_story_entities(
        story_bible=story_bible, chapter_outline={}, chapter_content="",
        scene_segments=[], project_id=7,
    )
    assert e1[0].character_id == e2[0].character_id


def test_collect_respects_max_20_entities():
    many = [{"name": f"角色{i}", "species": "human"} for i in range(50)]
    entities = collect_forbidden_story_entities(
        story_bible={"characters": many},
        chapter_outline={}, chapter_content="",
        scene_segments=[], project_id=1,
    )
    assert len(entities) <= 20


# ----------------------------- round trip -----------------------------

def test_entity_to_dict_and_back_roundtrip():
    original = ForbiddenStoryEntity(
        character_id="cid",
        canonical_name="林夜",
        aliases=("Lin Ye",),
        species="human",
        appearance_signature=("左眉刀疤", "深灰色风衣"),
        role_terms=("主角",),
        visual_fingerprint="abc123",
    )
    payload = original.to_dict()
    restored = entity_from_dict(payload)
    assert restored.character_id == original.character_id
    assert restored.canonical_name == original.canonical_name
    assert "Lin Ye" in restored.aliases
    assert "左眉刀疤" in restored.appearance_signature
    assert "主角" in restored.role_terms
    assert restored.visual_fingerprint == "abc123"
