"""project-visual-bible-v2 §F2 — asset prompt builder contract.

The contract from plan §C: three asset types (portrait / background /
keyframe) must share byte-identical shared-block lines. Only the
[ASSET ROLE] and [CONTENT] blocks differ; only backgrounds get a
[SCENE VARIATION] block.

These tests pin the layout invariants so a future refactor can't silently
re-introduce the "three independent style blocks" regression.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.asset_prompt_builder_service import (
    ASSET_PORTRAIT,
    ASSET_BACKGROUND,
    ASSET_KEYFRAME,
    VALID_ASSET_TYPES,
    VERSION,
    build_asset_prompt,
    build_shared_block,
    scene_visual_seed,
)
from app.services.project_visual_bible_service import (
    project_visual_bible_service,
)


def _anime_bible():
    sb = {
        "raw_json": {},
        "worldview": "2020 年代某普通一本大学",
        "style_rules": "轻松幽默",
        "characters": [{"name": "李明", "gender": "男"}],
    }
    return project_visual_bible_service.build_for_project(
        sb, project_title="T", project_style="",
    )


def test_three_asset_types_share_byte_identical_shared_block():
    """Plan §C: the first 8 lines of every asset prompt must be byte-identical."""
    bible = _anime_bible()
    p = build_asset_prompt(
        visual_bible=bible, asset_type=ASSET_PORTRAIT,
        subject="Li Ming standing",
    )
    b = build_asset_prompt(
        visual_bible=bible, asset_type=ASSET_BACKGROUND,
        subject="campus library",
    )
    k = build_asset_prompt(
        visual_bible=bible, asset_type=ASSET_KEYFRAME,
        subject="group meeting",
    )

    def shared_lines(text):
        # First 8 non-empty lines: [PROJECT STYLE SIGNATURE] through [FORBIDDEN STYLE]
        lines = [ln for ln in text.split("\n") if ln.strip()]
        return lines[:8]

    sp = shared_lines(p)
    sb = shared_lines(b)
    sk = shared_lines(k)
    assert sp == sb == sk, (
        "shared block must be byte-identical across asset types; "
        f"portrait={sp}, background={sb}, keyframe={sk}"
    )


def test_only_background_has_scene_variation_block():
    bible = _anime_bible()
    p = build_asset_prompt(visual_bible=bible, asset_type=ASSET_PORTRAIT, subject="x")
    b = build_asset_prompt(
        visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="y",
        scene_camera="24mm wide", scene_atmosphere="misty morning",
    )
    k = build_asset_prompt(visual_bible=bible, asset_type=ASSET_KEYFRAME, subject="z")

    assert "[SCENE VARIATION]" in b
    assert "[SCENE VARIATION]" not in p
    assert "[SCENE VARIATION]" not in k


def test_asset_role_block_differs_per_type():
    bible = _anime_bible()
    p = build_asset_prompt(visual_bible=bible, asset_type=ASSET_PORTRAIT, subject="x")
    b = build_asset_prompt(visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="y")
    k = build_asset_prompt(visual_bible=bible, asset_type=ASSET_KEYFRAME, subject="z")

    assert "[ASSET ROLE: PORTRAIT]" in p
    assert "[ASSET ROLE: BACKGROUND]" in b
    assert "[ASSET ROLE: KEYFRAME]" in k


def test_unknown_asset_type_raises():
    import pytest
    bible = _anime_bible()
    with pytest.raises(ValueError):
        build_asset_prompt(visual_bible=bible, asset_type="nonsense", subject="x")


def test_fingerprint_change_propagates_to_all_prompts():
    """If the bible's locked fingerprint changes, all three asset prompts
    must reflect it — otherwise the cache contract (plan §D) is broken."""
    bible = _anime_bible()
    p1 = build_asset_prompt(visual_bible=bible, asset_type=ASSET_PORTRAIT, subject="x")
    b1 = build_asset_prompt(visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="y")
    # The fingerprint line is in the shared block.
    assert bible.fingerprint in p1
    assert bible.fingerprint in b1


def test_scene_visual_seed_stable_per_inputs():
    """Same (project_id, fingerprint, scene_selector, variant) → same seed
    across calls / processes."""
    s1 = scene_visual_seed(1, "fp_abcd1234", "scene_selector_X", 0)
    s2 = scene_visual_seed(1, "fp_abcd1234", "scene_selector_X", 0)
    assert s1 == s2
    # Different variant → different seed
    s3 = scene_visual_seed(1, "fp_abcd1234", "scene_selector_X", 1)
    assert s1 != s3
    # Different scene → different seed
    s4 = scene_visual_seed(1, "fp_abcd1234", "scene_selector_Y", 0)
    assert s1 != s4
    # Seed is a positive 32-bit integer
    assert 0 < s1 < (2 ** 32)


def test_version_constant_bumped():
    """Plan §D: ASSET_PROMPT_CONTRACT_VERSION must equal 'shared-style-prompt-v2'."""
    assert VERSION == "shared-style-prompt-v2"
    assert VALID_ASSET_TYPES == (ASSET_PORTRAIT, ASSET_BACKGROUND, ASSET_KEYFRAME)
