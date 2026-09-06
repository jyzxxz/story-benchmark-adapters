"""DeterministicQuoteSplitter — 中文对白切分（rules fallback 用）。

设计目标：
- legacy VNGraph parsers without voice assets no longer treat the whole chapter
  塌缩为 1 条 narration；改用本 splitter 切出 narration + dialogue 序列。
- 纯规则、无 LLM 调用、可单测。
- 输出 ``SplitLine`` 列表，speaker 已尝试 resolver 解析；无法抽取的 speaker
  标 ``"未知角色"``，让 resolver 写入 unresolved。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.services.canonical_character_resolver import (
    CanonicalCharacterResolver,
)


# --------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------- #

@dataclass
class SplitLine:
    speaker: str
    text: str
    emotion: str
    show_portrait: bool
    character_id: str
    source_span: Tuple[int, int]   # (start, end) in original content


# --------------------------------------------------------------------- #
# Patterns
# --------------------------------------------------------------------- #

# 中文/英文引号对
_QUOTE_PAIRS = [
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ("\"", "\""),
    ("‘", "’"),
    ("'", "'"),
]

# speaker 抽取：前导「XXX说/道/笑道/喊道/低声道」
# 形式 1：在引号之前 "XXX说道："
# 形式 2：在引号之内 "「XXX说：内容」"（少数情况，先不处理）
_SPEAKER_VERBS = (
    "说道|道|笑道|喊道|冷笑道|低声道|沉声道|冷声道|怒道|惊道|问道|答道|叹道|喝道|"
    "低声说|沉声说|轻声说|笑着说|怒声说"
)
# speaker 前缀：2-4 个中文字符（姓氏 + 名/称呼），后接 verb 边界
# 用 alternation 让 regex 引擎先试 2 字，再试 3 字，再试 4 字
# （regex alternation 是 leftmost-shortest 在 Python re 里其实是 leftmost-longest，
#   但加上独立 lookahead 锚定 verb 起始位置后，2 字 lookahead 更易先满足）
_SPEAKER_PREFIX = (
    r"([一-龥]{2}(?:" + _SPEAKER_VERBS + r")"
    r"|[一-龥]{3}(?:" + _SPEAKER_VERBS + r")"
    r"|[一-龥]{4}(?:" + _SPEAKER_VERBS + r"))"
)
# name 是前 N 字（不含 verb），即 group(1) 去掉末尾 verb
_LEAD_PATTERN = re.compile(_SPEAKER_PREFIX)
_VERB_PATTERN = re.compile(_SPEAKER_VERBS)

# 段落分隔（2+ 换行）
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n+")

# 旁白 speaker 集合
_NARRATOR = "旁白"
_UNKNOWN = "未知角色"


# --------------------------------------------------------------------- #
# Splitter
# --------------------------------------------------------------------- #

class DeterministicQuoteSplitter:
    """纯规则中文对白切分器。

    用法::

        splitter = DeterministicQuoteSplitter(resolver, project_id)
        lines = splitter.split(content)
    """

    def __init__(self, resolver: CanonicalCharacterResolver, project_id: int):
        self.resolver = resolver
        self.project_id = project_id

    def split(self, content: str) -> List[SplitLine]:
        if not content or not content.strip():
            return []
        results: List[SplitLine] = []

        # 先按段落分隔切块；每块内找引号
        for block in _PARAGRAPH_BREAK.split(content):
            block = block.strip()
            if not block:
                continue
            results.extend(self._split_block(block, content))

        return results

    # ---------------- impl ---------------- #

    def _split_block(self, block: str, full_content: str) -> List[SplitLine]:
        """单段内：找所有引号对，引号外是 narration，引号内是 dialogue。"""
        out: List[SplitLine] = []
        cursor = 0
        block_start = full_content.find(block)
        if block_start < 0:
            block_start = 0

        # 收集所有引号 span
        spans = self._find_quote_spans(block)
        if not spans:
            # 整段都是 narration
            out.append(self._make_narration(block, block_start, block_start + len(block)))
            return out

        for qs, qe in spans:
            # 引号前的 narration（"XXX说道：" 形式 → 跳过，已抽 speaker）
            if qs > cursor:
                pre = block[cursor:qs]
                stripped = pre.strip()
                if stripped:
                    # 如果 pre 末尾是 "XXX说道：" 形式，整段视为 dialogue 引导，
                    # 不作为 narration 输出（避免产生 "李白笑道：" 这样的 noise line）
                    speaker_raw = self._extract_speaker(stripped)
                    if speaker_raw:
                        # 该段被 speaker 抽取消费
                        pass
                    else:
                        out.append(self._make_narration(
                            pre, block_start + cursor, block_start + qs,
                        ))
            # 引号内的 dialogue
            dialogue_text = block[qs + 1:qe].strip()
            if dialogue_text:
                # 从引号之前 backward 找 speaker
                pre_context = block[max(0, cursor - 30):qs]
                speaker_raw = self._extract_speaker(pre_context)
                out.append(self._make_dialogue(
                    dialogue_text, speaker_raw,
                    block_start + qs + 1, block_start + qe,
                ))
            cursor = qe + 1

        # 引号后的 narration
        if cursor < len(block):
            tail = block[cursor:]
            if tail.strip():
                out.append(self._make_narration(
                    tail, block_start + cursor, block_start + len(block),
                ))

        return out

    def _find_quote_spans(self, text: str) -> List[Tuple[int, int]]:
        """返回所有 (open_idx, close_idx) 引号对，嵌套时取最外层。"""
        spans: List[Tuple[int, int]] = []
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            open_ch = None
            close_ch = None
            for o, c in _QUOTE_PAIRS:
                if ch == o:
                    open_ch, close_ch = o, c
                    break
            if open_ch is None:
                i += 1
                continue
            # 找匹配的 close
            j = text.find(close_ch, i + 1)
            if j == -1:
                # 未闭合：把剩余作为 dialogue
                spans.append((i, n - 1))
                break
            spans.append((i, j))
            i = j + 1
        return spans

    def _extract_speaker(self, pre_context: str) -> str:
        """从前导文本抽 speaker name。找不到返回 ""。"""
        if not pre_context:
            return ""
        m = None
        # 反向搜索：找最后一个匹配
        for m_iter in _LEAD_PATTERN.finditer(pre_context):
            m = m_iter
        if m:
            # group(1) 是 "name + verb" 组合，需要去掉末尾 verb
            full = m.group(1)
            verb_m = _VERB_PATTERN.search(full)
            if verb_m:
                name = full[:verb_m.start()].strip()
                if name:
                    return name
            return full
        return ""

    # ---------------- builders ---------------- #

    def _make_narration(self, text: str, start: int, end: int) -> SplitLine:
        return SplitLine(
            speaker=_NARRATOR,
            text=text.strip(),
            emotion="neutral",
            show_portrait=False,
            character_id=self.resolver.resolve(_NARRATOR).character_id,
            source_span=(start, end),
        )

    def _make_dialogue(self, text: str, speaker_raw: str,
                       start: int, end: int) -> SplitLine:
        speaker = speaker_raw or _UNKNOWN
        resolved = self.resolver.resolve(speaker)
        # 未知角色 → show_portrait=False（避免错配立绘）
        show_portrait = (
            speaker != _UNKNOWN
            and resolved.canonical_name != _UNKNOWN
            and resolved.canonical_name != _NARRATOR
            and resolved.confidence >= 0.5
        )
        return SplitLine(
            speaker=speaker,
            text=text,
            emotion="neutral",   # 由 generator 上层或 timeline 推断
            show_portrait=show_portrait,
            character_id=resolved.character_id,
            source_span=(start, end),
        )


# --------------------------------------------------------------------- #
# 便捷函数
# --------------------------------------------------------------------- #

def split_chapter_content(
    content: str,
    resolver: CanonicalCharacterResolver,
    project_id: int,
) -> List[SplitLine]:
    """模块级便捷函数。"""
    return DeterministicQuoteSplitter(resolver, project_id).split(content)


__all__ = [
    "SplitLine",
    "DeterministicQuoteSplitter",
    "split_chapter_content",
]
