from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app import auth as auth_module
from app.database import Base, get_db
from app.models import User, UserSession
from app.routers import auth as auth_router


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
def client(db_session, monkeypatch):
    monkeypatch.setattr(auth_module, "AUTH_MAX_SESSIONS", 3)
    monkeypatch.setattr(auth_module, "AUTH_SESSION_TTL_DAYS", 14)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _register(client: TestClient, email: str = "User@Example.com", password: str = "password123"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "display_name": "Tester"},
    )


def test_register_creates_user_and_sets_session_cookie(client, db_session):
    response = _register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["user"]["email"] == "user@example.com"
    assert body["user"]["display_name"] == "Tester"
    assert "sid" in response.cookies

    user = db_session.query(User).filter(User.email == "user@example.com").one()
    assert user.password_hash.startswith("pbkdf2_sha256$")
    assert user.password_hash != "password123"
    assert db_session.query(UserSession).filter(UserSession.user_id == user.id).count() == 1

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "user@example.com"


def test_guest_session_creates_isolated_non_login_account_and_reuses_cookie(client, db_session):
    response = client.post("/api/auth/guest")

    assert response.status_code == 201
    assert "sid" in response.cookies
    guest = response.json()["user"]
    assert guest["email"].endswith("@guest.ifline.local")
    assert guest["display_name"].startswith("游客-")

    persisted = db_session.query(User).filter(User.id == guest["id"]).one()
    assert persisted.password_hash.startswith("guest-login-disabled$")
    assert persisted.quota_daily == 100

    reused = client.post("/api/auth/guest")
    assert reused.status_code == 201
    assert reused.json()["user"]["id"] == guest["id"]
    assert db_session.query(User).filter(User.email.like("%@guest.ifline.local")).count() == 1


def test_register_rejects_blank_display_name(client):
    response = client.post(
        "/api/auth/register",
        json={"email": "blank-name@example.com", "password": "password123", "display_name": "   "},
    )
    assert response.status_code == 422


def test_login_rejects_wrong_password_and_accepts_correct_password(client):
    _register(client, email="login@example.com", password="password123")
    client.post("/api/auth/logout")

    wrong = client.post(
        "/api/auth/login",
        json={"email": "login@example.com", "password": "bad"},
    )
    assert wrong.status_code == 401

    ok = client.post(
        "/api/auth/login",
        json={"email": "login@example.com", "password": "password123"},
    )
    assert ok.status_code == 200
    assert "sid" in ok.cookies


def test_logout_revokes_session(client):
    _register(client, email="logout@example.com", password="password123")

    before = client.get("/api/auth/me")
    assert before.status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200

    after = client.get("/api/auth/me")
    assert after.status_code == 401


def test_logout_without_cookie_is_idempotent(client):
    response = client.post("/api/auth/logout")
    assert response.status_code == 200


def test_update_me_changes_display_name_and_avatar(client, db_session):
    _register(client, email="profile@example.com", password="password123")

    response = client.patch(
        "/api/auth/me",
        json={"display_name": "New Name", "avatar_url": "https://example.com/avatar.png"},
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "New Name"
    assert response.json()["avatar_url"] == "https://example.com/avatar.png"

    user = db_session.query(User).filter(User.email == "profile@example.com").one()
    assert user.display_name == "New Name"
    assert user.avatar_url == "https://example.com/avatar.png"


def test_update_me_rejects_unsafe_avatar_url(client):
    _register(client, email="avatar@example.com", password="password123")

    javascript_url = client.patch(
        "/api/auth/me",
        json={"avatar_url": "javascript:alert(1)"},
    )
    data_url = client.patch(
        "/api/auth/me",
        json={"avatar_url": "data:text/html,<script>alert(1)</script>"},
    )
    relative_url = client.patch(
        "/api/auth/me",
        json={"avatar_url": "/static/avatars/avatar.png"},
    )

    assert javascript_url.status_code == 422
    assert data_url.status_code == 422
    assert relative_url.status_code == 200
    assert relative_url.json()["avatar_url"] == "/static/avatars/avatar.png"


def test_login_keeps_at_most_three_active_sessions(client, db_session):
    _register(client, email="multi@example.com", password="password123")
    client.post("/api/auth/logout")

    clients = []
    for _ in range(4):
        c = TestClient(client.app)
        response = c.post(
            "/api/auth/login",
            json={"email": "multi@example.com", "password": "password123"},
        )
        assert response.status_code == 200
        clients.append(c)

    user = db_session.query(User).filter(User.email == "multi@example.com").one()
    active_count = db_session.query(UserSession).filter(
        UserSession.user_id == user.id,
        UserSession.revoked_at.is_(None),
    ).count()
    assert active_count == 3

    assert clients[0].get("/api/auth/me").status_code == 401
    assert clients[1].get("/api/auth/me").status_code == 200
    assert clients[2].get("/api/auth/me").status_code == 200
    assert clients[3].get("/api/auth/me").status_code == 200


def test_login_rate_limit_after_repeated_failures(client, monkeypatch):
    """同一 IP 连续登录失败超过阈值后应返回 429。"""
    monkeypatch.setattr(auth_module, "AUTH_RATELIMIT_MAX_FAILURES", 3)
    monkeypatch.setattr(auth_module, "AUTH_RATELIMIT_WINDOW_SECONDS", 60)
    # 重置限流器状态，避免被前置测试污染
    auth_module._auth_rate_limiter = auth_module._IpRateLimiter()

    _register(client, email="ratelimit@example.com", password="password123")
    client.post("/api/auth/logout")

    for _ in range(3):
        resp = client.post(
            "/api/auth/login",
            json={"email": "ratelimit@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401

    blocked = client.post(
        "/api/auth/login",
        json={"email": "ratelimit@example.com", "password": "wrong"},
    )
    assert blocked.status_code == 429


def test_login_resets_failures_on_success(client, monkeypatch):
    """登录成功后应清空该 IP 的失败计数。"""
    monkeypatch.setattr(auth_module, "AUTH_RATELIMIT_MAX_FAILURES", 5)
    monkeypatch.setattr(auth_module, "AUTH_RATELIMIT_WINDOW_SECONDS", 60)
    auth_module._auth_rate_limiter = auth_module._IpRateLimiter()

    _register(client, email="reset@example.com", password="password123")
    client.post("/api/auth/logout")

    for _ in range(2):
        client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "wrong"},
        )

    ok = client.post(
        "/api/auth/login",
        json={"email": "reset@example.com", "password": "password123"},
    )
    assert ok.status_code == 200

    # 成功后再来两次失败不应立即触发限流（计数已清零）
    for _ in range(2):
        resp = client.post(
            "/api/auth/login",
            json={"email": "reset@example.com", "password": "wrong"},
        )
        assert resp.status_code == 401
