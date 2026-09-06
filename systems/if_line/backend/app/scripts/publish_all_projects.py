"""Publish every story currently stored on the server.

Legacy mutable rows are snapshotted into the v2 revision model before a
ProjectRelease is created.  Existing owner assignments are preserved.  The
script is intentionally idempotent: projects with a valid current published
release are only normalized to public flags.
"""
from __future__ import annotations

import json
from datetime import datetime

from app.application.release_service import create_release
from app.application.revision_service import (
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
    create_outline_revision,
)
from app.database import SessionLocal
from app.models import ChapterContent, ChapterOutline, Project, StoryBible, User
from app.models_v2 import ProjectContentHead, ProjectRelease, StoryNode


SYSTEM_EMAIL = "system-public-release@ifline.local"


def _system_user(db) -> User:
    user = db.query(User).filter(User.email == SYSTEM_EMAIL).first()
    if user:
        return user
    user = User(
        email=SYSTEM_EMAIL,
        password_hash="system-login-disabled$public-release",
        display_name="公共作品发布器",
        is_active=False,
        quota_total=0,
        quota_daily=0,
        quota_used_total=0,
        quota_used_daily=0,
        quota_reset_at=datetime.utcnow(),
    )
    db.add(user)
    db.flush()
    return user


def _valid_release(db, project_id: int) -> ProjectRelease | None:
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not head or not head.published_release_id:
        return None
    return (
        db.query(ProjectRelease)
        .filter(
            ProjectRelease.id == head.published_release_id,
            ProjectRelease.project_id == project_id,
            ProjectRelease.status == "published",
            ProjectRelease.withdrawn_at.is_(None),
        )
        .first()
    )


def _bible_payload(row: StoryBible | None, project: Project) -> dict:
    if not row:
        return {
            "worldview": project.story_start,
            "characters": project.characters or [],
            "main_conflict": project.story_end,
            "source": "legacy-project-fallback",
        }
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
        "raw_json": row.raw_json or {},
    }


def _snapshot_project(db, project: Project, publisher: User) -> ProjectRelease:
    legacy_bible = db.query(StoryBible).filter(StoryBible.project_id == project.id).first()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content=_bible_payload(legacy_bible, project),
        source={"origin": "publish-all", "legacy_bible_id": legacy_bible.id if legacy_bible else None},
        user_id=publisher.id,
    )

    contents = (
        db.query(ChapterContent)
        .filter(ChapterContent.project_id == project.id)
        .order_by(ChapterContent.chapter_index, ChapterContent.version.desc(), ChapterContent.id.desc())
        .all()
    )
    latest_by_index: dict[int, ChapterContent] = {}
    for item in contents:
        if item.chapter_index not in latest_by_index and (item.content or "").strip():
            latest_by_index[item.chapter_index] = item
    if not latest_by_index:
        raise RuntimeError("没有可发布的章节正文")

    outlines = {
        item.chapter_index: item
        for item in db.query(ChapterOutline)
        .filter(ChapterOutline.project_id == project.id)
        .order_by(ChapterOutline.chapter_index, ChapterOutline.id.desc())
        .all()
    }
    outline_rows = []
    for chapter_index, content in sorted(latest_by_index.items()):
        source = outlines.get(chapter_index)
        outline_rows.append(
            {
                "chapter_index": chapter_index,
                "title": source.title if source and source.title else f"第 {chapter_index} 章",
                "summary": source.summary if source and source.summary else (content.content or "")[:240],
                "conflict": source.conflict if source else None,
                "characters": source.characters if source and source.characters else [],
                "scene": source.scene if source else None,
                "emotion": source.emotion if source else None,
                "visual_keywords": source.visual_keywords if source and source.visual_keywords else [],
            }
        )
    outline = create_outline_revision(
        db,
        project_id=project.id,
        chapters=outline_rows,
        user_id=publisher.id,
        bible_revision_id=bible.id,
    )
    activate_outline_revision(db, project.id, outline.id, approve=True)
    db.flush()

    chapter_revisions = []
    for chapter_index, content in sorted(latest_by_index.items()):
        revision = create_chapter_revision(
            db,
            project_id=project.id,
            chapter_index=chapter_index,
            content=content.content,
            user_id=publisher.id,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
        )
        chapter_revisions.append(revision)
    db.flush()

    # Release validation requires one unambiguous start node for the current
    # chapter snapshot.  It is an immutable anchor, not a rewrite of prose.
    db.add(
        StoryNode(
            project_id=project.id,
            node_type="public_story_anchor",
            content_revision_id=chapter_revisions[0].id,
            payload={
                "title": project.title,
                "chapter_index": chapter_revisions[0].chapter_index,
                "text_excerpt": chapter_revisions[0].content[:1200],
            },
        )
    )
    db.flush()
    release, _ = create_release(
        db,
        project_id=project.id,
        user_id=publisher.id,
        publish=True,
    )
    return release


def publish_all() -> dict[str, object]:
    db = SessionLocal()
    published: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    try:
        publisher = _system_user(db)
        db.commit()
        project_ids = [row[0] for row in db.query(Project.id).order_by(Project.id).all()]
        for project_id in project_ids:
            try:
                project = db.query(Project).filter(Project.id == project_id).one()
                release = _valid_release(db, project.id)
                created = False
                if not release:
                    release = _snapshot_project(db, project, publisher)
                    created = True
                project.visibility = "public"
                project.published_at = release.published_at or datetime.utcnow()
                db.commit()
                published.append(
                    {
                        "project_id": project.id,
                        "title": project.title,
                        "release_id": release.id,
                        "created_release": created,
                    }
                )
            except Exception as exc:
                db.rollback()
                failed.append(
                    {
                        "project_id": project_id,
                        "error_type": type(exc).__name__,
                        "error": getattr(exc, "detail", None) or str(exc) or repr(exc),
                    }
                )
        return {"published": published, "failed": failed}
    finally:
        db.close()


if __name__ == "__main__":
    print(json.dumps(publish_all(), ensure_ascii=False, sort_keys=True))
