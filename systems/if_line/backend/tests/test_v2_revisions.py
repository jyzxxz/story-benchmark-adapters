import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.revision_service import (
    activate_bible_revision,
    activate_chapter_revision,
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
    create_outline_revision,
    project_readiness,
)
from app.database import Base
from app.models import Project, User
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _owner_and_project(db, suffix: str):
    user = User(
        email=f"revision-{suffix}@example.com",
        password_hash="hash",
        display_name="revision owner",
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title=f"revision project {suffix}",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    return user, project


def _chapters():
    return [
        {
            "chapter_index": 1,
            "title": "第一章",
            "summary": "相遇",
            "conflict": "误会",
            "characters": ["甲", "乙"],
            "scene": "车站",
            "emotion": "紧张",
            "visual_keywords": ["雨夜站台"],
        }
    ]


def test_revisions_are_immutable_and_can_activate_older_versions(db):
    user, project = _owner_and_project(db, "history")
    bible_one = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "one"},
        source={"origin": "manual"},
        user_id=user.id,
    )
    outline_one = create_outline_revision(
        db,
        project_id=project.id,
        chapters=_chapters(),
        user_id=user.id,
        bible_revision_id=bible_one.id,
    )
    activate_outline_revision(db, project.id, outline_one.id, approve=True)
    chapter_one = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="相同正文",
        user_id=user.id,
        bible_revision_id=bible_one.id,
        outline_revision_id=outline_one.id,
    )

    bible_two = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "two"},
        source={"origin": "manual"},
        user_id=user.id,
    )
    outline_two = create_outline_revision(
        db,
        project_id=project.id,
        chapters=_chapters(),
        user_id=user.id,
        bible_revision_id=bible_two.id,
    )
    chapter_two = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="相同正文",
        user_id=user.id,
        bible_revision_id=bible_two.id,
        outline_revision_id=outline_two.id,
    )
    db.flush()

    assert bible_one.id != bible_two.id
    assert outline_one.id != outline_two.id
    assert chapter_one.id != chapter_two.id
    assert db.query(StoryBibleRevision).filter_by(project_id=project.id).count() == 2
    assert db.query(OutlineRevision).filter_by(project_id=project.id).count() == 2
    assert db.query(ChapterRevision).filter_by(project_id=project.id, chapter_index=1).count() == 2
    assert chapter_two.parent_revision_id == chapter_one.id
    assert chapter_one.content == "相同正文"
    assert chapter_one.bible_revision_id == bible_one.id

    activate_bible_revision(db, project.id, bible_one.id)
    activate_outline_revision(db, project.id, outline_one.id, approve=False)
    activate_chapter_revision(db, project.id, 1, chapter_one.id)
    db.flush()
    project_head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    chapter_head = db.query(ChapterHead).filter_by(project_id=project.id, chapter_index=1).one()
    assert project_head.current_bible_revision_id == bible_one.id
    assert project_head.current_outline_revision_id == outline_one.id
    assert chapter_head.current_revision_id == chapter_one.id


def test_readiness_reports_upstream_staleness_and_recovers_after_rollback(db):
    user, project = _owner_and_project(db, "stale")
    bible_one = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "one"},
        source={"origin": "manual"},
        user_id=user.id,
    )
    outline_one = create_outline_revision(
        db,
        project_id=project.id,
        chapters=_chapters(),
        user_id=user.id,
    )
    chapter_one = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="第一版正文",
        user_id=user.id,
    )
    db.flush()
    assert project_readiness(db, project.id)["ready"] is True

    bible_two = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "two"},
        source={"origin": "manual"},
        user_id=user.id,
    )
    stale = project_readiness(db, project.id)
    stale_codes = {issue["code"] for issue in stale["issues"]}
    assert stale["ready"] is False
    assert "outline.stale_bible" in stale_codes
    assert "chapter.stale_bible" in stale_codes

    outline_two = create_outline_revision(
        db,
        project_id=project.id,
        chapters=_chapters(),
        user_id=user.id,
        bible_revision_id=bible_two.id,
    )
    stale_outline = project_readiness(db, project.id)
    assert "chapter.stale_outline" in {issue["code"] for issue in stale_outline["issues"]}

    chapter_two = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="第一版正文",
        user_id=user.id,
        bible_revision_id=bible_two.id,
        outline_revision_id=outline_two.id,
    )
    db.flush()
    assert chapter_two.id != chapter_one.id
    assert project_readiness(db, project.id)["ready"] is True

    activate_bible_revision(db, project.id, bible_one.id)
    activate_outline_revision(db, project.id, outline_one.id, approve=False)
    activate_chapter_revision(db, project.id, 1, chapter_one.id)
    db.flush()
    assert project_readiness(db, project.id)["ready"] is True


def test_chapter_revision_rejects_cross_project_dependencies(db):
    user_one, project_one = _owner_and_project(db, "owner-one")
    user_two, project_two = _owner_and_project(db, "owner-two")
    bible_one = create_bible_revision(
        db,
        project_id=project_one.id,
        content={"world": "one"},
        source={"origin": "manual"},
        user_id=user_one.id,
    )
    outline_one = create_outline_revision(
        db,
        project_id=project_one.id,
        chapters=_chapters(),
        user_id=user_one.id,
    )
    bible_two = create_bible_revision(
        db,
        project_id=project_two.id,
        content={"world": "two"},
        source={"origin": "manual"},
        user_id=user_two.id,
    )
    outline_two = create_outline_revision(
        db,
        project_id=project_two.id,
        chapters=_chapters(),
        user_id=user_two.id,
    )
    db.flush()

    with pytest.raises(HTTPException) as exc:
        create_chapter_revision(
            db,
            project_id=project_one.id,
            chapter_index=1,
            content="非法跨项目正文",
            user_id=user_one.id,
            bible_revision_id=bible_two.id,
            outline_revision_id=outline_two.id,
        )
    assert exc.value.status_code in {404, 409}

    # A matching Bible with an Outline derived from another Bible is also invalid.
    with pytest.raises(HTTPException) as exc:
        create_chapter_revision(
            db,
            project_id=project_one.id,
            chapter_index=1,
            content="依赖不一致正文",
            user_id=user_one.id,
            bible_revision_id=bible_one.id,
            outline_revision_id=outline_two.id,
        )
    assert exc.value.status_code in {404, 409}
    assert outline_one.project_id == project_one.id


def test_readiness_reports_missing_outline_chapters(db):
    user, project = _owner_and_project(db, "missing")
    create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "one"},
        source={"origin": "manual"},
        user_id=user.id,
    )
    chapters = _chapters() + [
        {
            "chapter_index": 2,
            "title": "第二章",
            "summary": "继续",
            "characters": ["甲"],
            "scene": "街道",
            "visual_keywords": [],
        }
    ]
    create_outline_revision(
        db,
        project_id=project.id,
        chapters=chapters,
        user_id=user.id,
    )
    create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="只有第一章",
        user_id=user.id,
    )
    db.flush()

    readiness = project_readiness(db, project.id)
    assert readiness["ready"] is False
    assert {
        (issue["code"], issue["artifact"])
        for issue in readiness["issues"]
    } >= {("chapter.missing", "chapter:2")}
