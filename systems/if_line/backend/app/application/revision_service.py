from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash, content_hash_js
from app.application.revision_head_service import (
    RevisionHeadValue,
    compare_and_swap_bible_head,
)
from app.models import Project
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StateSnapshot,
    StoryBibleRevision,
    VNGraphHead,
    VNGraphRevision,
)


_CURRENT_BIBLE_HEAD = object()


@dataclass(frozen=True)
class BibleHeadActivation:
    revision: StoryBibleRevision
    head: RevisionHeadValue


def build_bible_generation_source(
    project: Project,
    *,
    parent_revision_id: str | None,
) -> dict[str, Any]:
    snapshot = {
        "project_id": project.id,
        "title": project.title,
        "characters": deepcopy(project.characters or []),
        "story_start": project.story_start,
        "story_end": project.story_end,
        "style": project.style or "",
        "pace": project.pace or "medium",
        "extra_requirements": project.extra_requirements or "",
        "source_work": getattr(project, "source_work", "") or "",
    }
    return {
        "parent_revision_id": parent_revision_id,
        "project_snapshot": snapshot,
        "project_source_hash": content_hash(snapshot),
        "project_updated_at": (
            project.updated_at.isoformat() if project.updated_at else None
        ),
    }


def get_or_create_content_head(db: Session, project_id: int) -> ProjectContentHead:
    head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .with_for_update()
        .first()
    )
    if not head:
        # Only the first v2 write needs the legacy Project row as a creation
        # mutex. Re-check after taking it so concurrent first writers cannot
        # both insert the one-to-one content head.
        project = (
            db.query(Project)
            .filter(Project.id == project_id)
            .with_for_update()
            .first()
        )
        if not project:
            raise HTTPException(status_code=404, detail="项目不存在")
        head = (
            db.query(ProjectContentHead)
            .filter(ProjectContentHead.project_id == project_id)
            .with_for_update()
            .first()
        )
    if not head:
        head = ProjectContentHead(project_id=project_id)
        db.add(head)
        db.flush()
    return head


def _next_revision_no(db: Session, model, project_id: int, *, chapter_index: int | None = None) -> int:
    query = db.query(func.max(model.revision_no)).filter(model.project_id == project_id)
    if chapter_index is not None:
        query = query.filter(model.chapter_index == chapter_index)
    return int(query.scalar() or 0) + 1


def create_bible_revision(
    db: Session,
    *,
    project_id: int,
    content: dict[str, Any],
    source: dict[str, Any],
    user_id: int,
    task_id: str | None = None,
    activate: bool = True,
    parent_revision_id: str | None | object = _CURRENT_BIBLE_HEAD,
) -> StoryBibleRevision:
    digest = content_hash(content)
    existing = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.project_id == project_id, StoryBibleRevision.content_hash == digest)
        .first()
    )
    if existing is None:
        # 客户端物化内容经 JS 往返会归一化整值浮点（80.0→80），hash 漂移；
        # 内容按 JS 规范化语义相等时复用既有修订，避免物化出重复链分叉。
        js_digest = content_hash_js(content)
        existing = next(
            (
                revision
                for revision in db.query(StoryBibleRevision)
                .filter(StoryBibleRevision.project_id == project_id)
                .order_by(StoryBibleRevision.revision_no)
                .all()
                if content_hash_js(revision.content_json) == js_digest
            ),
            None,
        )
    head = get_or_create_content_head(db, project_id)
    parent_id = (
        head.current_bible_revision_id
        if parent_revision_id is _CURRENT_BIBLE_HEAD
        else parent_revision_id
    )
    if parent_id is not None:
        parent = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == parent_id,
                StoryBibleRevision.project_id == project_id,
            )
            .one_or_none()
        )
        if not parent:
            raise HTTPException(
                status_code=409,
                detail="父 Bible revision 不存在或不属于项目",
            )
    if existing:
        if activate:
            head.current_bible_revision_id = existing.id
            head.lock_version += 1
        return existing

    revision = StoryBibleRevision(
        project_id=project_id,
        parent_revision_id=parent_id,
        revision_no=_next_revision_no(db, StoryBibleRevision, project_id),
        source_hash=content_hash(source),
        content_hash=digest,
        content_json=content,
        status="complete",
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    if activate:
        head.current_bible_revision_id = revision.id
        head.lock_version += 1
    return revision


def activate_bible_revision_head(
    db: Session,
    *,
    project_id: int,
    revision_id: str,
    expected_lock_version: int,
) -> BibleHeadActivation:
    """Select a reviewed Bible revision with an atomic lock-version CAS."""

    revision = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.id == revision_id, StoryBibleRevision.project_id == project_id)
        .one_or_none()
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Bible revision 不存在")
    head = get_or_create_content_head(db, project_id)
    selected = compare_and_swap_bible_head(
        db,
        head=head,
        revision_id=revision.id,
        expected_lock_version=expected_lock_version,
    )
    return BibleHeadActivation(revision=revision, head=selected)


def activate_bible_revision(db: Session, project_id: int, revision_id: str) -> StoryBibleRevision:
    """Legacy activation bridge; target routers call activate_bible_revision_head."""

    head = get_or_create_content_head(db, project_id)
    result = activate_bible_revision_head(
        db,
        project_id=project_id,
        revision_id=revision_id,
        expected_lock_version=head.lock_version,
    )
    project = db.query(Project).filter(Project.id == project_id).one()
    if (project.status or "draft_input") == "draft_input":
        project.status = "bible_generated"
    return result.revision


def create_outline_revision(
    db: Session,
    *,
    project_id: int,
    chapters: list[dict[str, Any]],
    user_id: int,
    bible_revision_id: str | None = None,
    task_id: str | None = None,
    activate: bool = True,
) -> OutlineRevision:
    head = get_or_create_content_head(db, project_id)
    bible_id = bible_revision_id or head.current_bible_revision_id
    if not bible_id:
        raise HTTPException(status_code=409, detail="请先创建或激活 Story Bible revision")
    bible = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.id == bible_id, StoryBibleRevision.project_id == project_id)
        .first()
    )
    if not bible:
        raise HTTPException(status_code=404, detail="Story Bible revision 不存在")
    if not chapters:
        raise HTTPException(status_code=422, detail="大纲至少包含一章")
    indexes = [int(item["chapter_index"]) for item in chapters]
    if len(indexes) != len(set(indexes)) or min(indexes) < 1:
        raise HTTPException(status_code=422, detail="chapter_index 必须是互不重复的正整数")

    normalized = sorted(chapters, key=lambda item: int(item["chapter_index"]))
    digest = content_hash(normalized)
    source_digest = content_hash(
        {"bible_revision_id": bible_id, "bible_hash": bible.content_hash}
    )
    existing = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.project_id == project_id,
            OutlineRevision.source_hash == source_digest,
            OutlineRevision.content_hash == digest,
        )
        .first()
    )
    if existing is None:
        # 同 bible 的客户端物化大纲：JS 数字归一化会让 hash 漂移。
        # 每章行存有落库时的 content_hash(item)；与请求每章的原始/归一化 hash 对位比较。
        request_hashes = [{content_hash(item), content_hash_js(item)} for item in normalized]
        candidates = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.project_id == project_id,
                OutlineRevision.source_hash == source_digest,
            )
            .order_by(OutlineRevision.revision_no)
            .all()
        )
        for candidate in candidates:
            rows = (
                db.query(OutlineChapter)
                .filter(OutlineChapter.outline_revision_id == candidate.id)
                .order_by(OutlineChapter.chapter_index)
                .all()
            )
            if len(rows) != len(request_hashes):
                continue
            if all(row.content_hash in hashes for row, hashes in zip(rows, request_hashes)):
                existing = candidate
                break
    if existing:
        if activate:
            head.current_outline_revision_id = existing.id
            head.lock_version += 1
        return existing

    revision = OutlineRevision(
        project_id=project_id,
        bible_revision_id=bible_id,
        parent_revision_id=head.current_outline_revision_id,
        revision_no=_next_revision_no(db, OutlineRevision, project_id),
        source_hash=source_digest,
        content_hash=digest,
        status="draft",
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    for item in normalized:
        payload = dict(item)
        db.add(
            OutlineChapter(
                outline_revision_id=revision.id,
                chapter_index=int(payload.pop("chapter_index")),
                title=payload.get("title"),
                summary=payload.get("summary"),
                conflict=payload.get("conflict"),
                characters=payload.get("characters") or [],
                scene=payload.get("scene"),
                emotion=payload.get("emotion"),
                visual_keywords=payload.get("visual_keywords") or [],
                content_hash=content_hash(item),
            )
        )
    if activate:
        head.current_outline_revision_id = revision.id
        head.lock_version += 1
    return revision


def activate_outline_revision(db: Session, project_id: int, revision_id: str, *, approve: bool) -> OutlineRevision:
    revision = (
        db.query(OutlineRevision)
        .filter(OutlineRevision.id == revision_id, OutlineRevision.project_id == project_id)
        .first()
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Outline revision 不存在")
    head = get_or_create_content_head(db, project_id)
    head.current_outline_revision_id = revision.id
    head.lock_version += 1
    if approve:
        from app.models_v2 import utcnow

        revision.status = "approved"
        revision.approved_at = utcnow()
        # Advance project status to outline_approved so the frontend workflow
        # UI can move past the outline review stage. Phase 5 v2 path didn't
        # update project.status here, leaving projects stuck in
        # outline_generated / outline_reviewing forever.
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            current = project.status or "draft_input"
            order = (
                "draft_input",
                "bible_generated",
                "outline_generated",
                "outline_reviewing",
                "outline_approved",
                "chapter_generating",
                "asset_generating",
                "vn_graph_generating",
                "reading",
                "pre_generating",
                "completed",
            )
            try:
                if order.index(current) < order.index("outline_approved"):
                    project.status = "outline_approved"
            except ValueError:
                pass
    return revision


def create_chapter_revision(
    db: Session,
    *,
    project_id: int,
    chapter_index: int,
    content: str,
    user_id: int,
    bible_revision_id: str | None = None,
    outline_revision_id: str | None = None,
    state_snapshot_id: str | None = None,
    task_id: str | None = None,
    status: str = "complete",
    activate: bool = True,
) -> ChapterRevision:
    if chapter_index < 1:
        raise HTTPException(status_code=422, detail="chapter_index 必须大于 0")
    if not content.strip():
        raise HTTPException(status_code=422, detail="章节正文不能为空")
    project_head = get_or_create_content_head(db, project_id)
    bible_id = bible_revision_id or project_head.current_bible_revision_id
    outline_id = outline_revision_id or project_head.current_outline_revision_id
    if not bible_id or not outline_id:
        raise HTTPException(status_code=409, detail="请先激活 Bible 和 Outline revision")

    bible = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == bible_id,
            StoryBibleRevision.project_id == project_id,
        )
        .first()
    )
    if not bible:
        raise HTTPException(status_code=404, detail="Story Bible revision 不存在")
    outline = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.id == outline_id,
            OutlineRevision.project_id == project_id,
        )
        .first()
    )
    if not outline:
        raise HTTPException(status_code=404, detail="Outline revision 不存在")
    if outline.bible_revision_id != bible_id:
        raise HTTPException(status_code=409, detail="Outline revision 与 Story Bible revision 不匹配")
    outline_chapter = (
        db.query(OutlineChapter)
        .filter(
            OutlineChapter.outline_revision_id == outline_id,
            OutlineChapter.chapter_index == chapter_index,
        )
        .first()
    )
    if not outline_chapter:
        raise HTTPException(status_code=409, detail="Outline revision 不包含该章节")
    if state_snapshot_id:
        snapshot = (
            db.query(StateSnapshot)
            .filter(
                StateSnapshot.id == state_snapshot_id,
                StateSnapshot.project_id == project_id,
            )
            .first()
        )
        if not snapshot:
            raise HTTPException(status_code=404, detail="State snapshot 不存在")

    chapter_head = (
        db.query(ChapterHead)
        .filter(ChapterHead.project_id == project_id, ChapterHead.chapter_index == chapter_index)
        .first()
    )
    digest = content_hash(content)
    source_digest = content_hash(
        {
            "bible_revision_id": bible_id,
            "outline_revision_id": outline_id,
            "state_snapshot_id": state_snapshot_id,
        }
    )
    existing = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.project_id == project_id,
            ChapterRevision.chapter_index == chapter_index,
            ChapterRevision.source_hash == source_digest,
            ChapterRevision.content_hash == digest,
        )
        .first()
    )
    if existing:
        if activate:
            if chapter_head:
                chapter_head.current_revision_id = existing.id
                chapter_head.lock_version += 1
            else:
                db.add(ChapterHead(project_id=project_id, chapter_index=chapter_index, current_revision_id=existing.id))
        return existing

    revision = ChapterRevision(
        project_id=project_id,
        chapter_index=chapter_index,
        parent_revision_id=chapter_head.current_revision_id if chapter_head else None,
        bible_revision_id=bible_id,
        outline_revision_id=outline_id,
        state_snapshot_id=state_snapshot_id,
        revision_no=_next_revision_no(db, ChapterRevision, project_id, chapter_index=chapter_index),
        source_hash=source_digest,
        content_hash=digest,
        content=content,
        status=status,
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    if activate:
        if chapter_head:
            chapter_head.current_revision_id = revision.id
            chapter_head.lock_version += 1
        else:
            db.add(ChapterHead(project_id=project_id, chapter_index=chapter_index, current_revision_id=revision.id))
    return revision


def activate_chapter_revision(db: Session, project_id: int, chapter_index: int, revision_id: str) -> ChapterRevision:
    revision = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.id == revision_id,
            ChapterRevision.project_id == project_id,
            ChapterRevision.chapter_index == chapter_index,
        )
        .first()
    )
    if not revision:
        raise HTTPException(status_code=404, detail="Chapter revision 不存在")
    head = (
        db.query(ChapterHead)
        .filter(ChapterHead.project_id == project_id, ChapterHead.chapter_index == chapter_index)
        .first()
    )
    if head:
        head.current_revision_id = revision.id
        head.lock_version += 1
    else:
        db.add(ChapterHead(project_id=project_id, chapter_index=chapter_index, current_revision_id=revision.id))
    return revision


def project_readiness(db: Session, project_id: int) -> dict[str, Any]:
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    issues: list[dict[str, Any]] = []
    if not head or not head.current_bible_revision_id:
        issues.append({"code": "bible.missing", "artifact": "bible", "severity": "error"})
    if not head or not head.current_outline_revision_id:
        issues.append({"code": "outline.missing", "artifact": "outline", "severity": "error"})

    outline = None
    if head and head.current_outline_revision_id:
        outline = db.query(OutlineRevision).filter(OutlineRevision.id == head.current_outline_revision_id).first()
        if not outline:
            issues.append({"code": "outline.pointer_broken", "artifact": "outline", "severity": "error"})
        elif outline.bible_revision_id != head.current_bible_revision_id:
            issues.append(
                {
                    "code": "outline.stale_bible",
                    "artifact": "outline",
                    "severity": "error",
                    "recompute": "outline.generate",
                }
            )

    expected_chapter_indexes: set[int] = set()
    if outline:
        expected_chapter_indexes = {
            int(row.chapter_index)
            for row in db.query(OutlineChapter)
            .filter(OutlineChapter.outline_revision_id == outline.id)
            .all()
        }

    chapters = db.query(ChapterHead).filter(ChapterHead.project_id == project_id).all()
    current_chapter_ids: dict[int, str] = {}
    for chapter_head in chapters:
        revision = db.query(ChapterRevision).filter(ChapterRevision.id == chapter_head.current_revision_id).first()
        if not revision:
            issues.append(
                {
                    "code": "chapter.pointer_broken",
                    "artifact": f"chapter:{chapter_head.chapter_index}",
                    "severity": "error",
                }
            )
            continue
        current_chapter_ids[chapter_head.chapter_index] = revision.id
        if revision.status != "complete":
            issues.append(
                {
                    "code": "chapter.incomplete",
                    "artifact": f"chapter:{chapter_head.chapter_index}",
                    "severity": "error",
                    "recompute": "chapter.generate",
                }
            )
        if revision.bible_revision_id != (head.current_bible_revision_id if head else None):
            issues.append(
                {
                    "code": "chapter.stale_bible",
                    "artifact": f"chapter:{chapter_head.chapter_index}",
                    "severity": "error",
                    "recompute": "chapter.generate",
                }
            )
        if revision.outline_revision_id != (head.current_outline_revision_id if head else None):
            issues.append(
                {
                    "code": "chapter.stale_outline",
                    "artifact": f"chapter:{chapter_head.chapter_index}",
                    "severity": "error",
                    "recompute": "chapter.generate",
                }
            )

    for chapter_index in sorted(expected_chapter_indexes - set(current_chapter_ids)):
        issues.append(
            {
                "code": "chapter.missing",
                "artifact": f"chapter:{chapter_index}",
                "severity": "error",
                "recompute": "chapter.generate",
            }
        )

    graph_heads = db.query(VNGraphHead).filter(VNGraphHead.project_id == project_id).all()
    for graph_head in graph_heads:
        graph = db.query(VNGraphRevision).filter(VNGraphRevision.id == graph_head.current_revision_id).first()
        if not graph or graph.chapter_revision_id != current_chapter_ids.get(graph_head.chapter_index):
            issues.append(
                {
                    "code": "vngraph.stale_chapter",
                    "artifact": f"vngraph:{graph_head.chapter_index}",
                    "severity": "warning",
                    "recompute": "vngraph.compile",
                }
            )

    return {
        "project_id": project_id,
        "ready": not any(issue["severity"] == "error" for issue in issues),
        "current": {
            "bible_revision_id": head.current_bible_revision_id if head else None,
            "outline_revision_id": head.current_outline_revision_id if head else None,
            "chapter_revision_ids": current_chapter_ids,
        },
        "issues": issues,
    }
