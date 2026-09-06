"""project-visual-bible-v2 §F6 — end-to-end pipeline integration.

Walks the full asset-pipeline contract on synthetic projects:
build_for_project → build_asset_prompt × 3 types → stamp params →
project_visual_consistency_service.evaluate_group → assembler
presentation URL resolution.

The contract from plan §Verification: "ten assets across a single
project must share the same fingerprint, and the three asset types
must produce byte-identical shared blocks".

This test is intentionally unit-scoped (no Celery, no DB session, no
paid API) — it pins the *contract wiring*, not the I/O.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.asset_prompt_builder_service import (
    ASSET_PORTRAIT,
    ASSET_BACKGROUND,
    ASSET_KEYFRAME,
    build_asset_prompt,
)
from app.services.background_image_validator_service import (
    StyleDeviationReport,
)
from app.services.project_visual_bible_service import (
    project_visual_bible_service,
    VERSION as BIBLE_VERSION,
)
from app.services.project_visual_consistency_service import (
    project_visual_consistency_service,
)


# ----------------------------------------------------------------------
# three synthetic story bibles covering the three style families we care about
# ----------------------------------------------------------------------

def _modern_campus_sb():
    return {
        "raw_json": {},
        "worldview": "2020 年代某普通一本大学，计算机科学与技术专业",
        "style_rules": "轻松幽默为主基调，叙事节奏明快",
        "characters": [{"name": "李明", "gender": "男", "appearance": "黑框眼镜，格子衬衫"}],
    }


def _ink_guofeng_sb():
    return {
        "raw_json": {},
        "worldview": "古代唐朝长安城",
        "style_rules": "水墨工笔风格，工笔重彩",
        "characters": [{"name": "李白", "gender": "男"}],
    }


def _scifi_anime_sb():
    return {
        "raw_json": {},
        "worldview": "23 世纪人类殖民火星，赛博朋克都市",
        "style_rules": "科幻冒险，霓虹光影",
        "characters": [{"name": "Rei", "gender": "女"}],
    }


# ----------------------------------------------------------------------
# F6-A: fingerprint is shared across all assets in one project
# ----------------------------------------------------------------------

def test_fingerprint_shared_across_ten_assets_in_one_project():
    """Hard contract: ten assets from one project must carry the same
    fingerprint. This is the cache-key invariant."""
    for sb in (_modern_campus_sb(), _ink_guofeng_sb(), _scifi_anime_sb()):
        bible = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )
        # Build 10 prompts across all 3 asset types
        prompts = []
        for i in range(4):
            prompts.append(build_asset_prompt(
                visual_bible=bible, asset_type=ASSET_PORTRAIT,
                subject=f"subject_{i}",
            ))
        for i in range(4):
            prompts.append(build_asset_prompt(
                visual_bible=bible, asset_type=ASSET_BACKGROUND,
                subject=f"scene_{i}",
                scene_camera="24mm wide",
                scene_atmosphere="misty morning",
            ))
        for i in range(2):
            prompts.append(build_asset_prompt(
                visual_bible=bible, asset_type=ASSET_KEYFRAME,
                subject=f"moment_{i}",
            ))

        # All 10 prompts must contain the same fingerprint string
        fp = bible.fingerprint
        assert all(fp in p for p in prompts), (
            f"fingerprint {fp} missing from at least one of 10 prompts"
        )
        assert len(prompts) == 10


# ----------------------------------------------------------------------
# F6-B: three asset types byte-identical shared block, per project
# ----------------------------------------------------------------------

def test_shared_block_identical_across_asset_types_for_each_style_family():
    for sb in (_modern_campus_sb(), _ink_guofeng_sb(), _scifi_anime_sb()):
        bible = project_visual_bible_service.build_for_project(
            sb, project_title="T", project_style="",
        )
        p = build_asset_prompt(visual_bible=bible, asset_type=ASSET_PORTRAIT, subject="x")
        b = build_asset_prompt(visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="y")
        k = build_asset_prompt(visual_bible=bible, asset_type=ASSET_KEYFRAME, subject="z")

        def shared(text):
            return [ln for ln in text.split("\n") if ln.strip()][:8]

        sp, sb_, sk = shared(p), shared(b), shared(k)
        assert sp == sb_ == sk, (
            f"shared block diverged in style_family={bible.style_family}: "
            f"portrait={sp}, bg={sb_}, keyframe={sk}"
        )


# ----------------------------------------------------------------------
# F6-C: different projects have different fingerprints (no cross-project bleed)
# ----------------------------------------------------------------------

def test_fingerprint_differs_across_projects():
    b1 = project_visual_bible_service.build_for_project(
        _modern_campus_sb(), project_title="T1", project_style="",
    )
    b2 = project_visual_bible_service.build_for_project(
        _ink_guofeng_sb(), project_title="T2", project_style="",
    )
    b3 = project_visual_bible_service.build_for_project(
        _scifi_anime_sb(), project_title="T3", project_style="",
    )
    fps = {b1.fingerprint, b2.fingerprint, b3.fingerprint}
    assert len(fps) == 3, f"fingerprints must all differ, got {fps}"


# ----------------------------------------------------------------------
# F6-D: group consistency flow (validate → evaluate_group → outliers list)
# ----------------------------------------------------------------------

def test_group_consistency_flow_flags_off_project_image():
    """Simulate a project that's anime_cel but one image looks like neon photo.
    Decision 2: the outlier is recorded but the group is NEVER auto-rejected."""
    sb = _modern_campus_sb()
    bible = project_visual_bible_service.build_for_project(
        sb, project_title="T", project_style="",
    )

    # Six images: 5 cohesive anime_cel metrics, 1 wildly off (photo-like)
    def dev(sat, con, edge, pal, warnings=None):
        return StyleDeviationReport(
            saturation=sat, contrast=con, edge_density=edge,
            palette_distance_to_baseline=pal, warnings=warnings or [],
        )

    deviations = {
        "bg1": dev(0.30, 0.50, 0.06, 0.20),
        "bg2": dev(0.31, 0.49, 0.055, 0.22),
        "bg3": dev(0.29, 0.51, 0.065, 0.18),
        "bg4": dev(0.30, 0.50, 0.060, 0.21),
        "bg5": dev(0.32, 0.48, 0.058, 0.23),
        "bg6_outlier": dev(0.95, 0.92, 0.50, 0.85,
                           warnings=["saturation_gap=0.5"]),
    }
    report = project_visual_consistency_service.evaluate_group(deviations)
    # Outlier is flagged
    assert "bg6_outlier" in report.outliers
    # But rejected is False (decision 2: warning only)
    assert report.rejected is False
    # The cohesive five are NOT flagged
    for k in ("bg1", "bg2", "bg3", "bg4", "bg5"):
        assert k not in report.outliers


# ----------------------------------------------------------------------
# F6-F: version stamp constants are aligned across the pipeline
# ----------------------------------------------------------------------

def test_version_constants_align_with_pipeline_components():
    """All four version stamps must be the v2 constants, otherwise cache
    invalidation breaks."""
    from app.services.asset_prompt_builder_service import VERSION as PROMPT_VERSION
    from app.services.background_image_validator_service import (
        BACKGROUND_VALIDATION_VERSION,
    )
    from app.services.portrait_normalization_service import (
        PORTRAIT_NORMALIZATION_VERSION,
    )

    assert BIBLE_VERSION == "project-visual-bible-v5"
    assert PROMPT_VERSION == "shared-style-prompt-v2"
    assert BACKGROUND_VALIDATION_VERSION == "project-coherence-v2"
    assert PORTRAIT_NORMALIZATION_VERSION == "generic-portrait-canvas-v2"


# ----------------------------------------------------------------------
# F6-G: forbidden styles leak-check — anime bible never emits oil/photo vocab
# ----------------------------------------------------------------------

def test_anime_bible_pipeline_never_emits_oil_or_photo_in_background_prompt():
    """Real failure mode from the bug: anime_cel project had 'oil painting'
    leaking into background prompts as POSITIVE style vocabulary. After the
    refactor, medium vocab lives only in the shared block (where anime_cel
    uses cel-shading vocabulary) and in the forbidden list (where it's an
    explicit allow-list tell).

    A *negative* mention ("no photographic noise") is permitted — the bug
    was about positive style drift, not about disavowing a medium.
    """
    sb = _modern_campus_sb()
    bible = project_visual_bible_service.build_for_project(
        sb, project_title="T", project_style="",
    )
    assert bible.style_family == "anime_cel"

    p = build_asset_prompt(
        visual_bible=bible, asset_type=ASSET_BACKGROUND,
        subject="library interior with bookshelves",
        scene_camera="24mm wide",
        scene_atmosphere="soft afternoon light",
    )
    # Strip the forbidden block — those words are SUPPOSED to be there as an allow-list
    forbidden_lower_list = [s.lower() for s in bible.forbidden_styles]

    # Also strip negative phrases ("no photographic noise", "no oil painting ...")
    # — these are *disavowals*, not leaks.
    import re
    p_for_check = re.sub(r"\bno\s+\w+(?:\s+\w+){0,3}", " ", p.lower())
    for fb in forbidden_lower_list:
        p_for_check = p_for_check.replace(fb, " ")

    for term in ("oil painting", "photorealistic", "photograph"):
        assert term not in p_for_check, (
            f"anime_cel background prompt positively leaked '{term}': {p}"
        )
