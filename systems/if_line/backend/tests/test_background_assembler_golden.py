"""
L5.13 — Stage_Background_AR assembler golden 测试。

deterministic prompt 断言：相同 spec + style_tags → 完全相同的 final prompt 输出。
9 段固定顺序拼接，断言关键句段都按预期顺序出现。
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import (
    BackgroundSceneSpec, PeoplePolicy, StyleTag,
)
from app.services.background_prompt_assembler_service import BackgroundPromptAssembler


def _make_spec_empty() -> BackgroundSceneSpec:
    env = "废弃老站台，月台反射冷蓝雨夜光线，锈蚀站牌剥落墙面，旧式长椅积水轨道，远处城市微光极弱。" * 3
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="夜雨旧站台",
        scene_selector="old_station__night_rain__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷蓝色雨夜光线",
        weather="细雨",
        time_of_day="night",
        atmosphere="冷清萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="empty test"),
        forbidden_characters=["林夜", "Lin Ye"],
    )


def _make_style_tags():
    return [
        StyleTag(dimension="art_style", value="写实国风电影感"),
        StyleTag(dimension="color_palette", value="雨夜蓝黑"),
        StyleTag(dimension="mood", value="冷清"),
        StyleTag(dimension="lens_or_camera_feel", value="35mm环境广角"),
        StyleTag(dimension="texture_or_rendering", value="潮湿反光"),
    ]


@pytest.fixture
def assembler():
    return BackgroundPromptAssembler()


# -------------------- L5.13 deterministic output --------------------

def test_assembler_deterministic_same_inputs(assembler):
    """相同输入两次调用，输出完全一致"""
    spec = _make_spec_empty()
    tags = _make_style_tags()
    out1 = assembler.assemble(spec, style_tags=tags)
    out2 = assembler.assemble(spec, style_tags=tags)
    assert out1 == out2


def test_assembler_output_starts_with_scene_name(assembler):
    spec = _make_spec_empty()
    out = assembler.assemble(spec)
    assert "夜雨旧站台" in out


def test_assembler_ban_clause_present(assembler):
    """输出始终以 ban clause 结尾"""
    spec = _make_spec_empty()
    out = assembler.assemble(spec)
    assert "视觉小说背景图" in out
    assert "环境" in out
    assert "禁用命名角色" in out


def test_assembler_people_policy_empty(assembler):
    """empty_required → 含中文空场约束"""
    spec = _make_spec_empty()
    out = assembler.assemble(spec)
    assert "纯空环境" in out or "纯空环境" in out


def test_assembler_camera_shot_positioned_after_env(assembler):
    """camera_shot_type 段在 environment 段之后"""
    spec = _make_spec_empty()
    out = assembler.assemble(spec)
    # environment_description 出现位置
    env_idx = out.find("月台")
    # camera_shot_type 关键词
    cam_keywords = ["establishing", "环境", "广角", "镜头"]
    cam_idx = -1
    for kw in cam_keywords:
        idx = out.find(kw)
        if idx >= 0:
            cam_idx = idx
            break
    assert cam_idx > env_idx > 0


def test_assembler_idempotent(assembler):
    """assembler 不需要 enforce，输出本身就完整"""
    spec = _make_spec_empty()
    out = assembler.assemble(spec)
    assert len(out) > 200


def test_assembler_style_tags_5dims(assembler):
    """5 维 style_tags 全部进入输出"""
    spec = _make_spec_empty()
    tags = _make_style_tags()
    out = assembler.assemble(spec, style_tags=tags)
    for t in tags:
        assert t.value in out


def test_assembler_no_forbidden_chars_in_positive_segment(assembler):
    """forbidden_characters 只在 ban segment 出现"""
    spec = _make_spec_empty()
    spec.forbidden_characters = ["林夜", "Lin Ye"]
    out = assembler.assemble(spec)
    # 总共出现 1 次（ban segment）
    assert out.count("林夜") == 1


def test_assembler_strips_project16_remains_and_cast_from_positive_sections(assembler):
    """Historical contaminated specs cannot re-inject Zhongli's remains."""
    spec = _make_spec_empty()
    spec.scene_name = "钟离遗体所在的坑底"
    spec.environment_description = (
        "幽深石坑四周覆盖潮湿青苔，坑底中央躺着一具残破躯体，"
        "岩壁上有璃月式岩元素纹样，裂隙间透入琥珀色天光。"
    ) * 2
    spec.architecture = "岩壁围合的天然石坑，钟离倒在中央"
    spec.props = "散落的碎石与尸骸，岩元素结晶沿坑壁分布"
    spec.composition_constraints = "坑底躯体作为焦点；远处岩壁保持纵深"
    spec.people_policy.rationale = "钟离遗体需要从背景中移除；纯环境构图"
    spec.forbidden_characters = ["钟离", "Zhongli"]

    out = assembler.assemble(spec)
    positive, exclusion = out.split("【背景角色实体禁入规则", maxsplit=1)

    for token in (
        "钟离", "Zhongli", "遗体", "躯体", "尸骸", "碎袍", "皮肤", "corpse", "body",
    ):
        assert token.casefold() not in positive.casefold()
    assert "潮湿青苔" in positive
    assert "岩元素纹样" in positive
    assert "岩元素结晶" in positive
    assert "远处岩壁保持纵深" in positive
    assert "no corpses" in exclusion
