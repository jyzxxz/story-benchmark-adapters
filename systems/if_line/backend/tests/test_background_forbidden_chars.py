"""
L5.06 — Stage_Background_AR 禁用命名角色覆盖测试。

验证 forbidden_characters 在所有路径都正确注入到 ban segment，并且：
1. 不出现在正向描述里
2. 中文 + 英文别名双版本都被排除
3. 多次调用 enforce 不重复
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.image_generation_service import ImageGenerationService


def _make_spec(forbidden: list) -> BackgroundSceneSpec:
    env = "废弃站台，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="测试场景",
        scene_selector="test_slug_forbidden__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷光",
        atmosphere="萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_characters=forbidden,
    )


@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


# -------------------- L5.06 normal + rewriter + sanitize --------------------

def test_forbidden_chars_normal_path(svc):
    spec = _make_spec(["林夜", "Lin Ye", "苏晚晴", "Su Wanqing"])
    out = svc._enforce_background_environment_focus("abandoned platform", spec=spec)
    assert "林夜" in out
    assert "Lin Ye" in out
    assert "苏晚晴" in out
    assert "Su Wanqing" in out


def test_forbidden_chars_only_in_ban_segment(svc):
    """中文角色名只出现在 ban segment（中英两段共两次）"""
    spec = _make_spec(["林夜", "Lin Ye"])
    out = svc._enforce_background_environment_focus("abandoned platform", spec=spec)
    # Bilingual ban block emits the name once per language (zh + en)
    assert out.count("林夜") == 2
    ban_idx = out.find("禁用命名角色") if "禁用命名角色" in out else out.find("Never depict")
    assert ban_idx >= 0


def test_forbidden_chars_rewriter_path(svc):
    from app.services.prompt_rewriter_service import RewrittenPrompt
    rp = RewrittenPrompt(
        subject="abandoned station",
        details=["rusty tracks"],
        lighting="cool",
        composition="wide",
        style="cinematic",
    )
    spec = _make_spec(["赵峰", "Zhao Feng"])
    out = svc._enforce_background_environment_focus(rp.to_cogview_prompt(), spec=spec)
    assert "赵峰" in out
    assert "Zhao Feng" in out


def test_forbidden_chars_safety_fallback(svc):
    spec = _make_spec(["林夜", "Lin Ye"])
    out, _ = svc._scene_aware_safety_fallback(spec)
    assert "林夜" in out
    assert "Lin Ye" in out


def test_forbidden_chars_idempotent(svc):
    """多次调用 enforce，forbidden chars 不重复（保持中英两段）"""
    spec = _make_spec(["林夜", "Lin Ye"])
    out1 = svc._enforce_background_environment_focus("p", spec=spec)
    out2 = svc._enforce_background_environment_focus(out1, spec=spec)
    # Bilingual ban block emits the name once per language (zh + en)
    assert out1.count("林夜") == out2.count("林夜") == 2
    assert len(out1) == len(out2)


def test_forbidden_chars_format_helper(svc):
    """_format_named_cast_exclusion_spec 同时输出中文+英文"""
    text = svc._format_named_cast_exclusion_spec(["林夜", "Lin Ye", "苏晚晴"])
    assert "林夜" in text
    assert "Lin Ye" in text
    assert "苏晚晴" in text


def test_forbidden_chars_mixed_scripts_handled(svc):
    """混合中英文别名都进入 ban"""
    spec = _make_spec(["林夜", "阿夜", "Lin Ye", "night"])
    out = svc._enforce_background_environment_focus("p", spec=spec)
    for n in ["林夜", "阿夜", "Lin Ye", "night"]:
        assert n in out
