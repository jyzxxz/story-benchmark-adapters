"""Asset worker execution boundary.

Dispatcher wiring is intentionally kept outside this module so the durable
outbox can route ``asset.render`` to the image queue without coupling HTTP
routers to Celery.  The final worker route should map ``if_line.run_asset_task``
to :data:`ASSET_TASK_QUEUE` and pass a provider adapter implementing
``AssetRenderer``.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.application.asset_service import ASSET_RENDER_CREDITS, record_asset_version, refresh_asset_batch
from app.application.storage_service import (
    StoredFile,
    get_local_storage_backend,
    register_stored_file,
)
from app.application.task_service import (
    claim_task,
    complete_task,
    fail_task,
    lease_from_task,
    require_task_lease,
    task_lease_matches,
)
from app.database import SessionLocal
from app.models_v2 import GenerationTask, GenerationTaskDependency, ScriptResourceSlot
from app.services.image_generation_service import SIZE_PRESETS, image_generation_service
from app.workers.celery_app import celery_app
from app.workers.task_lease import TaskLeaseHeartbeat


ASSET_TASK_KIND = "asset.render"
ASSET_TASK_QUEUE = "image"
ASSET_CELERY_TASK_NAME = "if_line.run_asset_task"
ASSET_CELERY_ROUTE = {ASSET_CELERY_TASK_NAME: {"queue": ASSET_TASK_QUEUE}}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssetRenderResult:
    stored_file: StoredFile
    actual_cost: Decimal = Decimal("0")
    quality_score: float | None = None
    safety_status: str = "passed"
    rights_metadata: dict[str, Any] = field(default_factory=dict)


class AssetRenderer(Protocol):
    def render(self, parameters: dict[str, Any]) -> AssetRenderResult: ...


def _frozen_specs(parameters: dict[str, Any], expected_role: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    if str(parameters.get("asset_type") or "") != expected_role:
        raise ValueError(f"renderer requires asset_type={expected_role}")
    render_spec = dict(parameters.get("render_spec") or {})
    asset_spec = dict(
        dict(parameters.get("cache_material") or {}).get("normalized_asset_spec") or {}
    )
    prompt = str(render_spec.get("prompt") or "").strip()
    if not prompt:
        raise ValueError("asset.render prompt is empty")
    return render_spec, asset_spec, prompt


def _persist_provider_output(asset_type: str, result: dict[str, Any]) -> AssetRenderResult:
    if not result.get("success") or not result.get("image_path"):
        raise RuntimeError("image provider did not return an image")
    source = Path(str(result["image_path"])).resolve()
    provider_root = Path(image_generation_service.output_dir).resolve()
    try:
        source.relative_to(provider_root)
    except ValueError as exc:
        raise RuntimeError("image provider output escaped its root") from exc
    if not source.is_file():
        raise RuntimeError("image provider output is missing")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(source.suffix.lower())
    if not media_type:
        raise RuntimeError("image provider output type is unsupported")
    backend = get_local_storage_backend()
    with source.open("rb") as handle:
        stored = backend.save_stream(
            handle,
            namespace=f"generated-{asset_type}",
            filename_or_suffix=source.suffix,
            media_type=media_type,
        )
    return AssetRenderResult(
        stored_file=stored,
        actual_cost=Decimal("0") if result.get("cached") else ASSET_RENDER_CREDITS[asset_type],
        safety_status="passed",
        rights_metadata={
            "origin": "generated",
            "provider": "image-generation-service",
            "render_role": asset_type,
            "validation_results": result.get("validation_results") or [],
            "has_alpha": bool(
                asset_type == "portrait"
                and dict(result.get("portrait_alpha") or {}).get("passed")
            ),
            **(
                {"portrait_alpha": dict(result.get("portrait_alpha") or {})}
                if asset_type == "portrait"
                else {}
            ),
        },
    )


class PortraitTaskRenderer:
    def render(self, parameters: dict[str, Any]) -> AssetRenderResult:
        spec, asset_spec, prompt = _frozen_specs(parameters, "portrait")
        taxonomy = dict(asset_spec.get("taxonomy") or {})
        extra = dict(spec.get("extra_parameters") or {})
        result = asyncio.run(
            image_generation_service.generate_portrait(
                character_id=str(taxonomy.get("character_id") or parameters.get("asset_id")),
                character_name=str(asset_spec.get("target_name") or "portrait"),
                appearance_prompt=prompt,
                emotion=str(taxonomy.get("emotion") or "neutral"),
                outfit=str(taxonomy.get("outfit") or "default"),
                pose=str(taxonomy.get("pose") or "standing"),
                seed=spec.get("seed"),
                remove_bg=True,
                full_body=True,
                age_contract=extra.get("age_contract"),
                canonical_identity=extra.get("canonical_identity"),
                visual_style_prompt=extra.get("visual_style_prompt") or None,
                style_fingerprint=extra.get("style_fingerprint") or None,
            )
        )
        return _persist_provider_output("portrait", result)


class BackgroundTaskRenderer:
    def render(self, parameters: dict[str, Any]) -> AssetRenderResult:
        spec, asset_spec, prompt = _frozen_specs(parameters, "background")
        taxonomy = dict(asset_spec.get("taxonomy") or {})
        extra = dict(spec.get("extra_parameters") or {})
        cast_names = [str(name).strip() for name in (extra.get("forbidden_characters") or []) if str(name).strip()]
        # 本章出场角色以实体形式注册给 VLM 泄漏验收（背景图不得出现任何剧情角色）
        forbidden_entities = [
            {"canonical_name": name, "species": "human"} for name in cast_names
        ]
        result = asyncio.run(
            image_generation_service.generate_background_frozen_with_entity_validation(
                scene_name=str(asset_spec.get("target_name") or "scene"),
                scene_description=prompt,
                mood=str(taxonomy.get("mood") or "day"),
                final_prompt=prompt,
                forbidden_characters=cast_names,
                forbidden_entities=forbidden_entities,
                visual_style_prompt=extra.get("visual_style_prompt") or None,
            )
        )
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or "background generation failed"))
        return _persist_provider_output("background", result)


class KeyframeTaskRenderer:
    def render(self, parameters: dict[str, Any]) -> AssetRenderResult:
        from app.services.keyframe_semantic_validator_service import (
            KEYFRAME_SEMANTIC_MAX_RETRIES,
            KEYFRAME_SEMANTIC_VALIDATE_ENABLED,
            KeyframeSemanticValidatorService,
        )

        spec, asset_spec, prompt = _frozen_specs(parameters, "keyframe")
        extra = dict(spec.get("extra_parameters") or {})
        characters = list(extra.get("characters") or [])
        action_text = str(extra.get("action") or "").strip()
        # 携带外貌文本的角色必须走 _build_keyframe_prompt 合成画面，
        # 否则 final_prompt 短路会把立绘形象排除在关键帧之外。
        has_appearance = any(
            str((c or {}).get("appearance") or "").strip() for c in characters
        )

        def _generate(
            *,
            action_override: str | None = None,
            final_prompt_override: str | None = None,
        ) -> dict[str, Any]:
            return asyncio.run(
                image_generation_service.generate_keyframe(
                    event_name=str(asset_spec.get("target_name") or "keyframe"),
                    scene_description=prompt,
                    characters=characters,
                    action=action_override if action_override is not None else action_text,
                    emotion=str(extra.get("emotion") or ""),
                    final_prompt=(
                        final_prompt_override
                        if final_prompt_override is not None
                        else (None if has_appearance else prompt)
                    ),
                    visual_style_prompt=extra.get("visual_style_prompt") or None,
                )
            )

        result = _generate()

        # 出图后语义验收：画面必须呈现段落文本的核心视觉要素（kf20 类
        # 「文本写镜像世界、画面画黄昏街道」错配的闸门），失败带缺失要素
        # 重试，超限隔离不入库。
        if result.get("success") and KEYFRAME_SEMANTIC_VALIDATE_ENABLED and action_text:
            character_names = [
                str((c or {}).get("name") or "").strip()
                for c in characters
                if str((c or {}).get("name") or "").strip()
            ]
            validator = KeyframeSemanticValidatorService(
                config={"max_image_size": SIZE_PRESETS["keyframe"]},
            )
            result.setdefault("validation_results", [])
            attempt = 0
            while True:
                verdict = asyncio.run(
                    validator.validate(
                        Path(str(result["image_path"])),
                        action_text=action_text,
                        character_names=character_names,
                    )
                )
                result["validation_results"].append(
                    {
                        "stage": "semantic_match",
                        "attempt": attempt,
                        "passed": verdict.passed,
                        "missing_elements": list(verdict.missing_elements),
                        "reasons": list(verdict.reasons),
                    }
                )
                logger.info(
                    "[KF/semantic] attempt=%s passed=%s missing=%s",
                    attempt,
                    verdict.passed,
                    list(verdict.missing_elements)[:3],
                )
                if verdict.passed:
                    break
                if attempt >= KEYFRAME_SEMANTIC_MAX_RETRIES:
                    reasons_str = "; ".join(verdict.reasons) or "semantic mismatch"
                    raise RuntimeError(
                        f"quarantined: keyframe_semantic_mismatch: {reasons_str}"
                    )
                attempt += 1
                hints = "；".join(verdict.missing_elements[:5])
                retry_note = (
                    "【重试指令】上一次画面未呈现剧情要求。"
                    f"必须包含：{hints or '按剧情文本重新生成'}。"
                )
                if has_appearance:
                    result = _generate(action_override=f"{action_text}\n\n{retry_note}")
                else:
                    result = _generate(final_prompt_override=f"{prompt}\n\n{retry_note}")
                if not result.get("success"):
                    raise RuntimeError(
                        f"keyframe semantic retry generation failed: {result.get('error')}"
                    )
        return _persist_provider_output("keyframe", result)


class RoleDispatchRenderer:
    def __init__(self) -> None:
        self._renderers: dict[str, AssetRenderer] = {
            "portrait": PortraitTaskRenderer(),
            "background": BackgroundTaskRenderer(),
            "keyframe": KeyframeTaskRenderer(),
        }

    def render(self, parameters: dict[str, Any]) -> AssetRenderResult:
        role = str(parameters.get("asset_type") or "")
        renderer = self._renderers.get(role)
        if not renderer:
            raise ValueError("asset.render only supports visual asset types")
        return renderer.render(parameters)


def _refresh_parent_batches(db: Session, child_task_id: str) -> None:
    dependencies = (
        db.query(GenerationTaskDependency)
        .filter(GenerationTaskDependency.depends_on_task_id == child_task_id)
        .all()
    )
    for dependency in dependencies:
        parent = (
            db.query(GenerationTask)
            .filter(GenerationTask.id == dependency.task_id)
            .with_for_update()
            .first()
        )
        if parent:
            refresh_asset_batch(db, parent)


def execute_asset_task(
    task_id: str,
    *,
    renderer: AssetRenderer,
    worker_id: str,
    session_factory: sessionmaker = SessionLocal,
) -> str | None:
    """Claim, render outside a transaction, then persist one immutable version."""

    session = session_factory()
    try:
        task = claim_task(session, task_id, worker_id)
        if not task or task.kind != ASSET_TASK_KIND:
            session.rollback()
            return None
        parameters = dict(task.parameters or {})
        source_refs = dict(task.source_refs or {})
        user_id = task.user_id
        project_id = task.project_id
        lease = lease_from_task(task)
        session.commit()
    finally:
        session.close()
    try:
        with TaskLeaseHeartbeat(
            task_id,
            lease,
            session_factory=session_factory,
        ):
            rendered = renderer.render(parameters)
    except Exception:
        diagnostic = traceback.format_exc()
        logger.exception(
            "asset.render failed task_id=%s asset_type=%s asset_id=%s",
            task_id,
            parameters.get("asset_type"),
            parameters.get("asset_id"),
        )
        session = session_factory()
        try:
            task = (
                session.query(GenerationTask)
                .filter(GenerationTask.id == task_id)
                .with_for_update()
                .first()
            )
            if task and task_lease_matches(task, lease):
                fail_task(
                    session,
                    task,
                    error_code="asset.render_failed",
                    safe_detail="素材生成暂时不可用，请稍后重试",
                    retryable=True,
                    diagnostic=diagnostic,
                    lease=lease,
                )
                if task.status == "failed":
                    for slot in session.query(ScriptResourceSlot).filter(
                        ScriptResourceSlot.generation_task_id == task.id,
                        ScriptResourceSlot.status == "generating",
                    ):
                        slot.status = "failed"
                _refresh_parent_batches(session, task.id)
                session.commit()
        finally:
            session.close()
        return None

    backend = get_local_storage_backend()
    session = session_factory()
    try:
        task = (
            session.query(GenerationTask)
            .filter(GenerationTask.id == task_id)
            .with_for_update()
            .first()
        )
        if not task or not task_lease_matches(task, lease):
            session.rollback()
            backend.delete(rendered.stored_file.storage_key)
            return None
        require_task_lease(task, lease)
        session_scoped = str(source_refs.get("source_kind") or "") == "reading_continuation"
        storage = register_stored_file(
            session,
            stored=rendered.stored_file,
            owner_id=user_id,
            project_id=None if session_scoped else project_id,
            visibility="private" if session_scoped else "release",
            backend=backend,
        )
        version, version_created = record_asset_version(
            session,
            project_id=project_id,
            asset_id=int(parameters["asset_id"]),
            source_kind=str(source_refs["source_kind"]),
            source_revision_id=str(source_refs["source_revision_id"]),
            generation_task_id=task.id,
            storage_object_id=storage.id,
            cache_key=str(parameters["cache_key"]),
            render_spec=dict(parameters["render_spec"]),
            cache_material=dict(parameters["cache_material"]),
            quality_score=rendered.quality_score,
            safety_status=rendered.safety_status,
            rights_metadata=rendered.rights_metadata,
            storage_owner_id=user_id if session_scoped else None,
        )
        persisted_storage_object_id = version.storage_object_id
        actual_cost = rendered.actual_cost
        if not version_created:
            # Another worker committed the same immutable cache version while
            # this provider call was in flight. Keep the winner and remove the
            # duplicate object/file created by this task.
            session.delete(storage)
            session.flush()
            backend.delete(rendered.stored_file.storage_key)
            actual_cost = Decimal("0")
        bound_slots = []
        if str(source_refs.get("source_kind") or "") == "chapter_script_revision":
            from app.application.chapter_script_service import (
                bind_generated_script_resource_slots,
                queue_vn_graph_when_resources_ready,
            )

            bound_slots = bind_generated_script_resource_slots(
                session,
                project_id=int(project_id),
                asset_version_id=version.id,
                generation_task_id=task.id,
                script_revision_id=str(source_refs["source_revision_id"]),
            )
            for script_revision_id in sorted(
                {slot.chapter_script_revision_id for slot in bound_slots}
            ):
                queue_vn_graph_when_resources_ready(
                    session,
                    user_id=task.user_id,
                    project_id=int(project_id),
                    script_revision_id=script_revision_id,
                )
        complete_task(
            session,
            task,
            result_refs={
                "asset_id": parameters["asset_id"],
                "asset_version_id": version.id,
                "storage_object_id": persisted_storage_object_id,
                "cache_key": parameters["cache_key"],
                "script_resource_slot_ids": [slot.id for slot in bound_slots],
            },
            actual_cost=actual_cost,
            lease=lease,
        )
        _refresh_parent_batches(session, task.id)
        session.commit()
        return version.id
    except Exception:
        logger.exception("Asset persistence failed task_id=%s", task_id)
        session.rollback()
        backend.delete(rendered.stored_file.storage_key)
        failure_session = session_factory()
        try:
            task = (
                failure_session.query(GenerationTask)
                .filter(GenerationTask.id == task_id)
                .with_for_update()
                .first()
            )
            if task and task_lease_matches(task, lease):
                fail_task(
                    failure_session,
                    task,
                    error_code="asset.persist_failed",
                    safe_detail="素材保存暂时不可用，请稍后重试",
                    retryable=True,
                    lease=lease,
                )
                if task.status == "failed":
                    for slot in failure_session.query(ScriptResourceSlot).filter(
                        ScriptResourceSlot.generation_task_id == task.id,
                        ScriptResourceSlot.status == "generating",
                    ):
                        slot.status = "failed"
                _refresh_parent_batches(failure_session, task.id)
                failure_session.commit()
        finally:
            failure_session.close()
        return None
    finally:
        session.close()


@celery_app.task(name=ASSET_CELERY_TASK_NAME, bind=True)
def run_asset_task(self, task_id: str) -> None:
    execute_asset_task(
        task_id,
        renderer=RoleDispatchRenderer(),
        worker_id=f"asset-worker:{self.request.id}",
    )
