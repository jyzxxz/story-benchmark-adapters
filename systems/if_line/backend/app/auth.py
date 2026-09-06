"""
Authentication helpers for email/password accounts and server-side sessions.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, Request, Response, Security, status
from fastapi.security import APIKeyCookie
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserSession


# The cookie name is part of the frozen HTTP contract and must not vary by
# deployment. Cookie security attributes remain deployment-configurable below.
AUTH_COOKIE_NAME = "sid"
AUTH_SESSION_TTL_DAYS = int(os.getenv("AUTH_SESSION_TTL_DAYS", "14"))
AUTH_MAX_SESSIONS = int(os.getenv("AUTH_MAX_SESSIONS", "3"))
AUTH_PASSWORD_ITERATIONS = int(os.getenv("AUTH_PASSWORD_ITERATIONS", "260000"))
# 生产部署在 HTTPS 反向代理后应设置为 true
AUTH_COOKIE_SECURE = os.getenv("AUTH_COOKIE_SECURE", "false").lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax")
# 登录恒定延迟（秒），用于缓解用户名枚举与响应计时侧信道
AUTH_LOGIN_CONSTANT_DELAY = float(os.getenv("AUTH_LOGIN_CONSTANT_DELAY", "0.4"))
# 简易内存限流：每 IP 在窗口内允许的登录/注册失败次数
AUTH_RATELIMIT_WINDOW_SECONDS = float(os.getenv("AUTH_RATELIMIT_WINDOW_SECONDS", "60"))
AUTH_RATELIMIT_MAX_FAILURES = int(os.getenv("AUTH_RATELIMIT_MAX_FAILURES", "10"))
AUTH_RATELIMIT_BACKEND = os.getenv("AUTH_RATELIMIT_BACKEND", "memory").strip().lower()
AUTH_RATELIMIT_REDIS_URL = os.getenv("AUTH_RATELIMIT_REDIS_URL") or os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
AUTH_LAST_SEEN_WRITE_INTERVAL_SECONDS = int(os.getenv("AUTH_LAST_SEEN_WRITE_INTERVAL_SECONDS", "300"))
AUTH_TRUSTED_PROXY_IPS = {
    value.strip()
    for value in os.getenv("AUTH_TRUSTED_PROXY_IPS", "127.0.0.1,::1").split(",")
    if value.strip()
}

session_cookie_scheme = APIKeyCookie(
    name=AUTH_COOKIE_NAME,
    scheme_name="cookieAuth",
    description="Server-side session identifier set by the login endpoint.",
    auto_error=False,
)


class _IpRateLimiter:
    """进程内滑动窗口限流器；仅用于防爆破，不保证分布式精确性。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = {}

    def is_limited(self, ip: str) -> bool:
        if not ip:
            ip = "unknown"
        now = time.monotonic()
        cutoff = now - AUTH_RATELIMIT_WINDOW_SECONDS
        with self._lock:
            bucket = self._buckets.get(ip)
            if not bucket:
                self._buckets[ip] = deque()
                return False
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return len(bucket) >= AUTH_RATELIMIT_MAX_FAILURES

    def record_failure(self, ip: str) -> None:
        if not ip:
            ip = "unknown"
        now = time.monotonic()
        cutoff = now - AUTH_RATELIMIT_WINDOW_SECONDS
        with self._lock:
            bucket = self._buckets.setdefault(ip, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now)

    def reset(self, ip: str) -> None:
        if not ip:
            return
        with self._lock:
            self._buckets.pop(ip, None)


class _RedisIpRateLimiter:
    """Shared sliding-window limiter for multi-worker deployments."""

    def __init__(self, url: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - production dependency guard
            raise RuntimeError("Redis rate limiting requires redis-py") from exc
        self._client = redis.Redis.from_url(
            url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )

    @staticmethod
    def _key(ip: str) -> str:
        digest = hashlib.sha256((ip or "unknown").encode("utf-8")).hexdigest()
        return f"if-line:auth-fail:{digest}"

    def is_limited(self, ip: str) -> bool:
        now = time.time()
        key = self._key(ip)
        with self._client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - AUTH_RATELIMIT_WINDOW_SECONDS)
            pipe.zcard(key)
            pipe.expire(key, max(1, int(AUTH_RATELIMIT_WINDOW_SECONDS) + 1))
            _, count, _ = pipe.execute()
        return int(count) >= AUTH_RATELIMIT_MAX_FAILURES

    def record_failure(self, ip: str) -> None:
        now = time.time()
        key = self._key(ip)
        member = f"{now:.6f}:{secrets.token_hex(4)}"
        with self._client.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - AUTH_RATELIMIT_WINDOW_SECONDS)
            pipe.zadd(key, {member: now})
            pipe.expire(key, max(1, int(AUTH_RATELIMIT_WINDOW_SECONDS) + 1))
            pipe.execute()

    def reset(self, ip: str) -> None:
        if ip:
            self._client.delete(self._key(ip))


_auth_rate_limiter = (
    _RedisIpRateLimiter(AUTH_RATELIMIT_REDIS_URL)
    if AUTH_RATELIMIT_BACKEND == "redis"
    else _IpRateLimiter()
)


def check_auth_rate_limit(ip: str) -> None:
    """失败次数超限时抛 429。在登录/注册入口前调用。"""
    try:
        limited = _auth_rate_limiter.is_limited(ip)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证保护服务暂时不可用",
            headers={"Retry-After": "5"},
        ) from exc
    if limited:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(int(AUTH_RATELIMIT_WINDOW_SECONDS))},
        )


def record_auth_failure(ip: str) -> None:
    try:
        _auth_rate_limiter.record_failure(ip)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="认证保护服务暂时不可用",
            headers={"Retry-After": "5"},
        ) from exc


def reset_auth_failures(ip: str) -> None:
    try:
        _auth_rate_limiter.reset(ip)
    except Exception:
        # A successful credential check must not create a committed session
        # followed by an error solely because cleanup of the failure bucket
        # raced with a transient Redis outage.
        return


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password is required")

    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        AUTH_PASSWORD_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${AUTH_PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
    except (AttributeError, ValueError):
        return False

    actual = hashlib.pbkdf2_hmac(
        "sha256",
        (password or "").encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return secrets.compare_digest(actual, expected)


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def revoke_session_token(db: Session, token: Optional[str]) -> bool:
    if not token:
        return False

    now = datetime.utcnow()
    session = db.query(UserSession).filter(
        UserSession.session_token_hash == _hash_session_token(token),
        UserSession.revoked_at.is_(None),
    ).first()
    if not session:
        return False

    session.revoked_at = now
    db.commit()
    return True


def get_user_for_session_token(
    db: Session,
    token: Optional[str],
    *,
    update_last_seen: bool = False,
) -> Optional[User]:
    if not token:
        return None

    now = datetime.utcnow()
    session = db.query(UserSession).filter(
        UserSession.session_token_hash == _hash_session_token(token),
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    ).first()
    if not session or not session.user or not session.user.is_active:
        return None

    if update_last_seen and (
        session.last_seen_at is None
        or session.last_seen_at <= now - timedelta(seconds=AUTH_LAST_SEEN_WRITE_INTERVAL_SECONDS)
    ):
        session.last_seen_at = now
        db.commit()
    return session.user


def _get_request_ip(request: Request) -> str:
    peer_ip = (request.client.host if request.client else "")[:64]
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for and peer_ip in AUTH_TRUSTED_PROXY_IPS:
        return forwarded_for.split(",", 1)[0].strip()[:64]
    return peer_ip


def get_request_ip(request: Request) -> str:
    """公开版本，供路由层在限流时使用。"""
    return _get_request_ip(request)


def _set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    response.set_cookie(
        key=AUTH_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=AUTH_COOKIE_SECURE,
        samesite=AUTH_COOKIE_SAMESITE,
        max_age=int((expires_at - datetime.utcnow()).total_seconds()),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        key=AUTH_COOKIE_NAME,
        path="/",
        samesite=AUTH_COOKIE_SAMESITE,
        secure=AUTH_COOKIE_SECURE,
        httponly=True,
    )


def _revoke_excess_sessions(db: Session, user_id: int) -> None:
    if AUTH_MAX_SESSIONS <= 0:
        return

    now = datetime.utcnow()
    active_sessions = db.query(UserSession).filter(
        UserSession.user_id == user_id,
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    ).order_by(UserSession.last_seen_at.desc(), UserSession.created_at.desc()).all()

    for session in active_sessions[AUTH_MAX_SESSIONS:]:
        session.revoked_at = now


def create_user_session(
    db: Session,
    user: User,
    request: Request,
    response: Response,
) -> UserSession:
    now = datetime.utcnow()
    expires_at = now + timedelta(days=AUTH_SESSION_TTL_DAYS)
    token = secrets.token_urlsafe(48)
    session = UserSession(
        user_id=user.id,
        session_token_hash=_hash_session_token(token),
        device_id=(request.headers.get("x-device-id") or "")[:128],
        user_agent=(request.headers.get("user-agent") or "")[:500],
        ip_address=_get_request_ip(request),
        expires_at=expires_at,
        created_at=now,
        last_seen_at=now,
    )
    db.add(session)
    db.flush()
    _revoke_excess_sessions(db, user.id)
    _set_session_cookie(response, token, expires_at)
    return session


def get_current_session(
    sid: Optional[str] = Security(session_cookie_scheme),
    db: Session = Depends(get_db),
) -> UserSession:
    if not sid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已过期",
        )

    now = datetime.utcnow()
    session = db.query(UserSession).filter(
        UserSession.session_token_hash == _hash_session_token(sid),
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now,
    ).first()
    if not session or not session.user or not session.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已过期",
        )

    if (
        session.last_seen_at is None
        or session.last_seen_at <= now - timedelta(seconds=AUTH_LAST_SEEN_WRITE_INTERVAL_SECONDS)
    ):
        session.last_seen_at = now
        db.commit()
        db.refresh(session)
    return session


def get_current_user(session: UserSession = Depends(get_current_session)) -> User:
    return session.user


def get_optional_user(
    sid: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> Optional[User]:
    return get_user_for_session_token(db, sid, update_last_seen=True)
