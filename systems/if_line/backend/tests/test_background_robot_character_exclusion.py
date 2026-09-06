"""Stage_Background_Entity_Exclusion §XVI — robot character exclusion.

Verifies that a story-side robot/service-android is treated as a forbidden
entity and reaches the assembled background prompt through every enforce
entry point. All assertions are local; no image-generation API call is made.
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.background_prompt_assembler_service import BackgroundPromptAssembler
from app.services.background_story_entity_collector import ForbiddenStoryEntity
from app.services.background_story_entity_text import (
    BACKGROUND_ENTITY_EXCLUSION_VERSION,
    render_full_exclusion_block,
)
from app.services.image_generation_service import ImageGenerationService


def _robot_entity() -> ForbiddenStoryEntity:
    return ForbiddenStoryEntity(
        character_id="r17cid000000",
        canonical_name="R-17",
        aliases=("Robot-17", "Arcee"),
        species="service_robot",
        appearance_signature=(
            "哑光黑合金外壳",
            "右眼红色单眼传感器",
            "机体编号：R-17",
        ),
        role_terms=("反派机体", "刺客"),
        visual_fingerprint="r17fp",
    )


def _make_spec(forbidden_entities=None) -> BackgroundSceneSpec:
    env = "废弃机械工坊，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="机械工坊",
        scene_selector="test_robot_exclusion_001",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷光",
        atmosphere="肃杀",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_entities=[e.to_dict() for e in (forbidden_entities or [])],
    )


# ----------------------------- assembler -----------------------------

def test_assembler_includes_robot_entity_block():
    spec = _make_spec([_robot_entity()])
    out = BackgroundPromptAssembler().assemble(spec)
    assert "R-17" in out
    assert "Robot-17" in out
    # Header (zh + en) always emitted
    assert "背景角色实体禁入规则" in out
    assert "BACKGROUND ROLE EXCLUSION" in out
    # Mechanical identity features must be present
    assert "哑光黑合金外壳" in out
    assert "右眼红色单眼传感器" in out


def test_assembler_emits_version_stamp_even_with_no_entities():
    spec = _make_spec([])
    out = BackgroundPromptAssembler().assemble(spec)
    assert f"entity-exclusion-version: {BACKGROUND_ENTITY_EXCLUSION_VERSION}" in out


# ----------------------------- render_full_exclusion_block -----------------------------

def test_render_full_block_for_robot():
    block = render_full_exclusion_block(entities=[_robot_entity()])
    assert "R-17" in block
    assert "Robot-17" in block
    assert "service_robot" in block or "robot" in block.lower()
    # Indirect-representation ban
    assert "剪影" in block or "silhouette" in block.lower()


def test_render_full_block_legacy_fallback_when_no_entities():
    block = render_full_exclusion_block(entities=None, legacy_names=["林夜", "Lin Ye"])
    assert "林夜" in block
    assert "Lin Ye" in block


# ----------------------------- enforce entry points -----------------------------

@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


def test_enforce_environment_focus_includes_robot_block(svc):
    spec = _make_spec([_robot_entity()])
    out = svc._enforce_background_environment_focus("industrial workshop", spec=spec)
    assert "R-17" in out
    assert "哑光黑合金外壳" in out


def test_enforce_with_spec_includes_robot_block(svc):
    spec = _make_spec([_robot_entity()])
    out = svc._enforce_with_spec(spec, "industrial workshop", forbidden_characters=[])
    assert "R-17" in out
    assert "BACKGROUND ROLE EXCLUSION" in out


def test_enforce_no_human_subject_appends_robot_block(svc):
    spec = _make_spec([_robot_entity()])
    out = svc._enforce_background_no_human_subject(
        "industrial workshop",
        forbidden_entities=list(spec.forbidden_entities or []),
    )
    assert "R-17" in out
