from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy import update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app import auth as auth_module
from app.application.hashing import content_hash
from app.application.revision_service import (
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
)
from app.application.story_outline_service import create_story_path_outline_revision
from app.application.usage_service import reserve_usage
from app.database import Base, get_db
from app.models import ProjectLike, User
from app.models_v2 import (
    ChapterScriptHead,
    ChapterScriptRevision,
    GenerationTask,
    ProjectPublication,
    ProjectRelease,
    StoryPath,
    UsageLedgerEntry,
    VNGraphHead,
    VNGraphRevision,
)
from app.quota import reset_daily_quota_if_needed
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers import social as social_router
from app.routers.v2 import releases as releases_v2


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def app(db_session, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(projects_router.router, prefix="/api/projects")
    app.include_router(releases_v2.owner_router, prefix="/api")
    app.include_router(social_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db_session
    return app


def _publish_project(db, owner: TestClient, project_id: int, owner_email: str) -> None:
    user = db.query(User).filter(User.email == owner_email).one()
    bible = create_bible_revision(
        db,
        project_id=project_id,
        content={"world": "social test"},
        source={"origin": "test"},
        user_id=user.id,
    )
    path = StoryPath(project_id=project_id, title="Root", status="active")
    db.add(path)
    db.flush()
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        user_id=user.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": 1,
                "title": "第一章",
                "summary": "开场",
            }
        ],
    )
    activate_outline_revision(db, project_id, outline.id, approve=True)
    chapter = create_chapter_revision(
        db,
        project_id=project_id,
        chapter_index=1,
        content="用于发布的章节。",
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        user_id=user.id,
    )
    script_json = {
        "schema_version": "chapter-script-ir-v1",
        "scenes": [],
        "spans": [{"id": "span-1", "text": chapter.content}],
        "resources": [],
    }
    script = ChapterScriptRevision(
        project_id=project_id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=1,
        source_hash=content_hash({"chapter_revision_id": chapter.id}),
        script_hash=content_hash(script_json),
        script_json=script_json,
        coverage_json={"coverage_ratio": 1.0},
        schema_version="chapter-script-ir-v1",
        generator_version="test-v1",
        status="complete",
        created_by=user.id,
    )
    db.add(script)
    db.flush()
    db.add(
        ChapterScriptHead(
            project_id=project_id,
            chapter_index=1,
            chapter_revision_id=chapter.id,
            current_revision_id=script.id,
        )
    )
    graph_json = {"Meta": {"version": "fixture-v1"}, "Nodes": [{"id": "start"}]}
    graph = VNGraphRevision(
        project_id=project_id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        revision_no=1,
        source_manifest_hash=content_hash({"script_revision_id": script.id}),
        graph_hash=content_hash(graph_json),
        graph_json=graph_json,
        schema_version="2.0",
        compiler_version="test-v1",
        tachi_policy_version="test-v1",
        status="complete",
    )
    db.add(graph)
    db.flush()
    db.add(
        VNGraphHead(
            project_id=project_id,
            chapter_index=1,
            script_revision_id=script.id,
            current_revision_id=graph.id,
        )
    )
    db.commit()
    response = owner.post(f"/api/projects/{project_id}/releases", json={"publish": True})
    assert response.status_code == 201
    release = db.query(ProjectRelease).filter_by(id=response.json()["id"]).one()
    db.add(
        ProjectPublication(
            project_id=project_id,
            active_release_id=release.id,
            published_at=release.published_at,
        )
    )
    db.commit()


def _usage_task(db, user: User, *, key: str, amount: int) -> GenerationTask:
    parameters = {"chapter_index": 1}
    task = GenerationTask(
        user_id=user.id,
        kind="chapter_generate",
        status="queued",
        idempotency_key=key,
        source_refs={},
        result_refs={},
        parameters=parameters,
        parameters_hash=content_hash(parameters),
        estimated_cost=amount,
    )
    db.add(task)
    db.flush()
    return task


def _client(app: FastAPI, email: str, display_name: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123", "display_name": display_name},
    )
    assert response.status_code == 201
    return client


def _project_payload(title: str, *, visibility: str = "private", is_draft: bool = True) -> dict:
    return {
        "title": title,
        "characters": ["林夜"],
        "story_start": "开端",
        "story_end": "结局",
        "style": "古风",
        "pace": "medium",
        "visibility": visibility,
        "is_draft": is_draft,
    }


def test_like_public_project_creates_owner_notification_once(app, db_session):
    owner = _client(app, "like-owner@example.com", "Owner")
    liker = _client(app, "like-user@example.com", "Liker")
    guest = TestClient(app)

    created = owner.post("/api/projects/", json=_project_payload("like story"))
    project_id = created.json()["id"]

    assert guest.post(f"/api/projects/{project_id}/likes").status_code == 401
    assert liker.post(f"/api/projects/{project_id}/likes").status_code == 404

    _publish_project(db_session, owner, project_id, "like-owner@example.com")
    liked = liker.post(f"/api/projects/{project_id}/likes")
    assert liked.status_code == 200
    assert liked.json() == {"project_id": project_id, "liked": True, "like_count": 1}

    repeated = liker.post(f"/api/projects/{project_id}/likes")
    assert repeated.status_code == 200
    assert repeated.json()["like_count"] == 1
    assert db_session.query(ProjectLike).filter(ProjectLike.project_id == project_id).count() == 1

    owner_notifications = owner.get("/api/notifications").json()
    assert owner_notifications["unread_count"] == 1
    assert len(owner_notifications["items"]) == 1
    assert owner_notifications["items"][0]["notification_type"] == "project_like"
    assert owner_notifications["items"][0]["actor_display_name"] == "Liker"

    assert guest.get(f"/api/projects/{project_id}/social").json()["liked_by_me"] is False
    assert liker.get(f"/api/projects/{project_id}/social").json()["liked_by_me"] is True

    unliked = liker.delete(f"/api/projects/{project_id}/likes")
    assert unliked.status_code == 200
    assert unliked.json() == {"project_id": project_id, "liked": False, "like_count": 0}


def test_comment_public_project_notifies_owner_and_is_reader_visible(app, db_session):
    owner = _client(app, "comment-owner@example.com", "Author")
    commenter = _client(app, "comment-user@example.com", "Reader")
    guest = TestClient(app)

    created = owner.post(
        "/api/projects/",
        json=_project_payload("comment story", visibility="public", is_draft=False),
    )
    project_id = created.json()["id"]
    _publish_project(db_session, owner, project_id, "comment-owner@example.com")

    comment = commenter.post(
        f"/api/projects/{project_id}/comments",
        json={"body": "  很喜欢这一章  "},
    )
    assert comment.status_code == 201
    assert comment.json()["body"] == "很喜欢这一章"
    assert comment.json()["display_name"] == "Reader"

    comments = guest.get(f"/api/projects/{project_id}/comments").json()
    assert [item["body"] for item in comments] == ["很喜欢这一章"]

    owner_notifications = owner.get("/api/notifications").json()
    assert owner_notifications["unread_count"] == 1
    notification = owner_notifications["items"][0]
    assert notification["notification_type"] == "project_comment"
    assert notification["comment_id"] == comment.json()["id"]
    assert notification["actor_display_name"] == "Reader"

    marked = owner.patch(f"/api/notifications/{notification['id']}/read")
    assert marked.status_code == 200
    assert marked.json()["read_at"] is not None
    assert owner.get("/api/notifications").json()["unread_count"] == 0
    assert commenter.get("/api/notifications").json()["items"] == []


def test_private_project_comments_are_owner_only_and_do_not_self_notify(app):
    owner = _client(app, "private-comment-owner@example.com", "Owner")
    other = _client(app, "private-comment-other@example.com", "Other")
    guest = TestClient(app)

    created = owner.post("/api/projects/", json=_project_payload("private comments"))
    project_id = created.json()["id"]

    assert guest.get(f"/api/projects/{project_id}/comments").status_code == 404
    assert other.post(
        f"/api/projects/{project_id}/comments",
        json={"body": "看不到"},
    ).status_code == 404

    own_comment = owner.post(
        f"/api/projects/{project_id}/comments",
        json={"body": "自用备注"},
    )
    assert own_comment.status_code == 201
    assert owner.get("/api/notifications").json()["items"] == []


def test_quota_snapshot_usage_ledger_and_limits(app, db_session):
    client = _client(app, "quota@example.com", "Quota")
    user = db_session.query(User).filter(User.email == "quota@example.com").one()
    user.quota_total = 10
    user.quota_daily = 5
    user.quota_used_total = 1
    user.quota_used_daily = 4
    user.quota_reset_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()

    reset_snapshot = client.get("/api/users/me/quota")
    assert reset_snapshot.status_code == 200
    assert reset_snapshot.json()["quota_used_daily"] == 0

    task = _usage_task(db_session, user, key="quota-success", amount=3)
    reserve_usage(db_session, task, 3)
    db_session.commit()

    snapshot = client.get("/api/users/me/quota").json()
    assert snapshot["quota_used_total"] == 4
    assert snapshot["quota_used_daily"] == 3
    assert snapshot["quota_remaining_total"] == 6
    assert snapshot["quota_remaining_daily"] == 2

    usage = client.get("/api/users/me/usage").json()
    assert len(usage) == 1
    assert usage[0]["task_kind"] == "chapter_generate"
    assert usage[0]["project_id"] is None
    assert usage[0]["entry_type"] == "reserve"
    assert usage[0]["amount"] == 3.0
    assert usage[0]["event_metadata"] == {"kind": "chapter_generate"}

    failed_task = _usage_task(db_session, user, key="quota-insufficient", amount=3)
    with pytest.raises(HTTPException) as exc_info:
        reserve_usage(db_session, failed_task, 3)
    assert exc_info.value.status_code == 402
    assert db_session.query(UsageLedgerEntry).count() == 1


def test_usage_reservation_uses_database_current_counters(app, db_session):
    _client(app, "quota-stale@example.com", "Quota")
    user = db_session.query(User).filter(User.email == "quota-stale@example.com").one()
    user.quota_total = 5
    user.quota_daily = 5
    user.quota_used_total = 0
    user.quota_used_daily = 0
    user.quota_reset_at = datetime.utcnow()
    db_session.commit()

    db_session.execute(
        update(User)
        .where(User.id == user.id)
        .values(quota_used_total=4, quota_used_daily=4)
        .execution_options(synchronize_session=False)
    )
    task = _usage_task(db_session, user, key="quota-stale-counter", amount=2)
    with pytest.raises(HTTPException) as exc_info:
        reserve_usage(db_session, task, 2)

    assert exc_info.value.status_code == 402
    db_session.refresh(user)
    assert user.quota_used_total == 4
    assert user.quota_used_daily == 4
    assert db_session.query(UsageLedgerEntry).count() == 0


def test_quota_read_reset_uses_database_current_counters(app, db_session):
    client = _client(app, "quota-read-stale@example.com", "Quota")
    user = db_session.query(User).filter(User.email == "quota-read-stale@example.com").one()
    user.quota_total = 10
    user.quota_daily = 5
    user.quota_used_total = 0
    user.quota_used_daily = 4
    user.quota_reset_at = datetime.utcnow() - timedelta(days=1)
    db_session.commit()

    db_session.execute(
        update(User)
        .where(User.id == user.id)
        .values(
            quota_used_total=3,
            quota_used_daily=3,
            quota_reset_at=datetime.utcnow(),
        )
        .execution_options(synchronize_session=False)
    )

    changed = reset_daily_quota_if_needed(db_session, user)
    assert changed is False
    assert user.quota_used_total == 3
    assert user.quota_used_daily == 3

    snapshot = client.get("/api/users/me/quota")
    assert snapshot.status_code == 200
    assert snapshot.json()["quota_used_total"] == 3
    assert snapshot.json()["quota_used_daily"] == 3
