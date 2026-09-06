from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException
from sqlalchemy import exists, func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.application.public_release_service import (
    active_release_query,
    release_manifest_is_intact,
)
from app.application.task_service import (
    TERMINAL_TASK_STATUSES,
    append_task_event,
    complete_task,
    create_generation_task,
)
from app.models import Asset, Project
from app.models_v2 import (
    AssetBinding,
    AssetPlan,
    AssetPlanItem,
    AssetVersion,
    BranchCandidate,
    ChapterRevision,
    ChapterScriptRevision,
    GenerationTask,
    GenerationTaskDependency,
    OutlineRevision,
    ProjectRelease,
    ReadingContinuation,
    ReadingSession,
    ScriptResourceSlot,
    StorageObject,
    StoryBibleRevision,
    VNGraphRevision,
    utcnow,
)


ASSET_TYPES = {"portrait", "background", "keyframe", "audio"}
SOURCE_MODELS = {
    "story_bible_revision": (StoryBibleRevision, "content_hash"),
    "outline_revision": (OutlineRevision, "content_hash"),
    "chapter_revision": (ChapterRevision, "content_hash"),
    "chapter_script_revision": (ChapterScriptRevision, "script_hash"),
    "vn_graph_revision": (VNGraphRevision, "graph_hash"),
}
ASSET_RENDER_CREDITS = {
    "portrait": Decimal("15"),
    "background": Decimal("15"),
    "keyframe": Decimal("15"),
    "audio": Decimal("5"),
}
V2_NAMESPACE = "v2"
CACHE_SCHEMA_VERSION = "asset_cache_v1"
_ASSET_BATCH_DEPENDENCY_ERROR = "asset.batch_dependency_inconsistent"


def _v2_meta(asset: Asset) -> dict[str, Any]:
    params = asset.generation_params if isinstance(asset.generation_params, dict) else {}
    meta = params.get(V2_NAMESPACE)
    return dict(meta) if isinstance(meta, dict) else {}


def validate_source_revision(
    db: Session,
    *,
    project_id: int,
    source_kind: str,
    source_revision_id: str,
) -> tuple[Any, str]:
    if source_kind == "branch_candidate":
        candidate = (
            db.query(BranchCandidate)
            .filter(
                BranchCandidate.id == source_revision_id,
                BranchCandidate.project_id == project_id,
            )
            .first()
        )
        if not candidate:
            raise HTTPException(status_code=404, detail="来源分支候选不存在")
        return candidate, content_hash(
            {
                "preview_revision_id": candidate.preview_revision_id,
                "state_delta": candidate.state_delta,
                "status": candidate.candidate_status,
            }
        )
    if source_kind == "reading_continuation":
        continuation = (
            db.query(ReadingContinuation)
            .join(ReadingSession, ReadingSession.id == ReadingContinuation.session_id)
            .filter(
                ReadingContinuation.id == source_revision_id,
                ReadingSession.project_id == project_id,
            )
            .first()
        )
        if not continuation:
            raise HTTPException(status_code=404, detail="阅读续写不存在")
        return continuation, content_hash(
            {
                "direction": continuation.direction,
                "text": continuation.continuation_text,
                "state_delta": continuation.state_delta,
                "scene_manifest_id": continuation.scene_manifest_id,
            }
        )
    model_info = SOURCE_MODELS.get(source_kind)
    if not model_info:
        raise HTTPException(status_code=422, detail="不支持的 source_kind")
    model, hash_field = model_info
    revision = (
        db.query(model)
        .filter(model.id == source_revision_id, model.project_id == project_id)
        .first()
    )
    if not revision:
        # Scope by project to avoid accepting or leaking another project's ID.
        raise HTTPException(status_code=404, detail="来源 revision 不存在")
    return revision, str(getattr(revision, hash_field))


def _normalize_plan_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_slot: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in items:
        item = dict(raw)
        asset_type = str(item.get("asset_type") or "").strip()
        logical_key = str(item.get("logical_key") or "").strip()
        if asset_type not in ASSET_TYPES or not logical_key:
            raise HTTPException(status_code=422, detail="素材类型或 logical_key 无效")
        render_spec = dict(item.get("render_spec") or {})
        normalized = {
            "asset_type": asset_type,
            "logical_key": logical_key,
            "target_name": str(item.get("target_name") or "").strip(),
            "chapter_index": item.get("chapter_index"),
            "taxonomy": dict(item.get("taxonomy") or {}),
            "render_spec": render_spec,
        }
        slot = (asset_type, logical_key)
        previous = by_slot.get(slot)
        # 同一 logical_key 即同一可复用素材（如同一角色+情绪在多个段落出现）。
        # taxonomy 带段落级 provenance（scene_id/paragraph_id），随每次出现而变，
        # 只有 render_spec（真正决定画面）不同才算冲突。
        if previous and content_hash(previous["render_spec"]) != content_hash(render_spec):
            raise HTTPException(status_code=422, detail=f"同一 logical_key 存在冲突定义: {logical_key}")
        by_slot[slot] = normalized
    return [by_slot[key] for key in sorted(by_slot)]


def _apply_taxonomy_columns(asset: Asset, taxonomy: dict[str, Any]) -> None:
    asset.genre = taxonomy.get("genre")
    asset.description_cn = taxonomy.get("description_cn")
    if asset.asset_type == "portrait":
        asset.character_id = taxonomy.get("character_id")
        asset.emotion = taxonomy.get("emotion")
        asset.outfit = taxonomy.get("outfit")
        asset.pose = taxonomy.get("pose")
    elif asset.asset_type == "background":
        asset.scene_location = taxonomy.get("scene_location") or asset.target_name
        asset.mood = taxonomy.get("mood")
    elif asset.asset_type == "keyframe":
        asset.event_name = taxonomy.get("event_name") or asset.target_name


def create_asset_plan(
    db: Session,
    *,
    project_id: int,
    source_kind: str,
    source_revision_id: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    _, source_hash = validate_source_revision(
        db,
        project_id=project_id,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
    )
    normalized_items = _normalize_plan_items(items)
    plan_key = content_hash(
        {
            "schema": "asset_plan_v1",
            "project_id": project_id,
            "source_kind": source_kind,
            "source_revision_id": source_revision_id,
            "source_hash": source_hash,
            "items": normalized_items,
        }
    )

    # Serializes same-project logical-key creation on PostgreSQL.  Application
    # idempotency below remains the fallback for SQLite test/development mode.
    project = db.query(Project).filter(Project.id == project_id).with_for_update().first()
    if not project:
        raise HTTPException(status_code=404, detail="项目不存在")
    existing_plan = db.query(AssetPlan).filter(AssetPlan.plan_key == plan_key).first()
    existing_assets = db.query(Asset).filter(Asset.project_id == project_id).all()
    by_logical_key = {
        (asset.asset_type, asset.logical_key or _v2_meta(asset).get("logical_key")): asset
        for asset in existing_assets
        if asset.asset_type in ASSET_TYPES and (asset.logical_key or _v2_meta(asset).get("logical_key"))
    }

    if not existing_plan:
        existing_plan = AssetPlan(
            plan_key=plan_key,
            project_id=project_id,
            source_kind=source_kind,
            source_revision_id=source_revision_id,
            source_hash=source_hash,
        )
        db.add(existing_plan)
        db.flush()

    created_count = 0
    planned_assets: list[Asset] = []
    for item in normalized_items:
        slot = (item["asset_type"], item["logical_key"])
        asset = by_logical_key.get(slot)
        if not asset:
            asset = Asset(
                project_id=project_id,
                chapter_index=item["chapter_index"],
                asset_type=item["asset_type"],
                target_name=item["target_name"],
                prompt=item["render_spec"]["prompt"],
                status="pending",
                seed=item["render_spec"].get("seed"),
                generation_params={},
                logical_key=item["logical_key"],
                taxonomy_json=item["taxonomy"],
            )
            db.add(asset)
            db.flush()
            by_logical_key[slot] = asset
            created_count += 1

        plan_item = (
            db.query(AssetPlanItem)
            .filter(AssetPlanItem.plan_key == plan_key, AssetPlanItem.asset_id == asset.id)
            .first()
        )
        if not plan_item:
            asset_spec = {
                "asset_type": item["asset_type"],
                "logical_key": item["logical_key"],
                "target_name": item["target_name"],
                "taxonomy": item["taxonomy"],
                "chapter_index": item["chapter_index"],
            }
            db.add(
                AssetPlanItem(
                    plan_key=plan_key,
                    asset_id=asset.id,
                    asset_type=item["asset_type"],
                    logical_key=item["logical_key"],
                    asset_spec_json=asset_spec,
                    render_spec_json=item["render_spec"],
                    spec_hash=content_hash(item),
                )
            )
        planned_assets.append(asset)

    db.flush()
    return {
        "plan_key": plan_key,
        "source_kind": source_kind,
        "source_revision_id": source_revision_id,
        "created_count": created_count,
        "reused_count": len(planned_assets) - created_count,
        "assets": planned_assets,
    }


def _asset_version_read_dict(
    version: AssetVersion,
    storage: StorageObject | None,
    binding_count: int = 0,
) -> dict[str, Any]:
    return {
        "id": version.id,
        "asset_id": version.asset_id,
        "source_kind": version.source_kind,
        "source_revision_id": version.source_revision_id,
        "source_hash": version.source_hash,
        "generation_task_id": version.generation_task_id,
        "version_no": version.version_no,
        "cache_key": version.cache_key,
        "provider": version.provider,
        "model": version.model,
        "prompt_hash": version.prompt_hash,
        "prompt_version": version.prompt_version,
        "negative_prompt_hash": version.negative_prompt_hash,
        "seed": version.seed,
        "width": version.width,
        "height": version.height,
        "postprocess_version": version.postprocess_version,
        "validator_version": version.validator_version,
        "quality_score": version.quality_score,
        "safety_status": version.safety_status,
        "rights_metadata": dict(version.rights_metadata or {}),
        "asset_spec": dict(version.asset_spec_json or {}),
        "render_spec": dict(version.render_spec_json or {}),
        "render_spec_hash": version.render_spec_hash,
        "media_url": f"/api/media/{storage.id}" if storage and storage.status == "active" else None,
        "media_type": storage.media_type if storage else None,
        "storage_status": storage.status if storage else None,
        "binding_count": binding_count,
        "is_in_use": binding_count > 0,
        "created_at": version.created_at,
    }


def _assemble_asset_reads(db: Session, assets: list[Asset]) -> list[dict[str, Any]]:
    if not assets:
        return []
    asset_ids = [asset.id for asset in assets]
    version_rows = (
        db.query(AssetVersion, StorageObject)
        .outerjoin(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(AssetVersion.asset_id.in_(asset_ids))
        .order_by(AssetVersion.asset_id, AssetVersion.version_no.desc())
        .all()
    )
    version_ids = [version.id for version, _storage in version_rows]
    binding_counts = dict(
        db.query(AssetBinding.asset_version_id, func.count(AssetBinding.id))
        .filter(
            AssetBinding.asset_version_id.in_(version_ids or ["-"]),
            AssetBinding.deleted_at.is_(None),
        )
        .group_by(AssetBinding.asset_version_id)
        .all()
    )
    chapter_rows = (
        db.query(ScriptResourceSlot.asset_id, ChapterScriptRevision.chapter_index)
        .join(
            ChapterScriptRevision,
            ChapterScriptRevision.id == ScriptResourceSlot.chapter_script_revision_id,
        )
        .filter(ScriptResourceSlot.asset_id.in_(asset_ids))
        .distinct()
        .all()
    )
    versions_by_asset: dict[int, list[dict[str, Any]]] = {asset_id: [] for asset_id in asset_ids}
    for version, storage in version_rows:
        versions_by_asset[version.asset_id].append(
            _asset_version_read_dict(version, storage, int(binding_counts.get(version.id, 0)))
        )
    chapters_by_asset: dict[int, set[int]] = {asset_id: set() for asset_id in asset_ids}
    for asset_id, chapter_index in chapter_rows:
        chapters_by_asset[int(asset_id)].add(int(chapter_index))
    result = []
    for asset in assets:
        versions = versions_by_asset[asset.id]
        result.append(
            {
                "id": asset.id,
                "project_id": asset.project_id,
                "asset_type": asset.asset_type,
                "target_name": asset.target_name or "",
                "status": "archived" if asset.archived_at else (asset.status or "pending"),
                "logical_key": str(asset.logical_key or f"legacy:{asset.id}"),
                "taxonomy": dict(asset.taxonomy_json or {}),
                "chapter_indices": sorted(chapters_by_asset[asset.id]),
                "version_count": len(versions),
                "latest_version_id": versions[0]["id"] if versions else None,
                "versions": versions,
                "archived_at": asset.archived_at,
                "created_at": asset.created_at,
                "updated_at": asset.updated_at,
            }
        )
    return result


def asset_read_dict(db: Session, asset: Asset) -> dict[str, Any]:
    return _assemble_asset_reads(db, [asset])[0]


def list_logical_assets(
    db: Session,
    *,
    project_id: int,
    asset_type: str | None,
    status: str | None,
    logical_key: str | None,
    chapter_index: int | None,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    query = db.query(Asset).filter(
        Asset.project_id == project_id,
        Asset.asset_type.in_(sorted(ASSET_TYPES)),
        Asset.archived_at.is_(None),
    )
    if asset_type:
        if asset_type not in ASSET_TYPES:
            raise HTTPException(status_code=422, detail="不支持的素材类型")
        query = query.filter(Asset.asset_type == asset_type)
    if status:
        query = query.filter(Asset.status == status)
    if chapter_index is not None:
        query = query.filter(
            exists().where(
                ScriptResourceSlot.asset_id == Asset.id,
                ScriptResourceSlot.chapter_script_revision_id == ChapterScriptRevision.id,
                ChapterScriptRevision.chapter_index == chapter_index,
            )
        )
    if logical_key:
        query = query.filter(Asset.logical_key == logical_key)
    page_rows = (
        query.add_columns(func.count(Asset.id).over().label("logical_asset_total"))
        .order_by(Asset.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    assets = [asset for asset, _total in page_rows]
    total = int(page_rows[0][1]) if page_rows else int(query.order_by(None).count())
    return _assemble_asset_reads(db, assets), total


def get_project_asset(db: Session, project_id: int, asset_id: int, *, lock: bool = False) -> Asset:
    query = db.query(Asset).filter(
        Asset.id == asset_id,
        Asset.project_id == project_id,
        Asset.asset_type.in_(sorted(ASSET_TYPES)),
    )
    asset = query.with_for_update().first() if lock else query.first()
    if not asset:
        raise HTTPException(status_code=404, detail="素材不存在")
    return asset


def get_project_asset_version(db: Session, project_id: int, asset_version_id: str) -> dict[str, Any]:
    # Intentionally no archived_at filter: slots keep referencing versions
    # (ondelete=RESTRICT) after the parent asset is archived, and clients must
    # still be able to resolve media_url from asset_version_id alone.
    row = (
        db.query(AssetVersion, StorageObject)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .outerjoin(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(
            AssetVersion.id == asset_version_id,
            Asset.project_id == project_id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="素材版本不存在")
    version, storage = row
    binding_count = int(
        db.query(func.count(AssetBinding.id))
        .filter(
            AssetBinding.asset_version_id == version.id,
            AssetBinding.deleted_at.is_(None),
        )
        .scalar()
        or 0
    )
    return _asset_version_read_dict(version, storage, binding_count)


def delete_project_asset(db: Session, project_id: int, asset_id: int) -> Asset:
    asset = get_project_asset(db, project_id, asset_id, lock=True)
    asset.archived_at = asset.archived_at or utcnow()
    return asset


def build_asset_cache_material(
    *,
    asset: Asset,
    source_kind: str,
    source_revision_id: str,
    source_hash: str,
    render_spec: dict[str, Any],
    asset_spec_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    asset_spec = dict(asset_spec_override or {})
    negative_prompt = str(render_spec.get("negative_prompt") or "")
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "normalized_asset_spec": {
            "asset_type": asset_spec.get("asset_type") or asset.asset_type,
            "logical_key": asset_spec.get("logical_key") or asset.logical_key or f"legacy:{asset.id}",
            "target_name": asset_spec.get("target_name") or asset.target_name,
            "taxonomy": dict(asset_spec.get("taxonomy") or asset.taxonomy_json or {}),
            "prompt_hash": content_hash(str(render_spec.get("prompt") or "")),
            "extra_parameters": dict(render_spec.get("extra_parameters") or {}),
        },
        "identity_version": render_spec.get("identity_version"),
        "style_pack_version": render_spec.get("style_pack_version"),
        "provider": render_spec.get("provider"),
        "model": render_spec.get("model"),
        "prompt_template_version": render_spec.get("prompt_template_version"),
        "seed": render_spec.get("seed"),
        "dimensions": [render_spec.get("width"), render_spec.get("height")],
        "negative_prompt_hash": content_hash(negative_prompt),
        "negative_prompt_version": render_spec.get("negative_prompt_version"),
        "postprocess_version": render_spec.get("postprocess_version"),
        "validator_version": render_spec.get("validator_version"),
    }


def request_asset_variant(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    asset_id: int,
    source_kind: str,
    source_revision_id: str,
    render_spec: dict[str, Any],
    parent_task_id: str | None = None,
    asset_spec_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    asset = get_project_asset(db, project_id, asset_id, lock=True)
    _, source_hash = validate_source_revision(
        db,
        project_id=project_id,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
    )
    cache_material = build_asset_cache_material(
        asset=asset,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
        source_hash=source_hash,
        render_spec=render_spec,
        asset_spec_override=asset_spec_override,
    )
    cache_key = content_hash(cache_material)
    version = (
        db.query(AssetVersion)
        .filter(AssetVersion.asset_id == asset.id, AssetVersion.cache_key == cache_key)
        .first()
    )
    if version:
        return {"cache_key": cache_key, "cached": True, "version": version, "task": None, "created": False}

    task, created = create_generation_task(
        db,
        user_id=user_id,
        project_id=project_id,
        kind="asset.render",
        idempotency_key=f"asset-render:{project_id}:{asset.id}:{cache_key}",
        source_refs={
            "asset_id": asset.id,
            "source_kind": source_kind,
            "source_revision_id": source_revision_id,
            "source_hash": source_hash,
        },
        parameters={
            "asset_id": asset.id,
            "asset_type": asset.asset_type,
            "logical_key": asset.logical_key,
            "cache_key": cache_key,
            "cache_material": cache_material,
            "render_spec": render_spec,
        },
        estimated_cost=ASSET_RENDER_CREDITS[asset.asset_type],
        enqueue_event_type="task.asset.render.queued",
    )
    if parent_task_id:
        dependency = (
            db.query(GenerationTaskDependency)
            .filter(
                GenerationTaskDependency.task_id == parent_task_id,
                GenerationTaskDependency.depends_on_task_id == task.id,
            )
            .first()
        )
        if not dependency:
            db.add(
                GenerationTaskDependency(
                    task_id=parent_task_id,
                    depends_on_task_id=task.id,
                    required_status="succeeded",
                )
            )
    return {"cache_key": cache_key, "cached": False, "version": None, "task": task, "created": created}


def request_asset_plan_render(
    db: Session,
    *,
    user_id: int,
    project_id: int,
    plan_key: str,
    role: str,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if role not in {"portrait", "background", "keyframe"}:
        raise HTTPException(status_code=422, detail="不支持的资源角色")
    db.query(Project).filter(Project.id == project_id).with_for_update().one()
    plan = (
        db.query(AssetPlan)
        .filter(AssetPlan.plan_key == plan_key, AssetPlan.project_id == project_id)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="素材计划不存在")
    item_query = db.query(AssetPlanItem).filter(AssetPlanItem.plan_key == plan_key)
    item_query = item_query.filter(AssetPlanItem.asset_type == role)
    plan_items = item_query.order_by(AssetPlanItem.asset_id).all()
    assets_by_id = {
        asset.id: asset
        for asset in db.query(Asset)
        .filter(
            Asset.project_id == project_id,
            Asset.id.in_([item.asset_id for item in plan_items] or [-1]),
        )
        .all()
    }
    items = [(item, assets_by_id[item.asset_id]) for item in plan_items if item.asset_id in assets_by_id]

    parent, parent_created = create_generation_task(
        db,
        user_id=user_id,
        project_id=project_id,
        kind="asset.render.batch",
        idempotency_key=(
            idempotency_key
            or f"asset-render-batch:{project_id}:{plan_key}:{role}"
        ),
        source_refs={
            "plan_key": plan_key,
            "source_kind": plan.source_kind,
            "source_revision_id": plan.source_revision_id,
            "source_hash": plan.source_hash,
            "asset_ids": [asset.id for _item, asset in items],
        },
        parameters={"plan_key": plan_key, "role": role},
        estimated_cost=0,
        enqueue=False,
    )
    if parent_created:
        parent.root_task_id = parent.id
        parent.status = "running"
        parent.stage = "fanout"
        parent.started_at = utcnow()
        append_task_event(
            db,
            parent,
            "fanout.started",
            {"asset_count": len(items), "role": role},
        )

    task_ids: list[str] = []
    cached_version_ids: list[str] = []
    created_child_count = 0
    for plan_item, asset in items:
        variant = request_asset_variant(
            db,
            user_id=user_id,
            project_id=project_id,
            asset_id=asset.id,
            source_kind=plan.source_kind,
            source_revision_id=plan.source_revision_id,
            render_spec=dict(plan_item.render_spec_json or {}),
            parent_task_id=parent.id,
            asset_spec_override=dict(plan_item.asset_spec_json or {}),
        )
        if variant["version"]:
            cached_version_ids.append(variant["version"].id)
        elif variant["task"]:
            task_ids.append(variant["task"].id)
            created_child_count += int(variant["created"])

    # 合并写入而非整体覆盖：资源全部 bound 后重放（全缓存命中）时
    # child_task_ids 为空，若覆盖会与既有 GenerationTaskDependency 行错配，
    # refresh_asset_batch 把已 succeeded 的父任务打回 dependency_inconsistent。
    previous_refs = dict(parent.result_refs or {})
    merged_refs = {
        "plan_key": plan_key,
        "role": role,
        "selected_slot_count": len(items),
        "child_task_ids": sorted(
            set(task_ids) | set(previous_refs.get("child_task_ids") or [])
        ),
        "cached_version_ids": sorted(
            set(cached_version_ids) | set(previous_refs.get("cached_version_ids") or [])
        ),
        "succeeded_child_task_ids": sorted(
            set(previous_refs.get("succeeded_child_task_ids") or [])
        ),
        "failed_child_task_ids": sorted(
            set(previous_refs.get("failed_child_task_ids") or [])
        ),
        "bound_slot_ids": sorted(set(previous_refs.get("bound_slot_ids") or [])),
        "created_child_count": int(previous_refs.get("created_child_count") or 0)
        + created_child_count,
    }
    parent.result_refs = merged_refs
    # SessionLocal disables autoflush. Persist fan-out dependencies before the
    # aggregate query decides whether this parent has any children.
    db.flush()
    refresh_asset_batch(db, parent)
    return {
        "plan_key": plan_key,
        "parent": parent,
        "child_task_ids": sorted(set(task_ids)),
        "created_child_count": created_child_count,
        "parent_created": parent_created,
    }


def _mark_asset_batch_dependency_error(
    db: Session,
    parent: GenerationTask,
    *,
    detail: str,
) -> None:
    changed = (
        parent.error_code != _ASSET_BATCH_DEPENDENCY_ERROR
        or parent.error_detail != detail
    )
    parent.status = "running"
    parent.stage = "dependency_inconsistent"
    parent.progress = 0.0
    parent.finished_at = None
    parent.error_code = _ASSET_BATCH_DEPENDENCY_ERROR
    parent.error_detail = detail
    if changed:
        append_task_event(
            db,
            parent,
            "fanout.inconsistent",
            {"error_code": _ASSET_BATCH_DEPENDENCY_ERROR, "detail": detail},
        )


def _clear_asset_batch_dependency_error(parent: GenerationTask) -> None:
    if parent.error_code == _ASSET_BATCH_DEPENDENCY_ERROR:
        parent.error_code = None
        parent.error_detail = None


def refresh_asset_batch(db: Session, parent: GenerationTask) -> None:
    if parent.status in TERMINAL_TASK_STATUSES and parent.status != "succeeded":
        return
    result_refs = dict(parent.result_refs or {})
    selected_slot_count = int(result_refs.get("selected_slot_count") or 0)
    expected_child_ids = {
        str(task_id)
        for task_id in (result_refs.get("child_task_ids") or [])
        if task_id
    }
    cached_version_ids = {
        str(version_id)
        for version_id in (result_refs.get("cached_version_ids") or [])
        if version_id
    }
    dependencies = (
        db.query(GenerationTaskDependency)
        .filter(GenerationTaskDependency.task_id == parent.id)
        .all()
    )
    dependency_ids = {item.depends_on_task_id for item in dependencies}
    invalid_required_status_ids = sorted(
        item.depends_on_task_id
        for item in dependencies
        if item.required_status != "succeeded"
    )
    if dependency_ids != expected_child_ids or invalid_required_status_ids:
        missing = sorted(expected_child_ids - dependency_ids)
        unexpected = sorted(dependency_ids - expected_child_ids)
        _mark_asset_batch_dependency_error(
            db,
            parent,
            detail=(
                "Asset batch dependency mismatch: "
                f"missing={missing}, unexpected={unexpected}, "
                f"invalid_required_status={invalid_required_status_ids}"
            ),
        )
        return
    if not dependencies:
        fully_cached = (
            selected_slot_count > 0
            and not expected_child_ids
            and len(cached_version_ids) == selected_slot_count
        )
        if selected_slot_count != 0 and not fully_cached:
            _mark_asset_batch_dependency_error(
                db,
                parent,
                detail=(
                    "Asset batch selected slots without durable dependencies "
                    "or a complete cache result"
                ),
            )
            return
        _clear_asset_batch_dependency_error(parent)
        if parent.status not in TERMINAL_TASK_STATUSES:
            complete_task(db, parent, result_refs=result_refs, actual_cost=0)
        return
    children = (
        db.query(GenerationTask)
        .filter(GenerationTask.id.in_(dependency_ids))
        .all()
    )
    child_ids = {child.id for child in children}
    if child_ids != dependency_ids:
        _mark_asset_batch_dependency_error(
            db,
            parent,
            detail=(
                "Asset batch dependency targets are missing: "
                f"{sorted(dependency_ids - child_ids)}"
            ),
        )
        return
    _clear_asset_batch_dependency_error(parent)
    if any(child.status not in TERMINAL_TASK_STATUSES for child in children):
        parent.status = "running"
        parent.stage = "children_running"
        parent.finished_at = None
        terminal_count = sum(
            child.status in TERMINAL_TASK_STATUSES for child in children
        )
        parent.progress = 100.0 * terminal_count / len(dependencies)
        return
    failures = [child.id for child in children if child.status != "succeeded"]
    successes = [child.id for child in children if child.status == "succeeded"]
    result_refs["failed_child_task_ids"] = failures
    result_refs["succeeded_child_task_ids"] = successes
    result_refs["bound_slot_ids"] = sorted(
        {
            slot_id
            for child in children
            for slot_id in ((child.result_refs or {}).get("script_resource_slot_ids") or [])
        }
    )
    result_refs["aggregate_child_cost"] = str(
        sum((Decimal(child.actual_cost or 0) for child in children), Decimal("0"))
    )
    if parent.status == "succeeded" and not failures:
        # Older deployments could complete the parent before autoflush made
        # dependencies visible. Preserve the valid terminal status but repair
        # its aggregate result once every child is known to have succeeded.
        parent.result_refs = result_refs
        return
    complete_task(
        db,
        parent,
        result_refs=result_refs,
        # Child tasks settle their own usage. Charging their sum again on the
        # zero-cost orchestration parent would double bill the user.
        actual_cost=0,
        partial=bool(failures),
    )


def record_asset_version(
    db: Session,
    *,
    project_id: int,
    asset_id: int,
    source_kind: str,
    source_revision_id: str,
    generation_task_id: str | None,
    storage_object_id: str,
    cache_key: str,
    render_spec: dict[str, Any],
    cache_material: dict[str, Any] | None = None,
    quality_score: float | None = None,
    safety_status: str = "passed",
    rights_metadata: dict[str, Any] | None = None,
    storage_owner_id: int | None = None,
) -> tuple[AssetVersion, bool]:
    asset = get_project_asset(db, project_id, asset_id, lock=True)
    _, source_hash = validate_source_revision(
        db,
        project_id=project_id,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
    )
    storage_query = db.query(StorageObject).filter(
        StorageObject.id == storage_object_id,
        StorageObject.status == "active",
        StorageObject.deleted_at.is_(None),
    )
    if source_kind == "reading_continuation":
        if storage_owner_id is None:
            raise HTTPException(status_code=409, detail="阅读续写素材缺少会话所有者")
        owned_continuation = (
            db.query(ReadingContinuation)
            .join(ReadingSession, ReadingSession.id == ReadingContinuation.session_id)
            .filter(
                ReadingContinuation.id == source_revision_id,
                ReadingSession.project_id == project_id,
                ReadingSession.user_id == storage_owner_id,
            )
            .first()
        )
        if not owned_continuation:
            raise HTTPException(status_code=404, detail="阅读续写不存在")
        storage_query = storage_query.filter(
            StorageObject.owner_id == storage_owner_id,
            StorageObject.project_id.is_(None),
        )
    else:
        storage_query = storage_query.filter(StorageObject.project_id == project_id)
    storage = storage_query.first()
    if not storage:
        raise HTTPException(status_code=409, detail="物理媒体对象不可用")
    material = cache_material or build_asset_cache_material(
        asset=asset,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
        source_hash=source_hash,
        render_spec=render_spec,
    )
    normalized_spec = material.get("normalized_asset_spec") if isinstance(material, dict) else None
    if not isinstance(normalized_spec, dict):
        raise HTTPException(status_code=409, detail="素材 cache material 无效")
    expected_cache_key = content_hash(material)
    if cache_key != expected_cache_key:
        raise HTTPException(status_code=409, detail="素材版本 cache_key 与生成规格不一致")
    existing = (
        db.query(AssetVersion)
        .filter(AssetVersion.asset_id == asset.id, AssetVersion.cache_key == cache_key)
        .first()
    )
    if existing:
        return existing, False
    version_no = int(
        db.query(func.max(AssetVersion.version_no)).filter(AssetVersion.asset_id == asset.id).scalar() or 0
    ) + 1
    prompt = str(render_spec.get("prompt") or "")
    negative_prompt = str(render_spec.get("negative_prompt") or "")
    version = AssetVersion(
        asset_id=asset.id,
        source_kind=source_kind,
        source_revision_id=source_revision_id,
        source_hash=source_hash,
        generation_task_id=generation_task_id,
        storage_object_id=storage.id,
        version_no=version_no,
        cache_key=cache_key,
        provider=render_spec.get("provider"),
        model=render_spec.get("model"),
        prompt=prompt,
        prompt_hash=content_hash(prompt),
        prompt_version=render_spec.get("prompt_template_version"),
        negative_prompt_hash=content_hash(negative_prompt),
        seed=render_spec.get("seed"),
        width=render_spec.get("width"),
        height=render_spec.get("height"),
        postprocess_version=render_spec.get("postprocess_version"),
        validator_version=render_spec.get("validator_version"),
        quality_score=quality_score,
        safety_status=safety_status,
        rights_metadata=dict(rights_metadata or {}),
        asset_spec_json=dict(normalized_spec),
        render_spec_json=dict(render_spec),
        render_spec_hash=content_hash(render_spec),
    )
    db.add(version)
    db.flush()
    asset.status = "completed"
    return version, True


def list_asset_versions(db: Session, project_id: int, asset_id: int) -> list[dict[str, Any]]:
    asset = get_project_asset(db, project_id, asset_id)
    rows = (
        db.query(AssetVersion, StorageObject)
        .outerjoin(StorageObject, StorageObject.id == AssetVersion.storage_object_id)
        .filter(AssetVersion.asset_id == asset.id)
        .order_by(AssetVersion.version_no.desc())
        .all()
    )
    version_ids = [version.id for version, _storage in rows]
    binding_counts = dict(
        db.query(AssetBinding.asset_version_id, func.count(AssetBinding.id))
        .filter(
            AssetBinding.asset_version_id.in_(version_ids or ["-"]),
            AssetBinding.deleted_at.is_(None),
        )
        .group_by(AssetBinding.asset_version_id)
        .all()
    )
    return [
        _asset_version_read_dict(version, storage, int(binding_counts.get(version.id, 0)))
        for version, storage in rows
    ]


def _slot_filter(query, *, source_kind: str, source_id: str, node_key: str | None, segment_key: str | None, role: str, order_index: int):
    query = query.filter(
        AssetBinding.source_kind == source_kind,
        AssetBinding.source_id == source_id,
        AssetBinding.role == role,
        AssetBinding.order_index == order_index,
    )
    query = query.filter(AssetBinding.node_key.is_(None)) if node_key is None else query.filter(AssetBinding.node_key == node_key)
    return query.filter(AssetBinding.segment_key.is_(None)) if segment_key is None else query.filter(AssetBinding.segment_key == segment_key)


def _manifest_references(value: Any, object_id: str) -> bool:
    if isinstance(value, str):
        return value == object_id
    if isinstance(value, dict):
        return any(_manifest_references(item, object_id) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_manifest_references(item, object_id) for item in value)
    return False


def binding_is_published(db: Session, binding: AssetBinding) -> bool:
    releases = (
        active_release_query(db)
        .filter(ProjectRelease.project_id == binding.project_id)
        .all()
    )
    return any(
        release_manifest_is_intact(release)
        and _manifest_references(release.manifest_json, binding.id)
        for release in releases
    )


def create_asset_binding(
    db: Session,
    *,
    project_id: int,
    asset_id: int | None = None,
    source_kind: str,
    source_id: str,
    asset_version_id: str,
    node_key: str | None,
    segment_key: str | None,
    role: str,
    order_index: int,
    required: bool,
) -> tuple[AssetBinding, bool]:
    validate_source_revision(
        db,
        project_id=project_id,
        source_kind=source_kind,
        source_revision_id=source_id,
    )
    db.query(Project).filter(Project.id == project_id).with_for_update().one()
    version = (
        db.query(AssetVersion)
        .join(Asset, Asset.id == AssetVersion.asset_id)
        .filter(AssetVersion.id == asset_version_id, Asset.project_id == project_id)
    )
    if asset_id is not None:
        version = version.filter(AssetVersion.asset_id == asset_id)
    version = version.first()
    if not version or not version.storage_object_id:
        raise HTTPException(status_code=404, detail="素材版本不存在或尚无物理文件")
    query = db.query(AssetBinding).filter(AssetBinding.project_id == project_id)
    existing = _slot_filter(
        query,
        source_kind=source_kind,
        source_id=source_id,
        node_key=node_key,
        segment_key=segment_key,
        role=role,
        order_index=order_index,
    ).first()
    if existing:
        if existing.deleted_at is None and existing.asset_version_id == version.id and existing.required == required:
            return existing, False
        if existing.deleted_at is None and binding_is_published(db, existing):
            raise HTTPException(status_code=409, detail="已发布版本引用该 binding，不能原位替换")
        existing.asset_version_id = version.id
        existing.required = required
        existing.deleted_at = None
        return existing, False

    binding = AssetBinding(
        project_id=project_id,
        source_kind=source_kind,
        source_id=source_id,
        node_key=node_key,
        segment_key=segment_key,
        role=role,
        asset_version_id=version.id,
        order_index=order_index,
        required=required,
    )
    db.add(binding)
    db.flush()
    return binding, True


def soft_delete_asset_binding(db: Session, *, project_id: int, binding_id: str) -> AssetBinding:
    binding = (
        db.query(AssetBinding)
        .filter(AssetBinding.id == binding_id, AssetBinding.project_id == project_id)
        .with_for_update()
        .first()
    )
    if not binding:
        raise HTTPException(status_code=404, detail="素材 binding 不存在")
    if binding.deleted_at is not None:
        return binding
    if binding_is_published(db, binding):
        raise HTTPException(status_code=409, detail="已发布版本引用该 binding，不能删除")
    binding.deleted_at = utcnow()
    return binding


def list_asset_bindings(db: Session, *, project_id: int, asset_id: int | None = None) -> list[AssetBinding]:
    query = db.query(AssetBinding).filter(AssetBinding.project_id == project_id, AssetBinding.deleted_at.is_(None))
    if asset_id is not None:
        get_project_asset(db, project_id, asset_id)
        query = query.join(AssetVersion, AssetVersion.id == AssetBinding.asset_version_id).filter(
            AssetVersion.asset_id == asset_id
        )
    return query.order_by(AssetBinding.source_kind, AssetBinding.source_id, AssetBinding.order_index).all()
