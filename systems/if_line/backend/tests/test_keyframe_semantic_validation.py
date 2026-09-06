"""关键帧语义验收器与脚本槽渲染器接线测试。

覆盖：
- KeyframeSemanticValidatorService 的 VLM 判定解析（通过/不通过/空文本自动过）
- KeyframeTaskRenderer 的验收-重试-隔离循环（mock 生成与验收器）
- BackgroundTaskRenderer 改走冻结 prompt 实体验收入口
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.keyframe_semantic_validator_service import (
    KeyframeSemanticValidation,
    KeyframeSemanticValidatorService,
)


def _vlm_client(payload: dict):
    async def _content(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
                )
            ]
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_content)))


def _image(tmp_path: Path) -> Path:
    p = tmp_path / "kf.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return p


def test_validator_passes_when_scene_matches(tmp_path):
    client = _vlm_client({"passes": True, "missing_elements": [], "confidence": 0.9})
    verdict = asyncio.run(KeyframeSemanticValidatorService(vlm_client=client).validate(
        _image(tmp_path), action_text="剧院墙壁化作流动光纹，冷蓝火焰照亮倒悬的银河"
    ))
    assert isinstance(verdict, KeyframeSemanticValidation)
    assert verdict.passed is True


def test_validator_fails_with_missing_elements(tmp_path):
    client = _vlm_client(
        {
            "passes": False,
            "missing_elements": ["镜像世界剧院", "流动光纹"],
            "mismatch_reason": "画面呈现的是黄昏街道与店铺",
            "confidence": 0.85,
        }
    )
    verdict = asyncio.run(KeyframeSemanticValidatorService(vlm_client=client).validate(
        _image(tmp_path), action_text="剧院墙壁化作流动光纹"
    ))
    assert verdict.passed is False
    assert "镜像世界剧院" in verdict.missing_elements
    assert any("黄昏街道" in reason for reason in verdict.reasons)


def test_validator_auto_passes_without_action_text(tmp_path):
    verdict = asyncio.run(KeyframeSemanticValidatorService(vlm_client=None).validate(
        _image(tmp_path), action_text="  "
    ))
    assert verdict.passed is True


def test_validator_missing_image_fails(tmp_path):
    verdict = asyncio.run(KeyframeSemanticValidatorService(vlm_client=None).validate(
        tmp_path / "missing.png", action_text="任何剧情"
    ))
    assert verdict.passed is False


def _keyframe_parameters(**extra) -> dict:
    return {
        "asset_id": 1,
        "asset_type": "keyframe",
        "cache_material": {
            "normalized_asset_spec": {
                "target_name": "关键帧 paragraph-0020",
                "taxonomy": {},
            }
        },
        "render_spec": {
            "prompt": "视觉小说剧情关键帧，剧院化作镜像世界，无文字",
            "extra_parameters": {
                "characters": [{"name": "陈默", "appearance": "外卖骑手"}],
                "action": "剧院墙壁化作流动光纹，冷蓝色火焰照亮倒悬的银河",
                "emotion": "intense",
                **extra,
            },
        },
    }


def _fake_image_result(tmp_path: Path, tag: str) -> dict:
    p = tmp_path / f"kf-{tag}.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    return {"success": True, "image_path": str(p), "prompt": "p", "validation_results": []}


def test_keyframe_renderer_retries_then_passes(tmp_path, monkeypatch):
    from app.workers import asset_tasks

    calls = {"generate": 0, "validate": 0}
    results = [_fake_image_result(tmp_path, "first"), _fake_image_result(tmp_path, "second")]

    async def fake_generate_keyframe(**kwargs):
        calls["generate"] += 1
        return results[min(calls["generate"] - 1, len(results) - 1)]

    monkeypatch.setattr(
        asset_tasks.image_generation_service, "generate_keyframe", fake_generate_keyframe
    )

    class FlakyValidator:
        def __init__(self, *args, **kwargs):
            pass

        def validate(self, image_path, *, action_text, character_names, scene_hint=""):
            import asyncio

            async def _run():
                calls["validate"] += 1
                return KeyframeSemanticValidation(
                    passed=calls["validate"] > 1,
                    missing_elements=("镜像世界剧院",) if calls["validate"] == 1 else (),
                    reasons=("画面是黄昏街道",) if calls["validate"] == 1 else (),
                )

            return _run()

    monkeypatch.setattr(
        "app.services.keyframe_semantic_validator_service.KeyframeSemanticValidatorService",
        FlakyValidator,
    )
    monkeypatch.setattr(
        asset_tasks, "_persist_provider_output", lambda role, result: SimpleNamespace(
            stored_file=None, actual_cost=None
        )
    )

    asset_tasks.KeyframeTaskRenderer().render(_keyframe_parameters())
    assert calls["generate"] == 2  # 首图 + 1 次语义重试
    assert calls["validate"] == 2


def test_keyframe_renderer_quarantines_after_max_retries(tmp_path, monkeypatch):
    from app.workers import asset_tasks

    async def fake_generate_keyframe(**kwargs):
        return _fake_image_result(tmp_path, "only")

    monkeypatch.setattr(
        asset_tasks.image_generation_service, "generate_keyframe", fake_generate_keyframe
    )

    class AlwaysFailValidator:
        def __init__(self, *args, **kwargs):
            pass

        def validate(self, image_path, *, action_text, character_names, scene_hint=""):
            import asyncio

            async def _run():
                return KeyframeSemanticValidation(
                    passed=False,
                    missing_elements=("镜像世界剧院",),
                    reasons=("画面呈现黄昏街道",),
                )

            return _run()

    monkeypatch.setattr(
        "app.services.keyframe_semantic_validator_service.KeyframeSemanticValidatorService",
        AlwaysFailValidator,
    )

    with pytest.raises(RuntimeError, match="quarantined: keyframe_semantic_mismatch"):
        asset_tasks.KeyframeTaskRenderer().render(_keyframe_parameters())


def test_background_renderer_calls_frozen_validation_entry(monkeypatch):
    from app.workers import asset_tasks

    captured = {}

    async def fake_frozen(**kwargs):
        captured.update(kwargs)
        return {"success": True, "image_path": "x.png", "validation_results": []}

    monkeypatch.setattr(
        asset_tasks.image_generation_service,
        "generate_background_frozen_with_entity_validation",
        fake_frozen,
    )
    monkeypatch.setattr(
        asset_tasks, "_persist_provider_output", lambda role, result: SimpleNamespace(
            stored_file=None, actual_cost=None
        )
    )

    asset_tasks.BackgroundTaskRenderer().render(
        {
            "asset_id": 2,
            "asset_type": "background",
            "cache_material": {
                "normalized_asset_spec": {
                    "target_name": "潮汐书屋",
                    "taxonomy": {"mood": "night"},
                }
            },
            "render_spec": {
                "prompt": "视觉小说背景，旧书店内，无人，无文字",
                "extra_parameters": {
                    "forbidden_characters": ["林潮生", "江珊"],
                },
            },
        }
    )
    assert captured["final_prompt"] == "视觉小说背景，旧书店内，无人，无文字"
    assert captured["forbidden_characters"] == ["林潮生", "江珊"]
    assert captured["forbidden_entities"] == [
        {"canonical_name": "林潮生", "species": "human"},
        {"canonical_name": "江珊", "species": "human"},
    ]


def test_frozen_background_validation_loop(tmp_path, monkeypatch):
    """泄漏→escalation 重试→仍泄漏→quarantine(success=False)。"""
    from app.services import image_generation_service as igs

    images = [tmp_path / f"bg-{i}.png" for i in range(3)]
    for p in images:
        p.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    async def fake_generate_background(**kwargs):
        return {"success": True, "image_path": str(images[0]), "prompt": "p"}

    async def fake_once(prompt, **kwargs):
        return images[1]

    monkeypatch.setattr(
        igs.image_generation_service, "generate_background", fake_generate_background
    )
    monkeypatch.setattr(
        igs.image_generation_service,
        "_generate_background_once_with_prompt",
        fake_once,
    )
    monkeypatch.setattr(
        igs.image_generation_service,
        "_postprocess_background_preserve_aspect",
        lambda path, size: path,
    )

    from app.services.background_story_entity_validator_service import (
        BackgroundStoryEntityValidation,
    )

    verdicts = [
        BackgroundStoryEntityValidation(passed=False, detected_entity_count=1),
        BackgroundStoryEntityValidation(passed=False, detected_entity_count=1),
        BackgroundStoryEntityValidation(passed=False, detected_entity_count=1),
    ]

    class FakeValidator:
        def __init__(self, *args, **kwargs):
            pass

        async def validate(self, image_path, entities):
            return verdicts.pop(0) if verdicts else verdicts[-1]

    monkeypatch.setattr(
        "app.services.background_story_entity_validator_service"
        ".BackgroundStoryEntityValidatorService",
        FakeValidator,
    )

    import asyncio as _asyncio

    result = _asyncio.run(
        igs.image_generation_service.generate_background_frozen_with_entity_validation(
            scene_name="潮汐书屋",
            scene_description="旧书店内",
            final_prompt="视觉小说背景，旧书店内，无人，无文字",
            forbidden_characters=["林潮生"],
            forbidden_entities=[{"canonical_name": "林潮生", "species": "human"}],
        )
    )
    assert result["success"] is False
    assert "quarantined: forbidden_story_entity_leak" in result["error"]
    assert len(result["validation_results"]) == 3  # 首检 + 2 次重试
