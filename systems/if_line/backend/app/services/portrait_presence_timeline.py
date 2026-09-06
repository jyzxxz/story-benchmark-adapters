"""PortraitPresenceTimeline — 段级「在场角色流」+ 期望立绘 entries。

设计目标：
- 不再用 "speaker 列表" 作为唯一信号，加入 ``scene.characters_present`` 与
  显式 ``characters_exit``；
- **禁止用「停止说话」作为退场**；只在以下情况退场：
    1. scene 切换且新 scene.characters_present 不含该角色；
    2. paragraph 显式 stage direction（如「X 转身离开」「X 死亡」）；
    3. PortraitPresenceTimelineBuilder 显式接收的 characters_exit 列表。
- emotion 变化 / outfit 变化 → 触发新 ``ExpectedTachiEntry``（前端同 TachiID 替换）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.services.canonical_character_resolver import (
    CanonicalCharacterResolver,
    ResolvedCharacter,
)


# --------------------------------------------------------------------- #
# Dataclasses
# --------------------------------------------------------------------- #

@dataclass
class PresenceFrame:
    paragraph_index: int
    line_index: int
    characters_visible: List[str]           # canonical_names 在场（持续）
    characters_enter: List[str]             # 本帧相对上一帧新进入
    characters_exit: List[str]              # 本帧相对上一帧离开
    speaking: Optional[str]                 # canonical_name 说话者
    emotion: str
    outfit: Optional[str]
    pose: Optional[str]
    evidence: str                           # voice_asset | characters_present | inferred | narration
    confidence: float


@dataclass
class ExpectedTachiEntry:
    paragraph_index: int
    character_id: str
    canonical_name: str
    emotion: str
    outfit: Optional[str]
    pose: Optional[str]
    reason: str                             # speaker_switch | emotion_change | outfit_change | enter | restore_after_illustration
    confidence: float


@dataclass
class PortraitPresenceTimeline:
    frames: List[PresenceFrame]
    expected_entries: List[ExpectedTachiEntry]

    def entries_for_paragraph(self, paragraph_index: int) -> List[ExpectedTachiEntry]:
        return [e for e in self.expected_entries if e.paragraph_index == paragraph_index]


# --------------------------------------------------------------------- #
# Scene spec input
# --------------------------------------------------------------------- #

@dataclass
class SceneSpec:
    """章节内的一个场景切片。

    ``start_paragraph_index`` / ``end_paragraph_index`` 是闭区间，
    对应主流程 paragraph 在 graph 里的 Index。
    """
    start_paragraph_index: int
    end_paragraph_index: int
    characters_present: List[str] = field(default_factory=list)
    characters_exit: List[str] = field(default_factory=list)
    location: Optional[str] = None


# --------------------------------------------------------------------- #
# Paragraph spec input
# --------------------------------------------------------------------- #

@dataclass
class ParagraphLineSpec:
    speaker_raw: str                        # 原始 SpeakerId / 旁白
    text: str
    emotion: Optional[str] = None
    outfit: Optional[str] = None
    pose: Optional[str] = None
    character_id: Optional[str] = None      # 若上游已解析
    show_portrait: Optional[bool] = None


@dataclass
class ParagraphSpec:
    paragraph_index: int
    lines: List[ParagraphLineSpec]
    has_illustration_action: bool = False   # 该 paragraph.Actions 是否含 IllustrationNode


# --------------------------------------------------------------------- #
# Builder
# --------------------------------------------------------------------- #

# 退场关键词（出现在 narration 文本中）—— 用于 stage direction 启发
_EXIT_PATTERNS = [
    re.compile(r"([一-龥]{2,6})\s*(?:转身|转身离开|离开|退下|退场|离去|走了|死亡|倒下|消失)"),
]


class PortraitPresenceTimelineBuilder:
    """构造 ``PortraitPresenceTimeline``。

    输入：
    - paragraphs : List[ParagraphSpec]
    - scenes     : List[SceneSpec]（可选；为空时所有 paragraph 视为同一 scene）
    - resolver   : CanonicalCharacterResolver

    依赖 resolver 把 raw speaker name → canonical_name + character_id。
    """

    def __init__(self, resolver: CanonicalCharacterResolver):
        self.resolver = resolver

    def build(
        self,
        paragraphs: List[ParagraphSpec],
        scenes: Optional[List[SceneSpec]] = None,
    ) -> PortraitPresenceTimeline:
        scenes = scenes or []
        frames: List[PresenceFrame] = []
        expected: List[ExpectedTachiEntry] = []

        # 当前在场角色集合（canonical_name）
        visible: List[str] = []
        # 上一帧的 (speaker, emotion, outfit) — 用于检测变化
        prev_speaker: Optional[str] = None
        prev_emotion_by_char: Dict[str, str] = {}
        prev_outfit_by_char: Dict[str, str] = {}
        # 上一 paragraph 是否是 illustration
        after_illustration = False

        scene_by_start = sorted(scenes, key=lambda s: s.start_paragraph_index) if scenes else []

        for p_idx, para in enumerate(paragraphs):
            # 找当前 paragraph 所属 scene
            cur_scene = self._scene_for_paragraph(scene_by_start, para.paragraph_index)
            if cur_scene and cur_scene.characters_present:
                # scene 显式 present 列表 → resolver 解析 → 加入 visible
                for raw_name in cur_scene.characters_present:
                    if raw_name in ("旁白", "", "narrator"):
                        continue
                    resolved = self.resolver.resolve(raw_name)
                    cname = resolved.canonical_name
                    if cname and cname != "旁白" and cname not in visible:
                        # 新进入：标记 enter
                        visible.append(cname)
                        # 如果该角色在本 paragraph 不说话，也生成 enter entry（visible_silent）
                        if not any(l.speaker_raw == raw_name or
                                   self.resolver.resolve(l.speaker_raw).canonical_name == cname
                                   for l in para.lines):
                            expected.append(ExpectedTachiEntry(
                                paragraph_index=para.paragraph_index,
                                character_id=resolved.character_id,
                                canonical_name=cname,
                                emotion="neutral",
                                outfit=None,
                                pose=None,
                                reason="enter",
                                confidence=0.7,
                            ))

            # scene 显式 characters_exit
            scene_exit_cnames: List[str] = []
            if cur_scene and cur_scene.characters_exit:
                for raw_name in cur_scene.characters_exit:
                    if raw_name in ("旁白", ""):
                        continue
                    resolved = self.resolver.resolve(raw_name)
                    if resolved.canonical_name and resolved.canonical_name != "旁白":
                        scene_exit_cnames.append(resolved.canonical_name)

            # 遍历 lines 生成 frame
            for line_idx, line in enumerate(para.lines):
                resolved = self._resolve_line_speaker(line)
                cname = resolved.canonical_name if resolved.canonical_name != "旁白" else None

                # 推断 emotion / outfit / pose
                emotion = (line.emotion or self._infer_emotion_from_text(line.text) or "neutral")
                outfit = line.outfit
                pose = line.pose

                # 进入判定：speaker 在 visible 但还没记录
                entered: List[str] = []
                if cname and cname not in visible:
                    visible.append(cname)
                    entered.append(cname)

                # 退场判定（只信 scene.characters_exit / 显式 stage direction）
                exited: List[str] = []
                # 1. scene 显式 exit
                for exit_cname in scene_exit_cnames:
                    if exit_cname in visible:
                        visible.remove(exit_cname)
                        exited.append(exit_cname)
                # 2. stage direction 启发（从 narration 文本抽）
                if not cname:  # 旁白 line 才检查 stage direction
                    for pat in _EXIT_PATTERNS:
                        for m in pat.finditer(line.text or ""):
                            raw_exit_name = m.group(1)
                            r = self.resolver.resolve(raw_exit_name)
                            if (r.canonical_name and r.canonical_name != "旁白"
                                    and r.canonical_name in visible):
                                visible.remove(r.canonical_name)
                                if r.canonical_name not in exited:
                                    exited.append(r.canonical_name)

                frame = PresenceFrame(
                    paragraph_index=para.paragraph_index,
                    line_index=line_idx,
                    characters_visible=list(visible),
                    characters_enter=entered,
                    characters_exit=exited,
                    speaking=cname,
                    emotion=emotion,
                    outfit=outfit,
                    pose=pose,
                    evidence=("voice_asset" if (cname and line.emotion) else
                              "characters_present" if (cur_scene and cur_scene.characters_present) else
                              "inferred"),
                    confidence=0.85 if cname and line.emotion else 0.6,
                )
                frames.append(frame)

                # ---- expected entries 触发 ----
                if cname:
                    # 1. speaker 切换
                    if cname != prev_speaker:
                        expected.append(self._make_entry(
                            para.paragraph_index, resolved, emotion, outfit, pose,
                            reason="speaker_switch", confidence=0.9,
                        ))
                    # 2. emotion 变化
                    elif emotion != prev_emotion_by_char.get(cname):
                        expected.append(self._make_entry(
                            para.paragraph_index, resolved, emotion, outfit, pose,
                            reason="emotion_change", confidence=0.8,
                        ))
                    # 3. outfit 变化
                    elif outfit and outfit != prev_outfit_by_char.get(cname):
                        expected.append(self._make_entry(
                            para.paragraph_index, resolved, emotion, outfit, pose,
                            reason="outfit_change", confidence=0.75,
                        ))

                    prev_speaker = cname
                    prev_emotion_by_char[cname] = emotion
                    if outfit:
                        prev_outfit_by_char[cname] = outfit

                # update visible 状态（line 级 enter/exit 已经处理）
                for c in entered:
                    pass  # 已 append

            # illustration 后恢复
            if after_illustration and visible:
                # 该 paragraph 之前是 illustration，本段 visible 角色需要 restore
                for cname in visible:
                    resolved = self._find_resolved_by_cname(cname)
                    if resolved is None:
                        continue
                    expected.append(ExpectedTachiEntry(
                        paragraph_index=para.paragraph_index,
                        character_id=resolved.character_id,
                        canonical_name=cname,
                        emotion=prev_emotion_by_char.get(cname, "neutral"),
                        outfit=prev_outfit_by_char.get(cname),
                        pose=None,
                        reason="restore_after_illustration",
                        confidence=0.85,
                    ))
            after_illustration = para.has_illustration_action

        return PortraitPresenceTimeline(frames=frames, expected_entries=expected)

    # ---------------- helpers ---------------- #

    def _resolve_line_speaker(self, line: ParagraphLineSpec) -> ResolvedCharacter:
        if line.character_id:
            # 上游已解析；用 cid 反查 canonical_name
            res = self.resolver.resolve(line.character_id)
            return res
        return self.resolver.resolve(line.speaker_raw or "旁白")

    def _find_resolved_by_cname(self, cname: str) -> Optional[ResolvedCharacter]:
        # 通过 resolve(cname) 反查（exact_canonical tier 一定能命中）
        if not cname:
            return None
        res = self.resolver.resolve(cname)
        if res.canonical_name == cname:
            return res
        return None

    def _make_entry(
        self,
        paragraph_index: int,
        resolved: ResolvedCharacter,
        emotion: str,
        outfit: Optional[str],
        pose: Optional[str],
        reason: str,
        confidence: float,
    ) -> ExpectedTachiEntry:
        return ExpectedTachiEntry(
            paragraph_index=paragraph_index,
            character_id=resolved.character_id,
            canonical_name=resolved.canonical_name,
            emotion=emotion,
            outfit=outfit,
            pose=pose,
            reason=reason,
            confidence=confidence,
        )

    def _scene_for_paragraph(
        self,
        scenes_sorted: List[SceneSpec],
        paragraph_index: int,
    ) -> Optional[SceneSpec]:
        for s in scenes_sorted:
            if s.start_paragraph_index <= paragraph_index <= s.end_paragraph_index:
                return s
        return None

    # 简单情绪词典（narration 动词 → emotion）
    _EMOTION_VERBS = {
        "笑": "happy",
        "怒": "angry",
        "哭": "sad",
        "惊": "surprise",
        "怕": "fear",
        "惧": "fear",
        "叹": "sad",
    }

    def _infer_emotion_from_text(self, text: str) -> Optional[str]:
        if not text:
            return None
        for keyword, emotion in self._EMOTION_VERBS.items():
            if keyword in text:
                return emotion
        return None


__all__ = [
    "PresenceFrame",
    "ExpectedTachiEntry",
    "PortraitPresenceTimeline",
    "SceneSpec",
    "ParagraphSpec",
    "ParagraphLineSpec",
    "PortraitPresenceTimelineBuilder",
]
