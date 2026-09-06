"""Versioned chapter voice-line planning and TTS task orchestration."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from dataclasses import dataclass
from decimal import Decimal
from io import BytesIO
from typing import Any, Iterable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.storage_service import LocalStorageBackend, register_stored_file
from app.application.task_service import create_generation_task
from app.models import Asset, User
from app.models_v2 import (
    AssetVersion,
    ChapterRevision,
    GenerationTask,
    ProviderUsageRecord,
    StorageObject,
    StoryBibleRevision,
    VoiceLine,
)


VOICE_LINE_VERSION = "voice-line-v1"
VOICE_MAX_LINES = int(os.getenv("VOICE_MAX_LINES", "300"))
# 与 Script IR segment / TTS 闸门共用同一长度来源（minimax/aliyun 默认 600）。
from app.services.tts_service import TTS_MAX_TEXT_LENGTH as VOICE_MAX_TEXT_CHARS  # noqa: E402
TTS_CREDIT_PER_100_CHARS = Decimal(os.getenv("TTS_CREDIT_PER_100_CHARS", "0.25"))
TTS_DEFAULT_ENGINE = os.getenv("TTS_ENGINE", "aliyun").lower()
TTS_DEFAULT_MODEL = os.getenv("ALIYUN_TTS_COSYVOICE_MODEL", "cosyvoice-v3-flash")
TTS_DEFAULT_FORMAT = os.getenv("TTS_AUDIO_FORMAT", "mp3").lower()
TTS_DEFAULT_SAMPLE_RATE = int(os.getenv("ALIYUN_TTS_SAMPLE_RATE", "24000"))
TTS_DEFAULT_LANGUAGE = os.getenv("ALIYUN_TTS_LANGUAGE_TYPE", "Chinese")


@dataclass(frozen=True)
class VoiceLineSpec:
    kind: str
    text: str
    speaker_name: str | None = None
    speaker_character_id: str | None = None
    emotion: str | None = None
    degraded_mode: str | None = None


@dataclass(frozen=True)
class TTSRenderRequest:
    task_id: str
    voice_line_id: str
    project_id: int
    chapter_revision_id: str
    occurrence_id: str
    text: str
    speaker_name: str | None
    speaker_key: str
    emotion: str
    voice_profile_id: str | None
    voice_profile_version: int | None
    engine: str
    model: str
    speed: int
    pitch: int
    volume: int
    audio_format: str
    sample_rate: int
    language_type: str
    prompt_version: str
    cache_key: str


@dataclass(frozen=True)
class TTSRenderOutput:
    audio_bytes: bytes
    media_type: str
    provider: str
    model: str
    actual_cost: Decimal
    cache_hit: bool = False
    provider_request_id: str | None = None
    audio_seconds: Decimal = Decimal("0")
    latency_ms: int | None = None


def make_occurrence_id(
    chapter_revision_id: str,
    order_index: int,
    speaker_key: str,
    text: str,
) -> str:
    """Stable per-occurrence ID; order keeps duplicate text as distinct rows."""
    payload = f"{VOICE_LINE_VERSION}\0{chapter_revision_id}\0{order_index}\0{speaker_key}\0{text}"
    return f"occ_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def build_tts_cache_key(request: TTSRenderRequest | dict[str, Any]) -> str:
    if isinstance(request, TTSRenderRequest):
        payload = {
            "chapter_revision_id": request.chapter_revision_id,
            "occurrence_id": request.occurrence_id,
            "text": request.text,
            "speaker_key": request.speaker_key,
            "speaker_name": request.speaker_name,
            "emotion": request.emotion,
            "voice_profile_id": request.voice_profile_id,
            "voice_profile_version": request.voice_profile_version,
            "engine": request.engine,
            "model": request.model,
            "speed": request.speed,
            "pitch": request.pitch,
            "volume": request.volume,
            "audio_format": request.audio_format,
            "sample_rate": request.sample_rate,
            "language_type": request.language_type,
            "prompt_version": request.prompt_version,
        }
    else:
        payload = dict(request)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def estimate_tts_cost(text: str) -> Decimal:
    units = max(1, math.ceil(len(text) / 100))
    return (TTS_CREDIT_PER_100_CHARS * units).quantize(Decimal("0.000001"))


def _character_id(name: str, project_id: int, resolver: Any = None) -> str:
    """生成 character_id。

    优先用注入的 ``CanonicalCharacterResolver``；未注入时降级为
    ``md5(name|project_id)[:12]``。
    """
    if resolver is not None:
        try:
            return resolver.resolve(name).character_id
        except Exception:
            pass
    return hashlib.md5(f"{name}|{project_id}".encode("utf-8")).hexdigest()[:12]


def _split_long(text: str) -> Iterable[str]:
    remaining = (text or "").strip()
    while remaining:
        yield remaining[:VOICE_MAX_TEXT_CHARS]
        remaining = remaining[VOICE_MAX_TEXT_CHARS:]


def deterministic_voice_specs(
    chapter: ChapterRevision,
    bible: StoryBibleRevision | None,
    script_ir: dict[str, Any] | None = None,
) -> list[VoiceLineSpec]:
    """同步 wrapper：优先消费 Script IR segments（与 VN 图行同一份切分），
    否则调 chapter_voice_service 的 LLM 切句，再否则回退纯文本 fallback。
    """
    characters = ((bible.content_json or {}).get("characters") or []) if bible else []
    content = chapter.content or ""

    # 单事实源：script-ir-v3 的 paragraphs 直驱（文本/顺序与 VN 图行一致）。
    if script_ir and isinstance(script_ir.get("paragraphs"), list) and script_ir["paragraphs"]:
        specs = _specs_from_script_ir(script_ir)
        if specs:
            return specs
    valid_names = {str(c.get("name") or "").strip() for c in characters if isinstance(c, dict) and c.get("name")}

    # 尝试 LLM 切分（sync→async 桥接）
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    voice_lines = None
    slice_degraded_mode: str | None = None
    if loop is None:
        try:
            from app.services.chapter_voice_service import chapter_voice_service
            story_bible = {"characters": characters}
            voice_lines, slice_report = asyncio.run(chapter_voice_service._slice_chapter(
                project_id=chapter.project_id,
                chapter_index=getattr(chapter, "index", 0) or 0,
                chapter_content=content,
                story_bible=story_bible,
            ))
            slice_degraded_mode = slice_report.degraded_mode
        except Exception:
            voice_lines = None
            slice_degraded_mode = "slice_exception"

    if voice_lines:
        specs: list[VoiceLineSpec] = []
        for ln in voice_lines:
            for chunk in _split_long(ln.text or ""):
                is_dialogue = ln.is_dialogue() if hasattr(ln, "is_dialogue") else (ln.kind == "dialogue")
                speaker_name = ln.speaker_name if is_dialogue else None
                specs.append(
                    VoiceLineSpec(
                        kind="dialogue" if is_dialogue else "narration",
                        text=chunk,
                        speaker_name=speaker_name,
                        speaker_character_id=(
                            _character_id(speaker_name, chapter.project_id) if speaker_name else None
                        ),
                        emotion=(getattr(ln, "emotion", None) or "neutral") if is_dialogue else "calm",
                        degraded_mode=slice_degraded_mode,
                    )
                )
                if len(specs) >= VOICE_MAX_LINES:
                    return specs
        return specs

    # 回退：纯文本切分（按句号 + 中文引号识别对白），不依赖任何 LLM 或 splitter 模块
    return _text_only_specs(content, valid_names, chapter.project_id)


def _specs_from_script_ir(script_ir: dict[str, Any]) -> list[VoiceLineSpec]:
    """script-ir-v3 paragraphs → VoiceLineSpec（与 VN 图行同粒度同文本）。"""
    specs: list[VoiceLineSpec] = []
    for item in script_ir.get("paragraphs") or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        kind = str(item.get("kind") or "narration").lower()
        is_dialogue = kind in ("dialogue", "monologue")
        speaker_name = str(item.get("speaker_name") or "").strip() if is_dialogue else ""
        specs.append(
            VoiceLineSpec(
                kind="dialogue" if is_dialogue else "narration",
                text=text,
                speaker_name=speaker_name or None,
                speaker_character_id=(
                    str(item.get("speaker_character_id") or "").strip()
                    if speaker_name
                    else None
                ),
                emotion=str(item.get("emotion") or "").strip() or ("neutral" if is_dialogue else "calm"),
                degraded_mode=None,  # 单事实源直驱，不是降级
            )
        )
        if len(specs) >= VOICE_MAX_LINES:
            break
    return specs


def _text_only_specs(
    content: str,
    valid_names: set,
    project_id: int,
) -> list[VoiceLineSpec]:
    """纯文本 fallback：按句号切，识别中文引号内对白。
    比 LLM 切分粗糙，但保证链路不死 + 在无 LLM 环境下仍能产多行（用于测试/离线场景）。
    """
    specs: list[VoiceLineSpec] = []
    if not content:
        return specs

    import re as _re
    # 中文引号匹配：“...” / 「...」 / 『...』 / "..."`
    quote_re = _re.compile(r'[““][^”“”]{1,240}[””]|[「][^」]{1,240}[」]|[『][^』]{1,240}[』]|["][^"]{1,240}["]')

    pos = 0
    for m in quote_re.finditer(content):
        # 引号前的 narration 段
        if m.start() > pos:
            narration_chunk = content[pos:m.start()].strip()
            for piece in _split_by_sentence(narration_chunk):
                if piece.strip():
                    specs.append(VoiceLineSpec(
                        kind="narration",
                        text=piece.strip(),
                        speaker_name=None,
                        speaker_character_id=None,
                        emotion="calm",
                    ))
        # 引号内对白
        full = m.group(0)
        text = full[1:-1].strip()
        if text:
            # speaker 推断：引号前的最后一句末尾若有「X说/Y道/Z问」等，X/Y/Z 是 speaker
            prelude = content[max(0, m.start() - 40):m.start()]
            speaker = _infer_speaker(prelude, valid_names)
            specs.append(VoiceLineSpec(
                kind="dialogue",
                text=text,
                speaker_name=speaker,
                speaker_character_id=_character_id(speaker, project_id) if speaker else None,
                emotion="neutral",
            ))
        pos = m.end()
        if len(specs) >= VOICE_MAX_LINES:
            return specs

    # 末尾 narration 段
    if pos < len(content):
        for piece in _split_by_sentence(content[pos:].strip()):
            if piece.strip():
                specs.append(VoiceLineSpec(
                    kind="narration",
                    text=piece.strip(),
                    speaker_name=None,
                    speaker_character_id=None,
                    emotion="calm",
                ))
                if len(specs) >= VOICE_MAX_LINES:
                    break

    return specs


def _split_by_sentence(text: str) -> list[str]:
    """按中文句号/问号/感叹号切。"""
    if not text:
        return []
    import re as _re
    parts = _re.split(r'(?<=[。！？!?；;\n])', text)
    return [p for p in parts if p.strip()]


_SPEECH_VERBS = ("说", "道", "问", "答", "喊", "叫", "笑", "叹", "低吼", "嘟囔", "喃喃")


def _infer_speaker(prelude: str, valid_names: set) -> str | None:
    """从引号前的文本推断 speaker。优先匹配角色卡里的名字。"""
    if not valid_names or not prelude:
        return None
    # 在 prelude 最后 20 字符里找角色卡名字
    tail = prelude[-20:]
    for name in valid_names:
        if name and name in tail:
            # 确认 name 后面紧跟「说/道/问」等动词
            idx = tail.rfind(name)
            after = tail[idx + len(name):idx + len(name) + 3]
            if any(v in after for v in _SPEECH_VERBS):
                return name
            # 或者 name 紧贴引号（如 "林夜："）
            if idx + len(name) == len(tail):
                return name
    return None


async def _all_voice_lines_async(chapter: ChapterRevision, characters: list[Any]) -> list[Any]:
    """已废弃的 async helper —— 直接在 deterministic_voice_specs 内联了，保留仅为向后兼容。

    ``_slice_chapter`` 现在返回 ``(lines, VoiceSliceReport)``；这里仅返回 lines
    以保持旧调用方签名。需要 degraded_mode 的调用方请直接调 ``_slice_chapter``。
    """
    from app.services.chapter_voice_service import chapter_voice_service
    story_bible = {"characters": characters or []}
    lines, _slice_report = await chapter_voice_service._slice_chapter(
        project_id=chapter.project_id,
        chapter_index=getattr(chapter, "index", 0) or 0,
        chapter_content=chapter.content or "",
        story_bible=story_bible,
    )
    return lines


def materialize_voice_lines(
    db: Session,
    chapter: ChapterRevision,
    specs: list[VoiceLineSpec],
) -> list[VoiceLine]:
    if not specs:
        raise HTTPException(status_code=422, detail="章节没有可配音内容")
    existing = {
        line.order_index: line
        for line in db.query(VoiceLine)
        .filter(VoiceLine.chapter_revision_id == chapter.id)
        .order_by(VoiceLine.order_index)
        .all()
    }
    if any(index >= len(specs) for index in existing):
        raise HTTPException(status_code=409, detail="章节配音规划与已有 occurrence 冲突")

    result: list[VoiceLine] = []
    for order_index, spec in enumerate(specs):
        speaker_key = spec.speaker_character_id or "narrator"
        occurrence_id = make_occurrence_id(chapter.id, order_index, speaker_key, spec.text)
        line = existing.get(order_index)
        if line:
            if line.occurrence_id != occurrence_id:
                raise HTTPException(status_code=409, detail="章节配音规划与已有 occurrence 冲突")
        else:
            line = VoiceLine(
                chapter_revision_id=chapter.id,
                occurrence_id=occurrence_id,
                order_index=order_index,
                kind=spec.kind,
                text=spec.text,
                speaker_character_id=spec.speaker_character_id,
                speaker_name=spec.speaker_name,
                emotion=spec.emotion,
                status="planned",
            )
            db.add(line)
            db.flush()
        result.append(line)
    return result


def default_render_parameters(line: VoiceLine) -> dict[str, Any]:
    return {
        "engine": TTS_DEFAULT_ENGINE,
        "model": TTS_DEFAULT_MODEL,
        "speaker_key": line.speaker_character_id or "narrator",
        "emotion": line.emotion or "calm",
        "voice_profile_id": line.voice_profile_id,
        "voice_profile_version": line.voice_profile_version,
        "speed": 50,
        "pitch": 50,
        "volume": 50,
        "audio_format": TTS_DEFAULT_FORMAT,
        "sample_rate": TTS_DEFAULT_SAMPLE_RATE,
        "language_type": TTS_DEFAULT_LANGUAGE,
        "prompt_version": VOICE_LINE_VERSION,
    }


def _request_payload(chapter: ChapterRevision, line: VoiceLine) -> dict[str, Any]:
    parameters = default_render_parameters(line)
    cache_payload = {
        "chapter_revision_id": chapter.id,
        "occurrence_id": line.occurrence_id,
        "text": line.text,
        "speaker_name": line.speaker_name,
        **parameters,
    }
    parameters["cache_key"] = build_tts_cache_key(cache_payload)
    return parameters


def create_slice_task(
    db: Session,
    *,
    user_id: int,
    chapter: ChapterRevision,
    idempotency_key: str,
    render_audio: bool = True,
) -> tuple[GenerationTask, bool]:
    return create_generation_task(
        db,
        user_id=user_id,
        project_id=chapter.project_id,
        kind="voice.slice",
        idempotency_key=idempotency_key,
        source_refs={"chapter_revision_id": chapter.id},
        parameters={"slice_version": VOICE_LINE_VERSION, "render_audio": render_audio},
        estimated_cost=Decimal("0"),
        enqueue_event_type="task.voice.slice.queued",
    )


def create_render_tasks(
    db: Session,
    *,
    parent: GenerationTask,
    chapter: ChapterRevision,
    lines: list[VoiceLine],
) -> list[GenerationTask]:
    tasks: list[GenerationTask] = []
    root_id = parent.root_task_id or parent.id
    for line in lines:
        parameters = _request_payload(chapter, line)
        task, _ = create_generation_task(
            db,
            user_id=parent.user_id,
            project_id=chapter.project_id,
            kind="tts.render",
            idempotency_key=f"tts:{line.occurrence_id}:{parameters['cache_key'][:24]}",
            source_refs={
                "chapter_revision_id": chapter.id,
                "voice_line_id": line.id,
                "occurrence_id": line.occurrence_id,
            },
            parameters=parameters,
            estimated_cost=estimate_tts_cost(line.text),
            parent_task_id=parent.id,
            root_task_id=root_id,
            enqueue_event_type="task.tts.render.queued",
        )
        tasks.append(task)
    return tasks


def create_line_retry_task(
    db: Session,
    *,
    user_id: int,
    chapter: ChapterRevision,
    line: VoiceLine,
    idempotency_key: str,
) -> tuple[GenerationTask, bool]:
    if line.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败的配音行可以单独重试")
    task, created = create_generation_task(
        db,
        user_id=user_id,
        project_id=chapter.project_id,
        kind="tts.render",
        idempotency_key=idempotency_key,
        source_refs={
            "chapter_revision_id": chapter.id,
            "voice_line_id": line.id,
            "occurrence_id": line.occurrence_id,
        },
        parameters=_request_payload(chapter, line),
        estimated_cost=estimate_tts_cost(line.text),
        enqueue_event_type="task.tts.render.queued",
    )
    if created:
        line.status = "planned"
    return task, created


def build_voice_manifest(db: Session, chapter_revision_id: str) -> list[VoiceLine]:
    return (
        db.query(VoiceLine)
        .filter(VoiceLine.chapter_revision_id == chapter_revision_id)
        .order_by(VoiceLine.order_index.asc())
        .all()
    )


def prepare_render_request(
    db: Session,
    task: GenerationTask,
) -> tuple[ChapterRevision, VoiceLine, TTSRenderRequest, AssetVersion | None]:
    if task.kind != "tts.render":
        raise ValueError("task is not tts.render")
    chapter_id = str((task.source_refs or {}).get("chapter_revision_id") or "")
    line_id = str((task.source_refs or {}).get("voice_line_id") or "")
    chapter = db.query(ChapterRevision).filter(ChapterRevision.id == chapter_id).first()
    line = db.query(VoiceLine).filter(VoiceLine.id == line_id).first()
    if not chapter or not line or line.chapter_revision_id != chapter.id or chapter.project_id != task.project_id:
        raise ValueError("tts.render source references are invalid")
    parameters = dict(task.parameters or {})
    request = TTSRenderRequest(
        task_id=task.id,
        voice_line_id=line.id,
        project_id=chapter.project_id,
        chapter_revision_id=chapter.id,
        occurrence_id=line.occurrence_id,
        text=line.text,
        speaker_name=line.speaker_name,
        speaker_key=str(parameters.get("speaker_key") or "narrator"),
        emotion=str(parameters.get("emotion") or "calm"),
        voice_profile_id=parameters.get("voice_profile_id"),
        voice_profile_version=parameters.get("voice_profile_version"),
        engine=str(parameters.get("engine") or TTS_DEFAULT_ENGINE),
        model=str(parameters.get("model") or TTS_DEFAULT_MODEL),
        speed=int(parameters.get("speed", 50)),
        pitch=int(parameters.get("pitch", 50)),
        volume=int(parameters.get("volume", 50)),
        audio_format=str(parameters.get("audio_format") or TTS_DEFAULT_FORMAT),
        sample_rate=int(parameters.get("sample_rate", TTS_DEFAULT_SAMPLE_RATE)),
        language_type=str(parameters.get("language_type") or TTS_DEFAULT_LANGUAGE),
        prompt_version=str(parameters.get("prompt_version") or VOICE_LINE_VERSION),
        cache_key=str(parameters.get("cache_key") or ""),
    )
    expected = build_tts_cache_key(request)
    if request.cache_key != expected:
        raise ValueError("tts.render cache key mismatch")
    cached = (
        db.query(AssetVersion)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .join(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(
            AssetVersion.cache_key == request.cache_key,
            Asset.project_id == chapter.project_id,
            Asset.asset_type == "audio",
            StorageObject.status == "active",
            StorageObject.deleted_at.is_(None),
        )
        .first()
    )
    return chapter, line, request, cached


def attach_cached_render(line: VoiceLine, version: AssetVersion) -> None:
    line.audio_asset_version_id = version.id
    line.status = "ready"


def persist_render_output(
    db: Session,
    *,
    task: GenerationTask,
    chapter: ChapterRevision,
    line: VoiceLine,
    request: TTSRenderRequest,
    output: TTSRenderOutput,
    backend: LocalStorageBackend,
) -> AssetVersion:
    if not output.audio_bytes:
        raise ValueError("TTS adapter returned empty audio")
    stored = backend.save_stream(
        BytesIO(output.audio_bytes),
        namespace="tts/voice-lines",
        filename_or_suffix=f"audio.{request.audio_format}",
        media_type=output.media_type,
        max_bytes=50 * 1024 * 1024,
    )
    storage = register_stored_file(
        db,
        stored=stored,
        owner_id=task.user_id,
        project_id=chapter.project_id,
        visibility="release",
        backend=backend,
    )
    asset = Asset(
        project_id=chapter.project_id,
        chapter_index=chapter.chapter_index,
        asset_type="audio",
        target_name=line.speaker_name or "旁白",
        prompt=line.text,
        image_url=f"/api/media/{storage.id}",
        character_id=line.speaker_character_id,
        emotion=line.emotion,
        status="completed",
        processed=1,
        logical_key=f"audio:{chapter.id}:{line.occurrence_id}",
        taxonomy_json={
            "character_id": line.speaker_character_id,
            "emotion": line.emotion,
            "occurrence_id": line.occurrence_id,
        },
    )
    db.add(asset)
    db.flush()
    version = AssetVersion(
        asset_id=asset.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        source_hash=chapter.content_hash,
        generation_task_id=task.id,
        storage_object_id=storage.id,
        version_no=1,
        cache_key=request.cache_key,
        provider=output.provider,
        model=output.model,
        prompt=line.text,
        prompt_hash=content_hash(line.text),
        prompt_version=request.prompt_version,
        safety_status="approved",
        rights_metadata={"source": "generated_tts"},
        asset_spec_json={
            "asset_type": "audio",
            "logical_key": asset.logical_key,
            "occurrence_id": line.occurrence_id,
        },
        render_spec_json={
            "engine": request.engine,
            "model": request.model,
            "speed": request.speed,
            "pitch": request.pitch,
            "volume": request.volume,
            "audio_format": request.audio_format,
            "sample_rate": request.sample_rate,
            "language_type": request.language_type,
            "prompt_version": request.prompt_version,
        },
        render_spec_hash=request.cache_key,
    )
    db.add(version)
    db.flush()
    line.audio_asset_version_id = version.id
    line.status = "ready"
    db.add(
        ProviderUsageRecord(
            task_id=task.id,
            provider=output.provider,
            model=output.model,
            provider_request_id=output.provider_request_id,
            audio_seconds=output.audio_seconds,
            latency_ms=output.latency_ms,
            cost_amount=output.actual_cost,
            cache_hit=output.cache_hit,
        )
    )
    return version


def mark_line_failed(line: VoiceLine) -> None:
    line.status = "failed"
