from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app import auth as auth_module
from app.database import Base, get_db
from app.application.storage_service import LocalStorageBackend
from app.models import Asset, Project
from app.models_v2 import AssetVersion, StorageObject
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers import tts as tts_router
from app.static_files import ProjectAwareStaticFiles


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
    app.include_router(tts_router.router, prefix="/api/tts")
    app.dependency_overrides[get_db] = lambda: db_session
    return app


def _client(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
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


def test_create_project_requires_login(app):
    guest = TestClient(app)
    response = guest.post("/api/projects/", json=_project_payload("guest"))
    assert response.status_code == 401


def test_my_project_list_is_owner_scoped(app):
    alice = _client(app, "alice@example.com")
    bob = _client(app, "bob@example.com")

    alice_project = alice.post("/api/projects/", json=_project_payload("alice story"))
    bob_project = bob.post("/api/projects/", json=_project_payload("bob story"))
    assert alice_project.status_code == 201
    assert bob_project.status_code == 201

    alice_list = alice.get("/api/projects/").json()
    bob_list = bob.get("/api/projects/").json()

    assert [p["title"] for p in alice_list] == ["alice story"]
    assert [p["title"] for p in bob_list] == ["bob story"]


def test_private_project_is_not_readable_by_other_user_or_guest(app):
    alice = _client(app, "alice-private@example.com")
    bob = _client(app, "bob-private@example.com")
    guest = TestClient(app)

    created = alice.post("/api/projects/", json=_project_payload("private story"))
    project_id = created.json()["id"]

    assert alice.get(f"/api/projects/{project_id}").status_code == 200
    assert bob.get(f"/api/projects/{project_id}").status_code == 404
    assert guest.get(f"/api/projects/{project_id}").status_code == 404


def test_new_projects_are_always_drafts_until_release_publication(app):
    owner = _client(app, "filters@example.com")
    owner.post("/api/projects/", json=_project_payload("draft", visibility="private", is_draft=True))
    owner.post("/api/projects/", json=_project_payload("published", visibility="private", is_draft=False))

    drafts = owner.get("/api/projects/?publication=draft").json()
    published = owner.get("/api/projects/?publication=published").json()

    assert [p["title"] for p in drafts] == ["published", "draft"]
    assert published == []


def test_ownerless_legacy_project_is_not_writable_without_claim_token(app, db_session):
    user = _client(app, "legacy@example.com")
    project = Project(
        title="legacy",
        characters=[],
        story_start="旧数据",
        story_end="旧结局",
        status="draft_input",
        visibility="private",
        is_draft=True,
    )
    db_session.add(project)
    db_session.commit()

    publish = user.put(
        f"/api/projects/{project.id}/publication",
        json={"visibility": "public", "is_draft": False},
    )
    delete = user.delete(f"/api/projects/{project.id}")

    assert publish.status_code == 404
    assert delete.status_code == 403


def test_project_delete_keeps_storage_tombstone_until_gc(
    app, db_session, tmp_path, monkeypatch
):
    from app.application import maintenance_service

    db_session.execute(text("PRAGMA foreign_keys=ON"))
    owner = _client(app, "delete-storage@example.com")
    project_id = owner.post(
        "/api/projects/", json=_project_payload("delete storage")
    ).json()["id"]
    project = db_session.query(Project).filter_by(id=project_id).one()
    backend = LocalStorageBackend(tmp_path / "objects")
    stored = backend.save_stream(
        BytesIO(b"project-image"),
        namespace="generated-background",
        filename_or_suffix=".png",
        media_type="image/png",
    )
    storage = StorageObject(
        owner_id=project.owner_id,
        project_id=project.id,
        storage_backend="local",
        storage_key=stored.storage_key,
        media_type=stored.media_type,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        visibility="release",
        status="active",
    )
    asset = Asset(
        project_id=project.id,
        asset_type="background",
        target_name="room",
        prompt="empty room",
        status="completed",
        logical_key="background:room:day:clear:default",
        taxonomy_json={},
    )
    db_session.add_all([storage, asset])
    db_session.flush()
    version = AssetVersion(
        asset_id=asset.id,
        source_kind="legacy",
        source_revision_id=None,
        storage_object_id=storage.id,
        version_no=1,
        cache_key="project-delete-cache",
        prompt_hash="a" * 64,
        prompt_version="test-v1",
        safety_status="passed",
    )
    db_session.add(version)
    db_session.commit()
    version_id = version.id
    physical_path = backend.resolve_key(storage.storage_key)
    assert physical_path.is_file()

    deleted = owner.delete(f"/api/projects/{project.id}")
    assert deleted.status_code == 200
    db_session.expire_all()
    tombstone = db_session.query(StorageObject).filter_by(id=storage.id).one()
    assert tombstone.project_id is None
    assert tombstone.status == "deleted"
    assert tombstone.deleted_at is not None
    assert db_session.query(AssetVersion).filter_by(id=version_id).one_or_none() is None
    assert physical_path.is_file()

    monkeypatch.setattr(maintenance_service, "get_local_storage_backend", lambda: backend)
    assert maintenance_service.purge_unreferenced_deleted_media(
        db_session, grace_hours=0
    ) == 1
    assert not physical_path.exists()


def test_legacy_project_claim_requires_configured_token(app, db_session, monkeypatch):
    user = _client(app, "claim@example.com")
    project = Project(
        title="legacy claim",
        characters=[],
        story_start="旧数据",
        story_end="旧结局",
        status="draft_input",
        visibility="private",
        is_draft=True,
    )
    db_session.add(project)
    db_session.commit()

    assert user.post(f"/api/projects/{project.id}/claim").status_code == 403

    monkeypatch.setattr(projects_router, "LEGACY_PROJECT_CLAIM_TOKEN", "claim-token")
    assert user.post(
        f"/api/projects/{project.id}/claim",
        headers={"x-legacy-project-claim": "wrong"},
    ).status_code == 403

    ok = user.post(
        f"/api/projects/{project.id}/claim",
        headers={"x-legacy-project-claim": "claim-token"},
    )
    assert ok.status_code == 200
    assert ok.json()["owner_id"] is not None


def test_private_static_generated_assets_follow_project_permissions(app, db_session, tmp_path, monkeypatch):
    owner = _client(app, "static-owner@example.com")
    guest = TestClient(app)
    created = owner.post("/api/projects/", json=_project_payload("static private"))
    project_id = created.json()["id"]

    rel_path = Path("assets/backgrounds/private.png")
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"private image")

    db_session.add(Asset(
        project_id=project_id,
        chapter_index=1,
        asset_type="background",
        target_name="scene",
        prompt="prompt",
        image_url=f"/static/{rel_path.as_posix()}",
        status="completed",
    ))
    db_session.commit()

    static_app = FastAPI()
    static_app.mount("/static", ProjectAwareStaticFiles(directory=str(tmp_path)))
    static_app.dependency_overrides[get_db] = lambda: db_session
    monkeypatch.setattr("app.static_files.SessionLocal", lambda: db_session)

    static_guest = TestClient(static_app)
    assert static_guest.get(f"/static/{rel_path.as_posix()}").status_code == 404

    static_owner = TestClient(static_app)
    static_owner.cookies.update(owner.cookies)
    assert static_owner.get(f"/static/{rel_path.as_posix()}").status_code == 200


def test_static_generated_assets_allow_any_matching_readable_project(app, db_session, tmp_path, monkeypatch):
    alice = _client(app, "static-shared-alice@example.com")
    bob = _client(app, "static-shared-bob@example.com")
    guest = TestClient(app)

    alice_project = alice.post("/api/projects/", json=_project_payload("alice private")).json()["id"]
    bob_project = bob.post("/api/projects/", json=_project_payload("bob private")).json()["id"]

    rel_path = Path("assets/backgrounds/shared-private.png")
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_bytes(b"shared image")

    private_url = "/static/assets/backgrounds/shared-private.png"
    db_session.add_all([
        Asset(
            project_id=alice_project,
            chapter_index=1,
            asset_type="background",
            target_name="scene",
            prompt="same prompt",
            image_url=private_url,
            status="completed",
        ),
        Asset(
            project_id=bob_project,
            chapter_index=1,
            asset_type="background",
            target_name="scene",
            prompt="same prompt",
            image_url=private_url,
            status="completed",
        ),
    ])
    db_session.commit()

    static_app = FastAPI()
    static_app.mount("/static", ProjectAwareStaticFiles(directory=str(tmp_path)))
    monkeypatch.setattr("app.static_files.SessionLocal", lambda: db_session)

    static_guest = TestClient(static_app)
    assert static_guest.get(private_url).status_code == 404

    static_bob = TestClient(static_app)
    static_bob.cookies.update(bob.cookies)
    assert static_bob.get(private_url).status_code == 200


def test_static_tts_cache_fails_closed_without_project_binding(tmp_path):
    rel_path = Path("tts_cache/preview.mp3")
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True)
    file_path.write_bytes(b"audio")

    static_app = FastAPI()
    static_app.mount("/static", ProjectAwareStaticFiles(directory=str(tmp_path)))
    response = TestClient(static_app).get(f"/static/{rel_path.as_posix()}")

    assert response.status_code == 404
    assert response.content == b"Not Found"


def test_project_tts_requires_owner_for_every_project_id_request(app, db_session, monkeypatch, tmp_path):
    owner = _client(app, "tts-owner@example.com")
    other = _client(app, "tts-other@example.com")
    guest = TestClient(app)
    project_id = owner.post("/api/projects/", json=_project_payload("tts project")).json()["id"]
    synth_calls = []

    class FakeTTSService:
        output_dir = tmp_path / "tts-output"

        async def synthesize(self, **kwargs):
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / "project-preview.mp3").write_bytes(b"audio")
            synth_calls.append(kwargs)
            return {
                "success": True,
                "audio_url": "/static/tts_cache/project-preview.mp3",
                "cached": False,
                "speaker": kwargs.get("speaker"),
                "emotion_prompt": kwargs.get("emotion_prompt"),
            }

    monkeypatch.setattr(tts_router, "TTS_ENABLED", True)
    monkeypatch.setattr(tts_router, "tts_service", FakeTTSService())
    monkeypatch.setattr(
        tts_router,
        "get_local_storage_backend",
        lambda: LocalStorageBackend(tmp_path / "objects"),
    )

    payload = {"text": "preview", "project_id": project_id, "speaker": "x4_mingge"}
    assert guest.post("/api/tts/synthesize", json=payload).status_code == 401
    assert other.post("/api/tts/synthesize", json=payload).status_code == 403
    assert synth_calls == []
    assert db_session.query(Asset).filter(Asset.asset_type == "voice_preview").count() == 0

    ok = owner.post("/api/tts/synthesize", json=payload)
    assert ok.status_code == 200
    assert ok.json()["audio_url"].startswith("/api/media/")
    assert len(synth_calls) == 1
    assert db_session.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.asset_type == "voice_preview",
        Asset.image_url == ok.json()["audio_url"],
    ).count() == 1
