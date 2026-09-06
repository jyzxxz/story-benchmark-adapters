from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.application import branch_service as branch_service_module
from app.application.hashing import content_hash
from app.database import Base, get_db
from app.models import Project, User
from app.models_v2 import (
    BranchCandidate,
    ChoiceDecision,
    OutlineRevision,
    OutboxEvent,
    ProjectPublication,
    ProjectRelease,
    ReadingSession,
    StateSnapshot,
    StoryBibleRevision,
    StoryPath,
    StoryNode,
)
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers.v2 import reading as reading_router
from app.workers import tasks as worker_tasks


@pytest.fixture
def branch_app(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(projects_router.router, prefix="/api/projects")
    app.include_router(reading_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield app, db
    finally:
        db.close()
        engine.dispose()


def _client(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post("/api/auth/register", json={"email": email, "password": "password123"})
    assert response.status_code == 201
    return client


def _seed_release(app: FastAPI, db):
    owner_email = "reader@example.com"
    owner = _client(app, owner_email)
    other = _client(app, "other-reader@example.com")
    created = owner.post(
        "/api/projects/",
        json={
            "title": "branch story",
            "characters": ["林夜"],
            "story_start": "开端",
            "story_end": "结局",
            "style": "现代",
            "pace": "medium",
        },
    )
    project_id = created.json()["id"]
    owner_id = db.query(User).filter(User.email == owner_email).one().id
    project = db.query(Project).filter(Project.id == project_id).one()
    project.visibility = "public"
    project.is_draft = False
    project.published_at = datetime.now(timezone.utc)

    checkpoint = StoryNode(
        project_id=project_id,
        node_type="checkpoint",
        checkpoint_key="cp-1",
        payload={"text": "choose"},
    )
    preview = StoryNode(
        project_id=project_id,
        node_type="paragraph",
        content_revision_id="preview-revision",
        payload={"text": "selected text"},
    )
    outsider = StoryNode(
        project_id=project_id,
        node_type="paragraph",
        payload={"text": "unreleased authoring node"},
    )
    db.add_all([checkpoint, preview, outsider])
    db.flush()
    candidate = BranchCandidate(
        project_id=project_id,
        checkpoint_node_id=checkpoint.id,
        option_key="left",
        preview_node_id=preview.id,
        preview_revision_id="preview-revision",
        state_delta={"route": "left", "relationship": {"lin": 1}},
        candidate_status="preview_ready",
    )
    db.add(candidate)
    db.flush()
    path = StoryPath(project_id=project_id, title="Root", status="active")
    db.add(path)
    db.flush()
    bible_content = {"world": "branch story"}
    bible = StoryBibleRevision(
        project_id=project_id,
        revision_no=1,
        source_hash=content_hash({"source": "test"}),
        content_hash=content_hash(bible_content),
        content_json=bible_content,
        status="ready",
        created_by=owner_id,
    )
    db.add(bible)
    db.flush()
    outline_content = {"chapters": []}
    outline = OutlineRevision(
        project_id=project_id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash=content_hash({"bible_revision_id": bible.id}),
        content_hash=content_hash(outline_content),
        status="approved",
        approved_at=datetime.now(timezone.utc),
        created_by=owner_id,
    )
    db.add(outline)
    db.flush()
    manifest = {
        "schema_version": "2.0",
        "project": {"id": project_id, "title": project.title},
        "bible_revision": {"id": bible.id, "content_hash": bible.content_hash},
        "outline_revision": {
            "id": outline.id,
            "content_hash": outline.content_hash,
            "chapters": [],
        },
        "chapters": [],
        "story": {
            "start_node_id": checkpoint.id,
            "nodes": [
                {"id": checkpoint.id, "node_type": "checkpoint", "payload": {"text": "choose"}},
                {"id": preview.id, "node_type": "paragraph", "payload": {"text": "selected text"}},
            ],
            "edges": [],
            "candidates": [
                {
                    "id": candidate.id,
                    "checkpoint_node_id": checkpoint.id,
                    "option_key": "left",
                    "preview_node_id": preview.id,
                    "preview_revision_id": "preview-revision",
                    "state_delta": {"route": "left", "relationship": {"lin": 1}},
                    "candidate_status": "preview_ready",
                }
            ],
        },
        "storage_object_ids": [],
    }
    release = ProjectRelease(
        project_id=project_id,
        version=1,
        status="published",
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        manifest_json=manifest,
        manifest_hash=content_hash(manifest),
        created_by=owner_id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    db.add(
        ProjectPublication(
            project_id=project_id,
            active_release_id=release.id,
            published_at=release.published_at,
            lock_version=1,
        )
    )
    db.commit()
    return owner, other, project_id, release, checkpoint, preview, outsider, candidate


def _start(owner: TestClient, project_id: int, release_id: str):
    response = owner.post(
        "/api/reading-sessions",
        json={"project_id": project_id, "release_id": release_id, "initial_state": {"coins": 1}},
    )
    assert response.status_code == 201
    return response.json()


def test_choice_is_persistent_idempotent_and_uses_frozen_release_candidate(branch_app):
    app, db = branch_app
    owner, _, project_id, release, checkpoint, preview, _, candidate = _seed_release(app, db)
    session = _start(owner, project_id, release.id)
    session_id = session["id"]

    # Live authoring data may change after publication; released behavior may not.
    candidate.state_delta = {"route": "hacked", "admin": True}
    candidate.preview_revision_id = "tampered-revision"
    db.commit()

    payload = {"checkpoint_node_id": checkpoint.id, "option_key": "left"}
    headers = {"Idempotency-Key": "choice-1", "If-Match": '"1"'}
    chosen = owner.post(f"/api/reading-sessions/{session_id}/choices", json=payload, headers=headers)
    assert chosen.status_code == 200
    body = chosen.json()
    assert body["created"] is True
    assert body["session"]["head_node_id"] == preview.id
    assert body["session"]["lock_version"] == 2
    assert body["decision"]["result_revision_id"] == "preview-revision"

    snapshot = db.query(StateSnapshot).filter(StateSnapshot.id == body["session"]["state_snapshot_id"]).one()
    assert snapshot.state_json == {"coins": 1, "route": "left", "relationship": {"lin": 1}}
    assert db.query(ChoiceDecision).filter(ChoiceDecision.session_id == session_id).count() == 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_type == "choice_decision").count() == 1

    replay = owner.post(f"/api/reading-sessions/{session_id}/choices", json=payload, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["decision"]["id"] == body["decision"]["id"]
    assert replay.json()["session"]["lock_version"] == 2
    assert db.query(ChoiceDecision).filter(ChoiceDecision.session_id == session_id).count() == 1
    assert db.query(OutboxEvent).filter(OutboxEvent.aggregate_type == "choice_decision").count() == 1

    refreshed = owner.get(f"/api/reading-sessions/{session_id}")
    assert refreshed.json()["head_node_id"] == preview.id
    assert owner.get(f"/api/reading-sessions/{session_id}/timeline").json()[0]["id"] == body["decision"]["id"]


def test_status_only_published_release_cannot_start_reading_session(branch_app):
    app, db = branch_app
    owner, _, project_id, release, *_ = _seed_release(app, db)
    inactive_manifest = {**deepcopy(release.manifest_json), "inactive_fixture": True}
    inactive = ProjectRelease(
        project_id=project_id,
        version=2,
        status="published",
        bible_revision_id=release.bible_revision_id,
        outline_revision_id=release.outline_revision_id,
        manifest_json=inactive_manifest,
        manifest_hash=content_hash(inactive_manifest),
        created_by=release.created_by,
        published_at=datetime.now(timezone.utc),
    )
    db.add(inactive)
    db.commit()

    response = owner.post(
        "/api/reading-sessions",
        json={"project_id": project_id, "release_id": inactive.id},
    )
    assert response.status_code == 404


def test_concurrent_same_idempotency_key_rechecks_committed_decision_after_cas_loss(
    branch_app,
    monkeypatch,
):
    app, db = branch_app
    owner, _, project_id, release, checkpoint, preview, _, candidate = _seed_release(app, db)
    reading = _start(owner, project_id, release.id)
    winner = ChoiceDecision(
        id="winner-choice",
        session_id=reading["id"],
        checkpoint_node_id=checkpoint.id,
        option_key="left",
        candidate_id=candidate.id,
        idempotency_key="concurrent-same-key",
        previous_node_id=checkpoint.id,
        result_node_id=preview.id,
        result_revision_id="preview-revision",
        created_at=datetime.now(timezone.utc),
    )
    lookups = iter([None, winner])
    monkeypatch.setattr(
        branch_service_module,
        "_find_choice_decision",
        lambda *_args, **_kwargs: next(lookups),
    )
    monkeypatch.setattr(
        branch_service_module,
        "_advance_session_head",
        lambda *_args, **_kwargs: False,
    )

    decision, created = branch_service_module.choose(
        db,
        session_id=reading["id"],
        user_id=reading["user_id"],
        checkpoint_node_id=checkpoint.id,
        option_key="left",
        idempotency_key="concurrent-same-key",
        expected_lock_version=1,
    )

    assert created is False
    assert decision.id == winner.id
    assert db.query(ChoiceDecision).filter_by(id="winner-choice").count() == 0


def test_selected_branch_worker_reports_text_ready_without_claiming_media_complete(
    branch_app,
    monkeypatch,
):
    app, db = branch_app
    owner, _, project_id, release, checkpoint, _, _, candidate = _seed_release(app, db)
    reading = _start(owner, project_id, release.id)
    chosen = owner.post(
        f'/api/reading-sessions/{reading["id"]}/choices',
        json={"checkpoint_node_id": checkpoint.id, "option_key": "left"},
        headers={"Idempotency-Key": "worker-status", "If-Match": "1"},
    )
    assert chosen.status_code == 200
    db.commit()

    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    monkeypatch.setattr(worker_tasks, "SessionLocal", factory)
    decision_id = chosen.json()["decision"]["id"]
    worker_tasks.complete_selected_branch.run(decision_id)
    worker_tasks.complete_selected_branch.run(decision_id)

    db.expire_all()
    assert db.query(BranchCandidate).filter_by(id=candidate.id).one().candidate_status == (
        "selected_text_ready"
    )


def test_if_match_conflicts_undo_fork_and_bookmark_scope(branch_app):
    app, db = branch_app
    owner, _, project_id, release, checkpoint, preview, outsider, _ = _seed_release(app, db)
    session = _start(owner, project_id, release.id)
    session_id = session["id"]
    payload = {"checkpoint_node_id": checkpoint.id, "option_key": "left"}

    assert owner.post(
        f"/api/reading-sessions/{session_id}/choices",
        json=payload,
        headers={"Idempotency-Key": "missing-precondition"},
    ).status_code == 428
    assert owner.post(
        f"/api/reading-sessions/{session_id}/choices",
        json=payload,
        headers={"If-Match": "1"},
    ).status_code == 400

    chosen = owner.post(
        f"/api/reading-sessions/{session_id}/choices",
        json=payload,
        headers={"Idempotency-Key": "first", "If-Match": "1"},
    ).json()
    stale = owner.post(
        f"/api/reading-sessions/{session_id}/choices",
        json=payload,
        headers={"Idempotency-Key": "second", "If-Match": "1"},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "session.version_conflict"

    assert owner.post(
        f"/api/reading-sessions/{session_id}/bookmarks",
        json={"node_id": outsider.id, "label": "not released"},
    ).status_code == 404
    bookmark = owner.post(
        f"/api/reading-sessions/{session_id}/bookmarks",
        json={"node_id": preview.id, "label": "favorite"},
    )
    assert bookmark.status_code == 201
    assert owner.get(f"/api/reading-sessions/{session_id}/bookmarks").json()[0]["node_id"] == preview.id

    forked = owner.post(
        f"/api/reading-sessions/{session_id}/fork",
        json={"decision_id": chosen["decision"]["id"]},
    )
    assert forked.status_code == 201
    assert forked.json()["head_node_id"] == checkpoint.id
    assert forked.json()["parent_session_id"] == session_id

    undone = owner.post(
        f"/api/reading-sessions/{session_id}/undo",
        headers={"If-Match": "2"},
    )
    assert undone.status_code == 200
    assert undone.json()["session"]["head_node_id"] == checkpoint.id
    assert undone.json()["session"]["lock_version"] == 3
    assert undone.json()["decision"]["undone_at"] is not None


def test_cross_user_session_operations_are_uniform_404_and_withdraw_disables_reading(branch_app):
    app, db = branch_app
    owner, other, project_id, release, checkpoint, preview, _, _ = _seed_release(app, db)
    session = _start(owner, project_id, release.id)
    session_id = session["id"]
    endpoints = [
        other.get(f"/api/reading-sessions/{session_id}"),
        other.get(f"/api/reading-sessions/{session_id}/timeline"),
        other.get(f"/api/reading-sessions/{session_id}/bookmarks"),
        other.post(
            f"/api/reading-sessions/{session_id}/choices",
            json={"checkpoint_node_id": checkpoint.id, "option_key": "left"},
            headers={"Idempotency-Key": "cross-user", "If-Match": "1"},
        ),
        other.post(
            f"/api/reading-sessions/{session_id}/undo",
            headers={"If-Match": "1"},
        ),
        other.post(f"/api/reading-sessions/{session_id}/fork", json={}),
        other.post(
            f"/api/reading-sessions/{session_id}/bookmarks",
            json={"node_id": preview.id},
        ),
    ]
    assert {response.status_code for response in endpoints} == {404}

    publication = db.query(ProjectPublication).filter_by(project_id=project_id).one()
    publication.active_release_id = None
    publication.published_at = None
    publication.lock_version += 1
    db.flush()
    release.status = "withdrawn"
    release.withdrawn_at = datetime.now(timezone.utc)
    db.commit()
    assert owner.get(f"/api/reading-sessions/{session_id}").status_code == 404
    assert owner.post(
        "/api/reading-sessions",
        json={"project_id": project_id, "release_id": release.id},
    ).status_code == 404


def test_branch_state_payloads_are_bounded(branch_app):
    app, db = branch_app
    owner, _, project_id, release, *_ = _seed_release(app, db)
    oversized = {"payload": "x" * (65 * 1024)}
    response = owner.post(
        "/api/reading-sessions",
        json={"project_id": project_id, "release_id": release.id, "initial_state": oversized},
    )
    assert response.status_code == 422
    assert db.query(ReadingSession).count() == 0


def test_merged_choice_state_cannot_exceed_snapshot_limit(branch_app):
    app, db = branch_app
    owner, _, project_id, release, checkpoint, *_ = _seed_release(app, db)
    manifest = deepcopy(release.manifest_json)
    manifest["story"]["candidates"][0]["state_delta"] = {
        "large_branch_value": "x" * (64 * 1024)
    }
    release.manifest_json = manifest
    release.manifest_hash = content_hash(manifest)
    db.commit()
    session = _start(owner, project_id, release.id)

    response = owner.post(
        f'/api/reading-sessions/{session["id"]}/choices',
        json={"checkpoint_node_id": checkpoint.id, "option_key": "left"},
        headers={"Idempotency-Key": "oversized-merge", "If-Match": "1"},
    )
    assert response.status_code == 413
    db.expire_all()
    persisted = db.query(ReadingSession).filter_by(id=session["id"]).one()
    assert persisted.lock_version == 1
    assert persisted.head_node_id == checkpoint.id
