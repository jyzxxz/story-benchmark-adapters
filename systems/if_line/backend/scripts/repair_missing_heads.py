"""Repair NULL content heads for projects stuck with unactivated revisions.

Root cause: generation workers persist revisions without activating heads
(review gating) while the review UI affordance was unavailable — leaving
projects with ready/complete revisions but NULL head pointers.  That state
blocks chapter generation (context.*_head_missing 409) and publication
readiness (*.head_missing).

Repair semantics (mirrors the new auto-adopt rule):
- Bible: when the head pointer is NULL, activate the latest complete/ready
  revision and sync project characters.
- Outline: per active StoryPath with a NULL outline head, activate the latest
  ready/complete revision whose bible_revision_id equals the (repaired) bible
  head.  Activation runs through activate_story_path_outline_head so
  PathChapter placements are reconciled exactly like finalize-publish.
- Chapter/Script/VNGraph heads are REPORT-ONLY: finalize-publish activates
  them at publish time.

The command defaults to a transactionally rolled-back dry run; pass --apply
to commit.  Use --project-id to repair a single project first.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.application.revision_head_service import (
    get_or_create_story_path_outline_head,
)
from app.application.story_bible_service import (
    _sync_project_characters,
    get_or_create_content_head,
)
from app.application.story_outline_service import (
    _SELECTABLE_OUTLINE_STATUSES,
    activate_story_path_outline_head,
)
from app.core.errors import AppError
from app.database import SessionLocal
from app.models import Project
from app.models_v2 import (
    OutlineRevision,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
)

_SELECTABLE_BIBLE_STATUSES = frozenset({"complete", "ready"})


@dataclass
class ProjectReport:
    project_id: int
    title: str
    bible_activated: str | None = None
    bible_skipped_reason: str | None = None
    outlines_activated: list[str] = field(default_factory=list)
    outlines_skipped: list[str] = field(default_factory=list)
    # Report-only counters; left for finalize-publish to activate.
    chapters_missing_head: int = 0


@dataclass
class Counters:
    projects_seen: int = 0
    bible_heads_repaired: int = 0
    bible_skipped: int = 0
    outline_heads_repaired: int = 0
    outlines_skipped: int = 0
    chapters_reported_missing_head: int = 0


def _repair_bible_head(db: Session, project_id: int, report: ProjectReport) -> str | None:
    head = get_or_create_content_head(db, project_id)
    if head.current_bible_revision_id is not None:
        return head.current_bible_revision_id
    revision = (
        db.query(StoryBibleRevision)
        .filter(
            StoryBibleRevision.project_id == project_id,
            StoryBibleRevision.status.in_(_SELECTABLE_BIBLE_STATUSES),
        )
        .order_by(StoryBibleRevision.revision_no.desc())
        .first()
    )
    if revision is None:
        report.bible_skipped_reason = "no complete/ready bible revision exists"
        return None
    head.current_bible_revision_id = revision.id
    head.lock_version += 1
    _sync_project_characters(
        db, project_id=project_id, content_json=revision.content_json
    )
    report.bible_activated = revision.id
    return revision.id


def _repair_outline_heads(
    db: Session, project_id: int, bible_head_id: str | None, report: ProjectReport
) -> None:
    if bible_head_id is None:
        for path in (
            db.query(StoryPath)
            .filter(StoryPath.project_id == project_id, StoryPath.status == "active")
            .all()
        ):
            report.outlines_skipped.append(
                f"{path.id}: bible head missing, cannot activate outline"
            )
        return
    for path in (
        db.query(StoryPath)
        .filter(StoryPath.project_id == project_id, StoryPath.status == "active")
        .order_by(StoryPath.created_at, StoryPath.id)
        .all()
    ):
        head = get_or_create_story_path_outline_head(db, path.id)
        if head.current_revision_id is not None:
            continue
        revision = (
            db.query(OutlineRevision)
            .filter(
                OutlineRevision.story_path_id == path.id,
                OutlineRevision.status.in_(_SELECTABLE_OUTLINE_STATUSES),
                OutlineRevision.bible_revision_id == bible_head_id,
            )
            .order_by(OutlineRevision.revision_no.desc())
            .first()
        )
        if revision is None:
            report.outlines_skipped.append(
                f"{path.id}: no ready outline revision bound to the current bible"
            )
            continue
        try:
            activate_story_path_outline_head(
                db,
                story_path_id=path.id,
                revision_id=revision.id,
                expected_lock_version=head.lock_version,
            )
        except (HTTPException, AppError) as error:
            report.outlines_skipped.append(
                f"{path.id}: activation failed ({getattr(error, 'detail', None) or error})"
            )
            continue
        report.outlines_activated.append(revision.id)


def _report_chapter_gaps(db: Session, project_id: int, report: ProjectReport) -> None:
    placements = (
        db.query(StoryPathChapter)
        .join(StoryPath, StoryPathChapter.story_path_id == StoryPath.id)
        .filter(
            StoryPath.project_id == project_id,
            StoryPath.status == "active",
            StoryPathChapter.status == "active",
            StoryPathChapter.current_revision_id.is_(None),
        )
        .all()
    )
    report.chapters_missing_head = len(placements)


def repair(apply: bool, project_ids: list[int] | None = None) -> tuple[Counters, list[ProjectReport]]:
    db: Session = SessionLocal()
    counters = Counters()
    reports: list[ProjectReport] = []
    try:
        query = db.query(Project).order_by(Project.id)
        if project_ids:
            query = query.filter(Project.id.in_(project_ids))
        for project in query.all():
            counters.projects_seen += 1
            report = ProjectReport(project_id=project.id, title=project.title)
            bible_head_id = _repair_bible_head(db, project.id, report)
            if report.bible_activated:
                counters.bible_heads_repaired += 1
            if report.bible_skipped_reason:
                counters.bible_skipped += 1
            _repair_outline_heads(db, project.id, bible_head_id, report)
            counters.outline_heads_repaired += len(report.outlines_activated)
            counters.outlines_skipped += len(report.outlines_skipped)
            _report_chapter_gaps(db, project.id, report)
            counters.chapters_reported_missing_head += report.chapters_missing_head
            reports.append(report)
        if apply:
            db.commit()
        else:
            db.rollback()
        return counters, reports
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="commit the repairs")
    mode.add_argument(
        "--dry-run", action="store_true", help="run and roll back (default)"
    )
    parser.add_argument(
        "--project-id",
        type=int,
        action="append",
        help="limit the repair to the given project id (repeatable)",
    )
    args = parser.parse_args()
    counters, reports = repair(
        apply=bool(args.apply), project_ids=args.project_id or None
    )
    payload = {
        "mode": "apply" if args.apply else "dry-run",
        **asdict(counters),
        "projects": [asdict(report) for report in reports],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
