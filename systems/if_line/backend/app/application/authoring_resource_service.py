"""Project and stable-ID resource operations for the replacement API."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.models import Asset, Project
from app.models_v2 import (
    AssetVersion,
    BranchCandidate,
    CandidateSetRevision,
    ChapterRevision,
    ChapterScriptRevision,
    ChapterSlot,
    GenerationTask,
    OutlineRevision,
    ProjectContentHead,
    ProjectPublication,
    ProjectRelease,
    ScriptResourceSlot,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    VNGraphRevision,
)


PROJECT_EDITABLE_FIELDS = frozenset(
    {
        "title",
        "characters",
        "story_start",
        "story_end",
        "style",
        "source_work",
        "pace",
        "extra_requirements",
    }
)


def _not_found(code: str, message: str) -> AppError:
    return AppError(code=code, message=message, status_code=404)


def require_owned_project(db: Session, *, project_id: int, user_id: int) -> Project:
    project = (
        db.query(Project)
        .filter(
            Project.id == project_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not project:
        raise _not_found("project.not_found", "Project does not exist")
    return project


def require_owned_story_path(
    db: Session,
    *,
    story_path_id: str,
    user_id: int,
    for_update: bool = False,
) -> StoryPath:
    query = (
        db.query(StoryPath)
        .join(Project, Project.id == StoryPath.project_id)
        .filter(
            StoryPath.id == story_path_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
    )
    if for_update:
        query = query.with_for_update()
    path = query.one_or_none()
    if not path:
        raise _not_found("story_path.not_found", "StoryPath does not exist")
    return path


def require_owned_path_chapter(
    db: Session,
    *,
    path_chapter_id: str,
    user_id: int,
    for_update: bool = False,
) -> StoryPathChapter:
    query = (
        db.query(StoryPathChapter)
        .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
        .join(Project, Project.id == StoryPath.project_id)
        .filter(
            StoryPathChapter.id == path_chapter_id,
            StoryPathChapter.status == "active",
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
    )
    if for_update:
        query = query.with_for_update()
    placement = query.one_or_none()
    if not placement:
        raise _not_found("path_chapter.not_found", "PathChapter does not exist")
    return placement


def require_owned_chapter_revision(
    db: Session,
    *,
    chapter_revision_id: str,
    user_id: int,
) -> ChapterRevision:
    revision = (
        db.query(ChapterRevision)
        .join(Project, Project.id == ChapterRevision.project_id)
        .filter(
            ChapterRevision.id == chapter_revision_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not revision:
        raise _not_found(
            "chapter_revision.not_found",
            "Chapter revision does not exist",
        )
    return revision


def require_owned_candidate_set_revision(
    db: Session,
    *,
    candidate_set_revision_id: str,
    user_id: int,
) -> CandidateSetRevision:
    revision = (
        db.query(CandidateSetRevision)
        .join(Project, Project.id == CandidateSetRevision.project_id)
        .filter(
            CandidateSetRevision.id == candidate_set_revision_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not revision:
        raise _not_found(
            "candidate_set_revision.not_found",
            "CandidateSet revision does not exist",
        )
    return revision


def require_owned_branch_candidate(
    db: Session,
    *,
    candidate_id: str,
    user_id: int,
) -> BranchCandidate:
    candidate = (
        db.query(BranchCandidate)
        .join(Project, Project.id == BranchCandidate.project_id)
        .filter(
            BranchCandidate.id == candidate_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not candidate:
        raise _not_found(
            "branch_candidate.not_found",
            "Branch candidate does not exist",
        )
    return candidate


def require_owned_script_revision(
    db: Session,
    *,
    script_revision_id: str,
    user_id: int,
) -> ChapterScriptRevision:
    revision = (
        db.query(ChapterScriptRevision)
        .join(Project, Project.id == ChapterScriptRevision.project_id)
        .filter(
            ChapterScriptRevision.id == script_revision_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not revision:
        raise _not_found(
            "script_revision.not_found",
            "ChapterScript revision does not exist",
        )
    return revision


def require_owned_resource_slot(
    db: Session,
    *,
    script_revision_id: str,
    slot_id: str,
    user_id: int,
) -> ScriptResourceSlot:
    slot = (
        db.query(ScriptResourceSlot)
        .join(
            ChapterScriptRevision,
            ChapterScriptRevision.id == ScriptResourceSlot.chapter_script_revision_id,
        )
        .join(Project, Project.id == ChapterScriptRevision.project_id)
        .filter(
            ScriptResourceSlot.id == slot_id,
            ChapterScriptRevision.id == script_revision_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not slot:
        raise _not_found(
            "resource_slot.not_found",
            "Script resource slot does not exist",
        )
    return slot


def require_owned_vn_graph_revision(
    db: Session,
    *,
    vn_graph_revision_id: str,
    user_id: int,
) -> VNGraphRevision:
    revision = (
        db.query(VNGraphRevision)
        .join(Project, Project.id == VNGraphRevision.project_id)
        .filter(
            VNGraphRevision.id == vn_graph_revision_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not revision:
        raise _not_found(
            "vngraph_revision.not_found",
            "VNGraph revision does not exist",
        )
    return revision


def require_owned_release(
    db: Session,
    *,
    release_id: str,
    user_id: int,
) -> ProjectRelease:
    release = (
        db.query(ProjectRelease)
        .join(Project, Project.id == ProjectRelease.project_id)
        .filter(
            ProjectRelease.id == release_id,
            Project.owner_id == user_id,
            Project.deleted_at.is_(None),
        )
        .one_or_none()
    )
    if not release:
        raise _not_found("release.not_found", "Release does not exist")
    return release


def create_authoring_project(
    db: Session,
    *,
    user_id: int,
    values: dict[str, Any],
) -> tuple[Project, StoryPath]:
    """Create the Project, root path, and initially empty Heads atomically."""

    project = Project(
        owner_id=user_id,
        title=values["title"],
        characters=deepcopy(values.get("characters") or []),
        story_start=values.get("story_start") or "",
        story_end=values.get("story_end") or "",
        style=values.get("style") or "",
        source_work=values.get("source_work"),
        pace=values.get("pace"),
        extra_requirements=values.get("extra_requirements"),
        status="draft_input",
        visibility="private",
        is_draft=True,
        published_at=None,
    )
    with db.begin_nested():
        db.add(project)
        db.flush()
        root = StoryPath(
            project_id=project.id,
            title="Main",
            status="active",
            lock_version=1,
        )
        db.add(root)
        db.flush()
        db.add_all(
            [
                ProjectContentHead(project_id=project.id, lock_version=1),
                StoryPathOutlineHead(story_path_id=root.id, lock_version=1),
            ]
        )
        db.flush()
    return project, root


def get_root_story_path(db: Session, *, project_id: int) -> StoryPath | None:
    return (
        db.query(StoryPath)
        .filter(
            StoryPath.project_id == project_id,
            StoryPath.parent_path_id.is_(None),
        )
        .one_or_none()
    )


def update_authoring_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
    changes: dict[str, Any],
) -> Project:
    project = require_owned_project(db, project_id=project_id, user_id=user_id)
    unexpected = set(changes) - PROJECT_EDITABLE_FIELDS
    if unexpected:
        raise AppError(
            code="project.patch_invalid",
            message="Project patch contains non-editable fields",
            status_code=422,
            details={"fields": sorted(unexpected)},
        )
    for field, value in changes.items():
        setattr(project, field, deepcopy(value))
    project.updated_at = datetime.utcnow()
    db.flush()
    return project


def delete_authoring_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
) -> bool:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.owner_id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if not project:
        raise _not_found("project.not_found", "Project does not exist")
    if project.deleted_at is not None:
        return False

    publication = (
        db.query(ProjectPublication)
        .filter(ProjectPublication.project_id == project.id)
        .with_for_update()
        .one_or_none()
    )
    if publication and publication.active_release_id:
        raise AppError(
            code="project.published",
            message="Unpublish the active Release before deleting this Project",
            status_code=409,
        )
    project.deleted_at = datetime.now(timezone.utc)
    project.updated_at = datetime.utcnow()
    db.flush()
    return True


def update_story_path_metadata(
    db: Session,
    *,
    story_path_id: str,
    user_id: int,
    expected_lock_version: int,
    changes: dict[str, Any],
) -> StoryPath:
    path = require_owned_story_path(
        db,
        story_path_id=story_path_id,
        user_id=user_id,
        for_update=True,
    )
    if path.lock_version != expected_lock_version:
        raise AppError(
            code="story_path.version_conflict",
            message="StoryPath changed since it was read",
            status_code=409,
            details={"current_lock_version": path.lock_version},
        )
    changed = any(getattr(path, field) != value for field, value in changes.items())
    if changed:
        for field, value in changes.items():
            setattr(path, field, value)
        path.lock_version += 1
        path.updated_at = datetime.utcnow()
        db.flush()
    return path


def append_path_chapter(
    db: Session,
    *,
    story_path_id: str,
    user_id: int,
    expected_lock_version: int,
    after_path_chapter_id: str | None,
) -> StoryPathChapter:
    path = require_owned_story_path(
        db,
        story_path_id=story_path_id,
        user_id=user_id,
        for_update=True,
    )
    if path.lock_version != expected_lock_version:
        raise AppError(
            code="story_path.version_conflict",
            message="StoryPath changed since it was read",
            status_code=409,
            details={"current_lock_version": path.lock_version},
        )
    if path.status != "active":
        raise AppError(
            code="story_path.archived",
            message="Archived StoryPath cannot be edited",
            status_code=409,
        )

    current = (
        db.query(StoryPathChapter)
        .filter(
            StoryPathChapter.story_path_id == path.id,
            StoryPathChapter.status == "active",
        )
        .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
        .with_for_update()
        .all()
    )
    predecessor = current[-1] if current else None
    if after_path_chapter_id is not None and (
        predecessor is None or predecessor.id != after_path_chapter_id
    ):
        raise AppError(
            code="path_chapter.append_conflict",
            message="after_path_chapter_id must identify the current path tail",
            status_code=409,
            details={"current_tail_id": predecessor.id if predecessor else None},
        )

    with db.begin_nested():
        slot = ChapterSlot(
            project_id=path.project_id,
            created_for_story_path_id=path.id,
        )
        db.add(slot)
        db.flush()
        placement = StoryPathChapter(
            story_path_id=path.id,
            chapter_slot_id=slot.id,
            display_index=max(
                (item.display_index for item in current),
                default=0,
            )
            + 1,
            predecessor_path_chapter_id=predecessor.id if predecessor else None,
            lock_version=1,
        )
        db.add(placement)
        path.lock_version += 1
        path.updated_at = datetime.utcnow()
        db.flush()
    return placement


def get_project_metrics(db: Session, *, project_id: int) -> dict[str, Any]:
    paths = (
        db.query(StoryPath)
        .filter(StoryPath.project_id == project_id)
        .order_by(StoryPath.created_at, StoryPath.id)
        .all()
    )
    placements = (
        db.query(StoryPathChapter)
        .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
        .filter(
            StoryPath.project_id == project_id,
            StoryPathChapter.status == "active",
        )
        .order_by(StoryPathChapter.story_path_id, StoryPathChapter.display_index)
        .all()
    )
    placement_ids_by_path: dict[str, list[str]] = {path.id: [] for path in paths}
    for placement in placements:
        placement_ids_by_path.setdefault(placement.story_path_id, []).append(placement.id)
    tasks = db.query(GenerationTask).filter(GenerationTask.project_id == project_id).all()
    return {
        "project_id": project_id,
        "story_paths_by_id": {
            path.id: {
                "status": path.status,
                "parent_path_id": path.parent_path_id,
                "path_chapter_ids": placement_ids_by_path.get(path.id, []),
            }
            for path in paths
        },
        "selected_path_chapter_count": sum(
            placement.current_revision_id is not None for placement in placements
        ),
        "revision_counts": {
            "bible": db.query(StoryBibleRevision).filter_by(project_id=project_id).count(),
            "outline": db.query(OutlineRevision).filter_by(project_id=project_id).count(),
            "chapter": db.query(ChapterRevision).filter_by(project_id=project_id).count(),
            "script": db.query(ChapterScriptRevision).filter_by(project_id=project_id).count(),
            "vn_graph": db.query(VNGraphRevision).filter_by(project_id=project_id).count(),
        },
        "task_counts": {
            "total": len(tasks),
            "by_status": dict(sorted(Counter(task.status for task in tasks).items())),
            "by_kind": dict(sorted(Counter(task.kind for task in tasks).items())),
        },
    }


def _task_path_chapter_id(task: GenerationTask) -> str | None:
    source_refs = task.source_refs or {}
    chapter_source = source_refs.get("chapter_source") or {}
    manifest = chapter_source.get("context_manifest") or {}
    if isinstance(manifest.get("path_chapter_id"), str):
        return manifest["path_chapter_id"]
    script_source = source_refs.get("chapter_script_source") or {}
    if isinstance(script_source.get("path_chapter_id"), str):
        return script_source["path_chapter_id"]
    return None


def get_project_artifact_metrics(db: Session, *, project_id: int) -> dict[str, Any]:
    tasks = (
        db.query(GenerationTask)
        .filter(GenerationTask.project_id == project_id)
        .order_by(GenerationTask.created_at, GenerationTask.id)
        .all()
    )
    versions = (
        db.query(AssetVersion)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .filter(Asset.project_id == project_id)
        .order_by(AssetVersion.created_at, AssetVersion.id)
        .all()
    )
    return {
        "project_id": project_id,
        "generation_tasks_by_id": {
            task.id: {
                "kind": task.kind,
                "status": task.status,
                "path_chapter_id": _task_path_chapter_id(task),
                "duration_seconds": (
                    max((task.finished_at - task.started_at).total_seconds(), 0.0)
                    if task.started_at and task.finished_at
                    else None
                ),
            }
            for task in tasks
        },
        "asset_versions_by_id": {
            version.id: {
                "source_kind": version.source_kind,
                "source_revision_id": version.source_revision_id,
                "generation_task_id": version.generation_task_id,
                "storage_object_id": version.storage_object_id,
                "quality_score": version.quality_score,
                "safety_status": version.safety_status,
            }
            for version in versions
        },
    }


__all__ = [
    "PROJECT_EDITABLE_FIELDS",
    "append_path_chapter",
    "create_authoring_project",
    "delete_authoring_project",
    "get_project_artifact_metrics",
    "get_project_metrics",
    "get_root_story_path",
    "require_owned_branch_candidate",
    "require_owned_candidate_set_revision",
    "require_owned_chapter_revision",
    "require_owned_path_chapter",
    "require_owned_project",
    "require_owned_release",
    "require_owned_resource_slot",
    "require_owned_script_revision",
    "require_owned_story_path",
    "require_owned_vn_graph_revision",
    "update_authoring_project",
    "update_story_path_metadata",
]
