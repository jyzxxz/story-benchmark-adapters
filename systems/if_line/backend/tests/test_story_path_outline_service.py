from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application import story_generation_service
from app.application.revision_service import activate_bible_revision, create_bible_revision
from app.application.story_context_resolver import (
    StoryContextResolutionError,
    StoryContextResolver,
)
from app.application.story_generation_service import generate_outline_task
from app.application.story_outline_service import (
    activate_story_path_outline_revision,
    build_outline_generation_source,
    create_story_path_outline_revision,
)
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import claim_task, create_generation_task, lease_from_task
from app.core.story_outline import validate_outline_chapter_count
from app.integrations.llm import ModelCallResult
from app.models import Project, User
from app.models_v2 import (
    ChapterRevision,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
    TaskEvent,
)
from app.orm_base import Base
from app.schemas import ChapterOutlineCreate, LLMChapterOutlineOutput
from app.services.prompt_templates import get_chapter_outline_prompt


class _OutlineAdapter:
    calls: list[dict] = []
    returned_count: int | None = None

    async def generate_outline(self, **kwargs):
        type(self).calls.append(kwargs)
        count = type(self).returned_count
        if count is None:
            count = kwargs["chapter_count"]
        return ModelCallResult(
            data=LLMChapterOutlineOutput(
                chapters=[
                    ChapterOutlineCreate(
                        chapter_index=index,
                        title=f"Chapter {index}",
                        summary=f"Summary {index}",
                        characters=["A"],
                        visual_keywords=[f"scene-{index}"],
                    )
                    for index in range(1, count + 1)
                ]
            ),
            raw_text=None,
            provider="test",
            model="outline-test",
            provider_request_id=None,
            input_tokens=10,
            output_tokens=20,
            latency_ms=1,
            prompt_version="outline-test-v1",
        )


def _claim_lease(db, task):
    claimed = claim_task(db, task.id, f"outline-test:{task.id}")
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
        email="story-outline@example.com",
        password_hash="hash",
        display_name="Story Outline",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Arbitrary outline",
        story_start="Start",
        story_end="End",
        pace="fast",
    )
    db.add(project)
    db.flush()
    path = StoryPath(project_id=project.id, title="Main")
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
    db.commit()

    _OutlineAdapter.calls = []
    _OutlineAdapter.returned_count = None
    monkeypatch.setattr(story_generation_service, "SessionLocal", factory)
    monkeypatch.setattr(story_generation_service, "LegacyLLMAdapter", _OutlineAdapter)
    try:
        yield db, user, project, path, bible
    finally:
        db.close()
        engine.dispose()


def _chapters(count: int, *, path_chapter_ids: list[str | None] | None = None):
    path_chapter_ids = path_chapter_ids or [None] * count
    return [
        {
            "story_path_chapter_id": path_chapter_ids[index - 1],
            "display_index": index,
            "title": f"Chapter {index}",
            "summary": f"Summary {index}",
            "characters": ["A"],
            "visual_keywords": [],
        }
        for index in range(1, count + 1)
    ]


@pytest.mark.parametrize("chapter_count", [1, 7, 37, 500])
def test_chapter_count_policy_accepts_the_full_contract_range(chapter_count):
    assert validate_outline_chapter_count(chapter_count) == chapter_count


@pytest.mark.parametrize("chapter_count", [0, 501, True, 13.0, "13"])
def test_chapter_count_policy_rejects_invalid_values(chapter_count):
    with pytest.raises(ValueError):
        validate_outline_chapter_count(chapter_count)


def test_outline_prompt_keeps_count_independent_from_pace():
    prompt = get_chapter_outline_prompt(
        {"worldview": "Test"},
        37,
        pace="fast",
        instructions="Use a five-act structure",
    )
    assert "恰好包含 37 项" in prompt
    assert "连续编号到 37" in prompt
    assert "节奏偏好「fast」" in prompt
    assert "Use a five-act structure" in prompt


def test_outline_prompt_enforces_inter_chapter_continuity():
    prompt = get_chapter_outline_prompt({"worldview": "Test"}, 5)
    assert "章章衔接铁律" in prompt
    assert "承接上一章 ending_hook" in prompt
    assert "只保留一条因果链" in prompt
    assert "第 N 章开头与第 N-1 章结尾状态是否衔接" in prompt


def test_outline_prompt_enforces_character_consistency():
    prompt = get_chapter_outline_prompt({"worldview": "Test"}, 5)
    assert "角色设定一致性" in prompt
    assert "characters 数组中的设定逐字一致" in prompt
    assert "不得重新发明或改写角色身份" in prompt


def test_outline_prompt_enforces_scene_location_completeness():
    prompt = get_chapter_outline_prompt({"worldview": "Test"}, 5)
    assert "场景完整性" in prompt
    assert "每次换景一处不漏" in prompt


def test_revise_outline_prompt_enforces_continuity_and_character_consistency():
    from app.services.prompt_templates import get_revise_outline_prompt

    prompt = get_revise_outline_prompt(
        [{"chapter_index": 1, "title": "风暴夜", "summary": "…"}],
        "第二章节奏太慢",
        {"worldview": "Test"},
    )
    assert "角色设定一致性" in prompt
    assert "章章衔接" in prompt
    assert "不得重新发明或改写身份" in prompt


@pytest.mark.parametrize("chapter_count", [8, 13, 20, 120])
def test_generation_uses_requested_count_and_preserves_ids(seeded, chapter_count):
    db, user, project, path, bible = seeded
    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=chapter_count,
        instructions="Keep the middle act tense",
    )
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key=f"outline-{chapter_count}",
        source_refs=source_refs,
        parameters={"chapter_count": chapter_count},
        estimated_cost=Decimal("0"),
    )
    competing_bible = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Selected while generation runs"},
        source={"origin": "competing"},
        user_id=user.id,
        activate=True,
    )
    db.commit()

    result = generate_outline_task(task.id, _claim_lease(db, task))
    db.expire_all()

    revision = db.query(OutlineRevision).filter_by(generation_task_id=task.id).one()
    rows = (
        db.query(OutlineChapter)
        .filter_by(outline_revision_id=revision.id)
        .order_by(OutlineChapter.display_index)
        .all()
    )
    content_head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    artifact = db.query(TaskEvent).filter_by(task_id=task.id, event_type="artifact.ready").one()
    assert _OutlineAdapter.calls[0]["chapter_count"] == chapter_count
    assert _OutlineAdapter.calls[0]["pace"] == "fast"
    assert _OutlineAdapter.calls[0]["instructions"] == "Keep the middle act tense"
    assert len(rows) == chapter_count
    assert [row.display_index for row in rows] == list(
        range(1, chapter_count + 1)
    )
    assert all(row.story_path_chapter_id is None for row in rows)
    assert revision.story_path_id == path.id
    assert revision.bible_revision_id == bible.id
    assert revision.status == "ready"
    assert db.query(StoryPathOutlineHead).filter_by(story_path_id=path.id).count() == 0
    assert db.query(StoryPathChapter).filter_by(story_path_id=path.id).count() == 0
    assert content_head.current_bible_revision_id == competing_bible.id
    assert result.result_refs == {
        "outline_revision_id": revision.id,
        "story_path_id": path.id,
        "activated": False,
        "review_required": True,
    }
    assert artifact.payload["review_required"] is True

    with pytest.raises(HTTPException) as captured:
        activate_story_path_outline_revision(
            db,
            story_path_id=path.id,
            revision_id=revision.id,
        )
    assert captured.value.status_code == 409

    activate_bible_revision(db, project.id, bible.id)
    activated = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=revision.id,
    )
    db.commit()
    assert len(activated.path_chapters) == chapter_count
    assert activated.head.current_revision_id == revision.id

    stable_ids = [placement.id for placement in activated.path_chapters]
    reviewed = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(chapter_count, path_chapter_ids=stable_ids),
        user_id=user.id,
    )
    reviewed_activation = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=reviewed.id,
    )
    assert [placement.id for placement in reviewed_activation.path_chapters] == stable_ids


def test_worker_rejects_legacy_outline_task_source_schema(seeded):
    db, user, project, _path, bible = seeded
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="legacy-outline-11",
        source_refs={"bible_revision_id": bible.id},
        parameters={"chapter_count": 11},
        estimated_cost=0,
    )
    db.commit()

    with pytest.raises(NonRetryableTaskError) as captured:
        generate_outline_task(task.id, _claim_lease(db, task))

    assert captured.value.code == "outline.source_schema_unsupported"
    assert _OutlineAdapter.calls == []
    assert db.query(OutlineRevision).filter_by(generation_task_id=task.id).count() == 0


def test_generation_binds_only_frozen_path_chapter_ids(seeded):
    db, user, project, path, bible = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(3),
        user_id=user.id,
    )
    original = list(
        activate_story_path_outline_revision(
            db,
            story_path_id=path.id,
            revision_id=first.id,
        ).path_chapters
    )
    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=3,
    )
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="outline-frozen-path-chapters",
        source_refs=source_refs,
        parameters={"chapter_count": 3},
        estimated_cost=0,
    )
    db.commit()

    generate_outline_task(task.id, _claim_lease(db, task))
    generated = (
        db.query(OutlineRevision)
        .filter_by(generation_task_id=task.id)
        .one()
    )
    rows = (
        db.query(OutlineChapter)
        .filter_by(outline_revision_id=generated.id)
        .order_by(OutlineChapter.display_index)
        .all()
    )

    assert [row.story_path_chapter_id for row in rows] == [
        placement.id for placement in original
    ]


def test_activation_preserves_ids_when_reordering_and_expanding(seeded):
    db, user, _project, path, bible = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(3),
        user_id=user.id,
    )
    first_activation = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=first.id,
    )
    db.commit()
    original_ids = [chapter.id for chapter in first_activation.path_chapters]
    original_slots = {
        chapter.id: chapter.chapter_slot_id for chapter in first_activation.path_chapters
    }

    reordered_ids = [original_ids[1], original_ids[0], original_ids[2], None]
    second = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(4, path_chapter_ids=reordered_ids),
        user_id=user.id,
    )
    second_activation = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=second.id,
    )
    db.commit()

    ordered = list(second_activation.path_chapters)
    assert [chapter.id for chapter in ordered[:3]] == reordered_ids[:3]
    assert ordered[3].id not in original_ids
    assert ordered[0].chapter_slot_id == original_slots[original_ids[1]]
    assert ordered[1].chapter_slot_id == original_slots[original_ids[0]]
    assert ordered[0].predecessor_path_chapter_id is None
    assert ordered[1].predecessor_path_chapter_id == ordered[0].id
    assert ordered[2].predecessor_path_chapter_id == ordered[1].id
    assert ordered[3].predecessor_path_chapter_id == ordered[2].id
    first_rows = (
        db.query(OutlineChapter)
        .filter_by(outline_revision_id=first.id)
        .order_by(OutlineChapter.display_index)
        .all()
    )
    assert [row.story_path_chapter_id for row in first_rows] == original_ids


def test_activation_detaches_removed_chapter_without_deleting_revisions(seeded):
    db, user, project, path, bible = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(3),
        user_id=user.id,
    )
    first_activation = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=first.id,
    )
    db.commit()
    path_chapters = list(first_activation.path_chapters)
    third = path_chapters[2]
    chapter_revision = ChapterRevision(
        project_id=project.id,
        chapter_slot_id=third.chapter_slot_id,
        created_for_story_path_id=path.id,
        chapter_index=3,
        bible_revision_id=bible.id,
        outline_revision_id=first.id,
        revision_no=1,
        source_hash="a" * 64,
        context_manifest={"path_chapter_id": third.id},
        context_hash="b" * 64,
        content_hash="c" * 64,
        content="Reviewed third chapter",
        status="complete",
        created_by=user.id,
    )
    db.add(chapter_revision)
    db.commit()

    shorter = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2, path_chapter_ids=[path_chapters[0].id, path_chapters[1].id]),
        user_id=user.id,
    )
    activated = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=shorter.id,
    )
    db.commit()

    assert [chapter.id for chapter in activated.path_chapters] == [
        path_chapters[0].id,
        path_chapters[1].id,
    ]
    head = db.query(StoryPathOutlineHead).filter_by(story_path_id=path.id).one()
    assert head.current_revision_id == shorter.id
    db.refresh(third)
    assert third.status == "detached"
    assert third.detached_at is not None
    assert third.detached_by_outline_revision_id == shorter.id
    assert third.predecessor_path_chapter_id is None
    assert db.query(ChapterRevision).filter_by(id=chapter_revision.id).one().content == (
        "Reviewed third chapter"
    )
    assert [
        chapter.id
        for chapter in db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id, status="active")
        .order_by(StoryPathChapter.display_index)
        .all()
    ] == [chapter.id for chapter in path_chapters[:2]]
    with pytest.raises(StoryContextResolutionError) as captured:
        StoryContextResolver(db).resolve(third.id)
    assert captured.value.code == "context.path_chapter_missing"


def test_activation_requires_explicit_uuid_to_reattach_unwritten_chapters(seeded):
    db, user, _project, path, bible = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(4),
        user_id=user.id,
    )
    original = list(
        activate_story_path_outline_revision(
            db,
            story_path_id=path.id,
            revision_id=first.id,
        ).path_chapters
    )
    db.commit()

    shorter = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(2, path_chapter_ids=[original[0].id, original[1].id]),
        user_id=user.id,
    )
    shortened = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=shorter.id,
    )
    db.commit()
    assert [chapter.id for chapter in shortened.path_chapters] == [
        original[0].id,
        original[1].id,
    ]
    assert db.query(StoryPathChapter).filter_by(story_path_id=path.id).count() == 4
    assert {
        chapter.id
        for chapter in db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id, status="detached")
        .all()
    } == {original[2].id, original[3].id}

    expanded = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(4, path_chapter_ids=[original[0].id, original[1].id, None, None]),
        user_id=user.id,
    )
    reattached = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=expanded.id,
    )
    db.commit()
    replacement_ids = [chapter.id for chapter in reattached.path_chapters]
    assert replacement_ids[:2] == [original[0].id, original[1].id]
    assert replacement_ids[2:] != [original[2].id, original[3].id]
    assert db.query(StoryPathChapter).filter_by(story_path_id=path.id).count() == 6
    assert db.query(StoryPathChapter).filter_by(
        story_path_id=path.id, status="active"
    ).count() == 4

    explicit = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(4, path_chapter_ids=[chapter.id for chapter in original]),
        user_id=user.id,
    )
    restored = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=explicit.id,
    )
    db.commit()
    assert [chapter.id for chapter in restored.path_chapters] == [
        chapter.id for chapter in original
    ]
    assert {
        chapter.id
        for chapter in db.query(StoryPathChapter)
        .filter_by(story_path_id=path.id, status="detached")
        .all()
    } == set(replacement_ids[2:])


def test_revision_rejects_path_chapter_owned_by_another_path(seeded):
    db, user, _project, path, bible = seeded
    first = create_story_path_outline_revision(
        db,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        chapters=_chapters(1),
        user_id=user.id,
    )
    foreign_chapter = activate_story_path_outline_revision(
        db,
        story_path_id=path.id,
        revision_id=first.id,
    ).path_chapters[0]

    other_project = Project(
        owner_id=user.id,
        title="Other project",
        story_start="Start",
        story_end="End",
    )
    db.add(other_project)
    db.flush()
    other_path = StoryPath(project_id=other_project.id, title="Other path")
    db.add(other_path)
    db.flush()
    other_bible = create_bible_revision(
        db,
        project_id=other_project.id,
        content={"worldview": "Other"},
        source={"origin": "other"},
        user_id=user.id,
    )

    with pytest.raises(HTTPException) as captured:
        create_story_path_outline_revision(
            db,
            story_path_id=other_path.id,
            bible_revision_id=other_bible.id,
            chapters=_chapters(1, path_chapter_ids=[foreign_chapter.id]),
            user_id=user.id,
        )
    assert captured.value.status_code == 409


def test_worker_rejects_tampered_frozen_source_before_provider_call(seeded):
    db, user, project, path, _bible = seeded
    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=9,
    )
    source_refs["outline_source"]["chapter_count"] = 10
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="tampered-outline",
        source_refs=source_refs,
        estimated_cost=0,
    )
    db.commit()

    with pytest.raises(NonRetryableTaskError) as captured:
        generate_outline_task(task.id, _claim_lease(db, task))

    assert captured.value.code == "outline.source_invalid"
    assert _OutlineAdapter.calls == []
    assert db.query(OutlineRevision).filter_by(generation_task_id=task.id).count() == 0


def test_worker_rejects_provider_count_mismatch_without_persisting(seeded):
    db, user, project, path, _bible = seeded
    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=6,
    )
    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="wrong-count-outline",
        source_refs=source_refs,
        estimated_cost=0,
    )
    db.commit()
    _OutlineAdapter.returned_count = 5

    with pytest.raises(RuntimeError, match="expected 6"):
        generate_outline_task(task.id, _claim_lease(db, task))

    assert db.query(OutlineRevision).filter_by(generation_task_id=task.id).count() == 0


def test_generation_source_anchors_explicit_bible_revision(seeded):
    db, user, project, path, _bible = seeded
    anchored = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Draft world"},
        source={"origin": "manual_edit"},
        user_id=user.id,
        activate=False,
    )
    competing = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "Head moved elsewhere"},
        source={"origin": "competing"},
        user_id=user.id,
        activate=True,
    )
    source_refs = build_outline_generation_source(
        db,
        story_path_id=path.id,
        chapter_count=2,
        bible_revision_id=anchored.id,
    )
    source = source_refs["outline_source"]
    assert source["bible_revision_id"] == anchored.id
    assert source["bible_content_hash"] == anchored.content_hash

    task, _ = create_generation_task(
        db,
        user_id=user.id,
        project_id=project.id,
        kind="outline.generate",
        idempotency_key="anchored-outline",
        source_refs=source_refs,
        parameters={"chapter_count": 2},
        estimated_cost=Decimal("0"),
    )
    db.commit()

    generate_outline_task(task.id, _claim_lease(db, task))
    db.expire_all()

    revision = db.query(OutlineRevision).filter_by(generation_task_id=task.id).one()
    # head 已被 competing 抢走，但任务仍基于锚定的 bible 草稿。
    content_head = db.query(ProjectContentHead).filter_by(project_id=project.id).one()
    assert content_head.current_bible_revision_id == competing.id
    assert revision.bible_revision_id == anchored.id


def test_generation_source_rejects_invalid_bible_anchor(seeded):
    db, _user, _project, path, _bible = seeded
    with pytest.raises(HTTPException) as captured:
        build_outline_generation_source(
            db,
            story_path_id=path.id,
            chapter_count=2,
            bible_revision_id="0" * 36,
        )
    assert captured.value.status_code == 422
