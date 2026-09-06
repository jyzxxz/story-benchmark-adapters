"""固化发布：客户端草稿锚点 → 激活 heads → 原子发布。

前端把五类草稿逐步物化为未激活修订（materialize），发布时把修订 id 集
作为 anchors 提交；本服务在单个事务内按链路顺序激活并调用 publish_project。
任一步失败（锚点非法、readiness 阻塞、指纹失配）整体回滚，激活不留痕迹。
"""
from __future__ import annotations

from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.application.chapter_script_service import (
    activate_chapter_script_head,
    get_or_create_chapter_script_head,
)
from app.application.publication_readiness_service import (
    calculate_publication_readiness,
)
from app.application.publication_service import AtomicPublicationResult, publish_project
from app.application.revision_head_service import (
    activate_path_chapter_revision_head,
    get_or_create_story_path_outline_head,
)
from app.application.revision_service import (
    activate_bible_revision_head,
    get_or_create_content_head,
)
from app.application.story_outline_service import activate_story_path_outline_head
from app.application.vn_graph_service import (
    activate_vn_graph_head,
    get_or_create_vn_graph_head,
)
from app.core.errors import AppError
from app.models_v2 import (
    ChapterRevision,
    ChapterScriptRevision,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    VNGraphRevision,
)

_SELECTABLE_STATUSES = frozenset({"ready", "complete"})


def collect_current_anchors(db: Session, *, project_id: int) -> dict[str, Any]:
    """按当前链路收集固化锚点：head 已设且可用则用 head，否则取最新可选修订。

    供无草稿会话的固化发布（空 anchors 回退）与 agent publish 工具共用。
    规则：只收集 active placement；章节按 chapter_slot、脚本按所属章节修订、
    图按所属脚本精确归属，绝不按 chapter_index 全项目匹配；遍历全部 active
    StoryPath（readiness 校验所有 active 路径，不能只看根路径）。
    """
    content_head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .one_or_none()
    )
    bible_revision_id: str | None = None
    if content_head and content_head.current_bible_revision_id:
        bible_revision_id = content_head.current_bible_revision_id
    else:
        latest_bible = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.project_id == project_id,
                StoryBibleRevision.status.in_(_SELECTABLE_STATUSES),
            )
            .order_by(StoryBibleRevision.revision_no.desc())
            .first()
        )
        bible_revision_id = latest_bible.id if latest_bible else None

    outline_revision_ids: dict[str, str] = {}
    chapter_revision_ids: dict[str, str] = {}
    script_revision_ids: dict[str, str] = {}
    graph_revision_ids: dict[str, str] = {}

    paths = (
        db.query(StoryPath)
        .filter(StoryPath.project_id == project_id, StoryPath.status == "active")
        .all()
    )
    for path in paths:
        outline_head = (
            db.query(StoryPathOutlineHead)
            .filter(StoryPathOutlineHead.story_path_id == path.id)
            .one_or_none()
        )
        outline: OutlineRevision | None = None
        if outline_head and outline_head.current_revision_id:
            outline = (
                db.query(OutlineRevision)
                .filter(OutlineRevision.id == outline_head.current_revision_id)
                .one_or_none()
            )
        if outline is None or outline.status not in _SELECTABLE_STATUSES:
            outline = (
                db.query(OutlineRevision)
                .filter(
                    OutlineRevision.story_path_id == path.id,
                    OutlineRevision.status.in_(_SELECTABLE_STATUSES),
                )
                .order_by(OutlineRevision.revision_no.desc())
                .first()
            )
        if outline is not None:
            outline_revision_ids[path.id] = outline.id

        placements = (
            db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.status == "active",
            )
            .order_by(StoryPathChapter.display_index)
            .all()
        )
        for placement in placements:
            chapter: ChapterRevision | None = None
            if placement.current_revision_id:
                chapter = (
                    db.query(ChapterRevision)
                    .filter(ChapterRevision.id == placement.current_revision_id)
                    .one_or_none()
                )
            if chapter is None or chapter.status not in _SELECTABLE_STATUSES:
                chapter = (
                    db.query(ChapterRevision)
                    .filter(
                        ChapterRevision.chapter_slot_id == placement.chapter_slot_id,
                        ChapterRevision.status.in_(_SELECTABLE_STATUSES),
                    )
                    .order_by(ChapterRevision.revision_no.desc())
                    .first()
                )
            if chapter is None:
                continue
            chapter_revision_ids[placement.id] = chapter.id
            script = (
                db.query(ChapterScriptRevision)
                .filter(
                    ChapterScriptRevision.chapter_revision_id == chapter.id,
                    ChapterScriptRevision.status.in_(_SELECTABLE_STATUSES),
                )
                .order_by(ChapterScriptRevision.revision_no.desc())
                .first()
            )
            if script is not None:
                script_revision_ids[placement.id] = script.id
                graph = (
                    db.query(VNGraphRevision)
                    .filter(
                        VNGraphRevision.script_revision_id == script.id,
                        VNGraphRevision.status.in_(_SELECTABLE_STATUSES),
                    )
                    .order_by(VNGraphRevision.revision_no.desc())
                    .first()
                )
                if graph is not None:
                    graph_revision_ids[placement.id] = graph.id

    return {
        "bible_revision_id": bible_revision_id,
        "outline_revision_ids": outline_revision_ids,
        "chapter_revision_ids": chapter_revision_ids,
        "script_revision_ids": script_revision_ids,
        "graph_revision_ids": graph_revision_ids,
    }


def _fail_anchor(subject: str, revision_id: str, reason: str) -> None:
    raise AppError(
        code="finalize.anchor_invalid",
        message=f"固化锚点无效（{subject}）：{reason}",
        status_code=422,
        details={"subject": subject, "revision_id": revision_id, "reason": reason},
    )


def _owned_placement(
    db: Session,
    *,
    project_id: int,
    path_chapter_id: str,
    subject: str,
    revision_id: str,
) -> StoryPathChapter:
    placement = (
        db.query(StoryPathChapter)
        .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPath.project_id == project_id,
        )
        .one_or_none()
    )
    if not placement:
        _fail_anchor(subject, revision_id, "path_chapter 不存在或不属于该项目")
    return placement


def finalize_and_publish(
    db: Session,
    *,
    project_id: int,
    user_id: int,
    idempotency_key: str,
    bible_revision_id: str | None = None,
    outline_revision_ids: Mapping[str, str] | None = None,
    chapter_revision_ids: Mapping[str, str] | None = None,
    script_revision_ids: Mapping[str, str] | None = None,
    graph_revision_ids: Mapping[str, str] | None = None,
    release_notes: str | None = None,
) -> AtomicPublicationResult:
    """Activate materialized draft anchors and publish in one transaction."""

    if (
        bible_revision_id is None
        and not outline_revision_ids
        and not chapter_revision_ids
        and not script_revision_ids
        and not graph_revision_ids
    ):
        # 无草稿会话（或 agent 调用）未携带任何锚点时，回退为按服务端当前
        # 链路收集——否则 UI 里 finalize-publish 不可达形成发布死锁。
        anchors = collect_current_anchors(db, project_id=project_id)
        bible_revision_id = anchors["bible_revision_id"]
        outline_revision_ids = anchors["outline_revision_ids"]
        chapter_revision_ids = anchors["chapter_revision_ids"]
        script_revision_ids = anchors["script_revision_ids"]
        graph_revision_ids = anchors["graph_revision_ids"]

    with db.begin_nested():
        if bible_revision_id is not None:
            bible = (
                db.query(StoryBibleRevision)
                .filter(
                    StoryBibleRevision.id == bible_revision_id,
                    StoryBibleRevision.project_id == project_id,
                )
                .one_or_none()
            )
            if not bible or bible.status not in _SELECTABLE_STATUSES:
                _fail_anchor("bible", bible_revision_id, "修订不存在或状态不可用")
            head = get_or_create_content_head(db, project_id)
            activate_bible_revision_head(
                db,
                project_id=project_id,
                revision_id=bible.id,
                expected_lock_version=head.lock_version,
            )

        for story_path_id, revision_id in (outline_revision_ids or {}).items():
            subject = f"outline:{story_path_id}"
            revision = (
                db.query(OutlineRevision)
                .filter(
                    OutlineRevision.id == revision_id,
                    OutlineRevision.story_path_id == story_path_id,
                )
                .one_or_none()
            )
            if not revision or revision.status not in _SELECTABLE_STATUSES:
                _fail_anchor(subject, revision_id, "修订不存在或不属于该路径")
            head = get_or_create_story_path_outline_head(db, story_path_id)
            activate_story_path_outline_head(
                db,
                story_path_id=story_path_id,
                revision_id=revision.id,
                expected_lock_version=head.lock_version,
            )

        # 批量生成的正文是 provisional 链：激活后章前必须先激活其全部前驱章，
        # 因此按 display_index/前驱拓扑排序，绝不按请求体键序（前端 dict 序随机）。
        chapter_placements: list[tuple[StoryPathChapter, str]] = []
        for path_chapter_id, revision_id in (chapter_revision_ids or {}).items():
            placement = _owned_placement(
                db,
                project_id=project_id,
                path_chapter_id=path_chapter_id,
                subject=f"chapter:{path_chapter_id}",
                revision_id=revision_id,
            )
            chapter_placements.append((placement, revision_id))
        chapter_placements.sort(
            key=lambda item: (item[0].display_index, item[0].id)
        )
        for placement, revision_id in chapter_placements:
            revision = (
                db.query(ChapterRevision)
                .filter(
                    ChapterRevision.id == revision_id,
                    ChapterRevision.chapter_slot_id == placement.chapter_slot_id,
                )
                .one_or_none()
            )
            if not revision or revision.status not in _SELECTABLE_STATUSES:
                _fail_anchor(f"chapter:{placement.id}", revision_id, "修订不存在或不属于该章节位")
            activate_path_chapter_revision_head(
                db,
                path_chapter_id=placement.id,
                revision_id=revision.id,
                expected_lock_version=placement.lock_version,
            )

        for path_chapter_id, revision_id in (script_revision_ids or {}).items():
            subject = f"script:{path_chapter_id}"
            placement = _owned_placement(
                db,
                project_id=project_id,
                path_chapter_id=path_chapter_id,
                subject=subject,
                revision_id=revision_id,
            )
            script = (
                db.query(ChapterScriptRevision)
                .join(
                    ChapterRevision,
                    ChapterRevision.id == ChapterScriptRevision.chapter_revision_id,
                )
                .filter(
                    ChapterScriptRevision.id == revision_id,
                    ChapterRevision.chapter_slot_id == placement.chapter_slot_id,
                )
                .one_or_none()
            )
            if not script or script.status not in _SELECTABLE_STATUSES:
                _fail_anchor(subject, revision_id, "修订不存在或不属于该章节位")
            head = get_or_create_chapter_script_head(db, script.chapter_revision_id)
            activate_chapter_script_head(
                db,
                chapter_revision_id=script.chapter_revision_id,
                revision_id=script.id,
                expected_lock_version=head.lock_version,
            )

        for path_chapter_id, revision_id in (graph_revision_ids or {}).items():
            subject = f"graph:{path_chapter_id}"
            placement = _owned_placement(
                db,
                project_id=project_id,
                path_chapter_id=path_chapter_id,
                subject=subject,
                revision_id=revision_id,
            )
            graph = (
                db.query(VNGraphRevision)
                .join(
                    ChapterScriptRevision,
                    ChapterScriptRevision.id == VNGraphRevision.script_revision_id,
                )
                .join(
                    ChapterRevision,
                    ChapterRevision.id == ChapterScriptRevision.chapter_revision_id,
                )
                .filter(
                    VNGraphRevision.id == revision_id,
                    ChapterRevision.chapter_slot_id == placement.chapter_slot_id,
                )
                .one_or_none()
            )
            if not graph or graph.status not in _SELECTABLE_STATUSES:
                _fail_anchor(subject, revision_id, "修订不存在或不属于该章节位")
            head = get_or_create_vn_graph_head(db, graph.script_revision_id)
            activate_vn_graph_head(
                db,
                script_revision_id=graph.script_revision_id,
                revision_id=graph.id,
                expected_lock_version=head.lock_version,
            )

        readiness = calculate_publication_readiness(db, project_id=project_id)
        result = publish_project(
            db,
            project_id=project_id,
            user_id=user_id,
            expected_authoring_fingerprint=readiness.authoring_fingerprint,
            idempotency_key=idempotency_key,
            release_notes=release_notes,
        )
    return result
