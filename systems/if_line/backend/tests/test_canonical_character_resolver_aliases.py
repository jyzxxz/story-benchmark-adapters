"""CanonicalCharacterResolver — 用户原始 spec 的 alias 归并场景。

刘纯雨 / 纯雨 / 刘老师 / 刘纯雨老师 命中同一 cid；
NOVA / NOVA-7 / NOVA七号 / 机器人NOVA 命中同一 cid。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# 复用上一个测试文件的 _make_resolver helper
sys.path.insert(0, str(Path(__file__).parent))
from test_canonical_character_resolver import _make_resolver  # type: ignore


def test_liu_chunyu_aliases_all_resolve_to_same_cid():
    """刘纯雨 / 纯雨 / 刘老师 / 刘纯雨老师 应全部命中同一 character_id。"""
    r = _make_resolver([
        {
            "name": "刘纯雨",
            "aliases": ["纯雨", "刘老师", "刘纯雨老师"],
        },
    ], project_id=100)

    raw_mentions = ["刘纯雨", "纯雨", "刘老师", "刘纯雨老师"]
    cids = set()
    canonicals = set()
    for m in raw_mentions:
        res = r.resolve(m)
        cids.add(res.character_id)
        canonicals.add(res.canonical_name)

    assert len(cids) == 1, (
        f"四个 alias 应映射到同一 cid，实际={cids}"
    )
    assert canonicals == {"刘纯雨"}


def test_nova_robot_aliases_resolve():
    """NOVA / NOVA-7 / NOVA七号 / 机器人NOVA 应全部命中同一 cid。"""
    r = _make_resolver([
        {
            "name": "NOVA",
            "aliases": ["NOVA-7", "NOVA七号", "机器人NOVA"],
            "name_en": "NOVA-7",
        },
    ], project_id=200)

    raw_mentions = ["NOVA", "NOVA-7", "NOVA七号", "机器人NOVA"]
    cids = set()
    for m in raw_mentions:
        res = r.resolve(m)
        cids.add(res.character_id)
        # NOVA 七号 / 机器人 NOVA 可能 match_method 不同，但 cid 必须一致
        assert res.canonical_name == "NOVA", (
            f"{m} → canonical={res.canonical_name}, method={res.match_method}"
        )

    assert len(cids) == 1, f"NOVA 系列 alias 应映射到同一 cid，实际={cids}"


def test_mismatched_names_do_not_collide():
    """两个不同角色不应被错误归并。"""
    r = _make_resolver([
        {"name": "刘纯雨", "aliases": ["刘老师"]},
        {"name": "李老师", "aliases": ["老李"]},
    ])
    a = r.resolve("刘老师")
    b = r.resolve("老李")
    assert a.character_id != b.character_id
    assert a.canonical_name == "刘纯雨"
    assert b.canonical_name == "李老师"


def test_titles_as_aliases():
    """titles 字段也作为 alias 源。"""
    r = _make_resolver([
        {"name": "曹操", "titles": ["魏王", "孟德"]},
    ])
    assert r.resolve("孟德").canonical_name == "曹操"
    assert r.resolve("魏王").canonical_name == "曹操"


def test_name_en_as_alias():
    """name_en 字段作为 alias 源（中英互查）。"""
    r = _make_resolver([
        {"name": "杰克", "name_en": "Jack"},
    ])
    assert r.resolve("Jack").canonical_name == "杰克"


def test_two_resolver_instances_independent():
    """两个 resolver 实例（不同 project_id）的 unresolved 不串扰。"""
    r1 = _make_resolver([{"name": "A"}], project_id=1)
    r2 = _make_resolver([{"name": "A"}], project_id=2)
    r1.resolve("未知角色1")
    r2.resolve("未知角色2")
    assert len(r1.unresolved()) == 1
    assert len(r2.unresolved()) == 1
    # 不同 project_id 的 legacy_md5 不同
    cid1 = CanonicalCharacterResolver.legacy_md5("A", 1)
    cid2 = CanonicalCharacterResolver.legacy_md5("A", 2)
    assert cid1 != cid2


# 导入让 pytest 收集到 CanonicalCharacterResolver 类
from app.services.canonical_character_resolver import CanonicalCharacterResolver  # noqa: E402
