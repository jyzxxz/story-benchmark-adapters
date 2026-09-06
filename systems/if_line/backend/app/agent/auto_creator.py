"""
AutoCreator —— 自动化二创文章生成编排器。

链路:
  brainstorm idea → Project → Story Bible → Outline → 全部 ChapterContent

设计原则:
  - 复用 services/llm_service、services/workflow_engine、models 既有实现, 不重写。
  - 状态机一切走 WorkflowEngine.transition_to, 由它保证合法性。
  - 失败时写 error.log + manifest.status=failed, 并抛出, 让调用方决定重试策略。
  - 后期接入 asset / VN Graph / TTS 时, 只需实现对应的 _generate_* 私有方法
    并在 run() 里把对应开关从 presets 拉过来翻转即可, 主链路不动。
"""
import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models import (
    Project,
    StoryBible,
    ChapterOutline,
    ChapterContent,
    ProjectStatus,
)
from app.services.llm_service import llm_service
from app.services.prompt_builder_service import prompt_builder_service
from app.services.workflow_engine import WorkflowEngine
from app.agent import progress, resilient_llm, quality_check, ai_flavor_check
from app.agent.presets import (
    DEFAULT_WORD_COUNT_MIN,
    DEFAULT_WORD_COUNT_MAX,
    MAX_CHAPTER_FAILURES,
    AI_FLAVOR_MAX_RETRIES,
    ENABLE_ASSETS,
    ENABLE_TTS,
)
from app.agent.theme_brainstorm import brainstorm_idea
from app.core.story_outline import (
    DEFAULT_OUTLINE_CHAPTER_COUNT,
    validate_outline_chapter_count,
)


class AutoCreator:
    """单次全自动跑批的编排器。一个实例 = 一次 run。"""

    def __init__(
        self,
        db: Session,
        pace: str = "medium",
        theme_hint: Optional[str] = None,
        word_count_min: int = DEFAULT_WORD_COUNT_MIN,
        word_count_max: int = DEFAULT_WORD_COUNT_MAX,
        owner_id: Optional[int] = None,
        chapter_count: int = DEFAULT_OUTLINE_CHAPTER_COUNT,
    ) -> None:
        if pace not in ("fast", "medium", "slow"):
            pace = "medium"
        self.db = db
        self.pace = pace
        self.chapter_count = validate_outline_chapter_count(chapter_count)
        self.theme_hint = theme_hint
        self.word_count_min = word_count_min
        self.word_count_max = word_count_max
        self.owner_id = owner_id
        self.engine = WorkflowEngine(db)
        self.run_dir = progress.new_run_dir()

    async def _step_bridge_v2(self, project_id: int, chapters: List[Dict[str, Any]]) -> None:
        """把 legacy 产物桥接进 v2 revisions(bible/outline/chapter/script/vngraph)。

        AutoCreator 原本只写 story_bibles/chapter_contents 旧表,v2 审计管线
        (story_bible_revisions / chapter_script_revisions / vn_graph_revisions)
        完全看不到这些产物。这里复用 v2 既有服务建任务,由 celery worker 执行,
        不重写任何生成逻辑。
        """
        import time as _time

        from app.application.chapter_script_service import (
            create_chapter_script_generation_task,
        )
        from app.application.revision_service import (
            build_bible_generation_source,
            create_bible_revision,
        )
        from app.application.story_chapter_service import (
            create_manual_story_path_chapter_revision,
        )
        from app.application.story_outline_service import (
            activate_story_path_outline_revision,
            create_story_path_outline_revision,
        )
        from app.application.revision_head_service import (
            activate_path_chapter_revision_head,
        )
        from app.application.vn_graph_service import create_vn_graph_compile_task
        from app.models import User
        from app.models_v2 import GenerationTask, StoryPath, StoryPathOutlineHead

        print(f"[AutoCreator] Step 5/5 bridge v2 revisions")
        user = (
            self.db.query(User).filter(User.email == "autocreator@system.local").first()
        )
        if not user:
            user = User(
                email="autocreator@system.local",
                password_hash="!",
                display_name="AutoCreator",
            )
            self.db.add(user)
            self.db.commit()
            self.db.refresh(user)
        uid = user.id

        project = self.db.query(Project).filter(Project.id == project_id).first()
        bible = self.db.query(StoryBible).filter(StoryBible.project_id == project_id).first()

        bible_rev = create_bible_revision(
            self.db,
            project_id=project_id,
            content=bible.raw_json,
            source=build_bible_generation_source(project, parent_revision_id=None),
            user_id=uid,
            parent_revision_id=None,
        )
        self.db.commit()

        path = (
            self.db.query(StoryPath)
            .filter(StoryPath.project_id == project_id, StoryPath.parent_path_id.is_(None))
            .one_or_none()
        )
        if not path:
            path = StoryPath(
                project_id=project_id, title="Main", status="active", lock_version=1
            )
            self.db.add(path)
            self.db.flush()
            self.db.add(StoryPathOutlineHead(story_path_id=path.id, lock_version=1))
            self.db.commit()

        outline_payload = []
        for ch in chapters:
            payload = {k: ch.get(k) for k in (
                "title", "summary", "conflict", "characters",
                "scene", "emotion", "visual_keywords",
            )}
            payload["display_index"] = ch.get("chapter_index")
            outline_payload.append(payload)
        outline_rev = create_story_path_outline_revision(
            self.db,
            story_path_id=path.id,
            bible_revision_id=bible_rev.id,
            chapters=outline_payload,
            user_id=uid,
        )
        self.db.commit()
        activation = activate_story_path_outline_revision(
            self.db, story_path_id=path.id, revision_id=outline_rev.id
        )
        self.db.commit()

        def _wait_task(task_id: str, timeout: float = 600.0) -> GenerationTask:
            deadline = _time.monotonic() + timeout
            while _time.monotonic() < deadline:
                self.db.expire_all()
                t = self.db.query(GenerationTask).filter(GenerationTask.id == task_id).first()
                if t and t.status in ("succeeded", "failed", "cancelled"):
                    return t
                _time.sleep(3)
            raise RuntimeError(f"task {task_id} timed out")

        for placement in sorted(activation.path_chapters, key=lambda p: p.display_index):
            ci = placement.display_index
            content_row = (
                self.db.query(ChapterContent)
                .filter(
                    ChapterContent.project_id == project_id,
                    ChapterContent.chapter_index == ci,
                )
                .order_by(ChapterContent.id.desc())
                .first()
            )
            if not content_row:
                continue
            chapter_rev = create_manual_story_path_chapter_revision(
                self.db,
                path_chapter_id=placement.id,
                parent_revision_id=None,
                content=content_row.content,
                user_id=uid,
            )
            self.db.commit()
            activate_path_chapter_revision_head(
                self.db,
                path_chapter_id=placement.id,
                revision_id=chapter_rev.id,
                expected_lock_version=placement.lock_version,
            )
            self.db.commit()

            script_task, _ = create_chapter_script_generation_task(
                self.db,
                user_id=uid,
                chapter_revision_id=chapter_rev.id,
                idempotency_key=f"autocreator-script-{chapter_rev.id}",
            )
            self.db.commit()
            script_task = _wait_task(script_task.id)
            if script_task.status != "succeeded":
                print(f"[AutoCreator]   ch{ci} script task {script_task.status}, skip vngraph")
                continue
            script_rev_id = (script_task.result_refs or {}).get("chapter_script_revision_id")

            graph_task, _ = create_vn_graph_compile_task(
                self.db,
                user_id=uid,
                script_revision_id=script_rev_id,
                idempotency_key=f"autocreator-vngraph-{script_rev_id}",
            )
            self.db.commit()
            graph_task = _wait_task(graph_task.id)
            status = graph_task.status
            print(f"[AutoCreator]   ch{ci} script+vngraph -> {status}")

    async def run(self) -> int:
        """跑完整条生文链路, 返回 project_id。失败时抛出, manifest 已写 failed。"""
        started_at = datetime.utcnow()
        project_id: Optional[int] = None
        chapter_count = 0
        try:
            idea = await self._step_brainstorm()
            project_id = await self._step_create_project(idea)
            await self._step_generate_bible(project_id)
            chapters = await self._step_generate_outline(project_id)
            chapter_count = await self._step_generate_chapters(project_id, chapters)
            await self._step_bridge_v2(project_id, chapters)

            if ENABLE_ASSETS:
                await self._generate_assets(project_id)
            if ENABLE_TTS:
                await self._generate_tts(project_id)

            progress.write_manifest(
                self.run_dir,
                project_id=project_id,
                pace=self.pace,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                status="completed",
                chapter_count=chapter_count,
            )
            return project_id

        except Exception as exc:
            progress.write_error(self.run_dir, exc)
            progress.write_manifest(
                self.run_dir,
                project_id=project_id,
                pace=self.pace,
                started_at=started_at,
                finished_at=datetime.utcnow(),
                status="failed",
                chapter_count=chapter_count,
                error=str(exc),
            )
            raise

    # ---- Step impls ---------------------------------------------------------

    async def _step_brainstorm(self) -> Dict[str, Any]:
        print(f"[AutoCreator] Step 0/4 brainstorm (theme_hint={self.theme_hint!r})")
        idea = await brainstorm_idea(self.theme_hint)
        progress.write_json(self.run_dir, "idea.json", idea)
        print(
            f"[AutoCreator]   idea: 《{idea.get('title')}》 "
            f"theme={idea.get('theme_tag')} characters={idea.get('characters')}"
        )
        return idea

    async def _step_create_project(self, idea: Dict[str, Any]) -> int:
        print(f"[AutoCreator] Step 1/4 create project")
        project = Project(
            owner_id=self.owner_id,
            title=idea["title"],
            characters=idea.get("characters") or [],
            story_start=idea["story_start"],
            story_end=idea["story_end"],
            style=idea.get("style") or "",
            pace=idea.get("pace") or self.pace,
            extra_requirements=idea.get("extra_requirements") or "",
            status=ProjectStatus.draft_input.value,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        # Pace controls prose density only; chapter_count is an independent input.
        self.pace = project.pace or self.pace
        print(f"[AutoCreator]   project_id={project.id}")
        return project.id

    async def _step_generate_bible(self, project_id: int) -> None:
        print(f"[AutoCreator] Step 2/4 generate Story Bible")
        project = self.db.query(Project).filter(Project.id == project_id).first()

        bible_output = await resilient_llm.generate_story_bible(
            title=project.title,
            characters=project.characters or [],
            story_start=project.story_start,
            story_end=project.story_end,
            style=project.style or "",
            pace=project.pace or "medium",
            extra_requirements=project.extra_requirements or "",
        )
        bible_raw = prompt_builder_service.enrich_story_bible_genders(
            bible_output.dict(),
            context_text=" ".join([
                project.title or "",
                project.story_start or "",
                project.story_end or "",
                project.extra_requirements or "",
            ]),
        )
        if getattr(project, "source_work", None):
            bible_raw["source_work"] = project.source_work
        from app.services.source_visual_profile_service import source_visual_profile_service

        bible_raw = source_visual_profile_service.apply_detected_character_identities(
            bible_raw
        )

        story_bible = StoryBible(
            project_id=project_id,
            worldview=bible_raw.get("worldview"),
            characters=bible_raw.get("characters") or [],
            character_relations=bible_raw.get("character_relations"),
            main_conflict=bible_raw.get("main_conflict"),
            emotional_line=bible_raw.get("emotional_line"),
            style_rules=bible_raw.get("style_rules"),
            ending_constraints=bible_raw.get("ending_constraints"),
            forbidden_points=bible_raw.get("forbidden_points") or [],
            writing_notes=bible_raw.get("writing_notes") or [],
            raw_json=bible_raw,
        )
        self.db.add(story_bible)
        self.engine.transition_to(project, "bible_generated")
        self.db.commit()
        progress.write_json(self.run_dir, "bible.json", bible_raw)
        print(f"[AutoCreator]   bible saved")

    async def _step_generate_outline(self, project_id: int) -> List[Dict[str, Any]]:
        print(f"[AutoCreator] Step 3/4 generate outline")
        project = self.db.query(Project).filter(Project.id == project_id).first()
        bible = self.db.query(StoryBible).filter(StoryBible.project_id == project_id).first()

        outline_output = await resilient_llm.generate_chapter_outline(
            story_bible=bible.raw_json,
            chapter_count=self.chapter_count,
            pace=project.pace or "medium",
        )

        for chapter_data in outline_output.chapters:
            outline = ChapterOutline(
                project_id=project_id,
                chapter_index=chapter_data.chapter_index,
                title=chapter_data.title,
                summary=chapter_data.summary,
                conflict=chapter_data.conflict,
                characters=chapter_data.characters,
                scene=chapter_data.scene,
                emotion=chapter_data.emotion,
                visual_keywords=chapter_data.visual_keywords,
                status="approved",  # 跳过人工审核, 直接进入可生成状态
            )
            self.db.add(outline)

        # 状态机: bible_generated -> outline_generated -> outline_reviewing -> outline_approved
        self.engine.transition_to(project, "outline_generated")
        self.engine.transition_to(project, "outline_reviewing")
        self.engine.transition_to(project, "outline_approved")
        self.db.commit()

        chapters_payload = [c.dict() for c in outline_output.chapters]
        progress.write_json(self.run_dir, "outline.json", chapters_payload)
        print(f"[AutoCreator]   outline saved, {len(chapters_payload)} chapters")
        return chapters_payload

    async def _step_generate_chapters(
        self, project_id: int, chapters: List[Dict[str, Any]]
    ) -> int:
        print(f"[AutoCreator] Step 4/4 generate chapters ({len(chapters)} total)")
        project = self.db.query(Project).filter(Project.id == project_id).first()
        bible = self.db.query(StoryBible).filter(StoryBible.project_id == project_id).first()

        if project.status == "outline_approved":
            self.engine.transition_to(project, "chapter_generating")
            self.db.commit()

        chapters_summary: Dict[int, str] = {}
        for idx, ch in enumerate(chapters, 1):
            ci = ch["chapter_index"]
            outline = self.db.query(ChapterOutline).filter(
                ChapterOutline.project_id == project_id,
                ChapterOutline.chapter_index == ci,
            ).first()
            if not outline:
                continue

            content_output = None
            candidate = None
            quality_reasons: List[str] = []
            flavor_issues: List[str] = []
            rewrite_hint = ""
            flavor_rewrites = 0
            attempts = 0  # 首次生成与所有重写共享 MAX_CHAPTER_FAILURES 配额
            while content_output is None:
                attempts += 1
                try:
                    if candidate is None:
                        candidate = await resilient_llm.generate_chapter_content(
                            story_bible=bible.raw_json,
                            chapter_outline={
                                "chapter_index": outline.chapter_index,
                                "title": outline.title,
                                "summary": outline.summary,
                                "conflict": outline.conflict,
                                "characters": outline.characters,
                                "scene": outline.scene,
                                "emotion": outline.emotion,
                            },
                            previous_chapters=[
                                {
                                    "chapter_index": previous_index,
                                    "summary": summary,
                                }
                                for previous_index, summary in chapters_summary.items()
                            ],
                            word_count_min=self.word_count_min,
                            word_count_max=self.word_count_max,
                        )
                    else:
                        candidate = await resilient_llm.rewrite_chapter_content(
                            story_bible=bible.raw_json,
                            chapter_outline={
                                "chapter_index": outline.chapter_index,
                                "title": outline.title,
                                "summary": outline.summary,
                                "conflict": outline.conflict,
                                "characters": outline.characters,
                                "scene": outline.scene,
                                "emotion": outline.emotion,
                            },
                            previous_chapters=[
                                {
                                    "chapter_index": previous_index,
                                    "summary": summary,
                                }
                                for previous_index, summary in chapters_summary.items()
                            ],
                            draft_result=(
                                candidate.model_dump()
                                if hasattr(candidate, "model_dump")
                                else candidate.dict()
                            ),
                            quality_reasons=quality_reasons,
                            flavor_issues=flavor_issues,
                            rewrite_hint=rewrite_hint,
                            word_count_min=self.word_count_min,
                            word_count_max=self.word_count_max,
                        )
                except Exception as exc:
                    # resilient_llm 已经吃过 3 次重试, 走到这里说明真的救不回来了
                    print(f"[AutoCreator]   chapter {ci} LLM 失败 ({attempts}/{MAX_CHAPTER_FAILURES}): {exc}")
                    if attempts >= MAX_CHAPTER_FAILURES:
                        raise RuntimeError(
                            f"chapter {ci} generation failed {attempts} times, aborting run"
                        ) from exc
                    continue

                # 先跑免费、确定性的质量检查；通过后才调用付费的 AI 味评审。
                check = quality_check.check_chapter_content(
                    candidate,
                    chapter_outline={
                        "title": outline.title,
                        "characters": outline.characters or [],
                    },
                    word_count_min=self.word_count_min,
                )
                quality_reasons = list(check.reasons)
                flavor_report = None
                if check.passed:
                    flavor_report = await ai_flavor_check.check(
                        candidate.content,
                        bible.style_rules or "",
                    )
                flavor_issues = list(flavor_report.issues) if flavor_report else []
                rewrite_hint = flavor_report.rewrite_hint if flavor_report else ""
                progress.write_json(
                    self.run_dir,
                    f"chapter_{ci}_flavor.json",
                    {
                        "chapter_index": ci,
                        "attempt": attempts,
                        "quality": {
                            "passed": check.passed,
                            "reasons": quality_reasons,
                        },
                        "flavor": (
                            {
                                "passed": flavor_report.passed,
                                "score": flavor_report.score,
                                "issues": flavor_issues,
                                "rewrite_hint": rewrite_hint,
                            }
                            if flavor_report
                            else {
                                "passed": None,
                                "score": None,
                                "issues": [],
                                "rewrite_hint": "",
                                "skipped": "deterministic quality check failed",
                            }
                        ),
                    },
                )

                if check.passed and flavor_report and flavor_report.passed:
                    content_output = candidate
                else:
                    if not check.passed:
                        print(f"[AutoCreator]   chapter {ci} 质量不达标 ({attempts}/{MAX_CHAPTER_FAILURES}): "
                              f"{'; '.join(quality_reasons)}")
                    else:
                        print(f"[AutoCreator]   chapter {ci} AI 味不达标 "
                              f"({attempts}/{MAX_CHAPTER_FAILURES}, "
                              f"score={flavor_report.score}): "
                              f"{'; '.join(flavor_issues)}")
                    retry_budget_exhausted = attempts >= MAX_CHAPTER_FAILURES
                    flavor_budget_exhausted = (
                        flavor_report is not None
                        and not flavor_report.passed
                        and flavor_rewrites >= AI_FLAVOR_MAX_RETRIES
                    )
                    if retry_budget_exhausted or flavor_budget_exhausted:
                        # 保留容错语义：评审一直不通过时接受最后一个可用版本，
                        # 避免单章边缘质量问题让整篇任务失败。
                        print(f"[AutoCreator]   chapter {ci} 重写配额用尽, 接受最后一次输出")
                        content_output = candidate
                    elif flavor_report is not None and not flavor_report.passed:
                        flavor_rewrites += 1

            chapter_content = ChapterContent(
                project_id=project_id,
                chapter_index=ci,
                content=content_output.content,
                version=1,
                status="completed",
            )
            self.db.add(chapter_content)
            self.db.commit()
            chapters_summary[ci] = (content_output.content or "")[-800:]
            print(f"[AutoCreator]   chapter {idx}/{len(chapters)} (idx={ci}) done")

        progress.write_json(self.run_dir, "chapters.json", chapters_summary)

        # 收尾: chapter_generating -> pre_generating
        self.engine.transition_to(project, "pre_generating")
        self.db.commit()

        return len(chapters_summary)

    # ---- 后期扩展位 (本期不实现, 留 stub) -----------------------------------

    async def _generate_assets(self, project_id: int) -> None:
        """预留: 调 llm_service.generate_asset_prompts + image_generation_service。
        实现时填这里, run() 主链路无需改动。"""
        print(f"[AutoCreator] _generate_assets: not implemented yet (ENABLE_ASSETS off)")

    async def _generate_tts(self, project_id: int) -> None:
        """预留: 调 tts_service。"""
        print(f"[AutoCreator] _generate_tts: not implemented yet (ENABLE_TTS off)")


async def run_many(
    db: Session,
    count: int = 1,
    pace: str = "medium",
    theme_hint: Optional[str] = None,
    chapter_count: int = DEFAULT_OUTLINE_CHAPTER_COUNT,
) -> List[int]:
    """连续跑 count 次, 返回所有成功 project_id 列表。
    单次失败不影响后续, 但会打印错误。"""
    project_ids: List[int] = []
    for i in range(count):
        creator = AutoCreator(
            db,
            pace=pace,
            chapter_count=chapter_count,
            theme_hint=theme_hint,
        )
        try:
            pid = await creator.run()
            project_ids.append(pid)
            print(f"[AutoCreator] run {i + 1}/{count} -> project_id={pid}")
        except Exception as exc:
            print(f"[AutoCreator] run {i + 1}/{count} FAILED: {exc}")
    return project_ids
