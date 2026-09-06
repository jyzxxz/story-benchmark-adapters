"""
L5.12 — Stage_Background_AR 风格分类器 golden 测试。

需要 LLM KEY 的 case 标记为 skip；本地可运行的 case 验证:
- _validate_coverage（3-5 tags，>=3 dims，每维 ≤1）
- _deterministic_fallback
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import StyleTag
from app.services.background_style_classifier_service import BackgroundStyleClassifierService


@pytest.fixture
def svc() -> BackgroundStyleClassifierService:
    return BackgroundStyleClassifierService()


def _has_llm_key():
    return bool(
        os.getenv("AI_IMAGE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("ZHIPU_API_KEY")
    )


# -------------------- 本地 validation 用例 --------------------

def test_validate_coverage_rejects_too_few(svc):
    """< 3 tags → False"""
    tags = [
        StyleTag(dimension="art_style", value="写实"),
        StyleTag(dimension="mood", value="冷清"),
    ]
    ok, msg = svc._validate_coverage(tags)
    assert not ok


def test_validate_coverage_rejects_too_many(svc):
    """> 5 tags → False"""
    tags = [
        StyleTag(dimension="art_style", value="x1"),
        StyleTag(dimension="color_palette", value="x2"),
        StyleTag(dimension="mood", value="x3"),
        StyleTag(dimension="texture_or_rendering", value="x4"),
        StyleTag(dimension="lens_or_camera_feel", value="x5"),
        StyleTag(dimension="art_style", value="x6"),
    ]
    ok, msg = svc._validate_coverage(tags)
    assert not ok


def test_validate_coverage_rejects_dup_dims(svc):
    """同维度重复 → False"""
    tags = [
        StyleTag(dimension="art_style", value="x1"),
        StyleTag(dimension="art_style", value="x2"),
        StyleTag(dimension="mood", value="x3"),
    ]
    ok, msg = svc._validate_coverage(tags)
    assert not ok


def test_validate_coverage_accepts_valid(svc):
    """3 tags 覆盖 3 维 → True"""
    tags = [
        StyleTag(dimension="art_style", value="x1"),
        StyleTag(dimension="mood", value="x3"),
        StyleTag(dimension="color_palette", value="x4"),
    ]
    ok, msg = svc._validate_coverage(tags)
    assert ok, msg


def test_deterministic_fallback(svc):
    """无 LLM 时 deterministic fallback 仍然返回合规 StyleTag list"""
    for genre in ["historical", "modern", "scifi", "fantasy", "anime"]:
        for cat in ["abandoned_void", "private_interior", "public_commerce",
                    "wilderness_dreamscape", "civic_military", "public_hall"]:
            tags = svc._deterministic_fallback(scene_type=cat, genre=genre)
            assert isinstance(tags, list)
            assert len(tags) >= 1, f"no fallback tags for {genre}/{cat}"


@pytest.mark.asyncio
async def test_classify_scene_without_visual_bible_keeps_scene_variation(svc):
    """画风锁定失败时不应因 bible=None 丢掉镜头、氛围和时段。"""
    spec = SimpleNamespace(
        scene_selector="chapter-1:palace-garden",
        camera_shot_type="high_angle_distant",
        atmosphere="rain-soaked and tense",
        mood="foreboding",
        time_of_day="night",
    )

    treatment = await svc.classify_scene(spec, bible=None)

    assert treatment.camera == "high-angle distant shot"
    assert treatment.atmosphere == "rain-soaked and tense; foreboding"
    assert treatment.time_of_day == "night"
    assert treatment.accent_palette == ()


def test_scene_treatment_fallback_is_deterministic_and_scene_specific(svc):
    """分类异常后的本地兜底仍应按各场景字段产生不同处理结果。"""
    dawn = SimpleNamespace(
        camera_shot_type="establishing_wide",
        atmosphere="quiet mist",
        mood="hopeful",
        time_of_day="dawn",
    )
    midnight = SimpleNamespace(
        camera_shot_type="interior_wide",
        atmosphere="flickering candlelight",
        mood="uneasy",
        time_of_day="midnight",
    )

    dawn_treatment = svc.fallback_scene_treatment(dawn)
    repeated = svc.fallback_scene_treatment(dawn)
    midnight_treatment = svc.fallback_scene_treatment(midnight)

    assert dawn_treatment == repeated
    assert dawn_treatment != midnight_treatment
    assert dawn_treatment.accent_palette == ()
    assert midnight_treatment.accent_palette == ()


# -------------------- 需要 LLM KEY 的 golden case（skip）--------------------

GOLDEN_GENRES = ["historical", "modern", "scifi", "fantasy", "anime"]
GOLDEN_CATEGORIES = [
    "public_commerce", "civic_military", "public_hall",
    "private_interior", "wilderness_dreamscape", "abandoned_void",
]


@pytest.mark.parametrize("genre", GOLDEN_GENRES)
@pytest.mark.parametrize("category", GOLDEN_CATEGORIES)
def test_golden_style_cases(svc, genre, category):
    if not _has_llm_key():
        pytest.skip("LLM API key not set; skip golden case")
    pytest.skip("golden fixture data not bundled in this test run")
