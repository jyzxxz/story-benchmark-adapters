"""End-to-end test: 海贼王艾斯存活线 — 立绘 + 背景图生成。

This script simulates the full generation pipeline for a One Piece fan
story without going through the DB layer. It:
1. Builds the project visual bible (LLM classifier active).
2. Generates ONE portrait for Ace using prompt_rewriter + image_generation.
3. Generates ONE background for the Marineford battlefield.
4. Prints all asset URLs + final CogView prompts.

Run: python tests/_run_onepiece_e2e.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

# Load .env explicitly (don't rely on pytest-dotenv since this isn't a test)
from dotenv import load_dotenv
load_dotenv()

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.project_visual_bible_service import project_visual_bible_service
from app.services.visual_style_profile_service import visual_style_profile_service
from app.services.prompt_rewriter_service import prompt_rewriter_service
from app.services.image_generation_service import image_generation_service
from app.services.source_visual_profile_service import source_visual_profile_service
from app.services.prompt_builder_service import prompt_builder_service


# ------------------------------------------------------------------
# Story inputs
# ------------------------------------------------------------------

ONEPIECE_SB = {
    "raw_json": {},
    "worldview": (
        "故事发生在海贼王伟大航路后半段新世界。顶上战争马林梵多之战中，"
        "艾斯（波特卡斯·D·艾斯，火拳）没有死于赤犬的岩浆拳，而是被路飞拼死救出。"
        "战后艾斯重伤昏迷三个月，醒来后白胡子海贼团已解散，他独自踏上寻找自我意义的航程。"
    ),
    "style_rules": "热血少年漫二创，尾田荣一郎原作画风致敬，清透光影与戏剧化分镜",
    "characters": [
        {
            "name": "艾斯",
            "gender": "男",
            "appearance": (
                "黑发短发向后梳，左胸白胡子海贼团骷髅头紫色十字纹身，红橙色无袖背心敞开，"
                "蓝色牛仔短裤，脖子上挂橙色珠串项链，腰间短刀，桀骜而温和的气质"
            ),
        },
    ],
}


async def main():
    print("=" * 70)
    print("STAGE 1: Build project visual bible (LLM classifier path)")
    print("=" * 70)
    sb = json.loads(json.dumps(ONEPIECE_SB))  # deep copy
    bible = project_visual_bible_service.build_for_project(
        sb,
        project_title="火拳新生：艾斯存活线",
        project_style="热血少年漫二创",
        source_work="海贼王 / ONE PIECE",
    )
    cls = sb["raw_json"]["project_visual_bible"]["classification"]
    print(f"  visual_medium    = {cls['visual_medium']}")
    print(f"  source_profile_id = {cls.get('source_profile_id') or '(none — LLM path)'}")
    print(f"  llm_decision      = {cls.get('llm_decision') is not None}")
    if cls.get("llm_decision"):
        d = cls["llm_decision"]
        print(f"  LLM medium        = {d['medium']}")
        print(f"  LLM art_direction = {d['art_direction']}")
        print(f"  LLM palette       = {list(d['base_palette'])}")
        print(f"  LLM rationale     = {d.get('rationale')}")
    print(f"  bible fingerprint = {bible.fingerprint}")

    profile = visual_style_profile_service.profile_from_bible(bible)
    print()
    print("  shared portrait_prompt_en  ==", profile.portrait_prompt_en[:200], "...")
    print("  shared background_prompt_zh ==", profile.background_prompt_zh[:200], "...")

    # ------------------------------------------------------------------
    # STAGE 2: Portrait — Ace
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("STAGE 2: Generate portrait for 艾斯 (Ace)")
    print("=" * 70)

    # ------------------------------------------------------------------
    # STAGE 1.5: Run apply_character_overrides — this triggers the LLM
    # canonical-character recognizer for non-hardcoded franchises.
    # For 海贼王 / 艾斯, the LLM should lock the portrait to Ace's official
    # One Piece design rather than a generic "致敬尾田画风" fan drawing.
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("STAGE 1.5: apply_character_overrides (LLM canonical-char recognizer)")
    print("=" * 70)
    sb = source_visual_profile_service.apply_character_overrides(
        sb,
        cls.get("source_profile_id", ""),
    )
    ace_after = sb["characters"][0]
    print(f"  source_identity_locked  = {ace_after.get('source_identity_locked')}")
    print(f"  canonical_franchise     = {ace_after.get('canonical_franchise')}")
    print(f"  canonical_name_official = {ace_after.get('canonical_name_official')}")
    print(f"  visual_fingerprint      = {ace_after.get('visual_fingerprint')}")
    if ace_after.get("visual_prompt_en"):
        print(f"  visual_prompt_en (first 300 chars):")
        print(f"    {ace_after['visual_prompt_en'][:300]}")

    ace = sb["characters"][0]
    appearance = source_visual_profile_service.character_appearance(
        cls.get("source_profile_id", ""),
        ace["name"],
        prompt_builder_service._extract_appearance(ace),
        source_work=str(sb.get("source_work") or "海贼王 / ONE PIECE"),
        worldview=str(sb.get("worldview") or ""),
        style_rules=str(sb.get("style_rules") or ""),
    )
    gender = prompt_builder_service._extract_gender(ace)
    gender_prompt = prompt_builder_service.build_gender_prompt(gender)

    rewriter_fields = {
        "asset_type": "portrait",
        "character_id": "ace_001",
        "name": ace["name"],
        "appearance_zh": appearance,
        "gender": gender,
        "gender_prompt": gender_prompt,
        "emotion": "neutral",
        "outfit": "default",
        "pose": "standing",
        "genre": "anime",
        "project_genre": profile.project_genre,
        "visual_style_profile": profile.to_dict(),
        "visual_style_prompt": profile.portrait_prompt_en,
        "style_fingerprint": profile.fingerprint,
        "shot": "full_body",
        "character_visual_anchor": visual_style_profile_service.character_visual_anchor(
            ace["name"], appearance, gender
        ),
    }
    rewritten = await prompt_rewriter_service.rewrite("portrait", rewriter_fields)
    final_prompt = rewritten.to_cogview_prompt() if rewritten else None
    print(f"  rewriter returned = {rewritten is not None}")
    if rewritten:
        print(f"  subject (en)   = {rewritten.subject}")
        print(f"  details count  = {len(rewritten.details)}")
        print(f"  details (en)   = {rewritten.details[:3]}")
        print(f"  lighting (en)  = {rewritten.lighting}")
        print(f"  composition    = {rewritten.composition}")
        ok, errs = rewritten.guardrail_check("portrait")
        print(f"  guardrail ok   = {ok} errors={errs}")
    else:
        # Rewriter may fail due to LLM variance / guardrail overflow / timeout.
        # Probe the raw LLM output for debugging visibility.
        raw = await prompt_rewriter_service._call_llm("portrait", rewriter_fields)
        if raw is not None:
            print(f"  [debug] raw subject len = {len(raw.subject)} chars")
            print(f"  [debug] raw subject     = {raw.subject}")
            ok, errs = raw.guardrail_check("portrait")
            print(f"  [debug] raw guardrail   = {ok} errors={errs}")
        else:
            print("  [debug] raw LLM call returned None")
    print()
    print(f"  FINAL CogView prompt:\n  {final_prompt}")
    print()

    portrait_result = await image_generation_service.generate_portrait(
        character_id="ace_001",
        character_name="艾斯",
        appearance_prompt=appearance,
        emotion="neutral",
        outfit="default",
        pose="standing",
        genre="anime",
        seed=None,
        remove_bg=True,
        final_prompt=final_prompt,
        final_prompt_source="llm_rewriter" if final_prompt else None,
        gender=gender,
        gender_prompt=gender_prompt,
        visual_style_prompt=profile.portrait_prompt_en,
    )
    print(f"  portrait success = {portrait_result.get('success')}")
    if portrait_result.get("success"):
        print(f"  PORTRAIT image_url = {portrait_result['image_url']}")
        print(f"  source_image_url   = {portrait_result.get('source_image_url')}")
        print(f"  identity_ref_url   = {portrait_result.get('identity_reference_url')}")
        print(f"  presentation_url   = {portrait_result.get('presentation_url')}")
    else:
        print(f"  portrait error = {portrait_result.get('error')}")

    # ------------------------------------------------------------------
    # STAGE 3: Background — Marineford aftermath
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("STAGE 3: Generate background for 马林梵多战场 (Marineford)")
    print("=" * 70)

    bg_fields = {
        "asset_type": "background",
        "scene_name": "马林梵多战场废墟",
        "scene_description_zh": (
            "顶上战争结束后的马林梵多广场，断壁残垣，地面碎石与焦痕，"
            "远处海军本部建筑半毁，傍晚血色夕阳，硝烟未散，地面散落破碎的武器与披风"
        ),
        "mood": "dusk",
        "genre": "anime",
        "project_genre": profile.project_genre,
        "visual_style_profile": profile.to_dict(),
        "visual_style_prompt": profile.background_prompt_zh,
        "style_fingerprint": profile.fingerprint,
        "forbidden_characters": ["艾斯", "路飞", "白胡子"],
    }
    bg_rewritten = await prompt_rewriter_service.rewrite("background", bg_fields)
    bg_final_prompt = (
        bg_rewritten.to_background_cogview_prompt() if bg_rewritten else None
    )
    print(f"  rewriter returned = {bg_rewritten is not None}")
    if bg_rewritten:
        print(f"  subject (zh)  = {bg_rewritten.subject}")
        print(f"  details (zh)  = {bg_rewritten.details[:3]}")
        print(f"  lighting (zh) = {bg_rewritten.lighting}")
        print(f"  composition   = {bg_rewritten.composition}")
    print()
    print(f"  FINAL CogView background prompt:\n  {bg_final_prompt}")
    print()

    bg_result = await image_generation_service.generate_background(
        scene_name="马林梵多战场废墟",
        scene_description=bg_fields["scene_description_zh"],
        mood="dusk",
        genre="anime",
        final_prompt=bg_final_prompt,
        visual_style_prompt=profile.background_prompt_zh,
        forbidden_characters=["艾斯", "路飞", "白胡子"],
    )
    print(f"  background success = {bg_result.get('success')}")
    if bg_result.get("success"):
        print(f"  BACKGROUND image_url = {bg_result['image_url']}")
    else:
        print(f"  background error = {bg_result.get('error')}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if portrait_result.get("success"):
        print(f"PORTRAIT URL: {portrait_result['image_url']}")
    if bg_result.get("success"):
        print(f"BACKGROUND URL: {bg_result['image_url']}")
    print()
    print("To view locally:")
    backend = Path(__file__).parent.parent
    if portrait_result.get("success"):
        p = backend / portrait_result["image_url"].lstrip("/")
        print(f"  portrait file: {p} (exists={p.exists()})")
    if bg_result.get("success"):
        b = backend / bg_result["image_url"].lstrip("/")
        print(f"  background file: {b} (exists={b.exists()})")


if __name__ == "__main__":
    asyncio.run(main())
