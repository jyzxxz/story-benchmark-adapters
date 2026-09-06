"""Celery workers for voice.slice and per-occurrence tts.render tasks."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.application.storage_service import LocalStorageBackend, get_local_storage_backend
from app.application.task_service import (
    TaskLease,
    claim_task,
    complete_task,
    fail_task,
    lease_from_task,
    require_task_lease,
    task_lease_matches,
)
from app.application.voice_line_service import (
    TTSRenderOutput,
    TTSRenderRequest,
    attach_cached_render,
    create_render_tasks,
    deterministic_voice_specs,
    estimate_tts_cost,
    mark_line_failed,
    materialize_voice_lines,
    persist_render_output,
    prepare_render_request,
)
from app.database import SessionLocal
from app.models_v2 import (
    ChapterRevision,
    ChapterScriptRevision,
    GenerationTask,
    StoryBibleRevision,
    VoiceLine,
)
from app.services.generation_report import sanitize_detail
from app.services import tts_service as tts_module
from app.services.tts_service import TTS_AUDIO_BASE_URL, TTS_ENGINE, tts_service
from app.workers.celery_app import celery_app
from app.workers.task_lease import TaskLeaseHeartbeat


class TTSAdapter(Protocol):
    async def render(self, request: TTSRenderRequest) -> TTSRenderOutput: ...


class LegacyTTSAdapter:
    """Compatibility adapter; provider invocation happens only in the worker."""

    async def render(self, request: TTSRenderRequest) -> TTSRenderOutput:
        result = await tts_service.synthesize(
            text=request.text,
            character_name=request.speaker_name,
            emotion=request.emotion,
            speed=request.speed,
            pitch=request.pitch,
            volume=request.volume,
        )
        if not result.get("success") or not result.get("audio_url"):
            raise RuntimeError("TTS provider did not return audio")

        audio_url = str(result["audio_url"])
        base_url = TTS_AUDIO_BASE_URL.rstrip("/") + "/"
        if not audio_url.startswith(base_url):
            raise RuntimeError("TTS provider returned an unsupported audio location")
        filename = audio_url[len(base_url):]
        if not filename or "/" in filename or "\\" in filename:
            raise RuntimeError("TTS provider returned an invalid audio filename")
        output_root = Path(tts_service.output_dir).resolve()
        audio_path = (output_root / filename).resolve()
        try:
            audio_path.relative_to(output_root)
        except ValueError as exc:
            raise RuntimeError("TTS audio path escaped the cache root") from exc
        if not audio_path.is_file():
            raise RuntimeError("TTS audio file is missing")

        suffix = audio_path.suffix.lower()
        media_type = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".ogg": "audio/ogg",
            ".flac": "audio/flac",
            ".m4a": "audio/mp4",
            ".webm": "audio/webm",
        }.get(suffix)
        if not media_type:
            raise RuntimeError("TTS audio format is unsupported")
        return TTSRenderOutput(
            audio_bytes=audio_path.read_bytes(),
            media_type=media_type,
            provider=TTS_ENGINE,
            model=request.model,
            actual_cost=Decimal("0") if result.get("cached") else estimate_tts_cost(request.text),
            cache_hit=bool(result.get("cached")),
        )


def _worker_id() -> str:
    return f"voice-worker:{uuid4()}"


def _record_render_failure(
    task_id: str,
    *,
    session_factory: sessionmaker,
    error_code: str,
    safe_detail: str,
    lease: TaskLease,
    line_id: str | None = None,
) -> None:
    session: Session = session_factory()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().first()
        if not task or not task_lease_matches(task, lease):
            session.rollback()
            return
        source_line_id = line_id or str((task.source_refs or {}).get("voice_line_id") or "")
        line = session.query(VoiceLine).filter(VoiceLine.id == source_line_id).first()
        if line:
            mark_line_failed(line)
        fail_task(
            session,
            task,
            error_code=error_code,
            safe_detail=safe_detail,
            retryable=False,
            lease=lease,
        )
        session.commit()
    finally:
        session.close()


def execute_voice_slice_task(
    task_id: str,
    *,
    worker_id: str | None = None,
    session_factory: sessionmaker = SessionLocal,
) -> None:
    worker_id = worker_id or _worker_id()
    session: Session = session_factory()
    try:
        task = claim_task(session, task_id, worker_id)
        if not task:
            session.rollback()
            return
        lease = lease_from_task(task)
        session.commit()
    finally:
        session.close()

    session = session_factory()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        require_task_lease(task, lease)
        chapter_id = str((task.source_refs or {}).get("chapter_revision_id") or "")
        chapter = session.query(ChapterRevision).filter(ChapterRevision.id == chapter_id).first()
        if task.kind != "voice.slice" or not chapter or chapter.project_id != task.project_id:
            raise ValueError("voice.slice source references are invalid")
        bible = (
            session.query(StoryBibleRevision)
            .filter(StoryBibleRevision.id == chapter.bible_revision_id)
            .first()
        )
        # 单事实源：优先取该章节最新 ready 的 Script IR（v3 segments）驱动语音切分，
        # 使语音行与 VN 图行同粒度同文本；无 script revision 时回退旧 LLM 切片链路。
        script_revision = (
            session.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.chapter_revision_id == chapter.id,
                ChapterScriptRevision.status == "ready",
            )
            .order_by(ChapterScriptRevision.revision_no.desc())
            .first()
        )
        script_ir = script_revision.script_json if script_revision else None
        lines = materialize_voice_lines(
            session,
            chapter,
            deterministic_voice_specs(chapter, bible, script_ir),
        )
        child_tasks = (
            create_render_tasks(session, parent=task, chapter=chapter, lines=lines)
            if bool((task.parameters or {}).get("render_audio", True))
            else []
        )
        complete_task(
            session,
            task,
            result_refs={
                "chapter_revision_id": chapter.id,
                "voice_line_ids": [line.id for line in lines],
                "tts_task_ids": [child.id for child in child_tasks],
            },
            actual_cost=Decimal("0"),
            lease=lease,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        failure = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().first()
        if failure and task_lease_matches(failure, lease):
            fail_task(
                session,
                failure,
                error_code="voice.slice_failed",
                safe_detail=sanitize_detail(f"{type(exc).__name__}: {exc}") or "voice.slice_failed",
                retryable=False,
                lease=lease,
            )
            session.commit()
    finally:
        session.close()


async def execute_tts_render_task(
    task_id: str,
    *,
    adapter: TTSAdapter | None = None,
    session_factory: sessionmaker = SessionLocal,
    backend: LocalStorageBackend | None = None,
    worker_id: str | None = None,
) -> None:
    backend = backend or get_local_storage_backend()
    worker_id = worker_id or _worker_id()

    session: Session = session_factory()
    try:
        task = claim_task(session, task_id, worker_id)
        if not task:
            session.rollback()
            return
        lease = lease_from_task(task)
        session.commit()
    finally:
        session.close()

    if not tts_module.TTS_ENABLED:
        _record_render_failure(
            task_id,
            session_factory=session_factory,
            error_code="tts.feature_disabled",
            safe_detail="TTS feature is disabled",
            lease=lease,
        )
        return

    adapter = adapter or LegacyTTSAdapter()

    # Prepare in a short transaction, then close it before the provider call.
    session = session_factory()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        require_task_lease(task, lease)
        _, line, request, cached = prepare_render_request(session, task)
        if cached:
            attach_cached_render(line, cached)
            complete_task(
                session,
                task,
                result_refs={"voice_line_id": line.id, "asset_version_id": cached.id, "cache_hit": True},
                actual_cost=Decimal("0"),
                lease=lease,
            )
            session.commit()
            return
        line.status = "rendering"
        session.commit()
    except Exception as exc:
        session.rollback()
        session.close()
        _record_render_failure(
            task_id,
            session_factory=session_factory,
            error_code="tts.prepare_failed",
            safe_detail=sanitize_detail(f"{type(exc).__name__}: {exc}") or "tts.prepare_failed",
            lease=lease,
        )
        return
    finally:
        if session.is_active:
            session.close()

    try:
        with TaskLeaseHeartbeat(
            task_id,
            lease,
            session_factory=session_factory,
        ):
            output = await adapter.render(request)
    except Exception as exc:
        # Surface the real provider error (sanitized of any credentials) so the
        # user can diagnose auth/quota/vcn issues instead of seeing a fixed
        # Chinese string that hides the root cause.
        provider_detail = sanitize_detail(f"{type(exc).__name__}: {exc}") or "tts.render_failed"
        _record_render_failure(
            task_id,
            session_factory=session_factory,
            error_code="tts.render_failed",
            safe_detail=provider_detail,
            lease=lease,
            line_id=request.voice_line_id,
        )
        return

    session = session_factory()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        require_task_lease(task, lease)
        chapter = session.query(ChapterRevision).filter(ChapterRevision.id == request.chapter_revision_id).one()
        line = session.query(VoiceLine).filter(VoiceLine.id == request.voice_line_id).with_for_update().one()
        version = persist_render_output(
            session,
            task=task,
            chapter=chapter,
            line=line,
            request=request,
            output=output,
            backend=backend,
        )
        complete_task(
            session,
            task,
            result_refs={"voice_line_id": line.id, "asset_version_id": version.id, "cache_hit": output.cache_hit},
            actual_cost=output.actual_cost,
            lease=lease,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        session.close()
        _record_render_failure(
            task_id,
            session_factory=session_factory,
            error_code="tts.persist_failed",
            safe_detail=sanitize_detail(f"{type(exc).__name__}: {exc}") or "tts.persist_failed",
            lease=lease,
            line_id=request.voice_line_id,
        )
    finally:
        if session.is_active:
            session.close()


@celery_app.task(name="if_line.voice_slice", bind=True)
def run_voice_slice(self, task_id: str) -> None:
    execute_voice_slice_task(task_id, worker_id=f"voice-worker:{self.request.id}")


@celery_app.task(name="if_line.tts_render", bind=True)
def run_tts_render(self, task_id: str) -> None:
    asyncio.run(
        execute_tts_render_task(
            task_id,
            worker_id=f"tts-worker:{self.request.id}",
        )
    )
