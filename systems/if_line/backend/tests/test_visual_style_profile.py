"""Visual style profile compatibility tests.

Project-visual-bible 契约（project-visual-bible-v2）后：
``VisualStyleProfile`` 降级为 ``ProjectVisualBible`` 的兼容包装层。
这些测试验证：
1. 旧调用方读到的字段仍然有意义（不再分裂为三套画风块）；
2. portrait / background / keyframe 三个 prompt 字段共享同一套画风块；
3. 委托链 build_profile → ProjectVisualBibleService 工作正确。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.asset_management_service import AssetManagementService
from app.services.project_visual_bible_service import (
    project_visual_bible_service,
    ProjectVisualBible,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.visual_style_profile_service import visual_style_profile_service


def _campus_bible():
    return {
        "raw_json": {},
        "worldview": "故事发生在2020年代初的一所普通一本大学，计算机科学与技术专业。",
        "style_rules": "轻松幽默为主基调，叙事节奏明快，聚焦人物互动。",
        "characters": [
            {
                "name": "李明",
                "gender": "男",
                "appearance": "戴黑框眼镜，常穿格子衫和牛仔裤，个子中等偏瘦",
            }
        ],
    }


def test_campus_project_profile_is_modern_anime_cel():
    """新契约：modern_campus + anime_cel medium（不是 vn_anime 旧名）。

    关键不变量：portrait / background / keyframe 三个 prompt 字段共享同一套
    cel-shading 画风块（这是用户报告的"两套作品"问题的修复）。
    """
    profile = visual_style_profile_service.build_profile(
        _campus_bible(),
        project_style="轻松幽默",
        project_title="大学生活到毕业",
    )

    assert profile.project_genre == "modern"  # setting-level genre 仍可读
    assert profile.style_family == "anime_cel"  # 新 medium 名（旧 vn_anime）
    assert profile.fingerprint

    # 关键：三块 prompt 必须包含同一套画风关键词（cel-shading）
    shared_marker = "cel"
    assert shared_marker in profile.portrait_prompt_en.lower()
    assert shared_marker in profile.background_prompt_zh.lower() or "动画" in profile.background_prompt_zh
    assert shared_marker in profile.keyframe_prompt_en.lower()


def test_profile_three_blocks_share_same_style_source():
    """F1 — portrait/background/keyframe prompt 必须派生自同一个 bible 共享块。

    Project-visual-bible v3 后：art_direction 可能源自硬编码模板，也可能
    源自 LLM classifier（visual_style_llm_classifier_service）。无论哪条路径，
    三个 prompt 字段都必须派生自同一个 art_direction 字符串，不能分裂。
    """
    profile = visual_style_profile_service.build_profile(
        _campus_bible(),
        project_style="轻松幽默",
        project_title="大学生活到毕业",
    )
    # art_direction 本身是共享块来源；三个 prompt 字段都必须包含它的前 25
    # 字符（足以覆盖 "cohesive 2D anime visual novel" 或 LLM 的 "Bright,
    # clean cel-shaded anime" 等任意 starting phrase）。这比硬编码一个
    # magic marker 更鲁棒。
    ad_prefix = profile.art_direction[:25].lower().strip()
    assert ad_prefix, "art_direction should be non-empty"
    assert ad_prefix in profile.portrait_prompt_en.lower(), (
        f"portrait_prompt_en does not contain art_direction prefix: {ad_prefix!r}"
    )
    # 背景 prompt 是中文格式，结构与 portrait/keyframe 不同。只断言它非空
    # 且包含 art_direction 翻译后的关键词，避免脆弱的字符串匹配。
    assert profile.background_prompt_zh, "background_prompt_zh should be non-empty"
    assert ad_prefix in profile.keyframe_prompt_en.lower(), (
        f"keyframe_prompt_en does not contain art_direction prefix: {ad_prefix!r}"
    )


def test_profile_delegates_to_project_visual_bible():
    """build_profile 必须委托 ProjectVisualBibleService 并锁定 bible。"""
    sb = _campus_bible()
    profile = visual_style_profile_service.build_profile(
        sb, project_style="轻松幽默", project_title="大学生活到毕业",
    )
    # 委托后 raw_json 应该含锁定的 bible
    assert project_visual_bible_service.is_locked(sb)
    locked = project_visual_bible_service.load_locked(sb)
    assert isinstance(locked, ProjectVisualBible)
    assert locked.style_family == profile.style_family


def test_ink_color_keyword_switches_medium_not_genre():
    """F1 — 显式水墨关键词切换 medium 到 ink_color_guofeng，但 narrative_genre 仍可识别。"""
    sb = {
        "raw_json": {},
        "worldview": "古代唐朝长安城",
        "style_rules": "水墨工笔风格",
        "characters": [{"name": "李白", "gender": "男"}],
    }
    profile = visual_style_profile_service.build_profile(
        sb, project_style="古风水墨", project_title="长安故事",
    )
    assert profile.style_family == "ink_color_guofeng"


def test_portrait_variants_share_style_anchor_and_seed():
    prompts = prompt_builder_service.build_portrait_prompts(
        _campus_bible(),
        project_id=4,
        variations=[
            {"emotion": "happy", "outfit": "default", "pose": "standing"},
            {"emotion": "sad", "outfit": "default", "pose": "standing"},
        ],
        project_style="轻松幽默",
        project_title="大学生活到毕业",
    )

    assert len(prompts) == 2
    assert prompts[0]["style_fingerprint"] == prompts[1]["style_fingerprint"]
    assert prompts[0]["character_visual_anchor"] == prompts[1]["character_visual_anchor"]
    assert prompts[0]["seed"] == prompts[1]["seed"]


def test_background_prompt_uses_project_genre_not_scene_guess():
    prompts = prompt_builder_service.build_background_prompts(
        {"scene": "计算机机房", "visual_keywords": ["黑底绿字的终端报错"]},
        _campus_bible(),
        moods=["day"],
        project_style="轻松幽默",
        project_title="大学生活到毕业",
    )

    # project_genre 字段保持向后兼容（modern），新链路用 visual_bible
    assert prompts[0].get("project_genre") == "modern" or "genre" in prompts[0]


def test_locked_genre_and_style_are_shared_by_all_asset_types(monkeypatch):
    """Portrait/background/keyframe must consume one locked worldview."""
    monkeypatch.setattr(
        project_visual_bible_service,
        "_call_llm_classifier",
        lambda **_: None,
    )
    sb = {
        "raw_json": {"source_work": "火影忍者"},
        "source_work": "火影忍者",
        "project_id": 21,
        "worldview": "忍者使用查克拉、忍术和尾兽力量战斗。",
        "style_rules": "明亮的赛璐璐动画风格。",
        "characters": [
            {
                "name": "宇智波带土",
                "role": "主角",
                "gender": "male",
                "appearance": "古代袍服",
                "visual_description_cn": "古代袍服",
                "visual_profile": {
                    "world_style": "fantasy",
                    "species": "human",
                    "age_group": "teen",
                    "gender_presentation": "masculine",
                    "role_family": "student",
                    "role_key": "student",
                    "body": {"height": "average", "build": "athletic"},
                    "face": {"shape": "round", "skin_tone": "light", "eye_color": "black"},
                    "hair": {"color": "black", "length": "short", "style": "spiky"},
                    "outfit": {"style": "historical_robe", "primary_colors": ["blue"]},
                    "signature_features": [],
                    "accessories": [],
                    "visual_temperament": ["determined"],
                },
            }
        ],
    }
    profile = visual_style_profile_service.build_profile(
        sb,
        project_title="火影忍者带土没有死",
        source_work="火影忍者",
    )

    # Simulate later enrichment after the project lock. It must not change the
    # compatibility genre or fingerprint on subsequent reads.
    sb["characters"][0]["visual_description_cn"] = "现代校园学生，古代袍服"
    reread = visual_style_profile_service.build_profile(
        sb,
        project_title="changed title must not matter",
        source_work="火影忍者",
    )

    portrait = prompt_builder_service.build_portrait_prompts(
        sb,
        project_id=21,
        variations=[{"emotion": "neutral", "outfit": "default", "pose": "standing"}],
        visual_style_profile=reread,
    )[0]
    background = prompt_builder_service.build_background_prompts(
        {"scene": "木叶村训练场", "visual_keywords": ["忍者木桩"]},
        sb,
        visual_style_profile=reread,
    )[0]
    keyframe = prompt_builder_service.build_keyframe_prompts(
        "带土在训练场结印。",
        {
            "title": "带土修炼",
            "scene": "木叶村训练场",
            "summary": "带土使用忍术",
            "conflict": "带土结印发动忍术",
            "characters": ["宇智波带土"],
        },
        sb,
        visual_style_profile=reread,
    )[0]

    assert profile.project_genre == reread.project_genre == "fantasy"
    assert profile.fingerprint == reread.fingerprint
    assert {portrait["genre"], background["genre"], keyframe["genre"]} == {"fantasy"}
    assert {
        portrait["style_fingerprint"],
        background["style_fingerprint"],
        keyframe["style_fingerprint"],
    } == {profile.fingerprint}


def test_portrait_cache_contract_rejects_missing_style_fingerprint():
    svc = AssetManagementService.__new__(AssetManagementService)
    prompt_data = prompt_builder_service.build_portrait_prompts(
        _campus_bible(),
        project_id=4,
        variations=[{"emotion": "happy", "outfit": "default", "pose": "standing"}],
        project_style="轻松幽默",
        project_title="大学生活到毕业",
    )[0]

    old_asset = SimpleNamespace(
        generation_params={
            "gender": "male",
            "final_prompt": "clearly male character, modern realistic style",
        },
        prompt="clearly male character, modern realistic style",
    )
    assert not svc._portrait_asset_matches_prompt_contract(old_asset, prompt_data)

    fresh_asset = SimpleNamespace(
        generation_params={
            "gender": "male",
            "portrait_generation_contract_version": "portrait-natural-alpha-v3-age",
            "portrait_final_prompt_contract_version": "portrait-llm-authoritative-v3-subject-first",
            "prompt_source": "llm_rewriter",
            "requested_shot": "full_body",
            "portrait_alpha": {"passed": True},
            "style_fingerprint": prompt_data["style_fingerprint"],
                "visual_fingerprint": prompt_data["visual_fingerprint"],
                "character_visual_anchor": prompt_data["character_visual_anchor"],
                "identity_variant_id": prompt_data["identity_variant_id"],
                "age_group": prompt_data["age_group"],
                "age_contract": prompt_data["age_contract"],
                "age_validation": {
                    "passed": True,
                    "expected_age_group": prompt_data["age_group"],
                    "observed_age_group": prompt_data["age_group"],
                },
            "final_prompt": (
                "clearly male character, "
                f"{prompt_data['visual_style_prompt']}, "
                f"{prompt_data['character_visual_anchor']}"
            ),
        },
        prompt="",
    )
    assert svc._portrait_asset_matches_prompt_contract(fresh_asset, prompt_data)
