"""
L5.05 — Stage_Background_AR 剧情不泄漏测试。

验证 final background prompt 中不包含剧情性内容：
- 对白动词 (说道/问道/拥抱/亲吻)
- 角色心理动词 (心想/暗想/ decided to)
- 动作冲突 (打架/战斗/扑向)
- 剧情性叙述连词 (于是/然后/接着)

测试覆盖 4 条路径：normal / rewriter / sanitize_retry / safety_fallback。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.image_generation_service import ImageGenerationService
from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService


def _make_spec() -> BackgroundSceneSpec:
    env = "废弃站台，" * 30
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="测试场景",
        scene_selector="test_slug_no_plot__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷光",
        atmosphere="萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
    )


@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


PLOT_WORDS_ZH = [
    "说道", "问道", "答道", "喝道", "怒道",
    "心想", "暗想", "决定",
    "拥抱", "亲吻", "kiss", "hug",
    "fight", "打架", "扑向", "拔剑",
    "于是", "然后", "接着",
]


def _assert_no_plot(text: str):
    for w in PLOT_WORDS_ZH:
        assert w not in text.lower(), f"plot word leaked: {w}"


# -------------------- L5.05 normal path --------------------

def test_no_plot_normal_path(svc):
    """normal path: enforce 不引入剧情"""
    spec = _make_spec()
    out = svc._enforce_background_environment_focus("abandoned station", spec=spec)
    _assert_no_plot(out)


def test_no_plot_rewriter_path(svc):
    """rewriter path: rewriter 输出后再 enforce，剧情不残留"""
    from app.services.prompt_rewriter_service import RewrittenPrompt
    rp = RewrittenPrompt(
        subject="abandoned platform",
        details=["rusty tracks", "rain"],
        lighting="cool blue",
        composition="wide shot",
        style="cinematic",
    )
    base = rp.to_cogview_prompt()
    spec = _make_spec()
    out = svc._enforce_background_environment_focus(base, spec=spec)
    _assert_no_plot(out)


def test_no_plot_sanitize_retry_path(svc):
    """sanitize retry path: sanitize 后 enforce"""
    sanitized = svc._sanitize_prompt("abandoned station with cool lighting")
    spec = _make_spec()
    out = svc._enforce_background_environment_focus(sanitized, spec=spec)
    _assert_no_plot(out)


def test_no_plot_safety_fallback_path(svc):
    """safety fallback path: scene-aware safety"""
    spec = _make_spec()
    out, _ = svc._scene_aware_safety_fallback(spec)
    _assert_no_plot(out)


def test_no_plot_legacy_safety_fallback(svc):
    """legacy safety fallback builder 内部也调用 enforce"""
    fallback = svc._build_safety_fallback_background_prompt(
        forbidden_characters=["林夜"],
        scene_name="测试场景",
    )
    _assert_no_plot(fallback)


# -------------------- L5.05 plot-contamination rejection --------------------

def test_plot_contamination_raises():
    """BackgroundSceneSpec.environment_description 注入剧情 → model validator 拒绝"""
    from app.schemas import PlotContaminationError
    plot_words = ["他说：你走吧", "拥抱", "心想：完了"]
    for w in plot_words:
        env = "废弃站台 " * 30
        contaminated = env + " " + w + " 环境补充" * 5
        with pytest.raises(PlotContaminationError):
            BackgroundSceneSpec(
                scene_id="s1",
                scene_name="测试",
                scene_selector="test_plot_leak__01",
                scene_type="abandoned_void",
                environment_description=contaminated,
                lighting="冷光",
                atmosphere="萧索",
                camera_shot_type="establishing_wide",
                people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
            )


def test_no_plot_assembler_output():
    """BackgroundPromptAssembler 输出不含剧情"""
    from app.services.background_prompt_assembler_service import BackgroundPromptAssembler
    spec = _make_spec()
    out = BackgroundPromptAssembler().assemble(spec)
    _assert_no_plot(out)


def test_analyzer_cleans_remains_from_all_positive_spec_fields():
    analyzer = BackgroundSceneAnalyzerService()
    raw = {
        "scene_id": "pit",
        "scene_name": "发现钟离遗体的坑底",
        "scene_type": "wilderness_dreamscape",
        "environment_description": (
            "坑壁布满潮湿青苔，坑底中央躺着一具残破躯体，"
            "璃月岩元素纹样沿石壁延伸，琥珀色微光穿过裂缝。"
        ) * 2,
        "architecture": "天然石坑，钟离位于中央",
        "props": "碎石与尸体，岩元素结晶",
        "lighting": "琥珀色微光",
        "time_of_day": "unknown",
        "atmosphere": "钟离死后形成的凝滞感；洞穴潮湿幽深",
        "camera_shot_type": "establishing_wide",
        "composition_constraints": "坑底遗体作为焦点；石壁形成纵深",
        "people_policy": {
            "mode": "empty_required",
            "rationale": "钟离遗体不应成为背景主体；纯环境",
        },
    }

    specs = analyzer._build_specs([raw], ["钟离", "Zhongli"])

    assert len(specs) == 1
    spec = specs[0]
    positive_fields = " ".join(
        str(value or "")
        for value in (
            spec.scene_name,
            spec.environment_description,
            spec.architecture,
            spec.props,
            spec.lighting,
            spec.weather,
            spec.atmosphere,
            spec.composition_constraints,
        )
    )
    for token in ("钟离", "Zhongli", "遗体", "躯体", "尸体", "body", "remains"):
        assert token.casefold() not in positive_fields.casefold()
    assert "潮湿青苔" in spec.environment_description
    assert "璃月岩元素纹样" in spec.environment_description
    assert "岩元素结晶" in (spec.props or "")
    assert "石壁形成纵深" in (spec.composition_constraints or "")
    assert "钟离" not in spec.people_policy.rationale
    assert "遗体" not in spec.people_policy.rationale
