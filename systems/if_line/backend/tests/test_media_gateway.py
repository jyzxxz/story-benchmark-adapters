from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from io import BytesIO

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.application.branch_service import create_reading_session
from app.application.hashing import content_hash
from app.application.storage_service import (
    InvalidStoragePathError,
    LocalStorageBackend,
    StorageLimitExceededError,
    get_local_storage_backend,
    register_stored_file,
)
from app.application.visual_asset_service import confirm_reading_continuation
from app.database import Base, get_db
from app.models import Asset, User
from app.models_v2 import (
    AssetVersion,
    OutlineRevision,
    ProjectPublication,
    ProjectRelease,
    ReadingContinuation,
    StorageObject,
    StoryBibleRevision,
    StoryPath,
)
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers.v2 import media as media_router
from app.static_files import ProjectAwareStaticFiles


@pytest.fixture
def media_app(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()
    backend = LocalStorageBackend(tmp_path / "object-store")
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(projects_router.router, prefix="/api/projects")
    app.include_router(media_router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_local_storage_backend] = lambda: backend
    try:
        yield app, db, backend
    finally:
        db.close()
        engine.dispose()


def _client(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return client


def _user_id(db, email: str) -> int:
    return db.query(User).filter(User.email == email).one().id


def _project(client: TestClient, title: str) -> int:
    response = client.post(
        "/api/projects/",
        json={
            "title": title,
            "characters": ["林夜"],
            "story_start": "开端",
            "story_end": "结局",
            "style": "古风",
            "pace": "medium",
        },
    )
    assert response.status_code in {200, 201}
    return response.json()["id"]


def _store(
    db,
    backend: LocalStorageBackend,
    *,
    owner_id: int,
    project_id: int | None,
    data: bytes = b"0123456789",
    namespace: str = "project-media",
    visibility: str = "private",
    status: str = "active",
) -> StorageObject:
    stored = backend.save_stream(
        BytesIO(data),
        namespace=namespace,
        filename_or_suffix="asset.bin",
        media_type="application/octet-stream",
        max_bytes=1024,
    )
    obj = register_stored_file(
        db,
        stored=stored,
        owner_id=owner_id,
        project_id=project_id,
        visibility=visibility,
        status=status,
        backend=backend,
    )
    db.commit()
    db.refresh(obj)
    return obj


def test_private_media_requires_owner_and_supports_ranges(media_app):
    app, db, backend = media_app
    owner_email = "media-owner@example.com"
    owner = _client(app, owner_email)
    other = _client(app, "media-other@example.com")
    guest = TestClient(app)
    project_id = _project(owner, "private media")
    obj = _store(
        db,
        backend,
        owner_id=_user_id(db, owner_email),
        project_id=project_id,
    )
    url = f"/api/media/{obj.id}"

    assert guest.get(url).status_code == 404
    assert other.get(url).status_code == 404

    full = owner.get(url)
    assert full.status_code == 200
    assert full.content == b"0123456789"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["x-content-type-options"] == "nosniff"
    assert full.headers["cache-control"] == "private, no-store"

    partial = owner.get(url, headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206
    assert partial.content == b"2345"
    assert partial.headers["content-range"] == "bytes 2-5/10"
    assert partial.headers["content-length"] == "4"

    suffix = owner.get(url, headers={"Range": "bytes=-3"})
    assert suffix.status_code == 206
    assert suffix.content == b"789"

    invalid = owner.get(url, headers={"Range": "bytes=20-30"})
    assert invalid.status_code == 416
    assert invalid.headers["content-range"] == "bytes */10"
    assert owner.get(url, headers={"Range": "bytes=0-1,3-4"}).status_code == 416

    download = owner.get(f"{url}?download=true")
    assert download.headers["content-disposition"].startswith("attachment;")
    not_modified = owner.get(url, headers={"If-None-Match": full.headers["etag"]})
    assert not_modified.status_code == 304


def test_public_and_release_media_authorization(media_app):
    app, db, backend = media_app
    owner_email = "release-owner@example.com"
    owner = _client(app, owner_email)
    guest = TestClient(app)
    project_id = _project(owner, "release media")
    owner_id = _user_id(db, owner_email)

    public_obj = _store(
        db,
        backend,
        owner_id=owner_id,
        project_id=project_id,
        data=b"public",
        visibility="public",
    )
    public_response = guest.get(f"/api/media/{public_obj.id}")
    assert public_response.status_code == 200
    assert public_response.headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )

    release_obj = _store(
        db,
        backend,
        owner_id=owner_id,
        project_id=project_id,
        data=b"release",
        visibility="release",
    )
    release_url = f"/api/media/{release_obj.id}"
    assert guest.get(release_url).status_code == 404

    path = StoryPath(project_id=project_id, title="Root", status="active")
    db.add(path)
    db.flush()
    bible_content = {"world": "release media"}
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
    outline = OutlineRevision(
        project_id=project_id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash=content_hash({"bible_revision_id": bible.id}),
        content_hash=content_hash({"chapters": []}),
        status="approved",
        approved_at=datetime.now(timezone.utc),
        created_by=owner_id,
    )
    db.add(outline)
    db.flush()
    manifest = {"storage_object_ids": [release_obj.id]}
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
    publication = ProjectPublication(
        project_id=project_id,
        active_release_id=release.id,
        published_at=release.published_at,
        lock_version=1,
    )
    db.add(publication)
    db.commit()
    assert guest.get(release_url).content == b"release"

    publication.active_release_id = None
    publication.published_at = None
    publication.lock_version += 1
    db.flush()
    release.status = "withdrawn"
    release.withdrawn_at = datetime.now(timezone.utc)
    db.commit()
    assert guest.get(release_url).status_code == 404


@pytest.mark.parametrize("transition", ["unpublish", "supersede"])
def test_confirmed_continuation_media_requires_its_active_release(
    media_app,
    transition,
):
    app, db, backend = media_app
    owner_email = f"continuation-{transition}@example.com"
    owner = _client(app, owner_email)
    guest = TestClient(app)
    project_id = _project(owner, f"continuation media {transition}")
    owner_id = _user_id(db, owner_email)
    storage = _store(
        db,
        backend,
        owner_id=owner_id,
        project_id=project_id,
        data=b"confirmed-continuation",
        namespace="continuation-media",
        visibility="private",
    )

    path = StoryPath(project_id=project_id, title="Root", status="active")
    db.add(path)
    db.flush()
    bible_content = {"world": "continuation media"}
    bible = StoryBibleRevision(
        project_id=project_id,
        revision_no=1,
        source_hash=content_hash({"source": transition}),
        content_hash=content_hash(bible_content),
        content_json=bible_content,
        status="ready",
        created_by=owner_id,
    )
    db.add(bible)
    db.flush()
    outline = OutlineRevision(
        project_id=project_id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash=content_hash({"bible_revision_id": bible.id}),
        content_hash=content_hash({"chapters": []}),
        status="approved",
        approved_at=datetime.now(timezone.utc),
        created_by=owner_id,
    )
    db.add(outline)
    db.flush()
    manifest = {"story": {"nodes": []}}
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
    publication = ProjectPublication(
        project_id=project_id,
        active_release_id=release.id,
        published_at=release.published_at,
        lock_version=1,
    )
    db.add(publication)
    db.flush()
    reading = create_reading_session(
        db,
        user_id=owner_id,
        project_id=project_id,
        release_id=release.id,
        initial_state={"chapter_number": 1},
    )
    continuation = ReadingContinuation(
        session_id=reading.id,
        release_id=release.id,
        chapter_number=1,
        direction="继续调查",
        visual_mode="system_generate",
        status="preview_ready",
        continuation_text="调查继续。",
        state_delta={},
        uploaded_asset_version_ids=[],
        frozen_asset_version_ids=[],
        vngraph_patch=[],
        base_session_lock_version=reading.lock_version,
        idempotency_key=f"continuation-{transition}",
        request_hash=content_hash({"transition": transition}),
    )
    db.add(continuation)
    db.flush()
    asset = Asset(
        project_id=project_id,
        asset_type="background",
        target_name="continuation background",
        status="completed",
        logical_key=f"continuation:{continuation.id}",
        taxonomy_json={},
    )
    db.add(asset)
    db.flush()
    version = AssetVersion(
        asset_id=asset.id,
        source_kind=("upload" if transition == "unpublish" else "reading_continuation"),
        source_revision_id=continuation.id,
        source_hash=content_hash({"continuation_id": continuation.id}),
        storage_object_id=storage.id,
        version_no=1,
        cache_key=content_hash({"storage_object_id": storage.id}),
        prompt_hash=content_hash("continuation background"),
        prompt_version="test-v1",
        safety_status="passed",
        rights_metadata={"origin": "generated"},
        asset_spec_json={},
        render_spec_json={},
    )
    db.add(version)
    db.flush()
    continuation.frozen_asset_version_ids = [version.id]
    confirm_reading_continuation(
        db,
        continuation=continuation,
        user_id=owner_id,
        expected_lock_version=reading.lock_version,
    )
    db.commit()

    media_url = f"/api/media/{storage.id}"
    active_response = guest.get(media_url)
    assert active_response.status_code == 200
    assert active_response.content == b"confirmed-continuation"
    assert storage.visibility == "release"
    assert active_response.headers["cache-control"] == "private, no-store"
    not_modified = guest.get(
        media_url,
        headers={"If-None-Match": active_response.headers["etag"]},
    )
    assert not_modified.status_code == 304
    assert not_modified.headers["cache-control"] == "private, no-store"

    # Historical confirmations used to persist the same object as public.
    # It must remain lifecycle-bound even before those rows are normalized.
    storage.visibility = "public"
    db.commit()
    legacy_active_response = guest.get(media_url)
    assert legacy_active_response.status_code == 200
    assert legacy_active_response.headers["cache-control"] == "private, no-store"

    if transition == "unpublish":
        publication.active_release_id = None
        publication.published_at = None
        publication.lock_version += 1
        db.flush()
        release.status = "withdrawn"
        release.withdrawn_at = datetime.now(timezone.utc)
    else:
        next_manifest = {"story": {"nodes": []}, "version": 2}
        next_release = ProjectRelease(
            project_id=project_id,
            version=2,
            status="published",
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            manifest_json=next_manifest,
            manifest_hash=content_hash(next_manifest),
            created_by=owner_id,
            published_at=datetime.now(timezone.utc),
        )
        db.add(next_release)
        db.flush()
        publication.active_release_id = next_release.id
        publication.published_at = next_release.published_at
        publication.lock_version += 1
        db.flush()
        release.status = "superseded"
    db.commit()

    assert guest.get(media_url).status_code == 404


def test_quarantined_deleted_and_reference_audio_are_never_public(media_app):
    app, db, backend = media_app
    owner_email = "private-reference@example.com"
    owner = _client(app, owner_email)
    guest = TestClient(app)
    project_id = _project(owner, "private reference")
    owner_id = _user_id(db, owner_email)

    reference = _store(
        db,
        backend,
        owner_id=owner_id,
        project_id=project_id,
        namespace="voice-reference",
        visibility="public",
    )
    assert reference.visibility == "private"
    assert guest.get(f"/api/media/{reference.id}").status_code == 404
    assert owner.get(f"/api/media/{reference.id}").status_code == 200

    reference.status = "quarantined"
    db.commit()
    assert owner.get(f"/api/media/{reference.id}").status_code == 404
    reference.status = "deleted"
    reference.deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert owner.get(f"/api/media/{reference.id}").status_code == 404


def test_local_backend_streaming_hash_limits_and_root_containment(tmp_path):
    backend = LocalStorageBackend(tmp_path / "objects")
    data = b"streamed-media"
    stored = backend.save_stream(
        BytesIO(data),
        namespace="images/generated",
        filename_or_suffix="ignored-name.png",
        media_type="image/png",
        max_bytes=1024,
    )
    assert stored.byte_size == len(data)
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert backend.resolve_key(stored.storage_key).read_bytes() == data
    assert "ignored-name" not in stored.storage_key

    with pytest.raises(StorageLimitExceededError):
        backend.save_stream(
            BytesIO(b"too large"),
            namespace="images",
            filename_or_suffix="x.png",
            media_type="image/png",
            max_bytes=2,
        )
    with pytest.raises(InvalidStoragePathError):
        backend.resolve_key("../outside")
    with pytest.raises(InvalidStoragePathError):
        backend.resolve_key("C:/outside")
    with pytest.raises(InvalidStoragePathError):
        backend.save_stream(
            BytesIO(b"x"),
            namespace="../../escape",
            filename_or_suffix="x.png",
            media_type="image/png",
        )


@pytest.mark.asyncio
async def test_async_reader_is_streamed_and_hashed(tmp_path):
    backend = LocalStorageBackend(tmp_path / "async-objects")

    class Reader:
        def __init__(self):
            self.parts = [b"one", b"two", b""]

        async def read(self, size=-1):
            return self.parts.pop(0)

    stored = await backend.save_async_reader(
        Reader(),
        namespace="audio",
        filename_or_suffix="clip.wav",
        media_type="audio/wav",
        max_bytes=1024,
    )
    assert stored.sha256 == hashlib.sha256(b"onetwo").hexdigest()
    assert backend.resolve_key(stored.storage_key).read_bytes() == b"onetwo"


def test_legacy_static_mount_blocks_private_voice_directories(tmp_path):
    blocked = [
        tmp_path / "voice_refs" / "project_1" / "voice_x" / "reference.wav",
        tmp_path / "tts_cache" / "voice_clone" / "voice_x" / "sample.wav",
    ]
    for path in blocked:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"private")

    app = FastAPI()
    app.mount("/static", ProjectAwareStaticFiles(directory=str(tmp_path)))
    client = TestClient(app)

    assert client.get("/static/voice_refs/project_1/voice_x/reference.wav").status_code == 404
    assert client.get("/static/tts_cache/voice_clone/voice_x/sample.wav").status_code == 404
    assert client.get("/static/VOICE_REFS/project_1/voice_x/reference.wav").status_code == 404
