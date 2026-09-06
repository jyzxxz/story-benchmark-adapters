from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.hashing import content_hash
from app.application.chapter_script_service import (
    resolve_chapter_script_generation_source,
)
from app.auth import get_current_user
from app.core.config import AppSettings
from app.database import Base, get_db
from app.main import create_app
from app.models import User
from app.models_v2 import (
    AssetVersion,
    BranchCandidate,
    CandidateSetRevision,
    ChapterScriptRevision,
    GenerationTask,
    ProjectRelease,
    ScriptResourceSlot,
    StoryNode,
    StoryPath,
    VNGraphHead,
    VNGraphRevision,
)
from app.services.chapter_script_ir import build_script_ir_from_segments
from test_publication_readiness import _seed_ready_project


R62_ROUTES = {
    ("POST", "/api/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-generations"),
    ("GET", "/api/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-revisions"),
    ("GET", "/api/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-head"),
    ("PUT", "/api/story-paths/{story_path_id}/checkpoints/{node_id}/candidate-set-head"),
    ("GET", "/api/candidate-set-revisions/{candidate_set_revision_id}/candidates"),
    ("POST", "/api/branch-candidates/{candidate_id}/story-paths"),
    ("POST", "/api/chapter-revisions/{chapter_revision_id}/script-generations"),
    ("GET", "/api/chapter-revisions/{chapter_revision_id}/script-revisions"),
    ("GET", "/api/chapter-revisions/{chapter_revision_id}/script-head"),
    ("PUT", "/api/chapter-revisions/{chapter_revision_id}/script-head"),
    ("GET", "/api/chapter-script-revisions/{script_revision_id}/resource-slots"),
    ("POST", "/api/chapter-script-revisions/{script_revision_id}/resource-renders"),
    ("PUT", "/api/chapter-script-revisions/{script_revision_id}/resource-slots/{slot_id}"),
    ("POST", "/api/chapter-script-revisions/{script_revision_id}/vn-graph-compilations"),
    ("GET", "/api/chapter-script-revisions/{script_revision_id}/vn-graph-revisions"),
    ("GET", "/api/chapter-script-revisions/{script_revision_id}/vn-graph-head"),
    ("PUT", "/api/chapter-script-revisions/{script_revision_id}/vn-graph-head"),
    ("GET", "/api/vn-graph-revisions/{vn_graph_revision_id}"),
    ("POST", "/api/projects/{project_id}/publish"),
    ("GET", "/api/projects/{project_id}/releases"),
    ("GET", "/api/releases/{release_id}"),
    ("POST", "/api/projects/{project_id}/unpublish"),
    ("GET", "/api/public/projects"),
    ("GET", "/api/public/projects/{project_id}"),
    ("GET", "/api/public/releases/{release_id}/manifest"),
    ("GET", "/api/public/releases/{release_id}/path-chapters/{path_chapter_id}/manifest"),
    ("GET", "/api/public/releases/{release_id}/path-chapters/{path_chapter_id}/vn-graph"),
}


@pytest.fixture()
def artifact_api(tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    seeded = _seed_ready_project(db, with_resource=True)
    outsider = User(
        email=f"r62-outsider-{uuid4()}@example.com",
        password_hash="hash",
        display_name="R62 outsider",
        quota_total=1_000,
        quota_daily=1_000,
    )
    db.add(outsider)
    db.commit()

    app = create_app(
        AppSettings(
            _env_file=None,
            app_env="test",
            static_dir=tmp_path / "static",
        )
    )
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: seeded["user"]
    try:
        yield app, TestClient(app), db, seeded, outsider
    finally:
        db.close()
        engine.dispose()


def _candidate_set(db, seeded):
    checkpoint = StoryNode(
        project_id=seeded["project"].id,
        node_type="checkpoint",
        checkpoint_key=f"choice-{uuid4()}",
        content_revision_id=seeded["chapter"].id,
        payload={"question": "Which platform?"},
    )
    db.add(checkpoint)
    db.flush()
    candidate_ids = [str(uuid4()), str(uuid4())]
    candidates_json = [
        {
            "id": candidate_ids[0],
            "preview_node_id": None,
            "ordinal": 0,
            "option_key": "left",
            "preview_text": "Take the left platform.",
            "state_delta": {"route": "left"},
        },
        {
            "id": candidate_ids[1],
            "preview_node_id": None,
            "ordinal": 1,
            "option_key": "right",
            "preview_text": "Take the right platform.",
            "state_delta": {"route": "right"},
        },
    ]
    revision = CandidateSetRevision(
        project_id=seeded["project"].id,
        story_path_id=seeded["path"].id,
        checkpoint_node_id=checkpoint.id,
        chapter_revision_id=seeded["chapter"].id,
        state_snapshot_id=seeded["state"].id,
        revision_no=1,
        source_hash=content_hash({"checkpoint_node_id": checkpoint.id}),
        candidate_count=2,
        instructions="",
        candidates_json=candidates_json,
        content_hash=content_hash(candidates_json),
        created_by=seeded["user"].id,
    )
    db.add(revision)
    db.flush()
    candidates = [
        BranchCandidate(
            id=item["id"],
            project_id=seeded["project"].id,
            candidate_set_revision_id=revision.id,
            checkpoint_node_id=checkpoint.id,
            option_key=item["option_key"],
            state_delta=deepcopy(item["state_delta"]),
            candidate_status="preview_ready",
        )
        for item in candidates_json
    ]
    db.add_all(candidates)
    db.commit()
    return checkpoint, revision, candidates


def _second_script(db, seeded) -> ChapterScriptRevision:
    _content = seeded["chapter"].content or ""
    script_json = build_script_ir_from_segments(
        chapter_revision_id=seeded["chapter"].id,
        chapter_content=_content,
        chapter_content_hash=seeded["chapter"].content_hash,
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        characters=seeded["project"].characters,
        llm_output={
            "segments": [
                {"text": line, "kind": "narration"}
                for line in _content.splitlines()
                if line.strip()
            ]
        },
    )
    revision = ChapterScriptRevision(
        project_id=seeded["project"].id,
        chapter_index=seeded["script"].chapter_index,
        chapter_revision_id=seeded["chapter"].id,
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        parent_revision_id=seeded["script"].id,
        revision_no=2,
        source_hash=content_hash({"script": "second"}),
        script_hash=content_hash(script_json),
        script_json=script_json,
        coverage_json={"coverage_ratio": 1.0},
        schema_version="script-ir-v1",
        generator_version="r62-test-v1",
        status="ready",
        created_by=seeded["user"].id,
    )
    db.add(revision)
    db.commit()
    return revision


def _graph_revision(
    db,
    *,
    script: ChapterScriptRevision,
    manifest: dict,
    revision_no: int,
    parent_revision_id: str | None,
) -> VNGraphRevision:
    graph_json = {"Nodes": [], "Metadata": {"fixture": f"r62-{revision_no}"}}
    revision = VNGraphRevision(
        project_id=script.project_id,
        chapter_index=script.chapter_index,
        chapter_revision_id=script.chapter_revision_id,
        script_revision_id=script.id,
        parent_revision_id=parent_revision_id,
        revision_no=revision_no,
        binding_manifest=deepcopy(manifest),
        binding_manifest_hash=content_hash(manifest),
        source_manifest_hash=content_hash(manifest),
        graph_hash=content_hash(graph_json),
        graph_json=graph_json,
        schema_version="1",
        compiler_version=f"r62-test-v{revision_no}",
        tachi_policy_version="speaker-focus-v2",
        status="ready",
    )
    db.add(revision)
    db.commit()
    return revision


def test_r62_routes_are_registered_once_by_replacement_modules(artifact_api):
    app, _client, _db, _seeded, _outsider = artifact_api
    route_keys = [
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method not in {"HEAD", "OPTIONS"}
    ]
    assert R62_ROUTES <= set(route_keys)
    assert all(Counter(route_keys)[key] == 1 for key in R62_ROUTES)
    assert {
        route.endpoint.__module__
        for route in app.routes
        if isinstance(route, APIRoute)
        and any((method, route.path) in R62_ROUTES for method in route.methods or set())
    } == {
        "app.routers.v2.authoring_candidates",
        "app.routers.v2.authoring_scripts",
        "app.routers.v2.authoring_vn_graphs",
        "app.routers.v2.authoring_releases",
    }


def test_candidate_review_generation_and_promotion_are_path_scoped(artifact_api):
    app, client, db, seeded, outsider = artifact_api
    checkpoint, revision, candidates = _candidate_set(db, seeded)
    path_id = seeded["path"].id

    generated = client.post(
        f"/api/story-paths/{path_id}/checkpoints/{checkpoint.id}/candidate-set-generations",
        headers={"Idempotency-Key": "r62-candidates"},
        json={
            "chapter_revision_id": seeded["chapter"].id,
            "state_snapshot_id": seeded["state"].id,
            "candidate_count": 2,
            "instructions": "Keep both outcomes plausible",
        },
    )
    assert generated.status_code == 202, generated.text
    task = db.query(GenerationTask).filter_by(id=generated.json()["task_id"]).one()
    source = task.source_refs["candidate_set_source"]
    assert source["story_path_id"] == path_id
    assert source["path_chapter_id"] == seeded["placement"].id

    history = client.get(
        f"/api/story-paths/{path_id}/checkpoints/{checkpoint.id}/candidate-set-revisions"
    )
    assert [item["id"] for item in history.json()] == [revision.id]
    head = client.get(
        f"/api/story-paths/{path_id}/checkpoints/{checkpoint.id}/candidate-set-head"
    )
    assert head.json()["revision_id"] is None
    assert head.headers["etag"] == '"1"'
    selected = client.put(
        f"/api/story-paths/{path_id}/checkpoints/{checkpoint.id}/candidate-set-head",
        headers={"If-Match": "1"},
        json={"revision_id": revision.id},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["lock_version"] == 2

    listed = client.get(f"/api/candidate-set-revisions/{revision.id}/candidates")
    assert [item["id"] for item in listed.json()] == [item.id for item in candidates]
    promoted = client.post(
        f"/api/branch-candidates/{candidates[0].id}/story-paths",
        headers={"Idempotency-Key": "r62-promote-left"},
        json={"title": "Left route"},
    )
    replay = client.post(
        f"/api/branch-candidates/{candidates[0].id}/story-paths",
        headers={"Idempotency-Key": "r62-promote-left"},
        json={"title": "Left route"},
    )
    assert promoted.status_code == replay.status_code == 201
    assert promoted.json()["id"] == replay.json()["id"]
    assert promoted.json()["parent_path_id"] == path_id
    assert db.query(StoryPath).filter_by(parent_path_id=path_id).count() == 1

    app.dependency_overrides[get_current_user] = lambda: outsider
    assert client.get(f"/api/candidate-set-revisions/{revision.id}/candidates").status_code == 404


def test_script_resource_and_vn_graph_routes_use_exact_revision_heads(artifact_api):
    app, client, db, seeded, outsider = artifact_api
    chapter_id = seeded["chapter"].id
    script_id = seeded["script"].id

    generated = client.post(
        f"/api/chapter-revisions/{chapter_id}/script-generations",
        headers={"Idempotency-Key": "r62-script"},
        json={"instructions": "Emphasize silent reaction beats"},
    )
    assert generated.status_code == 202, generated.text
    task = db.query(GenerationTask).filter_by(id=generated.json()["task_id"]).one()
    context = resolve_chapter_script_generation_source(db, task=task)
    assert context.chapter_revision_id == chapter_id
    assert context.request.instructions == "Emphasize silent reaction beats"

    assert client.get(f"/api/chapter-revisions/{chapter_id}/script-revisions").json()[0][
        "id"
    ] == script_id
    script_head = client.get(f"/api/chapter-revisions/{chapter_id}/script-head")
    assert script_head.headers["etag"] == '"1"'
    second_script = _second_script(db, seeded)
    selected_script = client.put(
        f"/api/chapter-revisions/{chapter_id}/script-head",
        headers={"If-Match": "1"},
        json={"revision_id": second_script.id},
    )
    assert selected_script.json()["lock_version"] == 2
    assert (
        client.put(
            f"/api/chapter-revisions/{chapter_id}/script-head",
            headers={"If-Match": "1"},
            json={"revision_id": seeded["script"].id},
        ).status_code
        == 409
    )

    rendered = client.post(
        f"/api/chapter-script-revisions/{second_script.id}/resource-renders",
        headers={"Idempotency-Key": "r62-render-empty"},
        json={"role": "background"},
    )
    replay = client.post(
        f"/api/chapter-script-revisions/{second_script.id}/resource-renders",
        headers={"Idempotency-Key": "r62-render-empty"},
        json={"role": "background"},
    )
    assert rendered.status_code == replay.status_code == 202
    assert rendered.json()["created"] is True
    assert replay.json()["created"] is False

    slots = client.get(f"/api/chapter-script-revisions/{second_script.id}/resource-slots")
    assert slots.status_code == 200
    assert slots.json()[0]["script_revision_id"] == second_script.id
    assert slots.json()[0]["lock_version"] == 1
    resource_slot = db.query(ScriptResourceSlot).filter_by(id=slots.json()[0]["id"]).one()
    assert slots.json()[0]["asset_id"] == resource_slot.asset_id
    replacement = AssetVersion(
        asset_id=resource_slot.asset_id,
        source_kind="chapter_script_revision",
        source_revision_id=second_script.id,
        source_hash=second_script.script_hash,
        storage_object_id=seeded["storage"].id,
        version_no=1,
        cache_key=f"r62-{uuid4()}",
        prompt_hash="c" * 64,
        prompt_version="r62-v1",
        safety_status="passed",
    )
    db.add(replacement)
    db.commit()
    bound = client.put(
        f"/api/chapter-script-revisions/{second_script.id}/resource-slots/{resource_slot.id}",
        headers={"If-Match": "1"},
        json={"asset_version_id": replacement.id},
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["asset_version_id"] == replacement.id
    assert bound.json()["asset_id"] == resource_slot.asset_id
    assert bound.json()["lock_version"] == 2
    resolved = client.get(
        f"/api/projects/{seeded['project'].id}/asset-versions/{replacement.id}"
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["asset_id"] == resource_slot.asset_id
    assert resolved.json()["media_url"] == f"/api/media/{seeded['storage'].id}"
    stale = client.put(
        f"/api/chapter-script-revisions/{second_script.id}/resource-slots/{resource_slot.id}",
        headers={"If-Match": "1"},
        json={"asset_version_id": replacement.id},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "resource_slot.version_conflict"

    compiled = client.post(
        f"/api/chapter-script-revisions/{second_script.id}/vn-graph-compilations",
        headers={"Idempotency-Key": "r62-compile"},
        json={},
    )
    assert compiled.status_code == 202, compiled.text
    compile_task = db.query(GenerationTask).filter_by(id=compiled.json()["task_id"]).one()
    first_graph = _graph_revision(
        db,
        script=second_script,
        manifest=compile_task.parameters["binding_manifest"],
        revision_no=1,
        parent_revision_id=None,
    )
    persisted_head = db.query(VNGraphHead).filter_by(script_revision_id=second_script.id).one()
    persisted_head.current_revision_id = first_graph.id
    db.commit()
    assert client.get(
        f"/api/chapter-script-revisions/{second_script.id}/vn-graph-revisions"
    ).json()[0]["id"] == first_graph.id
    graph_head = client.get(f"/api/chapter-script-revisions/{second_script.id}/vn-graph-head")
    assert graph_head.headers["etag"] == '"1"'
    second_graph = _graph_revision(
        db,
        script=second_script,
        manifest=compile_task.parameters["binding_manifest"],
        revision_no=2,
        parent_revision_id=first_graph.id,
    )
    selected_graph = client.put(
        f"/api/chapter-script-revisions/{second_script.id}/vn-graph-head",
        headers={"If-Match": "1"},
        json={"revision_id": second_graph.id},
    )
    assert selected_graph.json()["lock_version"] == 2
    assert (
        client.put(
            f"/api/chapter-script-revisions/{second_script.id}/vn-graph-head",
            headers={"If-Match": "1"},
            json={"revision_id": first_graph.id},
        ).status_code
        == 409
    )
    assert client.get(f"/api/vn-graph-revisions/{second_graph.id}").json()["id"] == second_graph.id

    app.dependency_overrides[get_current_user] = lambda: outsider
    assert client.get(f"/api/chapter-revisions/{chapter_id}/script-head").status_code == 404
    assert client.get(f"/api/vn-graph-revisions/{second_graph.id}").status_code == 404


def test_atomic_publish_public_reads_and_unpublish_use_one_active_release(artifact_api):
    app, client, _db, seeded, outsider = artifact_api
    project_id = seeded["project"].id
    readiness = client.get(f"/api/projects/{project_id}/publication-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["ready"] is True

    published = client.post(
        f"/api/projects/{project_id}/publish",
        headers={"Idempotency-Key": "r62-publish"},
        json={
            "expected_authoring_fingerprint": readiness.json()["authoring_fingerprint"],
            "release_notes": "R6.2 public version",
        },
    )
    replay = client.post(
        f"/api/projects/{project_id}/publish",
        headers={"Idempotency-Key": "r62-publish"},
        json={
            "expected_authoring_fingerprint": readiness.json()["authoring_fingerprint"],
            "release_notes": "R6.2 public version",
        },
    )
    assert published.status_code == replay.status_code == 201
    release_id = published.json()["id"]
    assert replay.json()["id"] == release_id
    assert published.json()["status"] == "published"
    assert client.get(f"/api/projects/{project_id}/releases").json()[0]["id"] == release_id
    assert client.get(f"/api/releases/{release_id}").json()["id"] == release_id

    catalog = client.get("/api/public/projects")
    assert [item["id"] for item in catalog.json()] == [project_id]
    public_project = client.get(f"/api/public/projects/{project_id}")
    assert public_project.json()["title"] == "Frozen routes"
    assert public_project.json()["release_id"] == release_id
    assert public_project.json()["cover_url"] == f"/api/media/{seeded['storage'].id}"
    manifest = client.get(f"/api/public/releases/{release_id}/manifest")
    assert manifest.json()["manifest_hash"] == published.json()["manifest_hash"]
    chapter_manifest = client.get(
        f"/api/public/releases/{release_id}/path-chapters/{seeded['placement'].id}/manifest"
    )
    assert chapter_manifest.json()["path_chapter_id"] == seeded["placement"].id
    graph = client.get(
        f"/api/public/releases/{release_id}/path-chapters/{seeded['placement'].id}/vn-graph"
    )
    assert graph.status_code == 200
    assert graph.json() == seeded["graph"].graph_json
    cached = client.get(
        f"/api/public/releases/{release_id}/path-chapters/{seeded['placement'].id}/vn-graph",
        headers={"If-None-Match": graph.headers["etag"]},
    )
    assert cached.status_code == 304

    assert (
        client.post(
            f"/api/projects/{project_id}/unpublish",
            headers={"If-Match": "1"},
        ).status_code
        == 409
    )
    unpublished = client.post(
        f"/api/projects/{project_id}/unpublish",
        headers={"If-Match": "2"},
    )
    assert unpublished.status_code == 200, unpublished.text
    assert unpublished.json()["status"] == "withdrawn"
    assert client.get("/api/public/projects").json() == []
    assert client.get(f"/api/public/projects/{project_id}").status_code == 404
    assert client.get(f"/api/public/releases/{release_id}/manifest").status_code == 404
    assert client.get(
        f"/api/public/releases/{release_id}/path-chapters/"
        f"{seeded['placement'].id}/manifest"
    ).status_code == 404
    assert client.get(
        f"/api/public/releases/{release_id}/path-chapters/"
        f"{seeded['placement'].id}/vn-graph"
    ).status_code == 404

    app.dependency_overrides[get_current_user] = lambda: outsider
    assert client.get(f"/api/releases/{release_id}").status_code == 404


def test_legacy_withdrawn_release_with_nullable_metadata_is_readable(artifact_api):
    _app, client, db, seeded, _outsider = artifact_api
    release = ProjectRelease(
        project_id=seeded["project"].id,
        version=1,
        status="withdrawn",
        bible_revision_id=seeded["bible"].id,
        outline_revision_id=seeded["outline"].id,
        manifest_json={"legacy": True},
        manifest_hash="e" * 64,
        authoring_fingerprint=None,
        published_at=None,
        withdrawn_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.commit()

    listed = client.get(f"/api/projects/{seeded['project'].id}/releases")
    fetched = client.get(f"/api/releases/{release.id}")

    assert listed.status_code == 200, listed.text
    assert fetched.status_code == 200, fetched.text
    assert listed.json()[0]["authoring_fingerprint"] is None
    assert listed.json()[0]["published_at"] is None
    assert fetched.json()["authoring_fingerprint"] is None
    assert fetched.json()["published_at"] is None
