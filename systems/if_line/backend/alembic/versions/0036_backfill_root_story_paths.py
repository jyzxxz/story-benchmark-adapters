"""Backfill root StoryPaths and stable chapter families.

Revision ID: 0036_backfill_root_story_paths
Revises: 0035_script_resource_slot_lock
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0036_backfill_root_story_paths"
down_revision = "0035_script_resource_slot_lock"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_ID_NAMESPACE = uuid.UUID("73745902-570d-46dc-93f4-808f3b64f68d")
_CREATED_TABLE = "_alembic_0036_created"
_REVISION_TABLE = "_alembic_0036_revision_binding"
_PLACEMENT_TABLE = "_alembic_0036_path_chapter_state"


def _stable_id(kind: str, *parts: object) -> str:
    identity = ":".join(str(part) for part in parts)
    return str(uuid.uuid5(_ID_NAMESPACE, f"{kind}:{identity}"))


def _table_exists(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(*values):
    return next((value for value in values if value is not None), _now())


def _create_scratch_tables(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_CREATED_TABLE} (
                object_type VARCHAR(32) NOT NULL,
                object_id VARCHAR(36) NOT NULL,
                PRIMARY KEY (object_type, object_id)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_REVISION_TABLE} (
                revision_id VARCHAR(36) PRIMARY KEY,
                old_chapter_slot_id VARCHAR(36),
                old_story_path_id VARCHAR(36)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_PLACEMENT_TABLE} (
                path_chapter_id VARCHAR(36) PRIMARY KEY,
                old_predecessor_id VARCHAR(36),
                old_current_revision_id VARCHAR(36),
                old_lock_version INTEGER NOT NULL,
                old_updated_at TIMESTAMP
            )
            """
        )
    )


def _record_created(bind, object_type: str, object_id: str) -> None:
    existing = bind.execute(
        sa.text(
            f"SELECT 1 FROM {_CREATED_TABLE} "
            "WHERE object_type = :object_type AND object_id = :object_id"
        ),
        {"object_type": object_type, "object_id": object_id},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                f"INSERT INTO {_CREATED_TABLE} (object_type, object_id) "
                "VALUES (:object_type, :object_id)"
            ),
            {"object_type": object_type, "object_id": object_id},
        )


def _record_revision_binding(bind, row) -> None:
    existing = bind.execute(
        sa.text(f"SELECT 1 FROM {_REVISION_TABLE} WHERE revision_id = :revision_id"),
        {"revision_id": row["id"]},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_REVISION_TABLE} (
                    revision_id, old_chapter_slot_id, old_story_path_id
                ) VALUES (
                    :revision_id, :old_chapter_slot_id, :old_story_path_id
                )
                """
            ),
            {
                "revision_id": row["id"],
                "old_chapter_slot_id": row["chapter_slot_id"],
                "old_story_path_id": row["created_for_story_path_id"],
            },
        )


def _record_placement_state(bind, row) -> None:
    existing = bind.execute(
        sa.text(
            f"SELECT 1 FROM {_PLACEMENT_TABLE} "
            "WHERE path_chapter_id = :path_chapter_id"
        ),
        {"path_chapter_id": row["id"]},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_PLACEMENT_TABLE} (
                    path_chapter_id, old_predecessor_id,
                    old_current_revision_id, old_lock_version, old_updated_at
                ) VALUES (
                    :path_chapter_id, :old_predecessor_id,
                    :old_current_revision_id, :old_lock_version, :old_updated_at
                )
                """
            ),
            {
                "path_chapter_id": row["id"],
                "old_predecessor_id": row["predecessor_path_chapter_id"],
                "old_current_revision_id": row["current_revision_id"],
                "old_lock_version": row["lock_version"],
                "old_updated_at": row["updated_at"],
            },
        )


def _root_path(bind, project) -> str:
    roots = bind.execute(
        sa.text(
            "SELECT id FROM story_paths "
            "WHERE project_id = :project_id AND parent_path_id IS NULL"
        ),
        {"project_id": project["id"]},
    ).all()
    if len(roots) > 1:
        raise RuntimeError(f"project {project['id']} has multiple root StoryPaths")
    if roots:
        return roots[0][0]

    root_id = _stable_id("root-path", project["id"])
    timestamp = _timestamp(project["created_at"], project["updated_at"])
    bind.execute(
        sa.text(
            """
            INSERT INTO story_paths (
                id, project_id, parent_path_id, fork_path_chapter_id,
                fork_checkpoint_node_id, fork_candidate_id,
                base_state_snapshot_id, title, status, lock_version,
                created_at, updated_at
            ) VALUES (
                :id, :project_id, NULL, NULL,
                NULL, NULL, NULL, 'Main', 'active', 1,
                :created_at, :updated_at
            )
            """
        ),
        {
            "id": root_id,
            "project_id": project["id"],
            "created_at": timestamp,
            "updated_at": timestamp,
        },
    )
    _record_created(bind, "story_path", root_id)
    return root_id


def _ensure_outline_head(bind, root_id: str, timestamp) -> None:
    existing = bind.execute(
        sa.text(
            "SELECT 1 FROM story_path_outline_heads WHERE story_path_id = :root_id"
        ),
        {"root_id": root_id},
    ).first()
    if existing is not None:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO story_path_outline_heads (
                story_path_id, current_revision_id, lock_version, updated_at
            ) VALUES (:root_id, NULL, 1, :updated_at)
            """
        ),
        {"root_id": root_id, "updated_at": timestamp},
    )
    _record_created(bind, "outline_head", root_id)


def _add_indexes(indexes: set[int], rows, *, source: str) -> None:
    for row in rows:
        value = int(row[0])
        if value <= 0:
            raise RuntimeError(f"{source} contains invalid chapter_index {value}")
        indexes.add(value)


def _legacy_chapter_indexes(bind, project_id: int, root_id: str) -> list[int]:
    indexes: set[int] = set()
    _add_indexes(
        indexes,
        bind.execute(
            sa.text(
                "SELECT chapter_index FROM chapter_revisions "
                "WHERE project_id = :project_id "
                "AND (chapter_slot_id IS NULL "
                "OR created_for_story_path_id IS NULL "
                "OR created_for_story_path_id = :root_id)"
            ),
            {"project_id": project_id, "root_id": root_id},
        ),
        source="chapter_revisions",
    )
    _add_indexes(
        indexes,
        bind.execute(
            sa.text(
                "SELECT chapter_index FROM chapter_heads "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project_id},
        ),
        source="chapter_heads",
    )
    _add_indexes(
        indexes,
        bind.execute(
            sa.text(
                """
                SELECT chapter.chapter_index
                  FROM outline_revision_chapters AS chapter
                  JOIN outline_revisions AS revision
                    ON revision.id = chapter.outline_revision_id
                 WHERE revision.project_id = :project_id
                   AND (revision.story_path_id IS NULL
                        OR revision.story_path_id = :root_id)
                """
            ),
            {"project_id": project_id, "root_id": root_id},
        ),
        source="outline_revision_chapters",
    )
    for table_name in ("chapter_outlines", "chapter_contents"):
        if not _table_exists(bind, table_name):
            continue
        _add_indexes(
            indexes,
            bind.execute(
                sa.text(
                    f"SELECT chapter_index FROM {table_name} "
                    "WHERE project_id = :project_id"
                ),
                {"project_id": project_id},
            ),
            source=table_name,
        )
    return sorted(indexes)


def _projects(bind):
    columns = {column["name"] for column in inspect(bind).get_columns("projects")}
    created_at = "created_at" if "created_at" in columns else "NULL AS created_at"
    updated_at = "updated_at" if "updated_at" in columns else "NULL AS updated_at"
    return bind.execute(
        sa.text(
            f"SELECT id, {created_at}, {updated_at} FROM projects ORDER BY id"
        )
    ).mappings().all()


def _placement(bind, root_id: str, chapter_index: int):
    return bind.execute(
        sa.text(
            """
            SELECT id, chapter_slot_id, predecessor_path_chapter_id,
                   current_revision_id, lock_version, updated_at
              FROM story_path_chapters
             WHERE story_path_id = :root_id AND display_index = :chapter_index
            """
        ),
        {"root_id": root_id, "chapter_index": chapter_index},
    ).mappings().first()


def _root_revision_slots(bind, project_id: int, root_id: str, chapter_index: int) -> set[str]:
    return {
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT DISTINCT chapter_slot_id
                  FROM chapter_revisions
                 WHERE project_id = :project_id
                   AND chapter_index = :chapter_index
                   AND created_for_story_path_id = :root_id
                   AND chapter_slot_id IS NOT NULL
                """
            ),
            {
                "project_id": project_id,
                "chapter_index": chapter_index,
                "root_id": root_id,
            },
        )
    }


def _validate_slot(bind, slot_id: str, project_id: int, root_id: str) -> None:
    slot = bind.execute(
        sa.text(
            "SELECT project_id, created_for_story_path_id "
            "FROM chapter_slots WHERE id = :slot_id"
        ),
        {"slot_id": slot_id},
    ).first()
    if slot != (project_id, root_id):
        raise RuntimeError(
            f"chapter slot {slot_id} does not belong to project {project_id} root {root_id}"
        )


def _ensure_slot(bind, project_id: int, root_id: str, chapter_index: int, timestamp) -> str:
    placement = _placement(bind, root_id, chapter_index)
    bound_slots = _root_revision_slots(bind, project_id, root_id, chapter_index)
    if len(bound_slots) > 1:
        raise RuntimeError(
            f"project {project_id} chapter {chapter_index} has multiple root ChapterSlots"
        )
    if placement is not None:
        slot_id = placement["chapter_slot_id"]
        if bound_slots and slot_id not in bound_slots:
            raise RuntimeError(
                f"project {project_id} chapter {chapter_index} has conflicting Slot bindings"
            )
        _validate_slot(bind, slot_id, project_id, root_id)
        return slot_id
    if bound_slots:
        slot_id = next(iter(bound_slots))
        _validate_slot(bind, slot_id, project_id, root_id)
        return slot_id

    slot_id = _stable_id("chapter-slot", project_id, chapter_index)
    existing = bind.execute(
        sa.text("SELECT project_id, created_for_story_path_id FROM chapter_slots WHERE id = :id"),
        {"id": slot_id},
    ).first()
    if existing is not None:
        if existing != (project_id, root_id):
            raise RuntimeError(f"deterministic ChapterSlot ID collision: {slot_id}")
        return slot_id
    bind.execute(
        sa.text(
            """
            INSERT INTO chapter_slots (
                id, project_id, created_for_story_path_id, created_at
            ) VALUES (:id, :project_id, :root_id, :created_at)
            """
        ),
        {
            "id": slot_id,
            "project_id": project_id,
            "root_id": root_id,
            "created_at": timestamp,
        },
    )
    _record_created(bind, "chapter_slot", slot_id)
    return slot_id


def _bind_revisions(bind, project_id: int, root_id: str, chapter_index: int, slot_id: str) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, chapter_slot_id, created_for_story_path_id
              FROM chapter_revisions
             WHERE project_id = :project_id AND chapter_index = :chapter_index
             ORDER BY revision_no, created_at, id
            """
        ),
        {"project_id": project_id, "chapter_index": chapter_index},
    ).mappings().all()
    for row in rows:
        old_slot = row["chapter_slot_id"]
        old_path = row["created_for_story_path_id"]
        if old_slot is None and old_path is None:
            _record_revision_binding(bind, row)
            bind.execute(
                sa.text(
                    """
                    UPDATE chapter_revisions
                       SET chapter_slot_id = :slot_id,
                           created_for_story_path_id = :root_id
                     WHERE id = :revision_id
                    """
                ),
                {
                    "slot_id": slot_id,
                    "root_id": root_id,
                    "revision_id": row["id"],
                },
            )
            continue
        if old_slot is None or old_path is None:
            raise RuntimeError(f"chapter revision {row['id']} has a partial path binding")
        if old_path == root_id and old_slot != slot_id:
            raise RuntimeError(f"chapter revision {row['id']} has a conflicting root Slot")


def _legacy_head(bind, project_id: int, chapter_index: int):
    return bind.execute(
        sa.text(
            """
            SELECT current_revision_id, lock_version, updated_at
              FROM chapter_heads
             WHERE project_id = :project_id AND chapter_index = :chapter_index
            """
        ),
        {"project_id": project_id, "chapter_index": chapter_index},
    ).mappings().first()


def _validate_head_revision(
    bind,
    *,
    revision_id: str,
    project_id: int,
    chapter_index: int,
    slot_id: str,
    root_id: str,
) -> None:
    revision_row = bind.execute(
        sa.text(
            """
            SELECT project_id, chapter_index, chapter_slot_id,
                   created_for_story_path_id
              FROM chapter_revisions
             WHERE id = :revision_id
            """
        ),
        {"revision_id": revision_id},
    ).first()
    expected = (project_id, chapter_index, slot_id, root_id)
    if revision_row != expected:
        raise RuntimeError(
            f"legacy ChapterHead {project_id}:{chapter_index} points outside its root Slot"
        )


def _ensure_placement(
    bind,
    *,
    project_id: int,
    root_id: str,
    chapter_index: int,
    slot_id: str,
    timestamp,
) -> str:
    placement = _placement(bind, root_id, chapter_index)
    head = _legacy_head(bind, project_id, chapter_index)
    desired_revision_id = head["current_revision_id"] if head else None
    if desired_revision_id:
        _validate_head_revision(
            bind,
            revision_id=desired_revision_id,
            project_id=project_id,
            chapter_index=chapter_index,
            slot_id=slot_id,
            root_id=root_id,
        )
    if placement is None:
        path_chapter_id = _stable_id("path-chapter", project_id, chapter_index)
        lock_version = max(int(head["lock_version"]), 1) if head else 1
        updated_at = _timestamp(head["updated_at"] if head else None, timestamp)
        bind.execute(
            sa.text(
                """
                INSERT INTO story_path_chapters (
                    id, story_path_id, chapter_slot_id, display_index,
                    predecessor_path_chapter_id,
                    inherited_from_path_chapter_id, current_revision_id,
                    lock_version, created_at, updated_at
                ) VALUES (
                    :id, :root_id, :slot_id, :display_index,
                    NULL, NULL, :current_revision_id,
                    :lock_version, :created_at, :updated_at
                )
                """
            ),
            {
                "id": path_chapter_id,
                "root_id": root_id,
                "slot_id": slot_id,
                "display_index": chapter_index,
                "current_revision_id": desired_revision_id,
                "lock_version": lock_version,
                "created_at": timestamp,
                "updated_at": updated_at,
            },
        )
        _record_created(bind, "path_chapter", path_chapter_id)
        return path_chapter_id

    if placement["chapter_slot_id"] != slot_id:
        raise RuntimeError(
            f"project {project_id} chapter {chapter_index} placement has a conflicting Slot"
        )
    current_revision_id = placement["current_revision_id"]
    if desired_revision_id and current_revision_id not in (None, desired_revision_id):
        raise RuntimeError(
            f"project {project_id} chapter {chapter_index} has conflicting Head pointers"
        )
    if desired_revision_id and current_revision_id is None:
        _record_placement_state(bind, placement)
        bind.execute(
            sa.text(
                """
                UPDATE story_path_chapters
                   SET current_revision_id = :current_revision_id,
                       lock_version = :lock_version,
                       updated_at = :updated_at
                 WHERE id = :path_chapter_id
                """
            ),
            {
                "current_revision_id": desired_revision_id,
                "lock_version": max(int(head["lock_version"]), 1),
                "updated_at": _timestamp(head["updated_at"], timestamp),
                "path_chapter_id": placement["id"],
            },
        )
    return placement["id"]


def _repair_predecessor_chain(bind, root_id: str) -> None:
    placements = bind.execute(
        sa.text(
            """
            SELECT id, predecessor_path_chapter_id, current_revision_id,
                   lock_version, updated_at
              FROM story_path_chapters
             WHERE story_path_id = :root_id
             ORDER BY display_index, id
            """
        ),
        {"root_id": root_id},
    ).mappings().all()
    predecessor_id = None
    for placement in placements:
        if placement["predecessor_path_chapter_id"] != predecessor_id:
            created = bind.execute(
                sa.text(
                    f"SELECT 1 FROM {_CREATED_TABLE} "
                    "WHERE object_type = 'path_chapter' AND object_id = :id"
                ),
                {"id": placement["id"]},
            ).first()
            if created is None:
                _record_placement_state(bind, placement)
            bind.execute(
                sa.text(
                    "UPDATE story_path_chapters "
                    "SET predecessor_path_chapter_id = :predecessor_id "
                    "WHERE id = :id"
                ),
                {"predecessor_id": predecessor_id, "id": placement["id"]},
            )
        predecessor_id = placement["id"]


def upgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    # Historical migration tests and emergency repair databases may be stamped
    # at 0035 with only the table under repair. There is nothing to backfill
    # until the legacy projects table exists.
    if not _table_exists(bind, "projects"):
        return
    _create_scratch_tables(bind)
    for project in _projects(bind):
        timestamp = _timestamp(project["created_at"], project["updated_at"])
        root_id = _root_path(bind, project)
        _ensure_outline_head(bind, root_id, timestamp)
        for chapter_index in _legacy_chapter_indexes(bind, project["id"], root_id):
            slot_id = _ensure_slot(
                bind,
                project["id"],
                root_id,
                chapter_index,
                timestamp,
            )
            _bind_revisions(
                bind,
                project["id"],
                root_id,
                chapter_index,
                slot_id,
            )
            _ensure_placement(
                bind,
                project_id=project["id"],
                root_id=root_id,
                chapter_index=chapter_index,
                slot_id=slot_id,
                timestamp=timestamp,
            )
        _repair_predecessor_chain(bind, root_id)


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    scratch_tables = (_CREATED_TABLE, _REVISION_TABLE, _PLACEMENT_TABLE)
    if not all(_table_exists(bind, table_name) for table_name in scratch_tables):
        return

    bind.execute(
        sa.text(
            f"""
            UPDATE story_path_chapters
               SET predecessor_path_chapter_id = (
                       SELECT old_predecessor_id
                         FROM {_PLACEMENT_TABLE}
                        WHERE path_chapter_id = story_path_chapters.id
                   ),
                   current_revision_id = (
                       SELECT old_current_revision_id
                         FROM {_PLACEMENT_TABLE}
                        WHERE path_chapter_id = story_path_chapters.id
                   ),
                   lock_version = (
                       SELECT old_lock_version
                         FROM {_PLACEMENT_TABLE}
                        WHERE path_chapter_id = story_path_chapters.id
                   ),
                   updated_at = (
                       SELECT old_updated_at
                         FROM {_PLACEMENT_TABLE}
                        WHERE path_chapter_id = story_path_chapters.id
                   )
             WHERE id IN (SELECT path_chapter_id FROM {_PLACEMENT_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE chapter_revisions
               SET chapter_slot_id = (
                       SELECT old_chapter_slot_id
                         FROM {_REVISION_TABLE}
                        WHERE revision_id = chapter_revisions.id
                   ),
                   created_for_story_path_id = (
                       SELECT old_story_path_id
                         FROM {_REVISION_TABLE}
                        WHERE revision_id = chapter_revisions.id
                   )
             WHERE id IN (SELECT revision_id FROM {_REVISION_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM story_path_chapters
             WHERE id IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = 'path_chapter'
             )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM chapter_slots
             WHERE id IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = 'chapter_slot'
             )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM story_path_outline_heads
             WHERE story_path_id IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = 'outline_head'
             )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM story_paths
             WHERE id IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = 'story_path'
             )
            """
        )
    )
    for table_name in (_PLACEMENT_TABLE, _REVISION_TABLE, _CREATED_TABLE):
        bind.execute(sa.text(f"DROP TABLE {table_name}"))
