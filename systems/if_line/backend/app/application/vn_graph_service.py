"""Application commands and queries for deterministic v2 VNGraph revisions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.application.hashing import content_hash, js_normalized_json
from app.application.revision_head_service import (
    RevisionHeadValue,
    head_value,
    raise_head_version_conflict,
)
from app.application.task_service import (
    claim_task,
    complete_task,
    create_generation_task,
    fail_task,
    heartbeat_task,
    lease_from_task,
)
from app.application.vn_graph_manifest import (
    asset_version_reference,
    ensure_voice_line_version,
    validate_asset_version_reference,
    validate_voice_line_version_manifest_item,
    voice_line_version_manifest_item,
)
from app.core.errors import AppError
from app.models import Asset
from app.models_v2 import (
    AssetBinding,
    AssetVersion,
    ChapterRevision,
    ChapterScriptRevision,
    GenerationTask,
    ScriptResourceSlot,
    StorageObject,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
    utcnow,
)
from app.services.vn_graph_compiler import (
    VNGRAPH_BINDING_MANIFEST_VERSION,
    VNGRAPH_COMPILER_VERSION,
    VNGRAPH_SCHEMA_VERSION,
    VNGRAPH_TACHI_POLICY_VERSION,
    VNGraphCompileError,
    VNGraphCompileInput,
    VNGraphCompileResult,
    vn_graph_compiler,
)


VNGRAPH_COMPILE_TASK_KIND = "vngraph.compile"
VNGRAPH_COMPILE_EVENT = "vngraph.compile.requested"
_SELECTABLE_SCRIPT_STATUSES = frozenset({"ready", "complete"})
_SELECTABLE_CHAPTER_STATUSES = frozenset({"ready", "complete"})
_SELECTABLE_GRAPH_STATUSES = frozenset({"ready", "complete"})


@dataclass(frozen=True)
class VNGraphHeadActivation:
    revision: VNGraphRevision
    head: RevisionHeadValue


def _media_url(storage: StorageObject | None, legacy_url: str | None = None) -> str | None:
    if storage and storage.status == "active" and storage.deleted_at is None:
        return f"/api/media/{storage.id}"
    value = (legacy_url or "").strip()
    return value or None


def _asset_binding_manifest(
    db: Session,
    *,
    project_id: int,
    chapter_revision_id: str,
    script_revision_id: str,
) -> list[dict[str, Any]]:
    slots = (
        db.query(ScriptResourceSlot)
        .filter(
            ScriptResourceSlot.project_id == project_id,
            ScriptResourceSlot.chapter_script_revision_id == script_revision_id,
        )
        .order_by(ScriptResourceSlot.order_index, ScriptResourceSlot.id)
        .all()
    )
    bindings = (
        db.query(AssetBinding)
        .filter(
            AssetBinding.project_id == project_id,
            AssetBinding.source_kind == (
                "chapter_script_revision" if slots else "chapter_revision"
            ),
            AssetBinding.source_id == (script_revision_id if slots else chapter_revision_id),
            AssetBinding.deleted_at.is_(None),
        )
        .order_by(
            AssetBinding.order_index,
            AssetBinding.role,
            AssetBinding.node_key,
            AssetBinding.segment_key,
            AssetBinding.id,
        )
        .all()
    )
    version_ids = [
        version_id
        for version_id in (
            [slot.asset_version_id for slot in slots]
            if slots
            else [binding.asset_version_id for binding in bindings]
        )
        if version_id
    ]
    versions = {
        row.id: row
        for row in (
            db.query(AssetVersion).filter(AssetVersion.id.in_(version_ids)).all()
            if version_ids
            else []
        )
    }
    asset_ids = [version.asset_id for version in versions.values()]
    assets = {
        row.id: row
        for row in (db.query(Asset).filter(Asset.id.in_(asset_ids)).all() if asset_ids else [])
    }
    storage_ids = [
        version.storage_object_id
        for version in versions.values()
        if version.storage_object_id
    ]
    storage = {
        row.id: row
        for row in (
            db.query(StorageObject).filter(StorageObject.id.in_(storage_ids)).all()
            if storage_ids
            else []
        )
    }

    result: list[dict[str, Any]] = []
    source_items: list[tuple[ScriptResourceSlot | None, AssetBinding | None]]
    if slots:
        binding_by_order = {(item.role, item.order_index): item for item in bindings}
        source_items = [
            (slot, binding_by_order.get((slot.role, slot.order_index)))
            for slot in slots
        ]
    else:
        source_items = [(None, binding) for binding in bindings]

    for slot, binding in source_items:
        version_id = slot.asset_version_id if slot else binding.asset_version_id
        version = versions.get(version_id)
        # 槽位语义（如立绘情绪）以 spec_json 为准：Asset.emotion 对预分配槽位资产从不落库。
        slot_spec = slot.spec_json if slot is not None and isinstance(slot.spec_json, dict) else {}
        slot_emotion = str(slot_spec.get("emotion") or "").strip() or None
        base = {
            "resource_slot_id": slot.id if slot else None,
            "binding_id": binding.id if binding else (slot.id if slot else None),
            "source_kind": binding.source_kind if binding else "chapter_script_revision",
            "source_id": binding.source_id if binding else script_revision_id,
            "node_key": slot.scene_id if slot else binding.node_key,
            "segment_key": slot.paragraph_id if slot else binding.segment_key,
            "scene_id": slot.scene_id if slot else binding.node_key,
            "paragraph_id": slot.paragraph_id if slot else binding.segment_key,
            "character_id": slot.character_id if slot else None,
            "role": slot.role if slot else binding.role,
            "order_index": slot.order_index if slot else binding.order_index,
            "required": slot.required if slot else binding.required,
            "asset_version_id": version_id,
            "slot_status": slot.status if slot else "bound",
        }
        if not version:
            result.append({**base, "status": "missing_version"})
            continue
        asset = assets.get(version.asset_id)
        stored = storage.get(version.storage_object_id) if version.storage_object_id else None
        resolved_media_url = _media_url(
            stored,
            # A version with an explicit but unavailable StorageObject must
            # fail closed instead of falling back to a legacy public URL.
            asset.image_url if asset and not version.storage_object_id else None,
        )
        result.append(
            {
                **base,
                **asset_version_reference(version, stored),
                "media_url": resolved_media_url,
                "legacy_url": asset.image_url if asset else None,
                "asset_type": asset.asset_type if asset else None,
                "target_name": asset.target_name if asset else None,
                "character_id": base["character_id"] or (asset.character_id if asset else None),
                "emotion": slot_emotion or (asset.emotion if asset else None),
                "safety_status": version.safety_status,
                "quality_score": version.quality_score,
                "status": "ready" if resolved_media_url else "missing_media",
            }
        )
    return result


def _voice_line_manifest(
    db: Session,
    *,
    chapter_revision_id: str,
) -> list[dict[str, Any]]:
    lines = (
        db.query(VoiceLine)
        .filter(VoiceLine.chapter_revision_id == chapter_revision_id)
        .order_by(VoiceLine.order_index, VoiceLine.id)
        .with_for_update()
        .all()
    )
    line_versions = [ensure_voice_line_version(db, line) for line in lines]
    version_ids = [line.audio_asset_version_id for line in lines if line.audio_asset_version_id]
    versions = {
        row.id: row
        for row in (
            db.query(AssetVersion).filter(AssetVersion.id.in_(version_ids)).all()
            if version_ids
            else []
        )
    }
    storage_ids = [
        version.storage_object_id
        for version in versions.values()
        if version.storage_object_id
    ]
    storage = {
        row.id: row
        for row in (
            db.query(StorageObject).filter(StorageObject.id.in_(storage_ids)).all()
            if storage_ids
            else []
        )
    }

    result: list[dict[str, Any]] = []
    for line, line_version in zip(lines, line_versions, strict=True):
        version = (
            versions.get(line.audio_asset_version_id)
            if line.audio_asset_version_id
            else None
        )
        stored = (
            storage.get(version.storage_object_id)
            if version and version.storage_object_id
            else None
        )
        result.append(
            voice_line_version_manifest_item(
                line_version,
                version,
                stored,
                audio_url=_media_url(stored),
            )
        )
    return result


def _load_exact_script_source(
    db: Session,
    *,
    script_revision_id: str,
    expected_project_id: int | None = None,
) -> tuple[ChapterScriptRevision, ChapterRevision]:
    script = (
        db.query(ChapterScriptRevision)
        .filter(ChapterScriptRevision.id == script_revision_id)
        .one_or_none()
    )
    if not script or (
        expected_project_id is not None and script.project_id != expected_project_id
    ):
        raise AppError(
            code="script_revision.not_found",
            message="ChapterScriptRevision does not exist",
            status_code=404,
        )
    if (
        script.status not in _SELECTABLE_SCRIPT_STATUSES
        or content_hash(script.script_json) != script.script_hash
    ):
        raise AppError(
            code="script_revision.not_ready",
            message="ChapterScriptRevision is not ready for VNGraph compilation",
            status_code=409,
        )
    chapter = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id == script.chapter_revision_id)
        .one_or_none()
    )
    if (
        not chapter
        or chapter.project_id != script.project_id
        or chapter.status not in _SELECTABLE_CHAPTER_STATUSES
        or content_hash(chapter.content) != chapter.content_hash
    ):
        raise AppError(
            code="script_revision.source_invalid",
            message="ChapterScriptRevision source is missing or invalid",
            status_code=409,
        )
    return script, chapter


def _validate_immutable_resources(
    db: Session,
    *,
    project_id: int,
    chapter_revision_id: str,
    asset_bindings: list[dict[str, Any]],
    voice_line_versions: list[dict[str, Any]],
) -> None:
    try:
        for item in asset_bindings:
            if item.get("asset_version_id"):
                validate_asset_version_reference(
                    db,
                    item,
                    expected_project_id=project_id,
                )
        for item in voice_line_versions:
            validate_voice_line_version_manifest_item(
                db,
                item,
                expected_project_id=project_id,
                expected_chapter_revision_id=chapter_revision_id,
            )
    except (AttributeError, TypeError, ValueError) as exc:
        raise VNGraphCompileError(str(exc)) from exc


def _refresh_vn_graph_head(db: Session, head: VNGraphHead) -> VNGraphHead:
    db.expire(head)
    db.refresh(head)
    return head


def get_or_create_vn_graph_head(
    db: Session,
    script_revision_id: str,
    *,
    for_update: bool = False,
) -> VNGraphHead:
    script_query = db.query(ChapterScriptRevision).filter(
        ChapterScriptRevision.id == script_revision_id
    )
    if for_update:
        script_query = script_query.with_for_update()
    script = script_query.one_or_none()
    if not script:
        raise AppError(
            code="script_revision.not_found",
            message="ChapterScriptRevision does not exist",
            status_code=404,
        )

    head_query = db.query(VNGraphHead).filter(
        VNGraphHead.script_revision_id == script.id
    )
    if for_update:
        head_query = head_query.with_for_update()
    head = head_query.one_or_none()
    if not head and not for_update:
        script = (
            db.query(ChapterScriptRevision)
            .filter(ChapterScriptRevision.id == script.id)
            .with_for_update()
            .one()
        )
        head = (
            db.query(VNGraphHead)
            .filter(VNGraphHead.script_revision_id == script.id)
            .with_for_update()
            .one_or_none()
        )
    if not head:
        head = VNGraphHead(
            project_id=script.project_id,
            chapter_index=script.chapter_index,
            script_revision_id=script.id,
            current_revision_id=None,
            lock_version=1,
        )
        db.add(head)
        db.flush()
    if head.current_revision_id:
        selected = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == head.current_revision_id,
                VNGraphRevision.script_revision_id == script.id,
            )
            .one_or_none()
        )
        if not selected:
            raise AppError(
                code="vngraph_head.invalid",
                message="VNGraph Head points outside its ChapterScriptRevision",
                status_code=409,
            )
    return head


def get_vn_graph_head(
    db: Session,
    *,
    script_revision_id: str,
) -> RevisionHeadValue:
    return head_value(get_or_create_vn_graph_head(db, script_revision_id))


def activate_vn_graph_head(
    db: Session,
    *,
    script_revision_id: str,
    revision_id: str,
    expected_lock_version: int,
) -> VNGraphHeadActivation:
    if expected_lock_version < 1:
        raise AppError(
            code="precondition.invalid",
            message="Head lock version must be positive",
            status_code=400,
        )
    head = get_or_create_vn_graph_head(db, script_revision_id)
    revision = (
        db.query(VNGraphRevision)
        .filter(
            VNGraphRevision.id == revision_id,
            VNGraphRevision.script_revision_id == script_revision_id,
        )
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="vngraph_revision.not_found",
            message="VNGraphRevision does not belong to this ChapterScriptRevision",
            status_code=404,
        )
    if (
        revision.status not in _SELECTABLE_GRAPH_STATUSES
        or content_hash(revision.graph_json) != revision.graph_hash
    ):
        raise AppError(
            code="vngraph_revision.not_ready",
            message="VNGraphRevision is not ready for review selection",
            status_code=409,
        )

    same_revision = head.current_revision_id == revision.id
    values: dict[str, object]
    if same_revision:
        values = {
            "lock_version": VNGraphHead.lock_version,
            "updated_at": VNGraphHead.updated_at,
        }
    else:
        values = {
            "current_revision_id": revision.id,
            "lock_version": VNGraphHead.lock_version + 1,
            "updated_at": utcnow(),
        }
    result = db.execute(
        update(VNGraphHead)
        .where(
            VNGraphHead.id == head.id,
            VNGraphHead.lock_version == expected_lock_version,
            *((VNGraphHead.current_revision_id == revision.id,) if same_revision else ()),
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise_head_version_conflict(_refresh_vn_graph_head(db, head))
    return VNGraphHeadActivation(
        revision=revision,
        head=head_value(_refresh_vn_graph_head(db, head)),
    )


def list_script_vn_graph_revisions(
    db: Session,
    *,
    script_revision_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[VNGraphRevision]:
    _load_exact_script_source(db, script_revision_id=script_revision_id)
    return (
        db.query(VNGraphRevision)
        .filter(VNGraphRevision.script_revision_id == script_revision_id)
        .order_by(VNGraphRevision.revision_no.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_vn_graph_revision_by_id(
    db: Session,
    *,
    revision_id: str,
) -> VNGraphRevision:
    revision = (
        db.query(VNGraphRevision)
        .filter(VNGraphRevision.id == revision_id)
        .one_or_none()
    )
    if not revision:
        raise AppError(
            code="vngraph_revision.not_found",
            message="VNGraphRevision does not exist",
            status_code=404,
        )
    return revision


def load_compile_input(
    db: Session,
    *,
    script_revision_id: str,
    expected_project_id: int | None = None,
    schema_version: str = VNGRAPH_SCHEMA_VERSION,
    compiler_version: str = VNGRAPH_COMPILER_VERSION,
    tachi_policy_version: str = VNGRAPH_TACHI_POLICY_VERSION,
) -> VNGraphCompileInput:
    script, chapter = _load_exact_script_source(
        db,
        script_revision_id=script_revision_id,
        expected_project_id=expected_project_id,
    )
    head = get_or_create_vn_graph_head(db, script.id)
    source = VNGraphCompileInput(
        project_id=script.project_id,
        display_index=script.chapter_index,
        chapter_revision_id=chapter.id,
        chapter_content_hash=chapter.content_hash,
        chapter_content=chapter.content,
        script_revision_id=script.id,
        script_hash=script.script_hash,
        script_ir=script.script_json,
        asset_bindings=_asset_binding_manifest(
            db,
            project_id=script.project_id,
            chapter_revision_id=chapter.id,
            script_revision_id=script.id,
        ),
        voice_line_versions=_voice_line_manifest(db, chapter_revision_id=chapter.id),
        parent_revision_id=head.current_revision_id if head else None,
        schema_version=schema_version,
        compiler_version=compiler_version,
        tachi_policy_version=tachi_policy_version,
    )
    try:
        vn_graph_compiler.binding_manifest(source)
        _validate_immutable_resources(
            db,
            project_id=source.project_id,
            chapter_revision_id=source.chapter_revision_id,
            asset_bindings=list(source.asset_bindings),
            voice_line_versions=list(source.voice_line_versions),
        )
    except VNGraphCompileError as exc:
        raise AppError(
            code="vngraph.source_invalid",
            message=str(exc),
            status_code=422,
        ) from exc
    return source


def create_vn_graph_compile_task(
    db: Session,
    *,
    user_id: int,
    script_revision_id: str,
    idempotency_key: str,
    schema_version: str = VNGRAPH_SCHEMA_VERSION,
    compiler_version: str = VNGRAPH_COMPILER_VERSION,
    tachi_policy_version: str = VNGRAPH_TACHI_POLICY_VERSION,
) -> tuple[GenerationTask, bool]:
    request_identity = {
        "script_revision_id": script_revision_id,
        "schema_version": schema_version,
        "compiler_version": compiler_version,
        "tachi_policy_version": tachi_policy_version,
    }
    existing = (
        db.query(GenerationTask)
        .filter(
            GenerationTask.user_id == user_id,
            GenerationTask.idempotency_key == idempotency_key,
        )
        .one_or_none()
    )
    if existing:
        if (
            existing.kind != VNGRAPH_COMPILE_TASK_KIND
            or (existing.source_refs or {}).get("request_identity") != request_identity
        ):
            raise HTTPException(status_code=409, detail="Idempotency-Key 已用于不同请求")
        return existing, False

    source = load_compile_input(
        db,
        script_revision_id=script_revision_id,
        schema_version=schema_version,
        compiler_version=compiler_version,
        tachi_policy_version=tachi_policy_version,
    )
    head = get_or_create_vn_graph_head(db, script_revision_id, for_update=True)
    source = replace(source, parent_revision_id=head.current_revision_id)
    manifest = vn_graph_compiler.binding_manifest(source)
    manifest_hash = content_hash(manifest)
    parameters = {
        "parent_revision_id": source.parent_revision_id,
        "binding_manifest": manifest,
        "binding_manifest_hash": manifest_hash,
    }
    return create_generation_task(
        db,
        user_id=user_id,
        project_id=source.project_id,
        kind=VNGRAPH_COMPILE_TASK_KIND,
        idempotency_key=idempotency_key,
        source_refs={
            "request_identity": request_identity,
            "chapter_revision_id": source.chapter_revision_id,
            "script_revision_id": source.script_revision_id,
            "script_hash": source.script_hash,
            "chapter_content_hash": source.chapter_content_hash,
            "binding_manifest_hash": manifest_hash,
        },
        parameters=parameters,
        estimated_cost=Decimal("0"),
        enqueue_event_type=VNGRAPH_COMPILE_EVENT,
    )


def _input_from_task(db: Session, task: GenerationTask) -> VNGraphCompileInput:
    params = task.parameters or {}
    if task.project_id is None:
        raise VNGraphCompileError("VNGraph 任务缺少 project_id")
    manifest = params.get("binding_manifest")
    if not isinstance(manifest, dict):
        raise VNGraphCompileError("VNGraph 任务缺少 binding manifest")
    manifest_hash = content_hash(manifest)
    if (
        manifest_hash != params.get("binding_manifest_hash")
        or manifest_hash != (task.source_refs or {}).get("binding_manifest_hash")
    ):
        raise VNGraphCompileError("VNGraph 任务 binding manifest hash 不匹配")
    try:
        display_index = int(manifest["display_index"])
        chapter_revision = manifest["chapter_revision"]
        script_revision = manifest["script_revision"]
        chapter_revision_id = str(chapter_revision["id"])
        chapter_hash = str(chapter_revision["content_hash"])
        script_revision_id = str(script_revision["id"])
        script_hash = str(script_revision["script_hash"])
        versions = manifest["versions"]
        asset_bindings = manifest["asset_bindings"]
        voice_line_versions = manifest["voice_line_versions"]
    except (KeyError, TypeError, ValueError) as exc:
        raise VNGraphCompileError("VNGraph 任务快照不完整") from exc
    if (
        manifest.get("manifest_version") != VNGRAPH_BINDING_MANIFEST_VERSION
        or manifest.get("project_id") != task.project_id
        or not isinstance(versions, dict)
        or not isinstance(asset_bindings, list)
        or not isinstance(voice_line_versions, list)
    ):
        raise VNGraphCompileError("VNGraph 任务 binding manifest 项目不匹配")
    refs = task.source_refs or {}
    if (
        refs.get("chapter_revision_id") != chapter_revision_id
        or refs.get("chapter_content_hash") != chapter_hash
        or refs.get("script_revision_id") != script_revision_id
        or refs.get("script_hash") != script_hash
    ):
        raise VNGraphCompileError("VNGraph 任务 source refs 与 binding manifest 不匹配")

    try:
        script, chapter = _load_exact_script_source(
            db,
            script_revision_id=script_revision_id,
            expected_project_id=task.project_id,
        )
    except AppError as exc:
        raise VNGraphCompileError(exc.message) from exc
    if (
        chapter.id != chapter_revision_id
        or chapter.content_hash != chapter_hash
        or script.script_hash != script_hash
    ):
        raise VNGraphCompileError("VNGraph 任务引用的 Script revision 已损坏或不完整")
    parent_revision_id = params.get("parent_revision_id")
    if parent_revision_id:
        parent = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == str(parent_revision_id),
                VNGraphRevision.script_revision_id == script.id,
            )
            .one_or_none()
        )
        if not parent:
            raise VNGraphCompileError("VNGraph 任务引用的 parent revision 无效")
    _validate_immutable_resources(
        db,
        project_id=task.project_id,
        chapter_revision_id=chapter.id,
        asset_bindings=asset_bindings,
        voice_line_versions=voice_line_versions,
    )
    source = VNGraphCompileInput(
        project_id=task.project_id,
        display_index=display_index,
        chapter_revision_id=chapter.id,
        chapter_content_hash=chapter_hash,
        chapter_content=chapter.content,
        script_revision_id=script.id,
        script_hash=script.script_hash,
        script_ir=script.script_json,
        asset_bindings=asset_bindings,
        voice_line_versions=voice_line_versions,
        parent_revision_id=str(parent_revision_id) if parent_revision_id else None,
        schema_version=str(versions.get("schema") or ""),
        compiler_version=str(versions.get("compiler") or ""),
        tachi_policy_version=str(versions.get("tachi_policy") or ""),
    )
    if vn_graph_compiler.binding_manifest(source) != manifest:
        raise VNGraphCompileError("VNGraph 任务 binding manifest 无法重建")
    return source


def persist_vn_graph_result(
    db: Session,
    *,
    source: VNGraphCompileInput,
    result: VNGraphCompileResult,
    task_id: str | None,
) -> tuple[VNGraphRevision, bool]:
    expected_binding_manifest = vn_graph_compiler.binding_manifest(source)
    expected_binding_hash = content_hash(expected_binding_manifest)
    if (
        result.binding_manifest != expected_binding_manifest
        or result.binding_manifest_hash != expected_binding_hash
    ):
        raise VNGraphCompileError("VNGraph result 与 binding manifest 不匹配")
    if result.graph_hash != content_hash(result.graph):
        raise VNGraphCompileError("VNGraph result graph_hash 不匹配")

    script = (
        db.query(ChapterScriptRevision)
        .filter(ChapterScriptRevision.id == source.script_revision_id)
        .with_for_update()
        .one_or_none()
    )
    if (
        not script
        or script.project_id != source.project_id
        or script.chapter_revision_id != source.chapter_revision_id
        or script.script_hash != source.script_hash
    ):
        raise VNGraphCompileError("VNGraph source Script revision 已损坏")
    try:
        get_or_create_vn_graph_head(db, script.id, for_update=True)
    except AppError as exc:
        raise VNGraphCompileError(exc.message) from exc
    chapter = (
        db.query(ChapterRevision)
        .filter(ChapterRevision.id == source.chapter_revision_id)
        .one_or_none()
    )
    if (
        not chapter
        or chapter.project_id != source.project_id
        or chapter.content_hash != source.chapter_content_hash
        or content_hash(chapter.content) != chapter.content_hash
    ):
        raise VNGraphCompileError("VNGraph source Chapter revision 已损坏")
    if source.parent_revision_id:
        parent = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == source.parent_revision_id,
                VNGraphRevision.script_revision_id == script.id,
            )
            .one_or_none()
        )
        if not parent:
            raise VNGraphCompileError("VNGraph parent revision 不属于当前 Script")
    existing = (
        db.query(VNGraphRevision)
        .filter(
            VNGraphRevision.script_revision_id == script.id,
            VNGraphRevision.binding_manifest_hash == result.binding_manifest_hash,
            VNGraphRevision.compiler_version == source.compiler_version,
            VNGraphRevision.schema_version == source.schema_version,
            VNGraphRevision.graph_hash == result.graph_hash,
        )
        .order_by(VNGraphRevision.revision_no, VNGraphRevision.created_at)
        .first()
    )
    if existing:
        return existing, False

    revision_no = int(
        db.query(func.max(VNGraphRevision.revision_no))
        .filter(VNGraphRevision.script_revision_id == script.id)
        .scalar()
        or 0
    ) + 1
    revision = VNGraphRevision(
        project_id=source.project_id,
        chapter_index=source.display_index,
        chapter_revision_id=source.chapter_revision_id,
        script_revision_id=source.script_revision_id,
        parent_revision_id=source.parent_revision_id,
        revision_no=revision_no,
        binding_manifest=result.binding_manifest,
        binding_manifest_hash=result.binding_manifest_hash,
        source_manifest_hash=result.binding_manifest_hash,
        graph_hash=result.graph_hash,
        graph_json=result.graph,
        schema_version=source.schema_version,
        compiler_version=source.compiler_version,
        tachi_policy_version=source.tachi_policy_version,
        status="ready",
        generation_task_id=task_id,
    )
    db.add(revision)
    db.flush()
    return revision, True


def create_manual_vn_graph_revision(
    db: Session,
    *,
    script_revision_id: str,
    graph_json: dict[str, Any],
    parent_revision_id: str | None = None,
) -> tuple[VNGraphRevision, bool]:
    """Materialize a client-side graph draft as an immutable, non-activated revision."""
    from copy import deepcopy

    from app.utils.vn_graph_validator import vn_graph_validator as _graph_validator

    if not isinstance(graph_json, dict):
        raise AppError(
            code="vngraph.invalid_json",
            message="graph_json 必须是 JSON 对象",
            status_code=422,
        )
    validation = _graph_validator.validate_detailed(graph_json)
    if validation.errors:
        raise AppError(
            code="vngraph.validation_failed",
            message="VN graph 校验失败：" + "；".join(validation.errors[:5]),
            status_code=422,
        )
    # Canonical versions throughout: persist_vn_graph_result re-derives the
    # binding manifest through validate_versions, which only accepts the exact
    # compiler/schema/tachi versions. Manual provenance is carried by
    # generation_task_id IS NULL; content-identical manual and compiled graphs
    # dedup to the same revision.
    source = load_compile_input(db, script_revision_id=script_revision_id)
    if parent_revision_id is not None:
        parent = (
            db.query(VNGraphRevision)
            .filter(
                VNGraphRevision.id == parent_revision_id,
                VNGraphRevision.script_revision_id == script_revision_id,
            )
            .one_or_none()
        )
        if not parent:
            raise AppError(
                code="vngraph.parent_not_found",
                message="父图修订不存在或不属于该脚本",
                status_code=404,
            )
        source = replace(source, parent_revision_id=parent_revision_id)
    manifest = vn_graph_compiler.binding_manifest(source)
    binding_hash = content_hash(manifest)
    graph_hash = content_hash(graph_json)
    # uq_vngraph_binding_compile：同一脚本 + 绑定 + 编译版本只允许一条图修订。
    # 内容一致的物化幂等复用；内容不同则明确拒绝（改图走 VNGraph patch），避免 500。
    bound = (
        db.query(VNGraphRevision)
        .filter(
            VNGraphRevision.script_revision_id == script_revision_id,
            VNGraphRevision.binding_manifest_hash == binding_hash,
            VNGraphRevision.compiler_version == source.compiler_version,
            VNGraphRevision.schema_version == source.schema_version,
        )
        .order_by(VNGraphRevision.revision_no, VNGraphRevision.created_at)
        .first()
    )
    if bound is not None:
        # JS 往返会把整值浮点（80.0→80）归一化，客户端草稿 hash 会漂移；
        # 把存量图按同一规则归一化后再比，两侧任一相等即视为同内容。
        if graph_hash in {bound.graph_hash, content_hash(js_normalized_json(bound.graph_json))}:
            return bound, False
        raise AppError(
            code="vngraph.binding_conflict",
            message="该脚本的 VNGraph 已被编译绑定，且草稿内容与已绑定图不一致；修改图请走 VNGraph patch",
            status_code=409,
        )
    result = VNGraphCompileResult(
        graph=deepcopy(graph_json),
        graph_hash=graph_hash,
        binding_manifest=manifest,
        binding_manifest_hash=binding_hash,
    )
    return persist_vn_graph_result(db, source=source, result=result, task_id=None)


def execute_vn_graph_compile_task(
    db: Session,
    *,
    task_id: str,
    worker_id: str,
) -> tuple[GenerationTask | None, VNGraphRevision | None, bool]:
    task = db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
    if not task:
        return None, None, False
    if task.status == "succeeded":
        revision_id = (task.result_refs or {}).get("vngraph_revision_id")
        revision = db.query(VNGraphRevision).filter(VNGraphRevision.id == revision_id).first()
        return task, revision, False

    claimed = claim_task(db, task_id, worker_id)
    if not claimed:
        return task, None, False
    lease = lease_from_task(claimed)
    if claimed.kind != VNGRAPH_COMPILE_TASK_KIND:
        fail_task(
            db,
            claimed,
            error_code="invalid_task_kind",
            safe_detail="任务不是 VNGraph 编译任务",
            retryable=False,
            lease=lease,
        )
        return claimed, None, False
    try:
        heartbeat_task(db, claimed, lease, stage="loading_snapshot", progress=10)
        source = _input_from_task(db, claimed)
        heartbeat_task(db, claimed, lease, stage="compiling", progress=45)
        result = vn_graph_compiler.compile(source)
        heartbeat_task(db, claimed, lease, stage="persisting", progress=85)
        revision, created = persist_vn_graph_result(
            db,
            source=source,
            result=result,
            task_id=claimed.id,
        )
        complete_task(
            db,
            claimed,
            result_refs={
                "vngraph_revision_id": revision.id,
                "graph_hash": revision.graph_hash,
                "binding_manifest_hash": revision.binding_manifest_hash,
                "created": created,
            },
            actual_cost=Decimal("0"),
            lease=lease,
        )
        return claimed, revision, created
    except VNGraphCompileError as exc:
        fail_task(
            db,
            claimed,
            error_code="invalid_compile_snapshot",
            safe_detail=str(exc),
            retryable=False,
            lease=lease,
        )
        return claimed, None, False
    except Exception:
        fail_task(
            db,
            claimed,
            error_code="vngraph_compile_failed",
            safe_detail="VNGraph 编译失败",
            retryable=True,
            lease=lease,
        )
        return claimed, None, False
