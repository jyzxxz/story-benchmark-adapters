"""
F06 — Stage_Keyframe_Identity_AR_Blueprint E05: KeyframeMoment clothing state.

覆盖：
- KeyframeMoment.get_emotion / get_outfit / get_pose / get_action /
  get_injury_state / get_held_item 按 target_characters 索引返回正确值
- 不存在的角色名 → 空串
- _normalize_state_list 长度对齐 target_characters
- _dict_to_moment 把 dict 还原成完整 KeyframeMoment（含新字段）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.keyframe_moment_selector import (
    KeyframeMoment,
    KeyframeMomentSelector,
)


def _moment_factory() -> KeyframeMoment:
    return KeyframeMoment(
        moment_summary="林夜挥剑",
        target_characters=["林夜", "苏婉"],
        visual_focus="剑光乍现",
        source_excerpt="林夜拔剑",
        rationale="高潮",
        character_emotions=["愤怒", "惊恐"],
        character_outfits=["玄色长袍", "白衣"],
        character_poses=["侧身挥剑", "后退"],
        character_actions=["斩落", "惊呼"],
        character_injury_states=["左臂渗血", "无"],
        character_held_items=["寒霜剑", "空手"],
    )


def test_get_emotion_returns_aligned_value():
    m = _moment_factory()
    assert m.get_emotion("林夜") == "愤怒"
    assert m.get_emotion("苏婉") == "惊恐"


def test_get_outfit_returns_aligned_value():
    m = _moment_factory()
    assert m.get_outfit("林夜") == "玄色长袍"
    assert m.get_outfit("苏婉") == "白衣"


def test_get_pose_returns_aligned_value():
    m = _moment_factory()
    assert m.get_pose("林夜") == "侧身挥剑"


def test_get_action_returns_aligned_value():
    m = _moment_factory()
    assert m.get_action("林夜") == "斩落"


def test_get_injury_state_returns_aligned_value():
    m = _moment_factory()
    assert m.get_injury_state("林夜") == "左臂渗血"


def test_get_held_item_returns_aligned_value():
    m = _moment_factory()
    assert m.get_held_item("苏婉") == "空手"


def test_unknown_character_returns_empty():
    m = _moment_factory()
    assert m.get_emotion("路人甲") == ""
    assert m.get_outfit("路人甲") == ""


def test_no_state_lists_returns_empty():
    m = KeyframeMoment(
        moment_summary="x",
        target_characters=["甲"],
        visual_focus="",
        source_excerpt="",
        rationale="",
    )
    assert m.get_emotion("甲") == ""
    assert m.get_outfit("甲") == ""


def test_normalize_state_list_pads_to_name_count():
    out = KeyframeMomentSelector._normalize_state_list(
        ["a"], ["甲", "乙", "丙"],
    )
    assert out == ["a", "", ""]


def test_normalize_state_list_truncates_to_name_count():
    out = KeyframeMomentSelector._normalize_state_list(
        ["a", "b", "c"], ["甲"],
    )
    assert out == ["a"]


def test_normalize_state_list_none_returns_none():
    out = KeyframeMomentSelector._normalize_state_list(None, ["甲"])
    assert out is None


def test_dict_to_moment_carries_clothing_state():
    d = {
        "moment_summary": "x",
        "target_characters": ["甲"],
        "visual_focus": "",
        "source_excerpt": "",
        "rationale": "",
        "character_emotions": ["愤怒"],
        "character_outfits": ["甲胄"],
        "character_poses": ["挥剑"],
        "character_actions": ["斩"],
        "character_injury_states": ["无"],
        "character_held_items": ["剑"],
    }
    m = KeyframeMomentSelector._dict_to_moment(d, ["甲"])
    assert m.get_emotion("甲") == "愤怒"
    assert m.get_outfit("甲") == "甲胄"
    assert m.get_pose("甲") == "挥剑"
    assert m.get_action("甲") == "斩"
    assert m.get_injury_state("甲") == "无"
    assert m.get_held_item("甲") == "剑"


def test_dict_to_moment_handles_missing_state_fields():
    d = {"moment_summary": "x", "target_characters": ["甲"]}
    m = KeyframeMomentSelector._dict_to_moment(d, ["甲"])
    assert m.get_emotion("甲") == ""
