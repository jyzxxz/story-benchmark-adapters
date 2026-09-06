"""Required scene-anchor extraction and validation for generated keyframes."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)

VERSION = "keyframe-scene-anchor-v1"


@dataclass(frozen=True)
class RequiredSceneElement:
    element_id: str
    label: str
    prompt_constraint: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class KeyframeSceneValidation:
    passed: bool
    required_elements: tuple[str, ...]
    missing_elements: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    skipped: bool = False
    raw_vlm_response: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_BAR_BEHIND_PATTERN = re.compile(
    r"(?:站|坐|停|位于)?在?吧台后|"
    r"behind\s+(?:the\s+)?(?:wooden\s+)?(?:coffee[- ]shop\s+)?(?:bar|bar\s+counter|counter)",
    flags=re.IGNORECASE,
)

_BAR_COUNTER = RequiredSceneElement(
    element_id="bar_counter",
    label="visible physical coffee-shop bar counter",
    prompt_constraint=(
        "A solid wooden coffee-shop bar counter is mandatory and clearly visible across the lower "
        "foreground or midground. The character described as behind the bar must be physically behind "
        "it, with the countertop edge and front panel visible and the counter occluding the character's "
        "lower body. Do not crop out, hide, omit, or replace the bar counter with empty floor or a blank wall"
    ),
)


def extract_required_scene_elements(prompt_data: dict[str, Any]) -> tuple[RequiredSceneElement, ...]:
    """Extract only high-confidence spatial anchors that must be visible."""
    text = " ".join(
        str(prompt_data.get(key) or "")
        for key in (
            "event_name",
            "scene_description",
            "final_prompt",
            "_keyframe_moment_summary",
            "_keyframe_visual_focus",
        )
    )
    elements: list[RequiredSceneElement] = []
    if _BAR_BEHIND_PATTERN.search(text):
        elements.append(_BAR_COUNTER)
    return tuple(elements)


def augment_prompt_with_scene_contract(
    prompt: str,
    elements: tuple[RequiredSceneElement, ...],
    *,
    missing_elements: tuple[str, ...] = (),
) -> str:
    if not elements:
        return prompt
    lines = ["MANDATORY SCENE GEOMETRY CONTRACT:"]
    if missing_elements:
        lines.append(
            "The previous image omitted required scene elements: "
            + ", ".join(missing_elements)
            + ". Regenerate the whole composition and make them unmistakably visible."
        )
    lines.extend(f"- [{element.element_id}] {element.prompt_constraint}." for element in elements)
    lines.append("A result missing any listed element is invalid even if the characters look correct.")
    return (prompt or "").rstrip() + "\n\n" + "\n".join(lines)


def _loose_json(content: str) -> dict[str, Any]:
    stripped = (content or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    try:
        return json.loads(stripped) or {}
    except ValueError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            return json.loads(match.group(0)) or {}
        except ValueError:
            return {}


def _interpret_result(
    data: dict[str, Any],
    elements: tuple[RequiredSceneElement, ...],
) -> KeyframeSceneValidation:
    reports = data.get("elements") or []
    by_id = {
        str(item.get("element_id") or ""): item
        for item in reports
        if isinstance(item, dict)
    }
    missing: list[str] = []
    reasons: list[str] = []
    for element in elements:
        report = by_id.get(element.element_id) or {}
        confidence = float(report.get("confidence") or 0.0)
        if not bool(report.get("present")) or confidence < 0.65:
            missing.append(element.element_id)
            evidence = str(report.get("evidence") or "not visibly detected")
            reasons.append(f"missing_scene_element:{element.element_id}:{evidence}")
    return KeyframeSceneValidation(
        passed=not missing,
        required_elements=tuple(element.element_id for element in elements),
        missing_elements=tuple(missing),
        reasons=tuple(reasons),
        raw_vlm_response=data,
    )


class KeyframeSceneValidatorService:
    def __init__(self, *, vlm_client: Any = None) -> None:
        self._vlm_client_override = vlm_client

    def _get_client(self) -> Any:
        if self._vlm_client_override is not None:
            return self._vlm_client_override
        from app.services.background_image_validator_service import BackgroundImageValidatorService
        from app.services.image_generation_service import BG_VISION_API_KEY, BG_VISION_BASE_URL

        return BackgroundImageValidatorService()._get_async_vision_client(
            BG_VISION_API_KEY,
            BG_VISION_BASE_URL,
        )

    @staticmethod
    def _resolve_image_path(image_path: str) -> Path:
        path = Path(str(image_path or ""))
        if path.exists():
            return path
        if str(image_path).startswith("/static/"):
            backend_root = Path(__file__).resolve().parents[2]
            return backend_root / str(image_path).removeprefix("/")
        return path

    async def validate(
        self,
        *,
        keyframe_image_path: str,
        required_elements: tuple[RequiredSceneElement, ...],
        timeout: float = 15.0,
    ) -> KeyframeSceneValidation:
        if not required_elements:
            return KeyframeSceneValidation(passed=True, required_elements=())
        image_path = self._resolve_image_path(keyframe_image_path)
        if not image_path.exists():
            return KeyframeSceneValidation(
                passed=False,
                required_elements=tuple(e.element_id for e in required_elements),
                missing_elements=tuple(e.element_id for e in required_elements),
                reasons=(f"image_not_found:{image_path}",),
            )

        try:
            image_bytes = image_path.read_bytes()
            mime = "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            data_url = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
            client = self._get_client()
            from app.services.image_generation_service import BG_VISION_MODEL

            required_json = json.dumps(
                [e.to_dict() for e in required_elements],
                ensure_ascii=False,
            )
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=BG_VISION_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "Inspect a generated visual-novel keyframe for required physical scene elements. "
                                "Judge only what is clearly visible; do not infer hidden objects. Return JSON: "
                                '{"elements":[{"element_id":"...","present":true,"confidence":0.0,'
                                '"evidence":"short visual evidence"}]}.'
                            ),
                        },
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": f"Required elements: {required_json}"},
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                ),
                timeout=timeout,
            )
            data = _loose_json(response.choices[0].message.content or "{}")
            return _interpret_result(data, required_elements)
        except Exception as exc:  # noqa: BLE001
            logger.warning("keyframe scene validation unavailable: %s", exc)
            return KeyframeSceneValidation(
                passed=True,
                required_elements=tuple(e.element_id for e in required_elements),
                reasons=(f"scene_validator_unavailable:{type(exc).__name__}",),
                skipped=True,
            )


__all__ = [
    "KeyframeSceneValidation",
    "KeyframeSceneValidatorService",
    "RequiredSceneElement",
    "VERSION",
    "augment_prompt_with_scene_contract",
    "extract_required_scene_elements",
]
