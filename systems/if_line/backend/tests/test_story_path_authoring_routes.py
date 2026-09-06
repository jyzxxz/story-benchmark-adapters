from __future__ import annotations

from collections import Counter

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.auth import get_current_user
from app.core.config import AppSettings
from app.database import Base, get_db
from app.main import create_app
from app.models import Project, User
from app.models_v2 import (
    ChapterSlot,
    GenerationTask,
    OutlineRevision,
    ProjectContentHead,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)


R61_ROUTES = {
    ("POST", "/api/projects"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
    ("PATCH", "/api/projects/{project_id}"),
    ("DELETE", "/api/projects/{project_id}"),
    ("GET", "/api/projects/{project_id}/publication-readiness"),
    ("GET", "/api/projects/{project_id}/metrics"),
    ("GET", "/api/projects/{project_id}/metrics/artifacts"),
    ("POST", "/api/projects/{project_id}/bible-generations"),
    ("GET", "/api/projects/{project_id}/bible-revisions"),
    ("POST", "/api/projects/{project_id}/bible-revisions"),
    ("GET", "/api/projects/{project_id}/bible-head"),
    ("PUT", "/api/projects/{project_id}/bible-head"),
    ("GET", "/api/projects/{project_id}/story-paths"),
    ("POST", "/api/projects/{project_id}/story-paths"),
    ("GET", "/api/story-paths/{story_path_id}"),
    ("PATCH", "/api/story-paths/{story_path_id}"),
    ("POST", "/api/story-paths/{story_path_id}/outline-generations"),
    ("GET", "/api/story-paths/{story_path_id}/outline-revisions"),
    ("POST", "/api/story-paths/{story_path_id}/outline-revisions"),
    ("GET", "/api/story-paths/{story_path_id}/outline-head"),
    ("PUT", "/api/story-paths/{story_path_id}/outline-head"),
    ("GET", "/api/story-paths/{story_path_id}/chapters"),
    ("POST", "/api/story-paths/{story_path_id}/chapters"),
    ("POST", "/api/story-paths/{story_path_id}/chapter-generation-batches"),
    ("POST", "/api/path-chapters/{path_chapter_id}/generations"),
    ("GET", "/api/path-chapters/{path_chapter_id}/revisions"),
    ("POST", "/api/path-chapters/{path_chapter_id}/revisions"),
    ("GET", "/api/path-chapters/{path_chapter_id}/head"),
    ("PUT", "/api/path-chapters/{path_chapter_id}/head"),
    ("GET", "/api/chapter-revisions/{chapter_revision_id}"),
    ("POST", "/api/chapter-revisions/{chapter_revision_id}/script-revisions"),
    ("POST", "/api/chapter-script-revisions/{script_revision_id}/vn-graph-revisions"),
    ("POST", "/api/projects/{project_id}/finalize-publish"),
}


@pytest.fixture()
def authoring_api(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    owner = User(
        email="r61-owner@example.com",
        password_hash="hash",
        display_name="R61 owner",
        quota_total=10_000,
        quota_daily=10_000,
    )
    outsider = User(
        email="r61-outsider@example.com",
        password_hash="hash",
        display_name="R61 outsider",
        quota_total=10_000,
        quota_daily=10_000,
    )
    db.add_all([owner, outsider])
    db.commit()

    app = create_app(
        AppSettings(
            _env_file=None,
            app_env="test",
            static_dir=tmp_path / "static",
        )
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: owner
    try:
        yield app, TestClient(app), db, owner, outsider
    finally:
        db.close()
        engine.dispose()


def _project(client: TestClient, title: str = "Path authoring") -> dict:
    response = client.post(
        "/api/projects",
        json={
            "title": title,
            "characters": [{"name": "Lin"}],
            "story_start": "Start",
            "story_end": "End",
            "style": "cinematic",
            "pace": "medium",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _select_bible(client: TestClient, project_id: int) -> dict:
    revision = client.post(
        f"/api/projects/{project_id}/bible-revisions",
        json={"content_json": {"worldview": "Frozen world"}},
    )
    assert revision.status_code == 201, revision.text
    # head 为空时首个修订自动采用（lock 已推进），PUT 前先读当前锁版本。
    head = client.get(f"/api/projects/{project_id}/bible-head")
    assert head.status_code == 200, head.text
    selected = client.put(
        f"/api/projects/{project_id}/bible-head",
        headers={"If-Match": f'"{head.json()["lock_version"]}"'},
        json={"revision_id": revision.json()["id"]},
    )
    assert selected.status_code == 200, selected.text
    return revision.json()


def _select_outline(
    client: TestClient,
    *,
    story_path_id: str,
    bible_revision_id: str,
    count: int = 2,
) -> dict:
    revision = client.post(
        f"/api/story-paths/{story_path_id}/outline-revisions",
        json={
            "bible_revision_id": bible_revision_id,
            "chapters": [
                {
                    "display_index": index,
                    "title": f"Chapter {index}",
                    "summary": f"Summary {index}",
                }
                for index in range(1, count + 1)
            ],
        },
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["status"] == "ready"
    listed = client.get(f"/api/story-paths/{story_path_id}/outline-revisions")
    assert listed.status_code == 200, listed.text
    assert listed.json()[0]["status"] == "ready"
    # head 为空时首个大纲修订自动采用（lock 已推进），PUT 前先读当前锁版本。
    head = client.get(f"/api/story-paths/{story_path_id}/outline-head")
    assert head.status_code == 200, head.text
    selected = client.put(
        f"/api/story-paths/{story_path_id}/outline-head",
        headers={"If-Match": f'"{head.json()["lock_version"]}"'},
        json={"revision_id": revision.json()["id"]},
    )
    assert selected.status_code == 200, selected.text
    return revision.json()


def test_r61_routes_are_registered_once_by_replacement_modules(authoring_api):
    app, _client, _db, _owner, _outsider = authoring_api
    route_keys = [
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method not in {"HEAD", "OPTIONS"}
    ]
    assert R61_ROUTES <= set(route_keys)
    assert all(Counter(route_keys)[key] == 1 for key in R61_ROUTES)
    assert {
        route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
        and any((method, route.path) in R61_ROUTES for method in route.methods or set())
    } == {
        "app.routers.v2.authoring_projects",
        "app.routers.v2.authoring_bible",
        "app.routers.v2.authoring_story_paths",
        "app.routers.v2.authoring_outlines",
        "app.routers.v2.authoring_chapters",
        "app.routers.v2.authoring_scripts",
        "app.routers.v2.authoring_vn_graphs",
        "app.routers.v2.authoring_releases",
    }


def test_project_create_builds_root_path_and_computed_projection(authoring_api):
    _app, client, db, owner, _outsider = authoring_api
    created = _project(client)
    project_id = created["id"]

    assert created["root_story_path_id"]
    assert created["authoring"]["stage"] == "bible"
    assert created["publication"] == {
        "state": "unpublished",
        "active_release_id": None,
        "published_at": None,
        "lock_version": 1,
    }
    project = db.query(Project).filter_by(id=project_id, owner_id=owner.id).one()
    root = db.query(StoryPath).filter_by(project_id=project_id, parent_path_id=None).one()
    assert root.id == created["root_story_path_id"]
    assert db.query(ProjectContentHead).filter_by(project_id=project_id).count() == 1
    assert db.query(StoryPathOutlineHead).filter_by(story_path_id=root.id).count() == 1
    assert project.visibility == "private"

    updated = client.patch(
        f"/api/projects/{project_id}",
        json={"title": "Updated title", "pace": "fast"},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert client.get("/api/projects?publication_state=published").json() == []
    assert [item["id"] for item in client.get("/api/projects").json()] == [project_id]
    deleted = client.delete(f"/api/projects/{project_id}")
    assert deleted.status_code == 204
    assert db.query(Project).filter_by(id=project_id).count() == 0
    assert db.query(StoryPath).filter_by(id=root.id).count() == 0


def test_project_delete_removes_populated_story_path_aggregate(authoring_api):
    _app, client, db, _owner, _outsider = authoring_api
    project = _project(client, "Populated delete")
    project_id = project["id"]
    path_id = project["root_story_path_id"]
    bible = _select_bible(client, project_id)
    _select_outline(
        client,
        story_path_id=path_id,
        bible_revision_id=bible["id"],
        count=2,
    )

    assert db.query(ChapterSlot).filter_by(project_id=project_id).count() == 2
    assert (
        db.query(StoryPathChapter)
        .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
        .filter(StoryPath.project_id == project_id)
        .count()
        == 2
    )

    deleted = client.delete(f"/api/projects/{project_id}")

    assert deleted.status_code == 204, deleted.text
    assert db.query(Project).filter_by(id=project_id).count() == 0
    assert db.query(StoryPath).filter_by(project_id=project_id).count() == 0
    assert db.query(ChapterSlot).filter_by(project_id=project_id).count() == 0
    assert db.query(StoryBibleRevision).filter_by(project_id=project_id).count() == 0
    assert db.query(OutlineRevision).filter_by(project_id=project_id).count() == 0


def test_bible_generation_is_idempotent_and_head_selection_uses_cas(authoring_api):
    _app, client, db, _owner, _outsider = authoring_api
    project = _project(client)
    project_id = project["id"]
    first = client.post(
        f"/api/projects/{project_id}/bible-generations",
        headers={"Idempotency-Key": "bible-r61"},
        json={"instructions": "Keep both timelines", "parameters": {"temperature": 0.5}},
    )
    replay = client.post(
        f"/api/projects/{project_id}/bible-generations",
        headers={"Idempotency-Key": "bible-r61"},
        json={"instructions": "Keep both timelines", "parameters": {"temperature": 0.5}},
    )
    conflict = client.post(
        f"/api/projects/{project_id}/bible-generations",
        headers={"Idempotency-Key": "bible-r61"},
        json={"instructions": "Different request"},
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["task_id"] == replay.json()["task_id"]
    assert first.json()["created"] is True
    assert replay.json()["created"] is False
    assert conflict.status_code == 409
    task = db.query(GenerationTask).filter_by(id=first.json()["task_id"]).one()
    assert task.parameters["instructions"] == "Keep both timelines"

    revision = client.post(
        f"/api/projects/{project_id}/bible-revisions",
        json={"content_json": {"worldview": "Manual version"}},
    )
    assert revision.status_code == 201
    # head 为空时首个修订自动采用：创建后 head 即指向该修订（lock 2）。
    adopted_head = client.get(f"/api/projects/{project_id}/bible-head")
    assert adopted_head.json()["revision_id"] == revision.json()["id"]
    assert adopted_head.json()["lock_version"] == 2
    assert (
        client.put(
            f"/api/projects/{project_id}/bible-head",
            json={"revision_id": revision.json()["id"]},
        ).status_code
        == 428
    )
    selected = client.put(
        f"/api/projects/{project_id}/bible-head",
        headers={"If-Match": "2"},
        json={"revision_id": revision.json()["id"]},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["lock_version"] == 2
    replay_after_head_change = client.post(
        f"/api/projects/{project_id}/bible-generations",
        headers={"Idempotency-Key": "bible-r61"},
        json={"instructions": "Keep both timelines", "parameters": {"temperature": 0.5}},
    )
    assert replay_after_head_change.status_code == 202, replay_after_head_change.text
    assert replay_after_head_change.json()["task_id"] == first.json()["task_id"]
    assert replay_after_head_change.json()["created"] is False
    stale = client.put(
        f"/api/projects/{project_id}/bible-head",
        headers={"If-Match": "1"},
        json={"revision_id": revision.json()["id"]},
    )
    assert stale.status_code == 409


def test_outline_accepts_arbitrary_count_and_preserves_path_chapter_ids(authoring_api):
    _app, client, db, _owner, _outsider = authoring_api
    project = _project(client)
    project_id = project["id"]
    path_id = project["root_story_path_id"]
    bible = _select_bible(client, project_id)

    generated = client.post(
        f"/api/story-paths/{path_id}/outline-generations",
        headers={"Idempotency-Key": "outline-37"},
        json={"chapter_count": 37, "instructions": "Use three acts"},
    )
    assert generated.status_code == 202, generated.text
    task = db.query(GenerationTask).filter_by(id=generated.json()["task_id"]).one()
    assert task.source_refs["outline_source"]["chapter_count"] == 37
    assert task.source_refs["outline_source"]["story_path_id"] == path_id
    assert client.get(f"/api/story-paths/{path_id}/outline-head").json()["revision_id"] is None

    first = _select_outline(
        client,
        story_path_id=path_id,
        bible_revision_id=bible["id"],
    )
    replay_after_head_change = client.post(
        f"/api/story-paths/{path_id}/outline-generations",
        headers={"Idempotency-Key": "outline-37"},
        json={"chapter_count": 37, "instructions": "Use three acts"},
    )
    assert replay_after_head_change.status_code == 202, replay_after_head_change.text
    assert replay_after_head_change.json()["task_id"] == generated.json()["task_id"]
    assert replay_after_head_change.json()["created"] is False
    placements = client.get(f"/api/story-paths/{path_id}/chapters").json()
    stable_ids = [item["id"] for item in placements]
    second = client.post(
        f"/api/story-paths/{path_id}/outline-revisions",
        json={
            "bible_revision_id": bible["id"],
            "parent_revision_id": first["id"],
            "chapters": [
                {
                    "story_path_chapter_id": placement["id"],
                    "display_index": placement["display_index"],
                    "title": f"Revised {placement['display_index']}",
                    "summary": "Revised summary",
                }
                for placement in placements
            ],
        },
    )
    assert second.status_code == 201, second.text
    current_head = client.get(f"/api/story-paths/{path_id}/outline-head").json()
    assert current_head["revision_id"] == first["id"]
    selected = client.put(
        f"/api/story-paths/{path_id}/outline-head",
        headers={"If-Match": "2"},
        json={"revision_id": second.json()["id"]},
    )
    assert selected.status_code == 200, selected.text
    assert [
        item["id"] for item in client.get(f"/api/story-paths/{path_id}/chapters").json()
    ] == stable_ids


def test_chapter_list_hides_detached_rows_and_supports_audit_view(authoring_api):
    _app, client, _db, _owner, _outsider = authoring_api
    project = _project(client)
    path_id = project["root_story_path_id"]
    bible = _select_bible(client, project["id"])
    first = _select_outline(
        client,
        story_path_id=path_id,
        bible_revision_id=bible["id"],
        count=4,
    )
    original = client.get(f"/api/story-paths/{path_id}/chapters").json()

    shorter = client.post(
        f"/api/story-paths/{path_id}/outline-revisions",
        json={
            "bible_revision_id": bible["id"],
            "parent_revision_id": first["id"],
            "chapters": [
                {
                    "story_path_chapter_id": item["id"],
                    "display_index": item["display_index"],
                    "title": f"Short {item['display_index']}",
                    "summary": "Shortened outline",
                }
                for item in original[:2]
            ],
        },
    )
    assert shorter.status_code == 201, shorter.text
    selected = client.put(
        f"/api/story-paths/{path_id}/outline-head",
        headers={"If-Match": "2"},
        json={"revision_id": shorter.json()["id"]},
    )
    assert selected.status_code == 200, selected.text

    visible = client.get(f"/api/story-paths/{path_id}/chapters")
    assert visible.status_code == 200, visible.text
    assert [item["id"] for item in visible.json()] == [
        original[0]["id"],
        original[1]["id"],
    ]
    assert all(item["status"] == "active" for item in visible.json())

    audited = client.get(
        f"/api/story-paths/{path_id}/chapters?include_detached=true"
    )
    assert audited.status_code == 200, audited.text
    audited_by_id = {item["id"]: item for item in audited.json()}
    assert set(audited_by_id) == {item["id"] for item in original}
    for item in original[2:]:
        detached = audited_by_id[item["id"]]
        assert detached["status"] == "detached"
        assert detached["detached_at"] is not None
        assert detached["detached_by_outline_revision_id"] == shorter.json()["id"]
        assert client.get(f"/api/path-chapters/{item['id']}/head").status_code == 404


def test_chapter_routes_use_slot_revisions_and_frozen_path_context(authoring_api):
    _app, client, db, _owner, _outsider = authoring_api
    project = _project(client)
    project_id = project["id"]
    path_id = project["root_story_path_id"]
    bible = _select_bible(client, project_id)
    _select_outline(
        client,
        story_path_id=path_id,
        bible_revision_id=bible["id"],
    )
    placements = client.get(f"/api/story-paths/{path_id}/chapters").json()
    first, second = placements

    revision = client.post(
        f"/api/path-chapters/{first['id']}/revisions",
        json={"content": "Selected first chapter"},
    )
    assert revision.status_code == 201, revision.text
    assert client.get(f"/api/path-chapters/{first['id']}/head").json()["revision_id"] is None
    selected = client.put(
        f"/api/path-chapters/{first['id']}/head",
        headers={"If-Match": "1"},
        json={"revision_id": revision.json()["id"]},
    )
    assert selected.status_code == 200
    assert client.get(f"/api/chapter-revisions/{revision.json()['id']}").status_code == 200

    generated = client.post(
        f"/api/path-chapters/{second['id']}/generations",
        headers={"Idempotency-Key": "chapter-second"},
        json={"instructions": "Continue this branch", "parameters": {"stream": False}},
    )
    assert generated.status_code == 202, generated.text
    task = db.query(GenerationTask).filter_by(id=generated.json()["task_id"]).one()
    source = task.source_refs["chapter_source"]
    assert source["context_manifest"]["path_chapter_id"] == second["id"]
    assert source["context_manifest"]["story_path_id"] == path_id
    assert [item["path_chapter_id"] for item in source["context_manifest"]["ancestors"]] == [
        first["id"]
    ]
    assert source["generation_parameters"]["instructions"] == "Continue this branch"

    batch = client.post(
        f"/api/story-paths/{path_id}/chapter-generation-batches",
        headers={"Idempotency-Key": "chapter-batch-r61"},
        json={"path_chapter_ids": [second["id"]]},
    )
    assert batch.status_code == 202, batch.text
    batch_task = db.query(GenerationTask).filter_by(id=batch.json()["task_id"]).one()
    assert batch_task.source_refs["chapter_batch_source"]["path_chapter_ids"] == [
        second["id"]
    ]

    metrics = client.get(f"/api/projects/{project_id}/metrics").json()
    assert list(metrics["story_paths_by_id"]) == [path_id]
    assert metrics["story_paths_by_id"][path_id]["path_chapter_ids"] == [
        first["id"],
        second["id"],
    ]


def test_path_mutations_use_path_lock_and_cross_owner_reads_are_hidden(authoring_api):
    app, client, db, _owner, outsider = authoring_api
    project = _project(client)
    project_id = project["id"]
    path_id = project["root_story_path_id"]

    discarded_title = client.post(
        f"/api/story-paths/{path_id}/chapters",
        headers={"If-Match": "1"},
        json={"title": "Not a PathChapter field"},
    )
    assert discarded_title.status_code == 422
    appended = client.post(
        f"/api/story-paths/{path_id}/chapters",
        headers={"If-Match": "1"},
        json={},
    )
    assert appended.status_code == 201, appended.text
    first_id = appended.json()["id"]
    assert (
        client.post(
            f"/api/story-paths/{path_id}/chapters",
            headers={"If-Match": "1"},
            json={"after_path_chapter_id": first_id},
        ).status_code
        == 409
    )
    second = client.post(
        f"/api/story-paths/{path_id}/chapters",
        headers={"If-Match": "2"},
        json={"after_path_chapter_id": first_id},
    )
    assert second.status_code == 201
    assert second.json()["predecessor_path_chapter_id"] == first_id

    path = client.get(f"/api/story-paths/{path_id}")
    assert path.headers["etag"] == '"3"'
    renamed = client.patch(
        f"/api/story-paths/{path_id}",
        headers={"If-Match": "3"},
        json={"title": "Alternate main path"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["lock_version"] == 4

    app.dependency_overrides[get_current_user] = lambda: outsider
    assert client.get(f"/api/projects/{project_id}").status_code == 404
    assert client.get(f"/api/story-paths/{path_id}").status_code == 404
    assert client.get(f"/api/path-chapters/{first_id}/head").status_code == 404
    assert db.query(StoryPathChapter).filter_by(story_path_id=path_id).count() == 2
