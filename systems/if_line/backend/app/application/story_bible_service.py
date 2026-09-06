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
from app.models_v2 import ProjectContentHead, StoryBibleRevision

# 标注阶段不该回写的非角色称呼（旁白/未知声音占位等）。
_NON_CHARACTER_SPEAKER_LABELS = frozenset({"旁白", "？？？", "???", "？??"})


def collect_unregistered_speaker_names(
    script_ir: dict[str, Any],
    *,
    known_characters: list[dict[str, Any]],
) -> list[str]:
    """从 script IR 提取实际开口、但不在 bible 角色表中的说话人称呼。"""
    known = {
        str(char.get("name") or "").strip().casefold()
        for char in known_characters
        if isinstance(char, dict)
    }
    known.update(_NON_CHARACTER_SPEAKER_LABELS)
    ordered: list[str] = []
    seen: set[str] = set()
    for paragraph in script_ir.get("paragraphs") or []:
        if not isinstance(paragraph, dict) or not paragraph.get("unregistered_speaker"):
            continue
        name = str(paragraph.get("speaker_name") or "").strip()
        key = name.casefold()
        if not name or key in known or key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return ordered


def append_auto_registered_characters(
    db: Session,
    *,
    project_id: int,
    names: list[str],
    source_note: str,
    task_id: str | None = None,
    user_id: int | None = None,
) -> StoryBibleRevision | None:
    """把正文里出现但 bible 未登记的配角以占位档案增量补录进 bible head。

    只追加角色条目（visual_profile 留待后续补全），不改写既有角色；
    无新增或项目尚无 bible 时返回 None。
    """
    if not names:
        return None
    head = get_or_create_content_head(db, project_id)
    if not head.current_bible_revision_id:
        return None
    parent = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == head.current_bible_revision_id,
            StoryBibleRevision.project_id == project_id,
        )
        .one_or_none()
    )
    if not parent:
        return None
    content = deepcopy(parent.content_json or {})
    characters = content.setdefault("characters", [])
    if not isinstance(characters, list):
        return None
    known = {
        str(char.get("name") or "").strip().casefold()
        for char in characters
        if isinstance(char, dict)
    }
    known.update(_NON_CHARACTER_SPEAKER_LABELS)
    appended: list[dict[str, Any]] = []
    for name in names:
        if not name or name.strip().casefold() in known:
            continue
        known.add(name.strip().casefold())
        appended.append(
            {
                "name": name.strip(),
                "role": "配角",
                "gender": "unknown",
                "auto_registered": True,
                "auto_registered_source": source_note,
                "visual_profile": {},
                "visual_profile_source": "pending",
                "personality": "",
                "motivation_and_goal": "",
                "internal_conflict": "",
                "voice": "",
            }
        )
    if not appended:
        return None
    characters.extend(appended)
    return create_bible_revision(
        db,
        project_id=project_id,
        content=content,
        source={
            "kind": "auto_character_writeback",
            "parent_bible_revision_id": parent.id,
            "appended_character_names": [item["name"] for item in appended],
            "source_note": source_note,
        },
        user_id=user_id,
        task_id=task_id,
        activate=True,
        parent_revision_id=parent.id,
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


def _sync_project_characters(
    db: Session,
    *,
    project_id: int,
    content_json: dict[str, Any] | None,
) -> None:
    """当前生效的 Bible 变更时，把角色名单同步到 Project.characters。

    项目列表页「核心人物」读的是 Project.characters（建项目时手填），
    AI 生成圣经后从未回写导致永远显示"未设置"。仅在姓名集合与现有
    列表不一致时写库——publication readiness 指纹包含该字段，避免
    同内容激活反复翻转"有未发布修改"。
    """
    content = content_json or {}
    characters = content.get("characters")
    if not isinstance(characters, list):
        return
    roster = [
        {"name": str(item.get("name") or "").strip(), "role": str(item.get("role") or "")}
        for item in characters
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    project = db.query(Project).filter(Project.id == project_id).one_or_none()
    if project is None:
        return

    def _names(entries: list[Any]) -> set[str]:
        names: set[str] = set()
        for entry in entries or []:
            if isinstance(entry, dict):
                name = str(entry.get("name") or "").strip()
            else:
                name = str(entry or "").strip()
            if name:
                names.add(name)
        return names

    if _names(roster) == _names(project.characters):
        return
    project.characters = roster


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
    source_digest = content_hash(source)
    existing_for_task = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.generation_task_id == task_id)
        .first()
        if task_id is not None
        else None
    )
    if existing_for_task:
        parent_mismatch = (
            parent_revision_id is not _CURRENT_BIBLE_HEAD
            and existing_for_task.parent_revision_id != parent_revision_id
        )
        if (
            existing_for_task.project_id != project_id
            or parent_mismatch
            or existing_for_task.source_hash != source_digest
            or existing_for_task.content_hash != digest
            or existing_for_task.content_json != content
        ):
            raise HTTPException(
                status_code=409,
                detail="生成任务已绑定至不同的 Bible revision",
            )
        head = get_or_create_content_head(db, project_id)
        # head 为空时首个修订自动采用（否则换会话后生成链会 409 死锁）；
        # head 已有值时维持审阅制，仅显式 activate 才推进。
        if activate or head.current_bible_revision_id is None:
            if head.current_bible_revision_id != existing_for_task.id:
                head.current_bible_revision_id = existing_for_task.id
                head.lock_version += 1
            _sync_project_characters(
                db, project_id=project_id, content_json=existing_for_task.content_json
            )
        return existing_for_task

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
    # 内容寻址去重（仅手动/物化路径；AI 生成保留 task 溯源允许同内容共存）。
    # 客户端物化内容经浏览器往返会归一化整值浮点，hash 漂移会造出同内容重复
    # 修订并使下游链锚定失配，因此按 JS 规范化语义做宽容比较。
    dedupe = None
    if task_id is None:
        dedupe = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.project_id == project_id,
                StoryBibleRevision.content_hash == digest,
            )
            .order_by(StoryBibleRevision.revision_no)
            .first()
        )
        if dedupe is None:
            js_digest = content_hash_js(content)
            dedupe = next(
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
    if dedupe is not None:
        if activate or head.current_bible_revision_id is None:
            head.current_bible_revision_id = dedupe.id
            head.lock_version += 1
            _sync_project_characters(
                db, project_id=project_id, content_json=dedupe.content_json
            )
        return dedupe

    revision_no = int(
        db.query(func.max(StoryBibleRevision.revision_no))
        .filter(StoryBibleRevision.project_id == project_id)
        .scalar()
        or 0
    ) + 1
    revision = StoryBibleRevision(
        project_id=project_id,
        parent_revision_id=parent_id,
        revision_no=revision_no,
        source_hash=source_digest,
        content_hash=digest,
        content_json=content,
        status="complete",
        generation_task_id=task_id,
        created_by=user_id,
    )
    db.add(revision)
    db.flush()
    if activate or head.current_bible_revision_id is None:
        head.current_bible_revision_id = revision.id
        head.lock_version += 1
        _sync_project_characters(db, project_id=project_id, content_json=content)
    return revision


def activate_bible_revision_head(
    db: Session,
    *,
    project_id: int,
    revision_id: str,
    expected_lock_version: int,
) -> BibleHeadActivation:
    revision = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.id == revision_id,
            StoryBibleRevision.project_id == project_id,
        )
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
    _sync_project_characters(db, project_id=project_id, content_json=revision.content_json)
    return BibleHeadActivation(revision=revision, head=selected)


__all__ = [
    "BibleHeadActivation",
    "activate_bible_revision_head",
    "append_auto_registered_characters",
    "build_bible_generation_source",
    "collect_unregistered_speaker_names",
    "create_bible_revision",
    "get_or_create_content_head",
]
