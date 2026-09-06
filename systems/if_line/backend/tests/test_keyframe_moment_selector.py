"""
P1.2 — keyframe_moment_selector 单元测试。

覆盖：
- max_keyframes<=0 / 空正文 / 正文过短 → 返回空（caller fallback）
- API key 缺失 / KEYFRAME_AUTO_SELECT=0 → 返回空，不调 LLM
- LLM 返回正常 moments → 解析 + dedup
- LLM 返回 moments 里 source_excerpt 不在原文 → drop
- LLM 同 (summary, characters) 重复 → dedup
- target_characters 不在大纲白名单 → 过滤
- max_keyframes 截断
- LLM 失败 → 返回空
- 缓存命中 → 不再调 LLM
- _extract_outline_characters 兼容 list[str] 和 list[dict]
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import keyframe_moment_selector as kms
from app.services.keyframe_moment_selector import (
    KeyframeMoment,
    KeyframeMomentSelector,
)


# ----------------------- fixtures -----------------------

CHAPTER_OUTLINE = {
    "title": "朝堂惊变",
    "scene": "朝堂",
    "conflict": "林夜持剑指向群臣",
    "emotion": "intense",
    "characters": ["林夜", "苏婉", "群臣首领"],
    "summary": "林夜踏入朝堂质问群臣，剑光一闪。",
    "visual_keywords": ["朝堂", "剑光"],
}

LONG_CHAPTER = (
    "林夜踏入大殿，目光如刀，扫过两侧群臣。"
    "苏婉站在他身后，心中涌起一阵寒意。"
    "殿外风雷骤起，一道剑光破空而来。"
) * 20  # >200 字，且包含可被 source_excerpt 命中的片段


def _fake_llm_response(moments_payload):
    """构造一个 mock LLM client，返回给定 moments dict。"""
    import json as _json
    fake_choice = MagicMock()
    fake_choice.message.content = _json.dumps({"moments": moments_payload}, ensure_ascii=False)
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)
    return fake_client


def _fake_failing_llm():
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(side_effect=RuntimeError("network down"))
    return fake_client


@pytest.fixture
def selector(tmp_path, monkeypatch):
    """干净的 selector，CACHE_DIR 重定向到 tmp_path。"""
    monkeypatch.setattr(kms, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(kms, "KEYFRAME_MOMENT_ENABLED", True)
    monkeypatch.setattr(kms, "KEYFRAME_MOMENT_API_KEY", "sk-test-key")
    return KeyframeMomentSelector()


# ----------------------- 早退：空 / 过短 / max<=0 -----------------------

@pytest.mark.asyncio
async def test_empty_content_returns_empty(selector):
    out = await selector.select("", CHAPTER_OUTLINE, max_keyframes=3)
    assert out == []


@pytest.mark.asyncio
async def test_short_content_returns_empty(selector):
    out = await selector.select("太短了", CHAPTER_OUTLINE, max_keyframes=3)
    assert out == []


@pytest.mark.asyncio
async def test_zero_max_keyframes_returns_empty(selector):
    out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=0)
    assert out == []


# ----------------------- API key 缺失 / 关闭 → 不调 LLM -----------------------

@pytest.mark.asyncio
async def test_no_api_key_returns_empty(selector, monkeypatch):
    monkeypatch.setattr(kms, "KEYFRAME_MOMENT_API_KEY", "")
    fake_client = _fake_llm_response([
        {"moment_summary": "x", "target_characters": [], "visual_focus": "y",
         "source_excerpt": LONG_CHAPTER[:30], "rationale": "z"},
    ])
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    assert out == []
    assert fake_client.chat.completions.create.await_count == 0


@pytest.mark.asyncio
async def test_disabled_via_env_returns_empty(selector, monkeypatch):
    """KEYFRAME_AUTO_SELECT=0 → ENABLED=False → 直接返回空（蓝图回退开关）"""
    monkeypatch.setattr(kms, "KEYFRAME_MOMENT_ENABLED", False)
    fake_client = _fake_llm_response([
        {"moment_summary": "x", "target_characters": [], "visual_focus": "y",
         "source_excerpt": LONG_CHAPTER[:30], "rationale": "z"},
    ])
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    assert out == []
    assert fake_client.chat.completions.create.await_count == 0


# ----------------------- 正常解析 -----------------------

@pytest.mark.asyncio
async def test_llm_returns_moments_parsed(selector):
    payload = [
        {
            "moment_summary": "林夜持剑指向群臣",
            "target_characters": ["林夜", "群臣首领"],
            "visual_focus": "前景剑尖反光，中景林夜侧身，远景群臣让出一条路",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": "对峙高潮，画面感最强",
        },
        {
            "moment_summary": "苏婉寒意涌起",
            "target_characters": ["苏婉"],
            "visual_focus": "苏婉特写，背后剑光虚化",
            "source_excerpt": "苏婉站在他身后，心中涌起一阵寒意",
            "rationale": "情绪转折，构图反差",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)

    assert len(out) == 2
    by_summary = {m.moment_summary: m for m in out}
    assert "林夜持剑指向群臣" in by_summary
    assert by_summary["林夜持剑指向群臣"].target_characters == ["林夜", "群臣首领"]
    assert by_summary["林夜持剑指向群臣"].visual_focus.startswith("前景剑尖反光")
    assert "林夜踏入大殿" in by_summary["林夜持剑指向群臣"].source_excerpt


# ----------------------- source_excerpt 不在原文 → drop -----------------------

@pytest.mark.asyncio
async def test_source_excerpt_not_in_chapter_dropped(selector):
    payload = [
        {
            "moment_summary": "真实时刻",
            "target_characters": ["林夜"],
            "visual_focus": "v",
            "source_excerpt": "林夜踏入大殿，目光如刀",  # 在原文里
            "rationale": "r",
        },
        {
            "moment_summary": "虚构时刻",
            "target_characters": ["林夜"],
            "visual_focus": "v",
            "source_excerpt": "这段文字完全不在原文里出现所以应该被丢弃",
            "rationale": "r",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    assert len(out) == 1
    assert out[0].moment_summary == "真实时刻"


# ----------------------- dedup by (summary, characters) -----------------------

@pytest.mark.asyncio
async def test_dedup_identical_moments(selector):
    payload = [
        {
            "moment_summary": "林夜持剑",
            "target_characters": ["林夜"],
            "visual_focus": "v1",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": "r1",
        },
        {
            "moment_summary": "林夜持剑",
            "target_characters": ["林夜"],
            "visual_focus": "v2",
            "source_excerpt": "苏婉站在他身后，心中涌起一阵寒意",
            "rationale": "r2",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    assert len(out) == 1
    assert out[0].visual_focus == "v1"  # 保留先来的


# ----------------------- target_characters 不在白名单 → 过滤 -----------------------

@pytest.mark.asyncio
async def test_unknown_characters_filtered(selector):
    payload = [
        {
            "moment_summary": "林夜持剑",
            "target_characters": ["林夜", "孙悟空"],  # 孙悟空不在大纲白名单
            "visual_focus": "v",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": "r",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    assert len(out) == 1
    assert out[0].target_characters == ["林夜"]


@pytest.mark.asyncio
async def test_no_whitelist_allows_any_character(selector):
    """大纲没给 characters 字段时，不强制白名单（防误杀）。"""
    outline_no_chars = {k: v for k, v in CHAPTER_OUTLINE.items() if k != "characters"}
    payload = [
        {
            "moment_summary": "林夜持剑",
            "target_characters": ["林夜", "路人甲"],
            "visual_focus": "v",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": "r",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, outline_no_chars, max_keyframes=3)
    assert len(out) == 1
    assert set(out[0].target_characters) == {"林夜", "路人甲"}


# ----------------------- max_keyframes 截断 -----------------------

@pytest.mark.asyncio
async def test_max_keyframes_truncates(selector):
    payload = [
        {
            "moment_summary": f"时刻{i}",
            "target_characters": ["林夜"],
            "visual_focus": f"v{i}",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": f"r{i}",
        }
        for i in range(5)
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=2)
    assert len(out) == 2


# ----------------------- LLM 失败 → 返回空 -----------------------

@pytest.mark.asyncio
async def test_llm_failure_returns_empty(selector, monkeypatch):
    monkeypatch.setattr(kms, "KEYFRAME_MOMENT_RETRIES", 1)
    fake_client = _fake_failing_llm()
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        out = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
    # LLM 失败 → 返回空列表（caller 自行 fallback 到 prompt_builder_service）
    assert out == []


# ----------------------- 缓存命中 -----------------------

@pytest.mark.asyncio
async def test_cache_hit_skips_llm(selector):
    payload = [
        {
            "moment_summary": "林夜持剑",
            "target_characters": ["林夜"],
            "visual_focus": "v",
            "source_excerpt": "林夜踏入大殿，目光如刀",
            "rationale": "r",
        },
    ]
    fake_client = _fake_llm_response(payload)
    with patch.object(KeyframeMomentSelector, "client", new=fake_client):
        first = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
        assert len(first) == 1
        assert fake_client.chat.completions.create.await_count == 1

        # 第二次：同输入 → 命中缓存，LLM 不再被调
        second = await selector.select(LONG_CHAPTER, CHAPTER_OUTLINE, max_keyframes=3)
        assert len(second) == 1
        assert fake_client.chat.completions.create.await_count == 1  # 没增加


# ----------------------- helpers -----------------------

def test_extract_outline_characters_handles_str_and_dict():
    out = KeyframeMomentSelector._extract_outline_characters({
        "characters": ["甲", {"name": "乙"}, {"name_cn": "丙"}, "甲"],  # 含重复
    })
    # list[str] + list[dict] 都支持，保序
    assert out == ["甲", "乙", "丙", "甲"]


def test_extract_outline_characters_empty():
    assert KeyframeMomentSelector._extract_outline_characters({}) == []
    assert KeyframeMomentSelector._extract_outline_characters({"characters": []}) == []


def test_normalize_character_list_dedup_and_cap(monkeypatch):
    monkeypatch.setattr(kms, "MAX_CHARACTERS_PER_MOMENT", 2)
    out = KeyframeMomentSelector._normalize_character_list(
        ["甲", "甲", "乙", "丙"], valid_names=["甲", "乙", "丙"],
    )
    assert out == ["甲", "乙"]  # 去重 + 截断到 2


def test_trim_content_preserves_head_and_tail():
    content = "A" * 1000 + "MIDDLE" + "B" * 1000
    trimmed = KeyframeMomentSelector._trim_content(content, budget=200)
    assert len(trimmed) <= 300
    assert trimmed.startswith("A")
    assert trimmed.endswith("B")
    assert "省略" in trimmed


# ----------------------- KeyframeMoment dataclass -----------------------

def test_keyframe_moment_fingerprint():
    m1 = KeyframeMoment("s", ["甲", "乙"], "v", "e", "r")
    m2 = KeyframeMoment("s", ["乙", "甲"], "v2", "e2", "r2")  # 同 summary 同角色集（顺序不同）
    assert m1.fingerprint() == m2.fingerprint()
