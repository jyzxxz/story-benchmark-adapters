"""
大纲 schema 字段归一化测试。

历史 bug: LLM prompt 返回 scene_locations/emotion_shift，但 ORM 字段是 scene/emotion，
导致 DB 里这两个字段全是 None。修复后 ChapterOutlineCreate 用 model_validator 自动归一化。
"""
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import ChapterOutlineCreate


def test_outline_schema_accepts_legacy_scene_locations():
    """LLM 给 scene_locations 数组 → 归一化成 scene 取第一个元素。"""
    raw = {
        "chapter_index": 1,
        "title": "测试章",
        "summary": "林夜在实验室",
        "scene_locations": ["实验室", "废弃机甲仓库"],
        "emotion_shift": "从自信到震惊",
    }
    obj = ChapterOutlineCreate(**raw)
    assert obj.scene == "实验室"
    assert obj.emotion == "从自信到震惊"


def test_outline_schema_keeps_explicit_scene_emotion():
    """LLM 直接给 scene/emotion 字段 → 不被覆盖。"""
    raw = {
        "chapter_index": 1,
        "title": "测试章",
        "summary": "...",
        "scene": "皇宫",
        "emotion": "tense",
        "scene_locations": ["should", "not", "win"],
    }
    obj = ChapterOutlineCreate(**raw)
    assert obj.scene == "皇宫"
    assert obj.emotion == "tense"


def test_outline_schema_falls_back_to_summary_when_no_scene():
    """没有 scene_locations 也没有 scene → 用 summary 兜底。"""
    raw = {
        "chapter_index": 1,
        "title": "测试",
        "summary": "这是章节摘要",
    }
    obj = ChapterOutlineCreate(**raw)
    assert obj.scene == "这是章节摘要"
    assert obj.emotion == "这是章节摘要"


def test_outline_schema_handles_empty_scene_locations_list():
    """scene_locations 是空数组 → 不崩，继续走兜底。"""
    raw = {
        "chapter_index": 1,
        "title": "测试",
        "summary": "摘要内容",
        "scene_locations": [],
        "emotion_shift": "",
    }
    obj = ChapterOutlineCreate(**raw)
    # scene_locations 空 → 用 summary 兜底
    assert obj.scene == "摘要内容"
    # emotion_shift 空 → 用 summary 兜底
    assert obj.emotion == "摘要内容"


def test_outline_schema_truncates_long_summary():
    """summary 过长时截断到 60 字。"""
    raw = {
        "chapter_index": 1,
        "title": "测试",
        "summary": "一二三四五" * 30,  # 150 字
    }
    obj = ChapterOutlineCreate(**raw)
    assert obj.scene is not None
    assert len(obj.scene) <= 60
    assert len(obj.emotion) <= 60


def test_outline_schema_handles_first_empty_in_locations():
    """scene_locations 第一个是空字符串 → 跳过空值，取第一个非空。"""
    raw = {
        "chapter_index": 1,
        "title": "测试",
        "summary": "用这个",
        "scene_locations": ["", "实际场景"],
    }
    obj = ChapterOutlineCreate(**raw)
    # 跳过空，取第二个非空 location
    assert obj.scene == "实际场景"


def test_outline_schema_handles_all_empty_locations():
    """scene_locations 全是空 → 走 summary 兜底。"""
    raw = {
        "chapter_index": 1,
        "title": "测试",
        "summary": "用这个",
        "scene_locations": ["", "", None],
    }
    obj = ChapterOutlineCreate(**raw)
    assert obj.scene == "用这个"
