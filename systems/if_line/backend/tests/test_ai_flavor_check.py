"""
P4.1 — ai_flavor_check 单元测试。

覆盖：
- 空文本/过短文本 → 软通过 (passed=True, score=100)
- LLM 返回高分 → passed=True
- LLM 返回低分 → passed=False, rewrite_hint 非空
- _call_llm 重试 + 最终失败 → check 软通过 (passed=True)，不影响整章
- 缓存命中 → 不再调 LLM
- _cache_hash 稳定（相同输入同 hash；改 style_rules 不同 hash）
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.agent import ai_flavor_check
from app.agent.ai_flavor_check import (
    AiFlavorReport,
    _cache_hash,
    check,
)


# ----------------------- 短文本 -----------------------

@pytest.mark.asyncio
async def test_short_text_soft_passes():
    report = await check("", "")
    assert report.passed is True
    assert report.score == 100
    assert report.issues == []

    report = await check("短到无法评审。", "")
    assert report.passed is True
    assert report.score == 100


# ----------------------- 高分通过 -----------------------

@pytest.mark.asyncio
async def test_high_score_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_flavor_check, "CACHE_DIR", tmp_path)

    fake_choice = MagicMock()
    fake_choice.message.content = '{"score": 92, "issues": [], "rewrite_hint": ""}'
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(ai_flavor_check, "_get_client", return_value=fake_client):
        report = await check("这是一段足够长的正文用于触发评审。" * 20, "现代都市")

    assert report.passed is True
    assert report.score == 92
    assert report.issues == []


# ----------------------- 低分不通过 -----------------------

@pytest.mark.asyncio
async def test_low_score_fails_with_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_flavor_check, "CACHE_DIR", tmp_path)

    fake_choice = MagicMock()
    fake_choice.message.content = (
        '{"score": 45, "issues": ["套话:不禁", "模板句:时间仿佛静止"], '
        '"rewrite_hint": "Remove the cliché 不禁 and avoid the 时间仿佛静止 template."}'
    )
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(ai_flavor_check, "_get_client", return_value=fake_client):
        report = await check("一段充斥着套话的 AI 味正文。" * 20, "")

    assert report.passed is False
    assert report.score == 45
    assert "套话:不禁" in report.issues
    assert "Remove the cliché" in report.rewrite_hint


# ----------------------- LLM 失败 → 软通过 -----------------------

@pytest.mark.asyncio
async def test_llm_failure_soft_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_flavor_check, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(ai_flavor_check, "AI_FLAVOR_RETRIES", 1)

    fake_client = MagicMock()
    # 总是抛异常 → 重试耗尽 → check 软通过
    fake_client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError("network down")
    )

    with patch.object(ai_flavor_check, "_get_client", return_value=fake_client):
        report = await check("一段足够长的正文用于触发评审。" * 20, "")

    assert report.passed is True
    assert "ai_flavor check unavailable" in report.rewrite_hint


# ----------------------- 缓存命中 -----------------------

@pytest.mark.asyncio
async def test_cache_hit_skips_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(ai_flavor_check, "CACHE_DIR", tmp_path)

    # 第一次写缓存
    text = "可被缓存的长文本。" * 30
    style = "historical"

    fake_choice = MagicMock()
    fake_choice.message.content = '{"score": 80, "issues": [], "rewrite_hint": ""}'
    fake_resp = MagicMock()
    fake_resp.choices = [fake_choice]
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=fake_resp)

    with patch.object(ai_flavor_check, "_get_client", return_value=fake_client):
        first = await check(text, style)
        assert first.score == 80
        assert fake_client.chat.completions.create.await_count == 1

        # 第二次：应命中缓存，create 不再被调用
        second = await check(text, style)
        assert second.score == 80
        assert fake_client.chat.completions.create.await_count == 1  # 没增加


# ----------------------- cache_hash 稳定性 -----------------------

def test_cache_hash_stable_and_sensitive():
    text = "正文一样"
    h1 = _cache_hash(text, "ruleA")
    h2 = _cache_hash(text, "ruleA")
    h3 = _cache_hash(text, "ruleB")
    assert h1 == h2          # 相同输入同 hash
    assert h1 != h3          # 改 style_rules 不同 hash
    assert len(h1) == 64     # sha256 hex
