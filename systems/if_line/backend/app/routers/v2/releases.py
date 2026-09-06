from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.release_service import create_release, get_public_release, withdraw_release
from app.auth import get_current_user
from app.database import get_db
from app.models import Project, User
from app.models_v2 import ProjectContentHead, ProjectRelease, VNGraphRevision
from app.project_permissions import is_project_published, require_project_owner
from app.schemas_release import (
    PublicProjectRead,
    ReleaseCreated,
    ReleaseManifest,
    ReleaseRead,
)


owner_router = APIRouter(prefix="/projects/{project_id}/releases", tags=["releases"])
public_router = APIRouter(prefix="/public", tags=["public"])

_PUBLIC_SUMMARY_LENGTH = 240
_IMAGE_ROLE_PRIORITY = (
    "cover",
    "background",
    "bg",
    "keyframe",
    "cg",
    "illustration",
    "portrait",
    "tachi",
    "character",
    "image",
)


def _public_summary(story_start: str | None) -> str:
    """Build a compact single-line excerpt without splitting Unicode bytes."""

    summary = " ".join((story_start or "").split())
    if len(summary) <= _PUBLIC_SUMMARY_LENGTH:
        return summary
    return f"{summary[: _PUBLIC_SUMMARY_LENGTH - 1].rstrip()}…"


def _release_chapters(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("chapters"), list):
        return []
    chapters = [item for item in manifest["chapters"] if isinstance(item, dict)]
    return sorted(
        chapters,
        key=lambda item: (
            item.get("chapter_index")
            if isinstance(item.get("chapter_index"), int)
            else float("inf")
        ),
    )


def _cover_url(manifest: Any) -> str | None:
    chapters = _release_chapters(manifest)
    for role in _IMAGE_ROLE_PRIORITY:
        for chapter in chapters:
            bindings = chapter.get("asset_bindings")
            if not isinstance(bindings, list):
                continue
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                binding_role = str(binding.get("role") or "").strip().lower()
                storage_object_id = binding.get("storage_object_id")
                if binding_role == role and isinstance(storage_object_id, str) and storage_object_id:
                    return f"/api/media/{storage_object_id}"
    return None


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    for candidate in if_none_match.split(","):
        candidate = candidate.strip()
        if candidate == "*":
            return True
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _release_project_metadata(release: ProjectRelease) -> dict[str, Any]:
    manifest = release.manifest_json
    metadata = manifest.get("project") if isinstance(manifest, dict) else None
    if not isinstance(metadata, dict) or metadata.get("id") != release.project_id:
        return {}
    return metadata


# GET /api/projects/{project_id}/releases
@owner_router.get("", response_model=list[ReleaseRead])
def list_releases(
    project_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    return (
        db.query(ProjectRelease)
        .filter(ProjectRelease.project_id == project_id)
        .order_by(ProjectRelease.version.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


# POST /api/projects/{project_id}/releases
@owner_router.post("", response_model=ReleaseCreated, status_code=status.HTTP_201_CREATED)
def add_release(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    release, created = create_release(db, project_id=project_id, user_id=user.id, publish=True)
    db.commit()
    db.refresh(release)
    payload = ReleaseRead.model_validate(release).model_dump()
    return ReleaseCreated(**payload, created=created)


# POST /api/projects/{project_id}/releases/{release_id}/withdraw
@owner_router.post("/{release_id}/withdraw", response_model=ReleaseRead)
def withdraw(
    project_id: int,
    release_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_project_owner(db, project_id, user)
    release = (
        db.query(ProjectRelease)
        .filter(ProjectRelease.id == release_id, ProjectRelease.project_id == project_id)
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    withdraw_release(db, release)
    db.commit()
    return release


# GET /api/public/projects
@public_router.get("/projects", response_model=list[PublicProjectRead])
def list_public_projects(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """List only projects backed by their current effective release."""

    query = (
        db.query(Project, ProjectRelease)
        .select_from(Project)
        .join(ProjectContentHead, ProjectContentHead.project_id == Project.id)
        .join(
            ProjectRelease,
            ProjectRelease.id == ProjectContentHead.published_release_id,
        )
        .filter(
            ProjectRelease.project_id == Project.id,
            ProjectRelease.status == "published",
            ProjectRelease.withdrawn_at.is_(None),
            Project.visibility == "public",
        )
    )
    if q and (term := q.strip()):
        release_title = ProjectRelease.manifest_json["project"]["title"].as_string()
        query = query.filter(
            release_title.ilike(f"%{_escape_like(term)}%", escape="\\")
        )
    rows = (
        query.order_by(ProjectRelease.published_at.desc(), Project.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    result: list[PublicProjectRead] = []
    for project, release in rows:
        project_metadata = _release_project_metadata(release)
        chapters = _release_chapters(release.manifest_json)
        chapter_indices = [
            chapter["chapter_index"]
            for chapter in chapters
            if isinstance(chapter.get("chapter_index"), int) and chapter["chapter_index"] >= 1
        ]
        playable_chapter_indices = [
            chapter["chapter_index"]
            for chapter in chapters
            if isinstance(chapter.get("chapter_index"), int)
            and chapter["chapter_index"] >= 1
            and all(
                isinstance(chapter.get(key), str) and chapter[key]
                for key in ("chapter_revision_id", "vngraph_revision_id", "vngraph_hash")
            )
        ]
        result.append(
            PublicProjectRead(
                id=project.id,
                title=str(project_metadata.get("title") or f"IF Line 作品 {project.id}"),
                style=(
                    str(project_metadata["style"])
                    if project_metadata.get("style") is not None
                    else None
                ),
                summary=_public_summary(str(project_metadata.get("summary") or "")),
                release_id=release.id,
                release_version=release.version,
                manifest_hash=release.manifest_hash,
                published_at=release.published_at,
                chapter_count=len(chapter_indices),
                cover_url=_cover_url(release.manifest_json),
                entry_chapter_index=(
                    min(playable_chapter_indices) if playable_chapter_indices else None
                ),
            )
        )
    return result


# GET /api/public/projects/{project_id}
@public_router.get("/projects/{project_id}")
def public_project(project_id: int, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not project or not is_project_published(project) or not head or not head.published_release_id:
        raise HTTPException(status_code=404, detail="作品不存在")
    release = get_public_release(db, head.published_release_id)
    if release.project_id != project_id:
        raise HTTPException(status_code=404, detail="作品不存在")
    project_metadata = _release_project_metadata(release)
    return {
        "id": project.id,
        "title": str(project_metadata.get("title") or f"IF Line 作品 {project.id}"),
        "style": (
            str(project_metadata["style"])
            if project_metadata.get("style") is not None
            else None
        ),
        "summary": _public_summary(str(project_metadata.get("summary") or "")),
        "release_id": release.id,
        "release_version": release.version,
        "manifest_hash": release.manifest_hash,
        "published_at": release.published_at,
    }


# GET /api/public/releases/{release_id}/manifest
@public_router.get("/releases/{release_id}/manifest", response_model=ReleaseManifest)
def public_manifest(release_id: str, db: Session = Depends(get_db)):
    release = get_public_release(db, release_id)
    return ReleaseManifest(
        release_id=release.id,
        version=release.version,
        manifest_hash=release.manifest_hash,
        manifest=release.manifest_json,
    )


# GET /api/public/releases/{release_id}/chapters/{chapter_index}/manifest
@public_router.get("/releases/{release_id}/chapters/{chapter_index}/manifest")
def public_chapter_manifest(
    release_id: str,
    chapter_index: int = Path(ge=1),
    db: Session = Depends(get_db),
):
    release = get_public_release(db, release_id)
    chapter = next(
        (item for item in release.manifest_json.get("chapters", []) if item.get("chapter_index") == chapter_index),
        None,
    )
    if not chapter:
        raise HTTPException(status_code=404, detail="章节不存在")
    return {
        "release_id": release.id,
        "release_version": release.version,
        "manifest_hash": release.manifest_hash,
        "chapter": chapter,
    }


# GET /api/public/releases/{release_id}/chapters/{chapter_index}/vn-graph
@public_router.get(
    "/releases/{release_id}/chapters/{chapter_index}/vn-graph",
    response_model=dict[str, Any],
)
def public_vn_graph(
    release_id: str,
    response: Response,
    chapter_index: int = Path(ge=1),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
):
    """Return the VN graph revision frozen into an immutable release."""

    release = get_public_release(db, release_id)
    project = db.query(Project).filter(Project.id == release.project_id).first()
    if not project or not is_project_published(project):
        raise HTTPException(status_code=404, detail="VN 图不存在")

    chapter = next(
        (
            item
            for item in _release_chapters(release.manifest_json)
            if item.get("chapter_index") == chapter_index
        ),
        None,
    )
    revision_id = chapter.get("vngraph_revision_id") if chapter else None
    expected_graph_hash = chapter.get("vngraph_hash") if chapter else None
    expected_chapter_revision_id = chapter.get("chapter_revision_id") if chapter else None
    if not all(
        isinstance(value, str) and value
        for value in (revision_id, expected_graph_hash, expected_chapter_revision_id)
    ):
        raise HTTPException(status_code=404, detail="VN 图不存在")

    revision = db.query(VNGraphRevision).filter(VNGraphRevision.id == revision_id).first()
    if (
        not revision
        or revision.status != "complete"
        or revision.project_id != release.project_id
        or revision.chapter_index != chapter_index
        or revision.chapter_revision_id != expected_chapter_revision_id
        or revision.graph_hash != expected_graph_hash
        or not isinstance(revision.graph_json, dict)
        or content_hash(revision.graph_json) != expected_graph_hash
    ):
        raise HTTPException(status_code=404, detail="VN 图不存在")

    etag = f'"{expected_graph_hash}"'
    cache_headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=cache_headers)
    for name, value in cache_headers.items():
        response.headers[name] = value
    return revision.graph_json
