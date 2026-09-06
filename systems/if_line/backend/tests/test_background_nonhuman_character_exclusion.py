"""Stage_Background_Entity_Exclusion §XVI — non-human biological exclusion.

Animals, monsters, spirits, and holograms that are part of the story cast
must be treated as forbidden entities — not just humans and robots.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.background_prompt_assembler_service import BackgroundPromptAssembler
from app.services.background_story_entity_collector import ForbiddenStoryEntity
from app.services.background_story_entity_text import render_full_exclusion_block
from app.services.image_generation_service import ImageGenerationService


def _monster_entity() -> ForbiddenStoryEntity:
    return ForbiddenStoryEntity(
        character_id="monster001cid",
        canonical_name="黑影兽",
        aliases=("Shadow Beast",),
        species="monster",
        appearance_signature=("黑鳞", "额头蓝色独角"),
        role_terms=("剧情生物",),
    )


def _spirit_entity() -> ForbiddenStoryEntity:
    return ForbiddenStoryEntity(
        character_id="spirit0001cid",
        canonical_name="古井之灵",
        aliases=("Well Spirit",),
        species="spirit",
        appearance_signature=("半透明发光体", "水面倒影出现"),
    )


def _animal_entity() -> ForbiddenStoryEntity:
    return ForbiddenStoryEntity(
        character_id="pet0000001cid",
        canonical_name="乌云",
        aliases=("Wuyun", "黑犬"),
        species="animal",
        appearance_signature=("全身黑色长毛", "右耳缺口"),
        role_terms=("角色宠物",),
    )


def _hologram_entity() -> ForbiddenStoryEntity:
    return ForbiddenStoryEntity(
        character_id="hologram001cid",
        canonical_name="Aria",
        aliases=("AR-IA",),
        species="hologram",
        appearance_signature=("蓝色全息轮廓", "悬浮于终端上方"),
    )


def _make_spec(entities) -> BackgroundSceneSpec:
    env = "废弃的古井庭院，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="古井庭院",
        scene_selector="test_nonhuman_exclusion_01",
        scene_type="wilderness_dreamscape",
        environment_description=env,
        lighting="月色冷光",
        atmosphere="诡谲",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_entities=[e.to_dict() for e in entities],
    )


# ----------------------------- species labels -----------------------------

def test_monster_species_label_cn_present():
    assert "怪物" in _monster_entity().species_label_cn or "魔" in _monster_entity().species_label_cn


def test_spirit_is_nonhuman_biologial():
    assert _spirit_entity().is_nonhuman_biologial is True


def test_animal_is_nonhuman_biologial():
    assert _animal_entity().is_nonhuman_biologial is True


def test_hologram_is_mechanical_or_nonhuman():
    # hologram should not be classified as a plain human
    h = _hologram_entity()
    assert h.is_mechanical or h.is_nonhuman_biologial or h.species == "hologram"


# ----------------------------- assembler emits all species -----------------------------

def test_assembler_emits_monster_spirit_animal_hologram_blocks():
    spec = _make_spec([_monster_entity(), _spirit_entity(), _animal_entity(), _hologram_entity()])
    out = BackgroundPromptAssembler().assemble(spec)
    assert "黑影兽" in out
    assert "Shadow Beast" in out
    assert "古井之灵" in out
    assert "Well Spirit" in out
    assert "乌云" in out
    assert "Wuyun" in out
    assert "Aria" in out
    assert "AR-IA" in out
    # English header must still be present
    assert "FORBIDDEN STORY ENTITIES" in out


# ----------------------------- render_full_exclusion_block -----------------------------

def test_render_block_for_mixed_species_cast():
    block = render_full_exclusion_block(entities=[
        _monster_entity(), _spirit_entity(), _animal_entity(), _hologram_entity()
    ])
    assert "黑影兽" in block
    assert "古井之灵" in block
    assert "乌云" in block
    assert "Aria" in block
    # Indirect-representation ban is emitted for non-human species too
    assert "剪影" in block or "silhouette" in block.lower()


# ----------------------------- enforce entry points -----------------------------

@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


def test_enforce_environment_focus_with_mixed_species(svc):
    spec = _make_spec([_monster_entity(), _spirit_entity()])
    out = svc._enforce_background_environment_focus("abandoned courtyard", spec=spec)
    assert "黑影兽" in out
    assert "古井之灵" in out
