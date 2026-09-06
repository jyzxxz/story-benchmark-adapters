"""Private media persistence and authorization for the v2 media gateway."""
from __future__ import annotations

import hashlib
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator, Optional, Protocol

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.public_release_service import (
    active_release_query,
    release_manifest_is_intact,
)
from app.models import Project, User
from app.models_v2 import (
    AssetVersion,
    LibraryAsset,
    ProjectRelease,
    ReadingContinuation,
    StorageObject,
    VoiceProfile,
    utcnow,
)
from app.project_permissions import is_project_owner


BACKEND_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STORAGE_ROOT = BACKEND_ROOT / "storage" / "objects"
DEFAULT_MAX_OBJECT_BYTES = 100 * 1024 * 1024
IO_CHUNK_BYTES = 1024 * 1024

_NAMESPACE_PART_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,10}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_MEDIA_TYPES = {
    "application/octet-stream",
    "audio/flac",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-flac",
    "audio/x-wav",
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
    "video/mp4",
    "video/webm",
}
_PRIVATE_NAMESPACE_PREFIXES = (
    "voice-reference/",
    "voice_refs/",
    "tts/voice-clone/",
    "tts_cache/voice_clone/",
)


class StorageServiceError(Exception):
    """Base class for safe storage failures."""


class InvalidStoragePathError(StorageServiceError):
    pass


class StorageLimitExceededError(StorageServiceError):
    pass


class UnsupportedMediaTypeError(StorageServiceError):
    pass


class StorageObjectMissingError(StorageServiceError):
    pass


class AsyncReadable(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    media_type: str
    byte_size: int
    sha256: str


def normalize_media_type(value: str) -> str:
    media_type = (value or "").split(";", 1)[0].strip().lower()
    if media_type not in _ALLOWED_MEDIA_TYPES:
        raise UnsupportedMediaTypeError("不支持的媒体类型")
    return media_type


def safe_suffix(filename_or_suffix: str | None) -> str:
    raw = (filename_or_suffix or "").strip().lower()
    suffix = raw if raw.startswith(".") and "/" not in raw and "\\" not in raw else Path(raw).suffix
    if not suffix:
        return ".bin"
    if not _SAFE_SUFFIX_RE.fullmatch(suffix):
        raise InvalidStoragePathError("文件扩展名无效")
    return suffix


def normalize_namespace(namespace: str) -> str:
    raw = (namespace or "").strip().replace("\\", "/").strip("/")
    parts = raw.split("/") if raw else []
    if not parts or any(not _NAMESPACE_PART_RE.fullmatch(part) for part in parts):
        raise InvalidStoragePathError("存储命名空间无效")
    return "/".join(parts)


class LocalStorageBackend:
    """Filesystem-backed object store with strict root containment."""

    backend_name = "local"

    def __init__(self, root: Path | str | None = None) -> None:
        configured = root or os.getenv("MEDIA_STORAGE_ROOT") or DEFAULT_STORAGE_ROOT
        self.root = Path(configured).expanduser().resolve()
        self.tmp_root = (self.root / ".tmp").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)

    def resolve_key(self, storage_key: str) -> Path:
        key = (storage_key or "").strip().replace("\\", "/")
        pure = PurePosixPath(key)
        if (
            not key
            or pure.is_absolute()
            or any(part in ("", ".", "..") for part in pure.parts)
            or ":" in pure.parts[0]
        ):
            raise InvalidStoragePathError("storage_key 无效")
        candidate = self.root.joinpath(*pure.parts).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise InvalidStoragePathError("storage_key 越界") from exc
        return candidate

    def _new_destination(self, namespace: str, suffix: str) -> tuple[str, Path]:
        namespace = normalize_namespace(namespace)
        suffix = safe_suffix(suffix)
        now = datetime.now(timezone.utc)
        storage_key = f"{namespace}/{now:%Y/%m}/{uuid.uuid4().hex}{suffix}"
        destination = self.resolve_key(storage_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        return storage_key, destination

    def _new_temp_path(self) -> Path:
        temp_path = (self.tmp_root / f"{uuid.uuid4().hex}.part").resolve()
        try:
            temp_path.relative_to(self.tmp_root)
        except ValueError as exc:
            raise InvalidStoragePathError("临时存储路径越界") from exc
        return temp_path

    def _persist_chunks(
        self,
        chunks: Iterable[bytes],
        *,
        namespace: str,
        filename_or_suffix: str | None,
        media_type: str,
        max_bytes: int,
    ) -> StoredFile:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        normalized_media_type = normalize_media_type(media_type)
        storage_key, destination = self._new_destination(namespace, safe_suffix(filename_or_suffix))
        temp_path = self._new_temp_path()
        digest = hashlib.sha256()
        total = 0
        try:
            with temp_path.open("xb") as handle:
                for chunk in chunks:
                    if not chunk:
                        continue
                    if not isinstance(chunk, bytes):
                        raise TypeError("storage chunks must be bytes")
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageLimitExceededError("媒体文件超过大小限制")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if total == 0:
                raise StorageServiceError("媒体文件不能为空")
            os.replace(temp_path, destination)
        except Exception:
            temp_path.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return StoredFile(
            storage_key=storage_key,
            media_type=normalized_media_type,
            byte_size=total,
            sha256=digest.hexdigest(),
        )

    def save_stream(
        self,
        stream: BinaryIO,
        *,
        namespace: str,
        filename_or_suffix: str | None,
        media_type: str,
        max_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    ) -> StoredFile:
        def chunks() -> Iterator[bytes]:
            while True:
                chunk = stream.read(IO_CHUNK_BYTES)
                if not chunk:
                    return
                yield chunk

        return self._persist_chunks(
            chunks(),
            namespace=namespace,
            filename_or_suffix=filename_or_suffix,
            media_type=media_type,
            max_bytes=max_bytes,
        )

    async def save_async_reader(
        self,
        reader: AsyncReadable,
        *,
        namespace: str,
        filename_or_suffix: str | None,
        media_type: str,
        max_bytes: int = DEFAULT_MAX_OBJECT_BYTES,
    ) -> StoredFile:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        normalized_media_type = normalize_media_type(media_type)
        storage_key, destination = self._new_destination(namespace, safe_suffix(filename_or_suffix))
        temp_path = self._new_temp_path()
        digest = hashlib.sha256()
        total = 0
        try:
            with temp_path.open("xb") as handle:
                while True:
                    chunk = await reader.read(IO_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise StorageLimitExceededError("媒体文件超过大小限制")
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if total == 0:
                raise StorageServiceError("媒体文件不能为空")
            os.replace(temp_path, destination)
        except Exception:
            temp_path.unlink(missing_ok=True)
            destination.unlink(missing_ok=True)
            raise
        return StoredFile(
            storage_key=storage_key,
            media_type=normalized_media_type,
            byte_size=total,
            sha256=digest.hexdigest(),
        )

    def open(self, storage_key: str) -> BinaryIO:
        path = self.resolve_key(storage_key)
        if not path.is_file():
            raise StorageObjectMissingError("媒体文件不存在")
        return path.open("rb")

    def stat(self, storage_key: str) -> os.stat_result:
        path = self.resolve_key(storage_key)
        if not path.is_file():
            raise StorageObjectMissingError("媒体文件不存在")
        return path.stat()

    def delete(self, storage_key: str) -> None:
        path = self.resolve_key(storage_key)
        path.unlink(missing_ok=True)

    def iter_range(
        self,
        storage_key: str,
        *,
        start: int,
        length: int,
        chunk_size: int = 64 * 1024,
    ) -> Iterator[bytes]:
        if start < 0 or length < 0 or chunk_size <= 0:
            raise ValueError("invalid range")
        with self.open(storage_key) as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(chunk_size, remaining))
                if not chunk:
                    return
                remaining -= len(chunk)
                yield chunk


@lru_cache(maxsize=1)
def get_local_storage_backend() -> LocalStorageBackend:
    return LocalStorageBackend()


def register_stored_file(
    db: Session,
    *,
    stored: StoredFile,
    owner_id: int,
    project_id: int | None,
    visibility: str = "private",
    status: str = "active",
    backend: LocalStorageBackend | None = None,
) -> StorageObject:
    if owner_id <= 0 or db.query(User).filter(User.id == owner_id, User.is_active.is_(True)).first() is None:
        raise StorageServiceError("媒体所有者无效")
    if project_id is not None:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project or project.owner_id != owner_id:
            raise StorageServiceError("媒体项目归属无效")
    if visibility not in {"private", "release", "public"}:
        raise ValueError("invalid storage visibility")
    if status not in {"pending", "active", "quarantined", "deleted"}:
        raise ValueError("invalid storage status")
    if any(stored.storage_key.startswith(prefix) for prefix in _PRIVATE_NAMESPACE_PREFIXES):
        visibility = "private"
    if not _SHA256_RE.fullmatch(stored.sha256):
        raise ValueError("invalid sha256")

    obj = StorageObject(
        owner_id=owner_id,
        project_id=project_id,
        storage_backend="local",
        storage_key=stored.storage_key,
        media_type=normalize_media_type(stored.media_type),
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        visibility=visibility,
        status=status,
    )
    db.add(obj)
    try:
        db.flush()
    except SQLAlchemyError:
        (backend or get_local_storage_backend()).delete(stored.storage_key)
        raise
    return obj


def soft_delete_storage_object(
    db: Session,
    obj: StorageObject,
) -> None:
    """Mark an object deleted; physical removal is deferred to GC.

    Deleting the file inside the database transaction would leave an active
    row pointing to a missing file if the transaction later rolled back.
    """
    if obj.status == "deleted":
        return
    obj.status = "deleted"
    obj.deleted_at = utcnow()
    db.flush()


def purge_deleted_storage_object(
    obj: StorageObject,
    *,
    backend: LocalStorageBackend | None = None,
) -> None:
    """Physically remove an already committed soft-deleted object."""
    if obj.status != "deleted" or obj.deleted_at is None:
        raise ValueError("storage object must be soft-deleted before purge")
    (backend or get_local_storage_backend()).delete(obj.storage_key)


def _manifest_references_object(manifest: Any, object_id: str, *, max_nodes: int = 20_000) -> bool:
    stack = [manifest]
    visited = 0
    while stack:
        value = stack.pop()
        visited += 1
        if visited > max_nodes:
            return False
        if isinstance(value, str):
            if value == object_id:
                return True
        elif isinstance(value, dict):
            stack.extend(value.keys())
            stack.extend(value.values())
        elif isinstance(value, (list, tuple)):
            stack.extend(value)
    return False


def is_referenced_by_published_release(db: Session, obj: StorageObject) -> bool:
    query = active_release_query(db)
    if obj.project_id is not None:
        query = query.filter(ProjectRelease.project_id == obj.project_id)
    releases = query.all()
    for release in releases:
        manifest = release.manifest_json
        if not release_manifest_is_intact(release):
            continue
        storage_object_ids = manifest.get("storage_object_ids")
        if isinstance(storage_object_ids, list) and obj.id in storage_object_ids:
            return True
    return False


def _storage_asset_version_ids(db: Session, obj: StorageObject) -> set[str]:
    return {
        version_id
        for (version_id,) in (
            db.query(AssetVersion.id)
            .filter(AssetVersion.storage_object_id == obj.id)
            .all()
        )
    }


def is_referenced_by_active_continuation(db: Session, obj: StorageObject) -> bool:
    """Authorize a confirmed continuation only while its Release is active."""
    version_ids = _storage_asset_version_ids(db, obj)
    if not version_ids:
        return False

    query = (
        active_release_query(db)
        .join(
            ReadingContinuation,
            ReadingContinuation.release_id == ProjectRelease.id,
        )
        .filter(ReadingContinuation.status == "confirmed")
    )
    if obj.project_id is not None:
        query = query.filter(ProjectRelease.project_id == obj.project_id)
    rows = query.with_entities(
        ProjectRelease,
        ReadingContinuation.frozen_asset_version_ids,
    ).all()
    for release, frozen_ids in rows:
        if not release_manifest_is_intact(release) or not isinstance(
            frozen_ids, list
        ):
            continue
        if version_ids.intersection(str(version_id) for version_id in frozen_ids):
            return True
    return False


def _is_referenced_by_any_confirmed_continuation(
    db: Session,
    obj: StorageObject,
) -> bool:
    version_ids = _storage_asset_version_ids(db, obj)
    if not version_ids:
        return False
    query = db.query(ReadingContinuation.frozen_asset_version_ids).filter(
        ReadingContinuation.status == "confirmed"
    )
    if obj.project_id is not None:
        query = query.join(
            ProjectRelease,
            ProjectRelease.id == ReadingContinuation.release_id,
        ).filter(ProjectRelease.project_id == obj.project_id)
    return any(
        isinstance(frozen_ids, list)
        and bool(version_ids.intersection(str(version_id) for version_id in frozen_ids))
        for (frozen_ids,) in query.all()
    )


def is_unconditionally_public_storage_object(db: Session, obj: StorageObject) -> bool:
    """Return whether public access survives publication lifecycle changes."""
    if obj.visibility != "public":
        return False
    if (
        db.query(LibraryAsset.id)
        .filter(LibraryAsset.storage_object_id == obj.id)
        .first()
    ):
        return True
    # Older continuation confirmations promoted their objects to ``public``.
    # Keep those rows release-bound without requiring a destructive backfill.
    return not _is_referenced_by_any_confirmed_continuation(db, obj)


def is_referenced_by_any_release(db: Session, obj: StorageObject) -> bool:
    query = db.query(ProjectRelease)
    if obj.project_id is not None:
        query = query.filter(ProjectRelease.project_id == obj.project_id)
    return any(
        _manifest_references_object(release.manifest_json, obj.id)
        for release in query.all()
    )


def has_direct_storage_reference(db: Session, obj: StorageObject) -> bool:
    return bool(
        db.query(AssetVersion.id)
        .filter(AssetVersion.storage_object_id == obj.id)
        .first()
        or db.query(LibraryAsset.id)
        .filter(LibraryAsset.storage_object_id == obj.id)
        .first()
        or db.query(VoiceProfile.id)
        .filter(VoiceProfile.reference_storage_object_id == obj.id)
        .first()
    )


def can_read_storage_object(db: Session, obj: StorageObject, user: Optional[User]) -> bool:
    if obj.status != "active" or obj.deleted_at is not None:
        return False
    if user and obj.owner_id == user.id:
        return True

    if obj.project_id is not None and user:
        project = db.query(Project).filter(Project.id == obj.project_id).first()
        if project and is_project_owner(project, user):
            return True

    if any(obj.storage_key.startswith(prefix) for prefix in _PRIVATE_NAMESPACE_PREFIXES):
        return False
    if is_unconditionally_public_storage_object(db, obj):
        return True
    if obj.visibility in {"private", "release", "public"}:
        return is_referenced_by_published_release(
            db, obj
        ) or is_referenced_by_active_continuation(db, obj)
    return False
