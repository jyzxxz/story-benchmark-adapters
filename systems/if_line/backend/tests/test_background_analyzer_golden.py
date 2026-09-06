"""
L5.11 — Stage_Background_AR 场景提取器 golden 测试。

需要 LLM KEY 的 case 标记为 skip；本地可运行的 case 验证 analyzer 的:
- _slugify_zh / _build_scene_selector 确定性
- _collect_forbidden_characters 合并去重
- _scene_fingerprint 切场景逻辑（L5.04 已详测，这里 smoke）
"""
import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.background_scene_analyzer_service import BackgroundSceneAnalyzerService


@pytest.fixture
def svc() -> BackgroundSceneAnalyzerService:
    return BackgroundSceneAnalyzerService()


def _has_llm_key():
    return bool(
        os.getenv("AI_IMAGE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("ZHIPU_API_KEY")
    )


# -------------------- 本地 deterministic 用例 --------------------

def test_slugify_zh_deterministic(svc):
    """相同输入 → 相同 ASCII slug"""
    s1 = svc._slugify_zh("夜雨旧站台")
    s2 = svc._slugify_zh("夜雨旧站台")
    assert s1 == s2


def test_build_scene_selector_format(svc):
    """selector 满足 [a-z0-9][a-z0-9_-]* 模式"""
    spec_dict = {
        "scene_type": "private_interior",
        "time_of_day": "night",
        "weather": "rain",
        "people_policy": {"mode": "empty_required"},
    }
    sel = svc._build_scene_selector(spec_dict, scene_name="庭院", idx=1)
    import re
    assert re.fullmatch(r"[a-z0-9][a-z0-9_\-]*", sel), f"bad selector: {sel}"


def test_collect_forbidden_characters_dedup(svc):
    """outline + story_bible 合并 + 去重"""
    outline = {
        "characters": ["林夜", "苏晚晴"],
    }
    story_bible = {
        "characters": [
            {"name": "林夜", "aliases": ["阿夜", "Lin Ye"]},
            {"name": "苏晚晴", "aliases": ["晚晴"]},
            {"name": "赵峰"},
        ],
    }
    result = svc._collect_forbidden_characters(outline, story_bible)
    assert "林夜" in result
    assert "苏晚晴" in result
    assert "赵峰" in result
    # 去重
    assert result.count("林夜") == 1


def test_scene_fingerprint_stable(svc):
    """相同 spec_dict → 相同 fingerprint"""
    spec_dict = {
        "scene_type": "abandoned_void",
        "time_of_day": "night",
        "weather": "rain",
        "people_policy": {"mode": "empty_required"},
    }
    fp1 = svc._scene_fingerprint(spec_dict)
    fp2 = svc._scene_fingerprint(spec_dict)
    assert fp1 == fp2


# -------------------- 需要 LLM KEY 的 golden case（skip）--------------------

LLM_GOLDEN_CASES = [
    ("historical", "single_scene", "古风悬疑，单场景"),
    ("historical", "multi_scene", "古风悬疑，多场景"),
    ("modern", "single_scene", "现代都市，单场景"),
    ("modern", "multi_scene", "现代都市，多场景"),
    ("scifi", "single_scene", "科幻，单场景"),
    ("scifi", "multi_scene", "科幻，多场景"),
    ("fantasy", "single_scene", "奇幻，单场景"),
    ("fantasy", "multi_scene", "奇幻，多场景"),
    ("anime", "single_scene", "动漫，单场景"),
    ("anime", "multi_scene", "动漫，多场景"),
]


@pytest.mark.parametrize("genre,scene_type,desc", LLM_GOLDEN_CASES)
def test_golden_analyzer_cases(svc, genre, scene_type, desc):
    if not _has_llm_key():
        pytest.skip("LLM API key not set; skip golden case")
    pytest.skip("golden fixture data not bundled in this test run")
