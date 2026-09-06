from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application import story_generation_service
from app.application.story_chapter_batch_service import create_story_path_chapter_batch
from app.application.chapter_script_service import (
    create_chapter_script_generation_task,
    get_chapter_script_head,
)
from app.application.hashing import content_hash
from app.application.revision_service import create_bible_revision
from app.application.story_chapter_service import (
    build_chapter_generation_source,
    create_manual_story_path_chapter_revision,
    create_story_path_chapter_generation_task,
    create_story_path_chapter_revision,
    resolve_chapter_generation_source,
)
from app.application.story_generation_service import generate_chapter_task
from app.application.revision_head_service import activate_path_chapter_revision_head
from app.application.story_outline_service import (
    activate_story_path_outline_revision,
    create_story_path_outline_revision,
)
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import (
    claim_task,
    complete_task,
    create_generation_task,
    fail_task,
    lease_from_task,
    update_parent_aggregate,
)
from app.core.errors import AppError
from app.integrations.llm import ModelCallResult
from app.models import Project, User
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    GenerationTask,
    StateSnapshot,
    StoryPath,
    StoryPathChapter,
    TaskEvent,
)
from app.orm_base import Base
from app.schemas import LLMChapterContentOutput
from app.services.prompt_templates import get_chapter_content_prompt


class _ChapterAdapter:
    calls: list[dict] = []

    async def generate_chapter(self, **kwargs):
        type(self).calls.append(deepcopy(kwargs))
        return ModelCallResult(
            data=LLMChapterContentOutput(
                chapter_index=kwargs["chapter_outline"]["chapter_index"],
                title=kwargs["chapter_outline"]["title"],
                content="Generated from the frozen path.\nSecond paragraph.",
            ),
            raw_text=None,
            provider="test",
            model="chapter-test",
            provider_request_id=None,
            input_tokens=10,
            output_tokens=20,
            latency_ms=1,
            prompt_version="chapter-test-v1",
        )


def _outline_chapters(count: int) -> list[dict]:
    return [
        {
            "story_path_chapter_id": None,
            "display_index": index,
            "title": f"Chapter {index}",
            "summary": f"Summary {index}",
            "characters": ["A"],
            "visual_keywords": [f"scene-{index}"],
        }
        for index in range(1, count + 1)
    ]


def _create_selected_revision(db, *, project_id, path_chapter, user, content):
    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=path_chapter.id,
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    context = resolve_chapter_generation_source(
        db,
        task_project_id=project_id,
        source_refs=source_refs,
    )
    revision = create_story_path_chapter_revision(
        db,
        context=context,
        content=content,
        user_id=user.id,
    )
    path_chapter.current_revision_id = revision.id
    return revision


def _complete_batch_child(db, seeded, child, content):
    context = resolve_chapter_generation_source(
        db,
        task_project_id=seeded["project"].id,
        source_refs=child.source_refs,
    )
    revision = create_story_path_chapter_revision(
        db,
        context=context,
        content=content,
        user_id=seeded["user"].id,
        task_id=child.id,
    )
    complete_task(
        db,
        child,
        result_refs={
            "chapter_revision_id": revision.id,
            "story_path_id": seeded["path"].id,
            "path_chapter_id": context.path_chapter_id,
            "activated": False,
            "review_required": True,
        },
        actual_cost=0,
    )
    update_parent_aggregate(db, child)
    db.flush()
    return revision


def _child_path_id(child: GenerationTask) -> str:
    return child.source_refs["chapter_source"]["context_manifest"][
        "path_chapter_id"
    ]


def _claim_lease(db, task: GenerationTask):
    claimed = claim_task(db, task.id, f"chapter-test:{task.id}")
    assert claimed is not None
    lease = lease_from_task(claimed)
    db.commit()
    return lease


@pytest.fixture()
def seeded(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    user = User(
        email="story-chapter@example.com",
        password_hash="hash",
        display_name="Story Chapter",
        quota_total=500,
        quota_daily=500,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Frozen chapter project",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    state_json = {"route": "main", "trust": 3}
    state = StateSnapshot(
        project_id=project.id,
        state_hash=content_hash(state_json),
        state_json=state_json,
    )
    db.add(state)
    db.flush()
    path = StoryPath(
        project_id=project.id,
        title="Main",
        base_state_snapshot_id=state.id,
    )
    db.add(path)
    db.flush()
    bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Frozen world"},
        source={"origin": "seed"},
        user_id=user.id,
        activate=True,
    )
    outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_outline_chapters(3),
        user_id=user.id,
    )
    placements = list(
        activate_story_path_outline_revision(
            db,
            story_path_id=path.id,
            revision_id=outline.id,
        ).path_chapters
    )
    first_revision = _create_selected_revision(
        db,
        project_id=project.id,
        path_chapter=placements[0],
        user=user,
        content="Selected chapter one",
    )
    second_revision = _create_selected_revision(
        db,
        project_id=project.id,
        path_chapter=placements[1],
        user=user,
        content="Selected chapter two",
    )
    db.commit()

    _ChapterAdapter.calls = []
    monkeypatch.setattr(story_generation_service, "SessionLocal", factory)
    monkeypatch.setattr(story_generation_service, "LegacyLLMAdapter", _ChapterAdapter)
    try:
        yield {
            "db": db,
            "factory": factory,
            "user": user,
            "project": project,
            "path": path,
            "state": state,
            "bible": bible,
            "outline": outline,
            "first": placements[0],
            "second": placements[1],
            "target": placements[2],
            "first_revision": first_revision,
            "second_revision": second_revision,
        }
    finally:
        db.close()
        engine.dispose()


def test_script_task_freezes_exact_story_path_chapter_source(seeded):
    db = seeded["db"]
    task, created = create_chapter_script_generation_task(
        db,
        user_id=seeded["user"].id,
        chapter_revision_id=seeded["first_revision"].id,
        idempotency_key="exact-story-path-script",
    )
    source = task.source_refs["chapter_script_source"]
    head = get_chapter_script_head(
        db,
        chapter_revision_id=seeded["first_revision"].id,
    )

    assert created is True
    assert source["chapter_revision_id"] == seeded["first_revision"].id
    assert source["path_chapter_id"] == seeded["first"].id
    assert source["display_index"] == seeded["first"].display_index
    assert source["outline_revision_id"] == seeded["outline"].id
    assert source["provider_input"]["outline_chapter"][
        "story_path_chapter_id"
    ] == seeded["first"].id
    assert head.revision_id is None
    assert head.lock_version == 1


def test_worker_uses_frozen_predecessors_and_never_moves_head(seeded):
    db = seeded["db"]
    task, created = create_story_path_chapter_generation_task(
        db,
        user_id=seeded["user"].id,
        path_chapter_id=seeded["target"].id,
        idempotency_key="target-chapter-generation",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        instructions="Keep the branch route intact",
    )
    frozen_manifest = deepcopy(task.source_refs["chapter_source"]["context_manifest"])
    db.commit()

    second_source = build_chapter_generation_source(
        db,
        path_chapter_id=seeded["second"].id,
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    changed_second = create_story_path_chapter_revision(
        db,
        context=resolve_chapter_generation_source(
            db,
            task_project_id=seeded["project"].id,
            source_refs=second_source,
        ),
        content="Changed chapter two after enqueue",
        user_id=seeded["user"].id,
    )
    seeded["second"].current_revision_id = changed_second.id

    target_context = resolve_chapter_generation_source(
        db,
        task_project_id=seeded["project"].id,
        source_refs=task.source_refs,
    )
    competing_target = create_story_path_chapter_revision(
        db,
        context=target_context,
        content="A competing target revision selected after enqueue",
        user_id=seeded["user"].id,
    )
    seeded["target"].current_revision_id = competing_target.id
    db.commit()

    result = generate_chapter_task(task.id, _claim_lease(db, task))
    db.expire_all()

    generated = db.query(ChapterRevision).filter_by(generation_task_id=task.id).one()
    target = db.query(StoryPathChapter).filter_by(id=seeded["target"].id).one()
    artifact = db.query(TaskEvent).filter_by(
        task_id=task.id,
        event_type="artifact.ready",
    ).one()
    assert created is True
    assert [item["chapter_revision_id"] for item in _ChapterAdapter.calls[0]["previous_chapters"]] == [
        seeded["first_revision"].id,
        seeded["second_revision"].id,
    ]
    assert "Changed chapter two" not in str(_ChapterAdapter.calls[0]["previous_chapters"])
    assert _ChapterAdapter.calls[0]["state_snapshot"] == {"route": "main", "trust": 3}
    assert _ChapterAdapter.calls[0]["instructions"] == "Keep the branch route intact"
    assert generated.chapter_slot_id == seeded["target"].chapter_slot_id
    assert generated.created_for_story_path_id == seeded["path"].id
    assert generated.context_manifest == frozen_manifest
    assert generated.context_hash == content_hash(frozen_manifest)
    assert generated.parent_revision_id is None
    assert generated.status == "ready"
    assert target.current_revision_id == competing_target.id
    assert db.query(ChapterHead).filter_by(project_id=seeded["project"].id).count() == 0
    assert result.result_refs == {
        "chapter_revision_id": generated.id,
        "activated": False,
        "review_required": True,
        "story_path_id": seeded["path"].id,
        "path_chapter_id": seeded["target"].id,
    }
    assert artifact.payload["review_required"] is True

    recovered = generate_chapter_task(task.id)
    assert recovered.result_refs["chapter_revision_id"] == generated.id
    assert recovered.result_refs["recovered"] is True
    assert len(_ChapterAdapter.calls) == 1


def test_worker_rejects_tampered_manifest_before_provider_call(seeded):
    db = seeded["db"]
    task, _ = create_story_path_chapter_generation_task(
        db,
        user_id=seeded["user"].id,
        path_chapter_id=seeded["target"].id,
        idempotency_key="tampered-chapter-generation",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    source_refs = deepcopy(task.source_refs)
    source_refs["chapter_source"]["context_manifest"]["ancestors"][0][
        "chapter_revision_id"
    ] = seeded["second_revision"].id
    source_refs["chapter_source_hash"] = content_hash(source_refs["chapter_source"])
    task.source_refs = source_refs
    db.commit()

    with pytest.raises(NonRetryableTaskError) as captured:
        generate_chapter_task(task.id, _claim_lease(db, task))

    assert captured.value.code == "context.hash_mismatch"
    assert _ChapterAdapter.calls == []
    assert db.query(ChapterRevision).filter_by(generation_task_id=task.id).count() == 0


def test_worker_rejects_legacy_chapter_task_source_schema(seeded):
    db = seeded["db"]
    task, _ = create_generation_task(
        db,
        user_id=seeded["user"].id,
        project_id=seeded["project"].id,
        kind="chapter.generate",
        idempotency_key="legacy-queued-chapter",
        source_refs={
            "bible_revision_id": seeded["bible"].id,
            "outline_revision_id": seeded["outline"].id,
            "chapter_index": 3,
        },
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        estimated_cost=0,
    )
    db.commit()

    with pytest.raises(NonRetryableTaskError) as captured:
        generate_chapter_task(task.id, _claim_lease(db, task))

    assert captured.value.code == "chapter.source_schema_unsupported"
    assert _ChapterAdapter.calls == []
    assert db.query(ChapterRevision).filter_by(generation_task_id=task.id).count() == 0


def test_batch_freezes_next_context_only_after_prior_provisional_succeeds(seeded):
    db = seeded["db"]
    parent, created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="story-path-batch",
        path_chapter_ids=[seeded["target"].id, seeded["first"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        instructions="Use frozen inputs",
    )
    db.flush()

    initial_children = (
        db.query(GenerationTask)
        .filter(GenerationTask.parent_task_id == parent.id)
        .all()
    )
    assert created is True
    assert parent.status == "running"
    assert len(initial_children) == 1
    first_child = initial_children[0]
    assert first_child.source_refs["chapter_source"]["context_manifest"][
        "path_chapter_id"
    ] == seeded["first"].id
    assert first_child.source_refs["chapter_source"]["context_manifest"]["ancestors"] == []
    assert db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).count() == 1

    generated_first = _complete_batch_child(
        db,
        seeded,
        first_child,
        "New provisional chapter one",
    )
    children = (
        db.query(GenerationTask)
        .filter(GenerationTask.parent_task_id == parent.id)
        .all()
    )
    assert len(children) == 2
    target_child = next(child for child in children if child.id != first_child.id)
    target_manifest = target_child.source_refs["chapter_source"]["context_manifest"]
    assert target_manifest["path_chapter_id"] == seeded["target"].id
    assert [
        item["chapter_revision_id"]
        for item in target_manifest["ancestors"]
    ] == [generated_first.id, seeded["second_revision"].id]
    assert all(
        child.source_refs["chapter_source"]["generation_parameters"]["instructions"]
        == "Use frozen inputs"
        for child in children
    )

    generated_target = _complete_batch_child(
        db,
        seeded,
        target_child,
        "New provisional chapter three",
    )
    assert parent.status == "succeeded"
    assert seeded["first"].current_revision_id == seeded["first_revision"].id
    assert seeded["target"].current_revision_id is None
    assert first_child.result_refs["provisional_status"] == "provisional"
    assert target_child.result_refs["provisional_status"] == "provisional"
    assert parent.result_refs["provisional_chain"][-1]["chapter_revision_id"] == (
        generated_target.id
    )

    seeded["second"].current_revision_id = None
    replay, replay_created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="story-path-batch",
        path_chapter_ids=[seeded["target"].id, seeded["first"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        instructions="Use frozen inputs",
    )
    assert replay.id == parent.id
    assert replay_created is False
    assert db.query(GenerationTask).filter(GenerationTask.parent_task_id == parent.id).count() == 2


def test_batch_generates_a_new_outline_sequentially_without_formal_heads(seeded):
    db = seeded["db"]
    seeded["first"].current_revision_id = None
    seeded["second"].current_revision_id = None
    db.flush()

    parent, _created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="new-outline-generate-all",
        path_chapter_ids=[
            seeded["first"].id,
            seeded["second"].id,
            seeded["target"].id,
        ],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )

    generated: list[ChapterRevision] = []
    expected_path_ids = [
        seeded["first"].id,
        seeded["second"].id,
        seeded["target"].id,
    ]
    for position, path_chapter_id in enumerate(expected_path_ids, start=1):
        children = db.query(GenerationTask).filter(
            GenerationTask.parent_task_id == parent.id
        ).all()
        assert len(children) == position
        child = next(
            item
            for item in children
            if item.source_refs["chapter_source"]["context_manifest"][
                "path_chapter_id"
            ]
            == path_chapter_id
        )
        ancestors = child.source_refs["chapter_source"]["context_manifest"][
            "ancestors"
        ]
        assert [item["chapter_revision_id"] for item in ancestors] == [
            revision.id for revision in generated
        ]
        generated.append(
            _complete_batch_child(
                db,
                seeded,
                child,
                f"Provisional chapter {position}",
            )
        )

    assert parent.status == "succeeded"
    assert all(
        placement.current_revision_id is None
        for placement in (seeded["first"], seeded["second"], seeded["target"])
    )


def test_batch_stops_before_creating_the_next_child_after_failure(seeded):
    db = seeded["db"]
    parent, _created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="failed-sequential-batch",
        path_chapter_ids=[seeded["first"].id, seeded["target"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    child = db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).one()

    fail_task(
        db,
        child,
        error_code="provider.failed",
        safe_detail="provider failed",
        retryable=False,
    )
    update_parent_aggregate(db, child)
    db.flush()

    assert parent.status == "partial"
    assert parent.error_code == "chapter.batch_child_failed"
    assert db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).count() == 1


def test_batch_stops_if_story_path_topology_changes_between_children(seeded):
    db = seeded["db"]
    parent, _created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="topology-change-batch",
        path_chapter_ids=[seeded["first"].id, seeded["target"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    first_child = db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).one()
    seeded["target"].predecessor_path_chapter_id = seeded["first"].id
    db.flush()

    _complete_batch_child(
        db,
        seeded,
        first_child,
        "Generated before topology changed",
    )

    assert parent.status == "partial"
    assert parent.error_code == "chapter.batch_context_changed"
    assert db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).count() == 1


def test_reselecting_an_upstream_revision_marks_batch_descendants_stale(seeded):
    db = seeded["db"]
    parent, _created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="provisional-review-chain",
        path_chapter_ids=[
            seeded["first"].id,
            seeded["second"].id,
            seeded["target"].id,
        ],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    generated: list[ChapterRevision] = []
    for position in range(1, 4):
        child = (
            db.query(GenerationTask)
            .filter(GenerationTask.parent_task_id == parent.id)
            .order_by(GenerationTask.created_at, GenerationTask.id)
            .all()[position - 1]
        )
        generated.append(
            _complete_batch_child(
                db,
                seeded,
                child,
                f"Review chain chapter {position}",
            )
        )

    activate_path_chapter_revision_head(
        db,
        path_chapter_id=seeded["first"].id,
        revision_id=generated[0].id,
        expected_lock_version=seeded["first"].lock_version,
    )
    activate_path_chapter_revision_head(
        db,
        path_chapter_id=seeded["second"].id,
        revision_id=generated[1].id,
        expected_lock_version=seeded["second"].lock_version,
    )
    children = db.query(GenerationTask).filter(
        GenerationTask.parent_task_id == parent.id
    ).all()
    children_by_path_id = {_child_path_id(child): child for child in children}
    assert children_by_path_id[seeded["first"].id].result_refs[
        "provisional_status"
    ] == "accepted"
    assert children_by_path_id[seeded["second"].id].result_refs[
        "provisional_status"
    ] == "accepted"

    activate_path_chapter_revision_head(
        db,
        path_chapter_id=seeded["first"].id,
        revision_id=seeded["first_revision"].id,
        expected_lock_version=seeded["first"].lock_version,
    )
    assert all(
        child.result_refs["provisional_status"] == "stale" for child in children
    )
    with pytest.raises(AppError) as captured:
        activate_path_chapter_revision_head(
            db,
            path_chapter_id=seeded["target"].id,
            revision_id=generated[2].id,
            expected_lock_version=seeded["target"].lock_version,
        )
    assert captured.value.code == "chapter_revision.provisional_stale"


def test_batch_rejects_an_unreviewed_predecessor_before_creating_tasks(seeded):
    db = seeded["db"]
    seeded["second"].current_revision_id = None
    db.flush()
    task_count = db.query(GenerationTask).count()

    with pytest.raises(HTTPException) as captured:
        create_story_path_chapter_batch(
            db,
            user_id=seeded["user"].id,
            story_path_id=seeded["path"].id,
            idempotency_key="invalid-story-path-batch",
            path_chapter_ids=[seeded["target"].id],
        )

    assert captured.value.status_code == 409
    assert "predecessor" in str(captured.value.detail)
    assert db.query(GenerationTask).count() == task_count


def test_prompt_includes_frozen_state_and_generation_instructions():
    prompt = get_chapter_content_prompt(
        {"world": "w"},
        {"chapter_index": 3, "title": "Branch"},
        [{"chapter_index": 2, "summary": "Before"}],
        1000,
        1500,
        state_snapshot={"route": "left", "trust": 4},
        instructions="Keep the door locked",
    )

    assert "冻结剧情状态" in prompt
    assert '"route": "left"' in prompt
    assert "Keep the door locked" in prompt


def test_active_generation_code_has_no_index_range_context_query():
    source_files = [
        story_generation_service.__file__,
        __import__(
            "app.services.chapter_generation_service",
            fromlist=["ChapterGenerationService"],
        ).__file__,
    ]
    for source_file in source_files:
        source = Path(source_file).read_text(encoding="utf-8")
        assert "ChapterHead.chapter_index <" not in source
        assert "ChapterContent.chapter_index <" not in source


def test_chapter_task_accepts_materialized_draft_anchors(seeded):
    db = seeded["db"]
    user, project, path = seeded["user"], seeded["project"], seeded["path"]
    # 物化草稿链：未激活 bible + 绑定该 bible 的未激活 outline（沿用 placement）。
    draft_bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Draft bible"},
        source={"origin": "manual_edit"},
        user_id=user.id,
        activate=False,
    )
    placement_ids = [seeded["first"].id, seeded["second"].id, seeded["target"].id]
    chapters = [
        {
            "story_path_chapter_id": placement_ids[index],
            "display_index": index + 1,
            "title": f"Chapter {index + 1}",
            "summary": f"Draft summary {index + 1}",
            "characters": ["A"],
            "visual_keywords": [],
        }
        for index in range(3)
    ]
    draft_outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=draft_bible.id,
        chapters=chapters,
        user_id=user.id,
    )
    task, created = create_story_path_chapter_generation_task(
        db,
        user_id=user.id,
        path_chapter_id=seeded["target"].id,
        idempotency_key="anchored-chapter",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        bible_revision_id=draft_bible.id,
        outline_revision_id=draft_outline.id,
    )
    db.commit()

    assert created is True
    chapter_source = task.source_refs["chapter_source"]
    manifest = chapter_source["context_manifest"]
    assert manifest["bible_revision_id"] == draft_bible.id
    assert manifest["outline_revision_id"] == draft_outline.id
    assert chapter_source["revision_anchors"] == {
        "bible_revision_id": draft_bible.id,
        "outline_revision_id": draft_outline.id,
        "ancestor_revision_overrides": None,
    }

    generate_chapter_task(task.id, _claim_lease(db, task))
    assert _ChapterAdapter.calls[0]["chapter_outline"]["summary"] == "Draft summary 3"
    assert _ChapterAdapter.calls[0]["previous_chapters"][0]["summary"] == (
        "Selected chapter one"
    )

    # 同 key 同锚点 → 幂等重放既有任务。
    replay, created_replay = create_story_path_chapter_generation_task(
        db,
        user_id=user.id,
        path_chapter_id=seeded["target"].id,
        idempotency_key="anchored-chapter",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        bible_revision_id=draft_bible.id,
        outline_revision_id=draft_outline.id,
    )
    assert created_replay is False
    assert replay.id == task.id

    # 同 key 不同锚点 → 409。
    other_chapters = deepcopy(chapters)
    other_chapters[2]["summary"] = "A genuinely different draft"
    other_outline = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=draft_bible.id,
        chapters=other_chapters,
        user_id=user.id,
    )
    assert other_outline.id != draft_outline.id
    with pytest.raises(HTTPException) as captured:
        create_story_path_chapter_generation_task(
            db,
            user_id=user.id,
            path_chapter_id=seeded["target"].id,
            idempotency_key="anchored-chapter",
            parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
            bible_revision_id=draft_bible.id,
            outline_revision_id=other_outline.id,
        )
    assert captured.value.status_code == 409


def test_chapter_task_ancestor_override_uses_materialized_revision(seeded):
    db = seeded["db"]
    user, project, path = seeded["user"], seeded["project"], seeded["path"]
    source_refs = build_chapter_generation_source(
        db,
        path_chapter_id=seeded["second"].id,
        parameters={},
    )
    context = resolve_chapter_generation_source(
        db,
        task_project_id=project.id,
        source_refs=source_refs,
    )
    draft_first = create_story_path_chapter_revision(
        db,
        context=resolve_chapter_generation_source(
            db,
            task_project_id=project.id,
            source_refs=build_chapter_generation_source(
                db,
                path_chapter_id=seeded["first"].id,
                parameters={},
            ),
        ),
        content="Draft chapter one from localStorage",
        user_id=user.id,
    )
    task, created = create_story_path_chapter_generation_task(
        db,
        user_id=user.id,
        path_chapter_id=seeded["target"].id,
        idempotency_key="override-chapter",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        ancestor_revision_overrides={seeded["first"].id: draft_first.id},
    )
    db.commit()

    assert created is True
    manifest = task.source_refs["chapter_source"]["context_manifest"]
    overridden = [
        item
        for item in manifest["ancestors"]
        if item["path_chapter_id"] == seeded["first"].id
    ][0]
    assert overridden["chapter_revision_id"] == draft_first.id

    generate_chapter_task(task.id, _claim_lease(db, task))
    previous = _ChapterAdapter.calls[0]["previous_chapters"]
    assert previous[0]["summary"] == "Draft chapter one from localStorage"
    assert previous[1]["summary"] == "Selected chapter two"


def test_batch_anchors_to_materialized_bible_and_outline(seeded):
    db = seeded["db"]
    draft_bible = create_bible_revision(
        db,
        project_id=seeded["project"].id,
        content={"worldview": "Draft world from localStorage"},
        source={"origin": "manual_edit"},
        user_id=seeded["user"].id,
        activate=False,
    )
    # 解析器契约：大纲锚点必须与 bible 锚点配套，并沿用 placement 绑定。
    draft_outline = create_story_path_outline_revision(
        db,
        story_path_id=seeded["path"].id,
        bible_revision_id=draft_bible.id,
        chapters=[
            {
                "story_path_chapter_id": seeded["first"].id,
                "display_index": 1,
                "title": "Chapter 1",
                "summary": "Draft summary 1",
                "characters": ["A"],
                "visual_keywords": [],
            },
        ],
        user_id=seeded["user"].id,
    )

    parent, created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="story-path-batch-anchored",
        path_chapter_ids=[seeded["first"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        bible_revision_id=draft_bible.id,
        outline_revision_id=draft_outline.id,
    )
    db.flush()

    assert created is True
    anchor = parent.source_refs["chapter_batch_source"]["context_anchor"]
    assert anchor["bible_revision_id"] == draft_bible.id
    assert anchor["outline_revision_id"] == draft_outline.id
    child = (
        db.query(GenerationTask)
        .filter(GenerationTask.parent_task_id == parent.id)
        .one()
    )
    manifest = child.source_refs["chapter_source"]["context_manifest"]
    assert manifest["bible_revision_id"] == draft_bible.id

    # 同 Idempotency-Key + 同锚点 → 幂等重放；换成不同锚点 → 409。
    _src = (parent.source_refs or {}).get("chapter_batch_source", {})
    print("DEBUG anchor:", _src.get("context_anchor"))
    print("DEBUG ids:", _src.get("path_chapter_ids"), [seeded["first"].id])
    print("DEBUG snap:", repr(_src.get("requested_state_snapshot_id")))
    print("DEBUG params:", repr(parent.parameters))
    print("DEBUG kind/project:", parent.kind, parent.project_id == seeded["path"].project_id)
    print("DEBUG schema:", _src.get("schema_version"), repr(_src.get("story_path_id")), seeded["path"].id)
    replay, replay_created = create_story_path_chapter_batch(
        db,
        user_id=seeded["user"].id,
        story_path_id=seeded["path"].id,
        idempotency_key="story-path-batch-anchored",
        path_chapter_ids=[seeded["first"].id],
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
        bible_revision_id=draft_bible.id,
        outline_revision_id=draft_outline.id,
    )
    assert replay_created is False
    assert replay.id == parent.id

    with pytest.raises(HTTPException) as captured:
        create_story_path_chapter_batch(
            db,
            user_id=seeded["user"].id,
            story_path_id=seeded["path"].id,
            idempotency_key="story-path-batch-anchored",
            path_chapter_ids=[seeded["first"].id],
            parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
            bible_revision_id=seeded["bible"].id,
            outline_revision_id=seeded["outline"].id,
        )
    assert captured.value.status_code == 409


def test_manual_revision_materialization_follows_parent_frozen_context(seeded):
    """物化正文草稿：parent 携带生成任务冻结上下文时直接沿用，不依赖激活 head。"""
    db = seeded["db"]
    task, created = create_story_path_chapter_generation_task(
        db,
        user_id=seeded["user"].id,
        path_chapter_id=seeded["first"].id,
        idempotency_key="parent-context-task",
        parameters={"stream": False, "word_count_min": 1, "word_count_max": 100},
    )
    db.flush()
    assert created is True
    context = resolve_chapter_generation_source(
        db,
        task_project_id=seeded["project"].id,
        source_refs=task.source_refs,
    )
    parent = create_story_path_chapter_revision(
        db,
        context=context,
        content="AI generated chapter one",
        user_id=seeded["user"].id,
        task_id=task.id,
    )
    db.flush()

    revision = create_manual_story_path_chapter_revision(
        db,
        path_chapter_id=seeded["first"].id,
        parent_revision_id=parent.id,
        content="Manually edited chapter one",
        user_id=seeded["user"].id,
    )

    assert revision.revision_no == parent.revision_no + 1
    assert revision.content == "Manually edited chapter one"
    assert revision.generation_task_id is None
