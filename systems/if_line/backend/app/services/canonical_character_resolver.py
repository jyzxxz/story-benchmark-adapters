"""CanonicalCharacterResolver — 统一角色身份解析（消灭 6 处 md5(name|pid) 镜像）。

设计目标：
- 所有需要把 ``raw_name`` → ``character_id`` 的模块（generator / assembler /
  portrait_demand / voice_line / identity_master / background_story_entity /
  tts）共用同一份 alias → canonical → character_id 解析。
- 向后兼容：历史 Asset.character_id 是 ``md5(raw_name|project_id)[:12]``，
  resolver 内置 ``legacy_md5_fallback`` tier 保证旧资产仍能被索引命中。

匹配 ladder（5 tier + fallback）：
  1. ``exact_cid``       — name 已是 12 位 hex 且命中 StoryBible 已记录的 cid
  2. ``exact_canonical`` — == character.canonical_name / character.name
  3. ``alias``           — ∈ character.aliases / character.nicknames
  4. ``substring``       — 双向子串（长度 ≥ 2），最短 alias 优先
  5. ``fuzzy``           — rapidfuzz token_set_ratio ≥ 85（fallback 到 difflib）
  fallback ``legacy_md5`` — md5(raw|pid)[:12]，confidence=0.3，写入 unresolved
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Tuple


# --------------------------------------------------------------------- #
# Dataclass
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResolvedCharacter:
    """resolve() 返回的不可变结构。

    ``character_id`` 始终是非空 12 位 hex（fallback 也能产出），便于上层直接
    当 dict key 用。``confidence`` < 0.6 时上层应把 raw_mention 写入
    ``unresolved_characters``，但**仍然返回 character_id**（向后兼容旧索引）。
    """
    raw_mention: str
    canonical_name: str
    character_id: str
    confidence: float
    match_method: str           # exact_cid | exact_canonical | alias | substring | fuzzy | legacy_md5
    aliases_matched: List[str] = field(default_factory=list)


# --------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------- #

_CID_RE = re.compile(r"^[a-f0-9]{12}$")
_HEXI_RE = re.compile(r"^[a-f0-9]{8,16}$")


def _legacy_md5(name: str, project_id: int) -> str:
    """与 ``prompt_builder_service.generate_character_id`` 完全一致。"""
    data = f"{name}|{project_id}"
    return hashlib.md5(data.encode("utf-8")).hexdigest()[:12]


def _normalize(s: str) -> str:
    """小写 + 去空白 + 去常见标点，便于 alias 比对。"""
    if not s:
        return ""
    s = s.strip().lower()
    # 去掉中英文常见符号
    s = re.sub(r"[\s·\-_,，。.!！?？:：;；'\"`]+", "", s)
    return s


def _fuzzy_ratio(a: str, b: str) -> float:
    """rapidfuzz 不可用时退化到 difflib.SequenceMatcher。

    返回 0-100 的相似度。
    """
    try:
        from rapidfuzz import fuzz  # type: ignore
        return float(fuzz.token_set_ratio(a, b))
    except Exception:
        import difflib
        return difflib.SequenceMatcher(None, a, b).ratio() * 100.0


def _is_cid_like(s: str) -> bool:
    return bool(s and _HEXI_RE.match(s.lower()))


# --------------------------------------------------------------------- #
# CharacterEntry — 内部扁平结构
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class _CharEntry:
    canonical_name: str
    character_id: str
    aliases: Tuple[str, ...]            # 已 normalize 的 alias 列表
    raw_aliases: Tuple[str, ...]        # 原始 alias（调试用）

    def all_names_normalized(self) -> List[str]:
        out = [_normalize(self.canonical_name)]
        out.extend(self.aliases)
        return [n for n in out if n]


# --------------------------------------------------------------------- #
# Resolver
# --------------------------------------------------------------------- #

class CanonicalCharacterResolver:
    """DB-backed + lru_cache 的角色解析器。

    用法::

        resolver = CanonicalCharacterResolver(db, project_id)
        r = resolver.resolve("刘老师")
        print(r.canonical_name, r.character_id, r.confidence)

    Notes:
        - ``resolve`` 是 lru_cache 的（key=name + allow_fallback）；
          StoryBible 在 resolver 生命周期内被认为是只读快照。
        - 一个 resolver 实例对应一个 project_id。跨 project 请新建实例。
        - ``unresolved()`` 累积所有 confidence < 0.6 的 raw_mention，
          调用方应在结束时取出，写入 ``Meta.portrait_assembly_report``。
    """

    # 内置旁白/未识别角色 alias —— 全局共享，不进 StoryBible
    _NARRATOR_ALIASES = ("", "旁白", "旁述", "narration", "narrator", "系统", "未知", "未知角色")

    def __init__(
        self,
        db: Any,
        project_id: int,
        story_bible: Optional[Dict[str, Any]] = None,
    ):
        self._db = db
        self._project_id = int(project_id)
        self._story_bible = story_bible
        self._entries: Optional[List[_CharEntry]] = None
        self._unresolved: List[Dict[str, Any]] = []
        # 兼容 lru_cache on instance method（per-instance 缓存）
        self.resolve = lru_cache(maxsize=512)(self._resolve_impl)  # type: ignore

    # ----------------------- public API ----------------------- #

    def project_id(self) -> int:
        return self._project_id

    def unresolved(self) -> List[Dict[str, Any]]:
        """返回累积的 unresolved 列表（raw_mention + confidence + reason）。"""
        return list(self._unresolved)

    def clear_unresolved(self) -> None:
        self._unresolved.clear()

    def resolve_many(self, names: Iterable[str]) -> List[ResolvedCharacter]:
        return [self.resolve(n) for n in names if n is not None]

    # ----------------------- impl ----------------------- #

    def _resolve_impl(self, name: str, allow_fallback: bool = True) -> ResolvedCharacter:
        """实际 resolve 实现（被 lru_cache 包装）。

        ``allow_fallback=False`` 时 confidence<0.6 也**不**写入 unresolved，
        用于内部 tier 试探。
        """
        raw = (name or "").strip()
        norm = _normalize(raw)

        # 0. 旁白
        if norm in {_normalize(a) for a in self._NARRATOR_ALIASES}:
            return ResolvedCharacter(
                raw_mention=raw or "旁白",
                canonical_name="旁白",
                character_id=_legacy_md5("旁白", self._project_id),
                confidence=1.0,
                match_method="narrator",
            )

        entries = self._load_entries()

        # tier 1: exact_cid
        if _is_cid_like(raw):
            for e in entries:
                if e.character_id.lower() == raw.lower():
                    return ResolvedCharacter(
                        raw_mention=raw,
                        canonical_name=e.canonical_name,
                        character_id=e.character_id,
                        confidence=1.0,
                        match_method="exact_cid",
                    )

        # tier 2: exact_canonical
        for e in entries:
            if _normalize(e.canonical_name) == norm and norm:
                return ResolvedCharacter(
                    raw_mention=raw,
                    canonical_name=e.canonical_name,
                    character_id=e.character_id,
                    confidence=1.0,
                    match_method="exact_canonical",
                )

        # tier 3: alias exact
        for e in entries:
            if norm and norm in e.aliases:
                return ResolvedCharacter(
                    raw_mention=raw,
                    canonical_name=e.canonical_name,
                    character_id=e.character_id,
                    confidence=0.95,
                    match_method="alias",
                    aliases_matched=[a for a in e.raw_aliases if _normalize(a) == norm],
                )

        # tier 4: substring (双向，最短 alias 优先)
        if norm and len(norm) >= 2:
            candidates: List[Tuple[int, _CharEntry, str]] = []
            for e in entries:
                for n in e.all_names_normalized():
                    if not n:
                        continue
                    if norm in n or n in norm:
                        candidates.append((len(n), e, n))
            if candidates:
                candidates.sort(key=lambda x: x[0])  # 最短优先（最具体）
                _, e, matched = candidates[0]
                return ResolvedCharacter(
                    raw_mention=raw,
                    canonical_name=e.canonical_name,
                    character_id=e.character_id,
                    confidence=0.75,
                    match_method="substring",
                    aliases_matched=[matched],
                )

        # tier 5: fuzzy
        if norm and len(norm) >= 2:
            best_e: Optional[_CharEntry] = None
            best_score = 0.0
            best_alias = ""
            for e in entries:
                for n in e.all_names_normalized():
                    if not n:
                        continue
                    score = _fuzzy_ratio(norm, n)
                    if score > best_score:
                        best_score = score
                        best_e = e
                        best_alias = n
            if best_e is not None and best_score >= 75.0:
                return ResolvedCharacter(
                    raw_mention=raw,
                    canonical_name=best_e.canonical_name,
                    character_id=best_e.character_id,
                    confidence=0.55 + (best_score - 75) / 100 * 0.25,  # 0.55 - 0.8
                    match_method="fuzzy",
                    aliases_matched=[best_alias],
                )

        # fallback: legacy_md5
        cid = _legacy_md5(raw, self._project_id)
        if allow_fallback:
            self._unresolved.append({
                "raw_mention": raw,
                "confidence": 0.3,
                "reason": "no_match",
                "fallback_cid": cid,
            })
        return ResolvedCharacter(
            raw_mention=raw,
            canonical_name=raw or "未知角色",
            character_id=cid,
            confidence=0.3,
            match_method="legacy_md5",
        )

    # ----------------------- StoryBible 加载 ----------------------- #

    def _load_entries(self) -> List[_CharEntry]:
        if self._entries is not None:
            return self._entries
        entries: List[_CharEntry] = []
        try:
            story_bible = self._fetch_story_bible()
        except Exception:
            story_bible = None
        if story_bible:
            for c in (story_bible.get("characters") or []):
                if isinstance(c, str):
                    c = {"name": c}
                if not isinstance(c, dict):
                    continue
                name = (c.get("canonical_name") or c.get("name") or "").strip()
                if not name:
                    continue
                # cid 来源优先级：character_id > canonical_name > name
                cid = (c.get("character_id") or _legacy_md5(name, self._project_id))
                raw_aliases = []
                for k in ("aliases", "nicknames", "titles"):
                    v = c.get(k)
                    if isinstance(v, list):
                        raw_aliases.extend(str(x).strip() for x in v if x)
                    elif isinstance(v, str) and v:
                        raw_aliases.append(v.strip())
                if c.get("name_en"):
                    raw_aliases.append(str(c["name_en"]).strip())
                norm_aliases = tuple(sorted({
                    _normalize(a) for a in raw_aliases if _normalize(a)
                }))
                entries.append(_CharEntry(
                    canonical_name=name,
                    character_id=str(cid),
                    aliases=norm_aliases,
                    raw_aliases=tuple(raw_aliases),
                ))
        self._entries = entries
        return entries

    def _fetch_story_bible(self) -> Optional[Dict[str, Any]]:
        if self._story_bible is not None:
            return self._story_bible
        if not self._db:
            return None
        try:
            from app.models import StoryBible  # 延迟 import 避免循环
            row = (
                self._db.query(StoryBible)
                .filter(StoryBible.project_id == self._project_id)
                .first()
            )
            if not row:
                return None
            # 兼容 raw_json / characters 两个存储路径
            if row.raw_json and isinstance(row.raw_json, dict):
                return row.raw_json
            return {
                "characters": row.characters if isinstance(row.characters, list) else [],
            }
        except Exception:
            return None

    # ----------------------- 静态便捷方法（兼容旧调用） ----------------------- #

    @classmethod
    def legacy_md5(cls, name: str, project_id: int) -> str:
        """旧 ``generate_character_id`` 的纯函数版本，用于 prompt_builder_service
        薄包装与 Tier-6 stale 兜底。"""
        return _legacy_md5(name, project_id)


def _graph_string(data: Dict[str, Any], key: str) -> str:
    value = data.get(key)
    if isinstance(value, dict):
        return str(value.get("StringValue") or "").strip()
    return str(value or "").strip()


def _set_graph_string(data: Dict[str, Any], key: str, value: str) -> bool:
    current = _graph_string(data, key)
    if current == value:
        return False
    slot = data.get(key)
    if isinstance(slot, dict):
        slot["Kind"] = slot.get("Kind") or "String"
        slot["StringValue"] = value
    else:
        data[key] = {"Kind": "String", "StringValue": value}
    return True


def _runtime_tachi_id(canonical_name: str) -> str:
    return canonical_name.strip().lower().replace(" ", "_").replace("·", "_")


def canonicalize_vngraph_character_identities(
    graph: Dict[str, Any],
    resolver: Optional[CanonicalCharacterResolver],
) -> Dict[str, int]:
    """Normalize every VNGraph character reference to one canonical identity.

    Legacy graphs commonly mix a short dialogue name (for example ``带土``)
    with the Story Bible name (``宇智波带土``).  The renderer treats their
    different ``TachiID`` values as two active actors.  This pass updates the
    complete Tachi action family together, repairs line ``CharacterId`` values,
    and removes exact duplicate entry actions from a paragraph.
    """
    stats = {
        "canonicalized_tachi_nodes": 0,
        "canonicalized_tachi_references": 0,
        "canonicalized_line_identities": 0,
        "deduplicated_tachi_entries": 0,
    }
    if resolver is None:
        return stats
    nodes = graph.get("Nodes")
    if not isinstance(nodes, list):
        return stats

    node_by_index = {
        node.get("Index"): node
        for node in nodes
        if isinstance(node, dict) and isinstance(node.get("Index"), int)
    }
    runtime_aliases: Dict[str, str] = {}

    # Entry nodes carry the richest identity fields, so normalize them first
    # and remember the old runtime IDs for move/scale/effect/exit actions.
    for node in nodes:
        if not isinstance(node, dict) or node.get("NodeType") != 2 or node.get("SubType") != 1:
            continue
        data = node.get("Data")
        if not isinstance(data, dict):
            continue
        raw_tachi_id = _graph_string(data, "TachiID")
        raw_name = _graph_string(data, "CharacterName") or raw_tachi_id
        resolved = resolver.resolve(raw_name)
        if resolved.match_method == "narrator" or resolved.confidence < 0.6:
            continue
        canonical_tachi_id = _runtime_tachi_id(resolved.canonical_name)
        if raw_tachi_id:
            runtime_aliases[raw_tachi_id] = canonical_tachi_id
        changed = False
        changed |= _set_graph_string(data, "TachiID", canonical_tachi_id)
        changed |= _set_graph_string(data, "CharacterId", resolved.character_id)
        changed |= _set_graph_string(data, "CharacterName", resolved.canonical_name)
        if changed:
            stats["canonicalized_tachi_nodes"] += 1

    # Keep all runtime actions addressable after an entry ID was rewritten.
    for node in nodes:
        if not isinstance(node, dict):
            continue
        data = node.get("Data")
        if not isinstance(data, dict):
            continue
        if "TachiID" not in data and "ReferenceTachiID" not in data:
            continue
        changed = False
        for key in ("TachiID", "ReferenceTachiID"):
            raw = _graph_string(data, key)
            if not raw:
                continue
            canonical_tachi_id = runtime_aliases.get(raw)
            if not canonical_tachi_id:
                resolved = resolver.resolve(raw)
                if resolved.match_method == "narrator" or resolved.confidence < 0.6:
                    continue
                canonical_tachi_id = _runtime_tachi_id(resolved.canonical_name)
            changed |= _set_graph_string(data, key, canonical_tachi_id)
        if changed and not (node.get("NodeType") == 2 and node.get("SubType") == 1):
            stats["canonicalized_tachi_references"] += 1

    # Dialogue display names stay untouched; only their stable identity changes.
    for node in nodes:
        if not isinstance(node, dict) or node.get("NodeType") != 1 or node.get("SubType") != 2:
            continue
        data = node.get("Data")
        lines = data.get("Lines") if isinstance(data, dict) else None
        items = lines.get("Items") if isinstance(lines, dict) else None
        if not isinstance(items, list):
            continue
        for item in items:
            obj = item.get("ObjectValue") if isinstance(item, dict) else None
            if not isinstance(obj, dict):
                continue
            speaker = _graph_string(obj, "SpeakerId")
            if not speaker:
                continue
            resolved = resolver.resolve(speaker)
            if resolved.match_method == "narrator" or resolved.confidence < 0.6:
                continue
            if _set_graph_string(obj, "CharacterId", resolved.character_id):
                stats["canonicalized_line_identities"] += 1

    # A paragraph must not dispatch the same character variation twice.  Prefer
    # the original authored action over a later sync_recovery copy.
    removed_indices = set()
    for node in nodes:
        if not isinstance(node, dict) or node.get("NodeType") != 1 or node.get("SubType") != 2:
            continue
        outputs = node.get("Outputs")
        actions = outputs.get("Actions") if isinstance(outputs, dict) else None
        if not isinstance(actions, list):
            continue
        deduped: List[Any] = []
        key_positions: Dict[Tuple[str, str, str, str], int] = {}
        for action_index in actions:
            action = node_by_index.get(action_index)
            if not isinstance(action, dict) or action.get("NodeType") != 2 or action.get("SubType") != 1:
                deduped.append(action_index)
                continue
            action_data = action.get("Data") if isinstance(action.get("Data"), dict) else {}
            cid = _graph_string(action_data, "CharacterId")
            if not cid:
                deduped.append(action_index)
                continue
            key = (
                cid,
                _graph_string(action_data, "Emotion").lower() or "neutral",
                _graph_string(action_data, "Outfit").lower(),
                _graph_string(action_data, "Pose").lower(),
            )
            previous_position = key_positions.get(key)
            if previous_position is None:
                key_positions[key] = len(deduped)
                deduped.append(action_index)
                continue
            previous_index = deduped[previous_position]
            previous = node_by_index.get(previous_index)
            previous_is_sync = isinstance(previous, dict) and (
                previous.get("_sync_inserted") is True or previous.get("source") == "sync_recovery"
            )
            current_is_sync = action.get("_sync_inserted") is True or action.get("source") == "sync_recovery"
            if previous_is_sync and not current_is_sync:
                deduped[previous_position] = action_index
                removed_indices.add(previous_index)
            else:
                removed_indices.add(action_index)
            stats["deduplicated_tachi_entries"] += 1
        outputs["Actions"] = deduped

    if removed_indices:
        still_referenced = {
            action_index
            for node in nodes
            if isinstance(node, dict)
            for outputs in [node.get("Outputs")]
            if isinstance(outputs, dict)
            for action_index in (outputs.get("Actions") or [])
            if isinstance(action_index, int)
        }
        nodes[:] = [
            node
            for node in nodes
            if not (
                isinstance(node, dict)
                and node.get("Index") in removed_indices
                and node.get("Index") not in still_referenced
            )
        ]

    return stats


__all__ = [
    "CanonicalCharacterResolver",
    "ResolvedCharacter",
    "canonicalize_vngraph_character_identities",
]
