"""
Authentication routes.
"""
import asyncio
import secrets
from datetime import datetime

from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import (
    AUTH_COOKIE_NAME,
    AUTH_LOGIN_CONSTANT_DELAY,
    check_auth_rate_limit,
    clear_session_cookie,
    create_user_session,
    get_user_for_session_token,
    get_current_user,
    get_request_ip,
    hash_password,
    normalize_email,
    record_auth_failure,
    reset_auth_failures,
    revoke_session_token,
    verify_password,
)
from app.database import get_db
from app.models import User
from app.schemas import AuthResponse, UserLoginRequest, UserRegisterRequest, UserResponse, UserUpdateRequest

router = APIRouter()


GUEST_EMAIL_DOMAIN = "guest.ifline.local"


async def _apply_login_constant_delay() -> None:
    """恒定延迟，缓解用户名/密码枚举的计时侧信道。"""
    if AUTH_LOGIN_CONSTANT_DELAY > 0:
        await asyncio.sleep(AUTH_LOGIN_CONSTANT_DELAY)


def _default_display_name(email: str) -> str:
    name = email.split("@", 1)[0].strip()
    return name[:100] or "user"


# POST /api/auth/guest
@router.post("/guest", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def guest_session(
    request: Request,
    response: Response,
    sid: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    """Create a browser-scoped guest account without showing a login wall.

    Guest accounts are normal owners for projects they create, so every
    existing generation workflow keeps its authorization, quota and audit
    semantics.  A valid existing session is reused instead of silently
    replacing a registered account.
    """

    current = get_user_for_session_token(db, sid, update_last_seen=True)
    if current:
        return AuthResponse(user=UserResponse.model_validate(current))

    suffix = secrets.token_hex(12)
    user = User(
        email=f"guest-{suffix}@{GUEST_EMAIL_DOMAIN}",
        # This deliberately does not use the password hash format, so a guest
        # identity can never be recovered through the password login route.
        password_hash=f"guest-login-disabled${secrets.token_hex(32)}",
        display_name=f"游客-{suffix[:6]}",
        quota_total=1000,
        quota_daily=100,
        quota_used_total=0,
        quota_used_daily=0,
        quota_reset_at=datetime.utcnow(),
    )
    try:
        db.add(user)
        db.flush()
        create_user_session(db, user, request, response)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        clear_session_cookie(response)
        raise HTTPException(status_code=503, detail="游客空间创建失败，请重试") from exc

    db.refresh(user)
    return AuthResponse(user=UserResponse.model_validate(user))


# POST /api/auth/register
@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(
    payload: UserRegisterRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = get_request_ip(request)
    check_auth_rate_limit(ip)

    email = normalize_email(payload.email)
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        record_auth_failure(ip)
        raise HTTPException(status_code=409, detail="该邮箱已注册")

    try:
        user = User(
            email=email,
            password_hash=hash_password(payload.password),
            display_name=(payload.display_name or _default_display_name(email)).strip()[:100],
            quota_reset_at=datetime.utcnow(),
        )
        db.add(user)
        db.flush()
        create_user_session(db, user, request, response)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        clear_session_cookie(response)
        record_auth_failure(ip)
        raise HTTPException(status_code=409, detail="该邮箱已注册") from exc

    reset_auth_failures(ip)
    db.refresh(user)
    return AuthResponse(user=UserResponse.model_validate(user))


# POST /api/auth/login
@router.post("/login", response_model=AuthResponse)
async def login(
    payload: UserLoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = get_request_ip(request)
    check_auth_rate_limit(ip)

    email = normalize_email(payload.email)
    user = db.query(User).filter(User.email == email).first()
    # 无论用户是否存在，都执行一次密码哈希运算，使响应时间趋于恒定
    if user:
        password_ok = verify_password(payload.password, user.password_hash)
    else:
        verify_password(payload.password, "pbkdf2_sha256$260000$" + "0" * 32 + "$" + "0" * 64)
        password_ok = False

    await _apply_login_constant_delay()

    if not user or not password_ok:
        record_auth_failure(ip)
        raise HTTPException(status_code=401, detail="邮箱或密码不正确")
    if not user.is_active:
        record_auth_failure(ip)
        raise HTTPException(status_code=403, detail="账号已停用")

    create_user_session(db, user, request, response)
    db.commit()
    db.refresh(user)
    reset_auth_failures(ip)
    return AuthResponse(user=UserResponse.model_validate(user))


# POST /api/auth/logout
@router.post("/logout")
async def logout(
    response: Response,
    sid: Optional[str] = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    db: Session = Depends(get_db),
):
    revoke_session_token(db, sid)
    clear_session_cookie(response)
    return {"message": "已退出登录"}


# GET /api/auth/me
@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user


# PATCH /api/auth/me
@router.patch("/me", response_model=UserResponse)
async def update_me(
    payload: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if "display_name" in payload.model_fields_set:
        if payload.display_name is None:
            raise HTTPException(status_code=422, detail="显示名不能为空")
        current_user.display_name = payload.display_name
    if "avatar_url" in payload.model_fields_set:
        current_user.avatar_url = payload.avatar_url
    current_user.updated_at = datetime.utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="该显示名已被占用")
    db.refresh(current_user)
    return current_user
