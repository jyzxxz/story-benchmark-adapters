"""Stage_Background_Entity_Exclusion §XVI — VLM validator + retry → quarantine.

Mocked VLM only — never hits a real API. Covers:

- validator returns pass when VLM says no matches
- validator returns leak when confidence≥0.5 AND identity_similarity≥0.5
- validator returns leak when indirect_representation=True
- validator returns leak when VLM reports any_human_present=True
- validator gracefully passes when VLM client raises
- generate_background_with_validation retries on leak then quarantines after
  BG_ENTITY_VALIDATE_MAX_RETRIES attempts
- quarantined result does NOT get persisted (status filter)
"""
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.background_story_entity_validator_service import (
    BackgroundStoryEntityValidation,
    BackgroundStoryEntityValidatorService,
    StoryEntityLeak,
    _interpret_vlm_result,
    _loose_json,
)
from app.schemas import BackgroundSceneSpec, PeoplePolicy
from app.services.image_generation_service import ImageGenerationService


# ----------------------------- _loose_json -----------------------------

def test_loose_json_strips_code_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _loose_json(raw) == {"a": 1}


def test_loose_json_recovers_from_text_around():
    raw = 'Sure! Here is the answer: {"x": 2, "y": 3} thanks'
    assert _loose_json(raw) == {"x": 2, "y": 3}


def test_loose_json_empty_returns_empty():
    assert _loose_json("") == {}


# ----------------------------- _interpret_vlm_result -----------------------------

def test_interpret_passes_when_no_matches():
    data = {"forbidden_entity_matches": [], "any_human_present": False}
    result = _interpret_vlm_result(data, entities=[])
    assert result.passed is True
    assert result.detected_entity_count == 0


def test_interpret_leak_when_confidence_and_similarity_high():
    data = {
        "forbidden_entity_matches": [{
            "forbidden_character_id": "r17",
            "forbidden_name": "R-17",
            "detected_entity_type": "robot",
            "identity_similarity": 0.8,
            "confidence": 0.9,
            "indirect_representation": False,
            "evidence": ["right-eye red sensor visible"],
        }],
        "any_human_present": False,
    }
    result = _interpret_vlm_result(data, entities=[{"character_id": "r17"}])
    assert result.passed is False
    assert result.detected_entity_count == 1
    assert result.quarantinable is True


def test_interpret_leak_when_indirect_representation():
    data = {
        "forbidden_entity_matches": [{
            "forbidden_name": "林夜",
            "detected_entity_type": "human",
            "identity_similarity": 0.2,
            "confidence": 0.7,
            "indirect_representation": True,
            "evidence": ["poster on wall"],
        }],
        "any_human_present": False,
    }
    result = _interpret_vlm_result(data, entities=[])
    assert result.passed is False
    assert result.detected_entity_count == 1


def test_interpret_leak_when_any_human_present_synthesizes():
    data = {"forbidden_entity_matches": [], "any_human_present": True}
    result = _interpret_vlm_result(data, entities=[])
    assert result.passed is False
    # Synthesized human leak
    assert any(l.detected_entity_type == "human" for l in result.matched_forbidden_entities)


def test_interpret_skips_low_confidence():
    data = {
        "forbidden_entity_matches": [{
            "forbidden_name": "R-17",
            "identity_similarity": 0.9,
            "confidence": 0.3,
            "indirect_representation": False,
        }],
        "any_human_present": False,
    }
    result = _interpret_vlm_result(data, entities=[])
    assert result.passed is True


# ----------------------------- StoryEntityLeak.quarantinable -----------------------------

def test_quarantinable_when_name_present():
    leak = StoryEntityLeak(
        detected_entity_type="robot",
        forbidden_name="R-17",
        confidence=0.9,
        identity_similarity=0.8,
    )
    val = BackgroundStoryEntityValidation(
        passed=False,
        detected_entity_count=1,
        matched_forbidden_entities=(leak,),
    )
    assert val.quarantinable is True


def test_not_quarantinable_for_generic_human():
    leak = StoryEntityLeak(
        detected_entity_type="human",
        confidence=0.8,
    )
    val = BackgroundStoryEntityValidation(
        passed=False,
        detected_entity_count=1,
        matched_forbidden_entities=(leak,),
    )
    assert val.quarantinable is False


# ----------------------------- service.validate (mocked) -----------------------------


def _fake_img_path(tmp_path: Path) -> Path:
    p = tmp_path / "bg.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
    return p


class _FakeClient:
    def __init__(self, payload: dict):
        self._payload = payload
        self.chat = MagicMock()
        self.chat.completions = MagicMock()
        self.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=__import__("json").dumps(payload)))]
        ))


def test_validate_pass_when_vlm_reports_no_matches(tmp_path):
    img = _fake_img_path(tmp_path)
    client = _FakeClient({"forbidden_entity_matches": [], "any_human_present": False})
    svc = BackgroundStoryEntityValidatorService(vlm_client=client)
    import asyncio
    result = asyncio.run(svc.validate(img, [{"character_id": "r17", "canonical_name": "R-17"}]))
    assert result.passed is True


def test_validate_leak_when_vlm_reports_match(tmp_path):
    img = _fake_img_path(tmp_path)
    payload = {
        "forbidden_entity_matches": [{
            "forbidden_character_id": "r17",
            "forbidden_name": "R-17",
            "detected_entity_type": "robot",
            "identity_similarity": 0.7,
            "confidence": 0.85,
            "indirect_representation": False,
            "evidence": ["red eye"],
        }],
        "any_human_present": False,
    }
    client = _FakeClient(payload)
    svc = BackgroundStoryEntityValidatorService(vlm_client=client)
    import asyncio
    result = asyncio.run(svc.validate(img, [{"character_id": "r17", "canonical_name": "R-17"}]))
    assert result.passed is False
    assert result.detected_entity_count >= 1


def test_validate_passes_when_entities_empty(tmp_path):
    img = _fake_img_path(tmp_path)
    svc = BackgroundStoryEntityValidatorService(vlm_client=_FakeClient({}))
    import asyncio
    result = asyncio.run(svc.validate(img, []))
    assert result.passed is True


def test_validate_fails_when_image_missing(tmp_path):
    svc = BackgroundStoryEntityValidatorService(vlm_client=_FakeClient({}))
    import asyncio
    result = asyncio.run(svc.validate(tmp_path / "missing.png", [{"character_id": "r17"}]))
    assert result.passed is False


class _ExplodingClient:
    def __init__(self):
        self.chat = MagicMock()
        self.chat.completions = MagicMock()

        async def _boom(*a, **kw):
            raise RuntimeError("network down")
        self.chat.completions.create = _boom


def test_validate_passes_when_vlm_unavailable(tmp_path):
    img = _fake_img_path(tmp_path)
    svc = BackgroundStoryEntityValidatorService(vlm_client=_ExplodingClient())
    import asyncio
    result = asyncio.run(svc.validate(img, [{"character_id": "r17"}]))
    # Soft-fail posture: passes with a reason
    assert result.passed is True
    assert any("VLM unavailable" in r for r in result.reasons)


@pytest.mark.asyncio
async def test_confirmed_entity_leak_quarantines_when_retry_generation_fails(
    tmp_path,
    monkeypatch,
):
    """A failed retry must never publish the image already known to leak cast."""
    from PIL import Image
    from app.services import image_generation_service as image_module

    monkeypatch.setattr(image_module, "BG_VALIDATE_ENABLED", False)
    monkeypatch.setattr(image_module, "BG_ENTITY_VALIDATE_ENABLED", True)
    monkeypatch.setattr(image_module, "BG_ENTITY_VALIDATE_MAX_RETRIES", 2)

    image_path = tmp_path / "leaked-background.png"
    Image.new("RGB", (1920, 1080), (120, 120, 120)).save(image_path)
    spec = BackgroundSceneSpec(
        scene_id="leak-retry",
        scene_name="璃月港空场",
        scene_selector="liyue_harbor_empty__day__01",
        scene_type="public_commerce",
        environment_description="璃月港石阶与木构建筑，空旷环境建立镜头。" * 5,
        lighting="日间暖光",
        atmosphere="宁静",
        camera_shot_type="establishing_wide",
        people_policy=PeoplePolicy(mode="empty_required", rationale="纯环境背景"),
        forbidden_characters=["钟离"],
        forbidden_entities=[{
            "character_id": "zhongli",
            "canonical_name": "钟离",
            "species": "human",
        }],
    )
    leak = StoryEntityLeak(
        detected_entity_type="human",
        forbidden_name="钟离",
        confidence=0.95,
        identity_similarity=0.9,
        evidence=("amber eyes and black-gold coat",),
    )
    verdict = BackgroundStoryEntityValidation(
        passed=False,
        detected_entity_count=1,
        matched_forbidden_entities=(leak,),
        reasons=("forbidden character detected",),
    )
    validator = MagicMock()
    validator.validate = AsyncMock(return_value=verdict)
    service = ImageGenerationService()

    with patch.object(
        service,
        "_generate_background_once_with_prompt",
        new=AsyncMock(side_effect=[image_path, None]),
    ), patch(
        "app.services.background_story_entity_validator_service."
        "BackgroundStoryEntityValidatorService",
        return_value=validator,
    ):
        result = await service.generate_background_with_validation(spec)

    assert result.status == "quarantined"
    assert result.retry_count == 1
    assert "retry generation returned no image" in (result.reason or "")
    assert result.validation_results[-1].passed is False
    assert result.validation_results[-1].is_hard_fail is True
