"""Computed owner-facing authoring and publication state for a Project."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Sequence

from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.publication_readiness_service import (
    PublicationBlockingItem,
    calculate_publication_readiness,
)
from app.core.errors import AppError
from app.models_v2 import GenerationTask, ProjectPublication, ProjectRelease


AuthoringStage = Literal[
    "bible",
    "story_paths",
    "outlines",
    "chapters",
    "scripts",
    "vn_graphs",
    "ready",
    "blocked",
]
PublicationState = Literal["unpublished", "published", "changes_pending"]

_ACTIVE_TASK_STATUSES = ("queued", "running")
_STAGE_PREFIXES: tuple[tuple[AuthoringStage, tuple[str, ...]], ...] = (
    ("bible", ("bible.",)),
    ("story_paths", ("story_path.",)),
    ("outlines", ("outline.",)),
    ("chapters", ("chapter.",)),
    ("scripts", ("script.",)),
    ("vn_graphs", ("vngraph.",)),
)


@dataclass(frozen=True)
class AuthoringProjection:
    stage: AuthoringStage
    blocking_items: tuple[PublicationBlockingItem, ...]
    running_task_count: int
    has_unpublished_changes: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "blocking_items": [item.as_dict() for item in self.blocking_items],
            "running_task_count": self.running_task_count,
            "has_unpublished_changes": self.has_unpublished_changes,
        }


@dataclass(frozen=True)
class PublicationProjection:
    state: PublicationState
    active_release_id: str | None
    published_at: datetime | None
    lock_version: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "active_release_id": self.active_release_id,
            "published_at": self.published_at,
            "lock_version": self.lock_version,
        }


@dataclass(frozen=True)
class ProjectStateProjection:
    authoring: AuthoringProjection
    publication: PublicationProjection

    def as_dict(self) -> dict[str, Any]:
        return {
            "authoring": self.authoring.as_dict(),
            "publication": self.publication.as_dict(),
        }


def derive_authoring_stage(
    blocking_items: Sequence[PublicationBlockingItem],
) -> AuthoringStage:
    if not blocking_items:
        return "ready"
    codes = tuple(item.code for item in blocking_items)
    for stage, prefixes in _STAGE_PREFIXES:
        if any(code.startswith(prefixes) for code in codes):
            return stage
    return "blocked"


def _invalid_active_release() -> AppError:
    return AppError(
        code="release.active_invalid",
        message="ProjectPublication points to an invalid active Release",
        status_code=409,
    )


def _publication_and_release(
    db: Session,
    *,
    project_id: int,
) -> tuple[ProjectPublication | None, ProjectRelease | None]:
    publication = (
        db.query(ProjectPublication)
        .filter(ProjectPublication.project_id == project_id)
        .one_or_none()
    )
    if publication is None:
        return None, None
    if publication.lock_version < 1:
        raise _invalid_active_release()
    if publication.active_release_id is None:
        if publication.published_at is not None:
            raise _invalid_active_release()
        return publication, None

    release = (
        db.query(ProjectRelease)
        .filter(
            ProjectRelease.id == publication.active_release_id,
            ProjectRelease.project_id == project_id,
        )
        .one_or_none()
    )
    manifest = release.manifest_json if release is not None else None
    fingerprint = release.authoring_fingerprint if release is not None else None
    if (
        release is None
        or release.status != "published"
        or release.published_at is None
        or release.withdrawn_at is not None
        or publication.published_at is None
        or not isinstance(manifest, dict)
        or content_hash(manifest) != release.manifest_hash
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(character not in "0123456789abcdef" for character in fingerprint)
        or manifest.get("authoring_fingerprint") != fingerprint
    ):
        raise _invalid_active_release()
    return publication, release


def calculate_project_state_projection(
    db: Session,
    *,
    project_id: int,
) -> ProjectStateProjection:
    """Project owner state derived without consulting legacy lifecycle fields."""

    readiness = calculate_publication_readiness(db, project_id=project_id)
    publication, active_release = _publication_and_release(
        db,
        project_id=project_id,
    )
    running_task_count = int(
        db.query(GenerationTask.id)
        .filter(
            GenerationTask.project_id == project_id,
            GenerationTask.status.in_(_ACTIVE_TASK_STATUSES),
        )
        .count()
    )

    if active_release is None:
        publication_state: PublicationState = "unpublished"
        has_unpublished_changes = True
    elif active_release.authoring_fingerprint == readiness.authoring_fingerprint:
        publication_state = "published"
        has_unpublished_changes = False
    else:
        publication_state = "changes_pending"
        has_unpublished_changes = True

    return ProjectStateProjection(
        authoring=AuthoringProjection(
            stage=derive_authoring_stage(readiness.blocking_items),
            blocking_items=readiness.blocking_items,
            running_task_count=running_task_count,
            has_unpublished_changes=has_unpublished_changes,
        ),
        publication=PublicationProjection(
            state=publication_state,
            active_release_id=(active_release.id if active_release is not None else None),
            published_at=(publication.published_at if publication is not None else None),
            lock_version=(publication.lock_version if publication is not None else 1),
        ),
    )


__all__ = [
    "AuthoringProjection",
    "AuthoringStage",
    "ProjectStateProjection",
    "PublicationProjection",
    "PublicationState",
    "calculate_project_state_projection",
    "derive_authoring_stage",
]
