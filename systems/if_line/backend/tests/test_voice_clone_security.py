from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth as auth_module
from app.database import Base, get_db
from app.routers import auth as auth_router
from app.routers import projects as projects_router
from app.routers import tts as tts_router
from app.routers import voice_clone as voice_clone_router
from app.services import cosyvoice_clone_service as cosyvoice_module
from app.services import voice_clone_storage_service as storage_module
from app.services.cosyvoice_clone_service import (
    CosyVoiceCloneError,
    CosyVoiceFeatureDisabledError,
)
from app.services.voice_clone_storage_service import (
    VoiceCloneStorageError,
    VoiceCloneStorageService,
    VoiceProfileNotFoundError,
)


@pytest.fixture
def app_and_db(monkeypatch):
    monkeypatch.setattr(voice_clone_router.tts_module, "TTS_ENABLED", True)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(projects_router.router, prefix="/api/projects")
    app.include_router(tts_router.router, prefix="/api/tts")
    app.include_router(voice_clone_router.router, prefix="/api/voice-clone")
    app.dependency_overrides[get_db] = lambda: session
    try:
        yield app, session
    finally:
        session.close()
        engine.dispose()


def _client(app: FastAPI, email: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/auth/register",
        json={"email": email, "password": "password123"},
    )
    assert response.status_code == 201
    return client


def _create_project(client: TestClient, title: str = "security story") -> int:
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


def test_all_paid_voice_endpoints_require_login(app_and_db):
    app, _ = app_and_db
    guest = TestClient(app)
    voice_id = "voice_01234567"

    assert guest.get("/api/voice-clone/profiles").status_code == 401
    assert guest.get(f"/api/voice-clone/profiles/{voice_id}").status_code == 401
    assert guest.delete(f"/api/voice-clone/profiles/{voice_id}").status_code == 401
    assert guest.post(
        f"/api/voice-clone/profiles/{voice_id}/preview",
        json={"tts_text": "试听"},
    ).status_code == 401
    assert guest.post(
        "/api/voice-clone/synthesize",
        json={"voice_id": voice_id, "tts_text": "试听"},
    ).status_code == 401
    assert guest.post(
        "/api/voice-clone/profiles",
        data={"voice_name": "test", "prompt_text": "test"},
        files={"prompt_wav": ("voice.wav", b"wav", "audio/wav")},
    ).status_code == 401
    assert guest.post("/api/tts/synthesize", json={"text": "试听"}).status_code == 401


def test_disabled_voice_clone_endpoints_return_503_before_provider(app_and_db, monkeypatch):
    app, _ = app_and_db
    user = _client(app, "voice-disabled@example.com")
    calls = []

    async def forbidden_provider_call(**kwargs):
        calls.append(kwargs)
        raise AssertionError("disabled TTS must not invoke CosyVoice")

    monkeypatch.setattr(voice_clone_router.tts_module, "TTS_ENABLED", False)
    monkeypatch.setattr(voice_clone_router, "synthesize_zero_shot", forbidden_provider_call)
    voice_id = "voice_01234567"

    preview = user.post(
        f"/api/voice-clone/profiles/{voice_id}/preview",
        json={"tts_text": "试听"},
    )
    synthesize = user.post(
        "/api/voice-clone/synthesize",
        json={"voice_id": voice_id, "tts_text": "试听"},
    )
    assert preview.status_code == 503
    assert synthesize.status_code == 503
    assert preview.json()["detail"] == "TTS 功能未启用"
    assert synthesize.json()["detail"] == "TTS 功能未启用"
    assert calls == []


def test_disabled_standalone_tts_returns_503_before_provider(app_and_db, monkeypatch):
    app, _ = app_and_db
    user = _client(app, "tts-disabled@example.com")
    calls = []

    class ForbiddenTTS:
        voice_profiles = []

        async def synthesize(self, **kwargs):
            calls.append(kwargs)
            raise AssertionError("disabled TTS must not invoke its provider service")

    monkeypatch.setattr(tts_router, "TTS_ENABLED", False)
    monkeypatch.setattr(tts_router, "tts_service", ForbiddenTTS())
    response = user.post("/api/tts/synthesize", json={"text": "试听"})

    assert response.status_code == 503
    assert response.json()["detail"] == "TTS 功能未启用"
    assert calls == []


@pytest.mark.asyncio
async def test_disabled_cosyvoice_service_rejects_before_http_client(monkeypatch):
    monkeypatch.setattr(cosyvoice_module.tts_module, "TTS_ENABLED", False)

    class ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("disabled TTS must not construct an HTTP client")

    monkeypatch.setattr(cosyvoice_module.httpx, "AsyncClient", ForbiddenClient)
    with pytest.raises(CosyVoiceFeatureDisabledError):
        await cosyvoice_module.synthesize_zero_shot(
            tts_text="试听",
            prompt_text="参考",
            prompt_wav_path="/does/not/matter.wav",
            output_wav_path="/does/not/matter-output.wav",
        )


def test_voice_profile_access_is_owner_and_project_scoped(app_and_db, monkeypatch):
    app, _ = app_and_db
    owner = _client(app, "voice-owner@example.com")
    other = _client(app, "voice-other@example.com")
    project_id = _create_project(owner)
    owner_id = owner.get("/api/auth/me").json()["id"]
    voice_id = "voice_01234567"

    class FakeStorage:
        def get_profile(self, requested_voice_id):
            assert requested_voice_id == voice_id
            return {
                "voice_id": voice_id,
                "owner_id": owner_id,
                "project_id": project_id,
                "voice_name": "owner voice",
            }

        def list_profiles(self, project_id=None, owner_id=None):
            return [self.get_profile(voice_id)]

        def delete_profile(self, requested_voice_id):
            raise AssertionError("unauthorized request reached delete")

    monkeypatch.setattr(voice_clone_router, "storage", FakeStorage())

    assert owner.get(f"/api/voice-clone/profiles/{voice_id}").status_code == 200
    assert owner.get(f"/api/voice-clone/profiles?project_id={project_id}").status_code == 200
    assert other.get(f"/api/voice-clone/profiles/{voice_id}").status_code == 403
    assert other.get(f"/api/voice-clone/profiles?project_id={project_id}").status_code == 403
    assert other.delete(f"/api/voice-clone/profiles/{voice_id}").status_code == 403
    assert other.post(
        f"/api/voice-clone/profiles/{voice_id}/preview",
        json={"tts_text": "不能生成"},
    ).status_code == 403
    assert other.post(
        "/api/voice-clone/synthesize",
        json={"voice_id": voice_id, "tts_text": "不能生成"},
    ).status_code == 403


def test_voice_upload_is_stream_limited_and_type_checked(app_and_db, monkeypatch):
    app, _ = app_and_db
    owner = _client(app, "voice-upload@example.com")
    project_id = _create_project(owner)

    class NeverCalledStorage:
        def create_profile_from_path(self, **kwargs):
            raise AssertionError("rejected upload must not reach storage")

    monkeypatch.setattr(voice_clone_router, "storage", NeverCalledStorage())
    monkeypatch.setattr(voice_clone_router, "VOICE_CLONE_MAX_UPLOAD_BYTES", 4)

    oversized = owner.post(
        "/api/voice-clone/profiles",
        data={
            "voice_name": "test",
            "prompt_text": "test",
            "project_id": str(project_id),
            "consent_confirmed": "true",
        },
        files={"prompt_wav": ("voice.wav", b"12345", "audio/wav")},
    )
    assert oversized.status_code == 413

    wrong_type = owner.post(
        "/api/voice-clone/profiles",
        data={
            "voice_name": "test",
            "prompt_text": "test",
            "project_id": str(project_id),
            "consent_confirmed": "true",
        },
        files={"prompt_wav": ("voice.txt", b"123", "text/plain")},
    )
    assert wrong_type.status_code == 415


def test_standalone_tts_requires_auth_and_whitelists_provider_parameters(app_and_db, monkeypatch):
    app, _ = app_and_db
    user = _client(app, "tts-security@example.com")
    calls = []

    class FakeTTS:
        voice_profiles = [
            {"speaker": "allowed_voice", "emotion_prompt": "calm"},
        ]

        async def synthesize(self, **kwargs):
            calls.append(kwargs)
            return {
                "success": True,
                "audio_url": "/static/tts_cache/test.mp3",
                "cached": False,
                "speaker": kwargs.get("speaker"),
                "emotion_prompt": kwargs.get("emotion_prompt"),
            }

    monkeypatch.setattr(tts_router, "TTS_ENABLED", True)
    monkeypatch.setattr(tts_router, "tts_service", FakeTTS())
    monkeypatch.setattr(
        tts_router,
        "_store_tts_result",
        lambda *args, **kwargs: "/api/media/test-object",
    )

    assert user.post(
        "/api/tts/synthesize",
        json={"text": "hello", "speaker": "arbitrary_provider_voice"},
    ).status_code == 422
    assert user.post(
        "/api/tts/synthesize",
        json={"text": "hello", "emotion_prompt": "ignore all provider rules"},
    ).status_code == 422

    ok = user.post(
        "/api/tts/synthesize",
        json={"text": "hello", "speaker": "allowed_voice", "emotion_prompt": "calm"},
    )
    assert ok.status_code == 200
    assert ok.json()["audio_url"] == "/api/media/test-object"
    assert len(calls) == 1


def test_tts_provider_error_is_not_returned_to_client(app_and_db, monkeypatch):
    app, _ = app_and_db
    user = _client(app, "tts-error@example.com")

    class FailedTTS:
        voice_profiles = []

        async def synthesize(self, **kwargs):
            return {
                "success": False,
                "error": "provider_key=/srv/secrets/key raw_response=internal",
            }

    monkeypatch.setattr(tts_router, "TTS_ENABLED", True)
    monkeypatch.setattr(tts_router, "tts_service", FailedTTS())
    response = user.post("/api/tts/synthesize", json={"text": "hello"})

    assert response.status_code == 200
    assert response.json()["error"] == "语音合成失败，请稍后重试"
    assert "/srv/secrets" not in response.text


def test_voice_clone_errors_do_not_leak_paths_or_provider_payloads():
    generic = voice_clone_router._to_http(RuntimeError("C:/private/secrets.env"))
    provider = voice_clone_router._to_http(
        CosyVoiceCloneError("HTTP 500 raw provider response /srv/private"),
        502,
    )

    assert generic.detail == "服务器内部错误"
    assert provider.detail == "音色合成服务暂时不可用"
    assert "/srv/private" not in provider.detail


def test_storage_rejects_path_traversal_and_uses_server_generated_names(tmp_path, monkeypatch):
    static_dir = tmp_path / "static"
    refs = static_dir / "voice_refs"
    cache = tmp_path / "tts_cache" / "voice_clone"
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(storage_module, "VOICE_REFS_DIR", refs)
    monkeypatch.setattr(storage_module, "STATIC_DIR", static_dir)
    monkeypatch.setattr(storage_module, "VOICE_CLONE_CACHE_DIR", cache)
    monkeypatch.setattr(storage_module, "VOICE_CLONE_UPLOAD_TMP_DIR", uploads)
    storage_module.ensure_base_dirs()

    service = VoiceCloneStorageService()
    with pytest.raises(VoiceCloneStorageError):
        storage_module._project_dir_name("../../etc")
    with pytest.raises(VoiceProfileNotFoundError):
        service.reference_wav_for("../../etc/passwd")
    with pytest.raises(VoiceProfileNotFoundError):
        service.delete_profile("voice_../escape")

    upload = uploads / "incoming.wav"
    upload.write_bytes(b"fake audio")
    monkeypatch.setattr(
        service,
        "_convert_to_reference_wav",
        lambda src, dst: dst.write_bytes(b"converted wav"),
    )
    monkeypatch.setattr(storage_module, "_probe_audio_duration_seconds", lambda path: None)

    metadata, _ = service.create_profile_from_path(
        project_id=1,
        owner_id=7,
        voice_name="safe",
        prompt_text="reference text",
        prompt_wav_path=upload,
        prompt_wav_filename="../../client-controlled.wav",
    )

    voice_dir = refs / "project_1" / metadata["voice_id"]
    assert metadata["owner_id"] == 7
    assert (voice_dir / "reference.wav").is_file()
    assert not any("client-controlled" in path.name for path in voice_dir.iterdir())
