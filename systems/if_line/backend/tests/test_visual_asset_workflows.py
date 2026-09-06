from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.chapter_script_service import ensure_backfilled_script_revision
from app.application.library_tag_service import create_library_asset_tag_links
from app.application.hashing import content_hash
from app.application.branch_service import create_reading_session
from app.application.revision_service import (
    activate_bible_revision,
    activate_outline_revision,
    create_bible_revision,
    create_chapter_revision,
)
from app.application.story_outline_service import create_story_path_outline_revision
from app.application.storage_service import LocalStorageBackend
from app.application.visual_asset_service import (
    confirm_asset_action,
    create_asset_action,
    search_library_assets,
    upload_visual_asset,
    create_reading_continuation,
    confirm_reading_continuation,
    list_public_continuations,
    prepare_continuation_generation_request,
    prepare_direction_suggestion_request,
    select_public_continuation,
)
from app.database import Base
from app.library_tags import extract_library_facet_tags
from app.models import Asset, Project, User
from app.models_v2 import (
    AssetAction,
    AssetBinding,
    ChapterScriptRevision,
    GenerationTask,
    LibraryAsset,
    ProjectPublication,
    ProjectRelease,
    ReadingContinuation,
    StorageObject,
    StoryPath,
    StoryNode,
    VNGraphHead,
    VNGraphRevision,
)
from app.integrations.llm.base import ModelCallResult
from app.integrations.llm.continuation_adapter import (
    CONTINUATION_PROMPT_VERSION,
    ContinuationLLMRequest,
    LegacyContinuationLLMAdapter,
)
from app.schemas_continuation_generation import GeneratedContinuation
from app.workers.continuation_tasks import execute_continuation_task


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.info["factory"] = factory
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _owner_project(db):
    user = User(
        email="visual-owner@example.com",
        password_hash="hash",
        display_name="visual owner",
        is_active=True,
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="visual project",
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
        content={"world": "visual test"},
        source={"origin": "test"},
        user_id=user.id,
    )
    activate_bible_revision(db, project.id, bible.id)
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=[
            {
                "story_path_chapter_id": None,
                "display_index": 1,
                "title": "第一章",
                "summary": "雨夜城市",
                "characters": [],
                "scene": "城市",
                "visual_keywords": ["雨夜"],
            }
        ],
        user_id=user.id,
    )
    activate_outline_revision(db, project.id, outline.id, approve=True)
    chapter = create_chapter_revision(
        db,
        project_id=project.id,
        chapter_index=1,
        content="雨夜城市。",
        user_id=user.id,
    )
    db.flush()
    ensure_backfilled_script_revision(db, chapter=chapter, activate=True)
    return user, project, chapter


def _activate_test_release(db, release: ProjectRelease) -> None:
    release.manifest_hash = content_hash(release.manifest_json)
    release.published_at = datetime.now(timezone.utc)
    db.add(release)
    db.flush()
    db.add(
        ProjectPublication(
            project_id=release.project_id,
            active_release_id=release.id,
            published_at=release.published_at,
            lock_version=1,
        )
    )
    db.flush()


def _background_graph(background: str) -> dict:
    return {
        "Version": 1,
        "StartNodeIndex": 0,
        "Nodes": [
            {
                "Index": 0,
                "DisplayName": "Start",
                "NodeType": 1,
                "SubType": 6,
                "X": 0,
                "Y": 0,
                "Data": {},
                "Outputs": {},
            },
            {
                "Index": 3,
                "DisplayName": "Background",
                "NodeType": 2,
                "SubType": 3,
                "X": 100,
                "Y": 0,
                "Data": {
                    "BackgroundImage": {
                        "Kind": "String",
                        "StringValue": background,
                    },
                    "SceneId": {
                        "Kind": "String",
                        "StringValue": "scene-0",
                    },
                },
                "Outputs": {},
            },
        ],
    }


def test_continuation_schema_normalizes_provider_taxonomy_labels():
    generated = GeneratedContinuation.model_validate(
        {
            "continuation_text": "刘备拆开密信。",
            "state_delta": {},
            "scene_intent": {
                "background": "夜色中的军营",
                "style": "historical",
                "taxonomy": "古典悬疑场景",
                "characters": [
                    {
                        "name": "刘备",
                        "description": "手持密信",
                        "costume": "汉末布袍",
                    }
                ],
            },
        }
    )

    assert generated.scene_intent.taxonomy == {"description": "古典悬疑场景"}
    assert generated.scene_intent.characters[0].name == "刘备"
    assert generated.scene_intent.characters[0].model_dump()["costume"] == "汉末布袍"


def test_continuation_prompt_requests_substantial_longform_prose():
    prompt = LegacyContinuationLLMAdapter._prompt(
        ContinuationLLMRequest(
            source_hash="a" * 64,
            direction="继续推进冲突",
            release_context={},
            head_context={},
            state={},
            parent_continuation=None,
        )
    )
    assert "800 至 1200 个中文字" in prompt
    assert "至少包含 6 个自然段" in prompt
    assert "不要用提纲、概述或几句话草草结束" in prompt
    assert "用户 direction 是必须落实的创作合同" in prompt
    assert "开头最多用两句话承接上一幕" in prompt
    assert "scene_intent 必须同时服从用户 direction 和实际续写正文" in prompt
    assert "不得把人物、脸或人群画进背景" in prompt
    assert "identity_group 必须是同一人物跨表情稳定不变的身份键" in prompt


def _library_asset(db, user, *, stable_key="rain-city", identity_group=None):
    storage = StorageObject(
        owner_id=user.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"library/{stable_key}.png",
        media_type="image/png",
        byte_size=128,
        sha256=sha256(stable_key.encode("utf-8")).hexdigest(),
        visibility="public",
        status="active",
    )
    db.add(storage)
    db.flush()
    asset = LibraryAsset(
        catalog_version="v1.1",
        stable_key=stable_key,
        asset_type="portrait" if identity_group else "background",
        storage_object_id=storage.id,
        description_cn="雨夜城市" if not identity_group else "林夜开心站立",
        description_en="",
        tags=["雨夜城市"] if not identity_group else ["林夜", "开心"],
        taxonomy={
            "location": "city",
            "weather": "rain",
            "width": 1280,
            "height": 720,
        },
        identity_group=identity_group,
        expression="happy" if identity_group else None,
        pose="standing" if identity_group else None,
        style="anime",
        quality_score=0.9,
        safety_status="approved",
        rights_metadata={"license": "internal"},
        enabled=True,
    )
    db.add(asset)
    db.flush()
    create_library_asset_tag_links(
        db,
        asset=asset,
        specs=extract_library_facet_tags(
            taxonomy=asset.taxonomy,
            style=asset.style,
            identity_group=asset.identity_group,
            expression=asset.expression,
            pose=asset.pose,
        ),
    )
    return asset, storage


def _english_catalog_asset(db, user, *, stable_key, asset_type="background"):
    storage = StorageObject(
        owner_id=user.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"library/{stable_key}.png",
        media_type="image/png",
        byte_size=128,
        sha256=(stable_key.encode("utf-8").hex() + "0" * 64)[:64],
        visibility="public",
        status="active",
    )
    db.add(storage)
    db.flush()
    normalized = stable_key.replace("portrait_", "").replace("bg_", "")
    base, _, expression = normalized.partition("__")
    tokens = [item for item in base.split("_") if item]
    if expression:
        tokens.append(expression)
    asset = LibraryAsset(
        catalog_version="v1.1",
        stable_key=stable_key,
        asset_type=asset_type,
        storage_object_id=storage.id,
        description_cn="",
        description_en=" ".join(tokens),
        tags=tokens,
        taxonomy={"tokens": tokens},
        identity_group=base if asset_type == "portrait" else None,
        expression=expression or None,
        style="cyberpunk" if "cyberpunk" in tokens else None,
        quality_score=1.0,
        safety_status="approved",
        rights_metadata={"license": "internal"},
        enabled=True,
    )
    db.add(asset)
    db.flush()
    create_library_asset_tag_links(
        db,
        asset=asset,
        specs=extract_library_facet_tags(
            taxonomy=asset.taxonomy,
            style=asset.style,
            identity_group=asset.identity_group,
            expression=asset.expression,
            pose=asset.pose,
        ),
    )
    return asset


def _png_bytes(size=(96, 80)):
    image = Image.new("RGBA", size, (20, 40, 80, 128))
    exif = Image.Exif()
    exif[0x010E] = "private description"
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    return output.getvalue()


def test_upload_decodes_strips_metadata_quarantines_and_deduplicates(db, tmp_path, monkeypatch):
    user, project, _chapter = _owner_project(db)
    backend = LocalStorageBackend(tmp_path / "objects")
    monkeypatch.setattr(
        "app.application.visual_asset_service.get_local_storage_backend",
        lambda: backend,
    )
    raw = _png_bytes()
    first = upload_visual_asset(
        db,
        user_id=user.id,
        project_id=project.id,
        raw=raw,
        filename="fake.jpg",
        declared_content_type="image/jpeg",
        asset_type="background",
        target_name="雨夜",
        description="雨夜背景",
        ownership_scope="personal",
        reading_session_id=None,
        target={},
        rights_attested=True,
        idempotency_key="upload-one",
    )
    second = upload_visual_asset(
        db,
        user_id=user.id,
        project_id=project.id,
        raw=raw,
        filename="fake.jpg",
        declared_content_type="image/jpeg",
        asset_type="background",
        target_name="雨夜",
        description="雨夜背景",
        ownership_scope="personal",
        reading_session_id=None,
        target={},
        rights_attested=True,
        idempotency_key="upload-two",
    )
    db.flush()

    assert first["media_type"] == "image/png"
    assert first["status"] == "quarantined"
    assert first["width"] == 96 and first["height"] == 80
    assert first["has_alpha"] is True
    assert second["storage_object_id"] == first["storage_object_id"]
    assert second["reused_storage_object"] is True
    assert db.query(StorageObject).filter_by(project_id=project.id).count() == 1

    stored = db.query(StorageObject).filter_by(id=first["storage_object_id"]).one()
    with Image.open(backend.resolve_key(stored.storage_key)) as sanitized:
        assert len(sanitized.getexif()) == 0


def test_upload_rejects_small_and_broken_images(db, tmp_path, monkeypatch):
    user, project, _chapter = _owner_project(db)
    backend = LocalStorageBackend(tmp_path / "objects")
    monkeypatch.setattr(
        "app.application.visual_asset_service.get_local_storage_backend",
        lambda: backend,
    )
    with pytest.raises(HTTPException) as small:
        upload_visual_asset(
            db,
            user_id=user.id,
            project_id=project.id,
            raw=_png_bytes((32, 32)),
            filename="small.png",
            declared_content_type="image/png",
            asset_type="background",
            target_name="small",
            description="",
            ownership_scope="personal",
            reading_session_id=None,
            target={},
            rights_attested=True,
            idempotency_key="small",
        )
    assert small.value.status_code == 422

    with pytest.raises(HTTPException) as broken:
        upload_visual_asset(
            db,
            user_id=user.id,
            project_id=project.id,
            raw=b"not an image",
            filename="broken.png",
            declared_content_type="image/png",
            asset_type="background",
            target_name="broken",
            description="",
            ownership_scope="personal",
            reading_session_id=None,
            target={},
            rights_attested=True,
            idempotency_key="broken",
        )
    assert broken.value.status_code == 422


def test_natural_language_match_and_identity_group_lock(db):
    user, _project, _chapter = _owner_project(db)
    background, _ = _library_asset(db, user)
    portrait, _ = _library_asset(db, user, stable_key="linye-happy", identity_group="linye")
    _other, _ = _library_asset(db, user, stable_key="other-happy", identity_group="other")

    matches, total = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="雨夜城市",
        asset_type="background",
        style="anime",
        identity_group=None,
        filters={},
        limit=5,
        offset=0,
    )
    assert total == 1
    assert matches[0]["id"] == background.id
    assert matches[0]["confidence"] == "high"
    assert set(matches[0]["score_breakdown"]) == {
        "semantic",
        "taxonomy",
        "style",
        "quality",
        "base_score",
        "repeat_penalty",
    }

    portraits, portrait_total = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="开心站立",
        asset_type="portrait",
        style=None,
        identity_group="linye",
        filters={},
        limit=5,
        offset=0,
    )
    assert portrait_total == 1
    assert portraits[0]["id"] == portrait.id


def test_recent_exact_repeat_is_softly_downranked(db):
    user, _project, _chapter = _owner_project(db)
    first, first_storage = _library_asset(db, user, stable_key="rain-city-a")
    second, _second_storage = _library_asset(db, user, stable_key="rain-city-b")

    initial, _ = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="雨夜城市",
        asset_type="background",
        style="anime",
        identity_group=None,
        filters={},
        limit=5,
        offset=0,
    )
    repeated_id = initial[0]["id"]
    repeated_storage_id = initial[0]["storage_object_id"]
    assert repeated_id in {first.id, second.id}

    varied, _ = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="雨夜城市",
        asset_type="background",
        style="anime",
        identity_group=None,
        filters={},
        limit=5,
        offset=0,
        recent_library_asset_ids=[repeated_id],
        recent_storage_object_ids=[repeated_storage_id],
    )

    assert varied[0]["id"] != repeated_id
    repeated = next(item for item in varied if item["id"] == repeated_id)
    assert repeated["score_breakdown"]["repeat_penalty"] > 0
    assert "recent_exact_repeat" in repeated["conflicts"]
    assert first_storage.id in {item["storage_object_id"] for item in varied}


def test_agent_explicit_empty_required_facets_do_not_fall_back_to_taxonomy(db):
    user, project, chapter = _owner_project(db)
    _library_asset(db, user)

    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=project.id,
        mode="agent_compose",
        target={
            "source_kind": "chapter_revision",
            "source_id": chapter.id,
            "chapter_index": 1,
            "asset_slot": "BackgroundImage",
            "role": "background",
        },
        input_data={
            "query": "rain city",
            "asset_type": "background",
            "taxonomy": {"location": "not-in-the-library"},
            "required_facets": {},
            "max_assets": 1,
        },
        idempotency_key="agent-explicit-empty-required-facets",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        agent_enabled=True,
    )

    assert action.origin == "library"
    assert action.result_asset_version_ids


def test_direct_portrait_action_queues_llm_rewrite_and_forced_alpha_contract(db):
    user, project, chapter = _owner_project(db)
    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=project.id,
        mode="direct_generate",
        target={
            "source_kind": "chapter_revision",
            "source_id": chapter.id,
            "chapter_index": 1,
            "asset_slot": "TachiIamge",
            "role": "portrait",
        },
        input_data={
            "asset_type": "portrait",
            "target_name": "林夜",
            "prompt": (
                "transparent full-body sprite, wearing default, "
                "standing pose, upright posture"
            ),
            # Callers cannot relabel a portrait as if alpha postprocessing/QC
            # were disabled when the renderer always executes those contracts.
            "postprocess_version": "none-v1",
            "validator_version": "none-v1",
        },
        idempotency_key="direct-portrait-alpha-contract",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        agent_enabled=False,
    )

    task = db.query(GenerationTask).filter(GenerationTask.id == action.generation_task_id).one()
    render_spec = dict(task.parameters["render_spec"])
    # Queue stores the user's draft; the worker passes it through the LLM and
    # persists the resulting authoritative final_prompt after rendering.
    prompt = str(render_spec["prompt"]).lower()
    assert "portrait presentation contract" not in prompt
    assert "transparent full-body sprite" in prompt
    assert render_spec["prompt_template_version"] == "portrait-llm-authoritative-v3-subject-first"
    assert render_spec["width"] == 768
    assert render_spec["height"] == 1280
    assert render_spec["postprocess_version"] == "portrait-natural-alpha-v3-age"
    assert render_spec["validator_version"] == "portrait-alpha-v1"
    assert task.parameters["cache_material"]["postprocess_version"] == "portrait-natural-alpha-v3-age"


def test_portrait_variant_rotates_only_inside_same_identity_group(db):
    user, _project, _chapter = _owner_project(db)
    happy = _english_catalog_asset(
        db,
        user,
        stable_key="portrait_linye__happy",
        asset_type="portrait",
    )
    sad = _english_catalog_asset(
        db,
        user,
        stable_key="portrait_linye__sad",
        asset_type="portrait",
    )
    _english_catalog_asset(
        db,
        user,
        stable_key="portrait_other__happy",
        asset_type="portrait",
    )

    initial, _ = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="linye",
        asset_type="portrait",
        style=None,
        identity_group="linye",
        filters={},
        limit=5,
        offset=0,
    )
    repeated = initial[0]
    varied, _ = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query="linye",
        asset_type="portrait",
        style=None,
        identity_group="linye",
        filters={},
        limit=5,
        offset=0,
        recent_library_asset_ids=[repeated["id"]],
        recent_storage_object_ids=[repeated["storage_object_id"]],
    )

    assert {initial[0]["id"], varied[0]["id"]} == {happy.id, sad.id}
    assert all(item["identity_group"] == "linye" for item in varied)


@pytest.mark.parametrize(
    ("query", "asset_type", "expected_key"),
    [
        ("雨夜的城市街道", "background", "bg_city_street_rain"),
        ("夜晚的小巷", "background", "bg_alley_night"),
        ("赛博朋克黑客正在专注思考", "portrait", "portrait_cyberpunk_hacker__focused"),
    ],
)
def test_chinese_query_expands_to_frozen_catalog_terms(db, query, asset_type, expected_key):
    user, _project, _chapter = _owner_project(db)
    expected = _english_catalog_asset(db, user, stable_key=expected_key, asset_type=asset_type)
    _english_catalog_asset(
        db,
        user,
        stable_key="bg_school_day" if asset_type == "background" else "portrait_historical_princess__serious",
        asset_type=asset_type,
    )

    matches, total = search_library_assets(
        db,
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        query=query,
        asset_type=asset_type,
        style=None,
        identity_group=None,
        filters={},
        limit=5,
        offset=0,
    )

    assert total == 2
    assert matches[0]["id"] == expected.id
    assert matches[0]["stable_key"] == expected_key


def test_library_action_is_idempotent_and_confirm_creates_new_graph_revision(db):
    user, project, chapter = _owner_project(db)
    script = db.query(ChapterScriptRevision).filter_by(chapter_revision_id=chapter.id).one()
    library, library_storage = _library_asset(db, user)
    base_graph = _background_graph("")
    base_manifest = {
        "manifest_version": "vngraph-binding-v1",
        "project_id": project.id,
        "chapter_index": 1,
        "chapter_revision": {
            "id": chapter.id,
            "content_hash": chapter.content_hash,
        },
        "script_revision": {
            "id": script.id,
            "script_hash": script.script_hash,
        },
        "asset_bindings": [],
        "voice_line_versions": [],
        "versions": {
            "schema": "1",
            "compiler": "test",
            "tachi_policy": "test",
        },
    }
    base = VNGraphRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        revision_no=1,
        binding_manifest=base_manifest,
        binding_manifest_hash=content_hash(base_manifest),
        source_manifest_hash=content_hash(base_manifest),
        graph_hash=content_hash(base_graph),
        graph_json=base_graph,
        schema_version="1",
        compiler_version="test",
        tachi_policy_version="test",
        status="complete",
    )
    db.add(base)
    db.flush()
    db.add(
        VNGraphHead(
            project_id=project.id,
            chapter_index=1,
            script_revision_id=script.id,
            current_revision_id=base.id,
        )
    )
    db.flush()
    target = {
        "source_kind": "vn_graph_revision",
        "source_id": base.id,
        "chapter_index": 1,
        "node_index": 3,
        "node_key": "scene-0",
        "asset_slot": "BackgroundImage",
        "role": "background",
        "graph_revision_id": base.id,
        "graph_hash": base.graph_hash,
    }
    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=project.id,
        mode="library_select",
        target=target,
        input_data={"library_asset_id": library.id},
        idempotency_key="library-select-one",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        agent_enabled=False,
    )
    replay = create_asset_action(
        db,
        user_id=user.id,
        project_id=project.id,
        mode="library_select",
        target=target,
        input_data={"library_asset_id": library.id},
        idempotency_key="library-select-one",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        agent_enabled=False,
    )
    assert replay.id == action.id
    assert action.status == "awaiting_confirmation"
    assert action.origin == "library"
    assert action.vngraph_patch[0]["value"] == {
        "Kind": "String",
        "StringValue": f"/api/media/{library_storage.id}",
    }

    confirmed_graph = _background_graph(f"/api/media/{library_storage.id}")
    tampered_graph = _background_graph("/api/media/not-frozen")
    with pytest.raises(HTTPException) as tampered:
        confirm_asset_action(
            db,
            action=action,
            graph_json=tampered_graph,
            result_graph_hash=content_hash(tampered_graph),
        )
    assert tampered.value.status_code == 409
    assert tampered.value.detail["code"] == "vngraph.derivation_invalid"
    other_project = Project(
        owner_id=user.id,
        title="other visual project",
        story_start="start",
        story_end="end",
    )
    db.add(other_project)
    db.flush()
    library_storage.project_id = other_project.id
    db.flush()
    with pytest.raises(HTTPException) as cross_project:
        confirm_asset_action(
            db,
            action=action,
            graph_json=confirmed_graph,
            result_graph_hash=content_hash(confirmed_graph),
        )
    assert cross_project.value.status_code == 409
    assert "storage belongs to another project" in str(cross_project.value.detail)

    library_storage.project_id = None
    db.flush()
    confirm_asset_action(
        db,
        action=action,
        graph_json=confirmed_graph,
        result_graph_hash=content_hash(confirmed_graph),
    )
    db.flush()
    head = db.query(VNGraphHead).filter_by(project_id=project.id, chapter_index=1).one()
    assert action.status == "applied"
    assert head.current_revision_id != base.id
    assert db.query(VNGraphRevision).count() == 2
    assert db.query(AssetBinding).filter_by(source_id=head.current_revision_id).count() == 1
    child = db.query(VNGraphRevision).filter_by(id=head.current_revision_id).one()
    patch_binding = child.binding_manifest["asset_bindings"][-1]
    assert child.binding_manifest_hash == content_hash(child.binding_manifest)
    assert child.binding_manifest["derivation"]["parent_revision_id"] == base.id
    assert child.binding_manifest["derivation"]["patch"] == action.vngraph_patch
    assert patch_binding["asset_version_id"] == action.result_asset_version_ids[0]
    assert patch_binding["asset_version_hash"]


def test_graph_hash_conflict_never_overwrites_newer_edit(db):
    user, project, chapter = _owner_project(db)
    script = db.query(ChapterScriptRevision).filter_by(chapter_revision_id=chapter.id).one()
    library, _ = _library_asset(db, user)
    base_graph = _background_graph("")
    base = VNGraphRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        revision_no=1,
        source_manifest_hash="c" * 64,
        graph_hash=content_hash(base_graph),
        graph_json=base_graph,
        schema_version="1",
        compiler_version="test",
        tachi_policy_version="test",
        status="complete",
    )
    db.add(base)
    db.flush()
    head = VNGraphHead(
        project_id=project.id,
        chapter_index=1,
        script_revision_id=script.id,
        current_revision_id=base.id,
    )
    db.add(head)
    db.flush()
    target = {
        "source_kind": "vn_graph_revision",
        "source_id": base.id,
        "chapter_index": 1,
        "node_index": 3,
        "node_key": "scene-0",
        "asset_slot": "BackgroundImage",
        "role": "background",
        "graph_revision_id": base.id,
        "graph_hash": base.graph_hash,
    }
    action = create_asset_action(
        db,
        user_id=user.id,
        project_id=project.id,
        mode="library_select",
        target=target,
        input_data={"library_asset_id": library.id},
        idempotency_key="conflict",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
        agent_enabled=False,
    )
    newer_graph = _background_graph("v2")
    newer = VNGraphRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        parent_revision_id=base.id,
        revision_no=2,
        source_manifest_hash="d" * 64,
        graph_hash=content_hash(newer_graph),
        graph_json=newer_graph,
        schema_version="1",
        compiler_version="test",
        tachi_policy_version="test",
        status="complete",
    )
    db.add(newer)
    db.flush()
    head.current_revision_id = newer.id
    with pytest.raises(HTTPException) as conflict:
        confirm_asset_action(
            db,
            action=action,
            graph_json=_background_graph("v3"),
            result_graph_hash=content_hash(_background_graph("v3")),
        )
    assert conflict.value.status_code == 409
    assert head.current_revision_id == newer.id


@pytest.mark.asyncio
async def test_reader_continuation_worker_matches_library_and_freezes_session_overlay(db):
    user, project, chapter = _owner_project(db)
    _library_asset(db, user)
    _library_asset(
        db,
        user,
        stable_key="lin-night-portrait",
        identity_group="missing-exact-identity",
    )
    start = StoryNode(
        project_id=project.id,
        node_type="paragraph",
        content_revision_id=chapter.id,
        payload={"text": "故事开始"},
    )
    db.add(start)
    db.flush()
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=chapter.bible_revision_id,
        outline_revision_id=chapter.outline_revision_id,
        manifest_json={"story": {"start_node_id": start.id, "nodes": [{"id": start.id}]}},
        manifest_hash="",
        created_by=user.id,
    )
    _activate_test_release(db, release)
    reading = create_reading_session(
        db,
        user_id=user.id,
        project_id=project.id,
        release_id=release.id,
        initial_state={"chapter": 1},
    )
    continuation = create_reading_continuation(
        db,
        session_id=reading.id,
        user_id=user.id,
        direction="去雨夜城市寻找线索",
        visual_mode="system_generate",
        uploaded_asset_version_ids=[],
        max_generated_assets=3,
        parent_continuation_id=None,
        idempotency_key="reader-direction-one",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
    )
    db.commit()
    assert continuation.status == "processing"
    assert continuation.generation_task_id

    class FakeProvider:
        async def generate_continuation(self, request):
            assert request.direction == "去雨夜城市寻找线索"
            return ModelCallResult(
                data={
                    "continuation_text": "林夜撑伞走进雨夜城市，霓虹倒映在积水中。",
                    "state_delta": {"found_clue": True},
                    "scene_intent": {
                        "background": (
                            "雨夜城市。林夜撑伞站在路口，远处士兵身影穿过积水。"
                            "霓虹倒映在积水中。"
                        ),
                        "style": "anime",
                        "taxonomy": {},
                        "characters": [
                            {
                                "name": "林夜",
                                "description": "林夜在雨中警惕回望",
                                "identity_group": "missing-exact-identity",
                                "expression": "fear",
                                "pose": "turning",
                            }
                        ],
                    },
                },
                raw_text="{}",
                provider="fake",
                model="fake-v1",
                provider_request_id="fake-request",
                input_tokens=10,
                output_tokens=20,
                latency_ms=1,
                prompt_version=CONTINUATION_PROMPT_VERSION,
            )

    # Match production SessionLocal for the worker path. The task runner must
    # explicitly flush event sequence allocations when autoflush is disabled.
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False, autoflush=False)
    result_id = await execute_continuation_task(
        continuation.generation_task_id,
        provider=FakeProvider(),
        worker_id="continuation-test-worker",
        session_factory=factory,
    )
    assert result_id == continuation.id
    db.expire_all()
    saved = db.query(ReadingContinuation).filter_by(id=continuation.id).one()
    assert saved.status == "preview_ready"
    assert "雨夜城市" in saved.continuation_text
    assert saved.scene_manifest_id
    assert saved.asset_action_id
    assert len(saved.frozen_asset_version_ids) == 2
    assert saved.vngraph_patch[0]["op"] == "add_continuation_text"
    action = db.query(AssetAction).filter_by(id=saved.asset_action_id).one()
    assert "去雨夜城市寻找线索" in action.input["query"]
    assert action.input["direction_context"] == "去雨夜城市寻找线索"
    assert "雨夜城市" in action.input["generation_prompt"]
    assert "霓虹倒映在积水中" in action.input["generation_prompt"]
    assert "林夜" not in action.input["generation_prompt"]
    assert "士兵" not in action.input["generation_prompt"]
    assert "身影" not in action.input["generation_prompt"]
    assert "去雨夜城市寻找线索" not in action.input["generation_prompt"]
    assert "画面主体只由建筑、景观、地形、静态道具" in action.input["generation_prompt"]
    assert "person, people, human" in action.input["negative_prompt"]
    assert action.input["negative_prompt_version"] == "visual-novel-empty-environment-v2"
    assert action.input["generation_prompt_version"] == "visual-novel-empty-environment-v2"
    assert action.input["generation_source_policy"].endswith("without_raw_direction")

    frozen_release_hash = release.manifest_hash
    confirm_reading_continuation(
        db,
        continuation=saved,
        user_id=user.id,
        expected_lock_version=reading.lock_version,
    )
    db.flush()
    assert saved.status == "confirmed"
    assert reading.lock_version == 2
    assert release.manifest_hash == frozen_release_hash
    suggestion_request = prepare_direction_suggestion_request(
        db,
        session_id=reading.id,
        user_id=user.id,
    )
    assert suggestion_request.state == {"chapter": 1}
    assert suggestion_request.latest_continuation == {
        "selected_node_id": saved.id,
        "ancestry": [
            {
                "id": saved.id,
                "direction": "去雨夜城市寻找线索",
                "continuation_text": "林夜撑伞走进雨夜城市，霓虹倒映在积水中。",
                "state_delta": {"found_clue": True},
            }
        ],
    }
    public_tree = list_public_continuations(
        db,
        project_id=project.id,
        release_id=release.id,
        chapter_number=1,
    )
    assert [node["id"] for node in public_tree["nodes"]] == [saved.id]
    assert public_tree["nodes"][0]["selection_count"] == 1
    assert len(public_tree["nodes"][0]["assets"]) == 2
    assert all(
        asset["media_url"].startswith("/api/media/")
        for asset in public_tree["nodes"][0]["assets"]
    )

    reader = User(
        email="public-reader@example.com",
        password_hash="hash",
        display_name="public reader",
        is_active=True,
        quota_total=1000,
        quota_daily=1000,
    )
    db.add(reader)
    db.flush()
    other_session = create_reading_session(
        db,
        user_id=reader.id,
        project_id=project.id,
        release_id=release.id,
        initial_state={"chapter_number": 1},
    )
    select_public_continuation(
        db,
        session_id=other_session.id,
        user_id=reader.id,
        continuation_id=saved.id,
    )
    child = create_reading_continuation(
        db,
        session_id=other_session.id,
        user_id=reader.id,
        direction="沿雨夜线索继续追查",
        visual_mode="system_generate",
        uploaded_asset_version_ids=[],
        max_generated_assets=0,
        parent_continuation_id=saved.id,
        idempotency_key="public-reader-child",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
    )
    task = db.query(GenerationTask).filter(GenerationTask.id == child.generation_task_id).one()
    request = prepare_continuation_generation_request(db, task)
    assert request.parent_continuation is not None
    assert request.parent_continuation["selected_node_id"] == saved.id
    assert request.parent_continuation["ancestry"][0]["continuation_text"] == saved.continuation_text

    publication = db.query(ProjectPublication).filter_by(project_id=project.id).one()
    publication.active_release_id = None
    publication.published_at = None
    publication.lock_version += 1
    db.flush()
    with pytest.raises(ValueError, match="source snapshot is missing"):
        prepare_continuation_generation_request(db, task)
    with pytest.raises(HTTPException) as no_longer_public:
        list_public_continuations(
            db,
            project_id=project.id,
            release_id=release.id,
            chapter_number=1,
        )
    assert no_longer_public.value.status_code == 404


@pytest.mark.asyncio
async def test_reader_continuation_without_catalog_match_queues_direct_generation(db):
    user, project, chapter = _owner_project(db)
    start = StoryNode(
        project_id=project.id,
        node_type="paragraph",
        content_revision_id=chapter.id,
        payload={"text": "故事开始"},
    )
    db.add(start)
    db.flush()
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=chapter.bible_revision_id,
        outline_revision_id=chapter.outline_revision_id,
        manifest_json={"story": {"start_node_id": start.id, "nodes": [{"id": start.id}]}},
        manifest_hash="",
        created_by=user.id,
    )
    _activate_test_release(db, release)
    reading = create_reading_session(
        db,
        user_id=user.id,
        project_id=project.id,
        release_id=release.id,
        initial_state={"chapter": 1},
    )
    continuation = create_reading_continuation(
        db,
        session_id=reading.id,
        user_id=user.id,
        direction="去没有图库素材的陌生场景",
        visual_mode="system_generate",
        uploaded_asset_version_ids=[],
        max_generated_assets=1,
        parent_continuation_id=None,
        idempotency_key="reader-direct-visual-fallback",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
    )
    db.commit()

    class FakeProvider:
        async def generate_continuation(self, request):
            return ModelCallResult(
                data={
                    "continuation_text": "刘备举灯走入从未见过的石室。",
                    "state_delta": {"entered_stone_room": True},
                    "scene_intent": {
                        "background": "不存在于素材库的汉末地下石室",
                        "style": "historical",
                        "taxonomy": {
                            "location": "impossible-test-location",
                            "description": "preserve-generation-only-taxonomy",
                        },
                        "characters": [],
                    },
                },
                raw_text="{}",
                provider="fake",
                model="fake-v1",
                provider_request_id="fake-direct-fallback",
                input_tokens=10,
                output_tokens=20,
                latency_ms=1,
                prompt_version=CONTINUATION_PROMPT_VERSION,
            )

    result_id = await execute_continuation_task(
        continuation.generation_task_id,
        provider=FakeProvider(),
        worker_id="continuation-direct-fallback-worker",
        session_factory=sessionmaker(
            bind=db.get_bind(),
            expire_on_commit=False,
            autoflush=False,
        ),
    )

    assert result_id == continuation.id
    db.expire_all()
    saved = db.query(ReadingContinuation).filter_by(id=continuation.id).one()
    action = db.query(AssetAction).filter_by(id=saved.asset_action_id).one()
    assert saved.status == "preview_ready"
    assert saved.vngraph_patch[0]["op"] == "add_continuation_text"
    assert action.mode == "direct_generate"
    assert action.selection_method == "system_generate"
    assert action.status == "processing"
    assert action.generation_task_id
    generated = (
        db.query(Asset)
        .filter(Asset.logical_key == f"generated:{action.id}:background")
        .one()
    )
    assert generated.taxonomy_json == {
        "location": "impossible-test-location",
        "description": "preserve-generation-only-taxonomy",
    }


@pytest.mark.asyncio
async def test_reader_continuation_supports_text_only_generation(db):
    user, project, chapter = _owner_project(db)
    start = StoryNode(
        project_id=project.id,
        node_type="paragraph",
        content_revision_id=chapter.id,
        payload={"text": "故事开始"},
    )
    db.add(start)
    db.flush()
    release = ProjectRelease(
        project_id=project.id,
        version=1,
        status="published",
        bible_revision_id=chapter.bible_revision_id,
        outline_revision_id=chapter.outline_revision_id,
        manifest_json={"story": {"start_node_id": start.id, "nodes": [{"id": start.id}]}},
        manifest_hash="",
        created_by=user.id,
    )
    _activate_test_release(db, release)
    reading = create_reading_session(
        db,
        user_id=user.id,
        project_id=project.id,
        release_id=release.id,
        initial_state={"chapter": 1},
    )
    continuation = create_reading_continuation(
        db,
        session_id=reading.id,
        user_id=user.id,
        direction="只生成文字",
        visual_mode="system_generate",
        uploaded_asset_version_ids=[],
        max_generated_assets=0,
        parent_continuation_id=None,
        idempotency_key="reader-text-only",
        catalog_version="v1.1",
        matcher_version="hash-ngram-v1",
    )
    db.commit()

    class FakeProvider:
        async def generate_continuation(self, request):
            return ModelCallResult(
                data={
                    "continuation_text": "刘备整顿衣冠，继续赶路。",
                    "state_delta": {"continued": True},
                    "scene_intent": {
                        "background": "古代驿道",
                        "style": "historical",
                        "taxonomy": {},
                        "characters": [],
                    },
                },
                raw_text="{}",
                provider="fake",
                model="fake-v1",
                provider_request_id="fake-text-only",
                input_tokens=10,
                output_tokens=20,
                latency_ms=1,
                prompt_version=CONTINUATION_PROMPT_VERSION,
            )

    result_id = await execute_continuation_task(
        continuation.generation_task_id,
        provider=FakeProvider(),
        worker_id="continuation-text-only-worker",
        session_factory=sessionmaker(
            bind=db.get_bind(),
            expire_on_commit=False,
            autoflush=False,
        ),
    )
    assert result_id == continuation.id
    db.expire_all()
    saved = db.query(ReadingContinuation).filter_by(id=continuation.id).one()
    assert saved.status == "preview_ready"
    assert saved.frozen_asset_version_ids == []
    assert saved.asset_action_id is None
    assert saved.vngraph_patch == [
        {
            "op": "add_continuation_text",
            "text": "刘备整顿衣冠，继续赶路。",
            "state_delta": {"continued": True},
        }
    ]
