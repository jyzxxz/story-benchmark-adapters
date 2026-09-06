"""
背景图生成不再做人物检测。

这里保留原文件名，覆盖新的契约：
- schema-driven 背景只调用一次生图 helper
- 不调用 BackgroundImageValidatorService
- completed + image_path 即可持久化
"""
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.schemas import BackgroundSceneSpec, GenerationResult, PeoplePolicy
from app.services.image_generation_service import ImageGenerationService


def _make_spec() -> BackgroundSceneSpec:
    env = "废弃站台，月台地面湿漉反射冷蓝雨夜光线，远处城市微光极弱。" * 5
    return BackgroundSceneSpec(
        scene_id="s1",
        scene_name="夜雨旧站台",
        scene_selector="test_no_validator_old_station__01",
        scene_type="abandoned_void",
        environment_description=env,
        lighting="冷蓝色雨夜光线",
        weather="细雨",
        time_of_day="night",
        atmosphere="冷清萧索",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="test"),
        forbidden_characters=["林夜", "Lin Ye"],
    )


def _write_png(path: Path, size=(1920, 1080)) -> Path:
    Image.new("RGB", size, (120, 120, 120)).save(path)
    return path


def _png_bytes(size=(1920, 1080)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, (120, 120, 120)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def svc() -> ImageGenerationService:
    return ImageGenerationService()


@pytest.mark.asyncio
async def test_schema_background_generates_once_without_validator(svc, tmp_path, monkeypatch):
    # P1-1 重构后：v2 路径在 BG_VALIDATE_ENABLED=true 时会调 bbox validator。
    # 这个测试名是 "generates_once_without_validator"，语义是"验收关闭时只生成一次且不调 validator"。
    # 显式关闭开关以保持原测试语义。
    from app.services import image_generation_service as mod
    monkeypatch.setattr(mod, "BG_VALIDATE_ENABLED", False)

    spec = _make_spec()
    image_path = _write_png(tmp_path / "bg.png")

    with patch.object(
        svc,
        "_generate_background_once_with_prompt",
        new=AsyncMock(return_value=image_path),
    ) as gen_mock, patch(
        "app.services.background_image_validator_service.BackgroundImageValidatorService.validate",
        side_effect=AssertionError("validator must not be called"),
    ) as validate_mock:
        result = await svc.generate_background_with_validation(
            spec,
            forbidden_characters=["林夜"],
            genre="historical",
        )

    assert result.status == "completed"
    assert result.retry_count == 0
    assert result.fallback_type == "none"
    assert result.validation_results == []
    assert result.image_path
    assert gen_mock.await_count == 1
    assert validate_mock.call_count == 0


def test_completed_result_persists_without_validation_results():
    from app.services.asset_management_service import AssetManagementService

    svc = AssetManagementService.__new__(AssetManagementService)
    sentinel = object()
    svc._persist_background_asset = lambda *a, **k: sentinel

    gr = GenerationResult(
        status="completed",
        image_path="/tmp/x.png",
        final_prompt="x",
        retry_count=0,
        fallback_type="none",
        scene_selector="x__01",
        validation_results=[],
    )

    asset = svc._persist_background_asset_if_passed(
        project_id="p",
        chapter_index=0,
        spec=_make_spec(),
        generation_result=gr,
        genre="historical",
    )
    assert asset is sentinel


def test_completed_result_without_image_path_does_not_persist():
    from app.services.asset_management_service import AssetManagementService

    svc = AssetManagementService.__new__(AssetManagementService)
    svc._persist_background_asset = lambda *a, **k: pytest.fail("should not persist")

    gr = GenerationResult(
        status="completed",
        image_path=None,
        final_prompt="x",
        retry_count=0,
        fallback_type="none",
        scene_selector="x__01",
        validation_results=[],
    )

    asset = svc._persist_background_asset_if_passed(
        project_id="p",
        chapter_index=0,
        spec=_make_spec(),
        generation_result=gr,
        genre="historical",
    )
    assert asset is None


@pytest.mark.asyncio
async def test_legacy_background_generates_once_without_validator(monkeypatch):
    from app.services import image_generation_service as mod

    svc = mod.image_generation_service
    calls = {"gen": 0}

    async def fake_call(prompt, *args, **kwargs):
        calls["gen"] += 1
        return _png_bytes()

    original_call = svc._call_cogview_api
    svc._call_cogview_api = fake_call
    monkeypatch.setattr(mod, "IMAGE_GENERATION_ENABLED", True)
    monkeypatch.setattr(mod, "AI_IMAGE_API_KEY", "fake-key")

    try:
        with patch(
            "app.services.background_image_validator_service.BackgroundImageValidatorService.validate",
            side_effect=AssertionError("validator must not be called"),
        ) as validate_mock:
            result = await svc.generate_background(
                scene_name=f"market_no_validator_{id(calls)}",
                scene_description="busy market",
                mood="day",
                final_prompt=f"market unique_{id(calls)}",
                forbidden_characters=["林夜"],
            )
    finally:
        svc._call_cogview_api = original_call

    assert result["success"] is True
    assert calls["gen"] == 1
    assert validate_mock.call_count == 0
    assert result["validation_skipped"] is True
    assert result["validation_results"] == []
