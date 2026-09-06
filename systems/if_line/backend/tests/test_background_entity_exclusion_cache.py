"""Stage_Background_Entity_Exclusion §XVI — cache version + Asset stamping.

Verifies:

- BACKGROUND_ENTITY_EXCLUSION_VERSION constant is set to the v3 value
- The version tag appears in the assembled prompt so prompt-hash cache keys
  automatically roll over when the constant bumps
- The version tag is also embedded in render_full_exclusion_block
- Asset.generation_params stamping for forbidden_entity_ids /
  forbidden_entity_names / forbidden_entity_fingerprint (unit-tested via
  _persist_background_asset using a fake DB session)
"""
import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, GenerationResult, PeoplePolicy
from app.services.background_prompt_assembler_service import BackgroundPromptAssembler
from app.services.background_story_entity_collector import ForbiddenStoryEntity
from app.services.background_story_entity_text import (
    BACKGROUND_ENTITY_EXCLUSION_VERSION,
    render_full_exclusion_block,
)


def _spec(entities) -> BackgroundSceneSpec:
    env = "废弃站台，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="测试场景",
        scene_selector="test_cache_exclusion_01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷光",
        atmosphere="萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_entities=[e.to_dict() for e in entities],
    )


# ----------------------------- version constant -----------------------------

def test_version_constant_is_v3():
    assert BACKGROUND_ENTITY_EXCLUSION_VERSION == "story-entity-exclusion-v3"


def test_version_tag_in_assembled_prompt():
    """Cache key is md5(prompt); the version tag must be in the prompt so
    bumping the constant rolls every cached PNG."""
    out = BackgroundPromptAssembler().assemble(_spec([]))
    assert f"entity-exclusion-version: {BACKGROUND_ENTITY_EXCLUSION_VERSION}" in out


def test_version_tag_in_render_full_block():
    block = render_full_exclusion_block(entities=[])
    assert BACKGROUND_ENTITY_EXCLUSION_VERSION in block


def test_two_different_versions_produce_different_cache_keys():
    """Simulate cache-key derivation; different version strings → different md5."""
    p1 = f"prompt body v1\n[entity-exclusion-version: story-entity-exclusion-v1]"
    p2 = f"prompt body v1\n[entity-exclusion-version: {BACKGROUND_ENTITY_EXCLUSION_VERSION}]"
    h1 = hashlib.md5(p1.encode("utf-8")).hexdigest()
    h2 = hashlib.md5(p2.encode("utf-8")).hexdigest()
    assert h1 != h2


# ----------------------------- entity fingerprints change with edits -----------------------------

def _fp(entities):
    seed = sorted(
        f"{e.get('character_id', '')}|{e.get('species', '')}|"
        f"{','.join(sorted([str(a) for a in (e.get('aliases') or []) if a]))}"
        for e in entities
    )
    return hashlib.md5("\n".join(seed).encode("utf-8")).hexdigest() if seed else None


def test_fingerprint_changes_when_alias_added():
    e1 = ForbiddenStoryEntity(
        character_id="r17cid", canonical_name="R-17",
        aliases=("Robot-17",), species="service_robot",
    ).to_dict()
    e2 = dict(e1)
    e2["aliases"] = ["Robot-17", "Arcee"]
    assert _fp([e1]) != _fp([e2])


def test_fingerprint_changes_when_species_changes():
    e1 = ForbiddenStoryEntity(
        character_id="r17cid", canonical_name="R-17",
        species="service_robot",
    ).to_dict()
    e2 = dict(e1)
    e2["species"] = "android"
    assert _fp([e1]) != _fp([e2])


def test_fingerprint_stable_for_same_input():
    e1 = ForbiddenStoryEntity(
        character_id="r17cid", canonical_name="R-17",
        aliases=("Robot-17",), species="service_robot",
    ).to_dict()
    assert _fp([e1]) == _fp([e1])


# ----------------------------- Asset stamping via _persist_background_asset -----------------------------

def test_persist_background_asset_stamps_entity_metadata(monkeypatch):
    """Stub the DB session so we can read what _persist_background_asset writes."""
    from app.services.asset_management_service import AssetManagementService

    fake_db = MagicMock()
    fake_db.add = MagicMock()
    fake_db.commit = MagicMock()
    fake_db.refresh = MagicMock()
    svc = AssetManagementService(db=fake_db)

    captured = {}

    def _capture_add(asset):
        captured["asset"] = asset

    fake_db.add.side_effect = _capture_add

    spec = _spec([
        ForbiddenStoryEntity(
            character_id="r17cid", canonical_name="R-17",
            aliases=("Robot-17",), species="service_robot",
            appearance_signature=("红色右眼光感",),
        ),
    ])
    gen_result = GenerationResult(
        status="completed",
        image_path="/tmp/x.png",
        final_prompt="prompt body",
        scene_selector=spec.scene_selector,
    )
    asset = svc._persist_background_asset(
        project_id=1, chapter_index=1, spec=spec,
        generation_result=gen_result,
    )
    assert asset is not None
    params = captured["asset"].generation_params
    assert params["entity_exclusion_version"] == BACKGROUND_ENTITY_EXCLUSION_VERSION
    assert "r17cid" in params["forbidden_entity_ids"]
    assert "R-17" in params["forbidden_entity_names"]
    assert "Robot-17" in params["forbidden_entity_names"]
    assert params["forbidden_entity_fingerprint"]


def test_persist_returns_none_for_quarantined_result():
    """Stage_Background_Entity_Exclusion §XIV — quarantined backgrounds must
    not enter the Asset table (and therefore not the VN manifest)."""
    from app.services.asset_management_service import AssetManagementService

    fake_db = MagicMock()
    svc = AssetManagementService(db=fake_db)

    spec = _spec([])
    gen_result = GenerationResult(
        status="quarantined",
        image_path="/tmp/q.png",
        final_prompt="prompt",
        reason="forbidden_story_entity_leak",
        scene_selector=spec.scene_selector,
    )
    asset = svc._persist_background_asset_if_passed(
        project_id=1, chapter_index=1, spec=spec, generation_result=gen_result,
    )
    assert asset is None
    fake_db.add.assert_not_called()
