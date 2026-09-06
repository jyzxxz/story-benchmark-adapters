"""Post-generation visual validation for a portrait's apparent age stage."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.services.api_key_pool import api_key_available
from app.services.character_age_contract import (
    AGE_CONTRACT_VERSION,
    CharacterAgeContract,
    build_age_contract,
    normalize_age_group,
)


logger = logging.getLogger(__name__)

PORTRAIT_AGE_VALIDATION_VERSION = "portrait-age-v1"
PORTRAIT_AGE_VALIDATE_ENABLED = os.getenv(
    "PORTRAIT_AGE_VALIDATE_ENABLED", "true"
).lower() == "true"
PORTRAIT_AGE_VALIDATE_STRICT = os.getenv(
    "PORTRAIT_AGE_VALIDATE_STRICT", "true"
).lower() == "true"
PORTRAIT_AGE_VALIDATE_TIMEOUT = float(os.getenv("PORTRAIT_AGE_VALIDATE_TIMEOUT", "30"))
PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE = float(
    os.getenv("PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE", "0.65")
)
PORTRAIT_AGE_VALIDATE_MAX_RETRIES = int(
    os.getenv("PORTRAIT_AGE_VALIDATE_MAX_RETRIES", "1")
)


@dataclass(frozen=True)
class PortraitAgeValidation:
    passed: bool
    expected_age_group: str
    observed_age_group: str = "unknown"
    estimated_age_range: str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    validation_version: str = PORTRAIT_AGE_VALIDATION_VERSION
    age_contract_version: str = AGE_CONTRACT_VERSION
    skipped: bool = False
    method: str = "vlm"
    raw_vlm_response: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["evidence"] = list(self.evidence)
        out["violations"] = list(self.violations)
        return out

    def rewrite_feedback(self) -> list[str]:
        if self.passed:
            return []
        feedback = [
            f"The generated character looked {self.observed_age_group or 'age-ambiguous'}, "
            f"but must visibly read as {self.expected_age_group}."
        ]
        feedback.extend(self.violations)
        if self.evidence:
            feedback.append("Observed visual evidence: " + "; ".join(self.evidence))
        return feedback


def _loose_json(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:].lstrip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, dict) else {}
            except Exception:
                pass
    return {}


def _coerce_contract(value: CharacterAgeContract | dict[str, Any]) -> CharacterAgeContract:
    if isinstance(value, CharacterAgeContract):
        return value
    data = dict(value or {})
    return build_age_contract(
        data.get("age_group"),
        age_source=str(data.get("age_source") or "generation_params"),
        source_excerpt=str(data.get("source_excerpt") or ""),
    )


class PortraitAgeValidatorService:
    def __init__(self, *, vlm_client: Any = None) -> None:
        self._vlm_client_override = vlm_client

    @staticmethod
    def _resolve_image_path(image_path: str | Path) -> Path:
        path = Path(str(image_path or ""))
        if path.exists():
            return path
        if str(image_path).startswith("/static/"):
            backend_root = Path(__file__).resolve().parents[2]
            return backend_root / str(image_path).removeprefix("/")
        return path

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
    def _unavailable(expected: str, reason: str) -> PortraitAgeValidation:
        strict = PORTRAIT_AGE_VALIDATE_STRICT
        return PortraitAgeValidation(
            passed=not strict,
            expected_age_group=expected,
            skipped=True,
            method="unavailable",
            violations=(reason,),
        )

    async def validate(
        self,
        *,
        image_path: str | Path,
        age_contract: CharacterAgeContract | dict[str, Any],
        character_name: str = "",
        timeout: Optional[float] = None,
    ) -> PortraitAgeValidation:
        contract = _coerce_contract(age_contract)
        expected = contract.age_group
        if not PORTRAIT_AGE_VALIDATE_ENABLED:
            return PortraitAgeValidation(
                passed=True,
                expected_age_group=expected,
                skipped=True,
                method="disabled",
                violations=("portrait age validation disabled",),
            )

        resolved = self._resolve_image_path(image_path)
        if not resolved.exists():
            return self._unavailable(expected, f"image_not_found:{resolved}")

        from app.services.image_generation_service import (
            BG_VISION_API_KEY,
            BG_VISION_MODEL,
        )

        if not api_key_available(
            BG_VISION_API_KEY,
            pool_env=("BG_VISION_API_KEYS", "AI_IMAGE_API_KEYS", "OPENAI_API_KEYS"),
            allow_byok=False,
        ):
            return self._unavailable(expected, "vision_api_key_unavailable")

        suffix = resolved.suffix.lower()
        mime = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".webp": "image/webp",
        }.get(suffix, "image/png")
        data_url = f"data:{mime};base64,{base64.b64encode(resolved.read_bytes()).decode('ascii')}"
        expected_payload = json.dumps(contract.to_dict(), ensure_ascii=False)
        client = self._get_client()

        try:
            response = await asyncio.wait_for(
                client.chat.completions.create(
                    model=BG_VISION_MODEL,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You validate the apparent age of one illustrated character. Judge only visible "
                                "facial proportions, jaw maturity, body proportions, age lines, and other age cues. "
                                "Do not infer age from the character's name, franchise knowledge, clothing, or prompt. "
                                "Return strict JSON with observed_age_group using exactly one of: toddler, child, teen, "
                                "young_adult, adult, middle_aged, senior, unknown."
                            ),
                        },
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        f"Character label (not age evidence): {character_name or 'unknown'}\n"
                                        f"Expected contract: {expected_payload}\n"
                                        "Inspect the image and output JSON: "
                                        '{"observed_age_group":"...","estimated_age_range":"...",'
                                        '"confidence":0.0,"evidence":["..."],"violations":["..."]}. '
                                        "Confidence is confidence in the observed apparent age, not prompt compliance."
                                    ),
                                },
                                {"type": "image_url", "image_url": {"url": data_url}},
                            ],
                        },
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.0,
                ),
                timeout=timeout or PORTRAIT_AGE_VALIDATE_TIMEOUT,
            )
            data = _loose_json(response.choices[0].message.content or "{}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("portrait age validation unavailable: %s", exc)
            return self._unavailable(expected, f"vision_call_failed:{type(exc).__name__}")

        observed_raw = str(data.get("observed_age_group") or "unknown").strip().lower()
        observed = (
            normalize_age_group(observed_raw)
            if observed_raw not in {"", "unknown", "unclear", "ambiguous"}
            else "unknown"
        )
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = tuple(str(item).strip()[:180] for item in data.get("evidence") or [] if str(item).strip())[:8]
        reported_violations = tuple(
            str(item).strip()[:180] for item in data.get("violations") or [] if str(item).strip()
        )[:8]
        violations = list(reported_violations)
        if observed != expected:
            violations.insert(0, f"observed_age_group={observed}; expected={expected}")
        if confidence < PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE:
            violations.append(
                f"age confidence {confidence:.2f} below {PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE:.2f}"
            )
        passed = observed == expected and confidence >= PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE
        return PortraitAgeValidation(
            passed=passed,
            expected_age_group=expected,
            observed_age_group=observed,
            estimated_age_range=str(data.get("estimated_age_range") or "")[:80],
            confidence=confidence,
            evidence=evidence,
            violations=tuple(dict.fromkeys(violations)),
            raw_vlm_response=data,
        )


portrait_age_validator_service = PortraitAgeValidatorService()


__all__ = [
    "PORTRAIT_AGE_VALIDATE_ENABLED",
    "PORTRAIT_AGE_VALIDATE_MAX_RETRIES",
    "PORTRAIT_AGE_VALIDATE_MIN_CONFIDENCE",
    "PORTRAIT_AGE_VALIDATE_STRICT",
    "PORTRAIT_AGE_VALIDATION_VERSION",
    "PortraitAgeValidation",
    "PortraitAgeValidatorService",
    "portrait_age_validator_service",
]
