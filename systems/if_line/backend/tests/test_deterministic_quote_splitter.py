"""DeterministicQuoteSplitter — rules fallback 中文对白切分测试。

验证：
- 中文引号切分（「」/『』/""/''）
- 前导动词抽 speaker（XX 说/道/笑道/喊道）
- 纯 narration 段（无引号）
- 混合段（narration + dialogue）
- 无法抽 speaker 的对白 → "未知角色"，show_portrait=False
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
from app.services.deterministic_quote_splitter import (
    DeterministicQuoteSplitter,
    split_chapter_content,
)


def _resolver_with_names(names, project_id=1):
    r = CanonicalCharacterResolver(db=None, project_id=project_id)
    r._entries = [
        _CharEntry(
            n, _legacy_md5(n, project_id),
            tuple(sorted(_normalize(a) for a in aliases if _normalize(a))),
            tuple(aliases),
        )
        for n, aliases in names
    ]
    return r


# ---------- 引号切分 ----------

def test_single_dialogue_with_speaker_prefix():
    """前导「XX说道：」+ 引号 → 切出 1 个 dialogue + speaker。"""
    r = _resolver_with_names([("李白", [])])
    content = '李白笑道：「你好啊。」'
    lines = split_chapter_content(content, r, 1)
    # 应该只有 1 行 dialogue（speaker 抽取消费前缀）
    assert len(lines) == 1
    assert lines[0].speaker == "李白"
    assert lines[0].text == "你好啊。"
    assert lines[0].show_portrait is True


def test_mixed_narration_and_dialogue():
    """混合段：narration + dialogue + narration。"""
    r = _resolver_with_names([("李白", []), ("杜甫", [])])
    content = '''山道静谧。

李白笑道：「杜兄可好？」

杜甫沉声道：「尚可。」'''
    lines = split_chapter_content(content, r, 1)
    speakers = [l.speaker for l in lines]
    assert "旁白" in speakers  # "山道静谧"
    assert "李白" in speakers
    assert "杜甫" in speakers
    # 不应有 "李白笑道：" 这样的 noise narration
    texts = [l.text for l in lines]
    assert not any("笑道" in t for t in texts), f"noise narration: {texts}"


def test_pure_narration_paragraph():
    """无引号的整段 → 1 条 narration。"""
    r = _resolver_with_names([])
    lines = split_chapter_content("山风吹过，落叶纷飞。", r, 1)
    assert len(lines) == 1
    assert lines[0].speaker == "旁白"
    assert lines[0].show_portrait is False


def test_unknown_speaker_marks_未知角色():
    """speaker 无法从前缀抽出 → '未知角色'，show_portrait=False。"""
    r = _resolver_with_names([])
    # 直接引号开头，没有前缀
    lines = split_chapter_content('「这话是谁说的？」', r, 1)
    assert len(lines) == 1
    assert lines[0].speaker == "未知角色"
    assert lines[0].show_portrait is False


def test_double_quote_pairs_supported():
    """支持 「」/『』/"" /'' 多种引号对。"""
    r = _resolver_with_names([("李白", [])])
    pairs = [
        '李白道：「一」',
        '李白道：‘二’',
        '李白道："三"',
    ]
    for content in pairs:
        lines = split_chapter_content(content, r, 1)
        dialogues = [l for l in lines if l.speaker == "李白"]
        assert len(dialogues) == 1, f"failed on {content}: {[l.speaker for l in lines]}"


def test_paragraph_break_creates_separate_blocks():
    """\\n\\n+ 强制段落分隔。"""
    r = _resolver_with_names([("李白", []), ("杜甫", [])])
    content = '李白道：「一」\n\n杜甫道：「二」'
    lines = split_chapter_content(content, r, 1)
    speakers = [l.speaker for l in lines]
    assert speakers == ["李白", "杜甫"]


def test_empty_content_returns_empty_list():
    r = _resolver_with_names([])
    assert split_chapter_content("", r, 1) == []
    assert split_chapter_content("   ", r, 1) == []


def test_unclosed_quote_takes_rest_as_dialogue():
    """未闭合引号 → 剩余全部作为 dialogue。"""
    r = _resolver_with_names([])
    content = '李白道：「未闭合的对话'
    lines = split_chapter_content(content, r, 1)
    # 至少有一条 dialogue（speaker 可能是未知角色因为没有 entries）
    dialogues = [l for l in lines if l.speaker != "旁白"]
    assert len(dialogues) >= 1


def test_alias_speaker_resolves_to_canonical():
    """SpeakerId 是 alias，resolver 解析后 character_id = canonical 的 cid。"""
    r = _resolver_with_names([("李白", ["太白"])])
    content = '太白笑道：「Hi」'
    lines = split_chapter_content(content, r, 1)
    dialogue_lines = [l for l in lines if l.speaker != "旁白"]
    assert len(dialogue_lines) == 1
    # character_id 应等于李白的 cid（不是太白）
    li_bai_cid = _legacy_md5("李白", 1)
    assert dialogue_lines[0].character_id == li_bai_cid


def test_multiple_quotes_in_one_block():
    """一段内多个引号 → 多条 dialogue。"""
    r = _resolver_with_names([("李白", []), ("杜甫", [])])
    content = '李白道：「你好」杜甫道：「你好」'
    lines = split_chapter_content(content, r, 1)
    speakers = [l.speaker for l in lines]
    assert speakers == ["李白", "杜甫"]


def test_source_span_within_content():
    """source_span 的 (start, end) 应在原 content 范围内且定位正确。"""
    r = _resolver_with_names([("李白", [])])
    content = '李白道：「你好」'
    lines = split_chapter_content(content, r, 1)
    dialogue = next(l for l in lines if l.speaker == "李白")
    start, end = dialogue.source_span
    # 「你好」 应在 content 里
    assert content[start:end] == "你好"
