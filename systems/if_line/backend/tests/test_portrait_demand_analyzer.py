"""
P1.1 — portrait_demand_analyzer 单元测试。

覆盖：
- 空 chapter_indices + 空 story_bible → fallback 返回空
- LLM 返回正常 demands → 解析 + dedup
- LLM 返回未知角色（不在 story_bible 角色卡里）→ drop
- LLM 同一角色同 (emotion,outfit,pose) 重复 → dedup
- LLM 单角色超过 MAX_DEMANDS_PER_CHARACTER → 限频
- LLM 失败 → fallback 到默认 5 档变体
- 缓存命中 → 不再调 LLM
- _character_id 与 prompt_builder_service.generate_character_id 一致（保证下游 Asset.character_id 对齐）
- analyze 全局 dedup 跨章节生效
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services import portrait_demand_analyzer as pda
from app.services.portrait_demand_analyzer import (
    PortraitDemand,
    PortraitDemandAnalyzer,
)
from app.services.prompt_builder_service import prompt_builder_service


# ----------------------- fixtures -----------------------

STORY_BIBLE = {
    "worldview": "修真世界",
    "characters": [
        {"name": "林夜", "appearance": "冷峻青年，黑发，长衫", "role": "主角"},
        {"name": "苏婉", "appearance": "少女，白衣，长发", "role": "女主"},
    ],
}

LONG_CHAPTER = (
    "林夜踏入大殿，目光如刀，扫过两侧群臣。"
    "苏婉站在他身后，心中涌起一阵寒意。"
    "殿外风雷骤起，一道剑光破空而来。"
) * 20  # >400 字


def _fake_llm_response(demands_payload):
    """构造一个 mock LLM client，返回给定 demands dict。"""
    import json as _json
    fake_choice = MagicMock()
    fake_choice.message.content = _json.dumps({"demands": demands_payload}, ensure_ascii=False)
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
def analyzer(tmp_path, monkeypatch):
    """给一个干净的 analyzer，CACHE_DIR 重定向到 tmp_path，DB 用 mock 跳过。"""
    monkeypatch.setattr(pda, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(pda, "PORTRAIT_DEMAND_ENABLED", True)
    monkeypatch.setattr(pda, "PORTRAIT_DEMAND_API_KEY", "sk-test-key")
    a = PortraitDemandAnalyzer()
    # 替换 db 访问：直接返回 LONG_CHAPTER
    a._get_chapter_content = lambda pid, ci: LONG_CHAPTER
    return a


# ----------------------- 空 chapter_indices fallback -----------------------

@pytest.mark.asyncio
async def test_empty_chapter_indices_returns_fallback(analyzer):
    """chapter_indices=[] + 有角色卡 → fallback 默认 5 档变体 × N 角色"""
    out = await analyzer.analyze(project_id=1, chapter_indices=[], story_bible=STORY_BIBLE)
    assert len(out) == 2 * 5  # 2 角色 × 5 变体
    assert all(d.source_chapter_index == -1 for d in out)  # 标记 fallback
    assert all("fallback" in d.rationale for d in out)


@pytest.mark.asyncio
async def test_empty_story_bible_no_characters_returns_empty(analyzer):
    """chapter_indices=[] + 无角色卡 → fallback 也无东西可生成"""
    out = await analyzer.analyze(project_id=1, chapter_indices=[], story_bible={})
    assert out == []


# ----------------------- 正常解析 -----------------------

@pytest.mark.asyncio
async def test_llm_returns_demands_parsed(analyzer):
    demands_payload = [
        {
            "character_name": "林夜",
            "emotion": "angry",
            "outfit": "combat",
            "pose": "standing",
            "source_excerpt": "目光如刀",
            "rationale": "对峙时的冷峻",
        },
        {
            "character_name": "苏婉",
            "emotion": "fear",
            "outfit": "default",
            "pose": "standing",
            "source_excerpt": "心中涌起寒意",
            "rationale": "殿下惊变",
        },
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)

    assert len(out) == 2
    by_name = {d.character_name: d for d in out}
    assert "林夜" in by_name
    assert by_name["林夜"].emotion == "angry"
    assert by_name["林夜"].outfit == "combat"
    assert by_name["苏婉"].emotion == "fear"
    assert "目光如刀" in by_name["林夜"].source_excerpt
    assert by_name["林夜"].source_chapter_index == 1


# ----------------------- dedup by (character_id, emotion, outfit, pose) -----------------------

@pytest.mark.asyncio
async def test_dedup_within_chapter(analyzer):
    """同一章里同 (character_id, emotion, outfit, pose) 只保留 1 条"""
    demands_payload = [
        {"character_name": "林夜", "emotion": "angry", "outfit": "combat", "pose": "standing",
         "source_excerpt": "第一次", "rationale": "r1"},
        {"character_name": "林夜", "emotion": "angry", "outfit": "combat", "pose": "standing",
         "source_excerpt": "第二次", "rationale": "r2"},
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)

    assert len(out) == 1
    assert out[0].source_excerpt == "第一次"  # 保留先来的


@pytest.mark.asyncio
async def test_dedup_across_chapters(analyzer):
    """跨章节：同 (character_id, emotion, outfit, pose) 也 dedup"""
    demands_payload = [
        {"character_name": "林夜", "emotion": "angry", "outfit": "combat", "pose": "standing",
         "source_excerpt": "章一", "rationale": "r1"},
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(
            project_id=1, chapter_indices=[1, 2, 3], story_bible=STORY_BIBLE,
        )
    # 3 章都返回同一条 → 全局 dedup 后只剩 1 条
    assert len(out) == 1


@pytest.mark.asyncio
async def test_same_character_same_pose_keeps_distinct_age_stages(analyzer):
    chapter = (
        "少年时期，林夜站在城门前。多年后，四十五岁的林夜仍站在同一座城门前。"
        "苏婉从远处看着他，风吹过城墙。"
    ) * 20
    analyzer._get_chapter_content = lambda pid, ci: chapter
    fake_client = _fake_llm_response([
        {
            "character_name": "林夜",
            "emotion": "neutral",
            "outfit": "default",
            "pose": "standing",
            "age_group": "teen",
            "source_excerpt": "少年时期，林夜站在城门前。",
            "rationale": "少年时期",
        },
        {
            "character_name": "林夜",
            "emotion": "neutral",
            "outfit": "default",
            "pose": "standing",
            "age_group": "middle_aged",
            "source_excerpt": "四十五岁的林夜仍站在同一座城门前。",
            "rationale": "多年后的中年时期",
        },
    ])
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)

    assert {d.age_group for d in out} == {"teen", "middle_aged"}
    assert len({d.fingerprint() for d in out}) == 2


# ----------------------- 未知角色过滤 -----------------------

@pytest.mark.asyncio
async def test_unknown_character_dropped(analyzer):
    demands_payload = [
        {"character_name": "林夜", "emotion": "angry", "outfit": "combat", "pose": "standing",
         "source_excerpt": "x", "rationale": "r"},
        {"character_name": "路人甲",  # 不在 story_bible 角色卡里
         "emotion": "happy", "outfit": "default", "pose": "standing",
         "source_excerpt": "y", "rationale": "r"},
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)

    assert len(out) == 1
    assert out[0].character_name == "林夜"


@pytest.mark.asyncio
async def test_new_named_character_in_source_gets_base_portrait(analyzer):
    """正文中新命名人物即使不在角色卡，也应建立稳定基础立绘。"""
    chapter = (
        "顾青推门走进大殿，抬头说道：“我来迟了。”"
        "林夜回身看他，殿外风雷骤起。"
    ) * 20
    analyzer._get_chapter_content = lambda pid, ci: chapter
    excerpt = "顾青推门走进大殿，抬头说道：“我来迟了。”"
    fake_client = _fake_llm_response([
        {
            "character_name": "顾青",
            "emotion": "surprise",
            "outfit": "travel",
            "pose": "reaching",
            "source_excerpt": excerpt,
            "rationale": "新人物推门入场并说话",
        },
    ])
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(
            project_id=1,
            chapter_indices=[1],
            story_bible=STORY_BIBLE,
        )

    variants = {
        (d.emotion, d.outfit, d.pose)
        for d in out
        if d.character_name == "顾青"
    }
    assert ("surprise", "travel", "reaching") in variants
    assert ("neutral", "default", "standing") in variants


@pytest.mark.asyncio
async def test_story_bible_short_name_uses_canonical_portrait_identity(analyzer):
    """正文简称不能再生成一套与角色卡全名分离的立绘资产。"""
    story_bible = {"characters": [{"name": "宇智波带土", "role": "主角"}]}
    chapter = ("带土站在训练场中央，说道：‘我不会放弃。’" * 30)
    analyzer._get_chapter_content = lambda pid, ci: chapter
    fake_client = _fake_llm_response([{
        "character_name": "带土",
        "emotion": "neutral",
        "outfit": "default",
        "pose": "standing",
        "source_excerpt": "带土站在训练场中央",
        "rationale": "角色说话并在场",
    }])

    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(
            project_id=21,
            chapter_indices=[1],
            story_bible=story_bible,
        )

    assert len(out) == 1
    assert out[0].character_name == "宇智波带土"
    assert out[0].character_id == prompt_builder_service.generate_character_id(
        "宇智波带土", 21,
    )


# ----------------------- 单角色限频 -----------------------

@pytest.mark.asyncio
async def test_per_character_cap(analyzer, monkeypatch):
    """单角色超过 MAX_DEMANDS_PER_CHARACTER 被截断"""
    monkeypatch.setattr(pda, "MAX_DEMANDS_PER_CHARACTER", 3)
    # 给林夜生成 5 个不同 (emotion, outfit, pose) 组合
    demands_payload = [
        {"character_name": "林夜", "emotion": f"e{i}", "outfit": f"o{i}", "pose": f"p{i}",
         "source_excerpt": str(i), "rationale": "r"}
        for i in range(5)
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)
    assert len(out) == 3


# ----------------------- LLM 失败 → fallback -----------------------

@pytest.mark.asyncio
async def test_llm_failure_falls_back(analyzer, monkeypatch):
    monkeypatch.setattr(pda, "PORTRAIT_DEMAND_RETRIES", 1)
    fake_client = _fake_failing_llm()
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)

    # LLM 失败 → fallback 默认 5 档变体
    assert len(out) == 2 * 5
    assert all(d.source_chapter_index == -1 for d in out)


# ----------------------- API key 缺失 → fallback -----------------------

@pytest.mark.asyncio
async def test_no_api_key_falls_back(analyzer, monkeypatch):
    monkeypatch.setattr(pda, "PORTRAIT_DEMAND_API_KEY", "")
    fake_client = _fake_llm_response([{"character_name": "林夜", "emotion": "x", "outfit": "y", "pose": "z"}])
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        out = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)
    # LLM 不该被调
    assert fake_client.chat.completions.create.await_count == 0
    # 直接 fallback
    assert len(out) == 2 * 5


# ----------------------- 缓存命中 -----------------------

@pytest.mark.asyncio
async def test_cache_hit_skips_llm(analyzer):
    demands_payload = [
        {"character_name": "林夜", "emotion": "angry", "outfit": "combat", "pose": "standing",
         "source_excerpt": "x", "rationale": "r"},
    ]
    fake_client = _fake_llm_response(demands_payload)
    with patch.object(PortraitDemandAnalyzer, "client", new=fake_client):
        first = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)
        assert len(first) == 1
        assert fake_client.chat.completions.create.await_count == 1

        # 第二次：同输入 → 命中缓存，LLM 不再被调
        second = await analyzer.analyze(project_id=1, chapter_indices=[1], story_bible=STORY_BIBLE)
        assert len(second) == 1
        assert fake_client.chat.completions.create.await_count == 1  # 没增加


# ----------------------- character_id 与 prompt_builder_service 一致 -----------------------

def test_character_id_matches_prompt_builder():
    """P1.3 集成时下游 Asset.character_id 列要对齐，必须用同一算法。"""
    for name in ["林夜", "苏婉", "Alice", "路人甲"]:
        for pid in [1, 100, 99999]:
            expected = prompt_builder_service.generate_character_id(name, pid)
            actual = PortraitDemandAnalyzer._character_id(name, pid)
            assert expected == actual, f"mismatch name={name} pid={pid}"


def test_characters_block_preserves_gender():
    """传给需求分析 LLM 的角色卡必须包含标准化 gender。"""
    block, cards = PortraitDemandAnalyzer._build_characters_block({
        "characters": [
            {"name": "林夜", "gender": "男", "appearance": "黑发青年", "role": "主角"},
            {"name": "苏婉", "gender": "女", "appearance": "白衣少女", "role": "女主"},
        ]
    })
    assert len(cards) == 2
    assert "name=林夜 | gender=male" in block
    assert "name=苏婉 | gender=female" in block


# ----------------------- normalize_label -----------------------

def test_normalize_label_handles_dirty_input():
    assert PortraitDemandAnalyzer._normalize_label("  Angry ", "neutral") == "angry"
    assert PortraitDemandAnalyzer._normalize_label(None, "neutral") == "neutral"
    assert PortraitDemandAnalyzer._normalize_label("", "neutral") == "neutral"
    assert PortraitDemandAnalyzer._normalize_label("NONE", "neutral") == "neutral"
    assert PortraitDemandAnalyzer._normalize_label("happy", "neutral") == "happy"


# ----------------------- trim_content 保留头尾 -----------------------

def test_trim_content_preserves_head_and_tail():
    content = "A" * 1000 + "MIDDLE" + "B" * 1000
    trimmed = PortraitDemandAnalyzer._trim_content(content, budget=200)
    assert len(trimmed) <= 300  # budget + 一段省略提示
    assert trimmed.startswith("A")
    assert trimmed.endswith("B")
    assert "省略" in trimmed
