"""Repair selected legacy Outlines to the current immutable contract.

Revision ID: 0043_repair_legacy_outline_contract
Revises: 0042_align_postgresql_schema
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0043_repair_legacy_outline_contract"
down_revision = "0042_align_postgresql_schema"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_REPAIR_TABLE = "_alembic_0043_outline_repair"
_REPAIR_SOURCE = "outline_contract_repair_v1"
_REQUIRED_TABLES = (
    "authoring_project_delete_scopes",
    "outline_revision_chapters",
    "outline_revisions",
    "project_content_heads",
    "story_path_chapters",
    "story_path_outline_heads",
)


def _content_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _repair_uuid(kind: str, source_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"if-line:{_REPAIR_SOURCE}:{kind}:{source_id}"))


def _has_table(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _has_required_tables(bind) -> bool:
    inspector = inspect(bind)
    return all(inspector.has_table(table_name) for table_name in _REQUIRED_TABLES)


def _create_repair_table(bind) -> None:
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if bind.dialect.name == "postgresql"
        else "TIMESTAMP"
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_REPAIR_TABLE} (
                story_path_id VARCHAR(36) PRIMARY KEY,
                project_id INTEGER NOT NULL,
                old_revision_id VARCHAR(36) NOT NULL,
                new_revision_id VARCHAR(36),
                old_story_lock_version INTEGER NOT NULL,
                old_story_updated_at {timestamp_type},
                project_head_matched INTEGER NOT NULL,
                old_project_lock_version INTEGER,
                old_project_updated_at {timestamp_type}
            )
            """
        )
    )


def _selected_legacy_outlines(bind):
    return bind.execute(
        sa.text(
            """
            SELECT head.story_path_id, head.current_revision_id,
                   head.lock_version AS story_lock_version,
                   head.updated_at AS story_updated_at,
                   revision.project_id, revision.bible_revision_id,
                   revision.revision_no, revision.status,
                   revision.approved_at, revision.created_by
              FROM story_path_outline_heads AS head
              JOIN outline_revisions AS revision
                ON revision.id = head.current_revision_id
             WHERE revision.status IN ('approved', 'draft')
             ORDER BY head.story_path_id
            """
        )
    ).mappings().all()


def _outline_items(bind, revision_id: str) -> list[dict[str, Any]]:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, story_path_chapter_id, display_index, title, summary,
                   conflict, characters, scene, emotion, visual_keywords
              FROM outline_revision_chapters
             WHERE outline_revision_id = :revision_id
             ORDER BY display_index, id
            """
        ),
        {"revision_id": revision_id},
    ).mappings().all()
    if not rows:
        raise RuntimeError(f"legacy Outline {revision_id} has no chapters")

    items: list[dict[str, Any]] = []
    for row in rows:
        if not row["story_path_chapter_id"]:
            raise RuntimeError(
                f"legacy Outline chapter {row['id']} has no PathChapter binding"
            )
        items.append(
            {
                "id": row["id"],
                "story_path_chapter_id": row["story_path_chapter_id"],
                "display_index": int(row["display_index"]),
                "title": row["title"] or "",
                "summary": row["summary"] or "",
                "conflict": row["conflict"],
                "characters": list(_json_value(row["characters"]) or []),
                "scene": row["scene"],
                "emotion": row["emotion"],
                "visual_keywords": list(
                    _json_value(row["visual_keywords"]) or []
                ),
            }
        )
    return items


def _project_head_state(bind, project_id: int, revision_id: str):
    return bind.execute(
        sa.text(
            """
            SELECT lock_version, updated_at
              FROM project_content_heads
             WHERE project_id = :project_id
               AND current_outline_revision_id = :revision_id
            """
        ),
        {"project_id": project_id, "revision_id": revision_id},
    ).mappings().first()


def _record_repair(bind, selected, project_head, new_revision_id: str | None) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_REPAIR_TABLE} (
                story_path_id, project_id, old_revision_id, new_revision_id,
                old_story_lock_version, old_story_updated_at,
                project_head_matched, old_project_lock_version,
                old_project_updated_at
            ) VALUES (
                :story_path_id, :project_id, :old_revision_id, :new_revision_id,
                :story_lock_version, :story_updated_at,
                :project_head_matched, :project_lock_version,
                :project_updated_at
            )
            """
        ),
        {
            "story_path_id": selected["story_path_id"],
            "project_id": selected["project_id"],
            "old_revision_id": selected["current_revision_id"],
            "new_revision_id": new_revision_id,
            "story_lock_version": selected["story_lock_version"],
            "story_updated_at": selected["story_updated_at"],
            "project_head_matched": int(project_head is not None),
            "project_lock_version": (
                project_head["lock_version"] if project_head else None
            ),
            "project_updated_at": project_head["updated_at"] if project_head else None,
        },
    )


def _create_repaired_revision(bind, selected) -> str:
    old_revision_id = selected["current_revision_id"]
    new_revision_id = _repair_uuid("revision", old_revision_id)
    items = _outline_items(bind, old_revision_id)
    modern_items = [
        {key: value for key, value in item.items() if key != "id"}
        for item in items
    ]
    next_revision_no = int(
        bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 "
                "FROM outline_revisions WHERE story_path_id = :story_path_id"
            ),
            {"story_path_id": selected["story_path_id"]},
        ).scalar_one()
    )
    source_hash = _content_hash(
        {
            "schema_version": _REPAIR_SOURCE,
            "story_path_id": selected["story_path_id"],
            "bible_revision_id": selected["bible_revision_id"],
            "legacy_revision_id": old_revision_id,
        }
    )
    created_at = datetime.now(timezone.utc)
    bind.execute(
        sa.text(
            """
            INSERT INTO outline_revisions (
                id, project_id, story_path_id, bible_revision_id,
                parent_revision_id, revision_no, source_hash, content_hash,
                status, approved_at, generation_task_id,
                legacy_source_table, legacy_source_id, created_by, created_at
            ) VALUES (
                :id, :project_id, :story_path_id, :bible_revision_id,
                :parent_revision_id, :revision_no, :source_hash, :content_hash,
                'ready', :approved_at, NULL,
                :legacy_source_table, :legacy_source_id, :created_by, :created_at
            )
            """
        ),
        {
            "id": new_revision_id,
            "project_id": selected["project_id"],
            "story_path_id": selected["story_path_id"],
            "bible_revision_id": selected["bible_revision_id"],
            "parent_revision_id": old_revision_id,
            "revision_no": next_revision_no,
            "source_hash": source_hash,
            "content_hash": _content_hash(modern_items),
            "approved_at": selected["approved_at"],
            "legacy_source_table": _REPAIR_SOURCE,
            "legacy_source_id": old_revision_id,
            "created_by": selected["created_by"],
            "created_at": created_at,
        },
    )

    for item in items:
        modern_item = {key: value for key, value in item.items() if key != "id"}
        bind.execute(
            sa.text(
                """
                INSERT INTO outline_revision_chapters (
                    id, outline_revision_id, chapter_index,
                    story_path_chapter_id, display_index, title, summary,
                    conflict, characters, scene, emotion, visual_keywords,
                    content_hash, legacy_source_id
                ) VALUES (
                    :id, :outline_revision_id, :chapter_index,
                    :story_path_chapter_id, :display_index, :title, :summary,
                    :conflict, :characters, :scene, :emotion, :visual_keywords,
                    :content_hash, :legacy_source_id
                )
                """
            ),
            {
                "id": _repair_uuid("chapter", item["id"]),
                "outline_revision_id": new_revision_id,
                "chapter_index": item["display_index"],
                "story_path_chapter_id": item["story_path_chapter_id"],
                "display_index": item["display_index"],
                "title": item["title"],
                "summary": item["summary"],
                "conflict": item["conflict"],
                "characters": json.dumps(item["characters"], ensure_ascii=False),
                "scene": item["scene"],
                "emotion": item["emotion"],
                "visual_keywords": json.dumps(
                    item["visual_keywords"], ensure_ascii=False
                ),
                "content_hash": _content_hash(modern_item),
                "legacy_source_id": item["id"],
            },
        )
    return new_revision_id


def _move_heads(bind, selected, replacement_id: str | None) -> None:
    bind.execute(
        sa.text(
            """
            UPDATE story_path_outline_heads
               SET current_revision_id = :replacement_id,
                   lock_version = lock_version + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE story_path_id = :story_path_id
               AND current_revision_id = :old_revision_id
            """
        ),
        {
            "replacement_id": replacement_id,
            "story_path_id": selected["story_path_id"],
            "old_revision_id": selected["current_revision_id"],
        },
    )
    bind.execute(
        sa.text(
            """
            UPDATE project_content_heads
               SET current_outline_revision_id = :replacement_id,
                   lock_version = lock_version + 1,
                   updated_at = CURRENT_TIMESTAMP
             WHERE project_id = :project_id
               AND current_outline_revision_id = :old_revision_id
            """
        ),
        {
            "replacement_id": replacement_id,
            "project_id": selected["project_id"],
            "old_revision_id": selected["current_revision_id"],
        },
    )


def _delete_repaired_revision(bind, *, project_id: int, revision_id: str) -> None:
    existing_scope = bind.execute(
        sa.text(
            "SELECT 1 FROM authoring_project_delete_scopes "
            "WHERE project_id = :project_id"
        ),
        {"project_id": project_id},
    ).first()
    created_scope = existing_scope is None
    if created_scope:
        bind.execute(
            sa.text(
                "INSERT INTO authoring_project_delete_scopes (project_id, created_at) "
                "VALUES (:project_id, CURRENT_TIMESTAMP)"
            ),
            {"project_id": project_id},
        )
    bind.execute(
        sa.text(
            "DELETE FROM outline_revision_chapters "
            "WHERE outline_revision_id = :revision_id"
        ),
        {"revision_id": revision_id},
    )
    bind.execute(
        sa.text("DELETE FROM outline_revisions WHERE id = :revision_id"),
        {"revision_id": revision_id},
    )
    if created_scope:
        bind.execute(
            sa.text(
                "DELETE FROM authoring_project_delete_scopes "
                "WHERE project_id = :project_id"
            ),
            {"project_id": project_id},
        )


def upgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_required_tables(bind):
        return
    _create_repair_table(bind)
    for selected in _selected_legacy_outlines(bind):
        project_head = _project_head_state(
            bind,
            selected["project_id"],
            selected["current_revision_id"],
        )
        replacement_id = (
            _create_repaired_revision(bind, selected)
            if selected["status"] == "approved"
            else None
        )
        _record_repair(bind, selected, project_head, replacement_id)
        _move_heads(bind, selected, replacement_id)


def downgrade() -> None:
    if op.get_context().as_sql:
        return
    bind = op.get_bind()
    if not _has_table(bind, _REPAIR_TABLE):
        return
    rows = bind.execute(
        sa.text(f"SELECT * FROM {_REPAIR_TABLE} ORDER BY story_path_id DESC")
    ).mappings().all()
    for row in rows:
        bind.execute(
            sa.text(
                """
                UPDATE story_path_outline_heads
                   SET current_revision_id = :old_revision_id,
                       lock_version = :lock_version,
                       updated_at = :updated_at
                 WHERE story_path_id = :story_path_id
                """
            ),
            {
                "old_revision_id": row["old_revision_id"],
                "lock_version": row["old_story_lock_version"],
                "updated_at": row["old_story_updated_at"],
                "story_path_id": row["story_path_id"],
            },
        )
        if row["project_head_matched"]:
            bind.execute(
                sa.text(
                    """
                    UPDATE project_content_heads
                       SET current_outline_revision_id = :old_revision_id,
                           lock_version = :lock_version,
                           updated_at = :updated_at
                     WHERE project_id = :project_id
                    """
                ),
                {
                    "old_revision_id": row["old_revision_id"],
                    "lock_version": row["old_project_lock_version"],
                    "updated_at": row["old_project_updated_at"],
                    "project_id": row["project_id"],
                },
            )
        if row["new_revision_id"]:
            _delete_repaired_revision(
                bind,
                project_id=row["project_id"],
                revision_id=row["new_revision_id"],
            )
    bind.execute(sa.text(f"DROP TABLE {_REPAIR_TABLE}"))
