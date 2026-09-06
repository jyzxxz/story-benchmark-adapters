"""CanonicalCharacterResolver — 5 tier + legacy_md5 fallback 单元测试。

不调任何真实 DB；用 mock db 模拟 StoryBible。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.canonical_character_resolver import (
    CanonicalCharacterResolver,
    ResolvedCharacter,
    canonicalize_vngraph_character_identities,
)


# ---------- helpers ----------

class _FakeSBRow:
    def __init__(self, characters):
        self.raw_json = {"characters": characters}
        self.characters = characters


def _make_resolver(characters, project_id=42):
    """构造一个不查真实 DB 的 resolver，直接注入 characters。"""
    r = CanonicalCharacterResolver(db=None, project_id=project_id)
    # 跳过 DB 查询，直接填充 entries
    from app.services.canonical_character_resolver import _CharEntry, _normalize, _legacy_md5
    entries = []
    for c in characters:
        if isinstance(c, str):
            c = {"name": c}
        name = (c.get("canonical_name") or c.get("name") or "").strip()
        cid = c.get("character_id") or _legacy_md5(name, project_id)
        raw_aliases = []
        for k in ("aliases", "nicknames", "titles"):
            v = c.get(k)
            if isinstance(v, list):
                raw_aliases.extend(str(x).strip() for x in v if x)
            elif isinstance(v, str) and v:
                raw_aliases.append(v.strip())
        if c.get("name_en"):
            raw_aliases.append(str(c["name_en"]).strip())
        norm_aliases = tuple(sorted({_normalize(a) for a in raw_aliases if _normalize(a)}))
        entries.append(_CharEntry(
            canonical_name=name,
            character_id=str(cid),
            aliases=norm_aliases,
            raw_aliases=tuple(raw_aliases),
        ))
    r._entries = entries
    return r


# ---------- tier tests ----------

def test_tier1_exact_cid():
    """name 是 12 位 hex 且匹配 character_id → exact_cid。"""
    r = _make_resolver([
        {"name": "白玉堂", "character_id": "abcdef123456"},
    ])
    res = r.resolve("abcdef123456")
    assert res.match_method == "exact_cid"
    assert res.canonical_name == "白玉堂"
    assert res.confidence == 1.0


def test_tier2_exact_canonical():
    """name 完全等于 canonical_name → exact_canonical。"""
    r = _make_resolver([{"name": "白玉堂"}])
    res = r.resolve("白玉堂")
    assert res.match_method == "exact_canonical"
    assert res.canonical_name == "白玉堂"
    assert res.character_id  # 非空
    assert res.confidence == 1.0


def test_tier3_alias_exact():
    """name 在 aliases 列表 → alias。"""
    r = _make_resolver([
        {"name": "白玉堂", "aliases": ["小白", "锦毛鼠"]},
    ])
    res = r.resolve("锦毛鼠")
    assert res.match_method == "alias"
    assert res.canonical_name == "白玉堂"
    assert "锦毛鼠" in res.aliases_matched
    assert 0.9 <= res.confidence <= 1.0


def test_tier4_substring_bidirectional():
    """双向子串命中（含规范长度 ≥ 2）。"""
    r = _make_resolver([
        {"name": "欧阳明日", "aliases": ["明日"]},
    ])
    # raw '明日' 是 alias 的子串 → substring tier
    res = r.resolve("欧阳明")
    assert res.match_method == "substring"
    assert res.canonical_name == "欧阳明日"


def test_tier4_substring_reverse():
    """反向子串：raw 较长，包含 canonical_name。"""
    r = _make_resolver([{"name": "张三"}])
    res = r.resolve("张三李四")
    assert res.match_method == "substring"
    assert res.canonical_name == "张三"


def test_tier5_fuzzy():
    """rapidfuzz/difflib 模糊匹配（阈值 75，对中文短词友好）。"""
    r = _make_resolver([{"name": "李白"}])
    # '李大白' 与 '李白' token_set_ratio=80 ≥ 75 → fuzzy tier
    res = r.resolve("李大白")
    assert res.match_method == "fuzzy"
    assert res.canonical_name == "李白"
    assert res.confidence >= 0.55


def test_fallback_legacy_md5():
    """完全无匹配 → legacy_md5 fallback + 写入 unresolved。"""
    r = _make_resolver([{"name": "白玉堂"}])
    res = r.resolve("完全无关节色名")
    assert res.match_method == "legacy_md5"
    assert res.confidence == 0.3
    assert len(res.character_id) == 12
    # unresolved 累积
    assert len(r.unresolved()) == 1
    assert r.unresolved()[0]["raw_mention"] == "完全无关节色名"


def test_narrator_recognized():
    """旁白 / narration / narrator 都识别为 narrator。"""
    r = _make_resolver([])
    for alias in ("旁白", "narration", "narrator", "旁述"):
        res = r.resolve(alias)
        assert res.match_method == "narrator", f"{alias} should be narrator"
        assert res.canonical_name == "旁白"
        assert res.confidence == 1.0


def test_lru_cache_same_name_returns_same_object():
    """同一 raw_mention 二次 resolve 命中 lru_cache（同一对象）。"""
    r = _make_resolver([{"name": "白玉堂"}])
    a = r.resolve("白玉堂")
    b = r.resolve("白玉堂")
    assert a is b  # frozen dataclass + lru_cache 保证


def test_legacy_md5_compatible_with_old_algorithm():
    """legacy_md5 输出与历史 prompt_builder_service.generate_character_id 一致。"""
    import hashlib
    expected = hashlib.md5(f"白玉堂|42".encode("utf-8")).hexdigest()[:12]
    assert CanonicalCharacterResolver.legacy_md5("白玉堂", 42) == expected


def test_resolve_many():
    r = _make_resolver([
        {"name": "白玉堂", "aliases": ["小白"]},
        {"name": "展昭"},
    ])
    results = r.resolve_many(["小白", "展昭", "路人甲"])
    assert len(results) == 3
    assert results[0].canonical_name == "白玉堂"
    assert results[1].canonical_name == "展昭"
    assert results[2].match_method == "legacy_md5"


def test_story_bible_snapshot_resolves_short_name_without_db():
    resolver = CanonicalCharacterResolver(
        db=None,
        project_id=21,
        story_bible={"characters": [{"name": "宇智波带土"}]},
    )

    resolved = resolver.resolve("带土")

    assert resolved.canonical_name == "宇智波带土"
    assert resolved.character_id == CanonicalCharacterResolver.legacy_md5("宇智波带土", 21)
    assert resolved.match_method == "substring"


def test_vngraph_identity_normalization_merges_short_and_canonical_tachi_entries():
    project_id = 21
    canonical_name = "宇智波带土"
    canonical_cid = CanonicalCharacterResolver.legacy_md5(canonical_name, project_id)
    short_cid = CanonicalCharacterResolver.legacy_md5("带土", project_id)
    resolver = CanonicalCharacterResolver(
        db=None,
        project_id=project_id,
        story_bible={"characters": [{"name": canonical_name}]},
    )

    def tachi(index, name, cid, *, sync=False):
        node = {
            "Index": index,
            "NodeType": 2,
            "SubType": 1,
            "Data": {
                "TachiID": {"Kind": "String", "StringValue": name},
                "CharacterId": {"Kind": "String", "StringValue": cid},
                "CharacterName": {"Kind": "String", "StringValue": name},
                "Emotion": {"Kind": "String", "StringValue": "neutral"},
            },
            "Outputs": {},
        }
        if sync:
            node.update({"_sync_inserted": True, "source": "sync_recovery"})
        return node

    graph = {
        "Nodes": [
            {
                "Index": 1,
                "NodeType": 1,
                "SubType": 2,
                "Data": {"Lines": {"Items": [{"ObjectValue": {
                    "SpeakerId": {"Kind": "String", "StringValue": "带土"},
                    "CharacterId": {"Kind": "String", "StringValue": short_cid},
                }}]}},
                "Outputs": {"Actions": [10, 11], "Next": [2]},
            },
            {
                "Index": 2,
                "NodeType": 1,
                "SubType": 2,
                "Data": {"Lines": {"Items": []}},
                "Outputs": {"Actions": [12], "Next": []},
            },
            tachi(10, "带土", short_cid),
            tachi(11, canonical_name, canonical_cid, sync=True),
            {
                "Index": 12,
                "NodeType": 2,
                "SubType": 13,
                "Data": {"TachiID": {"Kind": "String", "StringValue": "带土"}},
                "Outputs": {},
            },
            {
                "Index": 13,
                "NodeType": 2,
                "SubType": 38,
                "Data": {"TachiID": {"Kind": "String", "StringValue": "带土"}},
                "Outputs": {},
            },
        ],
    }

    stats = canonicalize_vngraph_character_identities(graph, resolver)

    paragraph = next(node for node in graph["Nodes"] if node.get("Index") == 1)
    assert paragraph["Outputs"]["Actions"] == [10]
    assert all(node.get("Index") != 11 for node in graph["Nodes"])
    entry = next(node for node in graph["Nodes"] if node.get("Index") == 10)
    assert entry["Data"]["TachiID"]["StringValue"] == canonical_name
    assert entry["Data"]["CharacterName"]["StringValue"] == canonical_name
    assert entry["Data"]["CharacterId"]["StringValue"] == canonical_cid
    exit_node = next(node for node in graph["Nodes"] if node.get("Index") == 12)
    assert exit_node["Data"]["TachiID"]["StringValue"] == canonical_name
    highlight_node = next(node for node in graph["Nodes"] if node.get("Index") == 13)
    assert highlight_node["Data"]["TachiID"]["StringValue"] == canonical_name
    line = paragraph["Data"]["Lines"]["Items"][0]["ObjectValue"]
    assert line["CharacterId"]["StringValue"] == canonical_cid
    assert stats == {
        "canonicalized_tachi_nodes": 1,
        "canonicalized_tachi_references": 2,
        "canonicalized_line_identities": 1,
        "deduplicated_tachi_entries": 1,
    }
