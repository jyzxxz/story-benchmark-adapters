"""Portrait/keyframe compatibility regression tests."""
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.image_generation_service import ImageGenerationService
from app.services.asset_management_service import AssetManagementService
from app.services.prompt_builder_service import prompt_builder_service


@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


# -------------------- portrait regression --------------------

def test_portrait_build_prompt_callable(svc):
    """portrait prompt builder 仍可调用（按实际签名）"""
    out = svc._build_portrait_prompt(
        appearance_prompt="冷峻青年，黑发，长衫",
        emotion="calm",
        outfit="黑色长衫",
        pose="standing",
        style_prompt="cinematic",
        forbidden="林夜",
        full_body=True,
    )
    assert isinstance(out, str)
    assert len(out) > 0


def test_portrait_prompt_no_background_clause(svc):
    """portrait prompt 不含 background ban clause"""
    out = svc._build_portrait_prompt(
        appearance_prompt="x", emotion="y", outfit="z",
        pose="standing", style_prompt="cinematic",
        forbidden="林夜", full_body=True,
    )
    assert "Environment-focused background art" not in out


def test_portrait_prompt_includes_gender_anchor(svc):
    """fallback prompt 必须显式携带性别锚点，避免 anime 模型默认偏女性。"""
    out = svc._build_portrait_prompt(
        appearance_prompt="冷峻青年，黑发，长衫",
        emotion="neutral",
        outfit="default",
        pose="standing",
        style_prompt="cinematic",
        forbidden="",
        full_body=True,
        gender_prompt="clearly male character, masculine facial structure and body silhouette, not feminine",
    )
    assert "clearly male character" in out
    assert out.index("clearly male character") < out.index("冷峻青年")


def test_legacy_portrait_gender_anchor_helper_remains_available(svc):
    """旧辅助函数仍可用，但生产链路不会用它修改 LLM final_prompt。"""
    anchor = svc._build_portrait_gender_anchor("男")
    out = svc._apply_portrait_gender_anchor("black-haired swordsman in dark robes", anchor)
    assert out.startswith("clearly male character")
    assert "black-haired swordsman" in out


def test_prompt_builder_extracts_gender_fields():
    """StoryBible 角色卡的 gender 会进入 portrait prompt data。"""
    prompts = prompt_builder_service.build_portrait_prompts(
        {
            "characters": [
                {"name": "林夜", "gender": "男", "appearance": "黑发青年"},
                {"name": "苏婉", "gender": "女", "appearance": "白衣少女"},
            ]
        },
        project_id=7,
        variations=[{"emotion": "neutral", "outfit": "default", "pose": "standing"}],
    )
    by_name = {p["character_name"]: p for p in prompts}
    assert by_name["林夜"]["gender"] == "male"
    assert "clearly male character" in by_name["林夜"]["gender_prompt"]
    assert by_name["苏婉"]["gender"] == "female"
    assert "clearly female character" in by_name["苏婉"]["gender_prompt"]


def test_prompt_builder_enriches_legacy_character_genders():
    """旧版 StoryBible 缺 gender 时，用角色卡语义和正文代词补齐。"""
    story_bible = {
        "characters": [
            {
                "name": "高志远",
                "role": "主角",
                "appearance": "戴黑框眼镜，总穿着洗得发白的校服",
                "internal_conflict": "作为家中独子，渴望被认可。",
            },
            {
                "name": "林婉清",
                "role": "重要配角（女班长/知己）",
                "appearance": "清爽短发，眼神锐利",
            },
            {
                "name": "高父（高建国）",
                "role": "重要配角（父亲）",
                "appearance": "两鬓微白，常年穿工装",
            },
            {
                "name": "陈老师",
                "role": "次要角色（班主任）",
                "appearance": "三十多岁，身材微胖，总是抱着文件夹",
            },
        ],
    }
    context = "陈老师抱着文件夹推门进来。他从文件夹里抽出表格，语调平稳。"

    enriched = prompt_builder_service.enrich_story_bible_genders(story_bible, context)
    by_name = {c["name"]: c for c in enriched["characters"]}

    assert by_name["高志远"]["gender"] == "male"
    assert by_name["林婉清"]["gender"] == "female"
    assert by_name["高父（高建国）"]["gender"] == "male"
    assert by_name["陈老师"]["gender"] == "male"


def test_portrait_cache_contract_rejects_old_prompt_without_gender():
    """旧立绘没有性别锚点时不能继续被缓存复用。"""
    svc = AssetManagementService.__new__(AssetManagementService)
    old_asset = SimpleNamespace(generation_params={}, prompt="black-haired swordsman in dark robes")
    prompt_data = {
        "gender": "male",
        "gender_prompt": "clearly male character, masculine facial structure and body silhouette, not feminine",
    }
    assert not svc._portrait_asset_matches_prompt_contract(old_asset, prompt_data)

    fresh_asset = SimpleNamespace(
        generation_params={
            "gender": "male",
            "portrait_generation_contract_version": "portrait-natural-alpha-v3-age",
            "portrait_final_prompt_contract_version": "portrait-llm-authoritative-v3-subject-first",
            "prompt_source": "llm_rewriter",
            "requested_shot": "full_body",
            "portrait_alpha": {"passed": True},
            "final_prompt": "clearly male character, masculine facial structure and body silhouette, black-haired swordsman",
        },
        prompt="",
    )
    assert svc._portrait_asset_matches_prompt_contract(fresh_asset, prompt_data)


# -------------------- keyframe regression --------------------

def test_keyframe_build_prompt_callable(svc):
    """keyframe prompt builder 仍可调用"""
    out = svc._build_keyframe_prompt(
        scene_description="庭院夜晚",
        characters=[{"name": "林夜", "appearance": "黑发青年"}],
        action="对话",
        emotion="紧张",
        style_prompt="cinematic",
    )
    assert isinstance(out, str)
    assert len(out) > 0


def test_keyframe_prompt_no_background_ban(svc):
    """keyframe prompt 不含 background ban clause"""
    out = svc._build_keyframe_prompt(
        scene_description="x",
        characters=[{"name": "林夜", "appearance": "y"}],
        action="z", emotion="m", style_prompt="s",
    )
    assert "Environment-focused background art" not in out


# -------------------- enforce is background-only --------------------

def test_enforce_does_not_alter_portrait(svc):
    """portrait prompt 不被 background enforce 改写"""
    portrait = svc._build_portrait_prompt(
        appearance_prompt="x", emotion="y", outfit="z",
        pose="p", style_prompt="s", forbidden="f", full_body=True,
    )
    # 调用 enforce 但不传 spec（= legacy path）
    out = svc._enforce_background_environment_focus(portrait)
    # legacy path 应当不强加 background ban clause
    assert "Environment-focused background art" not in out or portrait in out


def test_portrait_keyframe_independent_of_schema():
    """portraits/keyframes 路径不依赖 BackgroundSceneSpec"""
    from app.services import image_generation_service as mod
    for fname in ("_build_portrait_prompt", "_build_keyframe_prompt"):
        sig = inspect.signature(getattr(mod.ImageGenerationService, fname))
        params = list(sig.parameters.keys())
        assert "spec" not in params
        assert "BackgroundSceneSpec" not in str(sig.parameters)
