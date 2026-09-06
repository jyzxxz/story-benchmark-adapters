"""Enforce StoryPath ownership, immutable sources, and active publication integrity.

Revision ID: 0039_story_path_integrity
Revises: 0038_backfill_project_publications
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0039_story_path_integrity"
down_revision = "0038_backfill_project_publications"
branch_labels = None
depends_on = None


TABLES = ("authoring_project_delete_scopes",)


class _Guard:
    __slots__ = (
        "name",
        "table",
        "events",
        "required_tables",
        "condition",
        "message",
        "postgresql_condition",
    )

    def __init__(
        self,
        name: str,
        table: str,
        events: tuple[str, ...],
        required_tables: tuple[str, ...],
        condition: str,
        message: str,
        postgresql_condition: str | None = None,
    ) -> None:
        self.name = name
        self.table = table
        self.events = events
        self.required_tables = required_tables
        self.condition = condition
        self.message = message
        self.postgresql_condition = postgresql_condition


_GUARDS = (
    _Guard(
        "slot_owner",
        "chapter_slots",
        ("INSERT", "UPDATE"),
        ("chapter_slots", "story_paths"),
        "NOT EXISTS (SELECT 1 FROM story_paths p "
        "WHERE p.id = NEW.created_for_story_path_id "
        "AND p.project_id = NEW.project_id)",
        "ChapterSlot StoryPath belongs to another Project",
    ),
    _Guard(
        "path_chapter_owner",
        "story_path_chapters",
        ("INSERT", "UPDATE"),
        ("story_path_chapters", "story_paths", "chapter_slots", "chapter_revisions"),
        "NOT EXISTS (SELECT 1 FROM story_paths p "
        "JOIN chapter_slots s ON s.id = NEW.chapter_slot_id "
        "WHERE p.id = NEW.story_path_id AND p.project_id = s.project_id) "
        "OR (NEW.predecessor_path_chapter_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_path_chapters pc "
        "WHERE pc.id = NEW.predecessor_path_chapter_id "
        "AND pc.story_path_id = NEW.story_path_id)) "
        "OR (NEW.inherited_from_path_chapter_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_path_chapters pc "
        "JOIN story_paths inherited_path ON inherited_path.id = pc.story_path_id "
        "JOIN story_paths owner_path ON owner_path.id = NEW.story_path_id "
        "WHERE pc.id = NEW.inherited_from_path_chapter_id "
        "AND inherited_path.project_id = owner_path.project_id)) "
        "OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM chapter_revisions cr "
        "JOIN story_paths p ON p.id = NEW.story_path_id "
        "WHERE cr.id = NEW.current_revision_id "
        "AND cr.chapter_slot_id = NEW.chapter_slot_id "
        "AND cr.project_id = p.project_id))",
        "StoryPathChapter ownership or Head family is invalid",
    ),
    _Guard(
        "outline_head_family",
        "story_path_outline_heads",
        ("INSERT", "UPDATE"),
        ("story_path_outline_heads", "outline_revisions"),
        "NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM outline_revisions r "
        "WHERE r.id = NEW.current_revision_id "
        "AND r.story_path_id = NEW.story_path_id)",
        "Outline Head Revision belongs to another StoryPath",
    ),
    _Guard(
        "script_head_family",
        "chapter_script_heads",
        ("INSERT", "UPDATE"),
        ("chapter_script_heads", "chapter_revisions", "chapter_script_revisions"),
        "NOT EXISTS (SELECT 1 FROM chapter_revisions c "
        "WHERE c.id = NEW.chapter_revision_id AND c.project_id = NEW.project_id) "
        "OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM chapter_script_revisions r "
        "WHERE r.id = NEW.current_revision_id "
        "AND r.project_id = NEW.project_id "
        "AND r.chapter_revision_id = NEW.chapter_revision_id))",
        "Script Head Revision belongs to another chapter family",
    ),
    _Guard(
        "graph_head_family",
        "vn_graph_heads",
        ("INSERT", "UPDATE"),
        ("vn_graph_heads", "chapter_script_revisions", "vn_graph_revisions"),
        "NOT EXISTS (SELECT 1 FROM chapter_script_revisions s "
        "WHERE s.id = NEW.script_revision_id AND s.project_id = NEW.project_id) "
        "OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM vn_graph_revisions r "
        "WHERE r.id = NEW.current_revision_id "
        "AND r.project_id = NEW.project_id "
        "AND r.script_revision_id = NEW.script_revision_id))",
        "VNGraph Head Revision belongs to another Script family",
    ),
    _Guard(
        "candidate_head_family",
        "candidate_set_heads",
        ("INSERT", "UPDATE"),
        ("candidate_set_heads", "story_paths", "story_nodes", "candidate_set_revisions"),
        "NOT EXISTS (SELECT 1 FROM story_paths p JOIN story_nodes n "
        "ON n.project_id = p.project_id "
        "WHERE p.id = NEW.story_path_id AND n.id = NEW.checkpoint_node_id) "
        "OR (NEW.current_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM candidate_set_revisions r "
        "WHERE r.id = NEW.current_revision_id "
        "AND r.story_path_id = NEW.story_path_id "
        "AND r.checkpoint_node_id = NEW.checkpoint_node_id))",
        "CandidateSet Head Revision belongs to another checkpoint family",
    ),
    _Guard(
        "bible_parent",
        "story_bible_revisions",
        ("INSERT",),
        ("story_bible_revisions",),
        "NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_bible_revisions r "
        "WHERE r.id = NEW.parent_revision_id AND r.project_id = NEW.project_id)",
        "Bible parent Revision belongs to another Project",
    ),
    _Guard(
        "outline_sources",
        "outline_revisions",
        ("INSERT",),
        ("outline_revisions", "story_bible_revisions", "story_paths"),
        "NOT EXISTS (SELECT 1 FROM story_bible_revisions b "
        "WHERE b.id = NEW.bible_revision_id AND b.project_id = NEW.project_id) "
        "OR (NEW.story_path_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_paths p WHERE p.id = NEW.story_path_id "
        "AND p.project_id = NEW.project_id)) "
        "OR (NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM outline_revisions r WHERE r.id = NEW.parent_revision_id "
        "AND r.project_id = NEW.project_id AND ("
        "r.story_path_id = NEW.story_path_id OR "
        "(r.story_path_id IS NULL AND NEW.story_path_id IS NULL))))",
        "Outline Revision sources belong to another family",
    ),
    _Guard(
        "chapter_sources",
        "chapter_revisions",
        ("INSERT",),
        (
            "chapter_revisions",
            "story_bible_revisions",
            "outline_revisions",
            "chapter_slots",
            "story_paths",
            "state_snapshots",
        ),
        "NOT EXISTS (SELECT 1 FROM story_bible_revisions b "
        "WHERE b.id = NEW.bible_revision_id AND b.project_id = NEW.project_id) "
        "OR NOT EXISTS (SELECT 1 FROM outline_revisions o "
        "WHERE o.id = NEW.outline_revision_id AND o.project_id = NEW.project_id) "
        "OR (NEW.chapter_slot_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM chapter_slots s WHERE s.id = NEW.chapter_slot_id "
        "AND s.project_id = NEW.project_id)) "
        "OR (NEW.created_for_story_path_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_paths p WHERE p.id = NEW.created_for_story_path_id "
        "AND p.project_id = NEW.project_id)) "
        "OR (NEW.state_snapshot_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM state_snapshots s WHERE s.id = NEW.state_snapshot_id "
        "AND s.project_id = NEW.project_id)) "
        "OR (NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM chapter_revisions r WHERE r.id = NEW.parent_revision_id "
        "AND r.project_id = NEW.project_id AND ((NEW.chapter_slot_id IS NOT NULL "
        "AND r.chapter_slot_id = NEW.chapter_slot_id) OR (NEW.chapter_slot_id IS NULL "
        "AND r.chapter_slot_id IS NULL AND r.chapter_index = NEW.chapter_index))))",
        "Chapter Revision sources belong to another family",
    ),
    _Guard(
        "script_sources",
        "chapter_script_revisions",
        ("INSERT",),
        ("chapter_script_revisions", "chapter_revisions"),
        "NOT EXISTS (SELECT 1 FROM chapter_revisions c "
        "WHERE c.id = NEW.chapter_revision_id AND c.project_id = NEW.project_id "
        "AND c.bible_revision_id = NEW.bible_revision_id "
        "AND c.outline_revision_id = NEW.outline_revision_id) "
        "OR (NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM chapter_script_revisions r "
        "WHERE r.id = NEW.parent_revision_id "
        "AND r.chapter_revision_id = NEW.chapter_revision_id))",
        "Script Revision sources belong to another family",
    ),
    _Guard(
        "graph_sources",
        "vn_graph_revisions",
        ("INSERT",),
        ("vn_graph_revisions", "chapter_script_revisions"),
        "NOT EXISTS (SELECT 1 FROM chapter_script_revisions s "
        "WHERE s.id = NEW.script_revision_id AND s.project_id = NEW.project_id "
        "AND s.chapter_revision_id = NEW.chapter_revision_id) "
        "OR (NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM vn_graph_revisions r WHERE r.id = NEW.parent_revision_id "
        "AND r.script_revision_id = NEW.script_revision_id))",
        "VNGraph Revision sources belong to another family",
    ),
    _Guard(
        "candidate_sources",
        "candidate_set_revisions",
        ("INSERT",),
        (
            "candidate_set_revisions",
            "story_paths",
            "story_nodes",
            "chapter_revisions",
            "state_snapshots",
        ),
        "NOT EXISTS (SELECT 1 FROM story_paths p, story_nodes n, "
        "chapter_revisions c, state_snapshots s "
        "WHERE p.id = NEW.story_path_id AND n.id = NEW.checkpoint_node_id "
        "AND c.id = NEW.chapter_revision_id AND s.id = NEW.state_snapshot_id "
        "AND p.project_id = NEW.project_id AND n.project_id = NEW.project_id "
        "AND c.project_id = NEW.project_id AND s.project_id = NEW.project_id) "
        "OR (NEW.parent_revision_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM candidate_set_revisions r "
        "WHERE r.id = NEW.parent_revision_id "
        "AND r.story_path_id = NEW.story_path_id "
        "AND r.checkpoint_node_id = NEW.checkpoint_node_id))",
        "CandidateSet Revision sources belong to another Project or family",
    ),
    _Guard(
        "outline_chapter_insert",
        "outline_revision_chapters",
        ("INSERT",),
        ("outline_revision_chapters", "outline_revisions", "story_path_chapters"),
        "NEW.story_path_chapter_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM outline_revisions o JOIN story_path_chapters pc "
        "ON pc.story_path_id = o.story_path_id "
        "WHERE o.id = NEW.outline_revision_id "
        "AND pc.id = NEW.story_path_chapter_id)",
        "OutlineChapter PathChapter belongs to another StoryPath",
    ),
    _Guard(
        "outline_chapter_update",
        "outline_revision_chapters",
        ("UPDATE",),
        ("outline_revision_chapters", "outline_revisions", "story_path_chapters"),
        "NOT (OLD.story_path_chapter_id IS NULL "
        "AND NEW.story_path_chapter_id IS NOT NULL "
        "AND NEW.id IS OLD.id "
        "AND NEW.outline_revision_id IS OLD.outline_revision_id "
        "AND NEW.chapter_index IS OLD.chapter_index "
        "AND NEW.display_index IS OLD.display_index "
        "AND NEW.title IS OLD.title AND NEW.summary IS OLD.summary "
        "AND NEW.conflict IS OLD.conflict AND NEW.characters IS OLD.characters "
        "AND NEW.scene IS OLD.scene AND NEW.emotion IS OLD.emotion "
        "AND NEW.visual_keywords IS OLD.visual_keywords "
        "AND NEW.content_hash IS OLD.content_hash "
        "AND NEW.legacy_source_id IS OLD.legacy_source_id "
        "AND EXISTS (SELECT 1 FROM outline_revisions o "
        "JOIN story_path_chapters pc ON pc.story_path_id = o.story_path_id "
        "WHERE o.id = NEW.outline_revision_id "
        "AND pc.id = NEW.story_path_chapter_id))",
        "OutlineChapter is immutable except for one PathChapter reconciliation",
        "NOT (OLD.story_path_chapter_id IS NULL "
        "AND NEW.story_path_chapter_id IS NOT NULL "
        "AND NEW.id IS NOT DISTINCT FROM OLD.id "
        "AND NEW.outline_revision_id IS NOT DISTINCT FROM OLD.outline_revision_id "
        "AND NEW.chapter_index IS NOT DISTINCT FROM OLD.chapter_index "
        "AND NEW.display_index IS NOT DISTINCT FROM OLD.display_index "
        "AND NEW.title IS NOT DISTINCT FROM OLD.title "
        "AND NEW.summary IS NOT DISTINCT FROM OLD.summary "
        "AND NEW.conflict IS NOT DISTINCT FROM OLD.conflict "
        "AND NEW.characters IS NOT DISTINCT FROM OLD.characters "
        "AND NEW.scene IS NOT DISTINCT FROM OLD.scene "
        "AND NEW.emotion IS NOT DISTINCT FROM OLD.emotion "
        "AND NEW.visual_keywords IS NOT DISTINCT FROM OLD.visual_keywords "
        "AND NEW.content_hash IS NOT DISTINCT FROM OLD.content_hash "
        "AND NEW.legacy_source_id IS NOT DISTINCT FROM OLD.legacy_source_id "
        "AND EXISTS (SELECT 1 FROM outline_revisions o "
        "JOIN story_path_chapters pc ON pc.story_path_id = o.story_path_id "
        "WHERE o.id = NEW.outline_revision_id "
        "AND pc.id = NEW.story_path_chapter_id))",
    ),
    _Guard(
        "bible_immutable",
        "story_bible_revisions",
        ("UPDATE",),
        ("story_bible_revisions",),
        "1 = 1",
        "StoryBibleRevision is immutable",
    ),
    _Guard(
        "outline_immutable",
        "outline_revisions",
        ("UPDATE",),
        ("outline_revisions",),
        "NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id "
        "OR NEW.story_path_id IS NOT OLD.story_path_id "
        "OR NEW.bible_revision_id IS NOT OLD.bible_revision_id "
        "OR NEW.parent_revision_id IS NOT OLD.parent_revision_id "
        "OR NEW.revision_no IS NOT OLD.revision_no "
        "OR NEW.source_hash IS NOT OLD.source_hash "
        "OR NEW.content_hash IS NOT OLD.content_hash "
        "OR NEW.generation_task_id IS NOT OLD.generation_task_id "
        "OR NEW.legacy_source_table IS NOT OLD.legacy_source_table "
        "OR NEW.legacy_source_id IS NOT OLD.legacy_source_id "
        "OR NEW.created_by IS NOT OLD.created_by "
        "OR NEW.created_at IS NOT OLD.created_at",
        "OutlineRevision payload and provenance are immutable",
        "NEW.id IS DISTINCT FROM OLD.id OR NEW.project_id IS DISTINCT FROM OLD.project_id "
        "OR NEW.story_path_id IS DISTINCT FROM OLD.story_path_id "
        "OR NEW.bible_revision_id IS DISTINCT FROM OLD.bible_revision_id "
        "OR NEW.parent_revision_id IS DISTINCT FROM OLD.parent_revision_id "
        "OR NEW.revision_no IS DISTINCT FROM OLD.revision_no "
        "OR NEW.source_hash IS DISTINCT FROM OLD.source_hash "
        "OR NEW.content_hash IS DISTINCT FROM OLD.content_hash "
        "OR NEW.generation_task_id IS DISTINCT FROM OLD.generation_task_id "
        "OR NEW.legacy_source_table IS DISTINCT FROM OLD.legacy_source_table "
        "OR NEW.legacy_source_id IS DISTINCT FROM OLD.legacy_source_id "
        "OR NEW.created_by IS DISTINCT FROM OLD.created_by "
        "OR NEW.created_at IS DISTINCT FROM OLD.created_at",
    ),
    _Guard(
        "chapter_immutable",
        "chapter_revisions",
        ("UPDATE",),
        ("chapter_revisions",),
        "1 = 1",
        "ChapterRevision is immutable",
    ),
    _Guard(
        "state_immutable",
        "state_snapshots",
        ("UPDATE",),
        ("state_snapshots",),
        "1 = 1",
        "StateSnapshot is immutable",
    ),
    _Guard(
        "publication_target",
        "project_publications",
        ("INSERT", "UPDATE"),
        ("project_publications", "project_releases"),
        "NEW.active_release_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM project_releases r "
        "WHERE r.id = NEW.active_release_id AND r.project_id = NEW.project_id "
        "AND r.status = 'published' AND r.published_at IS NOT NULL "
        "AND r.withdrawn_at IS NULL)",
        "ProjectPublication target is not an active published Release",
    ),
    _Guard(
        "active_release_lifecycle",
        "project_releases",
        ("UPDATE",),
        ("project_releases", "project_publications"),
        "(NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id "
        "OR NEW.status <> 'published' OR NEW.published_at IS NULL "
        "OR NEW.withdrawn_at IS NOT NULL) AND EXISTS ("
        "SELECT 1 FROM project_publications p "
        "WHERE p.project_id = OLD.project_id AND p.active_release_id = OLD.id)",
        "Active Release must be unpublished before its lifecycle changes",
        "(NEW.id IS DISTINCT FROM OLD.id OR NEW.project_id IS DISTINCT FROM OLD.project_id "
        "OR NEW.status <> 'published' OR NEW.published_at IS NULL "
        "OR NEW.withdrawn_at IS NOT NULL) AND EXISTS ("
        "SELECT 1 FROM project_publications p "
        "WHERE p.project_id = OLD.project_id AND p.active_release_id = OLD.id)",
    ),
    _Guard(
        "story_path_sources",
        "story_paths",
        ("INSERT", "UPDATE"),
        (
            "story_paths",
            "story_path_chapters",
            "story_nodes",
            "state_snapshots",
        ),
        "(NEW.parent_path_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_paths parent WHERE parent.id = NEW.parent_path_id "
        "AND parent.project_id = NEW.project_id)) "
        "OR (NEW.fork_path_chapter_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_path_chapters pc WHERE pc.id = NEW.fork_path_chapter_id "
        "AND pc.story_path_id = NEW.parent_path_id)) "
        "OR (NEW.fork_checkpoint_node_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_nodes n WHERE n.id = NEW.fork_checkpoint_node_id "
        "AND n.project_id = NEW.project_id)) "
        "OR (NEW.base_state_snapshot_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM state_snapshots s WHERE s.id = NEW.base_state_snapshot_id "
        "AND s.project_id = NEW.project_id))",
        "StoryPath fork provenance belongs to another Project or parent path",
    ),
    _Guard(
        "state_parent",
        "state_snapshots",
        ("INSERT",),
        ("state_snapshots",),
        "NEW.parent_snapshot_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM state_snapshots parent "
        "WHERE parent.id = NEW.parent_snapshot_id "
        "AND parent.project_id = NEW.project_id)",
        "StateSnapshot parent belongs to another Project",
    ),
    _Guard(
        "release_sources",
        "project_releases",
        ("INSERT", "UPDATE"),
        (
            "project_releases",
            "story_bible_revisions",
            "outline_revisions",
            "story_paths",
        ),
        "NOT EXISTS (SELECT 1 FROM story_bible_revisions b "
        "WHERE b.id = NEW.bible_revision_id AND b.project_id = NEW.project_id) "
        "OR NOT EXISTS (SELECT 1 FROM outline_revisions o "
        "JOIN story_paths p ON p.id = o.story_path_id "
        "WHERE o.id = NEW.outline_revision_id "
        "AND o.project_id = NEW.project_id AND p.project_id = NEW.project_id)",
        "ProjectRelease sources belong to another Project",
    ),
    _Guard(
        "branch_edge_owner",
        "branch_edges",
        ("INSERT", "UPDATE"),
        ("branch_edges", "story_nodes"),
        "NOT EXISTS (SELECT 1 FROM story_nodes from_node, story_nodes to_node "
        "WHERE from_node.id = NEW.from_node_id AND to_node.id = NEW.to_node_id "
        "AND from_node.project_id = NEW.project_id "
        "AND to_node.project_id = NEW.project_id)",
        "BranchEdge nodes belong to another Project",
    ),
    _Guard(
        "reading_session_owner",
        "reading_sessions",
        ("INSERT", "UPDATE"),
        ("reading_sessions", "project_releases", "story_nodes", "state_snapshots"),
        "NOT EXISTS (SELECT 1 FROM project_releases r "
        "WHERE r.id = NEW.release_id AND r.project_id = NEW.project_id) "
        "OR (NEW.head_node_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM story_nodes n WHERE n.id = NEW.head_node_id "
        "AND n.project_id = NEW.project_id)) "
        "OR (NEW.state_snapshot_id IS NOT NULL AND NOT EXISTS ("
        "SELECT 1 FROM state_snapshots s WHERE s.id = NEW.state_snapshot_id "
        "AND s.project_id = NEW.project_id))",
        "ReadingSession sources belong to another Project",
    ),
    _Guard(
        "bible_delete",
        "story_bible_revisions",
        ("DELETE",),
        ("story_bible_revisions", "authoring_project_delete_scopes"),
        "NOT EXISTS (SELECT 1 FROM authoring_project_delete_scopes scope "
        "WHERE scope.project_id = OLD.project_id)",
        "StoryBibleRevision is immutable",
    ),
    _Guard(
        "outline_delete",
        "outline_revisions",
        ("DELETE",),
        ("outline_revisions", "authoring_project_delete_scopes"),
        "NOT EXISTS (SELECT 1 FROM authoring_project_delete_scopes scope "
        "WHERE scope.project_id = OLD.project_id)",
        "OutlineRevision is immutable",
    ),
    _Guard(
        "outline_chapter_delete",
        "outline_revision_chapters",
        ("DELETE",),
        (
            "outline_revision_chapters",
            "outline_revisions",
            "authoring_project_delete_scopes",
        ),
        "NOT EXISTS (SELECT 1 FROM outline_revisions o "
        "JOIN authoring_project_delete_scopes scope "
        "ON scope.project_id = o.project_id "
        "WHERE o.id = OLD.outline_revision_id)",
        "OutlineChapter is immutable",
    ),
    _Guard(
        "chapter_delete",
        "chapter_revisions",
        ("DELETE",),
        ("chapter_revisions", "authoring_project_delete_scopes"),
        "NOT EXISTS (SELECT 1 FROM authoring_project_delete_scopes scope "
        "WHERE scope.project_id = OLD.project_id)",
        "ChapterRevision is immutable",
    ),
    _Guard(
        "state_delete",
        "state_snapshots",
        ("DELETE",),
        ("state_snapshots", "authoring_project_delete_scopes"),
        "NOT EXISTS (SELECT 1 FROM authoring_project_delete_scopes scope "
        "WHERE scope.project_id = OLD.project_id)",
        "StateSnapshot is immutable",
    ),
)


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_tables(bind, names: tuple[str, ...]) -> bool:
    if _is_offline():
        return True
    inspector = inspect(bind)
    return all(inspector.has_table(name) for name in names)


def _unique_names(bind, table_name: str) -> set[str]:
    if _is_offline() or not inspect(bind).has_table(table_name):
        return set()
    return {
        str(item["name"])
        for item in inspect(bind).get_unique_constraints(table_name)
        if item.get("name")
    }


def _upgrade_bible_constraints(bind) -> None:
    if not _has_tables(bind, ("story_bible_revisions",)):
        return
    if not _is_offline():
        duplicate = bind.execute(
            sa.text(
                "SELECT generation_task_id, COUNT(*) AS row_count "
                "FROM story_bible_revisions "
                "WHERE generation_task_id IS NOT NULL "
                "GROUP BY generation_task_id HAVING COUNT(*) > 1 LIMIT 1"
            )
        ).first()
        if duplicate is not None:
            raise RuntimeError(
                "duplicate StoryBibleRevision generation_task_id prevents 0039 upgrade: "
                f"{duplicate[0]} ({duplicate[1]} rows)"
            )
    existing = _unique_names(bind, "story_bible_revisions")
    drop_content = _is_offline() or "uq_bible_revision_content" in existing
    add_task = _is_offline() or "uq_bible_revision_generation_task" not in existing
    if bind.dialect.name == "sqlite":
        if drop_content or add_task:
            with op.batch_alter_table(
                "story_bible_revisions", recreate="always"
            ) as batch:
                if drop_content:
                    batch.drop_constraint(
                        "uq_bible_revision_content", type_="unique"
                    )
                if add_task:
                    batch.create_unique_constraint(
                        "uq_bible_revision_generation_task",
                        ["generation_task_id"],
                    )
        return
    if drop_content:
        op.drop_constraint(
            "uq_bible_revision_content",
            "story_bible_revisions",
            type_="unique",
        )
    if add_task:
        op.create_unique_constraint(
            "uq_bible_revision_generation_task",
            "story_bible_revisions",
            ["generation_task_id"],
        )


def _downgrade_bible_constraints(bind) -> None:
    if not _has_tables(bind, ("story_bible_revisions",)):
        return
    existing = _unique_names(bind, "story_bible_revisions")
    drop_task = _is_offline() or "uq_bible_revision_generation_task" in existing
    add_content = _is_offline() or "uq_bible_revision_content" not in existing
    if bind.dialect.name == "sqlite":
        if drop_task or add_content:
            with op.batch_alter_table(
                "story_bible_revisions", recreate="always"
            ) as batch:
                if drop_task:
                    batch.drop_constraint(
                        "uq_bible_revision_generation_task", type_="unique"
                    )
                if add_content:
                    batch.create_unique_constraint(
                        "uq_bible_revision_content", ["project_id", "content_hash"]
                    )
        return
    if drop_task:
        op.drop_constraint(
            "uq_bible_revision_generation_task",
            "story_bible_revisions",
            type_="unique",
        )
    if add_content:
        op.create_unique_constraint(
            "uq_bible_revision_content",
            "story_bible_revisions",
            ["project_id", "content_hash"],
        )


def _trigger_name(guard: _Guard, event: str) -> str:
    return f"trg_0039_{guard.name}_{event.lower()}"


def _function_name(guard: _Guard) -> str:
    return f"fn_0039_{guard.name}"


def _install_guard(bind, guard: _Guard) -> None:
    if not _has_tables(bind, guard.required_tables):
        return
    if bind.dialect.name == "sqlite":
        escaped = guard.message.replace("'", "''")
        for event in guard.events:
            trigger = _trigger_name(guard, event)
            op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger}"))
            op.execute(
                sa.text(
                    f"CREATE TRIGGER {trigger} BEFORE {event} ON {guard.table} "
                    f"WHEN ({guard.condition}) BEGIN "
                    f"SELECT RAISE(ABORT, '{escaped}'); END"
                )
            )
        return
    function = _function_name(guard)
    condition = guard.postgresql_condition or guard.condition
    escaped = guard.message.replace("'", "''")
    op.execute(
        sa.text(
            f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            f"IF {condition} THEN RAISE EXCEPTION '{escaped}' "
            "USING ERRCODE = '23514'; END IF; "
            "IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; "
            "RETURN NEW; END; $$"
        )
    )
    for event in guard.events:
        trigger = _trigger_name(guard, event)
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {trigger} ON {guard.table}"))
        op.execute(
            sa.text(
                f"CREATE TRIGGER {trigger} BEFORE {event} ON {guard.table} "
                f"FOR EACH ROW EXECUTE FUNCTION {function}()"
            )
        )


def _drop_guard(bind, guard: _Guard) -> None:
    if not _has_tables(bind, (guard.table,)):
        return
    if bind.dialect.name == "sqlite":
        for event in guard.events:
            op.execute(
                sa.text(f"DROP TRIGGER IF EXISTS {_trigger_name(guard, event)}")
            )
        return
    for event in guard.events:
        op.execute(
            sa.text(
                f"DROP TRIGGER IF EXISTS {_trigger_name(guard, event)} "
                f"ON {guard.table}"
            )
        )
    op.execute(sa.text(f"DROP FUNCTION IF EXISTS {_function_name(guard)}()"))


def _normalize_active_release_timestamps(bind) -> None:
    if not _has_tables(bind, ("project_publications", "project_releases")):
        return
    op.execute(
        sa.text(
            "UPDATE project_releases SET published_at = ("
            "SELECT p.published_at FROM project_publications p "
            "WHERE p.project_id = project_releases.project_id "
            "AND p.active_release_id = project_releases.id) "
            "WHERE published_at IS NULL AND EXISTS ("
            "SELECT 1 FROM project_publications p "
            "WHERE p.project_id = project_releases.project_id "
            "AND p.active_release_id = project_releases.id)"
        )
    )
    if not _is_offline():
        invalid = bind.execute(
            sa.text(
                "SELECT p.project_id, p.active_release_id "
                "FROM project_publications p LEFT JOIN project_releases r "
                "ON r.project_id = p.project_id AND r.id = p.active_release_id "
                "WHERE p.active_release_id IS NOT NULL AND (r.id IS NULL "
                "OR r.status <> 'published' OR r.published_at IS NULL "
                "OR r.withdrawn_at IS NOT NULL) LIMIT 1"
            )
        ).first()
        if invalid is not None:
            raise RuntimeError(
                "invalid active ProjectPublication prevents 0039 upgrade: "
                f"project={invalid[0]} release={invalid[1]}"
            )


def _create_delete_scope_table(bind) -> None:
    if not _has_tables(bind, ("projects",)):
        return
    if not _is_offline() and inspect(bind).has_table(
        "authoring_project_delete_scopes"
    ):
        return
    op.create_table(
        "authoring_project_delete_scopes",
        sa.Column("project_id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_delete_scope_table(bind)
    _upgrade_bible_constraints(bind)
    _normalize_active_release_timestamps(bind)
    for guard in _GUARDS:
        _install_guard(bind, guard)


def downgrade() -> None:
    bind = op.get_bind()
    for guard in reversed(_GUARDS):
        _drop_guard(bind, guard)
    _downgrade_bible_constraints(bind)
    if _has_tables(bind, ("authoring_project_delete_scopes",)):
        op.drop_table("authoring_project_delete_scopes")
