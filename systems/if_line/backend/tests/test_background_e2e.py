"""
L5.16 — Stage_Background_AR 端到端测试（mock CogView-4）。

完整链路：analyzer(spec) → classifier → assembler → generate(mock) → validator(mock) → persist。
所有外部调用（LLM、CogView、VLM）都被 mock；只验证内部 wiring。
"""
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, PeoplePolicy


def _write_png(path: Path, size=(1920, 1080)) -> Path:
    from PIL import Image
    Image.new("RGB", size, (120, 120, 120)).save(path)
    return path


def _has_llm_key():
    return bool(
        os.getenv("AI_IMAGE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("ZHIPU_API_KEY")
    )


# -------------------- L5.16 mocked e2e --------------------

@pytest.mark.asyncio
async def test_e2e_mocked_pipeline(tmp_path, monkeypatch):
    """e2e: 全链路 mock，验证不做人像检测也能 status=completed

    P1-1 重构后：v2 路径在 BG_VALIDATE_ENABLED=true 时会调 bbox validator。
    本测试保持"不调 validator"语义，显式关闭开关。
    """
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)
    from app.services.image_generation_service import ImageGenerationService

    env = "废弃老站台，月台反射冷蓝雨夜光线，锈蚀站牌剥落墙面，旧式长椅积水轨道，远处城市微光极弱。" * 3
    spec = BackgroundSceneSpec(
        scene_id="s1",
        scene_name="夜雨旧站台",
        scene_selector="e2e_old_station__night_rain__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷蓝色雨夜光线",
        weather="细雨",
        time_of_day="night",
        atmosphere="冷清萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="e2e test"),
        forbidden_characters=["林夜", "Lin Ye"],
    )

    svc = ImageGenerationService()
    image_path = _write_png(tmp_path / "e2e_x.png")

    with patch.object(
        svc, "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ), patch(
        "app.services.background_image_validator_service.BackgroundImageValidatorService.validate",
        side_effect=AssertionError("validator must not be called"),
    ) as validate_mock:
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["林夜"], genre="historical",
        )

    assert result.status == "completed"
    assert result.retry_count == 0
    assert result.validation_results == []
    assert "视觉小说背景图" in result.final_prompt
    assert "纯空环境" in result.final_prompt
    assert "林夜" in result.final_prompt
    assert validate_mock.call_count == 0


@pytest.mark.asyncio
async def test_e2e_mocked_no_validator_does_not_fallback(tmp_path, monkeypatch):
    """e2e: 不调用 validator，因此不会进入 scene_aware_safety fallback。

    P1-1 重构后：v2 路径在 BG_VALIDATE_ENABLED=true 时会调 bbox validator。
    本测试保持"不调 validator"语义，显式关闭开关。
    """
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)
    from app.services.image_generation_service import ImageGenerationService

    env = "废弃老站台，月台反射冷蓝雨夜光线，锈蚀站牌剥落墙面，旧式长椅积水轨道，远处城市微光极弱。" * 3
    spec = BackgroundSceneSpec(
        scene_id="s1",
        scene_name="夜雨旧站台",
        scene_selector="e2e2_old_station__night_rain__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷蓝色雨夜光线",
        weather="细雨", time_of_day="night",
        atmosphere="冷清萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="e2e test"),
        forbidden_characters=["林夜"],
    )

    svc = ImageGenerationService()
    image_path = _write_png(tmp_path / "e2e2_x.png")

    with patch.object(
        svc, "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ), patch(
        "app.services.background_image_validator_service.BackgroundImageValidatorService.validate",
        side_effect=AssertionError("validator must not be called"),
    ) as validate_mock:
        result = await svc.generate_background_with_validation(
            spec, forbidden_characters=["林夜"], genre="historical",
        )

    assert result.status == "completed"
    assert result.fallback_type == "none"
    assert result.validation_results == []
    assert "视觉小说背景图" in result.final_prompt
    assert "纯空环境" in result.final_prompt
    assert validate_mock.call_count == 0


# -------------------- real LLM e2e（skip without key）--------------------

@pytest.mark.asyncio
async def test_e2e_real_llm_skip_without_key():
    if not _has_llm_key():
        pytest.skip("LLM API key not set; skip real e2e")
    # 真实 e2e 需要 project fixture + chapter content；此 stub 仅记录断言架构
    pytest.skip("real e2e fixture data not bundled in this test run")
