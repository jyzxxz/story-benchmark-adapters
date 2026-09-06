"""RPM 限流相关行为测试（无真实 API 调用）。

覆盖：
- ``_is_rate_limit_error`` 对 MiniMax code=1002 的识别
- ``_is_transient_tts_error`` 同样覆盖限流标记
- ``_RPMCooldownGate`` 的 trigger / is_active / remaining 三态
- ``ChapterVoiceService._retry_delay_for_error`` 对限流错误的退避长度
- ``_RPMTokenBucket`` 滑动窗口 acquire 行为
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.chapter_voice_service import (
    _is_rate_limit_error,
    _is_transient_tts_error,
    _RPMCooldownGate,
    _rpm_cooldown,
    CHAPTER_VOICE_RPM_COOLDOWN_SECONDS,
    CHAPTER_VOICE_RPM_RETRY_DELAY,
    ChapterVoiceService,
)
from app.services.tts_service import _RPMTokenBucket


# ============================================================
# 错误识别
# ============================================================

def test_minimax_code_1002_recognized_as_rate_limit():
    err = "minimax [http=200 code=1002] rate limit exceeded(RPM)"
    assert _is_rate_limit_error(err)
    # 同样必须被识别为 transient（用于进入重试分支）
    assert _is_transient_tts_error(err)


def test_classic_429_still_recognized():
    assert _is_rate_limit_error("HTTP 429 too many requests")
    assert _is_transient_tts_error("429 too many requests")


def test_qps_token_recognized():
    assert _is_rate_limit_error("qps exceeded")
    assert _is_transient_tts_error("qps exceeded")


def test_permanent_voice_error_not_treated_as_rate_limit():
    err = "xunfei [10163] vcn not authorized"
    assert not _is_rate_limit_error(err)
    # voice-invalid 同时也不是 transient
    assert not _is_transient_tts_error(err)


def test_empty_or_none_input_safe():
    assert not _is_rate_limit_error(None)
    assert not _is_rate_limit_error("")
    assert not _is_transient_tts_error("")
    assert not _is_transient_tts_error(None)


# ============================================================
# Cooldown gate
# ============================================================

def test_cooldown_gate_inactive_until_trigger():
    gate = _RPMCooldownGate()
    assert not gate.is_active()
    assert gate.remaining() == 0.0


def test_cooldown_gate_active_after_trigger():
    gate = _RPMCooldownGate()
    gate.trigger(10.0, now=100.0)
    assert gate.is_active(now=100.0)
    assert gate.is_active(now=109.0)
    assert not gate.is_active(now=110.1)
    # remaining 单调递减
    assert 4.9 < gate.remaining(now=105.0) < 5.1


def test_cooldown_gate_takes_larger_value():
    """新 trigger 不应缩短已有 cooldown。"""
    gate = _RPMCooldownGate()
    gate.trigger(30.0, now=100.0)
    gate.trigger(5.0, now=101.0)  # 这个 trigger 更短
    assert gate.is_active(now=129.0)
    assert not gate.is_active(now=131.0)


def test_module_singleton_is_rpm_cooldown():
    """chapter_voice_service._rpm_cooldown 是模块级共享单例。"""
    assert isinstance(_rpm_cooldown, _RPMCooldownGate)


# ============================================================
# Retry delay —— RPM 错误强制长退避
# ============================================================

def test_retry_delay_for_rate_limit_is_longer_than_default():
    base = ChapterVoiceService._retry_delay(0)  # 默认指数退避 attempt=0
    rpm = ChapterVoiceService._retry_delay_for_error(0, "code=1002 rate limit RPM")
    assert rpm >= CHAPTER_VOICE_RPM_RETRY_DELAY
    assert rpm > base


def test_retry_delay_for_non_rate_limit_uses_default():
    """普通超时错误走指数退避，不被 RPM 下限抬高。"""
    timeout_delay = ChapterVoiceService._retry_delay_for_error(0, "tts timeout")
    base = ChapterVoiceService._retry_delay(0)
    assert timeout_delay == base


# ============================================================
# Token bucket —— 滑动窗口节流
# ============================================================

def test_token_bucket_disabled_when_limit_zero():
    """limit=0 时不节流，acquire 立即返回。"""
    bucket = _RPMTokenBucket(0, 60.0)
    # 不应抛异常，也不应 sleep
    start = time.monotonic()
    asyncio.run(bucket.acquire())
    asyncio.run(bucket.acquire())
    asyncio.run(bucket.acquire())
    elapsed = time.monotonic() - start
    assert elapsed < 0.1


def test_token_bucket_allows_up_to_limit_without_waiting():
    bucket = _RPMTokenBucket(3, 60.0)
    start = time.monotonic()
    for _ in range(3):
        asyncio.run(bucket.acquire())
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"前 {3} 个 token 不应等待，实际 {elapsed:.2f}s"


def test_token_bucket_blocks_when_full():
    """超过 limit 的第 N+1 个请求必须等待。"""
    bucket = _RPMTokenBucket(2, 0.3)  # 短窗口加速测试
    asyncio.run(bucket.acquire())
    asyncio.run(bucket.acquire())
    # 第 3 个必须等到队首过期
    start = time.monotonic()
    asyncio.run(bucket.acquire())
    elapsed = time.monotonic() - start
    assert elapsed >= 0.25, f"应等待接近 window=0.3s，实际 {elapsed:.2f}s"
