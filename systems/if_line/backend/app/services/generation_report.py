"""Reusable error-surfacing primitives for vngraph assembly pipelines.

The assembler fan-outs (backgrounds / portraits / keyframes / voice) used to
swallow every exception with a ``logger.warning`` and return an empty-looking
graph.  This module replaces that pattern with structured reports that travel
alongside the assembled graph in ``Meta.generation_report`` so callers can see
*why* an asset class came back empty.

Stdlib-only: no SQLAlchemy, no FastAPI, no project-specific domain types.  The
only domain knowledge is the four canonical stage names.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple, Union


StageName = Literal["backgrounds", "portraits", "keyframes", "voice_lines"]
StageStatus = Literal["ok", "partial", "failed", "skipped"]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class GenerationStageError(RuntimeError):
    """A stage ran but produced a known, structured failure.

    Raised by stage implementations when they can describe the failure mode
    (e.g. "missing outline", "no api key").  The assembler's ``run_stage``
    wrapper catches it and records the structured payload.
    """

    def __init__(
        self,
        *,
        stage: StageName,
        code: str,
        safe_message: str,
        scene: Optional[str] = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(f"[{stage}] {code}: {safe_message}")
        self.stage: StageName = stage
        self.code: str = code
        self.safe_message: str = safe_message
        self.scene: Optional[str] = scene
        self.retryable: bool = retryable


class MissingPrerequisiteError(GenerationStageError):
    """A required input (outline, StoryBible, chapter content) is missing.

    Default ``code="missing_prerequisite"``; callers pass a more specific
    ``code`` (e.g. ``"missing_outline"``, ``"missing_story_bible"``) and a
    user-facing ``safe_message``.
    """

    def __init__(
        self,
        *,
        stage: StageName,
        safe_message: str,
        code: str = "missing_prerequisite",
        scene: Optional[str] = None,
    ) -> None:
        super().__init__(
            stage=stage,
            code=code,
            safe_message=safe_message,
            scene=scene,
            retryable=False,
        )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass
class StageReport:
    """Per-stage structured result that becomes JSON in ``Meta.generation_report``."""

    stage: StageName
    status: StageStatus = "skipped"
    expected: int = 0
    generated: int = 0
    matched: int = 0
    fallback_used: Optional[str] = None
    errors: List[Dict[str, Any]] = field(default_factory=list)

    def add_error(
        self,
        *,
        code: str,
        message: str,
        scene: Optional[str] = None,
        retryable: bool = True,
    ) -> None:
        entry: Dict[str, Any] = {
            "code": code,
            "message": sanitize_detail(message),
            "retryable": bool(retryable),
        }
        if scene:
            entry["scene"] = scene
        self.errors.append(entry)

    def mark_running(self, expected: int) -> None:
        self.expected = int(expected or 0)
        if self.status == "skipped":
            self.status = "ok"

    def record_generated(self, count: int) -> None:
        self.generated += int(count or 0)
        self._recompute_status()

    def record_matched(self, count: int) -> None:
        self.matched += int(count or 0)
        self._recompute_status()

    def set_fallback(self, name: str) -> None:
        if not name:
            return
        self.fallback_used = str(name)
        if self.status == "ok":
            self.status = "partial"

    def _recompute_status(self) -> None:
        if self.status == "skipped":
            self.status = "ok"
        if self.errors:
            if self.generated > 0 or self.matched > 0:
                if self.status == "ok":
                    self.status = "partial"
            else:
                self.status = "failed"
        elif self.expected > 0 and (self.generated + self.matched) < self.expected:
            if self.status == "ok":
                self.status = "partial"

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "stage": self.stage,
            "status": self.status,
            "expected": self.expected,
            "generated": self.generated,
            "matched": self.matched,
            "errors": list(self.errors),
        }
        if self.fallback_used:
            payload["fallback_used"] = self.fallback_used
        return payload


@dataclass
class GenerationReport:
    """Aggregated report for the 4 canonical assembly stages."""

    backgrounds: StageReport = field(default_factory=lambda: StageReport("backgrounds"))
    portraits: StageReport = field(default_factory=lambda: StageReport("portraits"))
    keyframes: StageReport = field(default_factory=lambda: StageReport("keyframes"))
    voice_lines: StageReport = field(default_factory=lambda: StageReport("voice_lines"))

    @classmethod
    def all_skipped(cls) -> "GenerationReport":
        return cls()

    def reset(self) -> None:
        self.backgrounds = StageReport("backgrounds")
        self.portraits = StageReport("portraits")
        self.keyframes = StageReport("keyframes")
        self.voice_lines = StageReport("voice_lines")

    def stage(self, name: StageName) -> StageReport:
        return getattr(self, name)

    def any_failed_or_partial(self) -> bool:
        return any(
            self.stage(name).status in {"failed", "partial"}
            for name in ("backgrounds", "portraits", "keyframes", "voice_lines")
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "backgrounds": self.backgrounds.to_dict(),
            "portraits": self.portraits.to_dict(),
            "keyframes": self.keyframes.to_dict(),
            "voice_lines": self.voice_lines.to_dict(),
        }


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


_SECRET_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    # OpenAI / DashScope style keys: sk-... (lower threshold so test fixtures match)
    (re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "sk-***"),
    # Aliyun AccessKey pair
    (re.compile(r"LTAI[A-Za-z0-9]{12,}"), "LTAI***"),
    (re.compile(r"AccessKeyId[:=\s]*[A-Za-z0-9]{12,}", re.IGNORECASE), "AccessKeyId=***"),
    (re.compile(r"AccessKeySecret[:=\s]*[A-Za-z0-9+/=]{16,}", re.IGNORECASE), "AccessKeySecret=***"),
    # Authorization headers of any scheme
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE), "Bearer ***"),
    (re.compile(r"Authorization[:\s]+[A-Za-z0-9_\-\.]{8,}", re.IGNORECASE), "Authorization ***"),
    # Network identifiers
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "<ip>"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "<email>"),
]


def sanitize_detail(raw: Any, *, max_len: int = 500) -> str:
    """Strip common secret shapes (API keys, bearer tokens, IPs, emails).

    The output is safe to persist into ``GenerationTask.safe_detail`` or
    ``Meta.generation_report`` because it never carries raw credentials.
    """
    if raw is None:
        return ""
    text = str(raw)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# run_stage — the fail-loudly-but-continue wrapper
# ---------------------------------------------------------------------------


StageFn = Callable[[], Union[Any, Awaitable[Any]]]


async def _maybe_await(value: Union[Any, Awaitable[Any]]) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_stage(
    stage: StageName,
    expected: int,
    *,
    stage_fn: StageFn,
    report: GenerationReport,
    logger: Optional[logging.Logger] = None,
    project_id: Optional[int] = None,
    chapter_index: Optional[int] = None,
) -> Any:
    """Run a single generation stage and capture any failure into ``report``.

    Behavior:
    - Awaits ``stage_fn()`` (sync or async).
    - On :class:`GenerationStageError`, records its structured fields.
    - On any other ``Exception``, records a sanitized ``unexpected_exception`` entry.
    - Logs at ``error`` level when the resulting status is ``failed`` or ``partial``.
    - Returns the stage's result on success, ``None`` on failure.  Never raises.
    """
    log = logger or logging.getLogger("generation_report")
    stage_report = report.stage(stage)
    stage_report.mark_running(expected)

    try:
        result = await _maybe_await(stage_fn())
    except GenerationStageError as exc:
        stage_report.add_error(
            code=exc.code,
            message=exc.safe_message,
            scene=exc.scene,
            retryable=exc.retryable,
        )
        stage_report._recompute_status()
        log.error(
            "generation stage %s failed project=%s chapter=%s code=%s message=%s",
            stage, project_id, chapter_index, exc.code, exc.safe_message,
        )
        return None
    except Exception as exc:  # noqa: BLE001 — intentional: never re-raise
        message = f"{type(exc).__name__}: {exc}"
        stage_report.add_error(
            code="unexpected_exception",
            message=message,
            retryable=True,
        )
        stage_report._recompute_status()
        log.error(
            "generation stage %s unexpected exception project=%s chapter=%s err=%s",
            stage, project_id, chapter_index, sanitize_detail(message),
        )
        return None

    if stage_report.status in {"failed", "partial"}:
        log.error(
            "generation stage %s partial/failed project=%s chapter=%s status=%s errors=%d",
            stage, project_id, chapter_index, stage_report.status, len(stage_report.errors),
        )
    return result


def run_stage_sync(
    stage: StageName,
    expected: int,
    *,
    stage_fn: StageFn,
    report: GenerationReport,
    logger: Optional[logging.Logger] = None,
    project_id: Optional[int] = None,
    chapter_index: Optional[int] = None,
) -> Any:
    """Sync entry point for ``run_stage`` — runs the coroutine to completion.

    Use this only from contexts that are not already inside an event loop.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            raise RuntimeError(
                "run_stage_sync called inside a running event loop; "
                "use await run_stage(...) instead"
            )
    except RuntimeError:
        # No running loop — safe to use asyncio.run
        pass
    return asyncio.run(
        run_stage(
            stage,
            expected,
            stage_fn=stage_fn,
            report=report,
            logger=logger,
            project_id=project_id,
            chapter_index=chapter_index,
        )
    )
