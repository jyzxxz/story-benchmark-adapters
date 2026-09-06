from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.asset_service import (
    create_asset_binding,
    create_asset_plan,
    list_asset_bindings,
    list_asset_versions,
    list_logical_assets,
    record_asset_version,
    refresh_asset_batch,
    request_asset_plan_render,
    request_asset_variant,
    soft_delete_asset_binding,
)
from app.application.hashing import content_hash
from app.application.revision_service import (
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
)
from app.application.story_outline_service import create_story_path_outline_revision
from app.application.storage_service import LocalStorageBackend
from app.application.task_service import (
    claim_task,
    complete_task,
    fail_task,
    lease_from_task,
    retry_task,
)
from app.database import Base
from app.database import get_db
from app.auth import get_current_user
from app.models import Asset, Project, User
from app.models_v2 import (
    AssetBinding,
    AssetPlan,
    AssetPlanItem,
    AssetVersion,
    ChapterScriptRevision,
    GenerationTask,
    GenerationTaskDependency,
    OutboxEvent,
    ProjectPublication,
    ProjectRelease,
    ReadingContinuation,
    ReadingSession,
    ScriptResourceSlot,
    StorageObject,
    StoryPath,
    TaskEvent,
)
from app.schemas_asset import AssetRenderSpec
from app.workers import asset_tasks
from app.workers.asset_tasks import AssetRenderResult, execute_asset_task
from app.routers.v2.assets import router as assets_router


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = factory()
    session.info["factory"] = factory
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _project_with_source(db, suffix: str = "one"):
    user = User(
        email=f"asset-{suffix}@example.com",
        password_hash="hash",
        display_name="asset owner",
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title=f"asset project {suffix}",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Root", status="active")
    db.add(path)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": suffix},
        source={"origin": "test"},
        user_id=user.id,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": 1,
                "title": "第一章",
                "summary": "开场",
                "characters": ["林夜"],
                "scene": "雨夜车站",
                "visual_keywords": ["站台灯"],
            }
        ],
        user_id=user.id,
    )
    activate_outline_revision(db, project.id, outline.id, approve=True)
    chapter = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="林夜走进雨夜车站。",
        user_id=user.id,
    )
    db.flush()
    return user, project, bible, outline, chapter


def _render_spec(*, prompt: str, seed: int = 7, width: int = 1024, height: int = 1024):
    return AssetRenderSpec(
        prompt=prompt,
        provider="test-provider",
        model="image-v1",
        prompt_template_version="prompt-v2",
        negative_prompt="text, watermark",
        negative_prompt_version="negative-v3",
        seed=seed,
        width=width,
        height=height,
        style_pack_version="modern-v2",
        identity_version="linye-v3",
        postprocess_version="alpha-v2",
        validator_version="visual-qc-v4",
        extra_parameters={"steps": 30},
    ).model_dump(mode="json")


def _plan_items():
    return [
        {
            "asset_type": "portrait",
            "logical_key": "character:linye:neutral",
            "target_name": "林夜",
            "chapter_index": 1,
            "taxonomy": {
                "character_id": "linye",
                "emotion": "neutral",
                "outfit": "default",
                "pose": "standing",
                "genre": "modern",
            },
            "render_spec": _render_spec(prompt="林夜中性立绘"),
        },
        {
            "asset_type": "background",
            "logical_key": "location:station:rainy-night",
            "target_name": "雨夜车站",
            "chapter_index": 1,
            "taxonomy": {
                "scene_location": "车站",
                "mood": "rainy-night",
                "genre": "modern",
            },
            "render_spec": _render_spec(
                prompt="空无一人的雨夜车站",
                seed=11,
                width=1536,
                height=1024,
            ),
        },
    ]


def _create_plan(db, user, project, chapter):
    return create_asset_plan(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        items=_plan_items(),
    )


def test_asset_plan_is_logically_idempotent_and_paginated(db):
    user, project, _bible, _outline, chapter = _project_with_source(db)
    first = _create_plan(db, user, project, chapter)
    replay = create_asset_plan(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        items=list(reversed(_plan_items())),
    )
    db.flush()

    assert first["plan_key"] == replay["plan_key"]
    assert first["created_count"] == 2
    assert replay["created_count"] == 0
    assert replay["reused_count"] == 2
    assert db.query(Asset).filter_by(project_id=project.id).count() == 2
    assert {asset.asset_type for asset in first["assets"]} == {"portrait", "background"}
    for asset in first["assets"]:
        assert asset.logical_key
        assert asset.taxonomy_json
        assert asset.generation_params == {}
    assert db.query(AssetPlan).filter_by(plan_key=first["plan_key"]).count() == 1
    assert db.query(AssetPlanItem).filter_by(plan_key=first["plan_key"]).count() == 2

    page, total = list_logical_assets(
        db,
        project_id=project.id,
        asset_type=None,
        status=None,
        logical_key=None,
        chapter_index=None,
        limit=1,
        offset=1,
    )
    assert total == 2
    assert len(page) == 1
    assert page[0]["version_count"] == 0
    assert page[0]["versions"] == []


def test_role_render_only_creates_selected_durable_parent_and_children(db):
    user, project, _bible, _outline, chapter = _project_with_source(db, "fanout")
    plan = _create_plan(db, user, project, chapter)
    first = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    db.flush()
    replay = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    db.flush()

    assert first["parent"].id == replay["parent"].id
    assert first["created_child_count"] == 1
    assert replay["created_child_count"] == 0
    assert first["child_task_ids"] == replay["child_task_ids"]
    assert db.query(GenerationTask).filter_by(project_id=project.id).count() == 2
    assert db.query(GenerationTaskDependency).filter_by(task_id=first["parent"].id).count() == 1
    # The orchestration parent has no broker event; each render child has one.
    assert db.query(OutboxEvent).count() == 1
    assert db.query(AssetVersion).count() == 0
    assert first["parent"].status == "running"


def test_asset_batch_reports_missing_dependency_and_recovers(db):
    user, project, _bible, _outline, chapter = _project_with_source(
        db,
        "dependency-invariant",
    )
    plan = _create_plan(db, user, project, chapter)
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    parent = batch["parent"]
    dependency = (
        db.query(GenerationTaskDependency)
        .filter_by(task_id=parent.id)
        .one()
    )
    child_id = dependency.depends_on_task_id
    db.delete(dependency)
    db.flush()

    refresh_asset_batch(db, parent)

    assert parent.status == "running"
    assert parent.stage == "dependency_inconsistent"
    assert parent.error_code == "asset.batch_dependency_inconsistent"
    assert child_id in parent.error_detail
    assert (
        db.query(TaskEvent)
        .filter_by(task_id=parent.id, event_type="fanout.inconsistent")
        .count()
        == 1
    )

    repaired_dependency = GenerationTaskDependency(
        task_id=parent.id,
        depends_on_task_id=child_id,
        required_status="failed",
    )
    db.add(repaired_dependency)
    db.flush()
    refresh_asset_batch(db, parent)

    assert parent.status == "running"
    assert parent.stage == "dependency_inconsistent"
    assert "invalid_required_status" in parent.error_detail

    repaired_dependency.required_status = "succeeded"
    db.flush()
    refresh_asset_batch(db, parent)

    assert parent.status == "running"
    assert parent.stage == "children_running"
    assert parent.error_code is None
    assert parent.error_detail is None


def test_asset_batch_repairs_premature_success_after_child_failure(db):
    user, project, _bible, _outline, chapter = _project_with_source(
        db,
        "premature-success",
    )
    plan = _create_plan(db, user, project, chapter)
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    parent = batch["parent"]
    child = db.query(GenerationTask).filter_by(id=batch["child_task_ids"][0]).one()
    complete_task(db, parent, result_refs=parent.result_refs, actual_cost=0)
    claim_task(db, child.id, "asset-failure")
    lease = lease_from_task(child)
    fail_task(
        db,
        child,
        error_code="provider.rejected",
        safe_detail="rejected",
        retryable=False,
        lease=lease,
    )

    refresh_asset_batch(db, parent)

    assert parent.status == "partial"
    assert parent.result_refs["failed_child_task_ids"] == [child.id]


def test_role_render_with_no_matching_items_is_successful_noop(db):
    user, project, _bible, _outline, chapter = _project_with_source(db, "no-op")
    plan = _create_plan(db, user, project, chapter)

    result = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="keyframe",
    )
    db.flush()

    assert result["parent_created"] is True
    assert result["created_child_count"] == 0
    assert result["child_task_ids"] == []
    assert result["parent"].status == "succeeded"
    assert result["parent"].result_refs["role"] == "keyframe"
    assert result["parent"].result_refs["selected_slot_count"] == 0
    assert db.query(OutboxEvent).count() == 0


def test_partial_asset_batch_retries_only_failed_dependency_children(db):
    user, project, _bible, _outline, chapter = _project_with_source(db, "partial-retry")
    items = _plan_items()
    items.append(
        {
            **items[0],
            "logical_key": "character:zhou:neutral",
            "target_name": "周衡",
            "taxonomy": {**items[0]["taxonomy"], "character_id": "zhou"},
            "render_spec": _render_spec(prompt="周衡中性立绘", seed=17),
        }
    )
    plan = create_asset_plan(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        items=items,
    )
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    children = (
        db.query(GenerationTask)
        .join(
            GenerationTaskDependency,
            GenerationTaskDependency.depends_on_task_id == GenerationTask.id,
        )
        .filter(GenerationTaskDependency.task_id == batch["parent"].id)
        .order_by(GenerationTask.id)
        .all()
    )
    assert len(children) == 2

    claim_task(db, children[0].id, "asset-success")
    first_lease = lease_from_task(children[0])
    complete_task(
        db,
        children[0],
        result_refs={"asset_version_id": "v1"},
        actual_cost=12,
        lease=first_lease,
    )
    claim_task(db, children[1].id, "asset-failure")
    second_lease = lease_from_task(children[1])
    fail_task(
        db,
        children[1],
        error_code="provider.rejected",
        safe_detail="rejected",
        retryable=False,
        lease=second_lease,
    )
    refresh_asset_batch(db, batch["parent"])
    assert batch["parent"].status == "partial"

    # Retry is a later API request in production, after the worker committed
    # the terminal child states and aggregate parent result.
    db.flush()
    retry_task(db, batch["parent"])
    db.flush()

    assert batch["parent"].status == "running"
    assert batch["parent"].stage == "retrying_failed_children"
    assert children[0].status == "succeeded"
    assert children[1].status == "queued"
    retried = batch["parent"].result_refs["failed_child_task_ids"]
    assert retried == [children[1].id]
    assert (
        db.query(OutboxEvent)
        .filter(
            OutboxEvent.aggregate_id == children[1].id,
            OutboxEvent.event_type == "task.asset.render.queued",
        )
        .count()
        == 2
    )


def test_variant_cache_key_is_complete_and_same_spec_reuses_task(db):
    user, project, _bible, _outline, chapter = _project_with_source(db, "cache")
    plan = _create_plan(db, user, project, chapter)
    portrait = next(asset for asset in plan["assets"] if asset.asset_type == "portrait")
    spec = _render_spec(prompt="林夜中性立绘")
    first = request_asset_variant(
        db,
        user_id=user.id,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        render_spec=spec,
    )
    replay = request_asset_variant(
        db,
        user_id=user.id,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        render_spec=spec,
    )
    changed = request_asset_variant(
        db,
        user_id=user.id,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        render_spec=_render_spec(prompt="林夜中性立绘", seed=99),
    )
    db.flush()

    assert first["task"].id == replay["task"].id
    assert first["created"] is True
    assert replay["created"] is False
    assert changed["task"].id != first["task"].id
    material = first["task"].parameters["cache_material"]
    assert set(material) >= {
        "normalized_asset_spec",
        "identity_version",
        "style_pack_version",
        "provider",
        "model",
        "prompt_template_version",
        "seed",
        "dimensions",
        "negative_prompt_hash",
        "negative_prompt_version",
        "postprocess_version",
        "validator_version",
    }


def test_portrait_renderer_uses_frozen_asset_target_name(monkeypatch):
    captured = {}

    async def fake_generate_portrait(**kwargs):
        captured.update(kwargs)
        return {"success": False}

    monkeypatch.setattr(
        asset_tasks.image_generation_service,
        "generate_portrait",
        fake_generate_portrait,
    )

    with pytest.raises(RuntimeError, match="image provider did not return an image"):
        asset_tasks.PortraitTaskRenderer().render(
            {
                "asset_id": 209,
                "asset_type": "portrait",
                "logical_key": "script:revision:portrait:character-zhou",
                "cache_material": {
                    "normalized_asset_spec": {"target_name": "周衡"},
                },
                "render_spec": {"prompt": "角色立绘，周衡，透明背景"},
            }
        )

    assert captured["character_name"] == "周衡"
    # frozen render_spec.prompt 是模板占位 stub，从未经过 LLM 重写，不得标记为
    # llm_rewriter 权威 prompt（否则 validate_portrait_final_prompt 直接拒绝）。
    assert "final_prompt" not in captured
    assert "final_prompt_source" not in captured


def test_background_and_keyframe_renderers_use_frozen_provider_inputs(monkeypatch):
    background_call = {}
    keyframe_call = {}

    async def fake_background(**kwargs):
        background_call.update(kwargs)
        return {"success": False}

    async def fake_keyframe(**kwargs):
        keyframe_call.update(kwargs)
        return {"success": False}

    monkeypatch.setattr(asset_tasks.image_generation_service, "generate_background", fake_background)
    monkeypatch.setattr(asset_tasks.image_generation_service, "generate_keyframe", fake_keyframe)

    # 背景走冻结 prompt 实体验收入口, 生成失败在此抛出(带原因),
    # 冻结 kwargs 由入口透传给 generate_background 并在下方断言。
    with pytest.raises(RuntimeError, match="background generation failed"):
        asset_tasks.BackgroundTaskRenderer().render(
            {
                "asset_id": 301,
                "asset_type": "background",
                "cache_material": {
                    "normalized_asset_spec": {
                        "target_name": "雨夜车站",
                        "taxonomy": {"mood": "rainy-night"},
                    }
                },
                "render_spec": {
                    "prompt": "空无一人的雨夜车站",
                    "extra_parameters": {"forbidden_characters": ["林夜"]},
                },
            }
        )
    with pytest.raises(RuntimeError, match="image provider did not return an image"):
        asset_tasks.KeyframeTaskRenderer().render(
            {
                "asset_id": 302,
                "asset_type": "keyframe",
                "cache_material": {
                    "normalized_asset_spec": {"target_name": "灯光熄灭"}
                },
                "render_spec": {
                    "prompt": "灯光骤然熄灭",
                    "extra_parameters": {
                        "characters": [{"name": "林夜"}],
                        "action": "回头",
                        "emotion": "tense",
                    },
                },
            }
        )

    assert background_call == {
        "scene_name": "雨夜车站",
        "scene_description": "空无一人的雨夜车站",
        "mood": "rainy-night",
        "final_prompt": "空无一人的雨夜车站",
        "forbidden_characters": ["林夜"],
        "visual_style_prompt": None,
    }
    assert keyframe_call == {
        "event_name": "灯光熄灭",
        "scene_description": "灯光骤然熄灭",
        "characters": [{"name": "林夜"}],
        "action": "回头",
        "emotion": "tense",
        "final_prompt": "灯光骤然熄灭",
        "visual_style_prompt": None,
    }


def _storage_object(db, user, project, suffix: str) -> StorageObject:
    obj = StorageObject(
        owner_id=user.id,
        project_id=project.id,
        storage_backend="local",
        storage_key=f"assets/test/{suffix}.png",
        media_type="image/png",
        byte_size=8,
        sha256=(suffix[0] * 64),
        visibility="release",
        status="active",
    )
    db.add(obj)
    db.flush()
    return obj


def test_project_asset_list_nests_all_versions_with_fixed_query_count(db):
    user, project, bible, outline, chapter = _project_with_source(db, "nested-history")
    plan = _create_plan(db, user, project, chapter)
    portrait = next(asset for asset in plan["assets"] if asset.asset_type == "portrait")
    script = ChapterScriptRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=1,
        source_hash="1" * 64,
        script_hash="2" * 64,
        script_json={},
        coverage_json={},
        schema_version="test-v1",
        generator_version="test-v1",
        status="complete",
        created_by=user.id,
    )
    db.add(script)
    db.flush()
    db.add(
        ScriptResourceSlot(
            project_id=project.id,
            chapter_script_revision_id=script.id,
            slot_key="scene-1:portrait:linye",
            role="portrait",
            scene_id="scene-1",
            character_id="linye",
            order_index=0,
            required=True,
            status="bound",
            asset_id=portrait.id,
            spec_json={},
        )
    )
    first_storage = _storage_object(db, user, project, "c")
    second_storage = _storage_object(db, user, project, "d")
    versions = [
        AssetVersion(
            asset_id=portrait.id,
            source_kind="chapter_script_revision",
            source_revision_id=script.id,
            source_hash=script.script_hash,
            storage_object_id=storage.id,
            version_no=index,
            cache_key=f"nested-cache-{index}",
            prompt_hash=f"{index}" * 64,
            prompt_version="test-v1",
            safety_status="passed",
            asset_spec_json={"logical_key": portrait.logical_key},
            render_spec_json={"prompt": f"portrait-{index}"},
        )
        for index, storage in enumerate([first_storage, second_storage], start=1)
    ]
    db.add_all(versions)
    db.flush()
    slot = db.query(ScriptResourceSlot).filter_by(asset_id=portrait.id).one()
    slot.asset_version_id = versions[0].id
    db.add(
        AssetBinding(
            project_id=project.id,
            source_kind="chapter_script_revision",
            source_id=script.id,
            role="portrait",
            asset_version_id=versions[0].id,
        )
    )
    db.flush()

    statements: list[str] = []
    engine = db.get_bind()

    def count_query(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count_query)
    try:
        items, total = list_logical_assets(
            db,
            project_id=project.id,
            asset_type="portrait",
            status=None,
            logical_key=portrait.logical_key,
            chapter_index=1,
            limit=50,
            offset=0,
        )
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    assert total == 1
    assert len(statements) == 4
    assert len(items) == 1
    item = items[0]
    assert item["chapter_indices"] == [1]
    assert item["version_count"] == 2
    assert item["latest_version_id"] == versions[1].id
    assert [version["id"] for version in item["versions"]] == [versions[1].id, versions[0].id]
    assert item["versions"][0]["media_url"] == f"/api/media/{second_storage.id}"
    assert "storage_object_id" not in item["versions"][0]
    assert item["versions"][1]["binding_count"] == 1


def test_versions_are_immutable_and_bindings_are_soft_deleted(db):
    user, project, bible, outline, chapter = _project_with_source(db, "binding")
    plan = _create_plan(db, user, project, chapter)
    portrait = next(asset for asset in plan["assets"] if asset.asset_type == "portrait")
    variant = request_asset_variant(
        db,
        user_id=user.id,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        render_spec=_render_spec(prompt="林夜中性立绘"),
    )
    storage = _storage_object(db, user, project, "a")
    version, created = record_asset_version(
        db,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        generation_task_id=variant["task"].id,
        storage_object_id=storage.id,
        cache_key=variant["cache_key"],
        render_spec=variant["task"].parameters["render_spec"],
        cache_material=variant["task"].parameters["cache_material"],
    )
    same, same_created = record_asset_version(
        db,
        project_id=project.id,
        asset_id=portrait.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        generation_task_id=variant["task"].id,
        storage_object_id=storage.id,
        cache_key=variant["cache_key"],
        render_spec=variant["task"].parameters["render_spec"],
        cache_material=variant["task"].parameters["cache_material"],
    )
    assert created is True
    assert same_created is False
    assert same.id == version.id
    assert len(list_asset_versions(db, project.id, portrait.id)) == 1

    binding, binding_created = create_asset_binding(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_id=chapter.id,
        asset_version_id=version.id,
        node_key="paragraph:1",
        segment_key=None,
        role="portrait",
        order_index=0,
        required=True,
    )
    replay, replay_created = create_asset_binding(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_id=chapter.id,
        asset_version_id=version.id,
        node_key="paragraph:1",
        segment_key=None,
        role="portrait",
        order_index=0,
        required=True,
    )
    assert binding_created is True
    assert replay_created is False
    assert replay.id == binding.id

    soft_delete_asset_binding(db, project_id=project.id, binding_id=binding.id)
    assert binding.deleted_at is not None
    restored, restored_created = create_asset_binding(
        db,
        project_id=project.id,
        source_kind="chapter_revision",
        source_id=chapter.id,
        asset_version_id=version.id,
        node_key="paragraph:1",
        segment_key=None,
        role="portrait",
        order_index=0,
        required=True,
    )
    assert restored_created is False
    assert restored.id == binding.id
    assert restored.deleted_at is None
    assert list_asset_bindings(db, project_id=project.id, asset_id=portrait.id) == [binding]

    manifest = {"asset_bindings": [{"binding_id": binding.id}]}
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        manifest_json=manifest,
        manifest_hash=content_hash(manifest),
        created_by=user.id,
        published_at=datetime.now(timezone.utc),
    )
    db.add(release)
    db.flush()
    db.add(
        ProjectPublication(
            project_id=project.id,
            active_release_id=release.id,
            published_at=release.published_at,
            lock_version=1,
        )
    )
    db.flush()
    with pytest.raises(HTTPException) as exc:
        soft_delete_asset_binding(db, project_id=project.id, binding_id=binding.id)
    assert exc.value.status_code == 409


def test_source_and_asset_ownership_are_project_scoped(db):
    user_one, project_one, _b1, _o1, chapter_one = _project_with_source(db, "scope-one")
    _user_two, project_two, _b2, _o2, chapter_two = _project_with_source(db, "scope-two")
    plan = _create_plan(db, user_one, project_one, chapter_one)
    asset = plan["assets"][0]

    with pytest.raises(HTTPException) as exc:
        request_asset_variant(
            db,
            user_id=user_one.id,
            project_id=project_one.id,
            asset_id=asset.id,
            source_kind="chapter_revision",
            source_revision_id=chapter_two.id,
            render_spec=_render_spec(prompt="cross project"),
        )
    assert exc.value.status_code == 404
    assert project_two.id != project_one.id


def test_asset_routes_require_project_owner(db):
    owner, project, _bible, _outline, chapter = _project_with_source(db, "route-owner")
    _create_plan(db, owner, project, chapter)
    outsider = User(
        email="asset-outsider@example.com",
        password_hash="hash",
        display_name="outsider",
    )
    db.add(outsider)
    db.flush()

    app = FastAPI()
    app.include_router(assets_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: outsider
    client = TestClient(app)
    assert client.get(f"/api/projects/{project.id}/assets").status_code == 403

    app.dependency_overrides[get_current_user] = lambda: owner
    response = client.get(f"/api/projects/{project.id}/assets")
    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_asset_delete_archives_unbound_asset(db):
    owner, project, _bible, _outline, chapter = _project_with_source(db, "delete-unbound")
    plan = _create_plan(db, owner, project, chapter)
    asset = plan["assets"][0]
    db.commit()

    app = FastAPI()
    app.include_router(assets_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: owner
    response = TestClient(app).delete(f"/api/projects/{project.id}/assets/{asset.id}")

    assert response.status_code == 200
    assert response.json()["id"] == asset.id
    archived = db.query(Asset).filter(Asset.id == asset.id).one()
    assert archived.archived_at is not None
    listed = TestClient(app).get(f"/api/projects/{project.id}/assets")
    assert all(item["id"] != asset.id for item in listed.json()["items"])


def test_asset_delete_archives_bound_asset_without_deleting_history(db):
    owner, project, _bible, _outline, chapter = _project_with_source(db, "delete-bound")
    plan = _create_plan(db, owner, project, chapter)
    asset = plan["assets"][0]
    version = AssetVersion(
        asset_id=asset.id,
        source_revision_id=chapter.id,
        version_no=1,
        cache_key="bound-delete-cache",
        prompt_hash="bound-delete-prompt",
        prompt_version="test-v1",
        safety_status="passed",
    )
    db.add(version)
    db.flush()
    db.add(
        AssetBinding(
            project_id=project.id,
            source_kind="chapter_revision",
            source_id=chapter.id,
            role="background",
            asset_version_id=version.id,
        )
    )
    db.commit()

    app = FastAPI()
    app.include_router(assets_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: owner
    response = TestClient(app).delete(f"/api/projects/{project.id}/assets/{asset.id}")

    assert response.status_code == 200
    archived = db.query(Asset).filter(Asset.id == asset.id).one()
    assert archived.archived_at is not None
    assert db.query(AssetVersion).filter_by(id=version.id).one()
    assert db.query(AssetBinding).filter_by(asset_version_id=version.id).one()


def _asset_app(db, user):
    app = FastAPI()
    app.include_router(assets_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _versioned_asset(db, suffix: str):
    owner, project, _bible, _outline, chapter = _project_with_source(db, suffix)
    plan = _create_plan(db, owner, project, chapter)
    asset = next(item for item in plan["assets"] if item.asset_type == "portrait")
    storage = _storage_object(db, owner, project, f"{suffix}-v1")
    version = AssetVersion(
        asset_id=asset.id,
        source_kind="chapter_revision",
        source_revision_id=chapter.id,
        storage_object_id=storage.id,
        version_no=1,
        cache_key=f"{suffix}-version-lookup-cache",
        prompt_hash="1" * 64,
        prompt_version="test-v1",
        safety_status="passed",
    )
    db.add(version)
    db.flush()
    return owner, project, asset, storage, version


def test_asset_version_resolves_by_id_across_projects_and_owners(db):
    owner, project, asset, storage, version = _versioned_asset(db, "version-lookup")
    other_project = Project(
        owner_id=owner.id,
        title="other project",
        story_start="start",
        story_end="end",
    )
    db.add(other_project)
    db.flush()
    outsider = User(
        email="version-lookup-outsider@example.com",
        password_hash="hash",
        display_name="outsider",
    )
    db.add(outsider)
    db.commit()

    client = _asset_app(db, owner)
    response = client.get(f"/api/projects/{project.id}/asset-versions/{version.id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == version.id
    assert payload["asset_id"] == asset.id
    assert payload["media_url"] == f"/api/media/{storage.id}"
    assert payload["media_type"] == "image/png"
    assert payload["storage_status"] == "active"
    assert client.get(
        f"/api/projects/{other_project.id}/asset-versions/{version.id}"
    ).status_code == 404
    assert client.get(
        f"/api/projects/{project.id}/asset-versions/{str(uuid4())}"
    ).status_code == 404
    assert _asset_app(db, outsider).get(
        f"/api/projects/{project.id}/asset-versions/{version.id}"
    ).status_code == 403


def test_archived_asset_version_stays_resolvable_by_id(db):
    owner, project, asset, storage, version = _versioned_asset(db, "archived-lookup")
    asset.archived_at = datetime.now(timezone.utc)
    db.commit()

    client = _asset_app(db, owner)
    listed = client.get(f"/api/projects/{project.id}/assets")
    assert all(item["id"] != asset.id for item in listed.json()["items"])
    response = client.get(f"/api/projects/{project.id}/asset-versions/{version.id}")
    assert response.status_code == 200
    assert response.json()["media_url"] == f"/api/media/{storage.id}"


def test_asset_worker_persists_versions_and_completes_batch(db, tmp_path, monkeypatch):
    user, project, _bible, _outline, chapter = _project_with_source(db, "worker")
    plan = _create_plan(db, user, project, chapter)
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    db.commit()

    backend = LocalStorageBackend(tmp_path / "objects")
    monkeypatch.setattr(asset_tasks, "get_local_storage_backend", lambda: backend)

    class FakeRenderer:
        def render(self, parameters):
            stored = backend.save_stream(
                BytesIO(b"fake-image"),
                namespace=f"assets/{parameters['asset_type']}",
                filename_or_suffix=".png",
                media_type="image/png",
            )
            return AssetRenderResult(stored_file=stored, actual_cost=12)

    factory = db.info["factory"]
    version_ids = [
        execute_asset_task(
            task_id,
            renderer=FakeRenderer(),
            worker_id=f"worker-{index}",
            session_factory=factory,
        )
        for index, task_id in enumerate(batch["child_task_ids"], start=1)
    ]
    assert all(version_ids)
    db.expire_all()
    parent = db.query(GenerationTask).filter_by(id=batch["parent"].id).one()
    children = db.query(GenerationTask).filter(GenerationTask.id.in_((batch["child_task_ids"]))).all()
    assert parent.status == "succeeded"
    assert parent.actual_cost == 0
    assert parent.result_refs["aggregate_child_cost"] == "12.000000"
    assert {child.status for child in children} == {"succeeded"}
    assert db.query(AssetVersion).filter(AssetVersion.id.in_(version_ids)).count() == 1
    assert db.query(StorageObject).filter_by(project_id=project.id, status="active").count() == 1


def test_asset_worker_discards_duplicate_file_when_cache_version_wins_race(
    db, tmp_path, monkeypatch
):
    user, project, _bible, _outline, chapter = _project_with_source(db, "cache-race")
    plan = _create_plan(db, user, project, chapter)
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    child = db.query(GenerationTask).filter_by(id=batch["child_task_ids"][0]).one()
    parameters = dict(child.parameters or {})
    source_refs = dict(child.source_refs or {})
    existing_storage = StorageObject(
        owner_id=user.id,
        project_id=project.id,
        storage_backend="local",
        storage_key="generated-portrait/existing.png",
        media_type="image/png",
        byte_size=8,
        sha256="a" * 64,
        visibility="release",
        status="active",
    )
    db.add(existing_storage)
    db.flush()
    existing_version, created = record_asset_version(
        db,
        project_id=project.id,
        asset_id=int(parameters["asset_id"]),
        source_kind=str(source_refs["source_kind"]),
        source_revision_id=str(source_refs["source_revision_id"]),
        generation_task_id=None,
        storage_object_id=existing_storage.id,
        cache_key=str(parameters["cache_key"]),
        render_spec=dict(parameters["render_spec"]),
        cache_material=dict(parameters["cache_material"]),
    )
    assert created is True
    db.commit()

    backend = LocalStorageBackend(tmp_path / "cache-race-objects")
    monkeypatch.setattr(asset_tasks, "get_local_storage_backend", lambda: backend)

    class FakeRenderer:
        def render(self, _parameters):
            stored = backend.save_stream(
                BytesIO(b"duplicate-image"),
                namespace="generated-portrait",
                filename_or_suffix=".png",
                media_type="image/png",
            )
            return AssetRenderResult(stored_file=stored, actual_cost=12)

    version_id = execute_asset_task(
        child.id,
        renderer=FakeRenderer(),
        worker_id="cache-race-worker",
        session_factory=db.info["factory"],
    )

    assert version_id == existing_version.id
    db.expire_all()
    completed = db.query(GenerationTask).filter_by(id=child.id).one()
    assert completed.status == "succeeded"
    assert completed.actual_cost == 0
    assert completed.result_refs["storage_object_id"] == existing_storage.id
    assert db.query(AssetVersion).filter_by(asset_id=existing_version.asset_id).count() == 1
    assert db.query(StorageObject).filter_by(project_id=project.id).count() == 1
    assert list(backend.root.rglob("*.png")) == []


def test_asset_worker_removes_new_file_when_database_persistence_fails(
    db, tmp_path, monkeypatch
):
    user, project, _bible, _outline, chapter = _project_with_source(db, "persist-failure")
    plan = _create_plan(db, user, project, chapter)
    batch = request_asset_plan_render(
        db,
        user_id=user.id,
        project_id=project.id,
        plan_key=plan["plan_key"],
        role="portrait",
    )
    db.commit()
    backend = LocalStorageBackend(tmp_path / "failed-objects")
    monkeypatch.setattr(asset_tasks, "get_local_storage_backend", lambda: backend)

    class FakeRenderer:
        def render(self, _parameters):
            stored = backend.save_stream(
                BytesIO(b"file-before-db-failure"),
                namespace="generated-portrait",
                filename_or_suffix=".png",
                media_type="image/png",
            )
            return AssetRenderResult(stored_file=stored, actual_cost=12)

    def fail_version_write(*_args, **_kwargs):
        raise RuntimeError("injected database failure")

    monkeypatch.setattr(asset_tasks, "record_asset_version", fail_version_write)
    task_id = batch["child_task_ids"][0]
    result = execute_asset_task(
        task_id,
        renderer=FakeRenderer(),
        worker_id="persist-failure-worker",
        session_factory=db.info["factory"],
    )

    assert result is None
    db.expire_all()
    task = db.query(GenerationTask).filter_by(id=task_id).one()
    assert task.status == "queued"
    assert task.error_code == "asset.persist_failed"
    assert db.query(StorageObject).filter_by(project_id=project.id).count() == 0
    assert list(backend.root.rglob("*.png")) == []


def test_guest_reading_image_is_stored_in_private_session_scope(db, tmp_path, monkeypatch):
    owner, project, bible, outline, _chapter = _project_with_source(db, "guest-reading")
    guest = User(
        email="guest-reader@guest.ifline.local",
        password_hash="guest-login-disabled$test",
        display_name="guest reader",
        quota_total=1000,
        quota_daily=100,
    )
    db.add(guest)
    db.flush()
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        manifest_json={"story": {}},
        manifest_hash="f" * 64,
        created_by=owner.id,
    )
    db.add(release)
    db.flush()
    reading = ReadingSession(
        user_id=guest.id,
        project_id=project.id,
        release_id=release.id,
    )
    db.add(reading)
    db.flush()
    continuation = ReadingContinuation(
        session_id=reading.id,
        direction="收到一封密信",
        visual_mode="system_generate",
        status="preview_ready",
        continuation_text="刘备在夜色中拆开密信。",
        state_delta={},
        base_session_lock_version=1,
        idempotency_key="guest-reading-continuation",
        request_hash="e" * 64,
    )
    db.add(continuation)
    db.flush()
    plan = create_asset_plan(
        db,
        project_id=project.id,
        source_kind="reading_continuation",
        source_revision_id=continuation.id,
        items=[
            {
                "asset_type": "background",
                "logical_key": "guest-reading:secret-letter",
                "target_name": "夜读密信",
                "chapter_index": None,
                "taxonomy": {"scene_location": "桃园"},
                "render_spec": _render_spec(prompt="刘备在夜色中拆开密信"),
            }
        ],
    )
    variant = request_asset_variant(
        db,
        user_id=guest.id,
        project_id=project.id,
        asset_id=plan["assets"][0].id,
        source_kind="reading_continuation",
        source_revision_id=continuation.id,
        render_spec=_render_spec(prompt="刘备在夜色中拆开密信"),
    )
    db.commit()

    backend = LocalStorageBackend(tmp_path / "guest-objects")
    monkeypatch.setattr(asset_tasks, "get_local_storage_backend", lambda: backend)

    class FakeRenderer:
        def render(self, _parameters):
            stored = backend.save_stream(
                BytesIO(b"guest-image"),
                namespace="generated-background",
                filename_or_suffix=".png",
                media_type="image/png",
            )
            return AssetRenderResult(stored_file=stored, actual_cost=12)

    version_id = execute_asset_task(
        variant["task"].id,
        renderer=FakeRenderer(),
        worker_id="guest-reading-image-worker",
        session_factory=db.info["factory"],
    )

    assert version_id
    db.expire_all()
    version = db.query(AssetVersion).filter_by(id=version_id).one()
    storage = db.query(StorageObject).filter_by(id=version.storage_object_id).one()
    assert storage.owner_id == guest.id
    assert storage.project_id is None
    assert storage.visibility == "private"


# ----------------------- generate_chapter_backgrounds_v2 错误上报 -----------------------

def test_generate_chapter_backgrounds_v2_raises_on_missing_outline(db):
    """无 ChapterOutline → raise MissingPrerequisiteError(code=missing_outline)。

    回归保护：旧版静默返回 ``{"total":0, "error":"章节大纲不存在"}``，调用方
    无法区分「大纲没建」与「大纲存在但生成 0 张」。新版用结构化异常，
    assembler 的 run_stage 会把它写进 Meta.generation_report。
    """
    from app.models import Project, User
    from app.services.asset_management_service import AssetManagementService
    from app.services.generation_report import MissingPrerequisiteError

    user = User(
        email="bg-noprereq@example.com",
        password_hash="hash",
        display_name="bg owner",
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="bg project",
        story_start="start",
        story_end="end",
    )
    db.add(project)
    db.flush()
    # 故意不创建 ChapterOutline / StoryBible —— 模拟用户未跑前置大纲生成

    with pytest.raises(MissingPrerequisiteError) as exc_info:
        asyncio.run(
            AssetManagementService(db).generate_chapter_backgrounds_v2(
                project.id, chapter_index=1,
            )
        )
    assert exc_info.value.code == "missing_outline"
    assert exc_info.value.stage == "backgrounds"


def test_normalize_plan_items_merges_same_logical_key_across_paragraphs():
    """同一角色+情绪跨段落复用时不应被判为冲突。

    回归：同一 (asset_type, logical_key) 以相同 render_spec 出现 N 次、仅 taxonomy
    provenance（scene_id/paragraph_id）不同，过去会抛 422「同一 logical_key 存在冲突定义」，
    现在应折叠成一条素材。
    """
    from app.application.asset_service import _normalize_plan_items

    render_spec = {"prompt": "角色立绘，火麟飞，grief，透明背景，无文字", "model": "m"}
    items = [
        {
            "asset_type": "portrait",
            "logical_key": "portrait:character-1:grief:default:standing",
            "target_name": "火麟飞",
            "chapter_index": 1,
            "taxonomy": {
                "scene_id": "s1",
                "paragraph_id": "p3",
                "character_id": "character-1",
                "emotion": "grief",
            },
            "render_spec": dict(render_spec),
        },
        {
            "asset_type": "portrait",
            "logical_key": "portrait:character-1:grief:default:standing",
            "target_name": "火麟飞",
            "chapter_index": 1,
            "taxonomy": {
                "scene_id": "s1",
                "paragraph_id": "p8",
                "character_id": "character-1",
                "emotion": "grief",
            },
            "render_spec": dict(render_spec),
        },
    ]
    normalized = _normalize_plan_items(items)
    assert len(normalized) == 1
    assert normalized[0]["logical_key"] == "portrait:character-1:grief:default:standing"


def test_normalize_plan_items_rejects_genuine_render_conflict():
    """同一 logical_key 但 render_spec（真正画面）不同，仍是真冲突，照常抛 422。"""
    from app.application.asset_service import _normalize_plan_items

    base_spec = {"prompt": "角色立绘，火麟飞，grief，透明背景，无文字", "model": "m"}
    items = [
        {
            "asset_type": "portrait",
            "logical_key": "portrait:character-1:grief:default:standing",
            "target_name": "火麟飞",
            "chapter_index": 1,
            "taxonomy": {"paragraph_id": "p3"},
            "render_spec": dict(base_spec),
        },
        {
            "asset_type": "portrait",
            "logical_key": "portrait:character-1:grief:default:standing",
            "target_name": "火麟飞",
            "chapter_index": 1,
            "taxonomy": {"paragraph_id": "p8"},
            "render_spec": {**base_spec, "prompt": "角色立绘，火麟飞，rage，透明背景，无文字"},
        },
    ]
    with pytest.raises(HTTPException) as exc:
        _normalize_plan_items(items)
    assert exc.value.status_code == 422
