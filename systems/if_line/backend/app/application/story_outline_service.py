"""StoryPath-scoped outline revisions and path chapter reconciliation."""
from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash, content_hash_js
from app.application.revision_head_service import (
    compare_and_swap_outline_head,
    get_or_create_story_path_outline_head,
    raise_head_version_conflict,
)
from app.application.task_errors import NonRetryableTaskError
from app.core.errors import AppError
from app.core.story_outline import (
    MAX_OUTLINE_CHAPTER_COUNT,
    validate_outline_chapter_count,
    validate_outline_instructions,
)
from app.models import Project
from app.models_v2 import (
    ChapterSlot,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    utcnow,
)


logger = logging.getLogger("story_outline")

OUTLINE_SOURCE_SCHEMA_VERSION = "story-path-outline-source-v1"
_SELECTABLE_OUTLINE_STATUSES = frozenset({"ready", "complete"})
_CURRENT_OUTLINE_HEAD = object()


@dataclass(frozen=True)
class OutlineGenerationContext:
    project_id: int
    story_path_id: str
    bible_revision_id: str
    parent_revision_id: str | None
    chapter_count: int
    instructions: str | None
    pace: str
    story_bible: dict[str, Any]
    path_chapter_refs: tuple[dict[str, Any], ...]
    source_manifest: dict[str, Any]


@dataclass(frozen=True)
class OutlineActivationResult:
    revision: OutlineRevision
    head: StoryPathOutlineHead
    path_chapters: tuple[StoryPathChapter, ...]


def _validation_error(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _load_active_path(
    db: Session,
    story_path_id: str,
    *,
    for_update: bool = False,
) -> StoryPath:
    query = db.query(StoryPath).filter(StoryPath.id == story_path_id)
    if for_update:
        query = query.with_for_update()
    path = query.one_or_none()
    if not path:
        raise HTTPException(status_code=404, detail="StoryPath does not exist")
    if path.status != "active":
        raise HTTPException(status_code=409, detail="Archived StoryPath cannot be edited")
    return path


def _validated_count(value: object) -> int:
    try:
        return validate_outline_chapter_count(value)
    except ValueError as exc:
        raise _validation_error(str(exc)) from exc


def _validated_instructions(value: object) -> str | None:
    try:
        return validate_outline_instructions(value)
    except ValueError as exc:
        raise _validation_error(str(exc)) from exc


def _current_path_chapter_refs(
    db: Session,
    *,
    path: StoryPath,
    outline_head: StoryPathOutlineHead | None,
) -> list[dict[str, Any]]:
    if not outline_head or not outline_head.current_revision_id:
        placements = (
            db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.status == "active",
            )
            .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
            .all()
        )
        return [
            {
                "story_path_chapter_id": placement.id,
                "display_index": placement.display_index,
            }
            for placement in placements
        ]

    current = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == outline_head.current_revision_id,
            OutlineRevision.story_path_id == path.id,
        )
        .one_or_none()
    )
    if not current:
        raise HTTPException(status_code=409, detail="StoryPath Outline Head is invalid")
    rows = (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == current.id)
        .order_by(OutlineChapter.display_index)
        .all()
    )
    if not rows or any(not row.story_path_chapter_id for row in rows):
        raise HTTPException(
            status_code=409,
            detail="Current Outline has not been reconciled with stable PathChapter IDs",
        )
    return [
        {
            "story_path_chapter_id": row.story_path_chapter_id,
            "display_index": row.display_index,
        }
        for row in rows
    ]


def build_outline_generation_source(
    db: Session,
    *,
    story_path_id: str,
    chapter_count: int,
    instructions: str | None = None,
    bible_revision_id: str | None = None,
) -> dict[str, Any]:
    """Freeze all mutable inputs needed by an outline generation task."""

    count = _validated_count(chapter_count)
    instruction_text = _validated_instructions(instructions)
    path = _load_active_path(db, story_path_id, for_update=True)
    project = db.query(Project).filter(Project.id == path.project_id).one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project does not exist")
    if bible_revision_id is not None:
        # 草稿物化锚点：绕过 head，用指定 bible 修订作为生成上下文。
        bible = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == bible_revision_id,
                StoryBibleRevision.project_id == path.project_id,
            )
            .one_or_none()
        )
        if not bible or bible.status != "complete":
            raise HTTPException(
                status_code=422,
                detail="Bible 修订锚点不存在、不属于该项目或不可用",
            )
    else:
        content_head = (
            db.query(ProjectContentHead)
            .filter(ProjectContentHead.project_id == path.project_id)
            .with_for_update()
            .one_or_none()
        )
        if not content_head or not content_head.current_bible_revision_id:
            raise HTTPException(
                status_code=409, detail="Activate a Story Bible revision first"
            )
        bible = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == content_head.current_bible_revision_id,
                StoryBibleRevision.project_id == path.project_id,
            )
            .one_or_none()
        )
        if not bible:
            raise HTTPException(status_code=409, detail="Current Story Bible Head is invalid")
    outline_head = (
        db.query(StoryPathOutlineHead)
        .filter(StoryPathOutlineHead.story_path_id == path.id)
        .one_or_none()
    )
    source = {
        "schema_version": OUTLINE_SOURCE_SCHEMA_VERSION,
        "project_id": path.project_id,
        "story_path_id": path.id,
        "bible_revision_id": bible.id,
        "bible_content_hash": bible.content_hash,
        "parent_revision_id": outline_head.current_revision_id if outline_head else None,
        "path_chapters": _current_path_chapter_refs(
            db,
            path=path,
            outline_head=outline_head,
        ),
        "chapter_count": count,
        "instructions": instruction_text,
        "pace": project.pace or "medium",
    }
    return {
        "outline_source": source,
        "outline_source_hash": content_hash(source),
    }


def _source_failure(code: str, detail: str) -> NonRetryableTaskError:
    return NonRetryableTaskError(code=code, safe_detail=detail)


def resolve_outline_generation_source(
    db: Session,
    *,
    task_project_id: int | None,
    source_refs: dict[str, Any],
) -> OutlineGenerationContext:
    """Validate a frozen task source without consulting mutable Heads."""

    source = source_refs.get("outline_source")
    source_digest = source_refs.get("outline_source_hash")
    if (
        not isinstance(source, dict)
        or not isinstance(source_digest, str)
        or content_hash(source) != source_digest
    ):
        raise _source_failure("outline.source_invalid", "Outline generation source is invalid")
    if source.get("schema_version") != OUTLINE_SOURCE_SCHEMA_VERSION:
        raise _source_failure("outline.source_schema", "Outline generation source schema is unsupported")

    project_id = source.get("project_id")
    story_path_id = source.get("story_path_id")
    bible_revision_id = source.get("bible_revision_id")
    bible_content_hash = source.get("bible_content_hash")
    parent_revision_id = source.get("parent_revision_id")
    if (
        isinstance(project_id, bool)
        or not isinstance(project_id, int)
        or task_project_id != project_id
        or not isinstance(story_path_id, str)
        or not story_path_id
        or not isinstance(bible_revision_id, str)
        or not bible_revision_id
        or not isinstance(bible_content_hash, str)
        or (parent_revision_id is not None and not isinstance(parent_revision_id, str))
    ):
        raise _source_failure("outline.source_invalid", "Outline generation IDs are invalid")
    try:
        chapter_count = validate_outline_chapter_count(source.get("chapter_count"))
        instructions = validate_outline_instructions(source.get("instructions"))
    except ValueError as exc:
        raise _source_failure("outline.request_invalid", str(exc)) from exc
    pace = source.get("pace")
    if not isinstance(pace, str) or not pace:
        raise _source_failure("outline.source_invalid", "Outline pace guidance is invalid")

    path = (
        db.query(StoryPath)
        .filter(StoryPath.id == story_path_id, StoryPath.project_id == project_id)
        .one_or_none()
    )
    if not path:
        raise _source_failure("story_path.missing", "StoryPath no longer exists")
    if path.status != "active":
        raise _source_failure("story_path.inactive", "StoryPath is archived")
    bible = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == bible_revision_id,
            StoryBibleRevision.project_id == project_id,
        )
        .one_or_none()
    )
    if not bible or bible.content_hash != bible_content_hash:
        raise _source_failure("bible_revision.missing", "Frozen Story Bible revision is unavailable")
    if parent_revision_id is not None:
        parent = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.id == parent_revision_id,
                OutlineRevision.story_path_id == story_path_id,
            )
            .one_or_none()
        )
        if not parent:
            raise _source_failure("outline.parent_missing", "Frozen parent Outline revision is unavailable")

    raw_refs = source.get("path_chapters")
    if not isinstance(raw_refs, list):
        raise _source_failure("outline.source_invalid", "Frozen PathChapter manifest is invalid")
    refs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_indexes: set[int] = set()
    for raw in raw_refs:
        if not isinstance(raw, dict):
            raise _source_failure("outline.source_invalid", "Frozen PathChapter entry is invalid")
        path_chapter_id = raw.get("story_path_chapter_id")
        display_index = raw.get("display_index")
        if (
            not isinstance(path_chapter_id, str)
            or not path_chapter_id
            or isinstance(display_index, bool)
            or not isinstance(display_index, int)
            or display_index < 1
            or path_chapter_id in seen_ids
            or display_index in seen_indexes
        ):
            raise _source_failure("outline.source_invalid", "Frozen PathChapter entry is invalid")
        seen_ids.add(path_chapter_id)
        seen_indexes.add(display_index)
        refs.append(
            {
                "story_path_chapter_id": path_chapter_id,
                "display_index": display_index,
            }
        )
    if seen_ids:
        owned_ids = {
            row.id
            for row in db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == story_path_id,
                StoryPathChapter.id.in_(seen_ids),
                StoryPathChapter.status == "active",
            )
            .all()
        }
        if owned_ids != seen_ids:
            raise _source_failure("outline.source_invalid", "Frozen PathChapter belongs to another path")

    return OutlineGenerationContext(
        project_id=project_id,
        story_path_id=story_path_id,
        bible_revision_id=bible_revision_id,
        parent_revision_id=parent_revision_id,
        chapter_count=chapter_count,
        instructions=instructions,
        pace=pace,
        story_bible=deepcopy(bible.content_json or {}),
        path_chapter_refs=tuple(refs),
        source_manifest=deepcopy(source),
    )


def normalize_outline_chapters(
    chapters: list[dict[str, Any]],
    *,
    expected_count: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(chapters, list) or not chapters:
        raise _validation_error("Outline must contain at least one chapter")
    if len(chapters) > MAX_OUTLINE_CHAPTER_COUNT:
        raise _validation_error(
            f"Outline cannot contain more than {MAX_OUTLINE_CHAPTER_COUNT} chapters"
        )
    normalized: list[dict[str, Any]] = []
    for item in chapters:
        if not isinstance(item, dict):
            raise _validation_error("Each outline chapter must be an object")
        display_index = item.get("display_index")
        if (
            isinstance(display_index, bool)
            or not isinstance(display_index, int)
            or display_index < 1
        ):
            raise _validation_error("display_index must be a positive integer")
        title = item.get("title")
        summary = item.get("summary")
        if not isinstance(title, str) or not isinstance(summary, str):
            raise _validation_error("title and summary are required strings")
        path_chapter_id = item.get("story_path_chapter_id")
        if path_chapter_id is not None and (
            not isinstance(path_chapter_id, str) or not path_chapter_id
        ):
            raise _validation_error("story_path_chapter_id must be a UUID string or null")
        characters = item.get("characters") or []
        visual_keywords = item.get("visual_keywords") or []
        if not isinstance(characters, list) or not all(isinstance(value, str) for value in characters):
            raise _validation_error("characters must be a list of strings")
        if not isinstance(visual_keywords, list) or not all(
            isinstance(value, str) for value in visual_keywords
        ):
            raise _validation_error("visual_keywords must be a list of strings")
        normalized.append(
            {
                "story_path_chapter_id": path_chapter_id,
                "display_index": display_index,
                "title": title,
                "summary": summary,
                "conflict": item.get("conflict"),
                "characters": list(characters),
                "scene": item.get("scene"),
                "emotion": item.get("emotion"),
                "visual_keywords": list(visual_keywords),
            }
        )

    normalized.sort(key=lambda item: item["display_index"])
    indexes = [item["display_index"] for item in normalized]
    if indexes != list(range(1, len(normalized) + 1)):
        raise _validation_error("display_index must form a contiguous sequence starting at 1")
    path_chapter_ids = [
        item["story_path_chapter_id"]
        for item in normalized
        if item["story_path_chapter_id"] is not None
    ]
    if len(path_chapter_ids) != len(set(path_chapter_ids)):
        raise _validation_error("story_path_chapter_id cannot be repeated in one Outline")
    if expected_count is not None and len(normalized) != _validated_count(expected_count):
        raise _validation_error(
            f"Provider returned {len(normalized)} chapters; expected {expected_count}"
        )
    return normalized


def create_story_path_outline_revision(
    db: Session,
    *,
    story_path_id: str,
    bible_revision_id: str,
    chapters: list[dict[str, Any]],
    user_id: int,
    parent_revision_id: str | None | object = _CURRENT_OUTLINE_HEAD,
    source_manifest: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> OutlineRevision:
    """Create an immutable, unselected OutlineRevision for one StoryPath."""

    path = _load_active_path(db, story_path_id, for_update=True)
    bible = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == bible_revision_id,
            StoryBibleRevision.project_id == path.project_id,
        )
        .one_or_none()
    )
    if not bible:
        raise HTTPException(status_code=404, detail="Story Bible revision does not exist")
    outline_head = (
        db.query(StoryPathOutlineHead)
        .filter(StoryPathOutlineHead.story_path_id == path.id)
        .one_or_none()
    )
    parent_id = (
        outline_head.current_revision_id
        if parent_revision_id is _CURRENT_OUTLINE_HEAD and outline_head
        else None if parent_revision_id is _CURRENT_OUTLINE_HEAD else parent_revision_id
    )
    if parent_id is not None:
        parent = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.id == parent_id,
                OutlineRevision.story_path_id == path.id,
            )
            .one_or_none()
        )
        if not parent:
            raise HTTPException(
                status_code=409,
                detail="Parent Outline revision does not belong to this StoryPath",
            )

    normalized = normalize_outline_chapters(chapters)
    explicit_ids = {
        item["story_path_chapter_id"]
        for item in normalized
        if item["story_path_chapter_id"] is not None
    }
    if explicit_ids:
        owned_ids = {
            row.id
            for row in db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.id.in_(explicit_ids),
            )
            .all()
        }
        if owned_ids != explicit_ids:
            raise HTTPException(
                status_code=409,
                detail="Outline references a PathChapter from another StoryPath",
            )

    if source_manifest is not None and (
        source_manifest.get("project_id") != path.project_id
        or source_manifest.get("story_path_id") != path.id
        or source_manifest.get("bible_revision_id") != bible.id
        or source_manifest.get("chapter_count") != len(normalized)
    ):
        raise HTTPException(
            status_code=409,
            detail="Outline source manifest does not match the revision inputs",
        )
    # source 是内容寻址去重键：parent 是回滚溯源而非内容语义，不参与
    # （同脚本去重的既有约定——否则同内容从不同 parent 物化永远命中不了）。
    source = deepcopy(source_manifest) if source_manifest is not None else {
        "schema_version": OUTLINE_SOURCE_SCHEMA_VERSION,
        "project_id": path.project_id,
        "story_path_id": path.id,
        "bible_revision_id": bible.id,
        "bible_content_hash": bible.content_hash,
        "chapter_count": len(normalized),
    }
    source.pop("parent_revision_id", None)
    source_digest = content_hash(source)
    outline_digest = content_hash(normalized)
    existing = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.story_path_id == path.id,
            OutlineRevision.source_hash == source_digest,
            OutlineRevision.content_hash == outline_digest,
        )
        .one_or_none()
    )
    if existing is None:
        # 存量修订的 source_hash 可能是含 parent 的旧口径；同 bible 同内容
        # （逐章 hash，含 JS 归一化宽容）即视为同一修订，保证物化幂等。
        request_chapter_hashes = [
            {content_hash(item), content_hash_js(item)} for item in normalized
        ]
        candidates = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.story_path_id == path.id,
                OutlineRevision.bible_revision_id == bible.id,
            )
            .order_by(OutlineRevision.revision_no)
            .all()
        )
        for candidate in candidates:
            rows = (
                db.query(OutlineChapter)
                .filter(OutlineChapter.outline_revision_id == candidate.id)
                .order_by(OutlineChapter.display_index, OutlineChapter.id)
                .all()
            )
            if len(rows) != len(request_chapter_hashes):
                continue
            if all(row.content_hash in hashes for row, hashes in zip(rows, request_chapter_hashes)):
                existing = candidate
                break
    if existing:
        _auto_adopt_first_outline_revision(
            db, path=path, revision=existing, outline_head=outline_head
        )
        return existing

    revision_no = int(
        db.query(func.max(OutlineRevision.revision_no))
        .filter(OutlineRevision.story_path_id == path.id)
        .scalar()
        or 0
    ) + 1
    revision = OutlineRevision(
        project_id=path.project_id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        parent_revision_id=parent_id,
        revision_no=revision_no,
        source_hash=source_digest,
        content_hash=outline_digest,
        status="ready",
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    for item in normalized:
        db.add(
            OutlineChapter(
                outline_revision_id=revision.id,
                # Kept only as a rollback bridge until the R8 legacy-column removal.
                chapter_index=item["display_index"],
                display_index=item["display_index"],
                story_path_chapter_id=item["story_path_chapter_id"],
                title=item["title"],
                summary=item["summary"],
                conflict=item["conflict"],
                characters=item["characters"],
                scene=item["scene"],
                emotion=item["emotion"],
                visual_keywords=item["visual_keywords"],
                content_hash=content_hash(item),
            )
        )
    db.flush()
    _auto_adopt_first_outline_revision(
        db, path=path, revision=revision, outline_head=outline_head
    )
    return revision


def _auto_adopt_first_outline_revision(
    db: Session,
    *,
    path: StoryPath,
    revision: OutlineRevision,
    outline_head: StoryPathOutlineHead | None,
) -> None:
    """Head 为空时自动采用首个 Outline 修订（含 placement 对齐）。

    head 已有值时维持审阅制不动；前置条件不满足（Bible head 未设或
    不一致、CAS 冲突、修订不可选）时记 warning 静默跳过——修订照常
    落盘，激活留给显式审阅或固化发布。
    """
    if outline_head is not None and outline_head.current_revision_id is not None:
        return
    try:
        activate_story_path_outline_head(
            db,
            story_path_id=path.id,
            revision_id=revision.id,
            expected_lock_version=(
                1 if outline_head is None else outline_head.lock_version
            ),
        )
    except (HTTPException, AppError) as error:
        logger.warning(
            "outline auto-adopt skipped: story_path_id=%s revision_id=%s reason=%s",
            path.id,
            revision.id,
            getattr(error, "detail", None) or getattr(error, "message", None) or error,
        )


def _revision_rows(db: Session, revision_id: str) -> list[OutlineChapter]:
    return (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == revision_id)
        .order_by(OutlineChapter.display_index)
        .all()
    )


def activate_story_path_outline_head(
    db: Session,
    *,
    story_path_id: str,
    revision_id: str,
    expected_lock_version: int,
) -> OutlineActivationResult:
    """Select an Outline and reconcile placements under one Head CAS."""

    path = (
        db.query(StoryPath)
        .filter(StoryPath.id == story_path_id)
        .with_for_update()
        .one_or_none()
    )
    if not path:
        raise HTTPException(status_code=404, detail="StoryPath does not exist")
    if path.status != "active":
        raise HTTPException(status_code=409, detail="Archived StoryPath cannot be edited")
    revision = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == revision_id,
            OutlineRevision.story_path_id == path.id,
            OutlineRevision.project_id == path.project_id,
        )
        .one_or_none()
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Outline revision does not exist")
    if revision.status not in _SELECTABLE_OUTLINE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Outline revision is not ready for review selection",
        )
    content_head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == path.project_id)
        .with_for_update()
        .one_or_none()
    )
    if not content_head or content_head.current_bible_revision_id != revision.bible_revision_id:
        raise HTTPException(
            status_code=409,
            detail="Activate the Story Bible revision used by this Outline first",
        )
    rows = _revision_rows(db, revision.id)
    normalize_outline_chapters(
        [
            {
                "story_path_chapter_id": row.story_path_chapter_id,
                "display_index": row.display_index,
                "title": row.title or "",
                "summary": row.summary or "",
                "conflict": row.conflict,
                "characters": row.characters or [],
                "scene": row.scene,
                "emotion": row.emotion,
                "visual_keywords": row.visual_keywords or [],
            }
            for row in rows
        ]
    )
    head = get_or_create_story_path_outline_head(
        db,
        path.id,
        for_update=True,
    )
    if head.lock_version != expected_lock_version:
        raise_head_version_conflict(head)
    if head.current_revision_id == revision.id:
        if any(not row.story_path_chapter_id for row in rows):
            raise HTTPException(status_code=409, detail="Selected Outline has incomplete PathChapter bindings")
        selected_by_id = {
            placement.id: placement
            for placement in db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.id.in_([row.story_path_chapter_id for row in rows]),
                StoryPathChapter.status == "active",
            )
            .all()
        }
        selected = [selected_by_id.get(row.story_path_chapter_id) for row in rows]
        if any(placement is None for placement in selected):
            raise HTTPException(status_code=409, detail="Selected Outline has invalid PathChapter bindings")
        previous_id: str | None = None
        for row, placement in zip(rows, selected):
            if (
                placement.display_index != row.display_index
                or placement.predecessor_path_chapter_id != previous_id
            ):
                raise HTTPException(status_code=409, detail="Selected Outline topology is inconsistent")
            previous_id = placement.id
        compare_and_swap_outline_head(
            db,
            head=head,
            revision_id=revision.id,
            expected_lock_version=expected_lock_version,
        )
        return OutlineActivationResult(revision, head, tuple(selected))

    placements = (
        db.query(StoryPathChapter)
        .filter(StoryPathChapter.story_path_id == path.id)
        .with_for_update()
        .all()
    )
    placement_by_id = {placement.id: placement for placement in placements}
    selected: list[StoryPathChapter | None] = []
    selected_ids: set[str] = set()
    for row in rows:
        placement: StoryPathChapter | None = None
        if row.story_path_chapter_id:
            placement = placement_by_id.get(row.story_path_chapter_id)
            if not placement:
                raise HTTPException(
                    status_code=409,
                    detail="Outline references a PathChapter from another StoryPath",
                )
        if placement:
            if placement.id in selected_ids:
                raise HTTPException(status_code=409, detail="Outline repeats a PathChapter")
            selected_ids.add(placement.id)
        selected.append(placement)

    current_ids: set[str] = set()
    if head.current_revision_id:
        current_revision = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.id == head.current_revision_id,
                OutlineRevision.story_path_id == path.id,
            )
            .one_or_none()
        )
        if not current_revision:
            raise HTTPException(status_code=409, detail="StoryPath Outline Head is invalid")
        current_rows = _revision_rows(db, current_revision.id)
        if any(not row.story_path_chapter_id for row in current_rows):
            raise HTTPException(status_code=409, detail="Current Outline has incomplete PathChapter bindings")
        current_ids = {row.story_path_chapter_id for row in current_rows}

    missing_current_ids = current_ids - placement_by_id.keys()
    if missing_current_ids:
        raise HTTPException(status_code=409, detail="Current Outline has invalid PathChapter bindings")
    current_placements = [placement_by_id[path_chapter_id] for path_chapter_id in current_ids]
    if any(placement.status != "active" for placement in current_placements):
        raise HTTPException(
            status_code=409,
            detail="Current Outline references a detached PathChapter",
        )
    removed = [
        placement
        for placement in placements
        if placement.status == "active" and placement.id not in selected_ids
    ]

    selected_slot_ids = {
        placement.chapter_slot_id for placement in selected if placement is not None
    }
    if selected_slot_ids:
        owned_slot_ids = {
            slot.id
            for slot in db.query(ChapterSlot)
            .filter(
                ChapterSlot.id.in_(selected_slot_ids),
                ChapterSlot.project_id == path.project_id,
            )
            .all()
        }
        if owned_slot_ids != selected_slot_ids:
            raise HTTPException(status_code=409, detail="PathChapter has an invalid ChapterSlot")

    desired_by_id = {
        placement.id: rows[position].display_index
        for position, placement in enumerate(selected)
        if placement is not None
    }
    affected: dict[str, StoryPathChapter] = {}
    for placement in placements:
        desired_index = desired_by_id.get(placement.id)
        if (
            placement.status == "active"
            and desired_index is not None
            and desired_index != placement.display_index
        ):
            affected[placement.id] = placement

    original_state = {
        placement.id: (
            placement.display_index,
            placement.predecessor_path_chapter_id,
            placement.status,
            placement.detached_at,
            placement.detached_by_outline_revision_id,
        )
        for placement in placements
    }
    topology_changed = False
    with db.begin_nested():
        compare_and_swap_outline_head(
            db,
            head=head,
            revision_id=revision.id,
            expected_lock_version=expected_lock_version,
        )
        detached_at = utcnow()
        for placement in removed:
            placement.predecessor_path_chapter_id = None
            placement.status = "detached"
            placement.detached_at = detached_at
            placement.detached_by_outline_revision_id = revision.id
        if removed:
            db.flush()

        if affected:
            temporary_base = max(
                [placement.display_index for placement in placements] + [len(rows)]
            ) + len(placements) + len(rows) + 1
            for offset, placement in enumerate(sorted(affected.values(), key=lambda item: item.id)):
                placement.display_index = temporary_base + offset
                placement.predecessor_path_chapter_id = None
            db.flush()

        previous: StoryPathChapter | None = None
        resolved: list[StoryPathChapter] = []
        for position, row in enumerate(rows):
            placement = selected[position]
            if placement is None:
                slot = ChapterSlot(
                    project_id=path.project_id,
                    created_for_story_path_id=path.id,
                )
                db.add(slot)
                db.flush()
                placement = StoryPathChapter(
                    story_path_id=path.id,
                    chapter_slot_id=slot.id,
                    display_index=row.display_index,
                    predecessor_path_chapter_id=previous.id if previous else None,
                )
                db.add(placement)
                db.flush()
                topology_changed = True
            else:
                placement.status = "active"
                placement.detached_at = None
                placement.detached_by_outline_revision_id = None
                placement.display_index = row.display_index
                placement.predecessor_path_chapter_id = previous.id if previous else None
            row.story_path_chapter_id = placement.id
            resolved.append(placement)
            previous = placement

        for placement in placements:
            before = original_state[placement.id]
            after = (
                placement.display_index,
                placement.predecessor_path_chapter_id,
                placement.status,
                placement.detached_at,
                placement.detached_by_outline_revision_id,
            )
            if before != after:
                placement.lock_version += 1
                topology_changed = True

        if topology_changed:
            path.lock_version += 1
        db.flush()

    return OutlineActivationResult(revision, head, tuple(resolved))


def activate_story_path_outline_revision(
    db: Session,
    *,
    story_path_id: str,
    revision_id: str,
) -> OutlineActivationResult:
    """Legacy bridge; target routers call activate_story_path_outline_head."""

    head = get_or_create_story_path_outline_head(
        db,
        story_path_id,
        for_update=True,
    )
    return activate_story_path_outline_head(
        db,
        story_path_id=story_path_id,
        revision_id=revision_id,
        expected_lock_version=head.lock_version,
    )
