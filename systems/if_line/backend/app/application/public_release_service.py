"""Public reads constrained to the sole active immutable ProjectRelease."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.core.errors import AppError
from app.models_v2 import ProjectPublication, ProjectRelease, VNGraphRevision


_SELECTABLE_GRAPH_STATUSES = frozenset({"ready", "complete"})


@dataclass(frozen=True)
class PublicVNGraphSnapshot:
    release_id: str
    path_chapter_id: str
    revision_id: str
    graph_hash: str
    graph_json: dict[str, Any]


@dataclass(frozen=True)
class PublicReleaseManifestSnapshot:
    release_id: str
    version: int
    manifest_hash: str
    manifest: dict[str, Any]


def _not_found(message: str = "Public Release does not exist") -> AppError:
    return AppError(
        code="public.release_not_found",
        message=message,
        status_code=404,
    )


def release_manifest_is_intact(release: ProjectRelease) -> bool:
    return isinstance(release.manifest_json, dict) and content_hash(
        release.manifest_json
    ) == release.manifest_hash


def active_release_query(db: Session):
    return (
        db.query(ProjectRelease)
        .populate_existing()
        .join(
            ProjectPublication,
            ProjectPublication.active_release_id == ProjectRelease.id,
        )
        .filter(
            ProjectPublication.project_id == ProjectRelease.project_id,
            ProjectPublication.published_at.is_not(None),
            ProjectRelease.status == "published",
            ProjectRelease.published_at.is_not(None),
            ProjectRelease.withdrawn_at.is_(None),
        )
    )


def list_active_public_releases(
    db: Session,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[ProjectRelease]:
    if limit < 1 or limit > 100 or offset < 0:
        raise AppError(
            code="pagination.invalid",
            message="Public pagination is outside the supported range",
            status_code=422,
        )
    query = active_release_query(db).order_by(
        ProjectPublication.published_at.desc(),
        ProjectRelease.project_id.desc(),
    )
    # Manifest integrity cannot be expressed portably in SQL. Apply offset and
    # limit after filtering so one corrupt row cannot create a short page or
    # hide a later valid Release.
    result: list[ProjectRelease] = []
    valid_offset = 0
    database_offset = 0
    batch_size = max(100, limit)
    while len(result) < limit:
        rows = query.offset(database_offset).limit(batch_size).all()
        if not rows:
            break
        database_offset += len(rows)
        for release in rows:
            if not release_manifest_is_intact(release):
                continue
            if valid_offset < offset:
                valid_offset += 1
                continue
            result.append(release)
            if len(result) == limit:
                break
        if len(rows) < batch_size:
            break
    return result


def get_active_project_release(db: Session, *, project_id: int) -> ProjectRelease:
    release = (
        active_release_query(db)
        .filter(ProjectRelease.project_id == project_id)
        .one_or_none()
    )
    if not release or not release_manifest_is_intact(release):
        raise _not_found("Public project does not exist")
    return release


def get_active_public_release(db: Session, *, release_id: str) -> ProjectRelease:
    release = (
        active_release_query(db)
        .filter(ProjectRelease.id == release_id)
        .one_or_none()
    )
    if not release or not release_manifest_is_intact(release):
        raise _not_found()
    return release


def get_public_release_manifest(
    db: Session,
    *,
    release_id: str,
) -> PublicReleaseManifestSnapshot:
    release = get_active_public_release(db, release_id=release_id)
    return PublicReleaseManifestSnapshot(
        release_id=release.id,
        version=release.version,
        manifest_hash=release.manifest_hash,
        manifest=deepcopy(release.manifest_json),
    )


def _path_chapter_manifest(
    release: ProjectRelease,
    *,
    path_chapter_id: str,
) -> dict[str, Any]:
    matches: list[tuple[str, dict[str, Any]]] = []
    for path in release.manifest_json.get("story_paths") or []:
        if not isinstance(path, dict):
            continue
        story_path_id = path.get("id")
        if not isinstance(story_path_id, str):
            continue
        for chapter in path.get("chapters") or []:
            if (
                isinstance(chapter, dict)
                and chapter.get("path_chapter_id") == path_chapter_id
            ):
                matches.append((story_path_id, chapter))
    if len(matches) != 1:
        raise _not_found("Public PathChapter does not exist")
    story_path_id, chapter = matches[0]
    return {"story_path_id": story_path_id, **deepcopy(chapter)}


def get_public_path_chapter_manifest(
    db: Session,
    *,
    release_id: str,
    path_chapter_id: str,
) -> dict[str, Any]:
    release = get_active_public_release(db, release_id=release_id)
    return _path_chapter_manifest(release, path_chapter_id=path_chapter_id)


def get_public_path_chapter_vn_graph(
    db: Session,
    *,
    release_id: str,
    path_chapter_id: str,
) -> PublicVNGraphSnapshot:
    release = get_active_public_release(db, release_id=release_id)
    chapter = _path_chapter_manifest(release, path_chapter_id=path_chapter_id)
    chapter_revision = chapter.get("chapter_revision")
    script_revision = chapter.get("script_revision")
    graph_snapshot = chapter.get("vn_graph_revision")
    if not all(
        isinstance(value, dict)
        for value in (chapter_revision, script_revision, graph_snapshot)
    ):
        raise _not_found("Public VNGraph does not exist")
    revision = (
        db.query(VNGraphRevision)
        .filter(VNGraphRevision.id == graph_snapshot.get("id"))
        .one_or_none()
    )
    frozen_manifest = graph_snapshot.get("binding_manifest")
    if (
        not revision
        or revision.project_id != release.project_id
        or revision.status not in _SELECTABLE_GRAPH_STATUSES
        or revision.chapter_revision_id != chapter_revision.get("id")
        or revision.script_revision_id != script_revision.get("id")
        or revision.graph_hash != graph_snapshot.get("graph_hash")
        or revision.binding_manifest_hash
        != graph_snapshot.get("binding_manifest_hash")
        or not isinstance(frozen_manifest, dict)
        or content_hash(frozen_manifest) != revision.binding_manifest_hash
        or frozen_manifest != revision.binding_manifest
        or not isinstance(revision.graph_json, dict)
        or content_hash(revision.graph_json) != revision.graph_hash
    ):
        raise _not_found("Public VNGraph does not exist")
    return PublicVNGraphSnapshot(
        release_id=release.id,
        path_chapter_id=path_chapter_id,
        revision_id=revision.id,
        graph_hash=revision.graph_hash,
        graph_json=deepcopy(revision.graph_json),
    )


__all__ = [
    "PublicReleaseManifestSnapshot",
    "PublicVNGraphSnapshot",
    "active_release_query",
    "get_active_project_release",
    "get_active_public_release",
    "get_public_path_chapter_manifest",
    "get_public_path_chapter_vn_graph",
    "get_public_release_manifest",
    "list_active_public_releases",
    "release_manifest_is_intact",
]
