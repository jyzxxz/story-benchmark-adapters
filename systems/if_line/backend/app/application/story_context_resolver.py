from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.models_v2 import (
    BranchCandidate,
    CandidateSetRevision,
    ChapterRevision,
    ChapterSlot,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StateSnapshot,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)


class StoryContextResolutionError(RuntimeError):
    """A stable, transport-independent failure raised while freezing context."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ResolvedAncestor:
    path_chapter_id: str
    chapter_revision_id: str
    display_index: int
    content_hash: str
    content: str


@dataclass(frozen=True)
class ResolvedStoryContext:
    manifest: dict[str, Any]
    context_hash: str
    story_bible: dict[str, Any]
    outline_chapter: dict[str, Any]
    state: dict[str, Any] | None
    ancestors: tuple[ResolvedAncestor, ...]
    fork_choice: dict[str, Any] | None


class StoryContextResolver:
    """Freeze and validate chapter input using stable StoryPath identities."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve(
        self,
        path_chapter_id: str,
        *,
        state_snapshot_id: str | None = None,
        bible_revision_id: str | None = None,
        outline_revision_id: str | None = None,
        ancestor_revision_overrides: Mapping[str, str] | None = None,
    ) -> ResolvedStoryContext:
        target, path = self._target_and_path(path_chapter_id)
        self._require_active_path(path)
        self._validate_path_fork(path)

        if (bible_revision_id is None) != (outline_revision_id is None):
            self._fail(
                "context.revision_anchor_incomplete",
                "批次上下文必须同时固定 Bible 和 Outline revision",
            )
        selected_bible_revision_id = bible_revision_id
        selected_outline_revision_id = outline_revision_id
        if selected_bible_revision_id is None:
            content_head = (
                self.db.query(ProjectContentHead)
                .filter(ProjectContentHead.project_id == path.project_id)
                .one_or_none()
            )
            if not content_head or not content_head.current_bible_revision_id:
                self._fail(
                    "context.bible_head_missing",
                    "尚无被采用为当前版本的故事设定：请重新生成设定（首个将自动采用）"
                    "或使用固化发布",
                )
            outline_head = (
                self.db.query(StoryPathOutlineHead)
                .filter(StoryPathOutlineHead.story_path_id == path.id)
                .one_or_none()
            )
            if not outline_head or not outline_head.current_revision_id:
                self._fail(
                    "context.outline_head_missing",
                    "尚无被采用为当前版本的章节大纲：请重新生成大纲（首个将自动采用）"
                    "或使用固化发布",
                )
            selected_bible_revision_id = content_head.current_bible_revision_id
            selected_outline_revision_id = outline_head.current_revision_id

        bible, outline_chapter = self._load_bible_and_outline_chapter(
            project_id=path.project_id,
            story_path_id=path.id,
            target=target,
            bible_revision_id=selected_bible_revision_id,
            outline_revision_id=selected_outline_revision_id,
        )
        ancestors = self._selected_ancestor_manifest(
            path,
            target,
            revision_overrides=ancestor_revision_overrides,
        )
        selected_state_id = (
            state_snapshot_id
            if state_snapshot_id is not None
            else path.base_state_snapshot_id
        )
        state = self._load_state(path.project_id, selected_state_id)
        bible_digest = self._verified_payload_hash(
            stored_hash=bible.content_hash,
            payload=bible.content_json,
            code="context.bible_payload_invalid",
            detail="Story Bible revision payload 与 content_hash 不匹配",
        )
        _outline_payload, outline_digest = self._verified_outline_chapter_payload(
            target=target,
            outline_chapter=outline_chapter,
            code="context.outline_payload_invalid",
            detail="Outline chapter payload 与 content_hash 不匹配",
        )
        state_digest = (
            self._verified_payload_hash(
                stored_hash=state.state_hash,
                payload=state.state_json,
                code="context.state_payload_invalid",
                detail="StateSnapshot payload 与 state_hash 不匹配",
            )
            if state
            else None
        )
        manifest = {
            "project_id": path.project_id,
            "story_path_id": path.id,
            "path_chapter_id": target.id,
            "bible_revision_id": selected_bible_revision_id,
            "bible_content_hash": bible_digest,
            "outline_revision_id": selected_outline_revision_id,
            "outline_chapter_hash": outline_digest,
            "state_snapshot_id": selected_state_id,
            "state_hash": state_digest,
            "fork": self._fork_manifest(path),
            "fork_choice": self._fork_choice_manifest(path),
            "ancestors": ancestors,
        }
        digest = content_hash(manifest)
        return self.resolve_frozen(manifest, expected_context_hash=digest)

    def resolve_frozen(
        self,
        manifest: dict[str, Any],
        *,
        expected_context_hash: str,
    ) -> ResolvedStoryContext:
        """Load exact manifest revisions without consulting mutable Heads."""

        if not isinstance(manifest, dict):
            self._fail("context.manifest_invalid", "context manifest 必须是对象")
        actual_hash = content_hash(manifest)
        if actual_hash != expected_context_hash:
            self._fail("context.hash_mismatch", "context manifest hash 不匹配")

        project_id = self._required_project_id(manifest.get("project_id"))
        story_path_id = self._required_id(
            manifest.get("story_path_id"),
            "story_path_id",
        )
        path_chapter_id = self._required_id(
            manifest.get("path_chapter_id"),
            "path_chapter_id",
        )
        bible_revision_id = self._required_id(
            manifest.get("bible_revision_id"),
            "bible_revision_id",
        )
        outline_revision_id = self._required_id(
            manifest.get("outline_revision_id"),
            "outline_revision_id",
        )

        target, path = self._target_and_path(path_chapter_id)
        if path.id != story_path_id or path.project_id != project_id:
            self._fail(
                "context.target_ownership_mismatch",
                "目标章节不属于 manifest 声明的项目和 StoryPath",
            )
        self._require_active_path(path)
        self._validate_path_fork(path)
        if manifest.get("fork") != self._fork_manifest(path):
            self._fail("context.fork_mismatch", "StoryPath 分叉来源与 manifest 不匹配")
        if "fork_choice" in manifest:
            fork_choice = self._fork_choice_manifest(path)
            if manifest["fork_choice"] != fork_choice:
                self._fail("context.fork_choice_mismatch", "StoryPath 分叉选择与 manifest 不匹配")
        else:
            # Frozen tasks created before candidate promotion context was added
            # remain executable with their original, already-hashed manifest.
            fork_choice = None

        bible, outline_chapter = self._load_bible_and_outline_chapter(
            project_id=project_id,
            story_path_id=story_path_id,
            target=target,
            bible_revision_id=bible_revision_id,
            outline_revision_id=outline_revision_id,
        )
        bible_digest = self._verified_payload_hash(
            stored_hash=bible.content_hash,
            payload=bible.content_json,
            code="context.bible_payload_mismatch",
            detail="冻结的 Story Bible payload 已变化",
        )
        if (
            "bible_content_hash" in manifest
            and manifest["bible_content_hash"] != bible_digest
        ):
            self._fail("context.bible_payload_mismatch", "冻结的 Story Bible payload 已变化")
        outline_payload, outline_digest = self._verified_outline_chapter_payload(
            target=target,
            outline_chapter=outline_chapter,
            code="context.outline_payload_mismatch",
            detail="冻结的 Outline chapter payload 已变化",
        )
        if (
            "outline_chapter_hash" in manifest
            and manifest["outline_chapter_hash"] != outline_digest
        ):
            self._fail("context.outline_payload_mismatch", "冻结的 Outline chapter payload 已变化")

        state = self._load_state(project_id, manifest.get("state_snapshot_id"))
        if state:
            state_digest = self._verified_payload_hash(
                stored_hash=state.state_hash,
                payload=state.state_json,
                code="context.state_payload_mismatch",
                detail="冻结的 StateSnapshot payload 已变化",
            )
            if "state_hash" in manifest and manifest["state_hash"] != state_digest:
                self._fail("context.state_payload_mismatch", "冻结的 StateSnapshot payload 已变化")
        elif "state_hash" in manifest and manifest["state_hash"] is not None:
            self._fail("context.state_payload_mismatch", "冻结的 StateSnapshot payload 已变化")
        ancestor_records = self._load_frozen_ancestors(
            project_id=project_id,
            path=path,
            target=target,
            raw_ancestors=manifest.get("ancestors"),
        )
        return ResolvedStoryContext(
            manifest=deepcopy(manifest),
            context_hash=actual_hash,
            story_bible=deepcopy(bible.content_json),
            outline_chapter=deepcopy(outline_payload),
            state=deepcopy(state.state_json) if state else None,
            ancestors=ancestor_records,
            fork_choice=deepcopy(fork_choice),
        )

    def _load_bible_and_outline_chapter(
        self,
        *,
        project_id: int,
        story_path_id: str,
        target: StoryPathChapter,
        bible_revision_id: str,
        outline_revision_id: str,
    ) -> tuple[StoryBibleRevision, OutlineChapter]:
        bible = (
            self.db.query(StoryBibleRevision)
            .filter(
                StoryBibleRevision.id == bible_revision_id,
                StoryBibleRevision.project_id == project_id,
            )
            .one_or_none()
        )
        if not bible:
            self._fail(
                "context.bible_revision_missing",
                "manifest 引用的 Story Bible revision 不存在或不属于项目",
            )
        outline = (
            self.db.query(OutlineRevision)
            .filter(
                OutlineRevision.id == outline_revision_id,
                OutlineRevision.project_id == project_id,
                OutlineRevision.story_path_id == story_path_id,
            )
            .one_or_none()
        )
        if not outline:
            self._fail(
                "context.outline_revision_missing",
                "manifest 引用的 Outline revision 不存在或不属于 StoryPath",
            )
        if outline.bible_revision_id != bible.id:
            self._fail(
                "context.outline_bible_mismatch",
                "Outline revision 与 Story Bible revision 不匹配",
            )
        outline_chapter = (
            self.db.query(OutlineChapter)
            .filter(
                OutlineChapter.outline_revision_id == outline.id,
                OutlineChapter.story_path_chapter_id == target.id,
            )
            .one_or_none()
        )
        if not outline_chapter:
            self._fail(
                "context.outline_chapter_missing",
                "Outline revision 不包含目标 PathChapter",
            )
        if outline_chapter.display_index != target.display_index:
            self._fail(
                "context.outline_order_mismatch",
                "Outline 章节顺序与 StoryPath 不匹配",
            )
        return bible, outline_chapter

    @staticmethod
    def _outline_chapter_payload(
        target: StoryPathChapter,
        outline_chapter: OutlineChapter,
    ) -> dict[str, Any]:
        return {
            "story_path_chapter_id": target.id,
            "display_index": outline_chapter.display_index,
            "title": outline_chapter.title,
            "summary": outline_chapter.summary,
            "conflict": outline_chapter.conflict,
            "characters": deepcopy(outline_chapter.characters or []),
            "scene": outline_chapter.scene,
            "emotion": outline_chapter.emotion,
            "visual_keywords": deepcopy(outline_chapter.visual_keywords or []),
        }

    def _verified_outline_chapter_payload(
        self,
        *,
        target: StoryPathChapter,
        outline_chapter: OutlineChapter,
        code: str,
        detail: str,
    ) -> tuple[dict[str, Any], str]:
        payload = self._outline_chapter_payload(target, outline_chapter)
        actual_hash = content_hash(payload)
        pre_reconciliation_payload = {
            **payload,
            "story_path_chapter_id": None,
        }
        if outline_chapter.content_hash not in {
            actual_hash,
            content_hash(pre_reconciliation_payload),
        }:
            self._fail(code, detail)
        return payload, actual_hash

    def _verified_payload_hash(
        self,
        *,
        stored_hash: Any,
        payload: Any,
        code: str,
        detail: str,
    ) -> str:
        actual_hash = content_hash(payload)
        if stored_hash is not None and stored_hash != actual_hash:
            self._fail(code, detail)
        return actual_hash

    def _target_and_path(
        self,
        path_chapter_id: str,
    ) -> tuple[StoryPathChapter, StoryPath]:
        target = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.id == path_chapter_id,
                StoryPathChapter.status == "active",
            )
            .one_or_none()
        )
        if not target:
            self._fail("context.path_chapter_missing", "PathChapter 不存在")
        path = (
            self.db.query(StoryPath)
            .filter(StoryPath.id == target.story_path_id)
            .one_or_none()
        )
        if not path:
            self._fail("context.story_path_missing", "StoryPath 不存在")
        return target, path

    def _selected_ancestor_manifest(
        self,
        path: StoryPath,
        target: StoryPathChapter,
        *,
        revision_overrides: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        overrides = dict(revision_overrides or {})
        if any(
            not isinstance(path_chapter_id, str)
            or not path_chapter_id
            or not isinstance(revision_id, str)
            or not revision_id
            for path_chapter_id, revision_id in overrides.items()
        ):
            self._fail(
                "context.ancestor_override_invalid",
                "ancestor revision override 格式无效",
            )
        path_chapters = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.status == "active",
            )
            .all()
        )
        by_id = {row.id: row for row in path_chapters}
        reverse_chain: list[StoryPathChapter] = []
        visited = {target.id}
        cursor_id = target.predecessor_path_chapter_id
        next_display_index = target.display_index
        while cursor_id:
            if cursor_id in visited:
                self._fail("context.predecessor_cycle", "StoryPath predecessor 链存在环")
            visited.add(cursor_id)
            row = by_id.get(cursor_id)
            if not row:
                self._fail(
                    "context.predecessor_outside_path",
                    "predecessor 不属于目标 StoryPath",
                )
            if row.display_index >= next_display_index:
                self._fail(
                    "context.predecessor_order_invalid",
                    "predecessor 的显示顺序必须早于后继章节",
                )
            selected_revision_id = overrides.get(row.id, row.current_revision_id)
            if not selected_revision_id:
                self._fail(
                    "context.ancestor_head_missing",
                    "predecessor 尚未选择 Chapter revision",
                )
            reverse_chain.append(row)
            next_display_index = row.display_index
            cursor_id = row.predecessor_path_chapter_id

        ordered = list(reversed(reverse_chain))
        ancestor_ids = {row.id for row in ordered}
        if set(overrides) - ancestor_ids:
            self._fail(
                "context.ancestor_override_mismatch",
                "ancestor revision override 不属于目标章节的 predecessor 链",
            )
        selected_revision_ids = {
            row.id: overrides.get(row.id, row.current_revision_id) for row in ordered
        }
        revision_ids = list(selected_revision_ids.values())
        revisions = (
            self.db.query(ChapterRevision)
            .filter(ChapterRevision.id.in_(revision_ids))
            .all()
            if revision_ids
            else []
        )
        revisions_by_id = {revision.id: revision for revision in revisions}
        manifest: list[dict[str, Any]] = []
        for row in ordered:
            revision = revisions_by_id.get(selected_revision_ids[row.id])
            if (
                not revision
                or revision.project_id != path.project_id
                or revision.chapter_slot_id != row.chapter_slot_id
            ):
                self._fail(
                    "context.ancestor_revision_mismatch",
                    "ancestor revision 不属于对应 PathChapter",
                )
            digest = self._verified_payload_hash(
                stored_hash=revision.content_hash,
                payload=revision.content,
                code="context.ancestor_payload_invalid",
                detail="ancestor revision payload 与 content_hash 不匹配",
            )
            manifest.append(
                {
                    "path_chapter_id": row.id,
                    "chapter_revision_id": revision.id,
                    "display_index": row.display_index,
                    "content_hash": digest,
                }
            )
        return manifest

    def _load_frozen_ancestors(
        self,
        *,
        project_id: int,
        path: StoryPath,
        target: StoryPathChapter,
        raw_ancestors: Any,
    ) -> tuple[ResolvedAncestor, ...]:
        if not isinstance(raw_ancestors, list):
            self._fail("context.ancestors_invalid", "ancestors 必须是数组")

        specs: list[tuple[str, str, int, str | None]] = []
        seen_path_chapters: set[str] = set()
        for raw in raw_ancestors:
            if not isinstance(raw, dict):
                self._fail("context.ancestors_invalid", "ancestor 必须是对象")
            path_chapter_id = self._required_id(
                raw.get("path_chapter_id"),
                "ancestor.path_chapter_id",
            )
            revision_id = self._required_id(
                raw.get("chapter_revision_id"),
                "ancestor.chapter_revision_id",
            )
            display_index = raw.get("display_index")
            if isinstance(display_index, bool) or not isinstance(display_index, int):
                self._fail(
                    "context.ancestors_invalid",
                    "ancestor.display_index 必须是正整数",
                )
            if display_index < 1 or path_chapter_id in seen_path_chapters:
                self._fail(
                    "context.ancestors_invalid",
                    "ancestor 顺序或身份无效",
                )
            seen_path_chapters.add(path_chapter_id)
            expected_content_hash = (
                self._required_hash(
                    raw.get("content_hash"),
                    "ancestor.content_hash",
                )
                if "content_hash" in raw
                else None
            )
            specs.append(
                (path_chapter_id, revision_id, display_index, expected_content_hash)
            )

        ids = [item[0] for item in specs]
        rows = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.id.in_(ids),
                StoryPathChapter.status == "active",
            )
            .all()
            if ids
            else []
        )
        rows_by_id = {row.id: row for row in rows}
        if len(rows_by_id) != len(ids):
            self._fail(
                "context.ancestor_ownership_mismatch",
                "ancestor 不属于目标 StoryPath",
            )

        revision_ids = [item[1] for item in specs]
        revisions = (
            self.db.query(ChapterRevision)
            .filter(ChapterRevision.id.in_(revision_ids))
            .all()
            if revision_ids
            else []
        )
        revisions_by_id = {row.id: row for row in revisions}
        slot_ids = {target.chapter_slot_id, *(row.chapter_slot_id for row in rows)}
        slots = (
            self.db.query(ChapterSlot)
            .filter(ChapterSlot.id.in_(slot_ids))
            .all()
        )
        slots_by_id = {row.id: row for row in slots}
        if len(slots_by_id) != len(slot_ids) or any(
            slot.project_id != project_id for slot in slots
        ):
            self._fail(
                "context.chapter_slot_ownership_mismatch",
                "PathChapter 的 ChapterSlot 不属于目标项目",
            )

        result: list[ResolvedAncestor] = []
        expected_predecessor: str | None = None
        previous_display_index = 0
        for path_chapter_id, revision_id, display_index, expected_content_hash in specs:
            row = rows_by_id[path_chapter_id]
            if row.predecessor_path_chapter_id != expected_predecessor:
                self._fail(
                    "context.predecessor_chain_mismatch",
                    "manifest ancestors 不是完整 predecessor 链",
                )
            if row.display_index != display_index or display_index <= previous_display_index:
                self._fail(
                    "context.predecessor_order_invalid",
                    "manifest ancestor 顺序与 StoryPath 不匹配",
                )
            revision = revisions_by_id.get(revision_id)
            if (
                not revision
                or revision.project_id != project_id
                or revision.chapter_slot_id != row.chapter_slot_id
            ):
                self._fail(
                    "context.ancestor_revision_mismatch",
                    "ancestor revision 不属于对应 PathChapter",
                )
            actual_content_hash = self._verified_payload_hash(
                stored_hash=revision.content_hash,
                payload=revision.content,
                code="context.ancestor_payload_mismatch",
                detail="冻结的 ancestor revision payload 已变化",
            )
            if (
                expected_content_hash is not None
                and actual_content_hash != expected_content_hash
            ):
                self._fail(
                    "context.ancestor_payload_mismatch",
                    "冻结的 ancestor revision payload 已变化",
                )
            result.append(
                ResolvedAncestor(
                    path_chapter_id=row.id,
                    chapter_revision_id=revision.id,
                    display_index=row.display_index,
                    content_hash=actual_content_hash,
                    content=revision.content,
                )
            )
            expected_predecessor = row.id
            previous_display_index = display_index

        if target.predecessor_path_chapter_id != expected_predecessor:
            self._fail(
                "context.predecessor_chain_mismatch",
                "manifest ancestors 与目标章节 predecessor 不匹配",
            )
        if target.display_index <= previous_display_index:
            self._fail(
                "context.predecessor_order_invalid",
                "目标章节顺序必须晚于 predecessor",
            )
        return tuple(result)

    def _validate_path_fork(self, path: StoryPath) -> None:
        if path.parent_path_id is None:
            return
        parent = (
            self.db.query(StoryPath)
            .filter(
                StoryPath.id == path.parent_path_id,
                StoryPath.project_id == path.project_id,
            )
            .one_or_none()
        )
        if not parent:
            self._fail(
                "context.fork_parent_mismatch",
                "父 StoryPath 不属于目标项目",
            )
        fork_chapter = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.id == path.fork_path_chapter_id,
                StoryPathChapter.story_path_id == parent.id,
                StoryPathChapter.status == "active",
            )
            .one_or_none()
        )
        if not fork_chapter:
            self._fail(
                "context.fork_chapter_mismatch",
                "分叉章节不属于父 StoryPath",
            )
        checkpoint = (
            self.db.query(StoryNode)
            .filter(
                StoryNode.id == path.fork_checkpoint_node_id,
                StoryNode.project_id == path.project_id,
            )
            .one_or_none()
        )
        if not checkpoint:
            self._fail(
                "context.fork_checkpoint_mismatch",
                "分叉 checkpoint 不属于目标项目",
            )
        candidate = (
            self.db.query(BranchCandidate)
            .filter(
                BranchCandidate.id == path.fork_candidate_id,
                BranchCandidate.project_id == path.project_id,
                BranchCandidate.checkpoint_node_id == checkpoint.id,
            )
            .one_or_none()
        )
        if not candidate:
            self._fail(
                "context.fork_candidate_mismatch",
                "分叉 candidate 不属于目标 checkpoint",
            )
        self._load_state(path.project_id, path.base_state_snapshot_id)

    def _load_state(
        self,
        project_id: int,
        state_snapshot_id: Any,
    ) -> StateSnapshot | None:
        if state_snapshot_id is None:
            return None
        snapshot_id = self._required_id(state_snapshot_id, "state_snapshot_id")
        snapshot = (
            self.db.query(StateSnapshot)
            .filter(
                StateSnapshot.id == snapshot_id,
                StateSnapshot.project_id == project_id,
            )
            .one_or_none()
        )
        if not snapshot:
            self._fail(
                "context.state_snapshot_missing",
                "StateSnapshot 不存在或不属于目标项目",
            )
        return snapshot

    def _fork_choice_manifest(self, path: StoryPath) -> dict[str, Any] | None:
        if path.parent_path_id is None:
            return None
        candidate = (
            self.db.query(BranchCandidate)
            .filter(
                BranchCandidate.id == path.fork_candidate_id,
                BranchCandidate.project_id == path.project_id,
                BranchCandidate.checkpoint_node_id == path.fork_checkpoint_node_id,
            )
            .one_or_none()
        )
        local_fork_chapters = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.inherited_from_path_chapter_id
                == path.fork_path_chapter_id,
                StoryPathChapter.status == "active",
            )
            .all()
        )
        if not candidate or len(local_fork_chapters) != 1:
            self._fail(
                "context.fork_candidate_mismatch",
                "StoryPath 分叉 candidate 或子路径本地章节不存在",
            )
        fork_chapter = local_fork_chapters[0]

        if candidate.candidate_set_revision_id:
            revision = (
                self.db.query(CandidateSetRevision)
                .filter(
                    CandidateSetRevision.id == candidate.candidate_set_revision_id,
                    CandidateSetRevision.project_id == path.project_id,
                    CandidateSetRevision.story_path_id == path.parent_path_id,
                    CandidateSetRevision.checkpoint_node_id
                    == path.fork_checkpoint_node_id,
                )
                .one_or_none()
            )
            manifest = revision.candidates_json if revision else None
            if (
                not revision
                or not isinstance(manifest, list)
                or content_hash(manifest) != revision.content_hash
            ):
                self._fail(
                    "context.fork_candidate_payload_invalid",
                    "StoryPath 分叉 CandidateSet payload 损坏",
                )
            item = next(
                (
                    value
                    for value in manifest
                    if isinstance(value, dict) and value.get("id") == candidate.id
                ),
                None,
            )
            if (
                not item
                or item.get("option_key") != candidate.option_key
                or item.get("preview_node_id") != candidate.preview_node_id
                or item.get("state_delta") != (candidate.state_delta or {})
                or not isinstance(item.get("preview_text"), str)
            ):
                self._fail(
                    "context.fork_candidate_payload_invalid",
                    "StoryPath 分叉 candidate 与 CandidateSet payload 不匹配",
                )
            payload = {
                "candidate_id": candidate.id,
                "candidate_set_revision_id": revision.id,
                "candidate_set_content_hash": revision.content_hash,
                "fork_path_chapter_id": path.fork_path_chapter_id,
                "display_index": fork_chapter.display_index,
                "option_key": candidate.option_key,
                "preview_text": item["preview_text"],
                "state_delta": deepcopy(item["state_delta"]),
            }
        else:
            preview = (
                self.db.query(StoryNode)
                .filter(
                    StoryNode.id == candidate.preview_node_id,
                    StoryNode.project_id == path.project_id,
                )
                .one_or_none()
                if candidate.preview_node_id
                else None
            )
            preview_payload = (
                preview.payload
                if preview and isinstance(preview.payload, dict)
                else {}
            )
            preview_text = preview_payload.get(
                "preview_text",
                preview_payload.get("text", ""),
            )
            payload = {
                "candidate_id": candidate.id,
                "candidate_set_revision_id": None,
                "candidate_set_content_hash": None,
                "fork_path_chapter_id": path.fork_path_chapter_id,
                "display_index": fork_chapter.display_index,
                "option_key": candidate.option_key,
                "preview_text": preview_text if isinstance(preview_text, str) else "",
                "state_delta": deepcopy(candidate.state_delta or {}),
            }
        return {
            **payload,
            "candidate_content_hash": content_hash(payload),
        }

    @staticmethod
    def _fork_manifest(path: StoryPath) -> dict[str, str | None]:
        return {
            "parent_path_id": path.parent_path_id,
            "checkpoint_node_id": path.fork_checkpoint_node_id,
            "candidate_id": path.fork_candidate_id,
        }

    @staticmethod
    def _require_active_path(path: StoryPath) -> None:
        if path.status != "active":
            raise StoryContextResolutionError(
                "context.story_path_inactive",
                "已归档 StoryPath 不能生成章节",
            )

    @staticmethod
    def _required_project_id(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise StoryContextResolutionError(
                "context.manifest_invalid",
                "project_id 必须是正整数",
            )
        return value

    @staticmethod
    def _required_id(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise StoryContextResolutionError(
                "context.manifest_invalid",
                f"{field} 必须是非空字符串",
            )
        return value

    @staticmethod
    def _required_hash(value: Any, field: str) -> str:
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise StoryContextResolutionError(
                "context.manifest_invalid",
                f"{field} 必须是 SHA-256 hash",
            )
        return value

    @staticmethod
    def _fail(code: str, detail: str) -> None:
        raise StoryContextResolutionError(code, detail)


__all__ = [
    "ResolvedAncestor",
    "ResolvedStoryContext",
    "StoryContextResolutionError",
    "StoryContextResolver",
]
