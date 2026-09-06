from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy.orm import Session

from app.application.authoring_resource_service import (
    require_owned_project,
    require_owned_release,
)
from app.application.draft_publication_service import finalize_and_publish
from app.application.public_release_service import (
    get_active_project_release,
    get_public_path_chapter_manifest,
    get_public_path_chapter_vn_graph,
    get_public_release_manifest,
    list_active_public_releases,
)
from app.application.publication_service import publish_project, unpublish_project
from app.application.revision_head_service import parse_if_match
from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import ProjectRelease
from app.schemas_authoring_artifacts import (
    FinalizePublishRequest,
    PublicProject,
    PublishRequest,
    Release,
    ReleaseManifest,
)


router = APIRouter()

_SUMMARY_LENGTH = 240
_COVER_ROLE_PRIORITY = (
    "cover",
    "keyframe",
    "illustration",
    "background",
    "portrait",
)


def _summary(value: object) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= _SUMMARY_LENGTH:
        return normalized
    return f"{normalized[: _SUMMARY_LENGTH - 1].rstrip()}..."


def _cover_url(manifest: dict[str, Any]) -> str | None:
    bindings: list[dict[str, Any]] = []
    for path in manifest.get("story_paths") or []:
        if not isinstance(path, dict):
            continue
        for chapter in path.get("chapters") or []:
            if not isinstance(chapter, dict):
                continue
            graph = chapter.get("vn_graph_revision")
            frozen = graph.get("binding_manifest") if isinstance(graph, dict) else None
            if isinstance(frozen, dict):
                bindings.extend(
                    item
                    for item in frozen.get("asset_bindings") or []
                    if isinstance(item, dict)
                )
    for role in _COVER_ROLE_PRIORITY:
        for binding in bindings:
            storage_object_id = binding.get("storage_object_id")
            if (
                str(binding.get("role") or "").lower() == role
                and isinstance(storage_object_id, str)
                and storage_object_id
            ):
                return f"/api/media/{storage_object_id}"
    return None


def _public_project(release: ProjectRelease) -> PublicProject:
    metadata = (
        release.manifest_json.get("project")
        if isinstance(release.manifest_json, dict)
        else None
    )
    if not isinstance(metadata, dict) or metadata.get("id") != release.project_id:
        metadata = {}
    return PublicProject(
        id=release.project_id,
        title=str(metadata.get("title") or f"IF Line Project {release.project_id}"),
        summary=_summary(metadata.get("story_start") or metadata.get("summary")),
        cover_url=_cover_url(release.manifest_json),
        release_id=release.id,
        release_version=release.version,
        manifest_hash=release.manifest_hash,
        published_at=release.published_at,
    )


def _etag_matches(value: str | None, etag: str) -> bool:
    if not value:
        return False
    for candidate in value.split(","):
        normalized = candidate.strip()
        if normalized == "*":
            return True
        if normalized.startswith("W/"):
            normalized = normalized[2:].strip()
        if normalized == etag:
            return True
    return False


@router.post(
    "/projects/{project_id}/publish",
    response_model=Release,
    status_code=status.HTTP_201_CREATED,
    tags=["Releases"],
    operation_id="publishProject",
)
def publish_authoring_project(
    project_id: int,
    body: PublishRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    result = publish_project(
        db,
        project_id=project_id,
        user_id=user.id,
        expected_authoring_fingerprint=body.expected_authoring_fingerprint,
        idempotency_key=idempotency_key,
        release_notes=body.release_notes,
    )
    db.commit()
    return result.release


@router.post(
    "/projects/{project_id}/finalize-publish",
    response_model=Release,
    status_code=status.HTTP_201_CREATED,
    tags=["Releases"],
    operation_id="finalizeAndPublish",
)
def finalize_and_publish_project(
    project_id: int,
    body: FinalizePublishRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    result = finalize_and_publish(
        db,
        project_id=project_id,
        user_id=user.id,
        idempotency_key=idempotency_key,
        bible_revision_id=body.bible_revision_id,
        outline_revision_ids=body.outline_revision_ids,
        chapter_revision_ids=body.chapter_revision_ids,
        script_revision_ids=body.script_revision_ids,
        graph_revision_ids=body.graph_revision_ids,
        release_notes=body.release_notes,
    )
    db.commit()
    return result.release


@router.get(
    "/projects/{project_id}/releases",
    response_model=list[Release],
    tags=["Releases"],
    operation_id="listReleases",
)
def list_project_releases(
    project_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    return (
        db.query(ProjectRelease)
        .filter(ProjectRelease.project_id == project_id)
        .order_by(ProjectRelease.version.desc())
        .all()
    )


@router.get(
    "/releases/{release_id}",
    response_model=Release,
    tags=["Releases"],
    operation_id="getRelease",
)
def get_release(
    release_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return require_owned_release(db, release_id=release_id, user_id=user.id)


@router.post(
    "/projects/{project_id}/unpublish",
    response_model=Release,
    tags=["Releases"],
    operation_id="unpublishProject",
)
def unpublish_authoring_project(
    project_id: int,
    if_match: str | None = Header(default=None, alias="If-Match"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    require_owned_project(db, project_id=project_id, user_id=user.id)
    release = unpublish_project(
        db,
        project_id=project_id,
        expected_lock_version=parse_if_match(if_match),
    )
    db.commit()
    return release


@router.get(
    "/public/projects",
    response_model=list[PublicProject],
    tags=["Public"],
    operation_id="listPublicProjects",
)
def list_public_projects(db: Session = Depends(get_db)):
    return [
        _public_project(release)
        for release in list_active_public_releases(db, limit=100)
    ]


@router.get(
    "/public/projects/{project_id}",
    response_model=PublicProject,
    tags=["Public"],
    operation_id="getPublicProject",
)
def get_public_project(project_id: int, db: Session = Depends(get_db)):
    return _public_project(get_active_project_release(db, project_id=project_id))


@router.get(
    "/public/releases/{release_id}/manifest",
    response_model=ReleaseManifest,
    tags=["Public"],
    operation_id="getPublicReleaseManifest",
)
def get_release_manifest(release_id: str, db: Session = Depends(get_db)):
    return get_public_release_manifest(db, release_id=release_id)


@router.get(
    "/public/releases/{release_id}/path-chapters/{path_chapter_id}/manifest",
    response_model=dict[str, Any],
    tags=["Public"],
    operation_id="getPublicPathChapterManifest",
)
def get_path_chapter_manifest(
    release_id: str,
    path_chapter_id: str,
    db: Session = Depends(get_db),
):
    return get_public_path_chapter_manifest(
        db,
        release_id=release_id,
        path_chapter_id=path_chapter_id,
    )


@router.get(
    "/public/releases/{release_id}/path-chapters/{path_chapter_id}/vn-graph",
    response_model=dict[str, Any],
    tags=["Public"],
    operation_id="getPublicPathChapterVNGraph",
    responses={304: {"description": "ETag matched."}},
)
def get_path_chapter_vn_graph(
    release_id: str,
    path_chapter_id: str,
    response: Response,
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    db: Session = Depends(get_db),
):
    snapshot = get_public_path_chapter_vn_graph(
        db,
        release_id=release_id,
        path_chapter_id=path_chapter_id,
    )
    etag = f'"{snapshot.graph_hash}"'
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    for name, value in headers.items():
        response.headers[name] = value
    return snapshot.graph_json
