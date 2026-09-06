"""Atomic single-step publication from reviewed StoryPath authoring state."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.publication_readiness_service import (
    PublicationReadinessResult,
    calculate_publication_readiness,
)
from app.core.errors import AppError
from app.models import Asset, Project
from app.models_v2 import (
    AssetAction,
    AssetVersion,
    BranchCandidate,
    CandidateSetRevision,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    ProjectPublication,
    ProjectRelease,
    ScriptResourceSlot,
    StateSnapshot,
    StorageObject,
    StoryBibleRevision,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
    VoiceLineVersion,
    utcnow,
)


RELEASE_MANIFEST_VERSION = "story-path-release-v1"


@dataclass(frozen=True)
class AtomicPublicationResult:
    release: ProjectRelease
    created: bool


def _normalize_idempotency_key(value: object) -> str:
    if not isinstance(value, str):
        raise AppError(
            code="idempotency_key.invalid",
            message="A valid Idempotency-Key is required",
            status_code=400,
        )
    key = value.strip()
    if not key or len(key) > 255:
        raise AppError(
            code="idempotency_key.invalid",
            message="A valid Idempotency-Key is required",
            status_code=400,
        )
    return key


def _normalize_fingerprint(value: object) -> str:
    fingerprint = value.strip() if isinstance(value, str) else ""
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise AppError(
            code="release.fingerprint_invalid",
            message="expected_authoring_fingerprint must be a lowercase SHA-256 value",
            status_code=422,
        )
    return fingerprint


def _normalize_release_notes(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2000:
        raise AppError(
            code="release.notes_invalid",
            message="release_notes must be a string of at most 2000 characters",
            status_code=422,
        )
    return value


def _lock_or_create_publication(db: Session, project_id: int) -> ProjectPublication:
    publication = (
        db.query(ProjectPublication)
        .filter(ProjectPublication.project_id == project_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if publication is not None:
        return publication

    project = (
        db.query(Project)
        .filter(Project.id == project_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if not project:
        raise AppError(
            code="project.not_found",
            message="Project does not exist",
            status_code=404,
        )
    publication = (
        db.query(ProjectPublication)
        .filter(ProjectPublication.project_id == project_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if publication is None:
        publication = ProjectPublication(
            project_id=project_id,
            active_release_id=None,
            published_at=None,
            lock_version=1,
        )
        db.add(publication)
        db.flush()
    return publication


def _snapshot_ids(readiness: PublicationReadinessResult) -> dict[str, set[Any]]:
    source = readiness.authoring_snapshot
    result: dict[str, set[Any]] = {
        "path": set(),
        "path_chapter": set(),
        "outline": set(),
        "chapter": set(),
        "script": set(),
        "graph": set(),
        "state": set(),
        "candidate_set": set(),
        "candidate": set(),
    }
    bible = source.get("bible_revision")
    result["bible"] = {bible.get("id")} if isinstance(bible, dict) else set()
    for path in source.get("story_paths") or []:
        if not isinstance(path, dict):
            continue
        result["path"].add(path.get("id"))
        outline = path.get("outline_revision")
        if isinstance(outline, dict):
            result["outline"].add(outline.get("id"))
        state = path.get("base_state_snapshot")
        if isinstance(state, dict):
            result["state"].add(state.get("id"))
        choice = path.get("fork_choice")
        if isinstance(choice, dict):
            result["candidate_set"].add(choice.get("candidate_set_revision_id"))
            result["candidate"].add(choice.get("candidate_id"))
        for item in path.get("chapters") or []:
            if not isinstance(item, dict):
                continue
            result["path_chapter"].add(item.get("path_chapter_id"))
            chapter = item.get("chapter_revision")
            if isinstance(chapter, dict):
                result["chapter"].add(chapter.get("id"))
                result["outline"].add(chapter.get("outline_revision_id"))
                chapter_state = chapter.get("state_snapshot")
                if isinstance(chapter_state, dict):
                    result["state"].add(chapter_state.get("id"))
            script = item.get("script_revision")
            if isinstance(script, dict):
                result["script"].add(script.get("id"))
            graph = item.get("vn_graph_revision")
            if isinstance(graph, dict):
                result["graph"].add(graph.get("id"))
    return {
        key: {value for value in values if value is not None}
        for key, values in result.items()
    }


def _lock_query(query, order_column) -> list[Any]:
    return query.populate_existing().order_by(order_column).with_for_update().all()


def _manifest_resource_ids(manifests: Iterable[dict[str, Any]]) -> dict[str, set[str]]:
    result = {
        "asset_version": set(),
        "storage": set(),
        "voice_version": set(),
    }
    for manifest in manifests:
        for item in manifest.get("asset_bindings") or []:
            if not isinstance(item, dict):
                continue
            if item.get("asset_version_id"):
                result["asset_version"].add(item["asset_version_id"])
            if item.get("storage_object_id"):
                result["storage"].add(item["storage_object_id"])
        for item in manifest.get("voice_line_versions") or []:
            if not isinstance(item, dict):
                continue
            if item.get("voice_line_version_id"):
                result["voice_version"].add(item["voice_line_version_id"])
            audio = item.get("audio_asset_version")
            if not isinstance(audio, dict):
                continue
            if audio.get("asset_version_id"):
                result["asset_version"].add(audio["asset_version_id"])
            if audio.get("storage_object_id"):
                result["storage"].add(audio["storage_object_id"])
    return result


def _lock_publication_source(
    db: Session,
    *,
    project_id: int,
    readiness: PublicationReadinessResult,
) -> None:
    ids = _snapshot_ids(readiness)
    _lock_query(db.query(Project).filter(Project.id == project_id), Project.id)
    _lock_query(
        db.query(ProjectContentHead).filter(
            ProjectContentHead.project_id == project_id
        ),
        ProjectContentHead.project_id,
    )
    _lock_query(
        db.query(StoryPath).filter(StoryPath.project_id == project_id),
        StoryPath.id,
    )
    if ids["path"]:
        _lock_query(
            db.query(StoryPathOutlineHead).filter(
                StoryPathOutlineHead.story_path_id.in_(ids["path"])
            ),
            StoryPathOutlineHead.story_path_id,
        )
        _lock_query(
            db.query(StoryPathChapter).filter(
                StoryPathChapter.story_path_id.in_(ids["path"]),
                StoryPathChapter.status == "active",
            ),
            StoryPathChapter.id,
        )
    if ids["bible"]:
        _lock_query(
            db.query(StoryBibleRevision).filter(
                StoryBibleRevision.id.in_(ids["bible"])
            ),
            StoryBibleRevision.id,
        )
    if ids["outline"]:
        _lock_query(
            db.query(OutlineRevision).filter(OutlineRevision.id.in_(ids["outline"])),
            OutlineRevision.id,
        )
        _lock_query(
            db.query(OutlineChapter).filter(
                OutlineChapter.outline_revision_id.in_(ids["outline"])
            ),
            OutlineChapter.id,
        )
    if ids["chapter"]:
        _lock_query(
            db.query(ChapterRevision).filter(
                ChapterRevision.id.in_(ids["chapter"])
            ),
            ChapterRevision.id,
        )
        _lock_query(
            db.query(ChapterScriptHead).filter(
                ChapterScriptHead.chapter_revision_id.in_(ids["chapter"])
            ),
            ChapterScriptHead.id,
        )
        _lock_query(
            db.query(VoiceLine).filter(
                VoiceLine.chapter_revision_id.in_(ids["chapter"])
            ),
            VoiceLine.id,
        )
    if ids["script"]:
        _lock_query(
            db.query(ChapterScriptRevision).filter(
                ChapterScriptRevision.id.in_(ids["script"])
            ),
            ChapterScriptRevision.id,
        )
        _lock_query(
            db.query(ScriptResourceSlot).filter(
                ScriptResourceSlot.chapter_script_revision_id.in_(ids["script"])
            ),
            ScriptResourceSlot.id,
        )
        _lock_query(
            db.query(VNGraphHead).filter(
                VNGraphHead.script_revision_id.in_(ids["script"])
            ),
            VNGraphHead.id,
        )
    graph_rows: list[VNGraphRevision] = []
    if ids["graph"]:
        graph_rows = _lock_query(
            db.query(VNGraphRevision).filter(
                VNGraphRevision.id.in_(ids["graph"])
            ),
            VNGraphRevision.id,
        )
        graphs_by_id = {graph.id: graph for graph in graph_rows}
        pending_parent_ids = {
            graph.parent_revision_id
            for graph in graph_rows
            if graph.parent_revision_id
        } - set(graphs_by_id)
        while pending_parent_ids:
            parent_rows = _lock_query(
                db.query(VNGraphRevision).filter(
                    VNGraphRevision.id.in_(pending_parent_ids)
                ),
                VNGraphRevision.id,
            )
            if not parent_rows:
                break
            for parent in parent_rows:
                graphs_by_id[parent.id] = parent
            pending_parent_ids = {
                parent.parent_revision_id
                for parent in parent_rows
                if parent.parent_revision_id
            } - set(graphs_by_id)
        graph_rows = list(graphs_by_id.values())
        action_ids = {
            derivation.get("asset_action_id")
            for graph in graph_rows
            for derivation in [
                (graph.binding_manifest or {}).get("derivation")
                if isinstance(graph.binding_manifest, dict)
                else None
            ]
            if isinstance(derivation, dict)
            and isinstance(derivation.get("asset_action_id"), str)
            and derivation.get("asset_action_id")
        }
        if action_ids:
            _lock_query(
                db.query(AssetAction).filter(AssetAction.id.in_(action_ids)),
                AssetAction.id,
            )
    if ids["state"]:
        _lock_query(
            db.query(StateSnapshot).filter(StateSnapshot.id.in_(ids["state"])),
            StateSnapshot.id,
        )
    if ids["candidate_set"]:
        _lock_query(
            db.query(CandidateSetRevision).filter(
                CandidateSetRevision.id.in_(ids["candidate_set"])
            ),
            CandidateSetRevision.id,
        )
    if ids["candidate"]:
        _lock_query(
            db.query(BranchCandidate).filter(
                BranchCandidate.id.in_(ids["candidate"])
            ),
            BranchCandidate.id,
        )

    resource_ids = _manifest_resource_ids(
        graph.binding_manifest
        for graph in graph_rows
        if isinstance(graph.binding_manifest, dict)
    )
    version_rows: list[AssetVersion] = []
    if resource_ids["asset_version"]:
        version_rows = _lock_query(
            db.query(AssetVersion).filter(
                AssetVersion.id.in_(resource_ids["asset_version"])
            ),
            AssetVersion.id,
        )
    asset_ids = {version.asset_id for version in version_rows}
    if asset_ids:
        _lock_query(db.query(Asset).filter(Asset.id.in_(asset_ids)), Asset.id)
    if resource_ids["storage"]:
        _lock_query(
            db.query(StorageObject).filter(
                StorageObject.id.in_(resource_ids["storage"])
            ),
            StorageObject.id,
        )
    if resource_ids["voice_version"]:
        _lock_query(
            db.query(VoiceLineVersion).filter(
                VoiceLineVersion.id.in_(resource_ids["voice_version"])
            ),
            VoiceLineVersion.id,
        )


def _assert_expected_fingerprint(
    readiness: PublicationReadinessResult,
    expected_fingerprint: str,
) -> None:
    if readiness.authoring_fingerprint != expected_fingerprint:
        raise AppError(
            code="release.source_changed",
            message="Reviewed authoring state changed; refresh readiness before publishing",
            status_code=409,
            details={
                "current_authoring_fingerprint": readiness.authoring_fingerprint,
            },
        )
    if not readiness.ready:
        raise AppError(
            code="release.not_ready",
            message="Project has publication blockers",
            status_code=422,
            details={
                "authoring_fingerprint": readiness.authoring_fingerprint,
                "blocking_items": [
                    item.as_dict() for item in readiness.blocking_items
                ],
            },
        )


def _active_release(
    db: Session,
    publication: ProjectPublication,
) -> ProjectRelease | None:
    if publication.active_release_id is None:
        return None
    release = (
        db.query(ProjectRelease)
        .filter(
            ProjectRelease.id == publication.active_release_id,
            ProjectRelease.project_id == publication.project_id,
        )
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if (
        not release
        or release.status != "published"
        or release.published_at is None
        or release.withdrawn_at is not None
        or publication.published_at is None
    ):
        raise AppError(
            code="release.active_invalid",
            message="ProjectPublication points to an invalid active Release",
            status_code=409,
        )
    return release


def _activate_release(
    *,
    publication: ProjectPublication,
    release: ProjectRelease,
    previous_release: ProjectRelease | None,
) -> None:
    publication.active_release_id = release.id
    publication.published_at = release.published_at
    publication.lock_version += 1


def build_release_manifest(
    db: Session,
    readiness: PublicationReadinessResult,
) -> dict[str, Any]:
    """Freeze a ready authoring snapshot and each graph's resource manifest."""

    if not readiness.ready:
        raise AppError(
            code="release.not_ready",
            message="Cannot build a Release manifest from blocked authoring state",
            status_code=422,
        )
    source = readiness.authoring_snapshot
    paths = deepcopy(source.get("story_paths") or [])
    graph_ids = {
        graph.get("id")
        for path in paths
        if isinstance(path, dict)
        for chapter in path.get("chapters") or []
        if isinstance(chapter, dict)
        for graph in [chapter.get("vn_graph_revision")]
        if isinstance(graph, dict) and graph.get("id")
    }
    graph_rows = (
        db.query(VNGraphRevision).filter(VNGraphRevision.id.in_(graph_ids)).all()
        if graph_ids
        else []
    )
    graphs = {graph.id: graph for graph in graph_rows}
    storage_object_ids: set[str] = set()
    for path in paths:
        for chapter in path.get("chapters") or []:
            graph_snapshot = chapter.get("vn_graph_revision")
            graph = (
                graphs.get(graph_snapshot.get("id"))
                if isinstance(graph_snapshot, dict)
                else None
            )
            if (
                not graph
                or graph.graph_hash != graph_snapshot.get("graph_hash")
                or graph.binding_manifest_hash
                != graph_snapshot.get("binding_manifest_hash")
                or content_hash(graph.graph_json) != graph.graph_hash
                or content_hash(graph.binding_manifest or {})
                != graph.binding_manifest_hash
            ):
                raise AppError(
                    code="release.source_changed",
                    message="Selected VNGraph changed while building the Release manifest",
                    status_code=409,
                )
            binding_manifest = deepcopy(graph.binding_manifest)
            graph_snapshot["binding_manifest"] = binding_manifest
            resource_ids = _manifest_resource_ids([binding_manifest])
            storage_object_ids.update(resource_ids["storage"])

    project_snapshot = deepcopy(source.get("project") or {})
    authoring_metadata_hash = content_hash(project_snapshot)
    project_snapshot.pop("extra_requirements", None)
    return {
        "schema_version": RELEASE_MANIFEST_VERSION,
        "authoring_fingerprint": readiness.authoring_fingerprint,
        "authoring_metadata_hash": authoring_metadata_hash,
        "project": project_snapshot,
        "bible_revision": deepcopy(source.get("bible_revision")),
        "root_story_path_id": source.get("root_story_path_id"),
        "story_paths": paths,
        "storage_object_ids": sorted(storage_object_ids),
    }


def publish_project(
    db: Session,
    *,
    project_id: int,
    user_id: int,
    expected_authoring_fingerprint: object,
    idempotency_key: object,
    release_notes: object = None,
) -> AtomicPublicationResult:
    """Create and activate the first Release in one rollback-safe transaction."""

    expected = _normalize_fingerprint(expected_authoring_fingerprint)
    key = _normalize_idempotency_key(idempotency_key)
    notes = _normalize_release_notes(release_notes)
    request_hash = content_hash(
        {
            "project_id": project_id,
            "expected_authoring_fingerprint": expected,
            "release_notes": notes,
        }
    )

    with db.begin_nested():
        publication = _lock_or_create_publication(db, project_id)
        replay = (
            db.query(ProjectRelease)
            .filter(
                ProjectRelease.project_id == project_id,
                ProjectRelease.publication_idempotency_key == key,
            )
            .one_or_none()
        )
        if replay is not None:
            if replay.publication_request_hash != request_hash:
                raise AppError(
                    code="idempotency_key.conflict",
                    message="Idempotency-Key belongs to a different publish request",
                    status_code=409,
                )
            return AtomicPublicationResult(release=replay, created=False)

        previous_release = _active_release(db, publication)

        readiness = calculate_publication_readiness(db, project_id=project_id)
        _assert_expected_fingerprint(readiness, expected)
        _lock_publication_source(
            db,
            project_id=project_id,
            readiness=readiness,
        )
        locked_readiness = calculate_publication_readiness(db, project_id=project_id)
        _assert_expected_fingerprint(locked_readiness, expected)
        if (
            previous_release is not None
            and previous_release.authoring_fingerprint
            == locked_readiness.authoring_fingerprint
        ):
            raise AppError(
                code="release.no_changes",
                message="The active Release already matches reviewed authoring state",
                status_code=409,
                details={"active_release_id": previous_release.id},
            )
        manifest = build_release_manifest(db, locked_readiness)
        manifest_hash = content_hash(manifest)
        duplicate = (
            db.query(ProjectRelease)
            .filter(
                ProjectRelease.project_id == project_id,
                ProjectRelease.manifest_hash == manifest_hash,
            )
            .one_or_none()
        )
        if duplicate is not None:
            raise AppError(
                code="release.content_already_released",
                message="This immutable authoring snapshot already has a Release",
                status_code=409,
                details={"release_id": duplicate.id},
            )

        root_path_id = manifest.get("root_story_path_id")
        root_path = next(
            (
                path
                for path in manifest.get("story_paths") or []
                if path.get("id") == root_path_id
            ),
            None,
        )
        bible = manifest.get("bible_revision")
        root_outline = root_path.get("outline_revision") if root_path else None
        if not isinstance(bible, dict) or not isinstance(root_outline, dict):
            raise AppError(
                code="release.source_changed",
                message="Ready authoring snapshot lost its root revisions",
                status_code=409,
            )

        version = int(
            db.query(func.max(ProjectRelease.version))
            .filter(ProjectRelease.project_id == project_id)
            .scalar()
            or 0
        ) + 1
        published_at = utcnow()
        release = ProjectRelease(
            project_id=project_id,
            version=version,
            status="published",
            bible_revision_id=bible["id"],
            outline_revision_id=root_outline["id"],
            manifest_json=manifest,
            manifest_hash=manifest_hash,
            authoring_fingerprint=locked_readiness.authoring_fingerprint,
            release_notes=notes,
            publication_idempotency_key=key,
            publication_request_hash=request_hash,
            created_by=user_id,
            published_at=published_at,
        )
        db.add(release)
        db.flush()
        _activate_release(
            publication=publication,
            release=release,
            previous_release=previous_release,
        )
        db.flush()
        if previous_release is not None:
            previous_release.status = "superseded"
            db.flush()
        return AtomicPublicationResult(release=release, created=True)


def unpublish_project(
    db: Session,
    *,
    project_id: int,
    expected_lock_version: object,
) -> ProjectRelease:
    """Withdraw the sole active Release and clear its pointer atomically."""

    if (
        isinstance(expected_lock_version, bool)
        or not isinstance(expected_lock_version, int)
        or expected_lock_version < 1
    ):
        raise AppError(
            code="precondition.invalid",
            message="ProjectPublication lock version must be a positive integer",
            status_code=400,
        )
    with db.begin_nested():
        publication = (
            db.query(ProjectPublication)
            .filter(ProjectPublication.project_id == project_id)
            .populate_existing()
            .with_for_update()
            .one_or_none()
        )
        if not publication:
            raise AppError(
                code="release.active_not_found",
                message="Project has no active Release",
                status_code=404,
            )
        if publication.lock_version != expected_lock_version:
            raise AppError(
                code="publication.version_conflict",
                message="ProjectPublication changed; refresh before unpublishing",
                status_code=409,
                details={
                    "current_lock_version": publication.lock_version,
                    "active_release_id": publication.active_release_id,
                },
            )
        if publication.active_release_id is None:
            raise AppError(
                code="release.active_not_found",
                message="Project has no active Release",
                status_code=404,
            )
        release = _active_release(db, publication)
        if release is None:
            raise AppError(
                code="release.active_not_found",
                message="Project has no active Release",
                status_code=404,
            )
        publication.active_release_id = None
        publication.published_at = None
        publication.lock_version += 1
        db.flush()
        release.status = "withdrawn"
        release.withdrawn_at = utcnow()
        db.flush()
        return release


__all__ = [
    "AtomicPublicationResult",
    "RELEASE_MANIFEST_VERSION",
    "build_release_manifest",
    "publish_project",
    "unpublish_project",
]
