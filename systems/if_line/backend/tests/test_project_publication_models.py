from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Project, User
from app.models_v2 import (
    OutlineRevision,
    ProjectPublication,
    ProjectRelease,
    StoryBibleRevision,
    StoryPath,
)
from app.orm_base import Base


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(
        email="publication-model@example.com",
        password_hash="hash",
        display_name="Publication Model",
    )
    db.add(user)
    db.flush()
    projects = [
        Project(
            owner_id=user.id,
            title=f"Project {number}",
            story_start="Start",
            story_end="End",
        )
        for number in (1, 2)
    ]
    db.add_all(projects)
    db.flush()

    revisions = []
    releases = []
    for number, project in enumerate(projects, start=1):
        root_path = StoryPath(
            project_id=project.id,
            title="Main",
            status="active",
        )
        db.add(root_path)
        db.flush()
        bible = StoryBibleRevision(
            project_id=project.id,
            revision_no=1,
            source_hash=str(number) * 64,
            content_hash=str(number + 2) * 64,
            content_json={},
        )
        db.add(bible)
        db.flush()
        outline = OutlineRevision(
            project_id=project.id,
            story_path_id=root_path.id,
            bible_revision_id=bible.id,
            revision_no=1,
            source_hash=str(number + 4) * 64,
            content_hash=str(number + 6) * 64,
        )
        db.add(outline)
        db.flush()
        release = ProjectRelease(
            project_id=project.id,
            version=1,
            status="published",
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            manifest_json={"project_id": project.id},
            manifest_hash=str(number + 8) * 64,
            authoring_fingerprint=str(number + 1) * 64,
            published_at=datetime.now(timezone.utc),
        )
        db.add(release)
        revisions.append((bible, outline))
        releases.append(release)
    db.commit()

    try:
        yield db, projects, revisions, releases
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_project_publication_allows_explicit_unpublished_state(seeded):
    db, projects, _revisions, _releases = seeded
    publication = ProjectPublication(project_id=projects[0].id)
    db.add(publication)
    db.commit()

    assert publication.active_release_id is None
    assert publication.published_at is None
    assert publication.lock_version == 1


def test_project_publication_accepts_release_from_same_project(seeded):
    db, projects, _revisions, releases = seeded
    activated_at = datetime.now(timezone.utc)
    publication = ProjectPublication(
        project_id=projects[0].id,
        active_release_id=releases[0].id,
        published_at=activated_at,
    )
    db.add(publication)
    db.commit()

    assert publication.active_release_id == releases[0].id


def test_project_publication_rejects_release_from_another_project(seeded):
    db, projects, _revisions, releases = seeded
    db.add(
        ProjectPublication(
            project_id=projects[0].id,
            active_release_id=releases[1].id,
            published_at=datetime.now(timezone.utc),
        )
    )

    with pytest.raises(ValueError, match="active published Release"):
        db.commit()


@pytest.mark.parametrize(
    ("active_release", "published_at"),
    [
        (True, None),
        (False, datetime(2026, 8, 8, tzinfo=timezone.utc)),
    ],
)
def test_project_publication_requires_pointer_and_timestamp_together(
    seeded,
    active_release,
    published_at,
):
    db, projects, _revisions, releases = seeded
    db.add(
        ProjectPublication(
            project_id=projects[0].id,
            active_release_id=releases[0].id if active_release else None,
            published_at=published_at,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()


def test_project_publication_requires_positive_lock_version(seeded):
    db, projects, _revisions, _releases = seeded
    db.add(ProjectPublication(project_id=projects[0].id, lock_version=0))

    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("status", ["published", "superseded", "withdrawn"])
def test_project_release_accepts_target_lifecycle_states(seeded, status):
    db, projects, revisions, _releases = seeded
    bible, outline = revisions[0]
    release = ProjectRelease(
        project_id=projects[0].id,
        version=2,
        status=status,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        manifest_json={"status": status},
        manifest_hash=(status[0] * 63) + "0",
    )
    db.add(release)
    db.commit()

    assert release.status == status


def test_project_release_rejects_legacy_draft_state(seeded):
    db, projects, revisions, _releases = seeded
    bible, outline = revisions[0]
    db.add(
        ProjectRelease(
            project_id=projects[0].id,
            version=2,
            status="draft",
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            manifest_json={},
            manifest_hash="f" * 64,
        )
    )

    with pytest.raises(IntegrityError):
        db.commit()
