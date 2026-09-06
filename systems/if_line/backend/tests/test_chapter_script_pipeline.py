from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.chapter_script_service import (
    ChapterScriptSourceError,
    ProviderCallAlreadyAttempted,
    activate_chapter_script_head,
    bind_generated_script_resource_slots,
    bind_script_resource_slot,
    create_chapter_script_generation_task,
    create_manual_chapter_script_revision,
    generate_chapter_script_task,
    get_chapter_script_head,
    list_chapter_script_revisions,
    queue_vn_graph_when_resources_ready,
    request_script_resource_render,
)
from app.application.asset_service import record_asset_version, request_asset_variant
from app.application.hashing import content_hash
from app.application.story_bible_service import create_bible_revision
from app.application.story_chapter_service import (
    build_chapter_generation_source,
    create_story_path_chapter_revision,
    resolve_chapter_generation_source,
)
from app.application.story_outline_service import (
    activate_story_path_outline_revision,
    create_story_path_outline_revision,
)
from app.application.task_service import claim_task, lease_from_task
from app.application.vn_graph_service import (
    create_vn_graph_compile_task,
    execute_vn_graph_compile_task,
    load_compile_input,
)
from app.core.errors import AppError
from app.database import Base
from app.integrations.llm.base import ModelCallResult
from app.models import Asset, Project, User
from app.models_v2 import (
    AssetBinding,
    AssetPlanItem,
    AssetVersion,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    GenerationTask,
    OutlineChapter,
    OutlineRevision,
    ScriptResourceSlot,
    StateSnapshot,
    StorageObject,
    StoryBibleRevision,
    StoryPath,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
)
from app.routers.v2.authoring_scripts import bind_resource_slot
from app.schemas_authoring_artifacts import ResourceSlot, ResourceSlotBind
from app.services.chapter_script_ir import validate_script_ir
from app.services.vn_graph_compiler import vn_graph_compiler


@pytest.fixture()
def db_and_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    db = factory()
    try:
        yield db, factory
    finally:
        db.close()
        engine.dispose()


def _seed(db):
    user = User(
        email="script-pipeline@example.com",
        password_hash="hash",
        display_name="Script owner",
        is_active=True,
        quota_total=1000,
        quota_daily=1000,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="原文覆盖",
        story_start="开始",
        story_end="结束",
        visibility="private",
        is_draft=True,
    )
    db.add(project)
    db.flush()
    bible_content = {
        "worldview": "现代城市",
        "characters": [{"character_id": "character-an", "name": "阿宁"}],
    }
    path = StoryPath(project_id=project.id, title="Main")
    db.add(path)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content=bible_content,
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": 1,
                "title": "雨夜",
                "summary": "阿宁抵达车站",
                "conflict": "寻找出口",
                "characters": ["阿宁"],
                "scene": "雨夜车站",
                "emotion": "紧张",
                "visual_keywords": ["雨", "车站"],
            }
        ],
        user_id=user.id,
    )
    placement = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=outline.id,
    ).path_chapters[0]
    content = "  雨落在站台。\r\n\r\n阿宁说：\"走吧。\"  \n灯光熄灭。"
    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=placement.id,
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    chapter = create_story_path_chapter_revision(
        db,
        context=resolve_chapter_generation_source(
            db,
            task_project_id=project.id,
            source_refs=source_refs,
        ),
        content=content,
        user_id=user.id,
    )
    placement.current_revision_id = chapter.id
    db.commit()
    return SimpleNamespace(
        user=user,
        project=project,
        bible=bible,
        outline=outline,
        path=path,
        placement=placement,
        chapter=chapter,
        content=content,
    )


class FakeScriptAdapter:
    """v3 fake：LLM 自由切分（合并前两行 + 拆开混合行），text 为原文逐字摘录。

    支持三种剧本：
    - 默认：一次成功（4 个 segments 覆盖全文）
    - fail=True：provider 异常
    - bad_times=N：前 N 次输出改写文本触发守护校验失败，之后成功
    """

    def __init__(
        self,
        *,
        fail: bool = False,
        request_id: str = "request-once",
        bad_times: int = 0,
    ):
        self.calls = 0
        self.fail = fail
        self.request_id = request_id
        self.bad_times = bad_times
        self.feedbacks: list[str] = []

    async def generate(self, request, *, validation_feedback: str = ""):
        self.calls += 1
        if validation_feedback:
            self.feedbacks.append(validation_feedback)
        assert request.bible["worldview"] == "现代城市"
        assert request.outline_chapter["summary"] == "阿宁抵达车站"
        assert request.chapter_content.startswith("  雨落在站台")
        if self.fail:
            raise RuntimeError("provider response lost")
        if self.calls <= self.bad_times:
            segments = [{"text": "供应商试图改写正文", "kind": "narration"}]
        else:
            segments = [
                {"text": "雨落在站台。", "kind": "narration", "scene_key": "station"},
                {"text": "阿宁说：", "kind": "narration", "scene_key": "station"},
                {
                    "text": "\"走吧。\"",
                    "kind": "dialogue",
                    "scene_key": "station",
                    "speaker_character_id": "character-an",
                    "emotion": "tense",
                },
                {
                    "text": "灯光熄灭。",
                    "kind": "narration",
                    "scene_key": "station",
                    "keyframe": {"required": True, "prompt": "灯光骤然熄灭"},
                },
            ]
        return ModelCallResult(
            data={
                "scenes": [{"scene_key": "station", "title": "雨夜", "location": "车站"}],
                "segments": segments,
            },
            raw_text="{}",
            provider="fake",
            model="fake-script",
            provider_request_id=self.request_id,
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            prompt_version="test-v1",
        )


def _claim_lease(db, task: GenerationTask):
    claimed = claim_task(db, task.id, f"script-test:{task.id}")
    assert claimed is not None
    lease = lease_from_task(claimed)
    db.commit()
    return lease


def _queue_and_generate(db, factory, seeded, monkeypatch):
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, created = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-once",
    )
    db.commit()
    assert created is True
    assert task.max_attempts == 1
    adapter = FakeScriptAdapter()
    result = generate_chapter_script_task(
        task.id,
        adapter=adapter,
        lease=_claim_lease(db, task),
    )
    db.expire_all()
    return task, result, adapter


def _materialize_slot_versions(db, seeded, revision):
    slots = (
        db.query(ScriptResourceSlot)
        .filter(ScriptResourceSlot.chapter_script_revision_id == revision.id)
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )
    versions = []
    for index, slot in enumerate(slots):
        asset = db.query(Asset).filter(Asset.id == slot.asset_id).one()
        storage = StorageObject(
            owner_id=seeded.user.id,
            project_id=seeded.project.id,
            storage_backend="local",
            storage_key=f"script/{slot.role}-{index}.png",
            media_type="image/png",
            byte_size=10,
            sha256=f"{index + 1:064x}",
            visibility="release",
            status="active",
        )
        db.add(storage)
        db.flush()
        version = AssetVersion(
            asset_id=asset.id,
            source_revision_id=revision.id,
            storage_object_id=storage.id,
            version_no=1,
            cache_key=f"slot-{index}",
            prompt_hash=content_hash(asset.prompt or ""),
            prompt_version="script-resource-v1",
            safety_status="passed",
        )
        db.add(version)
        db.flush()
        versions.append(version)
    db.commit()
    return slots, versions


def _materialize_slots(db, seeded, revision):
    slots, versions = _materialize_slot_versions(db, seeded, revision)
    for slot, version in zip(slots, versions, strict=True):
        bind_script_resource_slot(
            db,
            project_id=seeded.project.id,
            slot_id=slot.id,
            asset_version_id=version.id,
            script_revision_id=revision.id,
        )
    db.commit()
    return slots


def _contains_res(value):
    if isinstance(value, str):
        return value.startswith("res://")
    if isinstance(value, dict):
        return any(_contains_res(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_res(item) for item in value)
    return False


def _paragraph_lines(graph):
    return [
        wrapped["ObjectValue"]
        for node in graph["Nodes"]
        if node.get("NodeType") == 1 and node.get("SubType") == 2
        for wrapped in node["Data"]["Lines"]["Items"]
    ]


def _node_shape(graph):
    return [
        (node["Index"], node["NodeType"], node["SubType"], dict(node["Outputs"]))
        for node in graph["Nodes"]
    ]


def test_script_ir_exactly_covers_original_source_with_separators():
    from app.services.chapter_script_ir import build_script_ir_from_segments

    content = "  第一行。\r\n\r\n第二行。  \n"
    script = build_script_ir_from_segments(
        chapter_revision_id="chapter",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="bible",
        outline_revision_id="outline",
        characters=[],
        llm_output={
            "segments": [
                {"text": "第一行。第二行。", "kind": "narration"},
            ]
        },
    )
    coverage = validate_script_ir(script, expected_content=content)
    assert "".join(span["text"] for span in script["spans"]) == content
    assert coverage["coverage_ratio"] == 1.0
    assert coverage["covered_characters"] == len(content)


def test_script_revision_uses_one_provider_call_and_allocates_semantic_slots(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    task, result, adapter = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).one()
    slots = db.query(ScriptResourceSlot).order_by(ScriptResourceSlot.order_index).all()

    assert adapter.calls == 1
    assert result.result_refs["chapter_script_revision_id"] == revision.id
    assert revision.coverage_json["coverage_ratio"] == 1.0
    assert "".join(span["text"] for span in revision.script_json["spans"]) == seeded.content
    assert all(
        paragraph["text"] != "供应商试图改写正文"
        for paragraph in revision.script_json["paragraphs"]
    )
    assert all(slot.scene_id and slot.paragraph_id for slot in slots)
    assert any(slot.character_id == "character-an" for slot in slots if slot.role == "portrait")
    assert all(slot.status == "planned" and slot.asset_id for slot in slots)
    plan_items = db.query(AssetPlanItem).order_by(AssetPlanItem.asset_type).all()
    assert plan_items
    dimensions = {
        "portrait": [1024, 1536],
        "background": [1536, 864],
        "keyframe": [1536, 864],
    }
    for item in plan_items:
        render_spec = item.render_spec_json
        assert render_spec["provider"] in {"qwen", "openai_compatible"}
        assert render_spec["model"]
        assert render_spec["prompt_template_version"] == "script-resource-v1"
        assert [render_spec["width"], render_spec["height"]] == dimensions[item.asset_type]
        assert render_spec["style_pack_version"] == "chapter-script-visual-v1"
        assert render_spec["postprocess_version"]
        assert render_spec["validator_version"]

    recovered = generate_chapter_script_task(task.id, adapter=adapter)
    assert recovered.result_refs["recovered"] is True
    assert adapter.calls == 1


def test_manual_vngraph_compile_allows_unbound_required_draft(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    slots = (
        db.query(ScriptResourceSlot)
        .filter_by(chapter_script_revision_id=revision.id)
        .order_by(ScriptResourceSlot.order_index)
        .all()
    )

    assert slots
    assert all(
        slot.required and slot.status == "planned" and slot.asset_version_id is None
        for slot in slots
    )
    slot_payload = ResourceSlot.model_validate(slots[0])
    assert slot_payload.required is True
    assert slot_payload.status == "planned"
    assert queue_vn_graph_when_resources_ready(
        db,
        user_id=seeded.user.id,
        project_id=seeded.project.id,
        script_revision_id=revision.id,
    ) is None

    source = load_compile_input(db, script_revision_id=revision.id)
    compiled = vn_graph_compiler.compile(source)
    assert all(item["required"] for item in compiled.binding_manifest["asset_bindings"])
    assert all(
        item["asset_version_id"] is None
        and item["slot_status"] == "planned"
        and item["status"] == "missing_version"
        for item in compiled.binding_manifest["asset_bindings"]
    )
    assert not any(
        node.get("NodeType") == 2 and node.get("SubType") in {1, 3, 6}
        for node in compiled.graph["Nodes"]
    )
    assert not _contains_res(compiled.graph)

    task, created = create_vn_graph_compile_task(
        db,
        user_id=seeded.user.id,
        script_revision_id=revision.id,
        idempotency_key="compile-unbound-draft",
    )
    db.commit()
    executed, graph_revision, graph_created = execute_vn_graph_compile_task(
        db,
        task_id=task.id,
        worker_id="test-draft-compiler",
    )
    db.commit()

    assert created is True
    assert executed.status == "succeeded"
    assert graph_created is True
    assert graph_revision.status == "ready"
    assert graph_revision.binding_manifest == compiled.binding_manifest


def test_same_chapter_supports_multiple_review_versions_and_explicit_head_activation(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    first_task, first_created = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-review-first",
        parameters={"direction": "quiet"},
    )
    db.commit()
    first_result = generate_chapter_script_task(
        first_task.id,
        adapter=FakeScriptAdapter(request_id="review-first"),
        lease=_claim_lease(db, first_task),
    )
    db.expire_all()

    initial_head = get_chapter_script_head(
        db,
        chapter_revision_id=seeded.chapter.id,
    )
    replay, replay_created = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-review-first",
        parameters={"direction": "quiet"},
    )
    second_task, second_created = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-review-second",
        parameters={"direction": "bold"},
    )
    db.commit()
    second_result = generate_chapter_script_task(
        second_task.id,
        adapter=FakeScriptAdapter(request_id="review-second"),
        lease=_claim_lease(db, second_task),
    )
    db.expire_all()

    revisions = list_chapter_script_revisions(
        db,
        chapter_revision_id=seeded.chapter.id,
    )
    first_revision_id = first_result.result_refs["chapter_script_revision_id"]
    second_revision_id = second_result.result_refs["chapter_script_revision_id"]
    assert first_created is True
    assert replay_created is False
    assert replay.id == first_task.id
    assert second_created is True
    assert second_task.id != first_task.id
    assert initial_head.revision_id is None
    assert initial_head.lock_version == 1
    assert [revision.revision_no for revision in revisions] == [2, 1]
    assert {revision.status for revision in revisions} == {"ready"}
    assert get_chapter_script_head(
        db,
        chapter_revision_id=seeded.chapter.id,
    ).revision_id is None

    first_activation = activate_chapter_script_head(
        db,
        chapter_revision_id=seeded.chapter.id,
        revision_id=first_revision_id,
        expected_lock_version=1,
    )
    second_activation = activate_chapter_script_head(
        db,
        chapter_revision_id=seeded.chapter.id,
        revision_id=second_revision_id,
        expected_lock_version=2,
    )
    same_activation = activate_chapter_script_head(
        db,
        chapter_revision_id=seeded.chapter.id,
        revision_id=second_revision_id,
        expected_lock_version=3,
    )
    assert first_activation.head.lock_version == 2
    assert second_activation.head.lock_version == 3
    assert second_activation.head.revision_id == second_revision_id
    assert same_activation.head.lock_version == 3
    with pytest.raises(AppError) as stale:
        activate_chapter_script_head(
            db,
            chapter_revision_id=seeded.chapter.id,
            revision_id=first_revision_id,
            expected_lock_version=2,
        )
    assert stale.value.code == "head.version_conflict"


def test_script_generation_rejects_tampered_exact_source_before_provider_call(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-tampered-source",
    )
    db.commit()
    # Bypass the ORM immutability listener to simulate out-of-band storage
    # corruption. Production PostgreSQL additionally rejects this at the
    # trigger layer; this SQLite fixture exercises the worker's hash check.
    db.execute(
        ChapterRevision.__table__.update()
        .where(ChapterRevision.id == seeded.chapter.id)
        .values(content="正文已被篡改")
    )
    db.commit()
    adapter = FakeScriptAdapter()

    with pytest.raises(ChapterScriptSourceError, match="正文哈希不匹配"):
        generate_chapter_script_task(
            task.id,
            adapter=adapter,
            lease=_claim_lease(db, task),
        )
    assert adapter.calls == 0


def test_script_revisions_reuse_matching_version_and_pin_changed_version(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, first_result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    first_script = db.query(ChapterScriptRevision).filter_by(
        id=first_result.result_refs["chapter_script_revision_id"]
    ).one()
    first_render = request_script_resource_render(
        db,
        user_id=seeded.user.id,
        project_id=seeded.project.id,
        script_revision_id=first_script.id,
        role="portrait",
    )
    assert len(first_render["child_task_ids"]) == 1
    first_task = db.query(GenerationTask).filter_by(id=first_render["child_task_ids"][0]).one()
    first_asset = db.query(Asset).filter_by(id=first_task.parameters["asset_id"]).one()
    assert first_script.id not in first_asset.logical_key
    generating_slots = (
        db.query(ScriptResourceSlot)
        .filter_by(chapter_script_revision_id=first_script.id, role="portrait")
        .all()
    )
    assert generating_slots
    assert {slot.status for slot in generating_slots} == {"generating"}
    assert {slot.generation_task_id for slot in generating_slots} == {first_task.id}

    first_storage = StorageObject(
        owner_id=seeded.user.id,
        project_id=seeded.project.id,
        storage_backend="local",
        storage_key="script-cache/portrait-v1.png",
        media_type="image/png",
        byte_size=10,
        sha256="a" * 64,
        visibility="release",
        status="active",
    )
    db.add(first_storage)
    db.flush()
    first_version, created = record_asset_version(
        db,
        project_id=seeded.project.id,
        asset_id=first_asset.id,
        source_kind="chapter_script_revision",
        source_revision_id=first_script.id,
        generation_task_id=first_task.id,
        storage_object_id=first_storage.id,
        cache_key=first_task.parameters["cache_key"],
        render_spec=dict(first_task.parameters["render_spec"]),
        cache_material=dict(first_task.parameters["cache_material"]),
    )
    assert created is True
    first_slots = bind_generated_script_resource_slots(
        db,
        project_id=seeded.project.id,
        asset_version_id=first_version.id,
        generation_task_id=first_task.id,
        script_revision_id=first_script.id,
    )
    assert first_slots and all(slot.status == "bound" for slot in first_slots)

    second_state_json = {"route": "same-prose-second-state"}
    second_state = StateSnapshot(
        project_id=seeded.project.id,
        state_hash=content_hash(second_state_json),
        state_json=second_state_json,
    )
    db.add(second_state)
    db.flush()
    second_source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=seeded.placement.id,
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        state_snapshot_id=second_state.id,
    )
    second_chapter = create_story_path_chapter_revision(
        db,
        context=resolve_chapter_generation_source(
            db,
            task_project_id=seeded.project.id,
            source_refs=second_source_refs,
        ),
        content=seeded.content,
        user_id=seeded.user.id,
    )
    second_task, created = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=second_chapter.id,
        idempotency_key="script-second-revision",
    )
    assert created is True
    db.commit()
    second_result = generate_chapter_script_task(
        second_task.id,
        adapter=FakeScriptAdapter(request_id="request-twice"),
        lease=_claim_lease(db, second_task),
    )
    db.expire_all()
    second_script = db.query(ChapterScriptRevision).filter_by(
        id=second_result.result_refs["chapter_script_revision_id"]
    ).one()
    second_render = request_script_resource_render(
        db,
        user_id=seeded.user.id,
        project_id=seeded.project.id,
        script_revision_id=second_script.id,
        role="portrait",
    )
    second_slots = (
        db.query(ScriptResourceSlot)
        .filter_by(chapter_script_revision_id=second_script.id, role="portrait")
        .all()
    )

    assert second_render["child_task_ids"] == []
    assert first_script.revision_no == second_script.revision_no == 1
    assert {
        head.chapter_revision_id
        for head in db.query(ChapterScriptHead).all()
    } == {seeded.chapter.id, second_chapter.id}
    assert second_render["parent"].result_refs["cached_version_ids"] == [first_version.id]
    assert second_slots
    assert {slot.asset_id for slot in second_slots} == {first_asset.id}
    assert {slot.asset_version_id for slot in second_slots} == {first_version.id}
    assert {slot.asset_version_id for slot in first_slots} == {first_version.id}

    changed_spec = dict(first_task.parameters["render_spec"])
    changed_spec["seed"] = int(changed_spec.get("seed") or 0) + 1
    changed = request_asset_variant(
        db,
        user_id=seeded.user.id,
        project_id=seeded.project.id,
        asset_id=first_asset.id,
        source_kind="chapter_script_revision",
        source_revision_id=second_script.id,
        render_spec=changed_spec,
    )
    second_storage = StorageObject(
        owner_id=seeded.user.id,
        project_id=seeded.project.id,
        storage_backend="local",
        storage_key="script-cache/portrait-v2.png",
        media_type="image/png",
        byte_size=10,
        sha256="b" * 64,
        visibility="release",
        status="active",
    )
    db.add(second_storage)
    db.flush()
    second_version, created = record_asset_version(
        db,
        project_id=seeded.project.id,
        asset_id=first_asset.id,
        source_kind="chapter_script_revision",
        source_revision_id=second_script.id,
        generation_task_id=changed["task"].id,
        storage_object_id=second_storage.id,
        cache_key=changed["cache_key"],
        render_spec=changed_spec,
        cache_material=dict(changed["task"].parameters["cache_material"]),
    )
    assert created is True
    assert second_version.version_no == 2
    for slot in second_slots:
        bind_script_resource_slot(
            db,
            project_id=seeded.project.id,
            slot_id=slot.id,
            asset_version_id=second_version.id,
            script_revision_id=second_script.id,
        )
    db.flush()

    assert {slot.asset_version_id for slot in first_slots} == {first_version.id}
    assert {slot.asset_version_id for slot in second_slots} == {second_version.id}
    assert {
        binding.asset_version_id
        for binding in db.query(AssetBinding).filter_by(
            source_kind="chapter_script_revision", source_id=first_script.id
        )
    } == {first_version.id}
    assert {
        binding.asset_version_id
        for binding in db.query(AssetBinding).filter_by(
            source_kind="chapter_script_revision", source_id=second_script.id
        )
    } == {second_version.id}


def test_failed_script_provider_call_cannot_be_replayed(db_and_factory, monkeypatch):
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-fails-once",
    )
    db.commit()
    adapter = FakeScriptAdapter(fail=True)
    lease = _claim_lease(db, task)
    with pytest.raises(RuntimeError, match="response lost"):
        generate_chapter_script_task(task.id, adapter=adapter, lease=lease)
    with pytest.raises(ProviderCallAlreadyAttempted):
        generate_chapter_script_task(task.id, adapter=adapter, lease=lease)
    assert adapter.calls == 1


def test_chapter_script_revision_rejects_in_place_updates(db_and_factory, monkeypatch):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    revision.generator_version = "mutated"
    with pytest.raises(RuntimeError, match="immutable"):
        db.flush()
    db.rollback()


def test_resource_binding_rejects_mismatched_revision_before_mutation(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    slot = db.query(ScriptResourceSlot).order_by(ScriptResourceSlot.order_index).first()

    with pytest.raises(HTTPException) as wrong_revision:
        bind_script_resource_slot(
            db,
            project_id=seeded.project.id,
            slot_id=slot.id,
            asset_version_id="missing-version",
            script_revision_id="another-revision",
        )

    assert wrong_revision.value.status_code == 404
    assert slot.status == "planned"
    assert slot.asset_version_id is None


def test_final_router_binding_automatically_compiles_and_persists_vngraph(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    slots, versions = _materialize_slot_versions(db, seeded, revision)

    for index, (slot, version) in enumerate(zip(slots, versions, strict=True)):
        bound = bind_resource_slot(
            script_revision_id=revision.id,
            slot_id=slot.id,
            body=ResourceSlotBind(asset_version_id=version.id),
            response=Response(),
            if_match='"1"',
            db=db,
            user=seeded.user,
        )
        compile_tasks = (
            db.query(GenerationTask)
            .filter(GenerationTask.kind == "vngraph.compile")
            .order_by(GenerationTask.created_at)
            .all()
        )
        assert bound.status == "bound"
        assert len(compile_tasks) == (1 if index == len(slots) - 1 else 0)

    compile_task = compile_tasks[0]
    _, graph_revision, created = execute_vn_graph_compile_task(
        db,
        task_id=compile_task.id,
        worker_id="test-auto-compiler",
    )
    db.commit()

    assert created is True
    assert graph_revision.script_revision_id == revision.id
    assert graph_revision.status == "ready"
    graph_head = db.query(VNGraphHead).filter_by(script_revision_id=revision.id).one()
    assert graph_head.current_revision_id is None
    assert graph_head.lock_version == 1
    assert graph_revision.graph_json["Meta"]["binding_manifest_hash"]
    assert not _contains_res(graph_revision.graph_json)


def test_bound_slots_compile_complete_graph_without_tts_degradation(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    _, result, _ = _queue_and_generate(db, factory, seeded, monkeypatch)
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    slots = _materialize_slots(db, seeded, revision)

    compile_task = queue_vn_graph_when_resources_ready(
        db,
        user_id=seeded.user.id,
        project_id=seeded.project.id,
        script_revision_id=revision.id,
    )
    assert compile_task is not None
    db.commit()
    _, graph_revision, created = execute_vn_graph_compile_task(
        db,
        task_id=compile_task.id,
        worker_id="test-compiler",
    )
    db.commit()
    assert created is True
    assert isinstance(graph_revision, VNGraphRevision)
    assert graph_revision.script_revision_id == revision.id
    assert graph_revision.graph_json["Meta"]["text_coverage"]["coverage_ratio"] == 1.0
    assert not _contains_res(graph_revision.graph_json)

    keyframe_slots = [slot for slot in slots if slot.role == "keyframe"]
    keyframe_nodes = [
        node
        for node in graph_revision.graph_json["Nodes"]
        if node.get("NodeType") == 2 and node.get("SubType") == 6
    ]
    assert len(keyframe_nodes) == len(keyframe_slots)
    assert {node["resource_slot_id"] for node in keyframe_nodes} == {
        slot.id for slot in keyframe_slots
    }

    no_tts = load_compile_input(
        db,
        script_revision_id=revision.id,
    )
    first_paragraph = revision.script_json["paragraphs"][0]
    db.add(
        VoiceLine(
            chapter_revision_id=seeded.chapter.id,
            occurrence_id="voice-first-paragraph",
            order_index=0,
            kind=first_paragraph["kind"],
            text=first_paragraph["text"],
            speaker_name=first_paragraph["speaker_name"],
            speaker_character_id=first_paragraph["speaker_character_id"],
            emotion=first_paragraph["emotion"],
            status="planned",
        )
    )
    db.commit()
    with_tts = load_compile_input(
        db,
        script_revision_id=revision.id,
    )
    graph_without_tts = vn_graph_compiler.compile(no_tts)
    graph_with_tts = vn_graph_compiler.compile(with_tts)
    repeated = vn_graph_compiler.compile(replace(with_tts))

    assert _node_shape(graph_without_tts.graph) == _node_shape(graph_with_tts.graph)
    assert [line["Text"]["StringValue"] for line in _paragraph_lines(graph_without_tts.graph)] == [
        paragraph["text"] for paragraph in revision.script_json["paragraphs"]
    ]
    assert graph_with_tts.graph_hash == repeated.graph_hash
    assert graph_with_tts.binding_manifest_hash == repeated.binding_manifest_hash


# --------------------------------------------------------------------- #
# v3：LLM 拆分 + 确定性守护
# --------------------------------------------------------------------- #


def test_llm_segmentation_retry_recovers_from_validation_failure(
    db_and_factory, monkeypatch
):
    """第一次改写原文 → 守护拒绝 → 带错误反馈重试 → 成功。"""
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-retry-once",
    )
    db.commit()
    adapter = FakeScriptAdapter(bad_times=1)
    result = generate_chapter_script_task(
        task.id, adapter=adapter, lease=_claim_lease(db, task)
    )
    db.expire_all()

    assert adapter.calls == 2
    assert len(adapter.feedbacks) == 1
    assert "校验失败" in adapter.feedbacks[0]
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    assert revision.schema_version == "script-ir-v3"
    assert revision.coverage_json["mode"] == "llm-segments-v2"
    assert revision.coverage_json["coverage_ratio"] == 1.0
    # 合并/拆分语义：混合行「阿宁说："走吧。"」被拆成 旁白+对白 两段
    texts = [p["text"] for p in revision.script_json["paragraphs"]]
    assert texts == ["雨落在站台。", "阿宁说：", '"走吧。"', "灯光熄灭。"]
    dialogue = revision.script_json["paragraphs"][2]
    assert dialogue["kind"] == "dialogue"
    assert dialogue["speaker_character_id"] == "character-an"


def test_llm_segmentation_fails_after_three_bad_attempts(db_and_factory, monkeypatch):
    """3 次校验全失败 → 任务失败，不静默回退换行切分。"""
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-retry-exhausted",
    )
    db.commit()
    adapter = FakeScriptAdapter(bad_times=99)
    with pytest.raises(RuntimeError, match="failed validation after 3 attempts"):
        generate_chapter_script_task(task.id, adapter=adapter, lease=_claim_lease(db, task))
    assert adapter.calls == 3
    assert db.query(ChapterScriptRevision).count() == 0


def test_generation_survives_expire_on_commit_sessions(db_and_factory, monkeypatch):
    """回归：生产 SessionLocal 是 expire_on_commit=True。

    旧实现在 provider 调用循环里直接访问已 commit+close 的 ORM 实例属性
    （chapter.content 等），抛 DetachedInstanceError 并把任务打死——
    且发生在 LLM 调用已花费数分钟之后。修复后循环只依赖 session 关闭前
    拷贝的纯值快照。
    """
    db, factory = db_and_factory
    seeded = _seed(db)
    from app.application import chapter_script_service

    production_like = sessionmaker(
        bind=db.get_bind(), expire_on_commit=True, autoflush=False
    )
    monkeypatch.setattr(chapter_script_service, "SessionLocal", production_like)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key="script-expire-commit",
    )
    db.commit()
    adapter = FakeScriptAdapter(bad_times=1)
    result = generate_chapter_script_task(
        task.id, adapter=adapter, lease=_claim_lease(db, task)
    )
    db.expire_all()

    assert adapter.calls == 2
    revision = db.query(ChapterScriptRevision).filter_by(
        id=result.result_refs["chapter_script_revision_id"]
    ).one()
    assert revision.coverage_json["coverage_ratio"] == 1.0
    assert db.query(GenerationTask).filter_by(id=task.id).one().status == "succeeded"


def test_build_script_ir_from_segments_merges_and_splits():
    """守护定位：跨行合并、空行跳过、混合行拆分、逐字符无缝。"""
    from app.services.chapter_script_ir import build_script_ir_from_segments

    content = "  刘忆良推开家门。\n屋里没人。\n\n“忆良：\n爸爸这边有事。”信很简单。\n他说：“我回来啦。”她点头。"
    segments = [
        {"text": "刘忆良推开家门。屋里没人。", "kind": "narration", "scene_key": "s1"},
        {
            "text": "“忆良：爸爸这边有事。”信很简单。",
            "kind": "narration",
            "scene_key": "s1",
        },
        {"text": "他说：", "kind": "narration", "scene_key": "s1"},
        {
            "text": "“我回来啦。”",
            "kind": "dialogue",
            "scene_key": "s1",
            "speaker_character_id": "character-f",
        },
        {"text": "她点头。", "kind": "narration", "scene_key": "s1"},
    ]
    script = build_script_ir_from_segments(
        chapter_revision_id="cr",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="b",
        outline_revision_id="o",
        characters=[{"character_id": "character-f", "name": "父亲"}],
        llm_output={"segments": segments, "scenes": [{"scene_key": "s1", "title": "家"}]},
    )
    assert script["schema_version"] == "script-ir-v3"
    assert script["coverage"]["coverage_ratio"] == 1.0
    assert "".join(span["text"] for span in script["spans"]) == content
    paragraphs = script["paragraphs"]
    assert [p["text"] for p in paragraphs] == [
        "刘忆良推开家门。屋里没人。",
        "“忆良：爸爸这边有事。”信很简单。",
        "他说：",
        "“我回来啦。”",
        "她点头。",
    ]
    # 平铺无缝：段落 source 区间按原文单调递增且不重叠
    starts = [p["source_start"] for p in paragraphs]
    ends = [p["source_end"] for p in paragraphs]
    assert starts == sorted(starts)
    assert all(ends[i] <= starts[i + 1] for i in range(len(starts) - 1))
    assert paragraphs[3]["speaker_name"] == "父亲"


def test_build_script_ir_from_segments_rejects_rewrite_and_gap():
    from app.services.chapter_script_ir import (
        ScriptIRValidationError,
        build_script_ir_from_segments,
    )

    content = "他说：“走吧。”她点头。"
    kwargs = dict(
        chapter_revision_id="cr",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="b",
        outline_revision_id="o",
        characters=[],
    )
    # 改写检测（增字触发"正文已耗尽"或"与原文不一致"任一分支）
    with pytest.raises(ScriptIRValidationError, match="定位失败|不一致"):
        build_script_ir_from_segments(
            llm_output={"segments": [{"text": "他说道：“走吧。”她点头。"}]}, **kwargs
        )
    # 覆盖缺口
    with pytest.raises(ScriptIRValidationError, match="覆盖缺口"):
        build_script_ir_from_segments(
            llm_output={"segments": [{"text": "他说：“走吧。”"}]}, **kwargs
        )
    # 顺序错乱 / 多余片段
    with pytest.raises(ScriptIRValidationError):
        build_script_ir_from_segments(
            llm_output={
                "segments": [
                    {"text": "她说：“回来。”"},
                    {"text": "他说：“走吧。”她点头。"},
                ]
            },
            **kwargs,
        )


def test_overlong_segment_is_split_by_sentence_fuse():
    from app.services.chapter_script_ir import (
        SEGMENT_MAX_TEXT_CHARS,
        build_script_ir_from_segments,
    )

    sentence = "这是很长的一句话，用来撑过单条上限。" # 18 字
    content = (sentence * 40) + "结尾。"
    assert len(sentence * 40) > SEGMENT_MAX_TEXT_CHARS
    script = build_script_ir_from_segments(
        chapter_revision_id="cr",
        chapter_content=content,
        chapter_content_hash=content_hash(content),
        bible_revision_id="b",
        outline_revision_id="o",
        characters=[],
        llm_output={"segments": [{"text": content, "kind": "narration"}]},
    )
    paragraphs = script["paragraphs"]
    assert len(paragraphs) > 1
    assert all(len(p["text"]) <= SEGMENT_MAX_TEXT_CHARS for p in paragraphs)
    assert "".join(p["text"] for p in paragraphs) == "".join(
        ch for ch in content if not ch.isspace()
    )


def _generate_one_script(db, factory, seeded, monkeypatch, key="manual-once"):
    from app.application import chapter_script_service

    monkeypatch.setattr(chapter_script_service, "SessionLocal", factory)
    task, _ = create_chapter_script_generation_task(
        db,
        user_id=seeded.user.id,
        chapter_revision_id=seeded.chapter.id,
        idempotency_key=key,
    )
    db.commit()
    generate_chapter_script_task(
        task.id,
        adapter=FakeScriptAdapter(),
        lease=_claim_lease(db, task),
    )
    db.expire_all()
    return db.query(ChapterScriptRevision).filter_by(
        chapter_revision_id=seeded.chapter.id
    ).one()


def test_manual_script_revision_materializes_draft_without_activation(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    generated = _generate_one_script(db, factory, seeded, monkeypatch)

    draft = deepcopy(generated.script_json)
    draft["scenes"][0]["title"] = "雨骤"
    draft["paragraphs"][0]["emotion"] = "tense"
    revision, appended = create_manual_chapter_script_revision(
        db,
        chapter_revision_id=seeded.chapter.id,
        script_json=draft,
        user_id=seeded.user.id,
    )
    assert revision.generator_version == "manual-edit-v1"
    assert revision.generation_task_id is None
    assert revision.status == "ready"
    assert revision.parent_revision_id == generated.id
    assert revision.coverage_json["coverage_ratio"] == 1.0
    assert revision.script_json["scenes"][0]["title"] == "雨骤"
    assert appended is None
    slots = (
        db.query(ScriptResourceSlot)
        .filter_by(chapter_script_revision_id=revision.id)
        .all()
    )
    assert slots and all(slot.asset_id for slot in slots)

    # 物化不推 head：生成路径本就不激活 head，物化后仍保持未激活。
    head = get_chapter_script_head(db, chapter_revision_id=seeded.chapter.id)
    assert head.revision_id is None

    # 同内容重复物化幂等：不新增修订。
    revision_again, _ = create_manual_chapter_script_revision(
        db,
        chapter_revision_id=seeded.chapter.id,
        script_json=deepcopy(draft),
        user_id=seeded.user.id,
    )
    assert revision_again.id == revision.id


def test_manual_script_revision_rejects_edited_span_text(db_and_factory, monkeypatch):
    db, factory = db_and_factory
    seeded = _seed(db)
    generated = _generate_one_script(db, factory, seeded, monkeypatch)

    draft = deepcopy(generated.script_json)
    draft["spans"][0]["text"] = draft["spans"][0]["text"] + "篡改"
    with pytest.raises(HTTPException) as exc_info:
        create_manual_chapter_script_revision(
            db,
            chapter_revision_id=seeded.chapter.id,
            script_json=draft,
            user_id=seeded.user.id,
        )
    assert exc_info.value.status_code == 422


def test_manual_script_revision_writeback_registers_unknown_speaker(
    db_and_factory, monkeypatch
):
    db, factory = db_and_factory
    seeded = _seed(db)
    generated = _generate_one_script(
        db, factory, seeded, monkeypatch, key="manual-writeback"
    )

    draft = deepcopy(generated.script_json)
    for paragraph in draft["paragraphs"]:
        if paragraph.get("kind") in ("dialogue", "monologue"):
            paragraph["speaker_character_id"] = None
            paragraph["speaker_name"] = "阿新"
            paragraph["unregistered_speaker"] = True
            break
    revision, appended = create_manual_chapter_script_revision(
        db,
        chapter_revision_id=seeded.chapter.id,
        script_json=draft,
        user_id=seeded.user.id,
        auto_register_characters=True,
    )
    assert appended == ["阿新"]
    head_bible = (
        db.query(StoryBibleRevision)
        .filter(StoryBibleRevision.project_id == seeded.project.id)
        .order_by(StoryBibleRevision.revision_no.desc())
        .first()
    )
    names = {
        char["name"]
        for char in (head_bible.content_json or {}).get("characters", [])
    }
    assert "阿新" in names
    assert revision.id  # writeback 不阻断物化


def test_manual_vn_graph_revision_materializes_and_dedupes(
    db_and_factory, monkeypatch
):
    from app.application.vn_graph_service import create_manual_vn_graph_revision

    db, factory = db_and_factory
    seeded = _seed(db)
    generated = _generate_one_script(
        db, factory, seeded, monkeypatch, key="manual-graph"
    )
    slots = _materialize_slots(db, seeded, generated)

    assert slots
    source = load_compile_input(db, script_revision_id=generated.id)
    compiled = vn_graph_compiler.compile(source)

    draft = deepcopy(compiled.graph)
    for node in draft["Nodes"]:
        if str(node.get("Type", "")).startswith("BackGround"):
            node["Data"]["Image"] = "manual-edit.png"
            break
    revision, created = create_manual_vn_graph_revision(
        db,
        script_revision_id=generated.id,
        graph_json=draft,
    )
    assert created is True
    # canonical compiler 版本贯穿；手动来源由 generation_task_id IS NULL 标记。
    assert revision.compiler_version == source.compiler_version
    assert revision.generation_task_id is None
    assert revision.status == "ready"
    assert revision.graph_hash == content_hash(draft)

    graph_head = db.query(VNGraphHead).filter_by(script_revision_id=generated.id).one()
    assert graph_head.current_revision_id is None  # 未激活

    # 同内容重复物化幂等。
    revision_again, created_again = create_manual_vn_graph_revision(
        db,
        script_revision_id=generated.id,
        graph_json=deepcopy(draft),
    )
    assert created_again is False
    assert revision_again.id == revision.id

    # 破坏 Nodes → 422。
    broken = deepcopy(compiled.graph)
    broken.pop("Nodes")
    with pytest.raises(AppError) as exc_info:
        create_manual_vn_graph_revision(
            db,
            script_revision_id=generated.id,
            graph_json=broken,
        )
    assert exc_info.value.status_code == 422
