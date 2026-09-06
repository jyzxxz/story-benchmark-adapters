"""Durable Chapter Script IR generation, resource planning and queries."""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.asset_service import (
    create_asset_binding,
    create_asset_plan,
    request_asset_plan_render,
)
from app.application.hashing import content_hash, content_hash_js
from app.application.story_bible_service import (
    append_auto_registered_characters,
    collect_unregistered_speaker_names,
)
from app.application.revision_head_service import (
    RevisionHeadValue,
    head_value,
    raise_head_version_conflict,
)
from app.application.task_service import (
    TaskLease,
    TaskLeaseLostError,
    append_task_event,
    complete_task,
    create_generation_task,
    require_task_lease,
    update_parent_aggregate,
)
from app.core.errors import AppError
from app.database import SessionLocal
from app.integrations.llm.chapter_script_adapter import (
    ChapterScriptAnnotationRequest,
    LegacyChapterScriptLLMAdapter,
)
from app.models import StoryBible
from app.models_v2 import (
    AssetVersion,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    GenerationTask,
    GenerationTaskDependency,
    OutlineChapter,
    OutlineRevision,
    ProviderUsageRecord,
    ScriptResourceSlot,
    StoryBibleRevision,
    StoryPathChapter,
    utcnow,
)
from app.services.asset_prompt_builder_service import build_shared_block
from app.services.chapter_script_ir import (
    SCRIPT_GENERATOR_VERSION,
    SCRIPT_IR_SCHEMA_VERSION,
    ScriptIRValidationError,
    build_script_ir_from_segments,
    normalize_characters,
    resource_slot_specs,
    validate_script_ir,
)
from app.services.project_visual_bible_service import project_visual_bible_service
from app.services.image_generation_service import (
    AI_IMAGE_MODEL,
    AI_IMAGE_NEGATIVE_PROMPT,
    LANDSCAPE_POSTPROCESS_CONTRACT_VERSION,
    PORTRAIT_GENERATION_CONTRACT_VERSION,
)


CHAPTER_SCRIPT_TASK_KIND = "chapter_script.generate"

logger = logging.getLogger("chapter_script")
CHAPTER_SCRIPT_EVENT = "task.chapter_script.generate.queued"
CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION = "chapter-script-source-v1"
_SELECTABLE_SOURCE_STATUSES = frozenset({"ready", "complete"})
_SELECTABLE_SCRIPT_STATUSES = frozenset({"ready", "complete"})


class ProviderCallAlreadyAttempted(RuntimeError):
    pass


class ChapterScriptSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChapterScriptSources:
    chapter: ChapterRevision
    bible: StoryBibleRevision
    outline: OutlineRevision
    outline_chapter: OutlineChapter
    path_chapter: StoryPathChapter


@dataclass(frozen=True)
class ChapterScriptGenerationContext:
    project_id: int
    chapter_revision_id: str
    display_index: int
    parent_revision_id: str | None
    generation_parameters: dict[str, Any]
    source_manifest: dict[str, Any]
    source_hash: str
    sources: ChapterScriptSources
    request: ChapterScriptAnnotationRequest


@dataclass(frozen=True)
class ChapterScriptHeadActivation:
    revision: ChapterScriptRevision
    head: RevisionHeadValue


def _outline_payload(row: OutlineChapter) -> dict[str, Any]:
    return {
        "story_path_chapter_id": row.story_path_chapter_id,
        "display_index": row.display_index,
        # Kept as provider-facing presentation metadata until the old adapter is retired.
        "chapter_index": row.display_index,
        "title": row.title,
        "summary": row.summary,
        "conflict": row.conflict,
        "characters": list(row.characters or []),
        "scene": row.scene,
        "emotion": row.emotion,
        "visual_keywords": list(row.visual_keywords or []),
    }


def _outline_integrity_payload(row: OutlineChapter) -> dict[str, Any]:
    return {
        "story_path_chapter_id": row.story_path_chapter_id,
        "display_index": row.display_index,
        "title": row.title,
        "summary": row.summary,
        "conflict": row.conflict,
        "characters": list(row.characters or []),
        "scene": row.scene,
        "emotion": row.emotion,
        "visual_keywords": list(row.visual_keywords or []),
    }


def _normalize_script_parameters(parameters: dict[str, Any] | None) -> dict[str, Any]:
    if parameters is None:
        return {}
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=422, detail="parameters must be an object")
    normalized = deepcopy(parameters)
    instructions = normalized.get("instructions")
    if instructions is not None and (
        not isinstance(instructions, str)
        or len(instructions) > 12_000
        or "\x00" in instructions
    ):
        raise HTTPException(status_code=422, detail="instructions must be valid text")
    if isinstance(instructions, str):
        normalized["instructions"] = instructions.strip()
    return normalized


def _load_sources(
    db: Session,
    *,
    chapter_revision_id: str,
    expected_project_id: int | None = None,
) -> ChapterScriptSources:
    chapter = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id == chapter_revision_id)
        .one_or_none()
    )
    if not chapter or (
        expected_project_id is not None and chapter.project_id != expected_project_id
    ):
        raise HTTPException(status_code=404, detail="Chapter revision 不存在")
    if chapter.status not in _SELECTABLE_SOURCE_STATUSES or not chapter.content:
        raise HTTPException(status_code=409, detail="Chapter revision 尚未完成")
    if content_hash(chapter.content) != chapter.content_hash:
        raise HTTPException(status_code=409, detail="Chapter revision 正文哈希不匹配")
    bible = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == chapter.bible_revision_id,
            StoryBibleRevision.project_id == chapter.project_id,
        )
        .one_or_none()
    )
    outline = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == chapter.outline_revision_id,
            OutlineRevision.project_id == chapter.project_id,
        )
        .one_or_none()
    )
    if (
        not bible
        or not outline
        or outline.bible_revision_id != bible.id
        or content_hash(bible.content_json or {}) != bible.content_hash
    ):
        raise HTTPException(status_code=409, detail="Script IR 的 Bible/Outline 来源不完整")

    manifest = chapter.context_manifest if isinstance(chapter.context_manifest, dict) else {}
    path_chapter_id = manifest.get("path_chapter_id")
    if not isinstance(path_chapter_id, str) or not path_chapter_id:
        raise HTTPException(status_code=409, detail="Chapter revision 缺少 StoryPath 身份")
    path_chapter = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.status == "active",
        )
        .one_or_none()
    )
    if (
        not path_chapter
        or path_chapter.chapter_slot_id != chapter.chapter_slot_id
        or manifest.get("project_id") != chapter.project_id
        or manifest.get("story_path_id") != path_chapter.story_path_id
        or manifest.get("bible_revision_id") != bible.id
        or manifest.get("outline_revision_id") != outline.id
        or chapter.context_hash != content_hash(manifest)
        or outline.story_path_id != path_chapter.story_path_id
        or chapter.created_for_story_path_id != path_chapter.story_path_id
    ):
        raise HTTPException(status_code=409, detail="Chapter revision 的路径上下文不完整")
    outline_chapter = (
        db.query(OutlineChapter)
        .filter(
            OutlineChapter.outline_revision_id == outline.id,
            OutlineChapter.story_path_chapter_id == path_chapter.id,
        )
        .one_or_none()
    )
    outline_payload = (
        _outline_integrity_payload(outline_chapter)
        if outline_chapter is not None
        else None
    )
    generated_outline_payload = (
        {**outline_payload, "story_path_chapter_id": None}
        if outline_payload is not None
        else None
    )
    if (
        not outline_chapter
        or outline_chapter.content_hash
        not in {
            content_hash(outline_payload),
            content_hash(generated_outline_payload),
        }
    ):
        raise HTTPException(status_code=409, detail="Script IR 的精确 OutlineChapter 来源无效")

    return ChapterScriptSources(
        chapter=chapter,
        bible=bible,
        outline=outline,
        outline_chapter=outline_chapter,
        path_chapter=path_chapter,
    )


def get_or_create_chapter_script_head(
    db: Session,
    chapter_revision_id: str,
    *,
    for_update: bool = False,
) -> ChapterScriptHead:
    chapter_query = db.query(ChapterRevision).filter(
        ChapterRevision.id == chapter_revision_id
    )
    if for_update:
        chapter_query = chapter_query.with_for_update()
    chapter = chapter_query.one_or_none()
    if not chapter:
        raise AppError(
            code="chapter_revision.not_found",
            message="ChapterRevision does not exist",
            status_code=404,
        )

    head_query = db.query(ChapterScriptHead).filter(
        ChapterScriptHead.chapter_revision_id == chapter.id
    )
    if for_update:
        head_query = head_query.with_for_update()
    head = head_query.one_or_none()
    if not head and not for_update:
        chapter = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == chapter.id)
            .with_for_update()
            .one()
        )
        head = (
            db.query(ChapterScriptHead)
            .filter(ChapterScriptHead.chapter_revision_id == chapter.id)
            .with_for_update()
            .one_or_none()
        )
    if not head:
        head = ChapterScriptHead(
            project_id=chapter.project_id,
            chapter_index=chapter.chapter_index,
            chapter_revision_id=chapter.id,
            current_revision_id=None,
            lock_version=1,
        )
        db.add(head)
        db.flush()
    if head.current_revision_id:
        selected = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == head.current_revision_id,
                ChapterScriptRevision.chapter_revision_id == chapter.id,
            )
            .one_or_none()
        )
        if not selected:
            raise AppError(
                code="script_head.invalid",
                message="Script Head points outside its ChapterRevision",
                status_code=409,
            )
    return head


def get_chapter_script_head(
    db: Session,
    *,
    chapter_revision_id: str,
) -> RevisionHeadValue:
    return head_value(get_or_create_chapter_script_head(db, chapter_revision_id))


def _refresh_script_head(db: Session, head: ChapterScriptHead) -> ChapterScriptHead:
    db.expire(head)
    db.refresh(head)
    return head


def activate_chapter_script_head(
    db: Session,
    *,
    chapter_revision_id: str,
    revision_id: str,
    expected_lock_version: int,
) -> ChapterScriptHeadActivation:
    if expected_lock_version < 1:
        raise AppError(
            code="precondition.invalid",
            message="Head lock version must be positive",
            status_code=400,
        )
    head = get_or_create_chapter_script_head(db, chapter_revision_id)
    revision = (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.id == revision_id,
            ChapterScriptRevision.chapter_revision_id == chapter_revision_id,
        )
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="script_revision.not_found",
            message="ScriptRevision does not belong to this ChapterRevision",
            status_code=404,
        )
    if (
        revision.status not in _SELECTABLE_SCRIPT_STATUSES
        or content_hash(revision.script_json) != revision.script_hash
    ):
        raise AppError(
            code="script_revision.not_ready",
            message="ScriptRevision is not ready for review selection",
            status_code=409,
        )

    same_revision = head.current_revision_id == revision.id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": ChapterScriptHead.lock_version,
            "updated_at": ChapterScriptHead.updated_at,
        }
    else:
        values = {
            "current_revision_id": revision.id,
            "lock_version": ChapterScriptHead.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(ChapterScriptHead)
        .where(
            ChapterScriptHead.id == head.id,
            ChapterScriptHead.lock_version == expected_lock_version,
            *((ChapterScriptHead.current_revision_id == revision.id,) if same_revision else ()),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise_head_version_conflict(_refresh_script_head(db, head))
    return ChapterScriptHeadActivation(
        revision=revision,
        head=head_value(_refresh_script_head(db, head)),
    )


def list_chapter_script_revisions(
    db: Session,
    *,
    chapter_revision_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[ChapterScriptRevision]:
    chapter = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id == chapter_revision_id)
        .one_or_none()
    )
    if not chapter:
        raise AppError(
            code="chapter_revision.not_found",
            message="ChapterRevision does not exist",
            status_code=404,
        )
    return (
        db.query(ChapterScriptRevision)
        .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
        .order_by(ChapterScriptRevision.revision_no.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def _chapter_script_source_envelope(
    source_refs: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    source = source_refs.get("chapter_script_source")
    digest = source_refs.get("chapter_script_source_hash")
    if not isinstance(source, dict) or not isinstance(digest, str):
        raise ChapterScriptSourceError("Chapter Script frozen source is missing")
    if content_hash(source) != digest:
        raise ChapterScriptSourceError("Chapter Script source hash does not match")
    if source.get("schema_version") != CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION:
        raise ChapterScriptSourceError("Chapter Script source schema is unsupported")
    return source, digest


def _build_chapter_script_source(
    *,
    sources: ChapterScriptSources,
    parent_revision_id: str | None,
    generation_parameters: dict[str, Any],
) -> dict[str, Any]:
    chapter = sources.chapter
    bible = sources.bible
    outline = sources.outline
    outline_chapter = sources.outline_chapter
    display_index = sources.outline_chapter.display_index
    return {
        "schema_version": CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION,
        "request_identity": {
            "chapter_revision_id": chapter.id,
            "generation_parameters": deepcopy(generation_parameters),
        },
        "project_id": chapter.project_id,
        "chapter_revision_id": chapter.id,
        "path_chapter_id": sources.path_chapter.id,
        "display_index": display_index,
        "parent_revision_id": parent_revision_id,
        "chapter_content_hash": chapter.content_hash,
        "chapter_context_hash": chapter.context_hash,
        "bible_revision_id": bible.id,
        "bible_content_hash": bible.content_hash,
        "outline_revision_id": outline.id,
        "outline_content_hash": outline.content_hash,
        "outline_chapter_id": outline_chapter.id,
        "outline_chapter_hash": outline_chapter.content_hash,
        "script_schema_version": SCRIPT_IR_SCHEMA_VERSION,
        "generator_version": SCRIPT_GENERATOR_VERSION,
        "provider_input": {
            "bible": deepcopy(bible.content_json or {}),
            "outline_chapter": _outline_payload(outline_chapter),
        },
    }


def _chapter_script_request(
    sources: ChapterScriptSources,
    generation_parameters: dict[str, Any],
) -> ChapterScriptAnnotationRequest:
    chapter = sources.chapter
    bible = sources.bible
    # v3：LLM 直接拿全文做切分+标注；不再预建换行段落列表。
    return ChapterScriptAnnotationRequest(
        project_id=chapter.project_id,
        chapter_index=sources.outline_chapter.display_index,
        bible=deepcopy(bible.content_json or {}),
        outline_chapter=_outline_payload(sources.outline_chapter),
        chapter_content=chapter.content or "",
        characters=normalize_characters((bible.content_json or {}).get("characters") or []),
        instructions=str(generation_parameters.get("instructions") or ""),
    )


def _context_from_chapter_script_source(
    db: Session,
    *,
    source: dict[str, Any],
    source_hash: str,
) -> ChapterScriptGenerationContext:
    try:
        chapter_revision_id = str(source["chapter_revision_id"])
        project_id = int(source["project_id"])
        display_index = int(source["display_index"])
        request_identity = source["request_identity"]
        generation_parameters = request_identity["generation_parameters"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ChapterScriptSourceError("Chapter Script source is incomplete") from exc
    if (
        not chapter_revision_id
        or display_index < 1
        or not isinstance(request_identity, dict)
        or not isinstance(generation_parameters, dict)
        or request_identity.get("chapter_revision_id") != chapter_revision_id
    ):
        raise ChapterScriptSourceError("Chapter Script request identity is invalid")

    try:
        sources = _load_sources(
            db,
            chapter_revision_id=chapter_revision_id,
            expected_project_id=project_id,
        )
    except HTTPException as exc:
        raise ChapterScriptSourceError(str(exc.detail)) from exc
    chapter = sources.chapter
    bible = sources.bible
    outline = sources.outline
    outline_chapter = sources.outline_chapter
    expected = {
        "project_id": chapter.project_id,
        "chapter_revision_id": chapter.id,
        "path_chapter_id": sources.path_chapter.id,
        "display_index": sources.outline_chapter.display_index,
        "chapter_content_hash": chapter.content_hash,
        "chapter_context_hash": chapter.context_hash,
        "bible_revision_id": bible.id,
        "bible_content_hash": bible.content_hash,
        "outline_revision_id": outline.id,
        "outline_content_hash": outline.content_hash,
        "outline_chapter_id": outline_chapter.id,
        "outline_chapter_hash": outline_chapter.content_hash,
        "script_schema_version": SCRIPT_IR_SCHEMA_VERSION,
        "generator_version": SCRIPT_GENERATOR_VERSION,
        "provider_input": {
            "bible": deepcopy(bible.content_json or {}),
            "outline_chapter": _outline_payload(outline_chapter),
        },
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise ChapterScriptSourceError("Chapter Script frozen source changed")

    parent_revision_id = source.get("parent_revision_id")
    if parent_revision_id is not None:
        parent = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == parent_revision_id,
                ChapterScriptRevision.chapter_revision_id == chapter.id,
            )
            .one_or_none()
        )
        if not parent:
            raise ChapterScriptSourceError("Chapter Script parent revision is invalid")
    return ChapterScriptGenerationContext(
        project_id=project_id,
        chapter_revision_id=chapter.id,
        display_index=display_index,
        parent_revision_id=str(parent_revision_id) if parent_revision_id else None,
        generation_parameters=deepcopy(generation_parameters),
        source_manifest=deepcopy(source),
        source_hash=source_hash,
        sources=sources,
        request=_chapter_script_request(sources, generation_parameters),
    )


def resolve_chapter_script_generation_source(
    db: Session,
    *,
    task: GenerationTask,
) -> ChapterScriptGenerationContext:
    source, digest = _chapter_script_source_envelope(task.source_refs or {})
    context = _context_from_chapter_script_source(db, source=source, source_hash=digest)
    if (
        task.kind != CHAPTER_SCRIPT_TASK_KIND
        or task.project_id != context.project_id
        or (task.parameters or {}) != context.generation_parameters
    ):
        raise ChapterScriptSourceError("Chapter Script task does not match frozen source")
    return context


def create_chapter_script_generation_task(
    db: Session,
    *,
    user_id: int,
    chapter_revision_id: str,
    idempotency_key: str,
    parameters: dict[str, Any] | None = None,
) -> tuple[GenerationTask, bool]:
    generation_parameters = _normalize_script_parameters(parameters)
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        try:
            source, _digest = _chapter_script_source_envelope(existing.source_refs or {})
        except ChapterScriptSourceError as exc:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key 已用于不同请求",
            ) from exc
        expected_identity = {
            "chapter_revision_id": chapter_revision_id,
            "generation_parameters": generation_parameters,
        }
        if (
            existing.kind != CHAPTER_SCRIPT_TASK_KIND
            or source.get("request_identity") != expected_identity
        ):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        return existing, False

    sources = _load_sources(db, chapter_revision_id=chapter_revision_id)
    head = get_or_create_chapter_script_head(db, chapter_revision_id, for_update=True)
    source_manifest = _build_chapter_script_source(
        sources=sources,
        parent_revision_id=head.current_revision_id,
        generation_parameters=generation_parameters,
    )
    source_hash = content_hash(source_manifest)

    task, created = create_generation_task(
        db,
        user_id=user_id,
        project_id=sources.chapter.project_id,
        kind=CHAPTER_SCRIPT_TASK_KIND,
        idempotency_key=idempotency_key,
        source_refs={
            "chapter_script_source": source_manifest,
            "chapter_script_source_hash": source_hash,
            "source_schema_version": CHAPTER_SCRIPT_SOURCE_SCHEMA_VERSION,
            "chapter_revision_id": sources.chapter.id,
            "source_hash": source_hash,
        },
        parameters=generation_parameters,
        estimated_cost=Decimal("3"),
        enqueue_event_type=CHAPTER_SCRIPT_EVENT,
    )
    # A provider response can be billed even when its worker dies before
    # receiving it. This task is therefore never replayed after one attempt.
    task.max_attempts = 1
    return task, created


def _logical_key(revision: ChapterScriptRevision, spec: dict[str, Any]) -> str:
    role = str(spec["role"])
    if role == "portrait":
        return ":".join(
            (
                "portrait",
                str(spec.get("character_id") or "unknown"),
                str(spec.get("emotion") or "neutral"),
                "default",
                "standing",
            )
        )
    if role == "background":
        semantic_scene = content_hash(str(spec.get("target_name") or spec.get("scene_id") or "scene"))[:20]
        return f"background:{semantic_scene}:default:default:default"
    event_anchor = content_hash(str(spec.get("prompt") or spec.get("paragraph_id") or "event"))[:20]
    return f"keyframe:chapter-revision-{revision.chapter_revision_id}:{event_anchor}"


def _project_visual_style_fields(db: Session, project_id: int) -> dict[str, Any]:
    """Resolve the project's locked visual bible into frozen render-spec fields.

    Returns {} when the project has no story bible (behavior unchanged).

    StoryBibleRevision rows are immutable (no lock write-back). Stability
    instead comes from the style classifier's determinism (temperature 0.2
    plus a process-level payload-sha cache) and from the fact that the
    resolved fields are frozen into each render_spec's extra_parameters.
    """
    try:
        bible = None
        bible_revision = (
            db.query(StoryBibleRevision)
            .filter(StoryBibleRevision.project_id == project_id)
            .order_by(StoryBibleRevision.revision_no.desc())
            .first()
        )
        if bible_revision is not None:
            content = dict(bible_revision.content_json or {})
            story_bible = {
                **{k: v for k, v in content.items() if k != "raw_json"},
                "raw_json": dict(content.get("raw_json") or {}),
            }
            bible = project_visual_bible_service.build_for_project(
                story_bible, project_id=project_id
            )
        else:
            legacy_row = (
                db.query(StoryBible)
                .filter(StoryBible.project_id == project_id)
                .first()
            )
            if legacy_row is not None:
                bible = project_visual_bible_service.build_for_project(
                    legacy_row, project_id=project_id
                )
        if bible is None or not bible.fingerprint:
            return {}
        return {
            "visual_style_prompt": build_shared_block(bible),
            "style_fingerprint": bible.fingerprint,
            "visual_bible_version": bible.version,
        }
    except Exception:
        logger.warning(
            "visual bible resolve failed for project %s; render specs continue "
            "without style lock",
            project_id,
            exc_info=True,
        )
        return {}


def _character_appearance_text(
    db: Session,
    project_id: int,
    character_id: str,
    character_name: str,
) -> str:
    """从最新 bible 提取角色外貌文本，供关键帧把立绘形象融入画面。

    优先 canonical identity_prompt_en（二创原作官方设计），其次
    visual_prompt_en / visual_description_cn。

    必须复用调用方 session：自开 SessionLocal 在共享连接（测试 StaticPool
    单连接）上 close 时回滚连接，会吞掉调用方已 flush 的写入。
    """
    if not project_id or (not character_id and not character_name):
        return ""
    try:
        revision = (
            db.query(StoryBibleRevision)
            .filter(StoryBibleRevision.project_id == project_id)
            .order_by(StoryBibleRevision.revision_no.desc())
            .first()
        )
        for char in ((revision.content_json or {}) if revision else {}).get("characters") or []:
            if not isinstance(char, dict):
                continue
            cid = str(char.get("id") or char.get("character_id") or "").strip()
            name = str(char.get("name") or "").strip()
            matched = (character_id and cid == character_id) or (
                character_name and name and name == character_name
            )
            if not matched:
                continue
            canonical = str(
                (char.get("canonical_identity") or {}).get("identity_prompt_en") or ""
            ).strip()
            if canonical:
                return canonical
            for key in ("visual_prompt_en", "visual_description_cn", "appearance"):
                value = str(char.get(key) or "").strip()
                if value:
                    return value
        return ""
    except Exception:
        logger.warning(
            "keyframe appearance resolve failed project=%s character=%s",
            project_id,
            character_id or character_name,
            exc_info=True,
        )
        return ""


def _script_resource_render_spec(
    db: Session,
    revision: ChapterScriptRevision,
    spec: dict[str, Any],
    style_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    role = str(spec["role"])
    # 与 image_generation_service.SIZE_PRESETS 保持一致。provider 无视 size，
    # 这里是 letterbox 归一化目标：横版素材 16:9（1536x864），portrait 角色竖版。
    width, height = {
        "portrait": (1024, 1536),
        "background": (1536, 864),
        "keyframe": (1536, 864),
    }[role]
    paragraphs = {
        str(item.get("paragraph_id") or ""): item
        for item in (revision.script_json or {}).get("paragraphs") or []
        if isinstance(item, dict)
    }
    cast_names = sorted(
        {
            str(item.get("speaker_name") or "").strip()
            for item in paragraphs.values()
            if str(item.get("speaker_name") or "").strip()
        }
    )
    paragraph = paragraphs.get(str(spec.get("paragraph_id") or ""), {})
    extra_parameters: dict[str, Any] = {}
    if style_fields:
        extra_parameters.update(style_fields)
    if role == "portrait":
        canonical_identity = spec.get("canonical_identity")
        if isinstance(canonical_identity, dict) and canonical_identity:
            extra_parameters["canonical_identity"] = canonical_identity
    if role == "background":
        extra_parameters["forbidden_characters"] = cast_names
    elif role == "keyframe":
        speaker_name = str(paragraph.get("speaker_name") or "").strip()
        character_id = str(paragraph.get("speaker_character_id") or "").strip()
        character_entry = {"name": speaker_name, "character_id": character_id}
        appearance = _character_appearance_text(
            db, getattr(revision, "project_id", None), character_id, speaker_name
        )
        if appearance:
            character_entry["appearance"] = appearance
        extra_parameters.update(
            {
                "characters": (
                    [character_entry]
                    if speaker_name or character_id
                    else []
                ),
                "action": str(paragraph.get("text") or ""),
                "emotion": str(paragraph.get("emotion") or "intense"),
            }
        )
    negative_prompt = "" if role == "portrait" else AI_IMAGE_NEGATIVE_PROMPT
    return {
        "prompt": spec["prompt"],
        "provider": "qwen" if AI_IMAGE_MODEL.lower().startswith("qwen-image") else "openai_compatible",
        "model": AI_IMAGE_MODEL,
        "prompt_template_version": "script-resource-v1",
        "negative_prompt": negative_prompt,
        "negative_prompt_version": (
            "none-v1"
            if not negative_prompt
            else f"ai-image-negative-{content_hash(negative_prompt)[:16]}"
        ),
        "seed": None,
        "width": width,
        "height": height,
        "style_pack_version": "chapter-script-visual-v1",
        "identity_version": (
            f"script-character:{spec.get('character_id')}"
            if role == "portrait" and spec.get("character_id")
            else None
        ),
        "postprocess_version": (
            PORTRAIT_GENERATION_CONTRACT_VERSION
            if role == "portrait"
            else LANDSCAPE_POSTPROCESS_CONTRACT_VERSION
        ),
        "validator_version": f"script-resource-{role}-v1",
        "extra_parameters": extra_parameters,
    }


def plan_script_resources(
    db: Session,
    *,
    revision: ChapterScriptRevision,
) -> list[ScriptResourceSlot]:
    existing = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == revision.id)
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )
    if existing:
        return existing

    specs = resource_slot_specs(revision.script_json)
    if not specs:
        return []
    style_fields = _project_visual_style_fields(db, revision.project_id)
    plan = create_asset_plan(
        db,
        project_id=revision.project_id,
        source_kind="chapter_script_revision",
        source_revision_id=revision.id,
        items=[
            {
                "asset_type": spec["role"],
                "logical_key": _logical_key(revision, spec),
                "target_name": spec["target_name"],
                "chapter_index": revision.chapter_index,
                "taxonomy": {
                    "scene_id": spec["scene_id"],
                    "paragraph_id": spec.get("paragraph_id"),
                    "character_id": spec.get("character_id"),
                    "emotion": spec.get("emotion"),
                    "scene_location": spec["target_name"] if spec["role"] == "background" else None,
                    "event_name": spec["target_name"] if spec["role"] == "keyframe" else None,
                },
                "render_spec": _script_resource_render_spec(db, revision, spec, style_fields),
            }
            for spec in specs
        ],
    )
    assets_by_key = {
        str(asset.logical_key or ""): asset
        for asset in plan["assets"]
    }
    slots: list[ScriptResourceSlot] = []
    for spec in specs:
        logical_key = _logical_key(revision, spec)
        asset = assets_by_key.get(logical_key)
        slot = ScriptResourceSlot(
            project_id=revision.project_id,
            chapter_script_revision_id=revision.id,
            slot_key=spec["slot_key"],
            role=spec["role"],
            scene_id=spec["scene_id"],
            paragraph_id=spec.get("paragraph_id"),
            character_id=spec.get("character_id"),
            order_index=spec["order_index"],
            required=bool(spec["required"]),
            status="planned",
            asset_id=asset.id if asset else None,
            spec_json={**spec, "logical_key": logical_key, "plan_key": plan["plan_key"]},
        )
        db.add(slot)
        slots.append(slot)
    db.flush()
    return slots


def ensure_backfilled_script_revision(
    db: Session,
    *,
    chapter: ChapterRevision,
    activate: bool = True,
) -> ChapterScriptRevision:
    head = get_or_create_chapter_script_head(db, chapter.id, for_update=True)
    existing = (
        db.query(ChapterScriptRevision)
        .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
        .order_by(ChapterScriptRevision.revision_no.desc())
        .first()
    )
    if existing:
        if activate and head.current_revision_id is None:
            head.current_revision_id = existing.id
            head.lock_version += 1
        return existing
    bible = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.id == chapter.bible_revision_id)
        .one_or_none()
    )
    # 回填占位（无 LLM）：显式按行传 segments 走 v3 构建器，仅为让旧章节
    # 可编译；正式切分请触发生成任务（LLM 拆分）。
    backfill_segments = [
        {"text": line, "kind": "narration", "scene_key": "default"}
        for line in (chapter.content or "").splitlines()
        if line.strip()
    ]
    script_ir = build_script_ir_from_segments(
        chapter_revision_id=chapter.id,
        chapter_content=chapter.content,
        chapter_content_hash=chapter.content_hash,
        bible_revision_id=chapter.bible_revision_id,
        outline_revision_id=chapter.outline_revision_id,
        characters=((bible.content_json or {}).get("characters") or []) if bible else [],
        llm_output={"segments": backfill_segments, "scenes": []},
    )
    revision_no = int(
        db.query(func.max(ChapterScriptRevision.revision_no))
        .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
        .scalar()
        or 0
    ) + 1
    source_hash = content_hash(
        {
            "origin": "chapter-revision-backfill",
            "chapter_revision_id": chapter.id,
            "content_hash": chapter.content_hash,
            "schema_version": SCRIPT_IR_SCHEMA_VERSION,
        }
    )
    revision = ChapterScriptRevision(
        project_id=chapter.project_id,
        chapter_index=chapter.chapter_index,
        chapter_revision_id=chapter.id,
        bible_revision_id=chapter.bible_revision_id,
        outline_revision_id=chapter.outline_revision_id,
        parent_revision_id=head.current_revision_id,
        revision_no=revision_no,
        source_hash=source_hash,
        script_hash=content_hash(script_ir),
        script_json=script_ir,
        coverage_json=script_ir["coverage"],
        schema_version=SCRIPT_IR_SCHEMA_VERSION,
        generator_version="deterministic-backfill-v1",
        status="complete",
        legacy_source_table="chapter_revisions",
        legacy_source_id=chapter.id,
        created_by=chapter.created_by,
    )
    db.add(revision)
    db.flush()
    if activate:
        head.current_revision_id = revision.id
        head.lock_version += 1
    return revision


def _record_provider_usage(
    db: Session,
    task: GenerationTask,
    result,
) -> None:
    db.add(
        ProviderUsageRecord(
            task_id=task.id,
            provider=result.provider,
            model=result.model,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_amount=task.reserved_cost or 0,
        )
    )


def generate_chapter_script_task(
    task_id: str,
    *,
    adapter: LegacyChapterScriptLLMAdapter | None = None,
    lease: TaskLease | None = None,
) -> Any:
    """Execute one provider call and persist one reviewable ScriptRevision."""
    adapter = adapter or LegacyChapterScriptLLMAdapter()
    session = SessionLocal()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        persisted = (
            session.query(ChapterScriptRevision)
            .filter(ChapterScriptRevision.generation_task_id == task_id)
            .first()
        )
        if persisted:
            from app.application.story_generation_service import GenerationExecutionResult

            result_refs = {"chapter_script_revision_id": persisted.id, "recovered": True}
            actual_cost = Decimal(task.reserved_cost or 0)
            if lease is None:
                if task.status != "succeeded":
                    raise TaskLeaseLostError("chapter script execution requires a fenced lease")
                return GenerationExecutionResult(
                    result_refs=result_refs,
                    actual_cost=actual_cost,
                )
            require_task_lease(task, lease)
            complete_task(
                session,
                task,
                result_refs=result_refs,
                actual_cost=actual_cost,
                lease=lease,
            )
            update_parent_aggregate(session, task)
            session.commit()
            return GenerationExecutionResult(
                result_refs=result_refs,
                actual_cost=actual_cost,
            )
        if lease is None:
            raise TaskLeaseLostError("chapter script execution requires a fenced lease")
        require_task_lease(task, lease)
        if task.kind != CHAPTER_SCRIPT_TASK_KIND:
            raise RuntimeError("task is not chapter_script.generate")
        if bool((task.result_refs or {}).get("provider_call_started")):
            raise ProviderCallAlreadyAttempted("chapter script provider call was already attempted")
        context = resolve_chapter_script_generation_source(session, task=task)
        # 提交会 expire 全部 ORM 实例，session.close() 后再访问属性会抛
        # DetachedInstanceError。LLM 重试循环只用这些纯值快照，不碰 ORM 对象。
        frozen = {
            "project_id": context.project_id,
            "chapter_revision_id": context.chapter_revision_id,
            "source_hash": context.source_hash,
            "request": context.request,
            "chapter_content": context.sources.chapter.content or "",
            "chapter_content_hash": context.sources.chapter.content_hash,
            "bible_id": context.sources.bible.id,
            "bible_characters": deepcopy(
                (context.sources.bible.content_json or {}).get("characters") or []
            ),
            "outline_id": context.sources.outline.id,
        }
        task.result_refs = {"provider_call_started": True}
        append_task_event(
            session,
            task,
            "provider.call.started",
            {"purpose": "chapter_script.semantic_annotations", "attempt": 1},
        )
        session.commit()
    finally:
        session.close()

    from app.application.story_generation_service import GenerationExecutionResult, _run

    # v3：LLM 切分 + 确定性守护校验，失败带具体错误重试（进程内 ≤3 次）。
    # 任务仍 max_attempts=1：不跨任务重放，避免 provider 二次计费失控。
    chapter_script_segment_attempts = 3
    feedback = ""
    provider_result = None
    validation_errors: list[str] = []
    for attempt in range(1, chapter_script_segment_attempts + 1):
        provider_result = _run(
            adapter.generate(frozen["request"], validation_feedback=feedback)
        )
        try:
            build_script_ir_from_segments(
                chapter_revision_id=frozen["chapter_revision_id"],
                chapter_content=frozen["chapter_content"],
                chapter_content_hash=frozen["chapter_content_hash"],
                bible_revision_id=frozen["bible_id"],
                outline_revision_id=frozen["outline_id"],
                characters=frozen["bible_characters"],
                llm_output=provider_result.data,
            )
            break
        except ScriptIRValidationError as exc:
            validation_errors.append(f"attempt {attempt}: {exc}")
            feedback = (
                f"attempt {attempt} 校验失败：{exc}。请重新输出完整 segments，"
                "确保 text 为原文逐字摘录且按顺序拼接覆盖全文。"
            )
    else:
        raise RuntimeError(
            "chapter script LLM segmentation failed validation after "
            f"{chapter_script_segment_attempts} attempts: "
            + " | ".join(validation_errors)
        )

    session = SessionLocal()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        if lease is None:
            raise TaskLeaseLostError("chapter script persistence requires a fenced lease")
        require_task_lease(task, lease)
        context = resolve_chapter_script_generation_source(session, task=task)
        if (
            context.project_id != frozen["project_id"]
            or context.chapter_revision_id != frozen["chapter_revision_id"]
            or context.source_hash != frozen["source_hash"]
        ):
            raise ChapterScriptSourceError("Chapter Script source changed during generation")
        chapter = context.sources.chapter
        bible = context.sources.bible
        outline = context.sources.outline
        script_ir = build_script_ir_from_segments(
            chapter_revision_id=chapter.id,
            chapter_content=chapter.content,
            chapter_content_hash=chapter.content_hash,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            characters=(bible.content_json or {}).get("characters") or [],
            llm_output=provider_result.data,
        )
        coverage = validate_script_ir(script_ir, expected_content=chapter.content)
        if coverage["coverage_ratio"] != 1.0:
            raise RuntimeError("chapter script source coverage is not 100%")

        session.query(ChapterRevision).filter(
            ChapterRevision.id == chapter.id
        ).with_for_update().one()
        persisted = (
            session.query(ChapterScriptRevision)
            .filter(ChapterScriptRevision.generation_task_id == task.id)
            .one_or_none()
        )
        if persisted:
            result_refs = {"chapter_script_revision_id": persisted.id, "recovered": True}
            actual_cost = Decimal(task.reserved_cost or 0)
            complete_task(
                session,
                task,
                result_refs=result_refs,
                actual_cost=actual_cost,
                lease=lease,
            )
            update_parent_aggregate(session, task)
            session.commit()
            return GenerationExecutionResult(result_refs=result_refs, actual_cost=actual_cost)
        script_hash = content_hash(script_ir)
        revision_no = int(
            session.query(func.max(ChapterScriptRevision.revision_no))
            .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
            .scalar()
            or 0
        ) + 1
        revision = ChapterScriptRevision(
            project_id=chapter.project_id,
            chapter_index=context.display_index,
            chapter_revision_id=chapter.id,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            parent_revision_id=context.parent_revision_id,
            revision_no=revision_no,
            source_hash=frozen["source_hash"],
            script_hash=script_hash,
            script_json=script_ir,
            coverage_json=coverage,
            schema_version=SCRIPT_IR_SCHEMA_VERSION,
            generator_version=SCRIPT_GENERATOR_VERSION,
            status="ready",
            generation_task_id=task.id,
            created_by=task.user_id,
        )
        session.add(revision)
        session.flush()
        slots = plan_script_resources(session, revision=revision)
        # 配角回写：正文里实际开口但 bible 未登记的角色，以占位档案增量补录，
        # 后续章节的标注/立绘/配音即可引用到 character_id。
        bible_characters = (bible.content_json or {}).get("characters") or []
        writeback_names = collect_unregistered_speaker_names(
            script_ir, known_characters=bible_characters
        )
        if writeback_names:
            writeback_revision = append_auto_registered_characters(
                session,
                project_id=chapter.project_id,
                names=writeback_names,
                source_note=f"chapter_script:{revision.id}",
                task_id=task.id,
                user_id=task.user_id,
            )
            if writeback_revision is not None:
                append_task_event(
                    session,
                    task,
                    "bible.characters_augmented",
                    {
                        "bible_revision_id": writeback_revision.id,
                        "appended_character_names": writeback_names,
                    },
                )
        _record_provider_usage(session, task, provider_result)
        append_task_event(
            session,
            task,
            "artifact.ready",
            {
                "type": "chapter_script_revision",
                "id": revision.id,
                "coverage_ratio": coverage["coverage_ratio"],
                "resource_slot_count": len(slots),
            },
        )
        result_refs = {
            "chapter_script_revision_id": revision.id,
            "script_hash": revision.script_hash,
            "coverage_ratio": 1.0,
            "resource_slot_ids": [slot.id for slot in slots],
        }
        actual_cost = Decimal(task.reserved_cost or 0)
        complete_task(
            session,
            task,
            result_refs=result_refs,
            actual_cost=actual_cost,
            lease=lease,
        )
        update_parent_aggregate(session, task)
        session.commit()
        return GenerationExecutionResult(result_refs=result_refs, actual_cost=actual_cost)
    finally:
        session.close()


def create_manual_chapter_script_revision(
    db: Session,
    *,
    chapter_revision_id: str,
    script_json: dict[str, Any],
    user_id: int,
    parent_revision_id: str | None = None,
    auto_register_characters: bool = True,
) -> tuple[ChapterScriptRevision, list[str] | None]:
    """Materialize a client-side draft as an immutable, non-activated revision.

    Draft-publish bridge: the caller (frontend finalize/generate flow) submits
    draft content; spans must still cover the chapter text verbatim, so plot
    edits belong to the chapter stage, not here.
    """
    sources = _load_sources(db, chapter_revision_id=chapter_revision_id)
    chapter = sources.chapter
    script_ir = deepcopy(script_json)
    if not isinstance(script_ir, dict):
        raise HTTPException(status_code=422, detail="script_json 必须是 JSON 对象")
    source_block = script_ir.get("source")
    if not isinstance(source_block, dict):
        raise HTTPException(status_code=422, detail="script_json 缺少 source 块")
    # Draft edits invalidate the embedded hash; rewrite against the target
    # chapter so only the verbatim-span check decides validity.
    source_block["content_hash"] = content_hash(chapter.content)
    try:
        coverage = validate_script_ir(script_ir, expected_content=chapter.content)
    except ScriptIRValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"脚本 spans 与章节正文不一致（要改剧情请先编辑章节正文）：{exc}",
        ) from exc

    head = get_or_create_chapter_script_head(db, chapter.id)
    parent_id = parent_revision_id or head.current_revision_id
    if parent_id is None:
        # No activated head yet (e.g. generated but never reviewed): fork from
        # the newest revision so the draft chain stays connected.
        parent_id = (
            db.query(ChapterScriptRevision.id)
            .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
            .order_by(ChapterScriptRevision.revision_no.desc())
            .limit(1)
            .scalar()
        )
    if parent_id is not None:
        parent = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.id == parent_id,
                ChapterScriptRevision.chapter_revision_id == chapter.id,
            )
            .one_or_none()
        )
        if not parent:
            raise HTTPException(
                status_code=404,
                detail="父脚本修订不存在或不属于该章节修订",
            )
    source_hash = content_hash(
        {
            "origin": "manual_edit",
            "chapter_revision_id": chapter.id,
            "chapter_content_hash": chapter.content_hash,
            "parent_revision_id": parent_id,
        }
    )
    script_hash = content_hash(script_ir)
    # script_hash covers the full IR incl. the rewritten source.content_hash,
    # so (chapter, script_hash) is a stable content-addressed dedup key across
    # manual and generated revisions alike; source_hash varies with the
    # fallback parent and must not participate.
    existing = (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.chapter_revision_id == chapter.id,
            ChapterScriptRevision.script_hash == script_hash,
        )
        .one_or_none()
    )
    if existing is None:
        # 客户端物化的 script_json 经 JS 往返会归一化整值浮点，hash 漂移；宽容内容比较。
        js_digest = content_hash_js(script_ir)
        existing = next(
            (
                revision
                for revision in db.query(ChapterScriptRevision)
                .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
                .order_by(ChapterScriptRevision.revision_no)
                .all()
                if content_hash_js(revision.script_json) == js_digest
            ),
            None,
        )
    if existing:
        return existing, None

    revision_no = int(
        db.query(func.max(ChapterScriptRevision.revision_no))
        .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
        .scalar()
        or 0
    ) + 1
    revision = ChapterScriptRevision(
        project_id=chapter.project_id,
        chapter_index=sources.path_chapter.display_index,
        chapter_revision_id=chapter.id,
        bible_revision_id=sources.bible.id,
        outline_revision_id=sources.outline.id,
        parent_revision_id=parent_id,
        revision_no=revision_no,
        source_hash=source_hash,
        script_hash=script_hash,
        script_json=script_ir,
        coverage_json=coverage,
        schema_version=str(script_ir.get("schema_version") or SCRIPT_IR_SCHEMA_VERSION),
        generator_version="manual-edit-v1",
        status="ready",
        generation_task_id=None,
        created_by=user_id,
    )
    db.add(revision)
    try:
        db.flush()
    except IntegrityError:
        # (chapter_revision, revision_no) 唯一键竞争：另一事务刚插入同号修订。
        # 回滚本插入后重查——同内容则复用既有修订（内容寻址幂等），否则顺延新号。
        db.rollback()
        existing = (
            db.query(ChapterScriptRevision)
            .filter(
                ChapterScriptRevision.chapter_revision_id == chapter.id,
                ChapterScriptRevision.script_hash == script_hash,
            )
            .one_or_none()
        )
        if existing is None:
            js_digest = content_hash_js(script_ir)
            existing = next(
                (
                    candidate
                    for candidate in db.query(ChapterScriptRevision)
                    .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
                    .order_by(ChapterScriptRevision.revision_no)
                    .all()
                    if content_hash_js(candidate.script_json) == js_digest
                ),
                None,
            )
        if existing is not None:
            return existing, None
        revision_no = int(
            db.query(func.max(ChapterScriptRevision.revision_no))
            .filter(ChapterScriptRevision.chapter_revision_id == chapter.id)
            .scalar()
            or 0
        ) + 1
        revision = ChapterScriptRevision(
            project_id=chapter.project_id,
            chapter_index=sources.path_chapter.display_index,
            chapter_revision_id=chapter.id,
            bible_revision_id=sources.bible.id,
            outline_revision_id=sources.outline.id,
            parent_revision_id=parent_id,
            revision_no=revision_no,
            source_hash=source_hash,
            script_hash=script_hash,
            script_json=script_ir,
            coverage_json=coverage,
            schema_version=str(script_ir.get("schema_version") or SCRIPT_IR_SCHEMA_VERSION),
            generator_version="manual-edit-v1",
            status="ready",
            generation_task_id=None,
            created_by=user_id,
        )
        db.add(revision)
        db.flush()
    plan_script_resources(db, revision=revision)

    appended_names: list[str] | None = None
    if auto_register_characters:
        bible_characters = (sources.bible.content_json or {}).get("characters") or []
        writeback_names = collect_unregistered_speaker_names(
            script_ir, known_characters=bible_characters
        )
        if writeback_names:
            append_auto_registered_characters(
                db,
                project_id=chapter.project_id,
                names=writeback_names,
                source_note=f"chapter_script:{revision.id}",
                user_id=user_id,
            )
            appended_names = writeback_names
    return revision, appended_names


def bind_script_resource_slot(
    db: Session,
    *,
    project_id: int,
    slot_id: str,
    asset_version_id: str,
    script_revision_id: str,
    generation_task_id: str | None = None,
    expected_lock_version: int | None = None,
) -> ScriptResourceSlot:
    if expected_lock_version is not None and (
        isinstance(expected_lock_version, bool)
        or not isinstance(expected_lock_version, int)
        or expected_lock_version < 1
    ):
        raise AppError(
            code="precondition.invalid",
            message="Resource slot lock version must be a positive integer",
            status_code=400,
        )
    query = db.query(ScriptResourceSlot).filter(
        ScriptResourceSlot.id == slot_id,
        ScriptResourceSlot.project_id == project_id,
    )
    query = query.join(
        ChapterScriptRevision,
        ChapterScriptRevision.id == ScriptResourceSlot.chapter_script_revision_id,
    ).filter(ChapterScriptRevision.id == script_revision_id)
    slot = query.with_for_update().first()
    if not slot:
        raise HTTPException(status_code=404, detail="Script resource slot 不存在")
    if (
        expected_lock_version is not None
        and slot.lock_version != expected_lock_version
    ):
        raise AppError(
            code="resource_slot.version_conflict",
            message="Script resource slot changed since it was read",
            status_code=409,
            details={
                "current_lock_version": slot.lock_version,
                "asset_version_id": slot.asset_version_id,
            },
        )
    version = db.query(AssetVersion).filter(AssetVersion.id == asset_version_id).first()
    if not version or not version.storage_object_id:
        raise HTTPException(status_code=409, detail="素材版本尚无可绑定文件")
    if slot.asset_id is not None and version.asset_id != slot.asset_id:
        raise HTTPException(status_code=409, detail="素材版本不属于该预分配槽位")
    previous_binding = (slot.asset_version_id, slot.status)
    create_asset_binding(
        db,
        project_id=project_id,
        source_kind="chapter_script_revision",
        source_id=slot.chapter_script_revision_id,
        asset_version_id=version.id,
        node_key=slot.scene_id,
        segment_key=slot.paragraph_id,
        role=slot.role,
        order_index=slot.order_index,
        required=slot.required,
    )
    slot.asset_version_id = version.id
    slot.generation_task_id = generation_task_id
    slot.status = "bound"
    if previous_binding != (slot.asset_version_id, slot.status):
        slot.lock_version += 1
        slot.updated_at = utcnow()
    return slot


def bind_generated_script_resource_slots(
    db: Session,
    *,
    project_id: int,
    asset_version_id: str,
    generation_task_id: str,
    script_revision_id: str,
) -> list[ScriptResourceSlot]:
    version = db.query(AssetVersion).filter(AssetVersion.id == asset_version_id).one()
    slots = (
        db.query(ScriptResourceSlot)
        .filter(
            ScriptResourceSlot.project_id == project_id,
            ScriptResourceSlot.chapter_script_revision_id == script_revision_id,
            ScriptResourceSlot.asset_id == version.asset_id,
            # "failed" slots must rebind too: a terminal failure marks the
            # slot failed, and a revived re-render succeeds later — skipping
            # it here would leave the slot stuck even though a version exists.
            ScriptResourceSlot.status.in_(["planned", "generating", "failed"]),
        )
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )
    for slot in slots:
        bind_script_resource_slot(
            db,
            project_id=project_id,
            slot_id=slot.id,
            asset_version_id=version.id,
            script_revision_id=script_revision_id,
            generation_task_id=generation_task_id,
        )
    return slots


def queue_vn_graph_when_resources_ready(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    script_revision_id: str,
) -> GenerationTask | None:
    revision = (
        db.query(ChapterScriptRevision)
        .filter(
            ChapterScriptRevision.id == script_revision_id,
            ChapterScriptRevision.project_id == project_id,
        )
        .first()
    )
    if not revision:
        return None
    slots = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == revision.id)
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )
    if not slots or any(
        slot.required and (slot.status != "bound" or not slot.asset_version_id) for slot in slots
    ):
        return None
    binding_hash = content_hash(
        [
            {
                "slot_id": slot.id,
                "scene_id": slot.scene_id,
                "paragraph_id": slot.paragraph_id,
                "character_id": slot.character_id,
                "asset_version_id": slot.asset_version_id,
            }
            for slot in slots
        ]
    )
    from app.application.vn_graph_service import create_vn_graph_compile_task
    from app.services.vn_graph_compiler import VNGRAPH_COMPILER_VERSION

    task, _ = create_vn_graph_compile_task(
        db,
        user_id=user_id,
        script_revision_id=revision.id,
        # 编译器版本进幂等键：版本升级后同 revision 重触发不撞旧任务 409。
        idempotency_key=f"auto-vngraph:{revision.id}:{binding_hash}:{VNGRAPH_COMPILER_VERSION}",
    )
    return task


def request_script_resource_render(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    script_revision_id: str,
    role: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if role not in {"portrait", "background", "keyframe"}:
        raise HTTPException(status_code=422, detail="不支持的资源角色")
    revision_query = db.query(ChapterScriptRevision).filter(
        ChapterScriptRevision.id == script_revision_id,
        ChapterScriptRevision.project_id == project_id,
    )
    revision = revision_query.first()
    if not revision:
        raise HTTPException(status_code=404, detail="Chapter Script revision 不存在")
    slots = plan_script_resources(db, revision=revision)
    selected_slots = [slot for slot in slots if slot.role == role]
    plan_keys = sorted({_text_slot_plan_key(slot) for slot in selected_slots if _text_slot_plan_key(slot)})
    if not selected_slots:
        all_plan_keys = sorted({_text_slot_plan_key(slot) for slot in slots if _text_slot_plan_key(slot)})
        if not all_plan_keys and not slots:
            empty_plan = create_asset_plan(
                db,
                project_id=revision.project_id,
                source_kind="chapter_script_revision",
                source_revision_id=revision.id,
                items=[],
            )
            all_plan_keys = [empty_plan["plan_key"]]
        if len(all_plan_keys) != 1:
            raise HTTPException(status_code=409, detail="Script resource plan 不完整")
        plan_keys = all_plan_keys
    if len(plan_keys) != 1:
        raise HTTPException(status_code=409, detail="Script resource plan 不完整")
    result = request_asset_plan_render(
        db,
        user_id=user_id,
        project_id=project_id,
        plan_key=plan_keys[0],
        role=role,
        idempotency_key=idempotency_key,
    )
    parent = result["parent"]
    # Some workers/tests intentionally disable autoflush. The dependency rows
    # must be visible before resolving each Slot's child task.
    db.flush()
    task_by_asset = {
        int((task.parameters or {}).get("asset_id")): task
        for task in db.query(GenerationTask)
        .join(
            GenerationTaskDependency,
            GenerationTaskDependency.depends_on_task_id == GenerationTask.id,
        )
        .filter(GenerationTaskDependency.task_id == parent.id)
        .all()
    }
    cached_ids = list((parent.result_refs or {}).get("cached_version_ids") or [])
    for version in (
        db.query(AssetVersion).filter(AssetVersion.id.in_(cached_ids)).all() if cached_ids else []
    ):
        bind_generated_script_resource_slots(
            db,
            project_id=project_id,
            asset_version_id=version.id,
            generation_task_id=parent.id,
            script_revision_id=script_revision_id,
        )
    for slot in selected_slots:
        task = task_by_asset.get(int(slot.asset_id or 0))
        if task and slot.status in ("planned", "failed"):
            slot.status = "generating"
            slot.generation_task_id = task.id
    compile_task = queue_vn_graph_when_resources_ready(
        db,
        user_id=user_id,
        project_id=project_id,
        script_revision_id=script_revision_id,
    )
    result["vngraph_task_id"] = compile_task.id if compile_task else None
    result["slot_ids"] = [slot.id for slot in selected_slots]
    parent.result_refs = {
        **dict(parent.result_refs or {}),
        "selected_slot_count": len(selected_slots),
        "role": role,
    }
    return result


def _text_slot_plan_key(slot: ScriptResourceSlot) -> str:
    return str((slot.spec_json or {}).get("plan_key") or "")


def list_script_resource_slots(
    db: Session,
    *,
    project_id: int,
    script_revision_id: str,
) -> list[ScriptResourceSlot]:
    revision_query = db.query(ChapterScriptRevision).filter(
        ChapterScriptRevision.id == script_revision_id,
        ChapterScriptRevision.project_id == project_id,
    )
    revision = revision_query.first()
    if not revision:
        raise HTTPException(status_code=404, detail="Chapter Script revision 不存在")
    return (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == revision.id)
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )
