"""project-visual-bible-v2 §F3 — conflict / leak tests.

Hard constraint (plan §A): forbidden_styles must physically appear in
the assembled prompt; an LLM emitting a polluted ``style`` field must
have it discarded; an anime_cel bible must never leak oil-photographic
vocabulary into its background prompt.
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.asset_prompt_builder_service import (
    ASSET_BACKGROUND,
    ASSET_PORTRAIT,
    build_asset_prompt,
)
from app.services.project_visual_bible_service import (
    project_visual_bible_service,
    ProjectVisualBible,
)


def _bible_with_forbidden(forbidden: list[str]) -> ProjectVisualBible:
    sb = {
        "raw_json": {},
        "worldview": "test",
        "style_rules": "",
        "characters": [],
    }
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    # Bible is frozen — construct a derivative with the forbidden list patched in.
    from dataclasses import replace
    return replace(bible, forbidden_styles=tuple(forbidden))


def test_forbidden_styles_appear_in_prompt():
    """Plan §B6: '[FORBIDDEN STYLE]' line must contain every entry."""
    bible = _bible_with_forbidden(["photorealistic", "oil painting"])
    p = build_asset_prompt(visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="x")
    assert "photorealistic" in p
    assert "oil painting" in p
    assert "[FORBIDDEN STYLE]" in p


def test_anime_cel_bible_does_not_leak_oil_or_photo_vocab():
    sb = {
        "raw_json": {},
        "worldview": "modern campus",
        "style_rules": "",
        "characters": [{"name": "x", "gender": "男"}],
    }
    bible = project_visual_bible_service.build_for_project(sb, project_title="T", project_style="")
    assert bible.style_family == "anime_cel"
    p = build_asset_prompt(visual_bible=bible, asset_type=ASSET_BACKGROUND, subject="library")
    p_lower = p.lower()
    # The shared art-direction line is the only place medium vocab lives,
    # and anime_cel's forbidden list typically excludes these.
    assert "oil painting" not in p_lower or "oil painting" in (
        " ".join(bible.forbidden_styles).lower()
    )


def test_prompt_rewriter_style_field_discarded_when_locked():
    """Plan §B6 + RewrittenPrompt.style_locked=True (default): the LLM-emitted
    ``style`` field must NOT appear in to_cogview_prompt output.

    This test pins the contract via prompt_rewriter_service directly.
    """
    from app.services.prompt_rewriter_service import RewrittenPrompt

    rp = RewrittenPrompt(
        subject="library interior",
        details=["wooden shelves", "reading lamp"],
        lighting="soft afternoon sun",
        composition="medium shot",
        style="oil painting, photorealistic",  # pollution attempt
    )
    assert rp.style_locked is True  # default
    cogview = rp.to_cogview_prompt()
    assert "oil painting" not in cogview.lower()
    assert "photorealistic" not in cogview.lower()
    # Subject / details / lighting must still be present
    assert "library interior" in cogview
    assert "wooden shelves" in cogview


def test_prompt_rewriter_style_unlocked_preserves_legacy_behavior():
    """Backwards-compat: legacy callers with style_locked=False still get
    the style block (so the migration doesn't break old code paths)."""
    from app.services.prompt_rewriter_service import RewrittenPrompt

    rp = RewrittenPrompt(
        subject="library",
        details=[],
        lighting="soft",
        composition="medium",
        style="anime cel-shading",
        style_locked=False,
    )
    cogview = rp.to_cogview_prompt()
    assert "anime cel-shading" in cogview
