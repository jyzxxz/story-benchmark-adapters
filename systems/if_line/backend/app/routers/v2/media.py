"""Authenticated and release-aware media delivery."""
from __future__ import annotations

import re
from typing import Iterator

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.application.storage_service import (
    LocalStorageBackend,
    StorageObjectMissingError,
    can_read_storage_object,
    get_local_storage_backend,
    is_unconditionally_public_storage_object,
    normalize_media_type,
)
from app.auth import AUTH_COOKIE_NAME, get_user_for_session_token
from app.database import get_db
from app.models import User
from app.models_v2 import StorageObject


router = APIRouter(prefix="/media", tags=["media"])
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_EXTENSION_BY_MEDIA_TYPE = {
    "audio/flac": ".flac",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
    "audio/x-flac": ".flac",
    "audio/x-wav": ".wav",
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}


def get_media_user(
    sid: str | None = Cookie(default=None, alias=AUTH_COOKIE_NAME),
    db: Session = Depends(get_db),
) -> User | None:
    # Media reads are frequent; do not write last_seen_at on every byte-range
    # request. Authentication state is still checked against the DB.
    return get_user_for_session_token(db, sid, update_last_seen=False)


def _parse_range_header(value: str | None, size: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if size <= 0 or not value.startswith("bytes="):
        raise ValueError("invalid range")
    spec = value[6:].strip()
    if not spec or "," in spec or "-" not in spec:
        raise ValueError("invalid range")
    start_raw, end_raw = (part.strip() for part in spec.split("-", 1))
    if not start_raw:
        if not end_raw.isdigit():
            raise ValueError("invalid suffix range")
        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise ValueError("invalid suffix range")
        start = max(0, size - suffix_length)
        return start, size - 1
    if not start_raw.isdigit() or (end_raw and not end_raw.isdigit()):
        raise ValueError("invalid range")
    start = int(start_raw)
    if start >= size:
        raise ValueError("range starts beyond object")
    end = int(end_raw) if end_raw else size - 1
    if end < start:
        raise ValueError("range is reversed")
    return start, min(end, size - 1)


def _safe_delivery_type(obj: StorageObject) -> tuple[str, bool]:
    try:
        return normalize_media_type(obj.media_type), False
    except Exception:
        # A legacy or manually corrupted row must never become executable
        # active content. Deliver it as an attachment only.
        return "application/octet-stream", True


def _content_disposition(obj: StorageObject, media_type: str, *, attachment: bool) -> str:
    suffix = _EXTENSION_BY_MEDIA_TYPE.get(media_type, ".bin")
    disposition = "attachment" if attachment else "inline"
    safe_id = re.sub(r"[^a-zA-Z0-9-]", "", obj.id or "") or "media"
    return f'{disposition}; filename="{safe_id}{suffix}"'


# GET /api/media/{storage_object_id}
@router.get("/{storage_object_id}")
def read_media(
    storage_object_id: str,
    download: bool = Query(default=False),
    range_header: str | None = Header(default=None, alias="Range"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_media_user),
    backend: LocalStorageBackend = Depends(get_local_storage_backend),
):
    obj = db.query(StorageObject).filter(StorageObject.id == storage_object_id).first()
    if not obj or not can_read_storage_object(db, obj, user):
        raise HTTPException(status_code=404, detail="媒体不存在")
    if obj.storage_backend != backend.backend_name:
        raise HTTPException(status_code=503, detail="媒体存储暂时不可用")

    try:
        stat = backend.stat(obj.storage_key)
    except StorageObjectMissingError as exc:
        raise HTTPException(status_code=404, detail="媒体不存在") from exc
    except Exception as exc:
        raise HTTPException(status_code=404, detail="媒体不存在") from exc
    if stat.st_size != obj.byte_size:
        raise HTTPException(status_code=503, detail="媒体文件校验失败")

    etag_value = obj.sha256 if _SHA256_RE.fullmatch(obj.sha256 or "") else obj.id
    etag_value = re.sub(r"[^a-zA-Z0-9-]", "", etag_value or "") or "media"
    etag = f'"{etag_value}"'
    if if_none_match == etag and range_header is None:
        is_public = is_unconditionally_public_storage_object(db, obj)
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": (
                    "public, max-age=31536000, immutable"
                    if is_public
                    else "private, no-store"
                ),
            },
        )

    try:
        byte_range = _parse_range_header(range_header, obj.byte_size)
    except ValueError as exc:
        raise HTTPException(
            status_code=416,
            detail="Range 请求无效",
            headers={"Content-Range": f"bytes */{obj.byte_size}"},
        ) from exc

    media_type, force_attachment = _safe_delivery_type(obj)
    is_public = is_unconditionally_public_storage_object(db, obj)
    cache_control = (
        "public, max-age=31536000, immutable"
        if is_public
        else "private, no-store"
    )
    headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": cache_control,
        "Content-Disposition": _content_disposition(
            obj,
            media_type,
            attachment=download or force_attachment,
        ),
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }

    if byte_range is None:
        start, end = 0, max(0, obj.byte_size - 1)
        status_code = 200
    else:
        start, end = byte_range
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{obj.byte_size}"
    content_length = 0 if obj.byte_size == 0 else end - start + 1
    headers["Content-Length"] = str(content_length)

    body: Iterator[bytes] = backend.iter_range(
        obj.storage_key,
        start=start,
        length=content_length,
    )
    return StreamingResponse(
        body,
        status_code=status_code,
        media_type=media_type,
        headers=headers,
    )
