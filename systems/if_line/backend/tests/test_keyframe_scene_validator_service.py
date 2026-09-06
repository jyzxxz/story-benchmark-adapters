from types import SimpleNamespace

import pytest

from app.services.keyframe_scene_validator_service import (
    KeyframeSceneValidatorService,
    augment_prompt_with_scene_contract,
    extract_required_scene_elements,
)


def test_behind_bar_requires_visible_bar_counter():
    elements = extract_required_scene_elements({
        "event_name": "陈叔离开咖啡店，林默独自站在吧台后",
        "scene_description": "前景是林默站在吧台后的身影，中景是陈叔离去的背影",
    })

    assert [element.element_id for element in elements] == ["bar_counter"]
    augmented = augment_prompt_with_scene_contract("Lin Mo stands behind the bar.", elements)
    assert "MANDATORY SCENE GEOMETRY CONTRACT" in augmented
    assert "countertop edge and front panel visible" in augmented
    assert "occluding the character's lower body" in augmented


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    async def create(self, **_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._content))]
        )


@pytest.mark.asyncio
async def test_missing_bar_counter_fails_scene_validation(tmp_path):
    image_path = tmp_path / "keyframe.png"
    image_path.write_bytes(b"not-a-real-image-needed-by-fake-client")
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=_FakeCompletions(
                '{"elements":[{"element_id":"bar_counter","present":false,'
                '"confidence":0.98,"evidence":"characters stand on empty floor near a door"}]}'
            )
        )
    )
    elements = extract_required_scene_elements({"final_prompt": "Lin Mo stands behind bar"})

    result = await KeyframeSceneValidatorService(vlm_client=client).validate(
        keyframe_image_path=str(image_path),
        required_elements=elements,
    )

    assert result.passed is False
    assert result.missing_elements == ("bar_counter",)
    assert result.reasons[0].startswith("missing_scene_element:bar_counter")
