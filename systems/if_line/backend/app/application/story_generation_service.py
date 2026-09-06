from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from fastapi import HTTPException

from app.application.hashing import content_hash
from app.application.story_bible_service import (
    build_bible_generation_source,
    create_bible_revision,
    get_or_create_content_head,
)
from app.application.story_chapter_service import (
    create_story_path_chapter_revision,
    resolve_chapter_generation_source,
)
from app.application.story_outline_service import (
    create_story_path_outline_revision,
    normalize_outline_chapters,
    resolve_outline_generation_source,
)
from app.application.task_errors import NonRetryableTaskError
from app.application.task_service import (
    TaskLease,
    TaskLeaseLostError,
    append_task_event,
    complete_task,
    require_task_lease,
    update_parent_aggregate,
)
from app.database import SessionLocal
from app.integrations.llm import LegacyLLMAdapter, ModelCallResult
from app.models import Project
from app.models_v2 import (
    ChapterRevision,
    ChapterSegment,
    GenerationTask,
    OutlineRevision,
    ProviderUsageRecord,
    StoryBibleRevision,
    StoryPathOutlineHead,
)
from app.services.prompt_builder_service import prompt_builder_service
from app.services.source_visual_profile_service import source_visual_profile_service


@dataclass(frozen=True)
class GenerationExecutionResult:
    result_refs: dict[str, Any]
    actual_cost: Decimal


class _TaskTextSink:
    """Batch streamed deltas into durable replayable task events."""

    def __init__(self, task_id: str, lease: TaskLease, *, flush_chars: int = 96) -> None:
        self.task_id = task_id
        self.lease = lease
        self.flush_chars = flush_chars
        self.pending = ""
        self.emitted_chars = 0

    def __call__(self, delta: str) -> None:
        self.pending += delta
        if len(self.pending) < self.flush_chars and "\n" not in self.pending:
            return
        self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        session = SessionLocal()
        try:
            task = (
                session.query(GenerationTask)
                .filter(GenerationTask.id == self.task_id)
                .with_for_update()
                .one()
            )
            require_task_lease(task, self.lease)
            self.emitted_chars += len(self.pending)
            task.stage = "text_streaming"
            # Progress is deliberately capped until validation/persistence.
            task.progress = min(85.0, 5.0 + self.emitted_chars / 80.0)
            append_task_event(
                session,
                task,
                "text.delta",
                {"delta": self.pending, "emitted_chars": self.emitted_chars},
            )
            session.commit()
            self.pending = ""
        finally:
            session.close()


def _run(coro):
    return asyncio.run(coro)


def _record_provider_usage(session, task: GenerationTask, result: ModelCallResult[Any]) -> None:
    session.add(
        ProviderUsageRecord(
            task_id=task.id,
            provider=result.provider,
            model=result.model,
            provider_request_id=result.provider_request_id,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            cost_amount=task.reserved_cost or 0,
        )
    )


def _get_task(task_id: str, lease: TaskLease | None = None) -> GenerationTask:
    session = SessionLocal()
    try:
        task = session.query(GenerationTask).filter(GenerationTask.id == task_id).first()
        if not task:
            raise RuntimeError("generation task not found")
        if lease is None:
            if task.status != "succeeded":
                raise TaskLeaseLostError("generation execution requires a fenced lease")
        else:
            require_task_lease(task, lease)
        session.expunge(task)
        return task
    finally:
        session.close()


def _complete_generation(
    session,
    task: GenerationTask,
    lease: TaskLease,
    *,
    result_refs: dict[str, Any],
    actual_cost: Decimal,
) -> GenerationExecutionResult:
    complete_task(
        session,
        task,
        result_refs=result_refs,
        actual_cost=actual_cost,
        lease=lease,
    )
    update_parent_aggregate(session, task)
    session.commit()
    return GenerationExecutionResult(result_refs=result_refs, actual_cost=actual_cost)


def generate_bible_task(
    task_id: str,
    lease: TaskLease | None = None,
) -> GenerationExecutionResult:
    task = _get_task(task_id, lease)
    session = SessionLocal()
    try:
        persisted = session.query(StoryBibleRevision).filter(
            StoryBibleRevision.generation_task_id == task_id
        ).first()
        if persisted:
            result_refs = {
                "bible_revision_id": persisted.id,
                "activated": False,
                "review_required": True,
                "recovered": True,
            }
            actual_cost = Decimal(task.reserved_cost or 0)
            if lease is None:
                return GenerationExecutionResult(
                    result_refs=result_refs,
                    actual_cost=actual_cost,
                )
            durable_task = (
                session.query(GenerationTask)
                .filter(GenerationTask.id == task_id)
                .with_for_update()
                .one()
            )
            require_task_lease(durable_task, lease)
            return _complete_generation(
                session,
                durable_task,
                lease,
                result_refs=result_refs,
                actual_cost=actual_cost,
            )
        project = session.query(Project).filter(Project.id == task.project_id).first()
        if not project:
            raise NonRetryableTaskError(
                code="project.missing",
                safe_detail="项目不存在，无法生成故事圣经",
            )
        source_refs = task.source_refs or {}
        snapshot = source_refs.get("project_snapshot")
        snapshot_hash = source_refs.get("project_source_hash")
        if snapshot is not None:
            if (
                not isinstance(snapshot, dict)
                or snapshot.get("project_id") != project.id
                or not isinstance(snapshot_hash, str)
                or content_hash(snapshot) != snapshot_hash
            ):
                raise NonRetryableTaskError(
                    code="bible.source_invalid",
                    safe_detail="故事圣经生成输入已损坏，请重新发起生成",
                )
            request = {
                "title": snapshot.get("title") or "",
                "characters": snapshot.get("characters") or [],
                "story_start": snapshot.get("story_start") or "",
                "story_end": snapshot.get("story_end") or "",
                "style": snapshot.get("style") or "",
                "pace": snapshot.get("pace") or "medium",
                "extra_requirements": snapshot.get("extra_requirements") or "",
                "source_work": snapshot.get("source_work") or "",
                "uuid": project.id,
            }
            project_source_hash = snapshot_hash
        else:
            # Deployments may still have tasks queued before source snapshots
            # were introduced. They remain review-only and use the old marker.
            legacy_source = build_bible_generation_source(
                project,
                parent_revision_id=source_refs.get("parent_revision_id"),
            )
            snapshot = legacy_source["project_snapshot"]
            request = {
                "title": snapshot["title"],
                "characters": snapshot["characters"],
                "story_start": snapshot["story_start"],
                "story_end": snapshot["story_end"],
                "style": snapshot["style"],
                "pace": snapshot["pace"],
                "extra_requirements": snapshot["extra_requirements"],
                "source_work": snapshot["source_work"],
                "uuid": project.id,
            }
            project_source_hash = legacy_source["project_source_hash"]
        instructions = (task.parameters or {}).get("instructions")
        if isinstance(instructions, str) and instructions.strip():
            request["extra_requirements"] = "\n".join(
                value
                for value in (request["extra_requirements"], instructions.strip())
                if value
            )
    finally:
        session.close()

    provider_result = _run(LegacyLLMAdapter().generate_bible(**request))
    content = provider_result.data.model_dump()
    content = prompt_builder_service.enrich_story_bible_genders(
        content,
        context_text=" ".join(
            [request["title"], request["story_start"], request["story_end"], request["extra_requirements"]]
        ),
    )
    if request["source_work"]:
        content["source_work"] = request["source_work"]
    content = source_visual_profile_service.apply_detected_character_identities(content)

    session = SessionLocal()
    try:
        durable_task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        if lease is None:
            raise TaskLeaseLostError("generation persistence requires a fenced lease")
        require_task_lease(durable_task, lease)
        project = session.query(Project).filter(Project.id == durable_task.project_id).one()
        parent_kwargs: dict[str, Any] = {}
        if "parent_revision_id" in durable_task.source_refs:
            parent_kwargs["parent_revision_id"] = durable_task.source_refs.get(
                "parent_revision_id"
            )
        revision = create_bible_revision(
            session,
            project_id=project.id,
            content=content,
            source={
                "project_source_hash": project_source_hash,
                "prompt_version": provider_result.prompt_version,
            },
            user_id=durable_task.user_id,
            task_id=durable_task.id,
            activate=False,
            **parent_kwargs,
        )
        # head 为空时首个修订自动采用（见 create_bible_revision），result_refs
        # 须如实反映激活状态供审阅链与回填脚本消费。
        head = get_or_create_content_head(session, project.id)
        activated = head.current_bible_revision_id == revision.id
        review_required = not activated
        _record_provider_usage(session, durable_task, provider_result)
        append_task_event(
            session,
            durable_task,
            "artifact.ready",
            {
                "type": "bible_revision",
                "id": revision.id,
                "review_required": review_required,
            },
        )
        return _complete_generation(
            session,
            durable_task,
            lease,
            result_refs={
                "bible_revision_id": revision.id,
                "activated": activated,
                "review_required": review_required,
            },
            actual_cost=Decimal(durable_task.reserved_cost or 0),
        )
    finally:
        session.close()


def generate_outline_task(
    task_id: str,
    lease: TaskLease | None = None,
) -> GenerationExecutionResult:
    task = _get_task(task_id, lease)
    source_refs = task.source_refs or {}
    if not isinstance(source_refs.get("outline_source"), dict):
        raise NonRetryableTaskError(
            code="outline.source_schema_unsupported",
            safe_detail="旧版大纲任务不再受支持，请在 StoryPath 中重新发起生成",
        )
    session = SessionLocal()
    try:
        persisted = session.query(OutlineRevision).filter(
            OutlineRevision.generation_task_id == task_id
        ).first()
        if persisted:
            result_refs = {
                "outline_revision_id": persisted.id,
                "story_path_id": persisted.story_path_id,
                "activated": False,
                "review_required": True,
                "recovered": True,
            }
            actual_cost = Decimal(task.reserved_cost or 0)
            if lease is None:
                return GenerationExecutionResult(
                    result_refs=result_refs,
                    actual_cost=actual_cost,
                )
            durable_task = (
                session.query(GenerationTask)
                .filter(GenerationTask.id == task_id)
                .with_for_update()
                .one()
            )
            require_task_lease(durable_task, lease)
            return _complete_generation(
                session,
                durable_task,
                lease,
                result_refs=result_refs,
                actual_cost=actual_cost,
            )
        context = resolve_outline_generation_source(
            session,
            task_project_id=task.project_id,
            source_refs=source_refs,
        )
        story_bible = context.story_bible
        pace = context.pace
        chapter_count = context.chapter_count
        instructions = context.instructions
    finally:
        session.close()

    provider_result = _run(
        LegacyLLMAdapter().generate_outline(
            story_bible=story_bible,
            chapter_count=chapter_count,
            pace=pace,
            instructions=instructions,
            uuid=task.project_id,
        )
    )
    provider_chapters = [item.model_dump() for item in provider_result.data.chapters]
    chapters = [
        {
            **{key: value for key, value in item.items() if key != "chapter_index"},
            "display_index": item["chapter_index"],
            "story_path_chapter_id": None,
        }
        for item in provider_chapters
    ]
    try:
        normalized = normalize_outline_chapters(chapters, expected_count=chapter_count)
    except HTTPException as exc:
        raise RuntimeError(f"outline provider output is invalid: {exc.detail}") from exc
    # The provider controls presentation order only. Stable identities come from
    # the task's frozen PathChapter manifest and are never looked up by an index.
    for chapter, path_chapter_ref in zip(normalized, context.path_chapter_refs):
        chapter["story_path_chapter_id"] = path_chapter_ref[
            "story_path_chapter_id"
        ]

    session = SessionLocal()
    try:
        durable_task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        if lease is None:
            raise TaskLeaseLostError("generation persistence requires a fenced lease")
        require_task_lease(durable_task, lease)
        final_context = resolve_outline_generation_source(
            session,
            task_project_id=durable_task.project_id,
            source_refs=durable_task.source_refs or {},
        )
        revision = create_story_path_outline_revision(
            session,
            story_path_id=final_context.story_path_id,
            bible_revision_id=final_context.bible_revision_id,
            chapters=normalized,
            user_id=durable_task.user_id,
            parent_revision_id=final_context.parent_revision_id,
            source_manifest=final_context.source_manifest,
            task_id=durable_task.id,
        )
        # head 为空且与 Bible head 一致时首个大纲修订自动采用（见
        # create_story_path_outline_revision），result_refs 如实反映。
        # 纯查询不创建 head 行，保持"首次选择前 head 行不存在"的既有语义。
        outline_head = (
            session.query(StoryPathOutlineHead)
            .filter(
                StoryPathOutlineHead.story_path_id == final_context.story_path_id
            )
            .one_or_none()
        )
        activated = (
            outline_head is not None
            and outline_head.current_revision_id == revision.id
        )
        review_required = not activated
        _record_provider_usage(session, durable_task, provider_result)
        append_task_event(
            session,
            durable_task,
            "artifact.ready",
            {
                "type": "outline_revision",
                "id": revision.id,
                "story_path_id": revision.story_path_id,
                "review_required": review_required,
            },
        )
        return _complete_generation(
            session,
            durable_task,
            lease,
            result_refs={
                "outline_revision_id": revision.id,
                "story_path_id": revision.story_path_id,
                "activated": activated,
                "review_required": review_required,
            },
            actual_cost=Decimal(durable_task.reserved_cost or 0),
        )
    finally:
        session.close()


def generate_chapter_task(
    task_id: str,
    lease: TaskLease | None = None,
) -> GenerationExecutionResult:
    task = _get_task(task_id, lease)
    source_refs = task.source_refs or {}
    if not isinstance(source_refs.get("chapter_source"), dict):
        raise NonRetryableTaskError(
            code="chapter.source_schema_unsupported",
            safe_detail="旧版章节任务不再受支持，请在 StoryPath 中重新发起生成",
        )
    session = SessionLocal()
    try:
        persisted = session.query(ChapterRevision).filter(
            ChapterRevision.generation_task_id == task_id
        ).first()
        if persisted:
            result_refs = {
                "chapter_revision_id": persisted.id,
                "activated": False,
                "review_required": True,
                "recovered": True,
            }
            if persisted.created_for_story_path_id:
                result_refs.update(
                    {
                        "story_path_id": persisted.created_for_story_path_id,
                        "path_chapter_id": (
                            source_refs.get("chapter_source", {})
                            .get("context_manifest", {})
                            .get("path_chapter_id")
                        ),
                    }
                )
            actual_cost = Decimal(task.reserved_cost or 0)
            if lease is None:
                return GenerationExecutionResult(
                    result_refs=result_refs,
                    actual_cost=actual_cost,
                )
            durable_task = (
                session.query(GenerationTask)
                .filter(GenerationTask.id == task_id)
                .with_for_update()
                .one()
            )
            require_task_lease(durable_task, lease)
            return _complete_generation(
                session,
                durable_task,
                lease,
                result_refs=result_refs,
                actual_cost=actual_cost,
            )
        chapter_context = resolve_chapter_generation_source(
            session,
            task_project_id=task.project_id,
            source_refs=source_refs,
        )
        bible_content = chapter_context.story_bible
        previous = list(chapter_context.previous_chapters)
        ancestor_outline_summaries = list(chapter_context.ancestor_outline_summaries)
        outline_payload = chapter_context.chapter_outline
        state = chapter_context.state
        parameters = chapter_context.generation_parameters
    finally:
        session.close()

    adapter = LegacyLLMAdapter()
    if lease is None:
        raise TaskLeaseLostError("generation provider call requires a fenced lease")
    sink = _TaskTextSink(task_id, lease)
    generation_kwargs = {
        "story_bible": bible_content,
        "chapter_outline": outline_payload,
        "previous_chapters": previous,
        "word_count_min": int(parameters.get("word_count_min") or 3000),
        "word_count_max": int(parameters.get("word_count_max") or 4500),
        "state_snapshot": state,
        "instructions": parameters.get("instructions"),
        "ancestor_outline_summaries": ancestor_outline_summaries,
        "total_chapters": chapter_context.total_chapters,
        "uuid": task.project_id,
    }
    if parameters.get("stream", True):
        try:
            provider_result = _run(
                adapter.generate_chapter_streaming(
                    **generation_kwargs,
                    on_text_delta=sink,
                )
            )
            sink.flush()
        except Exception:
            # A provider may not implement OpenAI streaming semantics.  Falling
            # back before any user-visible delta preserves correctness.  Once
            # text has been emitted, retry the durable task instead of mixing
            # two different generations in one stream.
            if sink.emitted_chars:
                raise
            provider_result = _run(adapter.generate_chapter(**generation_kwargs))
    else:
        provider_result = _run(adapter.generate_chapter(**generation_kwargs))
    content = provider_result.data.content

    session = SessionLocal()
    try:
        durable_task = session.query(GenerationTask).filter(GenerationTask.id == task_id).with_for_update().one()
        require_task_lease(durable_task, lease)
        project = session.query(Project).filter(Project.id == task.project_id).one_or_none()
        if not project:
            raise NonRetryableTaskError(
                code="project.missing",
                safe_detail="项目不存在，无法保存章节正文",
            )
        final_context = resolve_chapter_generation_source(
            session,
            task_project_id=durable_task.project_id,
            source_refs=durable_task.source_refs or {},
        )
        revision = create_story_path_chapter_revision(
            session,
            context=final_context,
            content=content,
            user_id=durable_task.user_id,
            task_id=durable_task.id,
        )
        existing_segments = session.query(ChapterSegment).filter(ChapterSegment.chapter_revision_id == revision.id).count()
        if not existing_segments:
            paragraphs = [paragraph.strip() for paragraph in content.split("\n") if paragraph.strip()]
            for index, paragraph in enumerate(paragraphs):
                segment = ChapterSegment(
                    chapter_revision_id=revision.id,
                    order_index=index,
                    segment_key=f"p-{index + 1}-{content_hash(paragraph)[:12]}",
                    content=paragraph,
                    status="ready",
                )
                session.add(segment)
                append_task_event(
                    session,
                    durable_task,
                    "segment.ready",
                    {"chapter_revision_id": revision.id, "order_index": index, "content": paragraph},
                )
        _record_provider_usage(session, durable_task, provider_result)
        artifact_payload = {
            "type": "chapter_revision",
            "id": revision.id,
            "review_required": True,
        }
        result_refs = {
            "chapter_revision_id": revision.id,
            "activated": False,
            "review_required": True,
        }
        artifact_payload.update(
            {
                "story_path_id": final_context.story_path_id,
                "path_chapter_id": final_context.path_chapter_id,
            }
        )
        result_refs.update(
            {
                "story_path_id": final_context.story_path_id,
                "path_chapter_id": final_context.path_chapter_id,
            }
        )
        append_task_event(session, durable_task, "artifact.ready", artifact_payload)
        return _complete_generation(
            session,
            durable_task,
            lease,
            result_refs=result_refs,
            actual_cost=Decimal(durable_task.reserved_cost or 0),
        )
    finally:
        session.close()


def generate_chapter_script_task(
    task_id: str,
    lease: TaskLease | None = None,
) -> GenerationExecutionResult:
    # Imported lazily to keep the script application service independent from
    # the generic generation dispatcher during module initialization.
    from app.application.chapter_script_service import generate_chapter_script_task as execute

    return execute(task_id, lease=lease)


HANDLERS: dict[str, Callable[[str, TaskLease | None], GenerationExecutionResult]] = {
    "bible.generate": generate_bible_task,
    "outline.generate": generate_outline_task,
    "chapter.generate": generate_chapter_task,
    "chapter_script.generate": generate_chapter_script_task,
}


def execute_generation_task(
    task_id: str,
    lease: TaskLease,
) -> GenerationExecutionResult:
    task = _get_task(task_id, lease)
    handler = HANDLERS.get(task.kind)
    if not handler:
        raise NonRetryableTaskError(
            code="task.kind_unsupported",
            safe_detail="任务类型不受支持",
            diagnostic=f"unsupported task kind: {task.kind}",
        )
    return handler(task_id, lease)
