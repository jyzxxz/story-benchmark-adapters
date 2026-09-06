"""Verify v2 backfill pointers and source traceability without exposing content."""
from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal
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


def verify() -> tuple[dict, list[dict]]:
    db = SessionLocal()
    errors: list[dict] = []
    try:
        for project in db.query(Project).order_by(Project.id).all():
            head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project.id).first()
            legacy_bibles = db.query(StoryBible).filter(StoryBible.project_id == project.id).count()
            legacy_outlines = db.query(ChapterOutline).filter(ChapterOutline.project_id == project.id).count()
            legacy_chapters = db.query(ChapterContent).filter(ChapterContent.project_id == project.id).count()
            legacy_graphs = db.query(VNGraph).filter(VNGraph.project_id == project.id).count()
            if not head and any((legacy_bibles, legacy_outlines, legacy_chapters, legacy_graphs)):
                errors.append({"project_id": project.id, "code": "head.missing"})
                continue
            if legacy_bibles and not db.query(StoryBibleRevision).filter(
                StoryBibleRevision.project_id == project.id
            ).count():
                errors.append({"project_id": project.id, "code": "bible.unmigrated"})
            if legacy_outlines and not db.query(OutlineRevision).filter(
                OutlineRevision.project_id == project.id
            ).count():
                errors.append({"project_id": project.id, "code": "outline.unmigrated"})
            if head and head.current_outline_revision_id:
                selected = db.query(OutlineChapter).filter(
                    OutlineChapter.outline_revision_id == head.current_outline_revision_id
                ).count()
                unique_legacy = len(
                    {
                        row[0]
                        for row in db.query(ChapterOutline.chapter_index).filter(
                            ChapterOutline.project_id == project.id
                        )
                    }
                )
                if selected != unique_legacy:
                    errors.append(
                        {
                            "project_id": project.id,
                            "code": "outline.chapter_count_mismatch",
                            "expected": unique_legacy,
                            "actual": selected,
                        }
                    )
            for chapter_head in db.query(ChapterHead).filter(ChapterHead.project_id == project.id).all():
                if not db.query(ChapterRevision).filter(
                    ChapterRevision.id == chapter_head.current_revision_id,
                    ChapterRevision.project_id == project.id,
                ).first():
                    errors.append(
                        {
                            "project_id": project.id,
                            "chapter_index": chapter_head.chapter_index,
                            "code": "chapter.pointer_broken",
                        }
                    )
            for graph_head in db.query(VNGraphHead).filter(VNGraphHead.project_id == project.id).all():
                if not db.query(VNGraphRevision).filter(
                    VNGraphRevision.id == graph_head.current_revision_id,
                    VNGraphRevision.project_id == project.id,
                ).first():
                    errors.append(
                        {
                            "project_id": project.id,
                            "chapter_index": graph_head.chapter_index,
                            "code": "vngraph.pointer_broken",
                        }
                    )
        summary = {
            "projects": db.query(Project).count(),
            "project_heads": db.query(ProjectContentHead).count(),
            "bible_revisions": db.query(StoryBibleRevision).count(),
            "outline_revisions": db.query(OutlineRevision).count(),
            "chapter_revisions": db.query(ChapterRevision).count(),
            "vngraph_revisions": db.query(VNGraphRevision).count(),
            "errors": len(errors),
        }
        return summary, errors
    finally:
        db.close()


if __name__ == "__main__":
    summary, errors = verify()
    print(json.dumps({"summary": summary, "errors": errors}, ensure_ascii=False, indent=2))
    sys.exit(1 if errors else 0)
