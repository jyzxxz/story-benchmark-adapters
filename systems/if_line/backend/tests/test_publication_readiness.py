from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.hashing import content_hash
from app.application.publication_readiness_service import (
    PUBLICATION_FINGERPRINT_VERSION,
    calculate_publication_readiness,
)
from app.application.vn_graph_manifest import asset_version_reference
from app.core.errors import AppError
from app.database import Base
from app.models import Asset, Project, User
from app.models_v2 import (
    AssetAction,
    AssetVersion,
    BranchCandidate,
    CandidateSetRevision,
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    ChapterSlot,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    ScriptResourceSlot,
    StateSnapshot,
    StorageObject,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    VNGraphHead,
    VNGraphRevision,
    VoiceLine,
)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _outline_item(path_chapter_id: str | None, *, title: str) -> dict:
    return {
        "story_path_chapter_id": path_chapter_id,
        "display_index": 1,
        "title": title,
        "summary": f"{title} summary",
        "conflict": None,
        "characters": ["A"],
        "scene": "station",
        "emotion": "tense",
        "visual_keywords": ["rain"],
    }


def _add_outline(
    db,
    *,
    project_id: int,
    path: StoryPath,
    bible: StoryBibleRevision,
    placement: StoryPathChapter,
    title: str,
) -> OutlineRevision:
    # Activation fills the PathChapter UUID after hashing a newly generated row.
    original = _outline_item(None, title=title)
    outline = OutlineRevision(
        project_id=project_id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash=content_hash({"path": path.id, "title": title}),
        content_hash=content_hash([original]),
        status="ready",
    )
    db.add(outline)
    db.flush()
    current = {**original, "story_path_chapter_id": placement.id}
    db.add(
        OutlineChapter(
            outline_revision_id=outline.id,
            chapter_index=1,
            story_path_chapter_id=placement.id,
            display_index=1,
            title=current["title"],
            summary=current["summary"],
            conflict=current["conflict"],
            characters=current["characters"],
            scene=current["scene"],
            emotion=current["emotion"],
            visual_keywords=current["visual_keywords"],
            content_hash=content_hash(original),
        )
    )
    db.add(
        StoryPathOutlineHead(
            story_path_id=path.id,
            current_revision_id=outline.id,
            lock_version=1,
        )
    )
    db.flush()
    return outline


def _graph_manifest(
    *,
    project_id: int,
    chapter: ChapterRevision,
    script: ChapterScriptRevision,
    asset_bindings: list[dict] | None = None,
) -> dict:
    return {
        "manifest_version": "vngraph-binding-v1",
        "project_id": project_id,
        "chapter_index": chapter.chapter_index,
        "chapter_revision": {
            "id": chapter.id,
            "content_hash": chapter.content_hash,
        },
        "script_revision": {
            "id": script.id,
            "script_hash": script.script_hash,
        },
        "asset_bindings": asset_bindings or [],
        "voice_line_versions": [],
        "versions": {
            "schema": "1",
            "compiler": "deterministic-v2",
            "tachi_policy": "speaker-focus-v2",
        },
    }


def _seed_ready_project(db, *, with_resource: bool = False) -> dict:
    user = User(
        email=f"readiness-{uuid4()}@example.com",
        password_hash="hash",
        display_name="Readiness owner",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Frozen routes",
        characters=[{"name": "A"}],
        story_start="Start",
        story_end="End",
        style="cinematic",
        source_work=None,
        pace="medium",
        extra_requirements="Keep branch choices explicit",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Root", status="active")
    db.add(path)
    db.flush()
    state = StateSnapshot(
        project_id=project.id,
        state_json={"route": "root"},
        state_hash=content_hash({"route": "root"}),
    )
    db.add(state)
    bible_json = {"worldview": "A frozen world"}
    bible = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash=content_hash({"source": "test"}),
        content_hash=content_hash(bible_json),
        content_json=bible_json,
        status="ready",
        created_by=user.id,
    )
    db.add(bible)
    db.flush()
    slot = ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
    db.add(slot)
    db.flush()
    placement = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=slot.id,
        display_index=1,
    )
    db.add(placement)
    db.flush()
    outline = _add_outline(
        db,
        project_id=project.id,
        path=path,
        bible=bible,
        placement=placement,
        title="Arrival",
    )
    db.add(
        ProjectContentHead(
            project_id=project.id,
            current_bible_revision_id=bible.id,
            lock_version=1,
        )
    )
    context = {
        "project_id": project.id,
        "story_path_id": path.id,
        "path_chapter_id": placement.id,
        "bible_revision_id": bible.id,
        "outline_revision_id": outline.id,
        "state_snapshot_id": state.id,
        "fork": None,
        "fork_choice": None,
        "ancestors": [],
    }
    chapter_text = "A arrives at the station in the rain."
    chapter = ChapterRevision(
        project_id=project.id,
        chapter_slot_id=slot.id,
        created_for_story_path_id=path.id,
        chapter_index=1,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        state_snapshot_id=state.id,
        revision_no=1,
        source_hash=content_hash({"context": context}),
        context_manifest=context,
        context_hash=content_hash(context),
        content_hash=content_hash(chapter_text),
        content=chapter_text,
        status="ready",
        created_by=user.id,
    )
    db.add(chapter)
    db.flush()
    placement.current_revision_id = chapter.id
    script_json = {"schema_version": "script-ir-v1", "paragraphs": []}
    script = ChapterScriptRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=1,
        source_hash=content_hash({"chapter_revision_id": chapter.id}),
        script_hash=content_hash(script_json),
        script_json=script_json,
        coverage_json={"coverage_ratio": 1.0},
        schema_version="script-ir-v1",
        generator_version="readiness-test-v1",
        status="ready",
        created_by=user.id,
    )
    db.add(script)
    db.flush()
    db.add(
        ChapterScriptHead(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapter.id,
            current_revision_id=script.id,
            lock_version=1,
        )
    )

    storage = None
    resource_slot = None
    asset_binding_items: list[dict] = []
    if with_resource:
        asset = Asset(
            project_id=project.id,
            chapter_index=1,
            asset_type="background",
            target_name="Rain station",
            prompt="rain station",
            status="completed",
        )
        db.add(asset)
        db.flush()
        storage = StorageObject(
            owner_id=user.id,
            project_id=project.id,
            storage_backend="local",
            storage_key=f"readiness/{uuid4()}.png",
            media_type="image/png",
            byte_size=12,
            sha256="a" * 64,
            visibility="private",
            status="active",
        )
        db.add(storage)
        db.flush()
        version = AssetVersion(
            asset_id=asset.id,
            source_kind="chapter_script_revision",
            source_revision_id=script.id,
            source_hash=script.script_hash,
            storage_object_id=storage.id,
            version_no=1,
            cache_key=f"readiness-{uuid4()}",
            prompt_hash="b" * 64,
            prompt_version="v1",
            safety_status="passed",
        )
        db.add(version)
        db.flush()
        resource_slot = ScriptResourceSlot(
            project_id=project.id,
            chapter_script_revision_id=script.id,
            slot_key="scene:station:background",
            role="background",
            scene_id="station",
            order_index=0,
            required=True,
            status="bound",
            asset_id=asset.id,
            asset_version_id=version.id,
            spec_json={"weather": "rain"},
        )
        db.add(resource_slot)
        db.flush()
        asset_binding_items.append(
            {
                "resource_slot_id": resource_slot.id,
                "binding_id": resource_slot.id,
                "source_kind": "chapter_script_revision",
                "source_id": script.id,
                "node_key": resource_slot.scene_id,
                "segment_key": None,
                "scene_id": resource_slot.scene_id,
                "paragraph_id": None,
                "character_id": None,
                "role": resource_slot.role,
                "order_index": 0,
                "required": True,
                "slot_status": "bound",
                **asset_version_reference(version, storage),
                "media_url": f"/api/media/{storage.id}",
                "legacy_url": None,
                "asset_type": asset.asset_type,
                "target_name": asset.target_name,
                "emotion": None,
                "status": "ready",
            }
        )

    manifest = _graph_manifest(
        project_id=project.id,
        chapter=chapter,
        script=script,
        asset_bindings=asset_binding_items,
    )
    graph_json = {"Nodes": [], "Metadata": {"fixture": "readiness"}}
    graph = VNGraphRevision(
        project_id=project.id,
        chapter_index=1,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        revision_no=1,
        binding_manifest=manifest,
        binding_manifest_hash=content_hash(manifest),
        source_manifest_hash=content_hash(manifest),
        graph_hash=content_hash(graph_json),
        graph_json=graph_json,
        schema_version="1",
        compiler_version="deterministic-v2",
        tachi_policy_version="speaker-focus-v2",
        status="ready",
    )
    db.add(graph)
    db.flush()
    db.add(
        VNGraphHead(
            project_id=project.id,
            chapter_index=1,
            script_revision_id=script.id,
            current_revision_id=graph.id,
            lock_version=1,
        )
    )
    db.commit()
    return {
        "user": user,
        "project": project,
        "path": path,
        "state": state,
        "bible": bible,
        "slot": slot,
        "placement": placement,
        "outline": outline,
        "chapter": chapter,
        "script": script,
        "graph": graph,
        "storage": storage,
        "resource_slot": resource_slot,
    }


def _blocker_codes(result) -> set[str]:
    return {item.code for item in result.blocking_items}


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
                        "StringValue": "station",
                    },
                },
                "Outputs": {},
            },
        ],
    }


def _add_derived_graph(
    db,
    seeded: dict,
    *,
    parent: VNGraphRevision,
    revision_no: int,
) -> tuple[VNGraphRevision, AssetAction]:
    base_binding = deepcopy(seeded["graph"].binding_manifest["asset_bindings"][0])
    version_id = base_binding["asset_version_id"]
    media_url = base_binding["media_url"]
    patch = [
        {
            "op": "set_node_data",
            "path": ["BackgroundImage"],
            "value": {"Kind": "String", "StringValue": media_url},
            "node_index": 3,
            "node_key": "station",
        }
    ]
    result_graph = _background_graph(media_url)
    result_hash = content_hash(result_graph)
    target = {
        "source_kind": "vn_graph_revision",
        "source_id": parent.id,
        "chapter_index": 1,
        "node_index": 3,
        "node_key": "station",
        "asset_slot": "BackgroundImage",
        "role": "background",
        "graph_revision_id": parent.id,
        "graph_hash": parent.graph_hash,
    }
    action = AssetAction(
        user_id=seeded["user"].id,
        project_id=seeded["project"].id,
        mode="library_select",
        selection_method="manual_library",
        origin="library",
        status="applied",
        stage="applied",
        progress=100,
        target=target,
        input={},
        request_hash=content_hash({"revision_no": revision_no}),
        idempotency_key=f"readiness-derived-{revision_no}",
        result_asset_version_ids=[version_id],
        base_graph_hash=parent.graph_hash,
        result_graph_hash=result_hash,
        vngraph_patch=patch,
        requires_confirmation=True,
        confirmed_at=datetime.now(timezone.utc),
    )
    db.add(action)
    db.flush()

    action_binding = deepcopy(base_binding)
    action_binding.update(
        {
            "resource_slot_id": f"asset-action:{action.id}:0",
            "binding_id": f"asset-action:{action.id}:0",
            "source_kind": "vn_graph_revision",
            "source_id": parent.id,
            "node_key": "station",
            "scene_id": "station",
            "order_index": 0,
        }
    )
    manifest = deepcopy(parent.binding_manifest)
    manifest.update(
        {
            "manifest_version": "vngraph-binding-derived-v1",
            "asset_bindings": [
                *(manifest.get("asset_bindings") or []),
                action_binding,
            ],
            "derivation": {
                "kind": "asset_action",
                "asset_action_id": action.id,
                "parent_revision_id": parent.id,
                "parent_graph_hash": parent.graph_hash,
                "patch": patch,
                "patch_hash": content_hash(patch),
                "result_graph_hash": result_hash,
            },
        }
    )
    revision = VNGraphRevision(
        project_id=seeded["project"].id,
        chapter_index=1,
        chapter_revision_id=seeded["chapter"].id,
        script_revision_id=seeded["script"].id,
        parent_revision_id=parent.id,
        revision_no=revision_no,
        binding_manifest=manifest,
        binding_manifest_hash=content_hash(manifest),
        source_manifest_hash=content_hash(manifest),
        graph_hash=result_hash,
        graph_json=result_graph,
        schema_version="1",
        compiler_version="deterministic-v2",
        tachi_policy_version="speaker-focus-v2",
        status="ready",
    )
    db.add(revision)
    db.flush()
    return revision, action


def _add_child_branch(db, seeded: dict) -> tuple[StoryPath, StoryPathChapter]:
    project = seeded["project"]
    parent = seeded["path"]
    chapter = seeded["chapter"]
    source_state = seeded["state"]
    checkpoint = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key=f"fork-{uuid4()}",
        content_revision_id=chapter.id,
        payload={},
    )
    db.add(checkpoint)
    db.flush()
    candidate_ids = [str(uuid4()), str(uuid4())]
    candidates_json = [
        {
            "id": candidate_ids[0],
            "ordinal": 0,
            "option_key": "left",
            "preview_node_id": None,
            "preview_text": "Take the left platform",
            "state_delta": {"route": "left"},
        },
        {
            "id": candidate_ids[1],
            "ordinal": 1,
            "option_key": "right",
            "preview_node_id": None,
            "preview_text": "Take the right platform",
            "state_delta": {"route": "right"},
        },
    ]
    candidate_set = CandidateSetRevision(
        project_id=project.id,
        story_path_id=parent.id,
        checkpoint_node_id=checkpoint.id,
        chapter_revision_id=chapter.id,
        state_snapshot_id=source_state.id,
        revision_no=1,
        source_hash=content_hash({"checkpoint": checkpoint.id}),
        candidate_count=2,
        instructions="",
        candidates_json=candidates_json,
        content_hash=content_hash(candidates_json),
        created_by=seeded["user"].id,
    )
    db.add(candidate_set)
    db.flush()
    candidates = [
        BranchCandidate(
            id=item["id"],
            project_id=project.id,
            candidate_set_revision_id=candidate_set.id,
            checkpoint_node_id=checkpoint.id,
            option_key=item["option_key"],
            preview_node_id=None,
            state_delta=item["state_delta"],
            candidate_status="preview_ready",
        )
        for item in candidates_json
    ]
    db.add_all(candidates)
    branch_state_json = {"route": "left"}
    branch_state = StateSnapshot(
        project_id=project.id,
        parent_snapshot_id=source_state.id,
        state_hash=content_hash(branch_state_json),
        state_json=branch_state_json,
    )
    db.add(branch_state)
    db.flush()
    child = StoryPath(
        project_id=project.id,
        parent_path_id=parent.id,
        fork_path_chapter_id=seeded["placement"].id,
        fork_checkpoint_node_id=checkpoint.id,
        fork_candidate_id=candidates[0].id,
        base_state_snapshot_id=branch_state.id,
        title="Left route",
        status="active",
    )
    db.add(child)
    db.flush()
    inherited = StoryPathChapter(
        story_path_id=child.id,
        chapter_slot_id=seeded["slot"].id,
        display_index=1,
        inherited_from_path_chapter_id=seeded["placement"].id,
        current_revision_id=chapter.id,
    )
    db.add(inherited)
    db.flush()
    return child, inherited


def test_ready_fingerprint_is_deterministic_and_tracks_only_selected_authoring(db):
    seeded = _seed_ready_project(db)

    first = calculate_publication_readiness(db, project_id=seeded["project"].id)
    second = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert first.ready is True
    assert first.blocking_items == ()
    assert first.authoring_fingerprint == second.authoring_fingerprint
    assert len(first.authoring_fingerprint) == 64
    assert first.authoring_snapshot["schema_version"] == PUBLICATION_FINGERPRINT_VERSION

    script_head = db.query(ChapterScriptHead).filter_by(
        chapter_revision_id=seeded["chapter"].id
    ).one()
    script_head.lock_version += 4
    seeded["path"].lock_version += 3
    alternative_json = {"schema_version": "script-ir-v1", "paragraphs": [{"draft": True}]}
    db.add(
        ChapterScriptRevision(
            project_id=seeded["project"].id,
            chapter_index=1,
            chapter_revision_id=seeded["chapter"].id,
            bible_revision_id=seeded["bible"].id,
            outline_revision_id=seeded["outline"].id,
            revision_no=2,
            source_hash="c" * 64,
            script_hash=content_hash(alternative_json),
            script_json=alternative_json,
            coverage_json={},
            schema_version="script-ir-v1",
            generator_version="unselected-v2",
            status="ready",
        )
    )
    db.commit()
    unchanged = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert unchanged.ready is True
    assert unchanged.authoring_fingerprint == first.authoring_fingerprint

    replacement_manifest = {
        **seeded["graph"].binding_manifest,
        "review_variant": "alternate-graph",
    }
    replacement_graph_json = {
        "Nodes": [],
        "Metadata": {"fixture": "alternate-readiness"},
    }
    replacement_graph = VNGraphRevision(
        project_id=seeded["project"].id,
        chapter_index=1,
        chapter_revision_id=seeded["chapter"].id,
        script_revision_id=seeded["script"].id,
        parent_revision_id=seeded["graph"].id,
        revision_no=2,
        binding_manifest=replacement_manifest,
        binding_manifest_hash=content_hash(replacement_manifest),
        source_manifest_hash=content_hash(replacement_manifest),
        graph_hash=content_hash(replacement_graph_json),
        graph_json=replacement_graph_json,
        schema_version="1",
        compiler_version="deterministic-v2",
        tachi_policy_version="speaker-focus-v2",
        status="ready",
    )
    db.add(replacement_graph)
    db.flush()
    graph_head = db.query(VNGraphHead).filter_by(
        script_revision_id=seeded["script"].id
    ).one()
    graph_head.current_revision_id = replacement_graph.id
    db.commit()
    selected_graph_changed = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert selected_graph_changed.ready is True
    assert selected_graph_changed.authoring_fingerprint != first.authoring_fingerprint

    seeded["project"].title = "Frozen routes, revised"
    db.commit()
    changed = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert changed.ready is True
    assert changed.authoring_fingerprint != selected_graph_changed.authoring_fingerprint


def test_readiness_ignores_detached_historical_path_chapters(db):
    seeded = _seed_ready_project(db)
    baseline = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert baseline.ready is True

    historical_slot = ChapterSlot(
        project_id=seeded["project"].id,
        created_for_story_path_id=seeded["path"].id,
    )
    db.add(historical_slot)
    db.flush()
    db.add(
        StoryPathChapter(
            story_path_id=seeded["path"].id,
            chapter_slot_id=historical_slot.id,
            display_index=seeded["placement"].display_index,
            status="detached",
            detached_at=datetime.now(timezone.utc),
            detached_by_outline_revision_id=seeded["outline"].id,
        )
    )
    db.flush()

    with_history = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert with_history.ready is True
    assert with_history.blocking_items == ()
    assert with_history.authoring_fingerprint == baseline.authoring_fingerprint


def test_missing_review_heads_return_stable_blockers_instead_of_partial_release(db):
    seeded = _seed_ready_project(db)
    graph_head = db.query(VNGraphHead).filter_by(
        script_revision_id=seeded["script"].id
    ).one()
    graph_head.current_revision_id = None
    db.flush()
    missing_graph = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert missing_graph.ready is False
    assert _blocker_codes(missing_graph) == {"vngraph.head_missing"}

    graph_head.current_revision_id = seeded["graph"].id
    script_head = db.query(ChapterScriptHead).filter_by(
        chapter_revision_id=seeded["chapter"].id
    ).one()
    script_head.current_revision_id = None
    db.flush()
    missing_script = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert _blocker_codes(missing_script) == {"script.head_missing"}

    script_head.current_revision_id = seeded["script"].id
    seeded["placement"].current_revision_id = None
    db.flush()
    missing_chapter = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert _blocker_codes(missing_chapter) == {"chapter.head_missing"}


def test_resource_and_voice_edits_make_the_selected_graph_stale(db):
    seeded = _seed_ready_project(db, with_resource=True)
    baseline = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert baseline.ready is True

    seeded["storage"].status = "deleted"
    seeded["storage"].deleted_at = datetime.now(timezone.utc)
    db.flush()
    unavailable = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert unavailable.ready is False
    assert "vngraph.resource_invalid" in _blocker_codes(unavailable)
    assert unavailable.authoring_fingerprint != baseline.authoring_fingerprint

    seeded["storage"].status = "active"
    seeded["storage"].deleted_at = None
    seeded["resource_slot"].status = "planned"
    seeded["resource_slot"].asset_version_id = None
    db.flush()
    stale_slot = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert {"script.resource_unbound", "vngraph.resources_stale"}.issubset(
        _blocker_codes(stale_slot)
    )

    seeded["resource_slot"].status = "bound"
    seeded["resource_slot"].asset_version_id = seeded["graph"].binding_manifest[
        "asset_bindings"
    ][0]["asset_version_id"]
    db.add(
        VoiceLine(
            chapter_revision_id=seeded["chapter"].id,
            occurrence_id="line-after-compile",
            order_index=0,
            kind="narration",
            text="A late narration edit",
            status="planned",
        )
    )
    db.flush()
    stale_voice = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert "vngraph.voice_lines_stale" in _blocker_codes(stale_voice)


def test_readiness_validates_every_asset_action_in_derived_graph_chain(db):
    seeded = _seed_ready_project(db, with_resource=True)
    base = seeded["graph"]
    base_graph = _background_graph("")
    db.execute(
        update(VNGraphRevision)
        .where(VNGraphRevision.id == base.id)
        .values(graph_json=base_graph, graph_hash=content_hash(base_graph))
        .execution_options(synchronize_session=False)
    )
    db.expire(base)
    first, first_action = _add_derived_graph(
        db,
        seeded,
        parent=base,
        revision_no=2,
    )
    second, _second_action = _add_derived_graph(
        db,
        seeded,
        parent=first,
        revision_no=3,
    )
    head = db.query(VNGraphHead).filter_by(
        script_revision_id=seeded["script"].id
    ).one()
    head.current_revision_id = second.id
    db.flush()

    valid = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert valid.ready is True

    tampered_patch = deepcopy(first_action.vngraph_patch)
    tampered_patch[0]["node_index"] = 999
    first_action.vngraph_patch = tampered_patch
    db.flush()
    invalid = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert invalid.ready is False
    assert "vngraph.derivation_invalid" in _blocker_codes(invalid)


def test_active_child_requires_own_outline_and_accepts_frozen_inherited_prefix(db):
    seeded = _seed_ready_project(db)
    root_only = calculate_publication_readiness(db, project_id=seeded["project"].id)
    child, inherited = _add_child_branch(db, seeded)
    db.flush()

    missing_outline = calculate_publication_readiness(
        db, project_id=seeded["project"].id
    )
    assert missing_outline.ready is False
    assert "outline.head_missing" in _blocker_codes(missing_outline)

    _add_outline(
        db,
        project_id=seeded["project"].id,
        path=child,
        bible=seeded["bible"],
        placement=inherited,
        title="Inherited arrival",
    )
    db.flush()
    with_child = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert with_child.ready is True
    assert len(with_child.authoring_snapshot["story_paths"]) == 2
    assert with_child.authoring_fingerprint != root_only.authoring_fingerprint

    child.status = "archived"
    grandchild = StoryPath(
        project_id=seeded["project"].id,
        parent_path_id=child.id,
        fork_path_chapter_id=inherited.id,
        fork_checkpoint_node_id=child.fork_checkpoint_node_id,
        fork_candidate_id=str(uuid4()),
        base_state_snapshot_id=child.base_state_snapshot_id,
        title="Excluded descendant",
        status="active",
    )
    db.add(grandchild)
    db.flush()
    archived = calculate_publication_readiness(db, project_id=seeded["project"].id)
    assert archived.ready is True
    assert archived.authoring_fingerprint == root_only.authoring_fingerprint
    assert [
        item["id"] for item in archived.authoring_snapshot["story_paths"]
    ] == [seeded["path"].id]


def test_unknown_project_uses_stable_application_error(db):
    with pytest.raises(AppError) as captured:
        calculate_publication_readiness(db, project_id=404)
    assert captured.value.code == "project.not_found"
    assert captured.value.status_code == 404
