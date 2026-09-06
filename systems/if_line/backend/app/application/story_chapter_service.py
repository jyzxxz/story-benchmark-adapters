"""StoryPath-scoped chapter generation and immutable revision persistence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.story_context_resolver import (
    StoryContextResolutionError,
    StoryContextResolver,
)
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import create_generation_task
from app.models_v2 import (
    ChapterRevision,
    ChapterSlot,
    GenerationTask,
    OutlineChapter,
    StoryPathChapter,
)


CHAPTER_SOURCE_SCHEMA_VERSION = "story-path-chapter-source-v1"
CHAPTER_GENERATION_COST = Decimal("8")
DEFAULT_CHAPTER_WORD_COUNT_MIN = 3000
DEFAULT_CHAPTER_WORD_COUNT_MAX = 4500
MAX_CHAPTER_WORD_COUNT = 100_000
MAX_CHAPTER_INSTRUCTIONS_LENGTH = 12_000


@dataclass(frozen=True)
class ChapterGenerationContext:
    project_id: int
    story_path_id: str
    path_chapter_id: str
    chapter_slot_id: str
    display_index: int
    parent_revision_id: str | None
    bible_revision_id: str
    outline_revision_id: str
    state_snapshot_id: str | None
    story_bible: dict[str, Any]
    chapter_outline: dict[str, Any]
    state: dict[str, Any] | None
    previous_chapters: tuple[dict[str, Any], ...]
    ancestor_outline_summaries: tuple[dict[str, Any], ...]
    context_manifest: dict[str, Any]
    context_hash: str
    generation_parameters: dict[str, Any]
    source_manifest: dict[str, Any]
    source_hash: str
    total_chapters: int | None = None


def _validation_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _positive_integer(value: object, *, field: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise _validation_error(f"{field} must be an integer")
    if value < 1 or value > MAX_CHAPTER_WORD_COUNT:
        raise _validation_error(
            f"{field} must be between 1 and {MAX_CHAPTER_WORD_COUNT}"
        )
    return value


def normalize_chapter_generation_parameters(
    parameters: dict[str, Any] | None,
    *,
    instructions: str | None = None,
) -> dict[str, Any]:
    """Validate and canonicalize every provider-affecting chapter parameter."""

    if parameters is None:
        normalized: dict[str, Any] = {}
    elif isinstance(parameters, dict):
        normalized = deepcopy(parameters)
    else:
        raise _validation_error("parameters must be an object")

    instruction_value = instructions
    if instruction_value is None:
        instruction_value = normalized.get("instructions")
    if instruction_value is not None:
        if not isinstance(instruction_value, str):
            raise _validation_error("instructions must be a string")
        instruction_value = instruction_value.strip()
        if len(instruction_value) > MAX_CHAPTER_INSTRUCTIONS_LENGTH:
            raise _validation_error(
                f"instructions cannot exceed {MAX_CHAPTER_INSTRUCTIONS_LENGTH} characters"
            )
        instruction_value = instruction_value or None

    word_count_min = _positive_integer(
        normalized.get("word_count_min"),
        field="word_count_min",
        default=DEFAULT_CHAPTER_WORD_COUNT_MIN,
    )
    word_count_max = _positive_integer(
        normalized.get("word_count_max"),
        field="word_count_max",
        default=DEFAULT_CHAPTER_WORD_COUNT_MAX,
    )
    if word_count_max < word_count_min:
        raise _validation_error("word_count_max cannot be less than word_count_min")
    stream = normalized.get("stream", True)
    if not isinstance(stream, bool):
        raise _validation_error("stream must be a boolean")

    normalized["word_count_min"] = word_count_min
    normalized["word_count_max"] = word_count_max
    normalized["stream"] = stream
    normalized["instructions"] = instruction_value
    return normalized


def _context_http_error(exc: StoryContextResolutionError) -> HTTPException:
    if exc.code in {
        "context.path_chapter_missing",
        "context.story_path_missing",
    }:
        status_code = 404
    elif exc.code in {"context.manifest_invalid", "context.hash_mismatch"}:
        status_code = 422
    else:
        status_code = 409
    return HTTPException(status_code=status_code, detail=exc.detail)


def build_chapter_generation_source(
    db: Session,
    *,
    path_chapter_id: str,
    parameters: dict[str, Any] | None = None,
    instructions: str | None = None,
    state_snapshot_id: str | None = None,
    bible_revision_id: str | None = None,
    outline_revision_id: str | None = None,
    ancestor_revision_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Freeze all mutable chapter inputs before a provider task is queued."""

    generation_parameters = normalize_chapter_generation_parameters(
        parameters,
        instructions=instructions,
    )
    try:
        resolved = StoryContextResolver(db).resolve(
            path_chapter_id,
            state_snapshot_id=state_snapshot_id,
            bible_revision_id=bible_revision_id,
            outline_revision_id=outline_revision_id,
            ancestor_revision_overrides=ancestor_revision_overrides,
        )
    except StoryContextResolutionError as exc:
        raise _context_http_error(exc) from exc

    target = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.status == "active",
        )
        .with_for_update()
        .one()
    )
    parent_revision_id = target.current_revision_id
    if parent_revision_id:
        parent = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == parent_revision_id)
            .one_or_none()
        )
        if (
            not parent
            or parent.project_id != resolved.manifest["project_id"]
            or parent.chapter_slot_id != target.chapter_slot_id
        ):
            raise HTTPException(
                status_code=409,
                detail="Current PathChapter Head does not belong to its ChapterSlot",
            )

    source = {
        "schema_version": CHAPTER_SOURCE_SCHEMA_VERSION,
        "context_manifest": resolved.manifest,
        "context_hash": resolved.context_hash,
        "chapter_slot_id": target.chapter_slot_id,
        "display_index": target.display_index,
        "parent_revision_id": parent_revision_id,
        "generation_parameters": generation_parameters,
        # 显式锚点进入冻结源：同 key 换锚点 → 409；无锚点任务此块为 None 值。
        "revision_anchors": {
            "bible_revision_id": bible_revision_id,
            "outline_revision_id": outline_revision_id,
            "ancestor_revision_overrides": dict(ancestor_revision_overrides)
            if ancestor_revision_overrides
            else None,
        },
    }
    return {
        "chapter_source": source,
        "chapter_source_hash": content_hash(source),
    }


def create_story_path_chapter_generation_task(
    db: Session,
    *,
    user_id: int,
    path_chapter_id: str,
    idempotency_key: str,
    parameters: dict[str, Any] | None = None,
    instructions: str | None = None,
    state_snapshot_id: str | None = None,
    parent_task_id: str | None = None,
    root_task_id: str | None = None,
    bible_revision_id: str | None = None,
    outline_revision_id: str | None = None,
    ancestor_revision_overrides: Mapping[str, str] | None = None,
) -> tuple[GenerationTask, bool]:
    generation_parameters = normalize_chapter_generation_parameters(
        parameters,
        instructions=instructions,
    )
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        existing_source = (existing.source_refs or {}).get("chapter_source", {})
        existing_manifest = existing_source.get("context_manifest", {})
        same_request = (
            existing.kind == "chapter.generate"
            and existing_manifest.get("path_chapter_id") == path_chapter_id
            and existing_source.get("generation_parameters") == generation_parameters
            and existing.parent_task_id == parent_task_id
            and existing.root_task_id == root_task_id
        )
        if state_snapshot_id is not None:
            same_request = same_request and (
                existing_manifest.get("state_snapshot_id") == state_snapshot_id
            )
        # 锚点只在请求显式给出时参与幂等比对（同 key 换锚点 → 409）；
        # 无锚点重放保持旧行为不变。
        existing_anchors = existing_source.get("revision_anchors") or {}
        if bible_revision_id is not None:
            same_request = same_request and (
                existing_anchors.get("bible_revision_id") == bible_revision_id
            )
        if outline_revision_id is not None:
            same_request = same_request and (
                existing_anchors.get("outline_revision_id") == outline_revision_id
            )
        if ancestor_revision_overrides:
            same_request = same_request and (
                existing_anchors.get("ancestor_revision_overrides")
                == dict(ancestor_revision_overrides)
            )
        if not same_request:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key has already been used for a different request",
            )
        return existing, False

    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=path_chapter_id,
        parameters=generation_parameters,
        state_snapshot_id=state_snapshot_id,
        bible_revision_id=bible_revision_id,
        outline_revision_id=outline_revision_id,
        ancestor_revision_overrides=ancestor_revision_overrides,
    )
    source = source_refs["chapter_source"]
    return create_generation_task(
        db,
        user_id=user_id,
        project_id=source["context_manifest"]["project_id"],
        kind="chapter.generate",
        idempotency_key=idempotency_key,
        source_refs=source_refs,
        parameters=source["generation_parameters"],
        estimated_cost=CHAPTER_GENERATION_COST,
        parent_task_id=parent_task_id,
        root_task_id=root_task_id,
    )


def create_manual_story_path_chapter_revision(
    db: Session,
    *,
    path_chapter_id: str,
    parent_revision_id: str | None,
    content: str,
    user_id: int,
) -> ChapterRevision:
    """Create an unselected edit against the path's exact reviewed context.

    草稿物化模型：head 在发布前保持为空，因此 parent 修订若携带生成任务的冻结
    上下文，则直接沿用它（解析器会复验 content_hash）；仅无 parent 时回落 head。
    """

    if parent_revision_id:
        parent = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == parent_revision_id)
            .one_or_none()
        )
        parent_task = (
            db.query(GenerationTask)
            .filter(
                GenerationTask.id == parent.generation_task_id,
                GenerationTask.kind == "chapter.generate",
            )
            .one_or_none()
            if parent
            else None
        )
        parent_source = (parent_task.source_refs or {}) if parent_task else {}
        manifest = (parent_source.get("chapter_source") or {}).get("context_manifest") or {}
        if manifest.get("path_chapter_id") == path_chapter_id and parent_source.get(
            "chapter_source"
        ):
            source_refs = deepcopy(parent_source)
            source = source_refs["chapter_source"]
            source["parent_revision_id"] = parent_revision_id
            source["origin"] = "manual_edit"
            source_refs["chapter_source_hash"] = content_hash(source)
            try:
                context = resolve_chapter_generation_source(
                    db,
                    task_project_id=manifest["project_id"],
                    source_refs=source_refs,
                )
                return create_story_path_chapter_revision(
                    db,
                    context=context,
                    content=content,
                    user_id=user_id,
                )
            except NonRetryableTaskError as exc:
                raise HTTPException(status_code=409, detail=exc.safe_detail) from exc

    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=path_chapter_id,
        parameters={},
    )
    source = source_refs["chapter_source"]
    source["parent_revision_id"] = parent_revision_id
    source["origin"] = "manual_edit"
    source_refs["chapter_source_hash"] = content_hash(source)
    try:
        context = resolve_chapter_generation_source(
            db,
            task_project_id=source["context_manifest"]["project_id"],
            source_refs=source_refs,
        )
        return create_story_path_chapter_revision(
            db,
            context=context,
            content=content,
            user_id=user_id,
        )
    except NonRetryableTaskError as exc:
        raise HTTPException(status_code=409, detail=exc.safe_detail) from exc


def _source_failure(code: str, detail: str) -> NonRetryableTaskError:
    return NonRetryableTaskError(code=code, safe_detail=detail)


def resolve_chapter_generation_source(
    db: Session,
    *,
    task_project_id: int | None,
    source_refs: dict[str, Any],
) -> ChapterGenerationContext:
    """Validate a frozen task source without reading any mutable Head."""

    source = source_refs.get("chapter_source")
    source_digest = source_refs.get("chapter_source_hash")
    if not isinstance(source, dict) or not isinstance(source_digest, str):
        raise _source_failure(
            "chapter.source_invalid",
            "章节生成输入缺少冻结的 StoryPath 上下文，请重新发起生成",
        )
    if content_hash(source) != source_digest:
        raise _source_failure(
            "chapter.source_hash_mismatch",
            "章节生成输入已损坏，请重新发起生成",
        )
    if source.get("schema_version") != CHAPTER_SOURCE_SCHEMA_VERSION:
        raise _source_failure(
            "chapter.source_schema_unsupported",
            "章节生成输入版本不受支持，请重新发起生成",
        )

    manifest = source.get("context_manifest")
    context_digest = source.get("context_hash")
    if not isinstance(manifest, dict) or not isinstance(context_digest, str):
        raise _source_failure(
            "chapter.context_invalid",
            "章节生成上下文格式无效，请重新发起生成",
        )
    try:
        resolved = StoryContextResolver(db).resolve_frozen(
            manifest,
            expected_context_hash=context_digest,
        )
    except StoryContextResolutionError as exc:
        raise _source_failure(exc.code, exc.detail) from exc

    project_id = resolved.manifest["project_id"]
    if task_project_id != project_id:
        raise _source_failure(
            "chapter.project_mismatch",
            "章节生成任务不属于冻结上下文声明的项目",
        )
    path_chapter_id = resolved.manifest["path_chapter_id"]
    target = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.status == "active",
        )
        .one_or_none()
    )
    if not target:
        raise _source_failure("context.path_chapter_missing", "PathChapter 不存在")
    chapter_slot_id = source.get("chapter_slot_id")
    display_index = source.get("display_index")
    if (
        not isinstance(chapter_slot_id, str)
        or not chapter_slot_id
        or target.chapter_slot_id != chapter_slot_id
    ):
        raise _source_failure(
            "chapter.slot_mismatch",
            "章节生成目标与冻结的 ChapterSlot 不匹配",
        )
    if (
        isinstance(display_index, bool)
        or not isinstance(display_index, int)
        or target.display_index != display_index
    ):
        raise _source_failure(
            "chapter.display_index_mismatch",
            "章节显示顺序与冻结输入不匹配",
        )
    slot = (
        db.query(ChapterSlot)
        .filter(
            ChapterSlot.id == chapter_slot_id,
            ChapterSlot.project_id == project_id,
        )
        .one_or_none()
    )
    if not slot:
        raise _source_failure(
            "chapter.slot_ownership_mismatch",
            "ChapterSlot 不属于章节生成任务的项目",
        )

    parent_revision_id = source.get("parent_revision_id")
    if parent_revision_id is not None:
        if not isinstance(parent_revision_id, str) or not parent_revision_id:
            raise _source_failure(
                "chapter.parent_invalid",
                "parent_revision_id 格式无效",
            )
        parent = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == parent_revision_id)
            .one_or_none()
        )
        if (
            not parent
            or parent.project_id != project_id
            or parent.chapter_slot_id != chapter_slot_id
        ):
            raise _source_failure(
                "chapter.parent_mismatch",
                "章节编辑父 Revision 不属于目标 ChapterSlot",
            )

    try:
        generation_parameters = normalize_chapter_generation_parameters(
            source.get("generation_parameters")
        )
    except HTTPException as exc:
        raise _source_failure(
            "chapter.parameters_invalid",
            str(exc.detail),
        ) from exc
    if generation_parameters != source.get("generation_parameters"):
        raise _source_failure(
            "chapter.parameters_not_canonical",
            "章节生成参数不是规范格式，请重新发起生成",
        )

    previous_chapters = tuple(
        {
            "path_chapter_id": ancestor.path_chapter_id,
            "chapter_revision_id": ancestor.chapter_revision_id,
            "chapter_index": ancestor.display_index,
            "display_index": ancestor.display_index,
            "content_hash": ancestor.content_hash,
            # 续写锚点取结尾而非开头:模型需要知道上一章停在哪,
            # 而不是重复看到上一章的开场场景。
            "summary": ancestor.content[-1200:],
        }
        for ancestor in resolved.ancestors
    )
    ancestor_outline_summaries = (
        db.query(OutlineChapter)
        .filter(
            OutlineChapter.outline_revision_id == resolved.manifest["outline_revision_id"],
            OutlineChapter.story_path_chapter_id.in_(
                [ancestor.path_chapter_id for ancestor in resolved.ancestors]
            ),
        )
        .order_by(OutlineChapter.display_index)
        .all()
        if resolved.ancestors
        else []
    )
    if (
        resolved.fork_choice
        and resolved.outline_chapter["display_index"]
        > resolved.fork_choice["display_index"]
    ):
        previous_chapters += (
            {
                "kind": "branch_choice",
                "candidate_id": resolved.fork_choice["candidate_id"],
                "candidate_set_revision_id": resolved.fork_choice[
                    "candidate_set_revision_id"
                ],
                "chapter_index": resolved.fork_choice["display_index"],
                "display_index": resolved.fork_choice["display_index"],
                "content_hash": resolved.fork_choice["candidate_content_hash"],
                "summary": resolved.fork_choice["preview_text"][:500],
            },
        )
    chapter_outline = {
        **resolved.outline_chapter,
        # The provider adapter still uses this field as presentation metadata.
        "chapter_index": resolved.outline_chapter["display_index"],
    }
    total_chapters = (
        db.query(func.count(OutlineChapter.id))
        .filter(
            OutlineChapter.outline_revision_id == resolved.manifest["outline_revision_id"]
        )
        .scalar()
    ) or None
    return ChapterGenerationContext(
        project_id=project_id,
        story_path_id=resolved.manifest["story_path_id"],
        path_chapter_id=path_chapter_id,
        chapter_slot_id=chapter_slot_id,
        display_index=display_index,
        parent_revision_id=parent_revision_id,
        bible_revision_id=resolved.manifest["bible_revision_id"],
        outline_revision_id=resolved.manifest["outline_revision_id"],
        state_snapshot_id=resolved.manifest.get("state_snapshot_id"),
        story_bible=resolved.story_bible,
        chapter_outline=chapter_outline,
        state=resolved.state,
        previous_chapters=previous_chapters,
        total_chapters=int(total_chapters) if total_chapters else None,
        ancestor_outline_summaries=tuple(
            {
                "display_index": row.display_index,
                "title": row.title,
                "summary": row.summary,
            }
            for row in ancestor_outline_summaries
        ),
        context_manifest=resolved.manifest,
        context_hash=resolved.context_hash,
        generation_parameters=generation_parameters,
        source_manifest=deepcopy(source),
        source_hash=source_digest,
    )


def create_story_path_chapter_revision(
    db: Session,
    *,
    context: ChapterGenerationContext,
    content: str,
    user_id: int,
    task_id: str | None = None,
) -> ChapterRevision:
    """Persist a reviewable ChapterRevision without changing a PathChapter Head."""

    if not isinstance(content, str) or not content.strip():
        raise _source_failure("chapter.content_empty", "章节正文不能为空")

    target = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == context.path_chapter_id,
            StoryPathChapter.story_path_id == context.story_path_id,
            StoryPathChapter.chapter_slot_id == context.chapter_slot_id,
            StoryPathChapter.status == "active",
        )
        .with_for_update()
        .one_or_none()
    )
    slot = (
        db.query(ChapterSlot)
        .filter(
            ChapterSlot.id == context.chapter_slot_id,
            ChapterSlot.project_id == context.project_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if not target or not slot:
        raise _source_failure(
            "chapter.target_changed",
            "章节生成目标已变化，请重新发起生成",
        )

    if task_id:
        persisted = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.generation_task_id == task_id)
            .one_or_none()
        )
        if persisted:
            if (
                persisted.chapter_slot_id != context.chapter_slot_id
                or persisted.context_hash != context.context_hash
            ):
                raise _source_failure(
                    "chapter.task_result_mismatch",
                    "章节任务已绑定到不一致的 Revision",
                )
            return persisted

    digest = content_hash(content)
    existing = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.chapter_slot_id == context.chapter_slot_id,
            ChapterRevision.context_hash == context.context_hash,
            ChapterRevision.content_hash == digest,
        )
        .one_or_none()
    )
    if existing:
        return existing

    if context.parent_revision_id:
        parent = (
            db.query(ChapterRevision)
            .filter(ChapterRevision.id == context.parent_revision_id)
            .one_or_none()
        )
        if not parent or parent.chapter_slot_id != context.chapter_slot_id:
            raise _source_failure(
                "chapter.parent_mismatch",
                "章节编辑父 Revision 不属于目标 ChapterSlot",
            )

    revision_no = int(
        db.query(func.max(ChapterRevision.revision_no))
        .filter(ChapterRevision.chapter_slot_id == context.chapter_slot_id)
        .scalar()
        or 0
    ) + 1
    revision = ChapterRevision(
        project_id=context.project_id,
        chapter_slot_id=context.chapter_slot_id,
        created_for_story_path_id=context.story_path_id,
        # Kept only as a rollback bridge until the R8 legacy-column removal.
        chapter_index=context.display_index,
        parent_revision_id=context.parent_revision_id,
        bible_revision_id=context.bible_revision_id,
        outline_revision_id=context.outline_revision_id,
        state_snapshot_id=context.state_snapshot_id,
        revision_no=revision_no,
        source_hash=context.source_hash,
        context_manifest=deepcopy(context.context_manifest),
        context_hash=context.context_hash,
        content_hash=digest,
        content=content,
        status="ready",
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    return revision


__all__ = [
    "CHAPTER_GENERATION_COST",
    "CHAPTER_SOURCE_SCHEMA_VERSION",
    "ChapterGenerationContext",
    "build_chapter_generation_source",
    "create_manual_story_path_chapter_revision",
    "create_story_path_chapter_generation_task",
    "create_story_path_chapter_revision",
    "normalize_chapter_generation_parameters",
    "resolve_chapter_generation_source",
]
