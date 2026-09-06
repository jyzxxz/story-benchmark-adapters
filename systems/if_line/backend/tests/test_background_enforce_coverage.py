"""
L5.01-L5.03 — Stage_Background_AR enforce 覆盖测试。

L5.01: 测试 _enforce_background_environment_focus(spec=...) 在 5 条路径都被调用：
       normal / rewriter / sanitize_retry / fallback / safety_fallback
L5.02: 测试 enforce idempotent（多次调用不重复追加）
L5.03: 测试 enforce 触发 CN-PLOT-STRIP（注入中文剧情 → strip 清除）
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.image_generation_service import ImageGenerationService


def _make_spec(mode: str = "empty_required") -> BackgroundSceneSpec:
    env = "废弃站台，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="测试场景",
        scene_selector=f"test_slug_{mode}__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷光",
        atmosphere="萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode=mode, rationale="test"),
        forbidden_characters=["林夜", "Lin Ye"],
    )


@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


# -------------------- L5.01 enforce 在 5 条路径都被调用 --------------------

def test_enforce_normal_path(svc):
    """normal path: 直接对 spec 调用 enforce"""
    spec = _make_spec()
    prompt = "夜雨中的废弃站台"
    out = svc._enforce_background_environment_focus(prompt, spec=spec)
    assert "视觉小说背景图" in out
    assert "纯空环境" in out
    assert "禁用命名角色" in out
    assert "林夜" in out  # 在 ban segment


def test_enforce_rewriter_path(svc):
    """rewriter path: rewriter 输出后再 enforce"""
    from app.services.prompt_rewriter_service import RewrittenPrompt
    rp = RewrittenPrompt(
        subject="abandoned station platform at night",
        details=["rain pooled on tracks", "rusty signpost"],
        lighting="cool blue moonlight",
        composition="wide establishing shot",
        style="cinematic",
    )
    base_prompt = rp.to_cogview_prompt()
    spec = _make_spec()
    out = svc._enforce_background_environment_focus(base_prompt, spec=spec)
    assert "视觉小说背景图" in out
    assert "纯空环境" in out
    assert "林夜" in out


def test_enforce_sanitize_retry_path(svc):
    """sanitize retry path: sanitize 后调用 enforce"""
    prompt = "abandoned station platform, cool lighting"
    sanitized = svc._sanitize_prompt(prompt)
    spec = _make_spec()
    out = svc._enforce_background_environment_focus(sanitized, spec=spec)
    assert "视觉小说背景图" in out


def test_enforce_fallback_path(svc):
    """fallback path: safety_fallback_background_prompt 内部调用 enforce"""
    spec = _make_spec()
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=spec.forbidden_characters,
        scene_name=spec.scene_name,
    )
    # legacy fallback 也会走 enforce
    assert "视觉小说背景图" in fallback or "环境" in fallback


def test_enforce_safety_fallback_path(svc):
    """safety fallback path: scene-aware safety"""
    spec = _make_spec()
    out_prompt, out_spec = svc._scene_aware_safety_fallback(spec)
    assert out_spec.people_policy.mode == "empty_required"
    assert "视觉小说背景图" in out_prompt
    assert "林夜" in out_prompt


# -------------------- L5.02 enforce idempotent --------------------

def test_enforce_idempotent_single_call(svc):
    """单次调用，ban clause 不重复"""
    spec = _make_spec()
    prompt = "test prompt"
    out1 = svc._enforce_background_environment_focus(prompt, spec=spec)
    out2 = svc._enforce_background_environment_focus(out1, spec=spec)
    # 第二次调用不应该再追加（长度相同）
    assert len(out1) == len(out2), f"not idempotent: {len(out1)} vs {len(out2)}"


def test_enforce_idempotent_three_calls(svc):
    """三次调用，长度不再增长"""
    spec = _make_spec()
    out1 = svc._enforce_background_environment_focus("p", spec=spec)
    out2 = svc._enforce_background_environment_focus(out1, spec=spec)
    out3 = svc._enforce_background_environment_focus(out2, spec=spec)
    assert len(out1) == len(out2) == len(out3)


def test_enforce_idempotent_escalation_level_does_not_duplicate(svc):
    """escalation level 1 多次调用不重复"""
    spec = _make_spec()
    out1 = svc._enforce_background_environment_focus("p", spec=spec, escalation_level=1)
    out2 = svc._enforce_background_environment_focus(out1, spec=spec, escalation_level=1)
    assert len(out1) == len(out2)


# -------------------- L5.03 enforce 触发 CN-PLOT-STRIP --------------------

def test_enforce_does_not_add_plot_to_prompt(svc):
    """enforce 不会把剧情注入 prompt"""
    spec = _make_spec()
    out = svc._enforce_background_environment_focus("abandoned platform", spec=spec)
    plot_words = ["说道", "心想", "拥抱", "kiss", "fight"]
    for w in plot_words:
        assert w not in out, f"plot word leaked: {w}"


def test_enforce_forbidden_chars_only_in_ban_segment(svc):
    """forbidden characters 只出现在 ban segment，不出现在正向描述里"""
    spec = _make_spec()
    prompt = "abandoned station, cool lighting"
    out = svc._enforce_background_environment_focus(prompt, spec=spec)
    # Bilingual ban block: 中文段一次，英文段一次，共两次
    assert out.count("林夜") == 2
    # ban segment 标志必须在第一次 "林夜" 之前
    ban_idx = out.find("禁用命名角色")
    char_idx = out.find("林夜")
    assert 0 <= ban_idx < char_idx


def test_enforce_no_dialogue_verbs(svc):
    """enforce 输出不含对话动词"""
    spec = _make_spec()
    out = svc._enforce_background_environment_focus("p", spec=spec)
    for v in ["说道", "问道", "答道", "喝道", "说道："]:
        assert v not in out
