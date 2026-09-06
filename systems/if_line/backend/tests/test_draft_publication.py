"""固化发布（finalize-publish）：物化锚点激活 + 原子发布。"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.draft_publication_service import finalize_and_publish
from app.application.hashing import content_hash
from app.core.errors import AppError
from app.database import Base
from app.models_v2 import (
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    ProjectRelease,
    VNGraphHead,
    VNGraphRevision,
)
from test_publication_readiness import _graph_manifest, _seed_ready_project


@pytest.fixture()
def db_and_setup():
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
    db = factory()
    setup = _seed_ready_project(db, with_resource=True)
    try:
        yield db, setup
    finally:
        db.close()
        engine.dispose()


def _draft_chain(db, seeded: dict) -> dict:
    """物化一套 chapter→script→graph 草稿修订（未激活 head）。"""

    placement = seeded["placement"]
    chapter_v1 = seeded["chapter"]
    script_v1 = seeded["script"]

    chapter_text = "A arrives at the station in heavy rain, revisited."
    chapter = ChapterRevision(
        project_id=seeded["project"].id,
        chapter_slot_id=chapter_v1.chapter_slot_id,
        created_for_story_path_id=chapter_v1.created_for_story_path_id,
        chapter_index=chapter_v1.chapter_index,
        bible_revision_id=chapter_v1.bible_revision_id,
        outline_revision_id=chapter_v1.outline_revision_id,
        state_snapshot_id=chapter_v1.state_snapshot_id,
        revision_no=chapter_v1.revision_no + 1,
        parent_revision_id=chapter_v1.id,
        source_hash=content_hash({"context": chapter_v1.context_manifest, "origin": "manual_edit"}),
        context_manifest=chapter_v1.context_manifest,
        context_hash=chapter_v1.context_hash,
        content_hash=content_hash(chapter_text),
        content=chapter_text,
        status="ready",
        created_by=seeded["user"].id,
    )
    db.add(chapter)
    db.flush()

    script_json = {"schema_version": "script-ir-v1", "paragraphs": []}
    # 新 chapter family 的脚本/图必须从头建链（parent 不能跨 chapter/script family）。
    script = ChapterScriptRevision(
        project_id=seeded["project"].id,
        chapter_index=script_v1.chapter_index,
        chapter_revision_id=chapter.id,
        bible_revision_id=script_v1.bible_revision_id,
        outline_revision_id=script_v1.outline_revision_id,
        parent_revision_id=None,
        revision_no=1,
        source_hash=content_hash({"chapter_revision_id": chapter.id, "origin": "manual_edit"}),
        script_hash=content_hash(script_json),
        script_json=script_json,
        coverage_json={"coverage_ratio": 1.0},
        schema_version="script-ir-v1",
        generator_version="manual-edit-v1",
        status="ready",
        created_by=seeded["user"].id,
    )
    db.add(script)
    db.flush()

    manifest = _graph_manifest(
        project_id=seeded["project"].id,
        chapter=chapter,
        script=script,
        asset_bindings=[],
    )
    graph_json = {"Nodes": [], "Metadata": {"fixture": "finalize-draft"}}
    graph = VNGraphRevision(
        project_id=seeded["project"].id,
        chapter_index=script_v1.chapter_index,
        chapter_revision_id=chapter.id,
        script_revision_id=script.id,
        parent_revision_id=None,
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
    db.commit()
    return {"chapter": chapter, "script": script, "graph": graph}


def _anchors(draft: dict, seeded: dict) -> dict:
    return {
        "chapter_revision_ids": {seeded["placement"].id: draft["chapter"].id},
        "script_revision_ids": {seeded["placement"].id: draft["script"].id},
        "graph_revision_ids": {seeded["placement"].id: draft["graph"].id},
    }


def test_finalize_publishes_new_release_and_aligns_heads(db_and_setup):
    db, seeded = db_and_setup
    first = finalize_and_publish(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        idempotency_key=f"finalize-first-{uuid4()}",
        release_notes="v1",
    )
    assert first.created is True
    assert first.release.version == 1

    draft = _draft_chain(db, seeded)
    result = finalize_and_publish(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        idempotency_key=f"finalize-second-{uuid4()}",
        release_notes="v2 from drafts",
        **_anchors(draft, seeded),
    )
    db.commit()

    assert result.created is True
    assert result.release.version == 2
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 2

    placement = seeded["placement"]
    db.refresh(placement)
    assert placement.current_revision_id == draft["chapter"].id
    script_head = db.query(ChapterScriptHead).filter_by(
        chapter_revision_id=draft["chapter"].id
    ).one()
    assert script_head.current_revision_id == draft["script"].id
    graph_head = db.query(VNGraphHead).filter_by(
        script_revision_id=draft["script"].id
    ).one()
    assert graph_head.current_revision_id == draft["graph"].id

    # 幂等重放：同一 key 同锚点返回同一 release，不新增版本。
    replay = finalize_and_publish(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        idempotency_key=result.release.publication_idempotency_key,
        release_notes="v2 from drafts",
        **_anchors(draft, seeded),
    )
    db.commit()
    assert replay.created is False
    assert replay.release.id == result.release.id
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 2


def test_finalize_rolls_back_activation_when_readiness_blocks(db_and_setup):
    db, seeded = db_and_setup
    finalize_and_publish(
        db,
        project_id=seeded["project"].id,
        user_id=seeded["user"].id,
        idempotency_key=f"finalize-base-{uuid4()}",
    )
    db.commit()

    draft = _draft_chain(db, seeded)
    # 只物化正文、下游不重做 → readiness 阻塞，激活必须整体回滚。
    with pytest.raises(AppError) as captured:
        finalize_and_publish(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            idempotency_key=f"finalize-blocked-{uuid4()}",
            chapter_revision_ids={seeded["placement"].id: draft["chapter"].id},
        )
    db.rollback()

    assert captured.value.code == "release.not_ready"
    assert captured.value.details and captured.value.details.get("blocking_items")

    placement = seeded["placement"]
    db.refresh(placement)
    assert placement.current_revision_id == seeded["chapter"].id
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 1


def test_finalize_rejects_invalid_anchor(db_and_setup):
    db, seeded = db_and_setup
    with pytest.raises(AppError) as captured:
        finalize_and_publish(
            db,
            project_id=seeded["project"].id,
            user_id=seeded["user"].id,
            idempotency_key=f"finalize-invalid-{uuid4()}",
            bible_revision_id=str(uuid4()),
        )
    db.rollback()

    assert captured.value.code == "finalize.anchor_invalid"
    assert captured.value.status_code == 422
    assert db.query(ProjectRelease).filter_by(project_id=seeded["project"].id).count() == 0
