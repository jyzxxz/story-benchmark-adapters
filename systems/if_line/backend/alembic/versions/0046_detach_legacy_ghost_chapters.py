"""Align legacy PathChapter lifecycle with the selected Outline.

Revision ID: 0046_detach_legacy_ghost_chapters
Revises: 0045_generation_task_lease_fencing
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0046_detach_legacy_ghost_chapters"
down_revision = "0045_generation_task_lease_fencing"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_PATH_REPAIR_TABLE = "_alembic_0046_story_path_repair"
_CHAPTER_REPAIR_TABLE = "_alembic_0046_path_chapter_repair"
_REQUIRED_TABLES = (
    "outline_revision_chapters",
    "outline_revisions",
    "story_path_chapters",
    "story_path_outline_heads",
    "story_paths",
)


def _has_table(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _has_required_schema(bind) -> bool:
    inspector = inspect(bind)
    if not all(inspector.has_table(table_name) for table_name in _REQUIRED_TABLES):
        return False
    chapter_columns = {
        column["name"] for column in inspector.get_columns("story_path_chapters")
    }
    return {
        "status",
        "detached_at",
        "detached_by_outline_revision_id",
    } <= chapter_columns


def _create_repair_tables(bind) -> None:
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if bind.dialect.name == "postgresql"
        else "TIMESTAMP"
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE {_PATH_REPAIR_TABLE} (
                story_path_id VARCHAR(36) PRIMARY KEY,
                old_lock_version INTEGER NOT NULL,
                old_updated_at {timestamp_type} NOT NULL
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE {_CHAPTER_REPAIR_TABLE} (
                path_chapter_id VARCHAR(36) PRIMARY KEY,
                story_path_id VARCHAR(36) NOT NULL,
                old_display_index INTEGER NOT NULL,
                old_predecessor_path_chapter_id VARCHAR(36),
                old_status VARCHAR(24) NOT NULL,
                old_detached_at {timestamp_type},
                old_detached_by_outline_revision_id VARCHAR(36),
                old_lock_version INTEGER NOT NULL,
                old_updated_at {timestamp_type} NOT NULL
            )
            """
        )
    )


def _selected_outlines(bind):
    return bind.execute(
        sa.text(
            """
            SELECT head.story_path_id, head.current_revision_id,
                   revision.story_path_id AS revision_story_path_id,
                   path.lock_version, path.updated_at
              FROM story_path_outline_heads AS head
              LEFT JOIN outline_revisions AS revision
                ON revision.id = head.current_revision_id
              JOIN story_paths AS path ON path.id = head.story_path_id
             WHERE head.current_revision_id IS NOT NULL
             ORDER BY head.story_path_id
            """
        )
    ).mappings().all()


def _outline_topology(bind, revision_id: str) -> list[dict[str, Any]]:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, story_path_chapter_id, display_index
              FROM outline_revision_chapters
             WHERE outline_revision_id = :revision_id
             ORDER BY display_index, id
            """
        ),
        {"revision_id": revision_id},
    ).mappings().all()
    if not rows:
        raise RuntimeError(f"selected Outline {revision_id} has no chapters")
    if any(not row["story_path_chapter_id"] for row in rows):
        raise RuntimeError(
            f"selected Outline {revision_id} has incomplete PathChapter bindings"
        )
    chapter_ids = [str(row["story_path_chapter_id"]) for row in rows]
    if len(chapter_ids) != len(set(chapter_ids)):
        raise RuntimeError(f"selected Outline {revision_id} repeats a PathChapter")
    indexes = [int(row["display_index"]) for row in rows]
    if indexes != list(range(1, len(rows) + 1)):
        raise RuntimeError(f"selected Outline {revision_id} has invalid display indexes")
    return [dict(row) for row in rows]


def _path_chapters(bind, story_path_id: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in bind.execute(
            sa.text(
                """
                SELECT id, story_path_id, display_index,
                       predecessor_path_chapter_id, status, detached_at,
                       detached_by_outline_revision_id, lock_version, updated_at
                  FROM story_path_chapters
                 WHERE story_path_id = :story_path_id
                 ORDER BY display_index, id
                """
            ),
            {"story_path_id": story_path_id},
        ).mappings()
    ]


def _desired_states(
    *,
    selected_revision_id: str,
    topology: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    detached_at: datetime,
) -> dict[str, dict[str, Any]]:
    chapter_by_id = {str(chapter["id"]): chapter for chapter in chapters}
    selected_ids = [str(row["story_path_chapter_id"]) for row in topology]
    missing = set(selected_ids) - chapter_by_id.keys()
    if missing:
        missing_list = ", ".join(sorted(missing))
        raise RuntimeError(
            f"selected Outline {selected_revision_id} references PathChapters "
            f"outside its StoryPath: {missing_list}"
        )

    desired: dict[str, dict[str, Any]] = {}
    previous_id: str | None = None
    for row, chapter_id in zip(topology, selected_ids):
        desired[chapter_id] = {
            "display_index": int(row["display_index"]),
            "predecessor_path_chapter_id": previous_id,
            "status": "active",
            "detached_at": None,
            "detached_by_outline_revision_id": None,
        }
        previous_id = chapter_id

    for chapter in chapters:
        chapter_id = str(chapter["id"])
        if chapter_id in desired:
            continue
        if chapter["status"] == "active":
            desired[chapter_id] = {
                "display_index": int(chapter["display_index"]),
                "predecessor_path_chapter_id": None,
                "status": "detached",
                "detached_at": detached_at,
                "detached_by_outline_revision_id": selected_revision_id,
            }
        else:
            desired[chapter_id] = {
                "display_index": int(chapter["display_index"]),
                "predecessor_path_chapter_id": None,
                "status": "detached",
                "detached_at": chapter["detached_at"],
                "detached_by_outline_revision_id": chapter[
                    "detached_by_outline_revision_id"
                ],
            }
    return desired


def _state_tuple(state: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(state["display_index"]),
        state["predecessor_path_chapter_id"],
        state["status"],
        state["detached_at"],
        state["detached_by_outline_revision_id"],
    )


def _record_repair(bind, selected, changed: list[dict[str, Any]]) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_PATH_REPAIR_TABLE} (
                story_path_id, old_lock_version, old_updated_at
            ) VALUES (:story_path_id, :old_lock_version, :old_updated_at)
            """
        ),
        {
            "story_path_id": selected["story_path_id"],
            "old_lock_version": selected["lock_version"],
            "old_updated_at": selected["updated_at"],
        },
    )
    for chapter in changed:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_CHAPTER_REPAIR_TABLE} (
                    path_chapter_id, story_path_id, old_display_index,
                    old_predecessor_path_chapter_id, old_status,
                    old_detached_at, old_detached_by_outline_revision_id,
                    old_lock_version, old_updated_at
                ) VALUES (
                    :path_chapter_id, :story_path_id, :old_display_index,
                    :old_predecessor_path_chapter_id, :old_status,
                    :old_detached_at, :old_detached_by_outline_revision_id,
                    :old_lock_version, :old_updated_at
                )
                """
            ),
            {
                "path_chapter_id": chapter["id"],
                "story_path_id": chapter["story_path_id"],
                "old_display_index": chapter["display_index"],
                "old_predecessor_path_chapter_id": chapter[
                    "predecessor_path_chapter_id"
                ],
                "old_status": chapter["status"],
                "old_detached_at": chapter["detached_at"],
                "old_detached_by_outline_revision_id": chapter[
                    "detached_by_outline_revision_id"
                ],
                "old_lock_version": chapter["lock_version"],
                "old_updated_at": chapter["updated_at"],
            },
        )


def _write_chapter(
    bind,
    *,
    chapter_id: str,
    state: dict[str, Any],
    lock_version: int,
    updated_at: Any,
) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE story_path_chapters
               SET display_index = :display_index,
                   predecessor_path_chapter_id = :predecessor_path_chapter_id,
                   status = :status,
                   detached_at = :detached_at,
                   detached_by_outline_revision_id =
                       :detached_by_outline_revision_id,
                   lock_version = :lock_version,
                   updated_at = :updated_at
             WHERE id = :chapter_id
            """
        ),
        {
            "chapter_id": chapter_id,
            **state,
            "lock_version": lock_version,
            "updated_at": updated_at,
        },
    )


def _repair_path(bind, selected) -> None:
    if selected["revision_story_path_id"] != selected["story_path_id"]:
        raise RuntimeError(
            f"Outline Head for StoryPath {selected['story_path_id']} points outside "
            "its revision family"
        )
    topology = _outline_topology(bind, str(selected["current_revision_id"]))
    chapters = _path_chapters(bind, str(selected["story_path_id"]))
    repaired_at = datetime.now(timezone.utc)
    desired = _desired_states(
        selected_revision_id=str(selected["current_revision_id"]),
        topology=topology,
        chapters=chapters,
        detached_at=repaired_at,
    )
    changed = [
        chapter
        for chapter in chapters
        if _state_tuple(chapter) != _state_tuple(desired[str(chapter["id"])])
    ]
    if not changed:
        return

    _record_repair(bind, selected, changed)
    changed_ids = {str(chapter["id"]) for chapter in changed}
    selected_ids = {str(row["story_path_chapter_id"]) for row in topology}

    # Release active indexes owned by chapters that the selected Outline removed.
    for chapter in chapters:
        chapter_id = str(chapter["id"])
        if chapter_id in selected_ids or chapter["status"] != "active":
            continue
        state = desired[chapter_id]
        _write_chapter(
            bind,
            chapter_id=chapter_id,
            state=state,
            lock_version=int(chapter["lock_version"]) + 1,
            updated_at=repaired_at,
        )

    # Move active selected chapters out of the way before rebuilding their order.
    temporary_base = max(int(chapter["display_index"]) for chapter in chapters) + len(
        chapters
    ) + 1
    selected_chapters = [
        chapter for chapter in chapters if str(chapter["id"]) in selected_ids
    ]
    for offset, chapter in enumerate(sorted(selected_chapters, key=lambda row: row["id"])):
        if chapter["status"] != "active":
            continue
        bind.execute(
            sa.text(
                """
                UPDATE story_path_chapters
                   SET display_index = :display_index,
                       predecessor_path_chapter_id = NULL
                 WHERE id = :chapter_id
                """
            ),
            {
                "chapter_id": chapter["id"],
                "display_index": temporary_base + offset,
            },
        )

    chapter_by_id = {str(chapter["id"]): chapter for chapter in chapters}
    for row in topology:
        chapter_id = str(row["story_path_chapter_id"])
        chapter = chapter_by_id[chapter_id]
        is_changed = chapter_id in changed_ids
        _write_chapter(
            bind,
            chapter_id=chapter_id,
            state=desired[chapter_id],
            lock_version=int(chapter["lock_version"]) + int(is_changed),
            updated_at=repaired_at if is_changed else chapter["updated_at"],
        )

    # Existing detached rows can only need their stale predecessor cleared.
    for chapter in chapters:
        chapter_id = str(chapter["id"])
        if (
            chapter_id in selected_ids
            or chapter["status"] == "active"
            or chapter_id not in changed_ids
        ):
            continue
        _write_chapter(
            bind,
            chapter_id=chapter_id,
            state=desired[chapter_id],
            lock_version=int(chapter["lock_version"]) + 1,
            updated_at=repaired_at,
        )

    bind.execute(
        sa.text(
            """
            UPDATE story_paths
               SET lock_version = lock_version + 1,
                   updated_at = :updated_at
             WHERE id = :story_path_id
            """
        ),
        {"story_path_id": selected["story_path_id"], "updated_at": repaired_at},
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_required_schema(bind):
        return
    _create_repair_tables(bind)
    for selected in _selected_outlines(bind):
        _repair_path(bind, selected)


def _restore_path(bind, path_repair) -> None:
    current_path_lock = bind.execute(
        sa.text("SELECT lock_version FROM story_paths WHERE id = :story_path_id"),
        {"story_path_id": path_repair["story_path_id"]},
    ).scalar_one_or_none()
    if current_path_lock != int(path_repair["old_lock_version"]) + 1:
        raise RuntimeError(
            f"0046 cannot restore StoryPath {path_repair['story_path_id']} after "
            "new topology changes"
        )
    chapters = bind.execute(
        sa.text(
            f"""
            SELECT repair.*, chapter.status AS current_status,
                   chapter.display_index AS current_display_index
              FROM {_CHAPTER_REPAIR_TABLE} AS repair
              JOIN story_path_chapters AS chapter
                ON chapter.id = repair.path_chapter_id
             WHERE repair.story_path_id = :story_path_id
             ORDER BY repair.path_chapter_id
            """
        ),
        {"story_path_id": path_repair["story_path_id"]},
    ).mappings().all()
    expected_count = bind.execute(
        sa.text(
            f"SELECT COUNT(*) FROM {_CHAPTER_REPAIR_TABLE} "
            "WHERE story_path_id = :story_path_id"
        ),
        {"story_path_id": path_repair["story_path_id"]},
    ).scalar_one()
    if not chapters or len(chapters) != int(expected_count):
        raise RuntimeError(
            f"0046 repair audit is incomplete for StoryPath {path_repair['story_path_id']}"
        )

    maximum_index = bind.execute(
        sa.text(
            "SELECT COALESCE(MAX(display_index), 0) FROM story_path_chapters "
            "WHERE story_path_id = :story_path_id"
        ),
        {"story_path_id": path_repair["story_path_id"]},
    ).scalar_one()
    temporary_base = int(maximum_index) + len(chapters) + 1
    for offset, chapter in enumerate(chapters):
        if chapter["current_status"] != "active":
            continue
        bind.execute(
            sa.text(
                """
                UPDATE story_path_chapters
                   SET display_index = :display_index,
                       predecessor_path_chapter_id = NULL
                 WHERE id = :chapter_id
                """
            ),
            {
                "chapter_id": chapter["path_chapter_id"],
                "display_index": temporary_base + offset,
            },
        )

    for chapter in chapters:
        _write_chapter(
            bind,
            chapter_id=str(chapter["path_chapter_id"]),
            state={
                "display_index": int(chapter["old_display_index"]),
                "predecessor_path_chapter_id": chapter[
                    "old_predecessor_path_chapter_id"
                ],
                "status": chapter["old_status"],
                "detached_at": chapter["old_detached_at"],
                "detached_by_outline_revision_id": chapter[
                    "old_detached_by_outline_revision_id"
                ],
            },
            lock_version=int(chapter["old_lock_version"]),
            updated_at=chapter["old_updated_at"],
        )

    bind.execute(
        sa.text(
            """
            UPDATE story_paths
               SET lock_version = :lock_version,
                   updated_at = :updated_at
             WHERE id = :story_path_id
            """
        ),
        {
            "story_path_id": path_repair["story_path_id"],
            "lock_version": path_repair["old_lock_version"],
            "updated_at": path_repair["old_updated_at"],
        },
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_table(bind, _PATH_REPAIR_TABLE):
        return
    if not _has_table(bind, _CHAPTER_REPAIR_TABLE):
        raise RuntimeError("0046 PathChapter repair audit table is missing")
    paths = bind.execute(
        sa.text(f"SELECT * FROM {_PATH_REPAIR_TABLE} ORDER BY story_path_id DESC")
    ).mappings().all()
    for path_repair in paths:
        _restore_path(bind, path_repair)
    bind.execute(sa.text(f"DROP TABLE {_CHAPTER_REPAIR_TABLE}"))
    bind.execute(sa.text(f"DROP TABLE {_PATH_REPAIR_TABLE}"))
