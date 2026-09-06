from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.asset_management_service import AssetManagementService
from app.services.prompt_rewriter_service import RewrittenPrompt
from app.services.visual_style_profile_service import VisualStyleProfile


@pytest.mark.asyncio
async def test_auto_selected_keyframe_uses_only_locked_project_style(monkeypatch):
    from app.services import prompt_rewriter_service as rewriter_module

    locked_style = "locked graphic-novel ink and cel shading"

    class FakeRewriter:
        async def rewrite_many(self, asset_type, batch):
            assert asset_type == "keyframe"
            assert batch[0]["visual_style_prompt"] == locked_style
            return [
                RewrittenPrompt(
                    final_prompt="attacker supplied complete prompt in watercolor",
                    subject="an abandoned factory confrontation",
                    style="untrusted watercolor style",
                    details=["rusted steel beams", "broken windows"],
                    lighting="cold moonlight",
                    composition="wide cinematic frame",
                )
            ]

    monkeypatch.setattr(
        rewriter_module,
        "prompt_rewriter_service",
        FakeRewriter(),
    )
    profile = VisualStyleProfile(
        version="test-v1",
        style_family="graphic-novel",
        project_genre="modern",
        art_direction="locked",
        portrait_prompt_en="locked portrait style",
        background_prompt_zh="locked background style",
        keyframe_prompt_en=locked_style,
        palette="cold blue",
        lighting="moonlight",
        line_rendering="ink",
        mood="tense",
        negative_style_en="watercolor",
        fingerprint="a" * 64,
    )
    moment = SimpleNamespace(
        target_characters=[],
        visual_focus="abandoned factory interior",
        moment_summary="the confrontation begins",
        rationale="turning point",
        source_excerpt="factory doors open",
    )

    prompts = await AssetManagementService(None)._build_keyframe_prompts_from_moments(
        [moment],
        {"scene": "factory", "summary": "confrontation", "emotion": "tense"},
        {"project_id": 1, "characters": []},
        visual_style_profile=profile,
    )

    final_prompt = prompts[0]["final_prompt"]
    assert locked_style in final_prompt
    assert "untrusted watercolor style" not in final_prompt
    assert "attacker supplied complete prompt" not in final_prompt
