from __future__ import annotations

from collections import defaultdict
from collections import deque
from copy import deepcopy
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.revision_service import project_readiness
from app.models import Project
from app.models_v2 import (
    AssetBinding,
    AssetVersion,
    BranchCandidate,
    BranchEdge,
    ChapterHead,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    ProjectRelease,
    StoryBibleRevision,
    StoryNode,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
    utcnow,
)


_PUBLIC_SUMMARY_LENGTH = 240


def _release_summary(value: str | None) -> str:
    summary = " ".join((value or "").split())
    if len(summary) <= _PUBLIC_SUMMARY_LENGTH:
        return summary
    return f"{summary[: _PUBLIC_SUMMARY_LENGTH - 1].rstrip()}…"


def _validate_release_story_dag(
    *,
    nodes: list[StoryNode],
    edges: list[BranchEdge],
    candidates: list[BranchCandidate],
) -> str | None:
    """Validate the complete authoring graph before freezing a release."""

    node_ids = {node.id for node in nodes}
    if any(edge.from_node_id not in node_ids or edge.to_node_id not in node_ids for edge in edges):
        raise HTTPException(status_code=409, detail="当前故事图存在指向旧内容版本的边")

    branch_edges: dict[tuple[str, str], BranchEdge] = {}
    for edge in edges:
        is_branch = edge.edge_type == "branch" or edge.option_key is not None
        if not is_branch:
            continue
        if not edge.option_key:
            raise HTTPException(status_code=409, detail="分支 DAG 边缺少 option_key")
        key = (edge.from_node_id, edge.option_key)
        if key in branch_edges:
            raise HTTPException(status_code=409, detail="分支 DAG 存在重复 option")
        branch_edges[key] = edge

    candidate_keys: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (candidate.checkpoint_node_id, candidate.option_key)
        edge = branch_edges.get(key)
        if (
            candidate.checkpoint_node_id not in node_ids
            or candidate.preview_node_id not in node_ids
            or edge is None
            or edge.to_node_id != candidate.preview_node_id
            or (edge.state_delta or {}) != (candidate.state_delta or {})
        ):
            raise HTTPException(status_code=409, detail="分支候选与 DAG 边不一致")
        candidate_keys.add(key)
    if set(branch_edges) != candidate_keys:
        raise HTTPException(status_code=409, detail="分支 DAG 边缺少对应候选")

    indegree = {node_id: 0 for node_id in node_ids}
    children: dict[str, list[str]] = {}
    for edge in edges:
        indegree[edge.to_node_id] += 1
        children.setdefault(edge.from_node_id, []).append(edge.to_node_id)
    start_nodes = [node_id for node_id, count in indegree.items() if count == 0]
    queue = deque(start_nodes)
    visited = 0
    while queue:
        node_id = queue.popleft()
        visited += 1
        for child_id in children.get(node_id, []):
            indegree[child_id] -= 1
            if indegree[child_id] == 0:
                queue.append(child_id)
    if visited != len(node_ids):
        raise HTTPException(status_code=409, detail="当前故事图存在环，不能发布")
    if nodes and len(start_nodes) != 1:
        raise HTTPException(status_code=409, detail="当前故事图必须且只能有一个起始节点")
    return start_nodes[0] if start_nodes else None


def _current_chapters(db: Session, project_id: int) -> list[ChapterRevision]:
    heads = (
        db.query(ChapterHead)
        .filter(ChapterHead.project_id == project_id)
        .order_by(ChapterHead.chapter_index)
        .all()
    )
    result: list[ChapterRevision] = []
    for head in heads:
        revision = db.query(ChapterRevision).filter(ChapterRevision.id == head.current_revision_id).first()
        if not revision:
            raise HTTPException(status_code=409, detail=f"第 {head.chapter_index} 章 current pointer 损坏")
        result.append(revision)
    return result


def build_release_manifest(db: Session, project_id: int) -> dict[str, Any]:
    project = db.query(Project).filter(Project.id == project_id).first()
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == project_id).first()
    if not project or not head or not head.current_bible_revision_id or not head.current_outline_revision_id:
        raise HTTPException(status_code=409, detail="项目尚未具备可发布的 v2 内容版本")

    bible = db.query(StoryBibleRevision).filter(StoryBibleRevision.id == head.current_bible_revision_id).first()
    outline = db.query(OutlineRevision).filter(OutlineRevision.id == head.current_outline_revision_id).first()
    chapters = _current_chapters(db, project_id)
    if not bible or not outline or not chapters:
        raise HTTPException(status_code=409, detail="发布至少需要 Bible、Outline 和一章正文")

    outline_chapters = (
        db.query(OutlineChapter)
        .filter(OutlineChapter.outline_revision_id == outline.id)
        .order_by(OutlineChapter.chapter_index)
        .all()
    )
    graph_heads = {
        item.chapter_index: item
        for item in db.query(VNGraphHead).filter(VNGraphHead.project_id == project_id).all()
    }
    script_heads = {
        item.chapter_index: item
        for item in db.query(ChapterScriptHead).filter(ChapterScriptHead.project_id == project_id).all()
    }
    bindings = (
        db.query(AssetBinding)
        .filter(AssetBinding.project_id == project_id, AssetBinding.deleted_at.is_(None))
        .order_by(AssetBinding.source_kind, AssetBinding.source_id, AssetBinding.order_index)
        .all()
    )
    versions = {
        item.id: item
        for item in db.query(AssetVersion)
        .filter(AssetVersion.id.in_([binding.asset_version_id for binding in bindings] or ["-"]))
        .all()
    }
    bindings_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    storage_object_ids: set[str] = set()
    for binding in bindings:
        version = versions.get(binding.asset_version_id)
        bindings_by_source[f"{binding.source_kind}:{binding.source_id}"].append(
            {
                "binding_id": binding.id,
                "node_key": binding.node_key,
                "segment_key": binding.segment_key,
                "role": binding.role,
                "order_index": binding.order_index,
                "required": binding.required,
                "asset_version_id": binding.asset_version_id,
                "storage_object_id": version.storage_object_id if version else None,
            }
        )

    chapter_rows: list[dict[str, Any]] = []
    for chapter in chapters:
        script_head = script_heads.get(chapter.chapter_index)
        script = (
            db.query(ChapterScriptRevision)
            .filter(ChapterScriptRevision.id == script_head.current_revision_id)
            .first()
            if script_head
            else None
        )
        if not script or script.chapter_revision_id != chapter.id:
            raise HTTPException(
                status_code=409,
                detail=f"第 {chapter.chapter_index} 章缺少与当前正文匹配的 Chapter Script",
            )
        graph = None
        graph_head = graph_heads.get(chapter.chapter_index)
        if graph_head:
            graph = db.query(VNGraphRevision).filter(VNGraphRevision.id == graph_head.current_revision_id).first()
        if not graph:
            raise HTTPException(
                status_code=409,
                detail=f"第 {chapter.chapter_index} 章缺少 VN Graph",
            )
        if graph.script_revision_id != script.id:
            raise HTTPException(
                status_code=409,
                detail=f"第 {chapter.chapter_index} 章 VN Graph 未基于当前 Chapter Script 编译",
            )
        voice_lines = (
            db.query(VoiceLine)
            .filter(VoiceLine.chapter_revision_id == chapter.id)
            .order_by(VoiceLine.order_index)
            .all()
        )
        voice_line_rows: list[dict[str, Any]] = []
        for line in voice_lines:
            storage_object_id = None
            if line.audio_asset_version_id:
                version = versions.get(line.audio_asset_version_id) or db.query(AssetVersion).filter(
                    AssetVersion.id == line.audio_asset_version_id
                ).first()
                if version and version.storage_object_id:
                    storage_object_id = version.storage_object_id
                    storage_object_ids.add(version.storage_object_id)
            voice_line_rows.append(
                {
                    "id": line.id,
                    "occurrence_id": line.occurrence_id,
                    "order_index": line.order_index,
                    "status": line.status,
                    "audio_asset_version_id": line.audio_asset_version_id,
                    "storage_object_id": storage_object_id,
                }
            )
        script_bindings = bindings_by_source.get(f"chapter_script_revision:{script.id}", [])
        storage_object_ids.update(
            binding["storage_object_id"]
            for binding in script_bindings
            if binding.get("storage_object_id")
        )
        chapter_rows.append(
            {
                "chapter_index": chapter.chapter_index,
                "chapter_revision_id": chapter.id,
                "content_hash": chapter.content_hash,
                "chapter_script_revision_id": script.id,
                "script_hash": script.script_hash,
                "vngraph_revision_id": graph.id if graph else None,
                "vngraph_hash": graph.graph_hash if graph else None,
                "asset_bindings": script_bindings,
                "voice_lines": voice_line_rows,
            }
        )

    current_chapter_ids = {chapter.id for chapter in chapters}
    nodes = (
        db.query(StoryNode)
        .filter(
            StoryNode.project_id == project_id,
            StoryNode.release_id.is_(None),
            StoryNode.content_revision_id.in_(current_chapter_ids or {"-"}),
        )
        .order_by(StoryNode.created_at, StoryNode.id)
        .all()
    )
    node_ids = [node.id for node in nodes]
    edges = (
        db.query(BranchEdge)
        .filter(BranchEdge.project_id == project_id, BranchEdge.from_node_id.in_(node_ids or ["-"]))
        .order_by(BranchEdge.created_at, BranchEdge.id)
        .all()
    )
    candidates = (
        db.query(BranchCandidate)
        .filter(
            BranchCandidate.project_id == project_id,
            BranchCandidate.checkpoint_node_id.in_(node_ids or ["-"]),
        )
        .order_by(BranchCandidate.created_at, BranchCandidate.id)
        .all()
    )
    start_node_id = _validate_release_story_dag(
        nodes=nodes,
        edges=edges,
        candidates=candidates,
    )

    return {
        "schema_version": "2.0",
        "project": {
            "id": project.id,
            "title": project.title,
            "style": project.style,
            "summary": _release_summary(project.story_start),
        },
        "bible_revision": {"id": bible.id, "content_hash": bible.content_hash},
        "outline_revision": {
            "id": outline.id,
            "content_hash": outline.content_hash,
            "chapters": [
                {"chapter_index": row.chapter_index, "title": row.title, "content_hash": row.content_hash}
                for row in outline_chapters
            ],
        },
        "chapters": chapter_rows,
        "story": {
            "start_node_id": start_node_id,
            "nodes": [
                {
                    "id": node.id,
                    "node_type": node.node_type,
                    "checkpoint_key": node.checkpoint_key,
                    "merge_key": node.merge_key,
                    "content_revision_id": node.content_revision_id,
                    "payload": deepcopy(node.payload),
                }
                for node in nodes
            ],
            "edges": [
                {
                    "id": edge.id,
                    "from_node_id": edge.from_node_id,
                    "to_node_id": edge.to_node_id,
                    "option_key": edge.option_key,
                    "state_delta": deepcopy(edge.state_delta),
                    "edge_type": edge.edge_type,
                }
                for edge in edges
            ],
            # Choice execution consumes this immutable snapshot. The live
            # BranchCandidate row is retained only as a referential anchor for
            # ChoiceDecision and may not change released behavior.
            "candidates": [
                {
                    "id": candidate.id,
                    "checkpoint_node_id": candidate.checkpoint_node_id,
                    "option_key": candidate.option_key,
                    "preview_node_id": candidate.preview_node_id,
                    "preview_revision_id": candidate.preview_revision_id,
                    "state_delta": deepcopy(candidate.state_delta),
                    "candidate_status": candidate.candidate_status,
                    "predicted_probability": candidate.predicted_probability,
                }
                for candidate in candidates
            ],
        },
        "storage_object_ids": sorted(storage_object_ids),
    }


def create_release(
    db: Session,
    *,
    project_id: int,
    user_id: int,
    publish: bool,
) -> tuple[ProjectRelease, bool]:
    # Serialize release snapshots with every authoring operation that can
    # mutate the live branch graph.  Branch-candidate persistence takes this
    # same lock before its final published-check, so a release can never omit
    # candidates that commit immediately after its manifest was assembled.
    content_head = (
        db.query(ProjectContentHead)
        .filter(ProjectContentHead.project_id == project_id)
        .with_for_update()
        .first()
    )
    if not content_head:
        raise HTTPException(status_code=409, detail="项目内容 head 不存在")
    readiness = project_readiness(db, project_id)
    if publish and not readiness["ready"]:
        raise HTTPException(status_code=409, detail={"message": "项目存在过期产物或不完整", "issues": readiness["issues"]})

    manifest = build_release_manifest(db, project_id)
    digest = content_hash(manifest)
    existing = (
        db.query(ProjectRelease)
        .filter(ProjectRelease.project_id == project_id, ProjectRelease.manifest_hash == digest)
        .first()
    )
    if existing:
        if existing.status == "withdrawn":
            raise HTTPException(
                status_code=409,
                detail="相同内容的发布版本已撤回；请创建新的内容修订后再发布",
            )
        if publish and existing.status != "published":
            existing.status = "published"
            existing.published_at = utcnow()
            existing.withdrawn_at = None
        _activate_published_release(db, existing) if publish else None
        return existing, False

    version = int(db.query(func.max(ProjectRelease.version)).filter(ProjectRelease.project_id == project_id).scalar() or 0) + 1
    release = ProjectRelease(
        project_id=project_id,
        version=version,
        status="published" if publish else "draft",
        bible_revision_id=manifest["bible_revision"]["id"],
        outline_revision_id=manifest["outline_revision"]["id"],
        manifest_json=deepcopy(manifest),
        manifest_hash=digest,
        created_by=user_id,
        published_at=utcnow() if publish else None,
    )
    db.add(release)
    db.flush()
    if publish:
        _activate_published_release(db, release)
    return release, True


def _activate_published_release(db: Session, release: ProjectRelease) -> None:
    if release.status != "published" or release.withdrawn_at is not None:
        raise HTTPException(status_code=409, detail="只有有效 published release 可以激活")
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == release.project_id).with_for_update().first()
    if not head:
        raise HTTPException(status_code=409, detail="项目内容 head 不存在")
    project = db.query(Project).filter(Project.id == release.project_id).with_for_update().one()
    head.published_release_id = release.id
    head.lock_version += 1
    project.visibility = "public"
    project.published_at = release.published_at or utcnow()


def withdraw_release(db: Session, release: ProjectRelease) -> None:
    if release.status == "withdrawn":
        return
    release.status = "withdrawn"
    release.withdrawn_at = utcnow()
    head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == release.project_id).with_for_update().first()
    if head and head.published_release_id == release.id:
        head.published_release_id = None
        head.lock_version += 1
        project = db.query(Project).filter(Project.id == release.project_id).with_for_update().one()
        project.visibility = "private"


def get_public_release(db: Session, release_id: str) -> ProjectRelease:
    release = (
        db.query(ProjectRelease)
        .filter(ProjectRelease.id == release_id, ProjectRelease.status == "published")
        .filter(ProjectRelease.withdrawn_at.is_(None))
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="发布版本不存在")
    return release
