"""P4.2: 验证 presets.py 里 AI_FLAVOR 常量存在且取值正确。"""
from app.agent.presets import AI_FLAVOR_MIN_SCORE, AI_FLAVOR_MAX_RETRIES


def test_ai_flavor_min_score_value():
    assert AI_FLAVOR_MIN_SCORE == 70


def test_ai_flavor_max_retries_value():
    assert AI_FLAVOR_MAX_RETRIES == 2


def test_constants_are_int():
    assert isinstance(AI_FLAVOR_MIN_SCORE, int)
    assert isinstance(AI_FLAVOR_MAX_RETRIES, int)
