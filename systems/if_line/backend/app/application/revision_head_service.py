"""Optimistic concurrency primitives for reviewable revision Heads."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.application.story_chapter_batch_service import (
    assert_provisional_revision_selectable,
    record_provisional_revision_selection,
)
from app.core.errors import AppError
from app.models_v2 import (
    ChapterRevision,
    ProjectContentHead,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    utcnow,
)


_IF_MATCH_PATTERN = re.compile(r'^(?:"([1-9][0-9]*)"|([1-9][0-9]*))$')
_SELECTABLE_CHAPTER_STATUSES = frozenset({"ready", "complete"})


@dataclass(frozen=True)
class RevisionHeadValue:
    revision_id: str | None
    lock_version: int
    updated_at: datetime

    def as_dict(self) -> dict[str, object]:
        return {
            "revision_id": self.revision_id,
            "lock_version": self.lock_version,
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class ChapterHeadActivation:
    revision: ChapterRevision
    head: RevisionHeadValue


def parse_if_match(value: str | None) -> int:
    """Parse the target API's integer ETag form without accepting weak/list tags."""

    if value is None or not value.strip():
        raise AppError(
            code="precondition.required",
            message="If-Match is required",
            status_code=428,
        )
    match = _IF_MATCH_PATTERN.fullmatch(value.strip())
    if not match:
        raise AppError(
            code="precondition.invalid",
            message="If-Match must be a positive integer lock version",
            status_code=400,
        )
    return int(match.group(1) or match.group(2))


def head_value(head: object) -> RevisionHeadValue:
    revision_id = getattr(head, "current_revision_id", None)
    if isinstance(head, ProjectContentHead):
        revision_id = head.current_bible_revision_id
    return RevisionHeadValue(
        revision_id=revision_id,
        lock_version=int(getattr(head, "lock_version")),
        updated_at=getattr(head, "updated_at"),
    )


def raise_head_version_conflict(head: object) -> None:
    current = head_value(head)
    raise AppError(
        code="head.version_conflict",
        message="Revision Head changed since it was read",
        status_code=409,
        details={"current_head": current.as_dict()},
    )


def get_or_create_story_path_outline_head(
    db: Session,
    story_path_id: str,
    *,
    for_update: bool = False,
) -> StoryPathOutlineHead:
    """Return a discoverable Head, including before the first Outline selection."""

    path_query = db.query(StoryPath).filter(StoryPath.id == story_path_id)
    if for_update:
        path_query = path_query.with_for_update()
    path = path_query.one_or_none()
    if not path:
        raise AppError(
            code="story_path.not_found",
            message="StoryPath does not exist",
            status_code=404,
        )
    query = db.query(StoryPathOutlineHead).filter(
        StoryPathOutlineHead.story_path_id == story_path_id
    )
    if for_update:
        query = query.with_for_update()
    head = query.one_or_none()
    if head:
        return head

    if not for_update:
        # Re-read under the parent-row mutex before creating the one-to-one row.
        path = (
            db.query(StoryPath)
            .filter(StoryPath.id == story_path_id)
            .with_for_update()
            .one()
        )
        head = (
            db.query(StoryPathOutlineHead)
            .filter(StoryPathOutlineHead.story_path_id == story_path_id)
            .with_for_update()
            .one_or_none()
        )
        if head:
            return head

    # The StoryPath row is the creation mutex for this one-to-one resource.
    head = StoryPathOutlineHead(
        story_path_id=story_path_id,
        current_revision_id=None,
        lock_version=1,
    )
    db.add(head)
    db.flush()
    return head


def _refresh_path_chapter(
    db: Session,
    placement: StoryPathChapter,
) -> StoryPathChapter:
    db.expire(placement)
    db.refresh(placement)
    return placement


def activate_path_chapter_revision_head(
    db: Session,
    *,
    path_chapter_id: str,
    revision_id: str,
    expected_lock_version: int,
) -> ChapterHeadActivation:
    """Select one ChapterRevision using the PathChapter's lock version."""

    placement = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.status == "active",
        )
        .one_or_none()
    )
    if not placement:
        raise AppError(
            code="path_chapter.not_found",
            message="PathChapter does not exist",
            status_code=404,
        )
    path = db.query(StoryPath).filter(StoryPath.id == placement.story_path_id).one()
    if path.status != "active":
        raise AppError(
            code="story_path.archived",
            message="Archived StoryPath cannot be edited",
            status_code=409,
        )
    revision = (
        db.query(ChapterRevision)
        .filter(
            ChapterRevision.id == revision_id,
            ChapterRevision.project_id == path.project_id,
            ChapterRevision.chapter_slot_id == placement.chapter_slot_id,
        )
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="chapter_revision.not_found",
            message="Chapter revision does not belong to this PathChapter",
            status_code=404,
        )
    if revision.status not in _SELECTABLE_CHAPTER_STATUSES:
        raise AppError(
            code="chapter_revision.not_ready",
            message="Chapter revision is not ready for review selection",
            status_code=409,
        )
    assert_provisional_revision_selectable(db, revision)

    same_revision = placement.current_revision_id == revision.id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": StoryPathChapter.lock_version,
            "updated_at": StoryPathChapter.updated_at,
        }
    else:
        values = {
            "current_revision_id": revision.id,
            "lock_version": StoryPathChapter.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(StoryPathChapter)
        .where(
            StoryPathChapter.id == placement.id,
            StoryPathChapter.lock_version == expected_lock_version,
            StoryPathChapter.status == "active",
            *(
                (StoryPathChapter.current_revision_id == revision.id,)
                if same_revision
                else ()
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise_head_version_conflict(_refresh_path_chapter(db, placement))

    record_provisional_revision_selection(
        db,
        placement=placement,
        revision=revision,
    )

    current = _refresh_path_chapter(db, placement)
    return ChapterHeadActivation(revision=revision, head=head_value(current))


def compare_and_swap_bible_head(
    db: Session,
    *,
    head: ProjectContentHead,
    revision_id: str,
    expected_lock_version: int,
) -> RevisionHeadValue:
    same_revision = head.current_bible_revision_id == revision_id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": ProjectContentHead.lock_version,
            "updated_at": ProjectContentHead.updated_at,
        }
    else:
        values = {
            "current_bible_revision_id": revision_id,
            "lock_version": ProjectContentHead.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(ProjectContentHead)
        .where(
            ProjectContentHead.project_id == head.project_id,
            ProjectContentHead.lock_version == expected_lock_version,
            *(
                (ProjectContentHead.current_bible_revision_id == revision_id,)
                if same_revision
                else ()
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.expire(head)
    db.refresh(head)
    if result.rowcount != 1:
        raise_head_version_conflict(head)
    return RevisionHeadValue(
        revision_id=head.current_bible_revision_id,
        lock_version=head.lock_version,
        updated_at=head.updated_at,
    )


def compare_and_swap_outline_head(
    db: Session,
    *,
    head: StoryPathOutlineHead,
    revision_id: str,
    expected_lock_version: int,
) -> RevisionHeadValue:
    same_revision = head.current_revision_id == revision_id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": StoryPathOutlineHead.lock_version,
            "updated_at": StoryPathOutlineHead.updated_at,
        }
    else:
        values = {
            "current_revision_id": revision_id,
            "lock_version": StoryPathOutlineHead.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(StoryPathOutlineHead)
        .where(
            StoryPathOutlineHead.story_path_id == head.story_path_id,
            StoryPathOutlineHead.lock_version == expected_lock_version,
            *(
                (StoryPathOutlineHead.current_revision_id == revision_id,)
                if same_revision
                else ()
            ),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    db.expire(head)
    db.refresh(head)
    if result.rowcount != 1:
        raise_head_version_conflict(head)
    return head_value(head)
