from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application import story_generation_service
from app.application.hashing import content_hash
from app.application.revision_service import (
    activate_bible_revision,
    build_bible_generation_source,
    create_bible_revision,
)
from app.application.story_generation_service import generate_bible_task
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import claim_task, create_generation_task, lease_from_task
from app.integrations.llm import ModelCallResult
from app.models import Project, User
from app.models_v2 import (
    GenerationTask,
    ProjectContentHead,
    StoryBibleRevision,
    TaskEvent,
)
from app.orm_base import Base
from app.routers.v2 import revisions as revisions_router
from app.schemas import LLMStoryBibleOutput
from app.schemas_v2 import GenerationRequest


class _BibleAdapter:
    calls = 0
    requests = []

    async def generate_bible(self, **kwargs):
        type(self).calls += 1
        type(self).requests.append(kwargs)
        return ModelCallResult(
            data=LLMStoryBibleOutput(
                worldview="generated world",
                characters=[],
                character_relations="",
                main_conflict="generated conflict",
                emotional_line="",
                style_rules="",
                ending_constraints="",
                forbidden_points=[],
                writing_notes=[],
            ),
            raw_text=None,
            provider="test",
            model="test-model",
            provider_request_id=f"bible-review-request-{type(self).calls}",
            input_tokens=12,
            output_tokens=34,
            latency_ms=5,
            prompt_version="test-v1",
        )


def _claim_lease(db, task: GenerationTask):
    claimed = claim_task(db, task.id, f"bible-test:{task.id}")
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
        email="bible-review@example.com",
        password_hash="hash",
        display_name="Bible Review",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Reviewable Bible",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    selected = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "selected"},
        source={"origin": "seed"},
        user_id=user.id,
        activate=True,
    )
    db.commit()

    _BibleAdapter.calls = 0
    _BibleAdapter.requests = []
    monkeypatch.setattr(story_generation_service, "SessionLocal", factory)
    monkeypatch.setattr(story_generation_service, "LegacyLLMAdapter", _BibleAdapter)
    try:
        yield db, user, project, selected
    finally:
        db.close()
        engine.dispose()


def test_generation_freezes_parent_and_never_moves_bible_head(seeded):
    db, user, project, selected = seeded
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="reviewable-bible",
        source_refs=build_bible_generation_source(
            project,
            parent_revision_id=selected.id,
        ),
        estimated_cost=Decimal("0"),
    )
    competing = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "selected while task runs"},
        source={"origin": "manual"},
        user_id=user.id,
        activate=True,
    )
    db.commit()
    head_before = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    lock_before = head_before.lock_version
    project.title = "Changed after enqueue"
    db.commit()

    result = generate_bible_task(task.id, _claim_lease(db, task))
    db.expire_all()

    generated = db.query(StoryBibleRevision).filter_by(
        generation_task_id=task.id
    ).one()
    head_after = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    artifact_event = (
        db.query(TaskEvent)
        .filter_by(task_id=task.id, event_type="artifact.ready")
        .one()
    )
    assert generated.parent_revision_id == selected.id
    assert generated.id != competing.id
    assert head_after.current_bible_revision_id == competing.id
    assert head_after.lock_version == lock_before
    assert project.status == "draft_input"
    assert result.result_refs == {
        "bible_revision_id": generated.id,
        "activated": False,
        "review_required": True,
    }
    assert artifact_event.payload["review_required"] is True
    assert _BibleAdapter.requests[0]["title"] == "Reviewable Bible"

    activate_bible_revision(db, project.id, generated.id)
    db.flush()
    selected_lock = head_after.lock_version
    assert head_after.current_bible_revision_id == generated.id
    assert project.status == "bible_generated"

    activate_bible_revision(db, project.id, generated.id)
    db.flush()
    assert head_after.lock_version == selected_lock


def test_generation_passes_frozen_api_instructions_to_provider(seeded):
    db, user, project, selected = seeded
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="bible-with-instructions",
        source_refs=build_bible_generation_source(
            project,
            parent_revision_id=selected.id,
        ),
        parameters={"instructions": "Preserve the sibling timelines"},
        estimated_cost=0,
    )
    db.commit()

    generate_bible_task(task.id, _claim_lease(db, task))

    assert "Preserve the sibling timelines" in _BibleAdapter.requests[0][
        "extra_requirements"
    ]


def test_recovering_generated_bible_keeps_review_required(seeded):
    db, user, project, selected = seeded
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="recover-bible",
        source_refs={"parent_revision_id": selected.id},
        estimated_cost=0,
    )
    db.commit()

    first = generate_bible_task(task.id, _claim_lease(db, task))
    recovered = generate_bible_task(task.id)

    assert first.result_refs["activated"] is False
    assert recovered.result_refs["activated"] is False
    assert recovered.result_refs["review_required"] is True
    assert recovered.result_refs["recovered"] is True
    assert _BibleAdapter.calls == 1


def test_identical_bible_outputs_keep_distinct_task_and_parent_provenance(seeded):
    db, user, project, selected = seeded
    alternate_parent = create_bible_revision(
        db,
        project_id=project.id,
        content={"world": "alternate parent"},
        source={"origin": "manual"},
        user_id=user.id,
        activate=False,
        parent_revision_id=selected.id,
    )
    first_source = build_bible_generation_source(
        project,
        parent_revision_id=selected.id,
    )
    project.title = "Alternate frozen project source"
    db.flush()
    second_source = build_bible_generation_source(
        project,
        parent_revision_id=alternate_parent.id,
    )
    first_task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="same-output-first",
        source_refs=first_source,
        estimated_cost=0,
    )
    second_task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="same-output-second",
        source_refs=second_source,
        estimated_cost=0,
    )
    db.commit()

    first_result = generate_bible_task(first_task.id, _claim_lease(db, first_task))
    second_result = generate_bible_task(second_task.id, _claim_lease(db, second_task))
    recovered = generate_bible_task(second_task.id)
    db.expire_all()
    first_revision = db.query(StoryBibleRevision).filter_by(
        generation_task_id=first_task.id
    ).one()
    second_revision = db.query(StoryBibleRevision).filter_by(
        generation_task_id=second_task.id
    ).one()

    assert first_revision.id != second_revision.id
    assert first_revision.content_hash == second_revision.content_hash
    assert first_revision.source_hash != second_revision.source_hash
    assert first_revision.parent_revision_id == selected.id
    assert second_revision.parent_revision_id == alternate_parent.id
    assert first_result.result_refs["bible_revision_id"] == first_revision.id
    assert second_result.result_refs["bible_revision_id"] == second_revision.id
    assert recovered.result_refs["bible_revision_id"] == second_revision.id
    assert recovered.result_refs["recovered"] is True
    assert _BibleAdapter.calls == 2


def test_bible_generation_route_freezes_selected_parent(seeded):
    db, user, project, selected = seeded
    accepted = revisions_router.generate_bible(
        project_id=project.id,
        body=GenerationRequest(parameters={}),
        idempotency_key="route-parent",
        db=db,
        user=user,
    )
    task = db.query(GenerationTask).filter_by(id=accepted.task_id).one()

    assert task.source_refs["parent_revision_id"] == selected.id
    assert task.source_refs["project_snapshot"]["title"] == project.title
    assert task.source_refs["project_source_hash"] == content_hash(
        task.source_refs["project_snapshot"]
    )


def test_bible_worker_rejects_tampered_source_before_provider_call(seeded):
    db, user, project, selected = seeded
    source_refs = build_bible_generation_source(
        project,
        parent_revision_id=selected.id,
    )
    source_refs["project_snapshot"]["title"] = "tampered"
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="bible.generate",
        idempotency_key="tampered-bible",
        source_refs=source_refs,
        estimated_cost=0,
    )
    db.commit()

    with pytest.raises(NonRetryableTaskError) as captured:
        generate_bible_task(task.id, _claim_lease(db, task))

    assert captured.value.code == "bible.source_invalid"
    assert _BibleAdapter.calls == 0
    assert db.query(StoryBibleRevision).filter_by(generation_task_id=task.id).count() == 0


def test_bible_revision_rejects_parent_from_another_project(seeded):
    db, user, project, _selected = seeded
    other = Project(
        owner_id=user.id,
        title="Other",
        story_start="Start",
        story_end="End",
    )
    db.add(other)
    db.flush()
    foreign = create_bible_revision(
        db,
        project_id=other.id,
        content={"world": "foreign"},
        source={"origin": "foreign"},
        user_id=user.id,
    )

    with pytest.raises(HTTPException) as captured:
        create_bible_revision(
            db,
            project_id=project.id,
            content={"world": "invalid parent"},
            source={"origin": "test"},
            user_id=user.id,
            activate=False,
            parent_revision_id=foreign.id,
        )

    assert captured.value.status_code == 409
