"""
BG-NO-HUMAN-SUBJECT — 背景图"人物主体化"硬性拦截测试（2026-07-03 P0+P1 重构版）。

历史背景：原版用否定式 prompt（"不要人物/不要守卫/不要剪影"），
但智谱 CogView-4 不支持 negative_prompt 参数，CLIP text encoder
把这些 token 反向当成正向 hint，导致画面频繁出现中景大人物。

P0 重构后改为正向环境声明："画面 100% 由环境构成，无任何生物存在"。

覆盖：
1. 所有场景（含 crowd / public）的 prompt 都用正向空场景声明；
2. 不允许人物的场景里，最终 prompt 包含正向空环境主体声明；
3. rewriter 输出也会经过正向空场景声明；
4. sanitize retry 也会经过正向空场景声明；
5. safety fallback 也是正向空场景声明；
6. v1 generate_background 不再调用背景人物检测（v2 路径才走 bbox validator）；
7. portrait 和 keyframe 不受影响；
8. forbidden_characters 仍然正常工作。
"""
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))


def _valid_png_bytes() -> bytes:
    output = BytesIO()
    Image.new("RGB", (640, 640), (40, 80, 120)).save(output, format="PNG")
    return output.getvalue()


# ---------- 1. crowd 场景也走正向空场景声明（P0 重构） ----------

def test_crowd_scene_bans_human_subject():
    """所有场景（含 crowd/public）的最终 prompt 必须含正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_no_human_subject(
        "busy market at noon",
        forbidden_characters=None,
        scene_name="market",
    )
    fl = final.lower()
    must_have = [
        # 正向环境声明（P0 重构后取代"不要人物"否定句）
        "纯空环境",
        "无任何生物",
        "角色、人形或生命体",  # "无任何生物、角色、人形或生命体存在" 中的片段
        "画面 100% 由",
    ]
    for w in must_have:
        assert w in final, f"crowd scene missing positive empty-env phrase '{w}': {final}"
    # 不能含旧的"incidental environmental elements"分支
    assert "incidental environmental elements" not in fl
    # 不能含诱导人物主体的"prefer multiple tiny background figures"
    assert "prefer multiple tiny background figures" not in fl


def test_crowd_scene_no_inducement_phrases():
    """crowd 场景不能含 'a guard at the gate' / 'central figure' / 'foreground person' 等诱导词。"""
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_no_human_subject(
        "A guard at the gate stands in the foreground, central figure, facing camera",
        forbidden_characters=None,
        scene_name="palace gate",
    )
    fl = final.lower()
    inducement_phrases = [
        "a guard at the gate",
        "central figure",
        "facing camera",
    ]
    for w in inducement_phrases:
        assert w not in fl, f"inducement phrase '{w}' should be cleaned: {final}"


# ---------- 2. unpopulated 场景含正向空环境主体声明 ----------

def test_unpopulated_scene_has_positive_empty_env():
    """不允许人物的场景，最终 prompt 必须含正向空环境主体声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    unpopulated = ["bedroom", "study room", "secret room", "forest", "courtyard", "ruins", "cave"]
    for scene_name in unpopulated:
        final = svc._enforce_background_no_human_subject(
            f"{scene_name} description",
            forbidden_characters=None,
            scene_name=scene_name,
        )
        assert "纯空环境" in final, f"scene {scene_name}: 正向空环境声明 missing"
        assert "无任何生物" in final, f"scene {scene_name}: 无生物声明 missing"
        assert "画面 100% 由" in final, f"scene {scene_name}: 画面构成声明 missing"


# ---------- 2b. 所有场景都用正向空场景声明 ----------

def test_all_scenes_always_unpopulated():
    """不论 scene_name 是什么，所有背景图最终 prompt 都用正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    all_scenes = [
        "market", "street", "city gate", "palace gate", "military camp",
        "battlefield", "harbor", "banquet hall", "festival square",
        "朝堂", "集市", "街道", "城门", "宫门", "军营", "战场",
        "bedroom", "study room", "secret room", "forest", "courtyard",
        "卧室", "书房", "密室", "山林", "庭院",
    ]
    for scene_name in all_scenes:
        final = svc._enforce_background_no_human_subject(
            f"{scene_name} description",
            forbidden_characters=None,
            scene_name=scene_name,
        )
        fl = final.lower()
        assert "纯空环境" in final, \
            f"scene {scene_name!r} must have positive empty-env declaration (P0 policy): {final}"
        assert "无任何生物" in final, f"scene {scene_name!r}: 无生物声明 missing"
        assert "incidental environmental elements" not in fl, \
            f"scene {scene_name!r}: should NOT have 'incidental environmental elements' anymore: {final}"


# ---------- 3. rewriter final_prompt 经过 enforce ----------

@pytest.mark.asyncio
async def test_final_prompt_path_passes_through_positive_empty_env(monkeypatch):
    """generate_background(final_prompt=...) 路径最终送 API 的 prompt 含正向空场景声明。"""
    from app.services import image_generation_service as mod

    captured = []

    async def fake_call(prompt, *args, **kwargs):
        captured.append(prompt)
        return _valid_png_bytes()

    svc = mod.image_generation_service
    original = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(mod, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key-nohuman")
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)

    try:
        await svc.generate_background(
            scene_name=f"market_{id(captured)}",
            scene_description="busy market",
            mood="day",
            final_prompt=f"market at noon unique_{id(captured)}",
            forbidden_characters=["林夜_nh"],
        )
    finally:
        svc._call_cogview_api = original

    assert len(captured) >= 1
    fl = captured[0].lower()
    # 必须含正向空场景声明（P0 重构后取代否定句）
    assert "纯空环境" in captured[0]
    assert "无任何生物" in captured[0]
    assert "画面 100% 由" in captured[0]
    # 角色名仍被排除
    assert "林夜_nh" in captured[0]


# ---------- 4. sanitize retry 经过 enforce（_build_safety_fallback_background_prompt） ----------

def test_sanitize_retry_path_includes_positive_empty_env():
    """safety fallback prompt 经过 enforce，含正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["林夜", "苏晚晴"],
        scene_name="market",
        scene_description="public market square",
    )
    assert "纯空环境" in fallback
    assert "无任何生物" in fallback
    assert "林夜" in fallback


# ---------- 5. safety fallback 默认无人也是正向空场景声明 ----------

def test_safety_fallback_positive_empty_env():
    """safety fallback 不传 scene_name 时也含正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    fallback = svc._build_safety_fallback_background_prompt(forbidden_characters=None)
    # 默认仍是正向空场景声明
    assert "纯空环境" in fallback
    assert "无任何生物" in fallback


# ---------- 6. v1 generate_background 不调用 validator（v2 路径才走 bbox） ----------

@pytest.mark.asyncio
async def test_generate_background_v1_does_not_call_validator(monkeypatch):
    """v1 generate_background 只生成一次并直接返回，不调用 validator（v2 路径才有 bbox 校验）。"""
    from app.services import image_generation_service as mod

    call_count = {"gen": 0}

    async def fake_call(prompt, *args, **kwargs):
        call_count["gen"] += 1
        return _valid_png_bytes()

    svc = mod.image_generation_service
    original_call = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(mod, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key-retry")

    try:
        with patch(
            "app.services.background_image_validator_service.BackgroundImageValidatorService.validate",
            side_effect=AssertionError("validator must not be called by v1 path"),
        ) as validate_mock:
            result = await svc.generate_background(
                scene_name=f"market_no_validator_{id(call_count)}",
                scene_description="busy market",
                mood="day",
                final_prompt=f"market unique_{id(call_count)}",
                forbidden_characters=["林夜_retry"],
            )
    finally:
        svc._call_cogview_api = original_call

    assert result["success"] is True
    assert call_count["gen"] == 1
    assert validate_mock.call_count == 0
    assert result["validation_skipped"] is True


# ---------- 7. portrait / keyframe 不受影响 ----------

def test_portrait_and_keyframe_do_not_call_enforce():
    """generate_portrait / generate_keyframe 不应该调用 enforce 函数。"""
    from app.services import image_generation_service as mod
    import inspect
    src_portrait = inspect.getsource(mod.image_generation_service.generate_portrait)
    src_keyframe = inspect.getsource(mod.image_generation_service.generate_keyframe)
    assert "_enforce_background_no_human_subject" not in src_portrait
    assert "_enforce_background_no_human_subject" not in src_keyframe
    assert "_enforce_background_environment_focus" not in src_portrait
    assert "_enforce_background_environment_focus" not in src_keyframe


def test_portrait_prompt_does_not_contain_no_human_subject():
    """portrait prompt builder 不会输出 no-human-subject ban。"""
    from app.services.image_generation_service import image_generation_service as svc
    p = svc._build_portrait_prompt(
        appearance_prompt="young woman in white mourning hanfu",
        emotion="sad",
        outfit="mourning",
        pose="standing",
        style_prompt="Tang dynasty style",
        forbidden="",
        full_body=False,
    )
    pl = p.lower()
    assert "no human subject" not in pl
    assert "do not depict a single person as the main subject" not in pl


# ---------- 8. forbidden_characters 正常工作 ----------

def test_forbidden_characters_named_cast_exclusion_in_no_human_subject():
    """forbidden_characters 在 enforce 函数里仍然形成 named cast exclusion。"""
    from app.services.image_generation_service import image_generation_service as svc
    final = svc._enforce_background_no_human_subject(
        "imperial court hall",
        forbidden_characters=["林夜", "苏晚晴", "赵峰", "陈墨"],
        scene_name="court hall",
    )
    assert "画面中不得出现这些命名角色" in final
    assert "林夜" in final
    assert "苏晚晴" in final
    assert "赵峰" in final
    assert "陈墨" in final
    # 同时含正向空场景声明（P0 重构）
    assert "纯空环境" in final


# ---------- 9. escalation 强化（P0 重构：level 升级仍是正向措辞） ----------

def test_escalation_level_strengthens_ban():
    """escalation_level=2 应该追加更强的正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    base = svc._enforce_background_no_human_subject(
        "city gate",
        forbidden_characters=None,
        scene_name="city gate",
        escalation_level=0,
    )
    escalated = svc._enforce_background_no_human_subject(
        "city gate",
        forbidden_characters=None,
        scene_name="city gate",
        escalation_level=2,
    )
    bl, el = base.lower(), escalated.lower()
    # base 不含 escalation 短语
    assert "再次强化空场景" not in bl
    # escalated 含
    assert "再次强化空场景" in escalated
    assert "无任何生命体" in escalated


def test_escalation_level_3_strongest():
    """escalation_level=3 应该追加最强正向空场景声明。"""
    from app.services.image_generation_service import image_generation_service as svc
    escalated = svc._enforce_background_no_human_subject(
        "city gate",
        forbidden_characters=None,
        scene_name="city gate",
        escalation_level=3,
    )
    assert "终极空场景声明" in escalated
    assert "纯环境画面" in escalated
