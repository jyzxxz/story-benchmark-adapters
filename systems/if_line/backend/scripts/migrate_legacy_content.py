"""Backfill immutable v2 revisions from the legacy content tables.

The command defaults to a transactionally rolled-back dry run.  It never
deletes or updates legacy rows.  Run Alembic to head before using it.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, inspect

from app.application.hashing import content_hash
from app.database import SessionLocal, engine
from app.models import ChapterContent, ChapterOutline, Project, StoryBible, VNGraph
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
    VNGraphHead,
    VNGraphRevision,
)


@dataclass
class Counters:
    projects_seen: int = 0
    heads_created: int = 0
    bible_revisions_created: int = 0
    bible_rows_collapsed_as_duplicates: int = 0
    outline_revisions_created: int = 0
    outline_chapters_created: int = 0
    ambiguous_outline_rows_archived: int = 0
    chapter_revisions_created: int = 0
    vngraph_revisions_created: int = 0
    skipped_existing: int = 0


def _assert_schema() -> None:
    required = {
        "project_content_heads",
        "story_bible_revisions",
        "outline_revisions",
        "chapter_revisions",
        "vn_graph_revisions",
    }
    tables = set(inspect(engine).get_table_names())
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"v2 schema missing; run alembic upgrade head first: {missing}")


def _next_no(db, model, project_id: int, chapter_index: int | None = None) -> int:
    query = db.query(func.max(model.revision_no)).filter(model.project_id == project_id)
    if chapter_index is not None:
        query = query.filter(model.chapter_index == chapter_index)
    return int(query.scalar() or 0) + 1


def _bible_payload(row: StoryBible) -> dict[str, Any]:
    if row.raw_json:
        return dict(row.raw_json)
    return {
        "worldview": row.worldview,
        "characters": row.characters or [],
        "character_relations": row.character_relations,
        "main_conflict": row.main_conflict,
        "emotional_line": row.emotional_line,
        "style_rules": row.style_rules,
        "ending_constraints": row.ending_constraints,
        "forbidden_points": row.forbidden_points or [],
        "writing_notes": row.writing_notes or [],
    }


def _get_or_create_head(db, project_id: int, counters: Counters) -> ProjectContentHead:
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not head:
        head = ProjectContentHead(project_id=project_id)
        db.add(head)
        db.flush()
        counters.heads_created += 1
    return head


def _migrate_bibles(db, project: Project, head: ProjectContentHead, counters: Counters) -> None:
    rows = (
        db.query(StoryBible)
        .filter(StoryBible.project_id == project.id)
        .order_by(StoryBible.created_at, StoryBible.id)
        .all()
    )
    latest: StoryBibleRevision | None = None
    for row in rows:
        existing = (
            db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.legacy_source_table == "story_bibles",
                StoryBibleRevision.legacy_source_id == str(row.id),
            )
            .first()
        )
        if existing:
            latest = existing
            counters.skipped_existing += 1
            continue
        payload = _bible_payload(row)
        digest = content_hash(payload)
        duplicate = (
            db.query(StoryBibleRevision)
            .filter(StoryBibleRevision.project_id == project.id, StoryBibleRevision.content_hash == digest)
            .first()
        )
        if duplicate:
            latest = duplicate
            counters.bible_rows_collapsed_as_duplicates += 1
            continue
        revision = StoryBibleRevision(
            project_id=project.id,
            parent_revision_id=latest.id if latest else None,
            revision_no=_next_no(db, StoryBibleRevision, project.id),
            source_hash=content_hash({"legacy_table": "story_bibles", "legacy_id": row.id}),
            content_hash=digest,
            content_json=payload,
            status="complete",
            legacy_source_table="story_bibles",
            legacy_source_id=str(row.id),
            created_by=project.owner_id,
            created_at=row.created_at,
        )
        db.add(revision)
        db.flush()
        latest = revision
        counters.bible_revisions_created += 1
    if latest:
        head.current_bible_revision_id = latest.id


def _migrate_current_outline(db, project: Project, head: ProjectContentHead, counters: Counters) -> None:
    rows = (
        db.query(ChapterOutline)
        .filter(ChapterOutline.project_id == project.id)
        .order_by(ChapterOutline.chapter_index, ChapterOutline.created_at, ChapterOutline.id)
        .all()
    )
    if not rows or not head.current_bible_revision_id:
        return
    by_index: dict[int, list[ChapterOutline]] = defaultdict(list)
    for row in rows:
        by_index[row.chapter_index].append(row)
    latest_rows = [items[-1] for _, items in sorted(by_index.items())]
    counters.ambiguous_outline_rows_archived += sum(max(0, len(items) - 1) for items in by_index.values())
    normalized = [
        {
            "chapter_index": row.chapter_index,
            "title": row.title,
            "summary": row.summary,
            "conflict": row.conflict,
            "characters": row.characters or [],
            "scene": row.scene,
            "emotion": row.emotion,
            "visual_keywords": row.visual_keywords or [],
        }
        for row in latest_rows
    ]
    source = {
        "legacy_table": "chapter_outlines",
        "selected_ids": [row.id for row in latest_rows],
        "ambiguity": "latest row per chapter; original generation batches unavailable",
    }
    source_digest = content_hash(source)
    digest = content_hash(normalized)
    revision = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.project_id == project.id,
            OutlineRevision.source_hash == source_digest,
            OutlineRevision.content_hash == digest,
        )
        .first()
    )
    if not revision:
        revision = OutlineRevision(
            project_id=project.id,
            bible_revision_id=head.current_bible_revision_id,
            parent_revision_id=head.current_outline_revision_id,
            revision_no=_next_no(db, OutlineRevision, project.id),
            source_hash=source_digest,
            content_hash=digest,
            status="approved" if all(row.status == "approved" for row in latest_rows) else "draft",
            legacy_source_table="chapter_outlines",
            legacy_source_id=",".join(str(row.id) for row in latest_rows),
            created_by=project.owner_id,
        )
        db.add(revision)
        db.flush()
        for payload, row in zip(normalized, latest_rows):
            db.add(
                OutlineChapter(
                    outline_revision_id=revision.id,
                    chapter_index=row.chapter_index,
                    title=row.title,
                    summary=row.summary,
                    conflict=row.conflict,
                    characters=row.characters or [],
                    scene=row.scene,
                    emotion=row.emotion,
                    visual_keywords=row.visual_keywords or [],
                    content_hash=content_hash(payload),
                    legacy_source_id=str(row.id),
                )
            )
            counters.outline_chapters_created += 1
        counters.outline_revisions_created += 1
    else:
        counters.skipped_existing += 1
    head.current_outline_revision_id = revision.id


def _migrate_chapters(db, project: Project, head: ProjectContentHead, counters: Counters) -> None:
    if not head.current_bible_revision_id or not head.current_outline_revision_id:
        return
    rows = (
        db.query(ChapterContent)
        .filter(ChapterContent.project_id == project.id)
        .order_by(ChapterContent.chapter_index, ChapterContent.created_at, ChapterContent.id)
        .all()
    )
    by_index: dict[int, list[ChapterContent]] = defaultdict(list)
    for row in rows:
        by_index[row.chapter_index].append(row)
    for chapter_index, items in sorted(by_index.items()):
        latest: ChapterRevision | None = None
        for row in items:
            existing = (
                db.query(ChapterRevision)
                .filter(
                    ChapterRevision.legacy_source_table == "chapter_contents",
                    ChapterRevision.legacy_source_id == str(row.id),
                )
                .first()
            )
            if existing:
                latest = existing
                counters.skipped_existing += 1
                continue
            source_digest = content_hash(
                {
                    "legacy_table": "chapter_contents",
                    "legacy_id": row.id,
                    "bible_revision_id": head.current_bible_revision_id,
                    "outline_revision_id": head.current_outline_revision_id,
                }
            )
            revision = ChapterRevision(
                project_id=project.id,
                chapter_index=chapter_index,
                parent_revision_id=latest.id if latest else None,
                bible_revision_id=head.current_bible_revision_id,
                outline_revision_id=head.current_outline_revision_id,
                revision_no=_next_no(db, ChapterRevision, project.id, chapter_index),
                source_hash=source_digest,
                content_hash=content_hash(row.content or ""),
                content=row.content or "",
                status="complete" if row.status in {"completed", "complete"} else (row.status or "complete"),
                legacy_source_table="chapter_contents",
                legacy_source_id=str(row.id),
                created_by=project.owner_id,
                created_at=row.created_at,
            )
            db.add(revision)
            db.flush()
            latest = revision
            counters.chapter_revisions_created += 1
        if latest:
            chapter_head = (
                db.query(ChapterHead)
                .filter(ChapterHead.project_id == project.id, ChapterHead.chapter_index == chapter_index)
                .first()
            )
            if chapter_head:
                chapter_head.current_revision_id = latest.id
            else:
                db.add(
                    ChapterHead(
                        project_id=project.id,
                        chapter_index=chapter_index,
                        current_revision_id=latest.id,
                    )
                )


def _migrate_vngraphs(db, project: Project, counters: Counters) -> None:
    rows = (
        db.query(VNGraph)
        .filter(VNGraph.project_id == project.id)
        .order_by(VNGraph.chapter_index, VNGraph.created_at, VNGraph.id)
        .all()
    )
    by_index: dict[int, list[VNGraph]] = defaultdict(list)
    for row in rows:
        by_index[row.chapter_index].append(row)
    for chapter_index, items in sorted(by_index.items()):
        chapter_head = (
            db.query(ChapterHead)
            .filter(ChapterHead.project_id == project.id, ChapterHead.chapter_index == chapter_index)
            .first()
        )
        if not chapter_head:
            continue
        latest: VNGraphRevision | None = None
        for row in items:
            existing = (
                db.query(VNGraphRevision)
                .filter(
                    VNGraphRevision.legacy_source_table == "vn_graphs",
                    VNGraphRevision.legacy_source_id == str(row.id),
                )
                .first()
            )
            if existing:
                latest = existing
                counters.skipped_existing += 1
                continue
            graph = row.graph_json or {}
            graph_digest = content_hash(graph)
            duplicate = (
                db.query(VNGraphRevision)
                .filter(
                    VNGraphRevision.project_id == project.id,
                    VNGraphRevision.chapter_index == chapter_index,
                    VNGraphRevision.graph_hash == graph_digest,
                )
                .first()
            )
            if duplicate:
                latest = duplicate
                counters.skipped_existing += 1
                continue
            revision = VNGraphRevision(
                project_id=project.id,
                chapter_index=chapter_index,
                chapter_revision_id=chapter_head.current_revision_id,
                parent_revision_id=latest.id if latest else None,
                revision_no=_next_no(db, VNGraphRevision, project.id, chapter_index),
                source_manifest_hash=content_hash({"legacy_table": "vn_graphs", "legacy_id": row.id}),
                graph_hash=graph_digest,
                graph_json=graph,
                schema_version="legacy",
                compiler_version="legacy",
                tachi_policy_version="legacy",
                status="complete" if row.status in {"completed", "complete"} else (row.status or "complete"),
                legacy_source_table="vn_graphs",
                legacy_source_id=str(row.id),
                created_at=row.created_at,
            )
            db.add(revision)
            db.flush()
            latest = revision
            counters.vngraph_revisions_created += 1
        if latest:
            graph_head = (
                db.query(VNGraphHead)
                .filter(VNGraphHead.project_id == project.id, VNGraphHead.chapter_index == chapter_index)
                .first()
            )
            if graph_head:
                graph_head.current_revision_id = latest.id
            else:
                db.add(
                    VNGraphHead(
                        project_id=project.id,
                        chapter_index=chapter_index,
                        current_revision_id=latest.id,
                    )
                )


def migrate(*, apply: bool) -> Counters:
    _assert_schema()
    counters = Counters()
    db = SessionLocal()
    try:
        for project in db.query(Project).order_by(Project.id).all():
            counters.projects_seen += 1
            head = _get_or_create_head(db, project.id, counters)
            _migrate_bibles(db, project, head, counters)
            _migrate_current_outline(db, project, head, counters)
            _migrate_chapters(db, project, head, counters)
            _migrate_vngraphs(db, project, counters)
            db.flush()
        if apply:
            db.commit()
        else:
            db.rollback()
        return counters
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="commit the additive backfill")
    mode.add_argument("--dry-run", action="store_true", help="run and roll back (default)")
    args = parser.parse_args()
    counters = migrate(apply=bool(args.apply))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **asdict(counters)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
