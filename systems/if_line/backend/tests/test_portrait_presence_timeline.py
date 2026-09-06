"""PortraitPresenceTimeline — 在场角色流单元测试。

重点验证：
- enter / exit 正确
- **停止说话 ≠ 退场**
- emotion 变化触发新 entry
- illustration 后下一段 restore entry
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.canonical_character_resolver import (
    CanonicalCharacterResolver, _CharEntry, _normalize, _legacy_md5,
)
from app.services.portrait_presence_timeline import (
    PortraitPresenceTimelineBuilder, ParagraphSpec, ParagraphLineSpec, SceneSpec,
)


def _make_resolver(names, project_id=1):
    r = CanonicalCharacterResolver(db=None, project_id=project_id)
    r._entries = [
        _CharEntry(
            n,
            _legacy_md5(n, project_id),
            tuple(sorted(_normalize(a) for a in aliases if _normalize(a))),
            tuple(aliases),
        )
        for n, aliases in names
    ]
    return r


# ---------- enter / exit ----------

def test_speaker_switch_creates_entries():
    r = _make_resolver([("Alice", []), ("Bob", [])])
    t = PortraitPresenceTimelineBuilder(r).build([
        ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
        ParagraphSpec(2, [ParagraphLineSpec("Bob", "hello")]),
    ])
    reasons = [(e.canonical_name, e.reason) for e in t.expected_entries]
    assert ("Alice", "speaker_switch") in reasons
    assert ("Bob", "speaker_switch") in reasons


def test_characters_present_keeps_silent_character_visible():
    """scene.characters_present 含 Carl 但 Carl 不说话 → Carl 仍 visible + enter entry。"""
    r = _make_resolver([("Alice", []), ("Carl", [])])
    t = PortraitPresenceTimelineBuilder(r).build(
        paragraphs=[
            ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
        ],
        scenes=[
            SceneSpec(
                start_paragraph_index=1, end_paragraph_index=1,
                characters_present=["Alice", "Carl"],
            ),
        ],
    )
    # Carl 应有 enter entry（visible_silent）
    carl_entries = [e for e in t.expected_entries if e.canonical_name == "Carl"]
    assert len(carl_entries) == 1, f"Carl 应作为 silent visible 进入，got {carl_entries}"
    assert carl_entries[0].reason == "enter"


def test_scene_exit_exits_character():
    """scene 显式 characters_exit → 退场。"""
    r = _make_resolver([("Alice", []), ("Bob", [])])
    t = PortraitPresenceTimelineBuilder(r).build(
        paragraphs=[
            ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
            ParagraphSpec(2, [ParagraphLineSpec("Bob", "yo")]),
        ],
        scenes=[
            SceneSpec(1, 1, characters_present=["Alice"]),
            SceneSpec(2, 2, characters_present=["Bob"], characters_exit=["Alice"]),
        ],
    )
    # Alice 在 scene 2 应退场
    last_frame = t.frames[-1]
    assert "Alice" in last_frame.characters_exit, f"Alice 应退场, got {last_frame.characters_exit}"
    assert "Alice" not in last_frame.characters_visible


def test_stopping_speaking_is_not_exit():
    """Alice 说话 → Bob 说话 → Alice 不再说话但仍在场 → Alice 不应被退场。

    关键规则：停止说话 ≠ 退场。
    """
    r = _make_resolver([("Alice", []), ("Bob", [])])
    t = PortraitPresenceTimelineBuilder(r).build(
        paragraphs=[
            ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
            ParagraphSpec(2, [ParagraphLineSpec("Bob", "yo")]),
            ParagraphSpec(3, [ParagraphLineSpec("Bob", "yo again")]),
        ],
        scenes=[
            SceneSpec(1, 3, characters_present=["Alice", "Bob"]),
        ],
    )
    last_frame = t.frames[-1]
    assert "Alice" in last_frame.characters_visible, (
        f"Alice 停止说话后仍应在场, got visible={last_frame.characters_visible}"
    )
    assert "Alice" not in last_frame.characters_exit


# ---------- emotion 变化 ----------

def test_emotion_change_creates_new_entry():
    """同 speaker neutral → angry → 触发 emotion_change entry。"""
    r = _make_resolver([("Alice", [])])
    t = PortraitPresenceTimelineBuilder(r).build([
        ParagraphSpec(1, [ParagraphLineSpec("Alice", "ok", emotion="neutral")]),
        ParagraphSpec(2, [ParagraphLineSpec("Alice", "grrr", emotion="angry")]),
    ])
    reasons = [(e.canonical_name, e.reason, e.emotion) for e in t.expected_entries]
    assert ("Alice", "speaker_switch", "neutral") in reasons
    assert ("Alice", "emotion_change", "angry") in reasons


def test_outfit_change_creates_new_entry():
    """同 speaker 同 emotion 但 outfit 变 → 触发 outfit_change entry。"""
    r = _make_resolver([("Alice", [])])
    t = PortraitPresenceTimelineBuilder(r).build([
        ParagraphSpec(1, [
            ParagraphLineSpec("Alice", "hi", emotion="neutral", outfit="casual"),
        ]),
        ParagraphSpec(2, [
            ParagraphLineSpec("Alice", "hi", emotion="neutral", outfit="formal"),
        ]),
    ])
    reasons = [e.reason for e in t.expected_entries]
    assert "outfit_change" in reasons


# ---------- illustration 后恢复 ----------

def test_illustration_followed_by_restore_entries():
    """Paragraph 2 是 illustration → Paragraph 3 visible 角色全部 restore。"""
    r = _make_resolver([("Alice", []), ("Bob", [])])
    t = PortraitPresenceTimelineBuilder(r).build([
        ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
        ParagraphSpec(2, [], has_illustration_action=True),
        ParagraphSpec(3, [ParagraphLineSpec("Bob", "yo")]),
    ])
    # paragraph 3 应该有 restore_after_illustration entries
    p3_entries = [e for e in t.expected_entries if e.paragraph_index == 3]
    restore_entries = [e for e in p3_entries if e.reason == "restore_after_illustration"]
    # 至少 Alice 应该 restore（paragraph 1 之后 Alice 一直 visible，paragraph 2 是 illustration）
    restored_names = {e.canonical_name for e in restore_entries}
    assert "Alice" in restored_names, (
        f"Alice 应在 illustration 后恢复, got restore={restored_names}"
    )


# ---------- enter (从 scene.characters_present) ----------

def test_scene_change_brings_new_character_in():
    """scene 切换 + 新 scene characters_present 含新角色 → enter。"""
    r = _make_resolver([("Alice", []), ("Charlie", [])])
    t = PortraitPresenceTimelineBuilder(r).build(
        paragraphs=[
            ParagraphSpec(1, [ParagraphLineSpec("Alice", "hi")]),
            ParagraphSpec(2, [ParagraphLineSpec("Alice", "hello Charlie")]),
        ],
        scenes=[
            SceneSpec(1, 1, characters_present=["Alice"]),
            SceneSpec(2, 2, characters_present=["Alice", "Charlie"]),
        ],
    )
    charlie_enter = [
        e for e in t.expected_entries
        if e.canonical_name == "Charlie" and e.reason == "enter"
    ]
    assert len(charlie_enter) >= 1


def test_narrator_does_not_create_entries():
    """旁白 line 不应触发 speaker_switch / enter。"""
    r = _make_resolver([("Alice", [])])
    t = PortraitPresenceTimelineBuilder(r).build([
        ParagraphSpec(1, [
            ParagraphLineSpec("旁白", "天空晴朗"),
            ParagraphLineSpec("Alice", "hi"),
            ParagraphLineSpec("旁白", "突然起风"),
        ]),
    ])
    # 应该只有 1 个 entry (Alice 的 speaker_switch)
    alice_entries = [e for e in t.expected_entries if e.canonical_name == "Alice"]
    assert len(alice_entries) == 1
