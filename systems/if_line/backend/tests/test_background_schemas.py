"""
L2.08 / L2.09 — Stage_Background_AR schema validation tests.

Covers:
- BackgroundSceneSpec field validators (scene_selector pattern, env_description min/max length,
  camera_shot_type Literal, style_tag dimension coverage)
- PeoplePolicy mode Literal rejection of invalid values
- BBox geometry validation
- BackgroundPromptData plot-leak detection
"""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from pydantic import ValidationError

from app.schemas import (
    BackgroundPromptData,
    BackgroundSceneSpec,
    BBox,
    PeoplePolicy,
    StyleTag,
)


# -------------------- helpers --------------------

def _long_env(n_extra: int = 0) -> str:
    base = (
        "废弃的老式火车站站台，狭长湿漉的月台地面反射冷蓝色雨夜光线，"
        "锈蚀的站牌、剥落的墙面、旧式长椅、积水的轨道和远处模糊的灯箱共同构成主要视觉信息。"
        "空气里有细雨和薄雾，远处城市微光极弱，空间显得空旷、寒冷、无人使用已久。"
        "地面凹凸不平，墙面斑驳，潮湿蔓延到每个角落，时间在这里凝固。"
    )
    if n_extra > 0:
        base += "细节" * n_extra
    return base


def _valid_spec_kwargs(**overrides):
    base = dict(
        scene_id="s1",
        scene_name="夜雨旧站台",
        scene_selector="old_station__night_rain__empty",
        scene_type="abandoned_void",
        environment_description=_long_env(),
        lighting="冷蓝色雨夜光线",
        atmosphere="空旷、寒冷、无人使用已久",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(
            mode="empty_required",
            rationale="文本提到空无一人、深夜、废墟"
        ),
        style_tags=[
            StyleTag(dimension="art_style", value="写实国风电影感"),
            StyleTag(dimension="color_palette", value="雨夜蓝黑"),
            StyleTag(dimension="lens_or_camera_feel", value="24mm广角 establishing shot"),
        ],
        forbidden_characters=["林夜", "Lin Ye"],
    )
    base.update(overrides)
    return base


# -------------------- L2.08 BackgroundSceneSpec validators --------------------

def test_scene_spec_happy_path():
    spec = BackgroundSceneSpec(**_valid_spec_kwargs())
    assert spec.scene_selector == "old_station__night_rain__empty"
    assert spec.people_policy.mode == "empty_required"
    assert len(spec.style_tags) == 3


def test_scene_spec_rejects_non_ascii_selector():
    with pytest.raises(ValidationError) as exc:
        BackgroundSceneSpec(**_valid_spec_kwargs(scene_selector="夜雨站台"))
    assert "scene_selector" in str(exc.value)


def test_scene_spec_rejects_selector_uppercase():
    with pytest.raises(ValidationError):
        BackgroundSceneSpec(**_valid_spec_kwargs(scene_selector="Old_Station"))


def test_scene_spec_rejects_selector_leading_dash():
    with pytest.raises(ValidationError):
        BackgroundSceneSpec(**_valid_spec_kwargs(scene_selector="-bad"))


def test_scene_spec_rejects_short_env_description():
    with pytest.raises(ValidationError) as exc:
        BackgroundSceneSpec(**_valid_spec_kwargs(environment_description="太短"))
    assert "environment_description" in str(exc.value)


def test_scene_spec_rejects_invalid_camera_shot_type():
    with pytest.raises(ValidationError) as exc:
        BackgroundSceneSpec(**_valid_spec_kwargs(camera_shot_type="extreme_closeup"))
    assert "camera_shot_type" in str(exc.value)


def test_scene_spec_rejects_invalid_scene_type():
    with pytest.raises(ValidationError):
        BackgroundSceneSpec(**_valid_spec_kwargs(scene_type="invalid_type"))


def test_scene_spec_rejects_style_tag_dim_coverage_lt_3():
    with pytest.raises(ValidationError) as exc:
        BackgroundSceneSpec(**_valid_spec_kwargs(
            style_tags=[
                StyleTag(dimension="art_style", value="x"),
                StyleTag(dimension="art_style", value="y"),
            ]
        ))
    assert "style_tags" in str(exc.value).lower() or "维度" in str(exc.value)


def test_scene_spec_accepts_style_tag_dim_coverage_eq_3():
    spec = BackgroundSceneSpec(**_valid_spec_kwargs())
    assert len(spec.style_tags) == 3


# -------------------- L2.09 PeoplePolicy mode enum --------------------

def test_people_policy_accepts_three_modes():
    for mode in ("empty_required", "background_people_optional", "background_groups_required"):
        p = PeoplePolicy(mode=mode, rationale="测试理由文本")
        assert p.mode == mode


def test_people_policy_rejects_invalid_mode():
    with pytest.raises(ValidationError) as exc:
        PeoplePolicy(mode="crowds_welcome", rationale="测试理由文本")
    assert "mode" in str(exc.value)


def test_people_policy_rejects_empty_rationale():
    with pytest.raises(ValidationError):
        PeoplePolicy(mode="empty_required", rationale="   ")


def test_people_policy_rejects_short_rationale():
    with pytest.raises(ValidationError):
        PeoplePolicy(mode="empty_required", rationale="ab")


# -------------------- BBox geometry --------------------

def test_bbox_happy_path():
    b = BBox(x1=0.1, y1=0.2, x2=0.5, y2=0.6, conf=0.8)
    assert 0.15 < b.area < 0.17


def test_bbox_rejects_degenerate():
    with pytest.raises(ValidationError):
        BBox(x1=0.5, y1=0.5, x2=0.4, y2=0.6, conf=0.5)


def test_bbox_rejects_out_of_range():
    with pytest.raises(ValidationError):
        BBox(x1=-0.1, y1=0.0, x2=0.5, y2=0.5, conf=0.5)
    with pytest.raises(ValidationError):
        BBox(x1=0.0, y1=0.0, x2=1.5, y2=0.5, conf=0.5)


def test_bbox_center_region_intersection():
    b = BBox(x1=0.1, y1=0.1, x2=0.3, y2=0.3, conf=0.6)
    assert b.in_center_region(0.2, 0.2, 0.8, 0.8)  # corner touch
    assert b.center_area_within(0.2, 0.2, 0.8, 0.8) > 0.0


# -------------------- BackgroundPromptData plot leak --------------------

def test_prompt_data_rejects_plot_words():
    with pytest.raises(ValidationError):
        BackgroundPromptData(
            scene_name="x",
            scene_selector="test_slug_x",
            environment_description="他说这是一个空房间",
            lighting="冷光",
            atmosphere="静谧",
            camera_shot_type="establishing_wide",
            people_policy_mode="empty_required",
        )


def test_prompt_data_accepts_clean_env():
    pd = BackgroundPromptData(
        scene_name="x",
        scene_selector="test_slug_x",
        environment_description="一个空旷的房间，墙上有旧画，地面潮湿",
        lighting="冷光",
        atmosphere="静谧",
        camera_shot_type="establishing_wide",
        people_policy_mode="empty_required",
    )
    assert pd.scene_selector == "test_slug_x"
