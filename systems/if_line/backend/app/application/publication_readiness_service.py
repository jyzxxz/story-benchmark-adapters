"""Publication readiness over reviewed, immutable StoryPath authoring heads."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.application.candidate_set_review_service import list_candidate_set_candidates
from app.application.hashing import content_hash
from app.application.vn_graph_derivation import (
    VNGraphDerivationError,
    validate_derived_vn_graph,
)
from app.application.vn_graph_manifest import (
    validate_asset_version_reference,
    validate_voice_line_version_manifest_item,
    voice_line_payload,
)
from app.core.errors import AppError
from app.models import Asset, Project
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


PUBLICATION_FINGERPRINT_VERSION = "authoring-fingerprint-v1"

_SELECTABLE_STATUSES = frozenset({"ready", "complete"})
_SUPPORTED_GRAPH_MANIFESTS = frozenset(
    {
        "vngraph-binding-v1",
        "vngraph-binding-v2",
        "vngraph-binding-derived-v1",
    }
)


@dataclass(frozen=True)
class PublicationBlockingItem:
    code: str
    detail: str
    story_path_id: str | None = None
    path_chapter_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.story_path_id is not None:
            result["story_path_id"] = self.story_path_id
        if self.path_chapter_id is not None:
            result["path_chapter_id"] = self.path_chapter_id
        return result


@dataclass(frozen=True)
class PublicationReadinessResult:
    ready: bool
    authoring_fingerprint: str
    blocking_items: tuple[PublicationBlockingItem, ...]
    authoring_snapshot: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "authoring_fingerprint": self.authoring_fingerprint,
            "blocking_items": [item.as_dict() for item in self.blocking_items],
        }


class _PublicationReadinessBuilder:
    def __init__(self, db: Session, project: Project) -> None:
        self.db = db
        self.project = project
        self.project_id = project.id
        self._blocking_items: dict[
            tuple[str, str, str, str], PublicationBlockingItem
        ] = {}
        self._path_chapters: dict[str, StoryPathChapter] | None = None
        self._outline_integrity: dict[str, tuple[dict[str, Any], bool]] = {}

    def build(self) -> PublicationReadinessResult:
        snapshot: dict[str, Any] = {
            "schema_version": PUBLICATION_FINGERPRINT_VERSION,
            "project": self._project_snapshot(),
            "bible_revision": None,
            "root_story_path_id": None,
            "story_paths": [],
        }
        bible = self._selected_bible()
        if bible is not None:
            snapshot["bible_revision"] = {
                "id": bible.id,
                "content_hash": bible.content_hash,
            }

        root, paths = self._reachable_paths()
        if root is not None:
            snapshot["root_story_path_id"] = root.id
        snapshot["story_paths"] = [
            self._path_snapshot(path, bible=bible) for path in paths
        ]

        blockers = tuple(
            sorted(
                self._blocking_items.values(),
                key=lambda item: (
                    item.code,
                    item.story_path_id or "",
                    item.path_chapter_id or "",
                    item.detail,
                ),
            )
        )
        fingerprint = content_hash(
            {
                "schema_version": PUBLICATION_FINGERPRINT_VERSION,
                "authoring_snapshot": snapshot,
                "blocking_items": [item.as_dict() for item in blockers],
            }
        )
        return PublicationReadinessResult(
            ready=not blockers,
            authoring_fingerprint=fingerprint,
            blocking_items=blockers,
            authoring_snapshot=snapshot,
        )

    def _block(
        self,
        code: str,
        detail: str,
        *,
        story_path_id: str | None = None,
        path_chapter_id: str | None = None,
    ) -> None:
        item = PublicationBlockingItem(
            code=code,
            detail=detail,
            story_path_id=story_path_id,
            path_chapter_id=path_chapter_id,
        )
        key = (code, story_path_id or "", path_chapter_id or "", detail)
        self._blocking_items[key] = item

    def _project_snapshot(self) -> dict[str, Any]:
        return {
            "id": self.project.id,
            "title": self.project.title,
            "characters": deepcopy(self.project.characters),
            "story_start": self.project.story_start,
            "story_end": self.project.story_end,
            "style": self.project.style,
            "source_work": self.project.source_work,
            "pace": self.project.pace,
            "extra_requirements": self.project.extra_requirements,
        }

    def _selected_bible(self) -> StoryBibleRevision | None:
        head = (
            self.db.query(ProjectContentHead)
            .filter(ProjectContentHead.project_id == self.project_id)
            .one_or_none()
        )
        if not head or not head.current_bible_revision_id:
            self._block(
                "bible.head_missing",
                "No StoryBible revision is selected as current — regenerate to "
                "auto-adopt the first revision, activate one in review, or run "
                "finalize-publish",
            )
            return None
        bible = (
            self.db.query(StoryBibleRevision)
            .filter(StoryBibleRevision.id == head.current_bible_revision_id)
            .one_or_none()
        )
        if not bible or bible.project_id != self.project_id:
            self._block(
                "bible.revision_invalid",
                "StoryBible Head points outside the project",
            )
            return None
        if bible.status not in _SELECTABLE_STATUSES:
            self._block(
                "bible.status_invalid",
                "Reviewed StoryBible revision is not ready",
            )
        if content_hash(bible.content_json) != bible.content_hash:
            self._block(
                "bible.content_hash_mismatch",
                "Reviewed StoryBible content does not match its immutable hash",
            )
        return bible

    def _reachable_paths(self) -> tuple[StoryPath | None, list[StoryPath]]:
        paths = (
            self.db.query(StoryPath)
            .filter(StoryPath.project_id == self.project_id)
            .order_by(StoryPath.id)
            .all()
        )
        roots = [path for path in paths if path.parent_path_id is None]
        if len(roots) != 1:
            self._block(
                "story_path.root_invalid",
                "Project must have exactly one root StoryPath",
            )
            for path in paths:
                if path.status == "active":
                    self._block(
                        "story_path.topology_invalid",
                        "Active StoryPath is not reachable from a unique root",
                        story_path_id=path.id,
                    )
            return None, []

        root = roots[0]
        if root.status != "active":
            self._block(
                "story_path.root_inactive",
                "Root StoryPath must be active",
                story_path_id=root.id,
            )
            return root, []

        children: dict[str, list[StoryPath]] = {}
        for path in paths:
            if path.parent_path_id:
                children.setdefault(path.parent_path_id, []).append(path)
        for values in children.values():
            values.sort(key=lambda item: item.id)

        reachable: list[StoryPath] = []
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(path: StoryPath) -> None:
            if path.id in visiting:
                self._block(
                    "story_path.topology_invalid",
                    "StoryPath ancestry contains a cycle",
                    story_path_id=path.id,
                )
                return
            if path.id in visited or path.status != "active":
                return
            visiting.add(path.id)
            reachable.append(path)
            for child in children.get(path.id, []):
                visit(child)
            visiting.remove(path.id)
            visited.add(path.id)

        visit(root)
        local_ids = {path.id for path in paths}
        for path in paths:
            if path.status != "active" or path.id in visited:
                continue
            ancestor_id = path.parent_path_id
            excluded_by_archive = False
            seen = {path.id}
            while ancestor_id and ancestor_id in local_ids and ancestor_id not in seen:
                seen.add(ancestor_id)
                ancestor = next(item for item in paths if item.id == ancestor_id)
                if ancestor.status == "archived":
                    excluded_by_archive = True
                    break
                ancestor_id = ancestor.parent_path_id
            if not excluded_by_archive:
                self._block(
                    "story_path.topology_invalid",
                    "Active StoryPath is not reachable from the project root",
                    story_path_id=path.id,
                )

        reachable.sort(key=lambda item: item.id)
        return root, reachable

    def _path_snapshot(
        self,
        path: StoryPath,
        *,
        bible: StoryBibleRevision | None,
    ) -> dict[str, Any]:
        fork_choice = self._validate_path_provenance(path)
        base_state = self._state_snapshot(
            path.base_state_snapshot_id,
            story_path_id=path.id,
            code="story_path.state_invalid",
        )
        placements = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.story_path_id == path.id,
                StoryPathChapter.status == "active",
            )
            .order_by(StoryPathChapter.display_index, StoryPathChapter.id)
            .all()
        )
        self._validate_path_topology(path, placements)
        outline, outline_snapshot = self._selected_outline(
            path,
            placements=placements,
            bible=bible,
        )
        chapter_snapshots = [
            self._chapter_snapshot(
                path,
                placement=placement,
                current_outline=outline,
                current_bible=bible,
            )
            for placement in placements
        ]
        return {
            "id": path.id,
            "parent_path_id": path.parent_path_id,
            "fork_path_chapter_id": path.fork_path_chapter_id,
            "fork_checkpoint_node_id": path.fork_checkpoint_node_id,
            "fork_candidate_id": path.fork_candidate_id,
            "title": path.title,
            "base_state_snapshot": base_state,
            "fork_choice": fork_choice,
            "outline_revision": outline_snapshot,
            "chapters": chapter_snapshots,
        }

    def _validate_path_provenance(self, path: StoryPath) -> dict[str, Any] | None:
        if path.parent_path_id is None:
            if any(
                value is not None
                for value in (
                    path.fork_path_chapter_id,
                    path.fork_checkpoint_node_id,
                    path.fork_candidate_id,
                )
            ):
                self._block(
                    "story_path.provenance_invalid",
                    "Root StoryPath has branch provenance",
                    story_path_id=path.id,
                )
            return None

        parent = (
            self.db.query(StoryPath)
            .filter(StoryPath.id == path.parent_path_id)
            .one_or_none()
        )
        fork_chapter = (
            self.db.query(StoryPathChapter)
            .filter(
                StoryPathChapter.id == path.fork_path_chapter_id,
                StoryPathChapter.status == "active",
            )
            .one_or_none()
            if path.fork_path_chapter_id
            else None
        )
        checkpoint = (
            self.db.query(StoryNode)
            .filter(StoryNode.id == path.fork_checkpoint_node_id)
            .one_or_none()
            if path.fork_checkpoint_node_id
            else None
        )
        candidate = (
            self.db.query(BranchCandidate)
            .filter(BranchCandidate.id == path.fork_candidate_id)
            .one_or_none()
            if path.fork_candidate_id
            else None
        )
        if (
            not parent
            or parent.project_id != self.project_id
            or not fork_chapter
            or fork_chapter.story_path_id != parent.id
            or not checkpoint
            or checkpoint.project_id != self.project_id
            or checkpoint.id != path.fork_checkpoint_node_id
            or checkpoint.node_type != "checkpoint"
            or not candidate
            or candidate.project_id != self.project_id
            or candidate.checkpoint_node_id != checkpoint.id
            or not candidate.candidate_set_revision_id
        ):
            self._block(
                "story_path.provenance_invalid",
                "Child StoryPath branch provenance is incomplete or cross-project",
                story_path_id=path.id,
            )
            return None

        revision = (
            self.db.query(CandidateSetRevision)
            .filter(CandidateSetRevision.id == candidate.candidate_set_revision_id)
            .one_or_none()
        )
        if (
            not revision
            or revision.project_id != self.project_id
            or revision.story_path_id != parent.id
            or revision.checkpoint_node_id != checkpoint.id
        ):
            self._block(
                "story_path.provenance_invalid",
                "Child StoryPath candidate set does not belong to its parent fork",
                story_path_id=path.id,
            )
            return None
        source_chapter = (
            self.db.query(ChapterRevision)
            .filter(ChapterRevision.id == revision.chapter_revision_id)
            .one_or_none()
        )
        if (
            not source_chapter
            or source_chapter.project_id != self.project_id
            or source_chapter.chapter_slot_id != fork_chapter.chapter_slot_id
        ):
            self._block(
                "story_path.provenance_invalid",
                "Child StoryPath candidate source does not match its fork chapter",
                story_path_id=path.id,
            )
            return None
        self._state_snapshot(
            revision.state_snapshot_id,
            story_path_id=path.id,
            code="story_path.provenance_invalid",
        )
        try:
            candidates = list_candidate_set_candidates(
                self.db,
                candidate_set_revision_id=revision.id,
                project_id=self.project_id,
            )
        except AppError:
            self._block(
                "story_path.provenance_invalid",
                "Child StoryPath candidate payload is incomplete or corrupted",
                story_path_id=path.id,
            )
            return None
        selected = next((item for item in candidates if item.id == candidate.id), None)
        if selected is None:
            self._block(
                "story_path.provenance_invalid",
                "Child StoryPath candidate is absent from its immutable set",
                story_path_id=path.id,
            )
            return None
        return {
            "candidate_set_revision_id": revision.id,
            "candidate_set_content_hash": revision.content_hash,
            "candidate_id": selected.id,
            "option_key": selected.option_key,
            "preview_text": selected.preview_text,
            "state_delta": deepcopy(selected.state_delta),
        }

    def _state_snapshot(
        self,
        snapshot_id: str | None,
        *,
        story_path_id: str,
        code: str,
        path_chapter_id: str | None = None,
    ) -> dict[str, Any] | None:
        if snapshot_id is None:
            return None
        snapshot = (
            self.db.query(StateSnapshot)
            .filter(StateSnapshot.id == snapshot_id)
            .one_or_none()
        )
        if (
            not snapshot
            or snapshot.project_id != self.project_id
            or content_hash(snapshot.state_json) != snapshot.state_hash
        ):
            self._block(
                code,
                "StateSnapshot is missing, cross-project, or corrupted",
                story_path_id=story_path_id,
                path_chapter_id=path_chapter_id,
            )
            return {"id": snapshot_id, "state_hash": None}
        return {"id": snapshot.id, "state_hash": snapshot.state_hash}

    def _validate_path_topology(
        self,
        path: StoryPath,
        placements: list[StoryPathChapter],
    ) -> None:
        if not placements:
            self._block(
                "story_path.chapters_missing",
                "Active StoryPath has no chapters",
                story_path_id=path.id,
            )
            return
        expected_indexes = list(range(1, len(placements) + 1))
        actual_indexes = [item.display_index for item in placements]
        previous_id: str | None = None
        valid = actual_indexes == expected_indexes
        for placement in placements:
            valid = valid and placement.predecessor_path_chapter_id == previous_id
            previous_id = placement.id
        if not valid:
            self._block(
                "story_path.topology_invalid",
                "PathChapter ordering does not match its predecessor chain",
                story_path_id=path.id,
            )

    def _selected_outline(
        self,
        path: StoryPath,
        *,
        placements: list[StoryPathChapter],
        bible: StoryBibleRevision | None,
    ) -> tuple[OutlineRevision | None, dict[str, Any] | None]:
        head = (
            self.db.query(StoryPathOutlineHead)
            .filter(StoryPathOutlineHead.story_path_id == path.id)
            .one_or_none()
        )
        if not head or not head.current_revision_id:
            self._block(
                "outline.head_missing",
                "No Outline revision is selected as current — regenerate to "
                "auto-adopt the first revision, activate one in review, or run "
                "finalize-publish",
                story_path_id=path.id,
            )
            return None, None
        outline = (
            self.db.query(OutlineRevision)
            .filter(OutlineRevision.id == head.current_revision_id)
            .one_or_none()
        )
        if (
            not outline
            or outline.project_id != self.project_id
            or outline.story_path_id != path.id
        ):
            self._block(
                "outline.revision_invalid",
                "Outline Head points outside its StoryPath",
                story_path_id=path.id,
            )
            return None, None
        if outline.status not in _SELECTABLE_STATUSES:
            self._block(
                "outline.status_invalid",
                "Reviewed Outline revision is not ready",
                story_path_id=path.id,
            )
        if bible is None or outline.bible_revision_id != bible.id:
            self._block(
                "outline.bible_mismatch",
                "Reviewed Outline was not generated from the current StoryBible",
                story_path_id=path.id,
            )
        outline_snapshot, _ = self._outline_snapshot(
            outline,
            story_path_id=path.id,
        )
        rows = (
            self.db.query(OutlineChapter)
            .filter(OutlineChapter.outline_revision_id == outline.id)
            .order_by(OutlineChapter.display_index, OutlineChapter.id)
            .all()
        )
        if (
            len(rows) != len(placements)
            or any(
                row.story_path_chapter_id != placement.id
                or row.display_index != placement.display_index
                for row, placement in zip(rows, placements)
            )
        ):
            self._block(
                "outline.topology_mismatch",
                "Reviewed Outline does not match the active PathChapter topology",
                story_path_id=path.id,
            )
        return outline, outline_snapshot

    def _outline_snapshot(
        self,
        outline: OutlineRevision,
        *,
        story_path_id: str,
        path_chapter_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        cached = self._outline_integrity.get(outline.id)
        if cached is not None:
            snapshot, valid = cached
            if not valid:
                self._block(
                    "outline.content_hash_mismatch",
                    "Outline content does not match its immutable hash",
                    story_path_id=story_path_id,
                    path_chapter_id=path_chapter_id,
                )
            return deepcopy(snapshot), valid

        rows = (
            self.db.query(OutlineChapter)
            .filter(OutlineChapter.outline_revision_id == outline.id)
            .order_by(OutlineChapter.display_index, OutlineChapter.id)
            .all()
        )
        original_items: list[dict[str, Any]] = []
        row_snapshots: list[dict[str, Any]] = []
        valid = bool(rows)
        for row in rows:
            item = {
                "story_path_chapter_id": row.story_path_chapter_id,
                "display_index": row.display_index,
                "title": row.title or "",
                "summary": row.summary or "",
                "conflict": row.conflict,
                "characters": list(row.characters or []),
                "scene": row.scene,
                "emotion": row.emotion,
                "visual_keywords": list(row.visual_keywords or []),
            }
            original = item
            if content_hash(item) != row.content_hash:
                before_activation = {**item, "story_path_chapter_id": None}
                if content_hash(before_activation) == row.content_hash:
                    original = before_activation
                else:
                    valid = False
            original_items.append(original)
            row_snapshots.append(
                {
                    "story_path_chapter_id": row.story_path_chapter_id,
                    "display_index": row.display_index,
                    "title": row.title,
                    "content_hash": row.content_hash,
                }
            )
        if content_hash(original_items) != outline.content_hash:
            valid = False
        snapshot = {
            "id": outline.id,
            "bible_revision_id": outline.bible_revision_id,
            "content_hash": outline.content_hash,
            "chapters": row_snapshots,
        }
        self._outline_integrity[outline.id] = (deepcopy(snapshot), valid)
        if not valid:
            self._block(
                "outline.content_hash_mismatch",
                "Outline content does not match its immutable hash",
                story_path_id=story_path_id,
                path_chapter_id=path_chapter_id,
            )
        return snapshot, valid

    def _all_path_chapters(self) -> dict[str, StoryPathChapter]:
        if self._path_chapters is None:
            rows = (
                self.db.query(StoryPathChapter)
                .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
                .filter(
                    StoryPath.project_id == self.project_id,
                    StoryPathChapter.status == "active",
                )
                .all()
            )
            self._path_chapters = {row.id: row for row in rows}
        return self._path_chapters

    def _chapter_snapshot(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        current_outline: OutlineRevision | None,
        current_bible: StoryBibleRevision | None,
    ) -> dict[str, Any]:
        base = {
            "path_chapter_id": placement.id,
            "chapter_slot_id": placement.chapter_slot_id,
            "display_index": placement.display_index,
            "predecessor_path_chapter_id": placement.predecessor_path_chapter_id,
            "inherited_from_path_chapter_id": placement.inherited_from_path_chapter_id,
            "chapter_revision": None,
            "script_revision": None,
            "vn_graph_revision": None,
        }
        slot = (
            self.db.query(ChapterSlot)
            .filter(ChapterSlot.id == placement.chapter_slot_id)
            .one_or_none()
        )
        if not slot or slot.project_id != self.project_id:
            self._block(
                "chapter.slot_invalid",
                "PathChapter references a ChapterSlot outside the project",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if placement.inherited_from_path_chapter_id:
            inherited = self._all_path_chapters().get(
                placement.inherited_from_path_chapter_id
            )
            if (
                not inherited
                or inherited.chapter_slot_id != placement.chapter_slot_id
                or inherited.story_path_id != path.parent_path_id
            ):
                self._block(
                    "chapter.inheritance_invalid",
                    "Inherited PathChapter does not match its parent source",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
        elif slot and slot.created_for_story_path_id != path.id:
            self._block(
                "chapter.slot_invalid",
                "Owned PathChapter uses a ChapterSlot created for another path",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )

        if not placement.current_revision_id:
            self._block(
                "chapter.head_missing",
                "No ChapterRevision is selected as current for this PathChapter "
                "— generate content or run finalize-publish",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return base
        chapter = (
            self.db.query(ChapterRevision)
            .filter(ChapterRevision.id == placement.current_revision_id)
            .one_or_none()
        )
        if (
            not chapter
            or chapter.project_id != self.project_id
            or chapter.chapter_slot_id != placement.chapter_slot_id
        ):
            self._block(
                "chapter.revision_invalid",
                "Chapter Head points outside its ChapterSlot",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return base
        chapter_state = self._state_snapshot(
            chapter.state_snapshot_id,
            story_path_id=path.id,
            path_chapter_id=placement.id,
            code="chapter.state_invalid",
        )
        chapter_snapshot = {
            "id": chapter.id,
            "bible_revision_id": chapter.bible_revision_id,
            "outline_revision_id": chapter.outline_revision_id,
            "created_for_story_path_id": chapter.created_for_story_path_id,
            "context_hash": chapter.context_hash,
            "content_hash": chapter.content_hash,
            "state_snapshot": chapter_state,
        }
        base["chapter_revision"] = chapter_snapshot
        if chapter.status not in _SELECTABLE_STATUSES:
            self._block(
                "chapter.status_invalid",
                "Reviewed ChapterRevision is not ready",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if not chapter.content.strip() or content_hash(chapter.content) != chapter.content_hash:
            self._block(
                "chapter.content_hash_mismatch",
                "Chapter content does not match its immutable hash",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if current_bible is None or chapter.bible_revision_id != current_bible.id:
            self._block(
                "chapter.bible_mismatch",
                "Reviewed ChapterRevision was not generated from the current StoryBible",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if (
            placement.inherited_from_path_chapter_id is None
            and current_outline is not None
            and chapter.outline_revision_id != current_outline.id
        ):
            self._block(
                "chapter.outline_mismatch",
                "Reviewed ChapterRevision was not generated from the current path Outline",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        self._validate_chapter_context(path, placement=placement, chapter=chapter)
        script, script_snapshot, slots = self._selected_script(
            path,
            placement=placement,
            chapter=chapter,
        )
        base["script_revision"] = script_snapshot
        graph_snapshot = self._selected_graph(
            path,
            placement=placement,
            chapter=chapter,
            script=script,
            slots=slots,
        )
        base["vn_graph_revision"] = graph_snapshot
        return base

    def _validate_chapter_context(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        chapter: ChapterRevision,
    ) -> None:
        manifest = chapter.context_manifest
        if not isinstance(manifest, dict) or content_hash(manifest) != chapter.context_hash:
            self._block(
                "chapter.context_invalid",
                "Chapter context manifest does not match its immutable hash",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return
        source_path_id = manifest.get("story_path_id")
        source_path_chapter_id = manifest.get("path_chapter_id")
        source_placement = self._all_path_chapters().get(source_path_chapter_id)
        inherited_ids: set[str] = set()
        current = placement
        while current.inherited_from_path_chapter_id:
            inherited_id = current.inherited_from_path_chapter_id
            if inherited_id in inherited_ids:
                break
            inherited_ids.add(inherited_id)
            inherited = self._all_path_chapters().get(inherited_id)
            if inherited is None:
                break
            current = inherited
        source_matches_placement = source_path_chapter_id == placement.id
        source_matches_inheritance = source_path_chapter_id in inherited_ids
        if (
            manifest.get("project_id") != self.project_id
            or not source_placement
            or source_placement.story_path_id != source_path_id
            or source_placement.chapter_slot_id != chapter.chapter_slot_id
            or not (source_matches_placement or source_matches_inheritance)
            or chapter.created_for_story_path_id != source_path_id
            or manifest.get("bible_revision_id") != chapter.bible_revision_id
            or manifest.get("outline_revision_id") != chapter.outline_revision_id
            or manifest.get("state_snapshot_id") != chapter.state_snapshot_id
        ):
            self._block(
                "chapter.context_invalid",
                "Chapter context provenance does not match its selected PathChapter",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return

        outline = (
            self.db.query(OutlineRevision)
            .filter(OutlineRevision.id == chapter.outline_revision_id)
            .one_or_none()
        )
        if (
            not outline
            or outline.project_id != self.project_id
            or outline.story_path_id != source_path_id
            or outline.bible_revision_id != chapter.bible_revision_id
        ):
            self._block(
                "chapter.context_invalid",
                "Chapter context references an invalid Outline revision",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return
        self._outline_snapshot(
            outline,
            story_path_id=path.id,
            path_chapter_id=placement.id,
        )
        outline_row = (
            self.db.query(OutlineChapter)
            .filter(
                OutlineChapter.outline_revision_id == outline.id,
                OutlineChapter.story_path_chapter_id == source_placement.id,
            )
            .one_or_none()
        )
        if (
            not outline_row
            or outline_row.display_index != source_placement.display_index
        ):
            self._block(
                "chapter.context_invalid",
                "Chapter context does not match its frozen Outline chapter",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        ancestors = manifest.get("ancestors")
        if not isinstance(ancestors, list):
            self._block(
                "chapter.context_invalid",
                "Chapter context ancestors must be an ordered list",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return
        for item in ancestors:
            if not isinstance(item, dict):
                self._block(
                    "chapter.context_invalid",
                    "Chapter context contains an invalid ancestor",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                break
            ancestor_placement = self._all_path_chapters().get(item.get("path_chapter_id"))
            ancestor_revision = (
                self.db.query(ChapterRevision)
                .filter(ChapterRevision.id == item.get("chapter_revision_id"))
                .one_or_none()
            )
            if (
                not ancestor_placement
                or not ancestor_revision
                or ancestor_revision.project_id != self.project_id
                or ancestor_revision.chapter_slot_id != ancestor_placement.chapter_slot_id
                or content_hash(ancestor_revision.content) != ancestor_revision.content_hash
            ):
                self._block(
                    "chapter.context_invalid",
                    "Chapter context contains a missing or corrupted ancestor",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                break

    def _selected_script(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        chapter: ChapterRevision,
    ) -> tuple[
        ChapterScriptRevision | None,
        dict[str, Any] | None,
        list[ScriptResourceSlot],
    ]:
        head = (
            self.db.query(ChapterScriptHead)
            .filter(ChapterScriptHead.chapter_revision_id == chapter.id)
            .one_or_none()
        )
        if not head or not head.current_revision_id:
            self._block(
                "script.head_missing",
                "No ScriptRevision is selected as current — generate a script "
                "or run finalize-publish",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return None, None, []
        script = (
            self.db.query(ChapterScriptRevision)
            .filter(ChapterScriptRevision.id == head.current_revision_id)
            .one_or_none()
        )
        if (
            not script
            or script.project_id != self.project_id
            or script.chapter_revision_id != chapter.id
            or script.bible_revision_id != chapter.bible_revision_id
            or script.outline_revision_id != chapter.outline_revision_id
        ):
            self._block(
                "script.revision_invalid",
                "Script Head points outside its exact ChapterRevision provenance",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return None, None, []
        if script.status not in _SELECTABLE_STATUSES:
            self._block(
                "script.status_invalid",
                "Reviewed ScriptRevision is not ready",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if content_hash(script.script_json) != script.script_hash:
            self._block(
                "script.content_hash_mismatch",
                "Script IR does not match its immutable hash",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        slots = (
            self.db.query(ScriptResourceSlot)
            .filter(ScriptResourceSlot.chapter_script_revision_id == script.id)
            .order_by(ScriptResourceSlot.order_index, ScriptResourceSlot.id)
            .all()
        )
        slot_snapshots: list[dict[str, Any]] = []
        for slot in slots:
            if slot.project_id != self.project_id:
                self._block(
                    "script.resource_invalid",
                    "Script resource slot belongs to another project",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
            if slot.required and (
                slot.status != "bound" or not slot.asset_version_id
            ):
                self._block(
                    "script.resource_unbound",
                    f"Required Script resource slot is not bound: {slot.slot_key}",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
            slot_snapshots.append(
                {
                    "id": slot.id,
                    "slot_key": slot.slot_key,
                    "role": slot.role,
                    "scene_id": slot.scene_id,
                    "paragraph_id": slot.paragraph_id,
                    "character_id": slot.character_id,
                    "order_index": slot.order_index,
                    "required": slot.required,
                    "status": slot.status,
                    "asset_version_id": slot.asset_version_id,
                    "spec_hash": content_hash(slot.spec_json or {}),
                }
            )
        return (
            script,
            {
                "id": script.id,
                "chapter_revision_id": script.chapter_revision_id,
                "script_hash": script.script_hash,
                "schema_version": script.schema_version,
                "generator_version": script.generator_version,
                "resource_slots": slot_snapshots,
            },
            slots,
        )

    def _selected_graph(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        chapter: ChapterRevision,
        script: ChapterScriptRevision | None,
        slots: list[ScriptResourceSlot],
    ) -> dict[str, Any] | None:
        if script is None:
            return None
        head = (
            self.db.query(VNGraphHead)
            .filter(VNGraphHead.script_revision_id == script.id)
            .one_or_none()
        )
        if not head or not head.current_revision_id:
            self._block(
                "vngraph.head_missing",
                "No VNGraphRevision is selected as current — compile a graph "
                "or run finalize-publish",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return None
        graph = (
            self.db.query(VNGraphRevision)
            .filter(VNGraphRevision.id == head.current_revision_id)
            .one_or_none()
        )
        if (
            not graph
            or graph.project_id != self.project_id
            or graph.chapter_revision_id != chapter.id
            or graph.script_revision_id != script.id
        ):
            self._block(
                "vngraph.revision_invalid",
                "VNGraph Head points outside its exact ScriptRevision provenance",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return None
        if graph.status not in _SELECTABLE_STATUSES:
            self._block(
                "vngraph.status_invalid",
                "Reviewed VNGraphRevision is not ready",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        if content_hash(graph.graph_json) != graph.graph_hash:
            self._block(
                "vngraph.graph_hash_mismatch",
                "VNGraph JSON does not match its immutable hash",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        manifest = graph.binding_manifest
        manifest_valid = (
            isinstance(manifest, dict)
            and content_hash(manifest) == graph.binding_manifest_hash
            and graph.source_manifest_hash == graph.binding_manifest_hash
        )
        if not manifest_valid:
            self._block(
                "vngraph.manifest_invalid",
                "VNGraph binding manifest does not match its immutable hash",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        elif manifest.get("manifest_version") not in _SUPPORTED_GRAPH_MANIFESTS:
            self._block(
                "vngraph.manifest_legacy",
                "VNGraph must be recompiled with an immutable binding manifest",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        else:
            self._validate_graph_manifest(
                path,
                placement=placement,
                chapter=chapter,
                script=script,
                graph=graph,
                manifest=manifest,
                slots=slots,
            )
        return {
            "id": graph.id,
            "script_revision_id": graph.script_revision_id,
            "binding_manifest_hash": graph.binding_manifest_hash,
            "graph_hash": graph.graph_hash,
            "schema_version": graph.schema_version,
            "compiler_version": graph.compiler_version,
            "tachi_policy_version": graph.tachi_policy_version,
        }

    def _validate_graph_manifest(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        chapter: ChapterRevision,
        script: ChapterScriptRevision,
        graph: VNGraphRevision,
        manifest: dict[str, Any],
        slots: list[ScriptResourceSlot],
    ) -> None:
        chapter_ref = manifest.get("chapter_revision")
        script_ref = manifest.get("script_revision")
        versions = manifest.get("versions")
        assets = manifest.get("asset_bindings")
        voices = manifest.get("voice_line_versions")
        if (
            manifest.get("project_id") != self.project_id
            or not isinstance(chapter_ref, dict)
            or chapter_ref.get("id") != chapter.id
            or chapter_ref.get("content_hash") != chapter.content_hash
            or not isinstance(script_ref, dict)
            or script_ref.get("id") != script.id
            or script_ref.get("script_hash") != script.script_hash
            or not isinstance(versions, dict)
            or versions.get("schema") != graph.schema_version
            or versions.get("compiler") != graph.compiler_version
            or versions.get("tachi_policy") != graph.tachi_policy_version
            or not isinstance(assets, list)
            or not isinstance(voices, list)
        ):
            self._block(
                "vngraph.manifest_invalid",
                "VNGraph binding manifest provenance is incomplete or stale",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
            return

        manifest_slots = {
            item.get("resource_slot_id"): item
            for item in assets
            if isinstance(item, dict) and item.get("resource_slot_id")
        }
        for slot in slots:
            item = manifest_slots.get(slot.id)
            if (
                not item
                or item.get("source_id") != script.id
                or item.get("role") != slot.role
                or item.get("scene_id") != slot.scene_id
                or item.get("paragraph_id") != slot.paragraph_id
                or item.get("character_id") != slot.character_id
                or item.get("order_index") != slot.order_index
                or bool(item.get("required")) != bool(slot.required)
                or item.get("slot_status") != slot.status
                or item.get("asset_version_id") != slot.asset_version_id
            ):
                self._block(
                    "vngraph.resources_stale",
                    f"VNGraph does not match Script resource slot: {slot.slot_key}",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )

        for item in assets:
            if not isinstance(item, dict):
                self._block(
                    "vngraph.resource_invalid",
                    "VNGraph contains an invalid resource manifest item",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                continue
            if item.get("required") and not item.get("asset_version_id"):
                self._block(
                    "vngraph.resource_invalid",
                    "VNGraph contains an unbound required resource",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                continue
            reference_id = item.get("asset_version_id")
            if reference_id:
                self._validate_asset_reference(
                    item,
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )

        current_lines = (
            self.db.query(VoiceLine)
            .filter(VoiceLine.chapter_revision_id == chapter.id)
            .order_by(VoiceLine.order_index, VoiceLine.id)
            .all()
        )
        manifest_line_ids = {
            item.get("id") for item in voices if isinstance(item, dict)
        }
        if manifest_line_ids != {line.id for line in current_lines}:
            self._block(
                "vngraph.voice_lines_stale",
                "VNGraph VoiceLineVersions do not match the current chapter voice lines",
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )
        current_by_id = {line.id: line for line in current_lines}
        for item in voices:
            if not isinstance(item, dict):
                self._block(
                    "vngraph.voice_line_invalid",
                    "VNGraph contains an invalid VoiceLineVersion manifest item",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                continue
            try:
                validate_voice_line_version_manifest_item(
                    self.db,
                    item,
                    expected_project_id=self.project_id,
                    expected_chapter_revision_id=chapter.id,
                )
            except ValueError:
                self._block(
                    "vngraph.voice_line_invalid",
                    "VNGraph VoiceLineVersion snapshot is missing or corrupted",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
                continue
            line = current_by_id.get(item.get("id"))
            if line and content_hash(voice_line_payload(line)) != item.get(
                "voice_line_version_hash"
            ):
                self._block(
                    "vngraph.voice_lines_stale",
                    "VNGraph VoiceLineVersion is stale after a voice-line edit",
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )
            audio_reference = item.get("audio_asset_version")
            if isinstance(audio_reference, dict):
                self._validate_asset_reference(
                    audio_reference,
                    story_path_id=path.id,
                    path_chapter_id=placement.id,
                )

        if manifest.get("manifest_version") == "vngraph-binding-derived-v1":
            self._validate_derived_graph_chain(
                path,
                placement=placement,
                script=script,
                graph=graph,
            )

    def _validate_derived_graph_chain(
        self,
        path: StoryPath,
        *,
        placement: StoryPathChapter,
        script: ChapterScriptRevision,
        graph: VNGraphRevision,
    ) -> None:
        def invalid(detail: str) -> None:
            self._block(
                "vngraph.derivation_invalid",
                detail,
                story_path_id=path.id,
                path_chapter_id=placement.id,
            )

        current = graph
        seen: set[str] = set()
        while True:
            manifest = current.binding_manifest
            if (
                not isinstance(manifest, dict)
                or content_hash(manifest) != current.binding_manifest_hash
                or current.source_manifest_hash != current.binding_manifest_hash
                or content_hash(current.graph_json) != current.graph_hash
            ):
                invalid("Derived VNGraph chain contains a corrupted revision")
                return
            if manifest.get("manifest_version") != "vngraph-binding-derived-v1":
                if manifest.get("manifest_version") not in _SUPPORTED_GRAPH_MANIFESTS:
                    invalid("Derived VNGraph chain has an unsupported root manifest")
                return
            if current.id in seen:
                invalid("Derived VNGraph chain contains a cycle")
                return
            seen.add(current.id)

            derivation = manifest.get("derivation")
            patch = derivation.get("patch") if isinstance(derivation, dict) else None
            parent = (
                self.db.query(VNGraphRevision)
                .filter(VNGraphRevision.id == current.parent_revision_id)
                .one_or_none()
                if current.parent_revision_id
                else None
            )
            action_id = (
                derivation.get("asset_action_id")
                if isinstance(derivation, dict)
                else None
            )
            action = (
                self.db.query(AssetAction)
                .filter(AssetAction.id == action_id)
                .one_or_none()
                if isinstance(action_id, str) and action_id
                else None
            )
            if (
                not isinstance(derivation, dict)
                or derivation.get("kind") != "asset_action"
                or not isinstance(patch, list)
                or not parent
                or not action
                or parent.id in seen
                or parent.project_id != self.project_id
                or parent.status not in _SELECTABLE_STATUSES
                or parent.chapter_revision_id != current.chapter_revision_id
                or parent.script_revision_id != script.id
                or current.script_revision_id != script.id
                or derivation.get("parent_revision_id") != parent.id
                or derivation.get("parent_graph_hash") != parent.graph_hash
                or derivation.get("patch_hash") != content_hash(patch)
                or derivation.get("result_graph_hash") != current.graph_hash
                or content_hash(parent.graph_json) != parent.graph_hash
                or content_hash(parent.binding_manifest or {})
                != parent.binding_manifest_hash
                or parent.source_manifest_hash != parent.binding_manifest_hash
            ):
                invalid("Derived VNGraph parent or patch provenance is invalid")
                return

            target = action.target if isinstance(action.target, dict) else {}
            if (
                action.project_id != self.project_id
                or action.status != "applied"
                or action.base_graph_hash != parent.graph_hash
                or action.result_graph_hash != current.graph_hash
                or action.vngraph_patch != patch
                or target.get("source_kind") != "vn_graph_revision"
                or target.get("source_id") != parent.id
                or target.get("graph_revision_id") != parent.id
                or target.get("graph_hash") != parent.graph_hash
            ):
                invalid("Derived VNGraph AssetAction provenance is invalid")
                return

            prefix = f"asset-action:{action.id}:"
            action_bindings = sorted(
                (
                    item
                    for item in manifest.get("asset_bindings") or []
                    if isinstance(item, dict)
                    and isinstance(item.get("resource_slot_id"), str)
                    and item["resource_slot_id"].startswith(prefix)
                ),
                key=lambda item: (item.get("order_index", -1), item["resource_slot_id"]),
            )
            if [item.get("asset_version_id") for item in action_bindings] != list(
                action.result_asset_version_ids or []
            ):
                invalid("Derived VNGraph AssetAction result manifest is invalid")
                return
            try:
                validate_derived_vn_graph(
                    graph=current.graph_json,
                    manifest=manifest,
                    parent_graph=parent.graph_json,
                    patch=patch,
                )
            except VNGraphDerivationError as exc:
                invalid(f"Derived VNGraph replay validation failed: {exc}")
                return
            current = parent

    def _validate_asset_reference(
        self,
        reference: dict[str, Any],
        *,
        story_path_id: str,
        path_chapter_id: str,
    ) -> None:
        try:
            validate_asset_version_reference(
                self.db,
                reference,
                expected_project_id=self.project_id,
            )
        except ValueError:
            self._block(
                "vngraph.resource_invalid",
                "VNGraph AssetVersion snapshot is missing, cross-project, or corrupted",
                story_path_id=story_path_id,
                path_chapter_id=path_chapter_id,
            )
            return
        version = (
            self.db.query(AssetVersion)
            .filter(AssetVersion.id == reference.get("asset_version_id"))
            .one_or_none()
        )
        asset = (
            self.db.query(Asset).filter(Asset.id == version.asset_id).one_or_none()
            if version
            else None
        )
        storage = (
            self.db.query(StorageObject)
            .filter(StorageObject.id == version.storage_object_id)
            .one_or_none()
            if version and version.storage_object_id
            else None
        )
        if (
            not version
            or not asset
            or asset.project_id != self.project_id
            or not storage
            or storage.project_id not in {None, self.project_id}
            or storage.status != "active"
            or storage.deleted_at is not None
        ):
            self._block(
                "vngraph.resource_unavailable",
                "VNGraph resource storage is not active and publishable",
                story_path_id=story_path_id,
                path_chapter_id=path_chapter_id,
            )


def calculate_publication_readiness(
    db: Session,
    *,
    project_id: int,
) -> PublicationReadinessResult:
    """Calculate one deterministic snapshot without mutating authoring state."""

    project = db.query(Project).filter(Project.id == project_id).one_or_none()
    if not project:
        raise AppError(
            code="project.not_found",
            message="Project does not exist",
            status_code=404,
        )
    return _PublicationReadinessBuilder(db, project).build()


__all__ = [
    "PUBLICATION_FINGERPRINT_VERSION",
    "PublicationBlockingItem",
    "PublicationReadinessResult",
    "calculate_publication_readiness",
]
