"""Backfill exact Outline, CandidateSet, Script, and VNGraph relations.

Revision ID: 0037_backfill_immutable_artifact_relations
Revises: 0036_backfill_root_story_paths
"""
from __future__ import annotations

import hashlib
import json
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0037_backfill_immutable_artifact_relations"
down_revision = "0036_backfill_root_story_paths"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_ID_NAMESPACE = uuid.UUID("837be6e7-bbd8-4645-a060-59468827a1ba")
_CREATED_TABLE = "_alembic_0037_created"
_OUTLINE_TABLE = "_alembic_0037_outline_binding"
_OUTLINE_CHAPTER_TABLE = "_alembic_0037_outline_chapter_binding"
_OUTLINE_HEAD_TABLE = "_alembic_0037_outline_head_state"
_CANDIDATE_TABLE = "_alembic_0037_candidate_binding"
_EMPTY_STATE = {}
_EMPTY_STATE_HASH = hashlib.sha256(b"{}").hexdigest()


def _stable_id(kind: str, *parts: object) -> str:
    identity = ":".join(str(part) for part in parts)
    return str(uuid.uuid5(_ID_NAMESPACE, f"{kind}:{identity}"))


def _canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    payload = value if isinstance(value, str) else _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _table_exists(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _create_scratch_tables(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_CREATED_TABLE} (
                object_type VARCHAR(32) NOT NULL,
                object_id VARCHAR(255) NOT NULL,
                PRIMARY KEY (object_type, object_id)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_OUTLINE_TABLE} (
                revision_id VARCHAR(36) PRIMARY KEY,
                old_story_path_id VARCHAR(36)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_OUTLINE_CHAPTER_TABLE} (
                outline_chapter_id VARCHAR(36) PRIMARY KEY,
                old_path_chapter_id VARCHAR(36)
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_OUTLINE_HEAD_TABLE} (
                story_path_id VARCHAR(36) PRIMARY KEY,
                old_current_revision_id VARCHAR(36),
                old_lock_version INTEGER NOT NULL,
                old_updated_at TIMESTAMP
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_CANDIDATE_TABLE} (
                candidate_id VARCHAR(36) PRIMARY KEY,
                old_candidate_set_revision_id VARCHAR(36)
            )
            """
        )
    )


def _record_created(bind, object_type: str, object_id: str) -> None:
    exists = bind.execute(
        sa.text(
            f"SELECT 1 FROM {_CREATED_TABLE} "
            "WHERE object_type = :object_type AND object_id = :object_id"
        ),
        {"object_type": object_type, "object_id": object_id},
    ).first()
    if exists is None:
        bind.execute(
            sa.text(
                f"INSERT INTO {_CREATED_TABLE} (object_type, object_id) "
                "VALUES (:object_type, :object_id)"
            ),
            {"object_type": object_type, "object_id": object_id},
        )


def _record_outline(bind, revision_id: str, old_story_path_id: str | None) -> None:
    bind.execute(
        sa.text(
            f"INSERT INTO {_OUTLINE_TABLE} (revision_id, old_story_path_id) "
            "VALUES (:revision_id, :old_story_path_id)"
        ),
        {
            "revision_id": revision_id,
            "old_story_path_id": old_story_path_id,
        },
    )


def _record_outline_chapter(
    bind,
    outline_chapter_id: str,
    old_path_chapter_id: str | None,
) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_OUTLINE_CHAPTER_TABLE} (
                outline_chapter_id, old_path_chapter_id
            ) VALUES (:outline_chapter_id, :old_path_chapter_id)
            """
        ),
        {
            "outline_chapter_id": outline_chapter_id,
            "old_path_chapter_id": old_path_chapter_id,
        },
    )


def _record_outline_head(bind, row) -> None:
    exists = bind.execute(
        sa.text(
            f"SELECT 1 FROM {_OUTLINE_HEAD_TABLE} "
            "WHERE story_path_id = :story_path_id"
        ),
        {"story_path_id": row["story_path_id"]},
    ).first()
    if exists is None:
        bind.execute(
            sa.text(
                f"""
                INSERT INTO {_OUTLINE_HEAD_TABLE} (
                    story_path_id, old_current_revision_id,
                    old_lock_version, old_updated_at
                ) VALUES (
                    :story_path_id, :old_current_revision_id,
                    :old_lock_version, :old_updated_at
                )
                """
            ),
            {
                "story_path_id": row["story_path_id"],
                "old_current_revision_id": row["current_revision_id"],
                "old_lock_version": row["lock_version"],
                "old_updated_at": row["updated_at"],
            },
        )


def _record_candidate(bind, candidate_id: str, old_revision_id: str | None) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_CANDIDATE_TABLE} (
                candidate_id, old_candidate_set_revision_id
            ) VALUES (:candidate_id, :old_revision_id)
            """
        ),
        {"candidate_id": candidate_id, "old_revision_id": old_revision_id},
    )


def _root_path_id(bind, project_id: int) -> str:
    rows = bind.execute(
        sa.text(
            "SELECT id FROM story_paths "
            "WHERE project_id = :project_id AND parent_path_id IS NULL"
        ),
        {"project_id": project_id},
    ).all()
    if len(rows) != 1:
        raise RuntimeError(f"project {project_id} must have exactly one root StoryPath")
    return rows[0][0]


def _bind_outline_revisions(bind, project_id: int, root_id: str) -> None:
    outlines = bind.execute(
        sa.text(
            """
            SELECT id, story_path_id, revision_no, source_hash, content_hash
              FROM outline_revisions
             WHERE project_id = :project_id
             ORDER BY revision_no, created_at, id
            """
        ),
        {"project_id": project_id},
    ).mappings().all()
    for outline in outlines:
        if outline["story_path_id"] is not None:
            continue
        conflict = bind.execute(
            sa.text(
                """
                SELECT id
                  FROM outline_revisions
                 WHERE story_path_id = :root_id
                   AND id <> :revision_id
                   AND (
                       revision_no = :revision_no
                       OR (source_hash = :source_hash AND content_hash = :content_hash)
                   )
                """
            ),
            {
                "root_id": root_id,
                "revision_id": outline["id"],
                "revision_no": outline["revision_no"],
                "source_hash": outline["source_hash"],
                "content_hash": outline["content_hash"],
            },
        ).first()
        if conflict:
            raise RuntimeError(
                f"outline revision {outline['id']} conflicts with root revision {conflict[0]}"
            )
        _record_outline(bind, outline["id"], None)
        bind.execute(
            sa.text(
                "UPDATE outline_revisions SET story_path_id = :root_id "
                "WHERE id = :revision_id"
            ),
            {"root_id": root_id, "revision_id": outline["id"]},
        )

    root_outlines = bind.execute(
        sa.text(
            """
            SELECT id, parent_revision_id
              FROM outline_revisions
             WHERE story_path_id = :root_id
            """
        ),
        {"root_id": root_id},
    ).mappings().all()
    root_outline_ids = {row["id"] for row in root_outlines}
    for outline in root_outlines:
        parent_id = outline["parent_revision_id"]
        if parent_id and parent_id not in root_outline_ids:
            raise RuntimeError(f"outline revision {outline['id']} has a cross-path parent")


def _bind_outline_chapters(bind, root_id: str) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT chapter.id, chapter.story_path_chapter_id,
                   chapter.display_index, chapter.outline_revision_id
              FROM outline_revision_chapters AS chapter
              JOIN outline_revisions AS revision
                ON revision.id = chapter.outline_revision_id
             WHERE revision.story_path_id = :root_id
             ORDER BY chapter.outline_revision_id, chapter.display_index
            """
        ),
        {"root_id": root_id},
    ).mappings().all()
    for row in rows:
        placement = bind.execute(
            sa.text(
                """
                SELECT id, story_path_id, display_index
                  FROM story_path_chapters
                 WHERE story_path_id = :root_id
                   AND display_index = :display_index
                """
            ),
            {"root_id": root_id, "display_index": row["display_index"]},
        ).first()
        if placement is None:
            raise RuntimeError(
                f"outline chapter {row['id']} has no root PathChapter at its display index"
            )
        current_id = row["story_path_chapter_id"]
        if current_id is None:
            _record_outline_chapter(bind, row["id"], None)
            bind.execute(
                sa.text(
                    "UPDATE outline_revision_chapters "
                    "SET story_path_chapter_id = :path_chapter_id WHERE id = :id"
                ),
                {"path_chapter_id": placement[0], "id": row["id"]},
            )
        elif current_id != placement[0]:
            raise RuntimeError(
                f"outline chapter {row['id']} points outside its root placement"
            )


def _ensure_outline_head(bind, project_id: int, root_id: str) -> None:
    legacy = bind.execute(
        sa.text(
            """
            SELECT current_outline_revision_id, updated_at
              FROM project_content_heads
             WHERE project_id = :project_id
            """
        ),
        {"project_id": project_id},
    ).mappings().first()
    if not legacy or not legacy["current_outline_revision_id"]:
        return
    selected_id = legacy["current_outline_revision_id"]
    selected = bind.execute(
        sa.text(
            "SELECT 1 FROM outline_revisions "
            "WHERE id = :revision_id AND project_id = :project_id "
            "AND story_path_id = :root_id"
        ),
        {
            "revision_id": selected_id,
            "project_id": project_id,
            "root_id": root_id,
        },
    ).first()
    if selected is None:
        raise RuntimeError(f"project {project_id} Outline Head points outside its root")

    head = bind.execute(
        sa.text(
            """
            SELECT story_path_id, current_revision_id, lock_version, updated_at
              FROM story_path_outline_heads
             WHERE story_path_id = :root_id
            """
        ),
        {"root_id": root_id},
    ).mappings().first()
    if head is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO story_path_outline_heads (
                    story_path_id, current_revision_id, lock_version, updated_at
                ) VALUES (:root_id, :revision_id, 1, :updated_at)
                """
            ),
            {
                "root_id": root_id,
                "revision_id": selected_id,
                "updated_at": legacy["updated_at"],
            },
        )
        _record_created(bind, "outline_head", root_id)
        return
    if head["current_revision_id"] not in (None, selected_id):
        raise RuntimeError(f"project {project_id} has conflicting Outline Head pointers")
    if head["current_revision_id"] is None:
        _record_outline_head(bind, head)
        bind.execute(
            sa.text(
                "UPDATE story_path_outline_heads "
                "SET current_revision_id = :revision_id, updated_at = :updated_at "
                "WHERE story_path_id = :root_id"
            ),
            {
                "revision_id": selected_id,
                "updated_at": legacy["updated_at"],
                "root_id": root_id,
            },
        )


def _empty_state_snapshot(bind, project_id: int, created_at) -> str:
    existing = bind.execute(
        sa.text(
            "SELECT id FROM state_snapshots "
            "WHERE project_id = :project_id AND state_hash = :state_hash"
        ),
        {"project_id": project_id, "state_hash": _EMPTY_STATE_HASH},
    ).first()
    if existing:
        return existing[0]
    snapshot_id = _stable_id("legacy-empty-state", project_id)
    bind.execute(
        sa.text(
            """
            INSERT INTO state_snapshots (
                id, project_id, parent_snapshot_id,
                state_hash, state_json, created_at
            ) VALUES (
                :id, :project_id, NULL, :state_hash, :state_json, :created_at
            )
            """
        ),
        {
            "id": snapshot_id,
            "project_id": project_id,
            "state_hash": _EMPTY_STATE_HASH,
            "state_json": _canonical_json(_EMPTY_STATE),
            "created_at": created_at,
        },
    )
    _record_created(bind, "state_snapshot", snapshot_id)
    return snapshot_id


def _candidate_source(bind, project_id: int, root_id: str, checkpoint_id: str, created_at):
    checkpoint = bind.execute(
        sa.text(
            "SELECT project_id, content_revision_id FROM story_nodes WHERE id = :id"
        ),
        {"id": checkpoint_id},
    ).first()
    if not checkpoint or checkpoint[0] != project_id or not checkpoint[1]:
        raise RuntimeError(
            f"legacy checkpoint {checkpoint_id} has no exact ChapterRevision source"
        )
    chapter = bind.execute(
        sa.text(
            """
            SELECT project_id, created_for_story_path_id, state_snapshot_id
              FROM chapter_revisions
             WHERE id = :revision_id
            """
        ),
        {"revision_id": checkpoint[1]},
    ).first()
    if not chapter or chapter[0] != project_id or chapter[1] != root_id:
        raise RuntimeError(
            f"legacy checkpoint {checkpoint_id} ChapterRevision is outside the root path"
        )
    state_snapshot_id = chapter[2]
    if state_snapshot_id:
        snapshot_project = bind.execute(
            sa.text("SELECT project_id FROM state_snapshots WHERE id = :id"),
            {"id": state_snapshot_id},
        ).scalar()
        if snapshot_project != project_id:
            raise RuntimeError(
                f"legacy checkpoint {checkpoint_id} StateSnapshot belongs to another project"
            )
    else:
        state_snapshot_id = _empty_state_snapshot(bind, project_id, created_at)
    return checkpoint[1], state_snapshot_id


def _ensure_candidate_head(bind, root_id: str, checkpoint_id: str, updated_at) -> None:
    existing = bind.execute(
        sa.text(
            """
            SELECT current_revision_id
              FROM candidate_set_heads
             WHERE story_path_id = :root_id
               AND checkpoint_node_id = :checkpoint_id
            """
        ),
        {"root_id": root_id, "checkpoint_id": checkpoint_id},
    ).first()
    if existing:
        if existing[0]:
            valid = bind.execute(
                sa.text(
                    "SELECT 1 FROM candidate_set_revisions "
                    "WHERE id = :revision_id AND story_path_id = :root_id "
                    "AND checkpoint_node_id = :checkpoint_id"
                ),
                {
                    "revision_id": existing[0],
                    "root_id": root_id,
                    "checkpoint_id": checkpoint_id,
                },
            ).first()
            if valid is None:
                raise RuntimeError(f"CandidateSet Head at {checkpoint_id} is invalid")
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO candidate_set_heads (
                story_path_id, checkpoint_node_id,
                current_revision_id, lock_version, updated_at
            ) VALUES (
                :root_id, :checkpoint_id, NULL, 1, :updated_at
            )
            """
        ),
        {
            "root_id": root_id,
            "checkpoint_id": checkpoint_id,
            "updated_at": updated_at,
        },
    )
    _record_created(bind, "candidate_head", f"{root_id}:{checkpoint_id}")


def _backfill_candidate_sets(bind, project_id: int, owner_id: int | None, root_id: str) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT id, checkpoint_node_id, option_key, state_delta,
                   preview_node_id, preview_revision_id, candidate_status,
                   predicted_probability, created_at
              FROM branch_candidates
             WHERE project_id = :project_id
               AND candidate_set_revision_id IS NULL
             ORDER BY checkpoint_node_id, created_at, id
            """
        ),
        {"project_id": project_id},
    ).mappings().all()
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row["checkpoint_node_id"], []).append(row)

    for checkpoint_id, candidates in groups.items():
        if len(candidates) < 2 or len(candidates) > 4:
            raise RuntimeError(
                f"legacy checkpoint {checkpoint_id} has {len(candidates)} candidates; expected 2-4"
            )
        option_keys = [row["option_key"] for row in candidates]
        if len(option_keys) != len(set(option_keys)):
            raise RuntimeError(f"legacy checkpoint {checkpoint_id} repeats an option key")
        created_at = candidates[0]["created_at"]
        chapter_revision_id, state_snapshot_id = _candidate_source(
            bind,
            project_id,
            root_id,
            checkpoint_id,
            created_at,
        )
        source_manifest = {
            "bridge_version": "legacy-candidate-set-v1",
            "story_path_id": root_id,
            "checkpoint_node_id": checkpoint_id,
            "chapter_revision_id": chapter_revision_id,
            "state_snapshot_id": state_snapshot_id,
            "candidate_ids": [row["id"] for row in candidates],
            "option_keys": option_keys,
        }
        source_hash = _hash(source_manifest)
        set_id = _stable_id("legacy-candidate-set", project_id, checkpoint_id, source_hash)
        revision_no = int(
            bind.execute(
                sa.text(
                    "SELECT COALESCE(MAX(revision_no), 0) "
                    "FROM candidate_set_revisions "
                    "WHERE story_path_id = :root_id "
                    "AND checkpoint_node_id = :checkpoint_id"
                ),
                {"root_id": root_id, "checkpoint_id": checkpoint_id},
            ).scalar()
            or 0
        ) + 1
        bind.execute(
            sa.text(
                """
                INSERT INTO candidate_set_revisions (
                    id, project_id, story_path_id, checkpoint_node_id,
                    chapter_revision_id, state_snapshot_id, parent_revision_id,
                    revision_no, source_hash, candidate_count, instructions,
                    candidates_json, content_hash, generation_task_id,
                    created_by, created_at
                ) VALUES (
                    :id, :project_id, :story_path_id, :checkpoint_node_id,
                    :chapter_revision_id, :state_snapshot_id, NULL,
                    :revision_no, :source_hash, :candidate_count, :instructions,
                    NULL, NULL, NULL, :created_by, :created_at
                )
                """
            ),
            {
                "id": set_id,
                "project_id": project_id,
                "story_path_id": root_id,
                "checkpoint_node_id": checkpoint_id,
                "chapter_revision_id": chapter_revision_id,
                "state_snapshot_id": state_snapshot_id,
                "revision_no": revision_no,
                "source_hash": source_hash,
                "candidate_count": len(candidates),
                "instructions": "Legacy candidate bridge; ordered payload unavailable",
                "created_by": owner_id,
                "created_at": created_at,
            },
        )
        _record_created(bind, "candidate_set_revision", set_id)
        for candidate in candidates:
            _record_candidate(bind, candidate["id"], None)
            bind.execute(
                sa.text(
                    "UPDATE branch_candidates "
                    "SET candidate_set_revision_id = :revision_id WHERE id = :candidate_id"
                ),
                {"revision_id": set_id, "candidate_id": candidate["id"]},
            )
        _ensure_candidate_head(bind, root_id, checkpoint_id, created_at)


def _validate_script_revisions(bind) -> None:
    invalid = bind.execute(
        sa.text(
            """
            SELECT script.id
              FROM chapter_script_revisions AS script
              LEFT JOIN chapter_revisions AS chapter
                ON chapter.id = script.chapter_revision_id
             WHERE chapter.id IS NULL
                OR script.project_id <> chapter.project_id
                OR script.chapter_index <> chapter.chapter_index
                OR script.bible_revision_id <> chapter.bible_revision_id
                OR script.outline_revision_id <> chapter.outline_revision_id
             LIMIT 1
            """
        )
    ).first()
    if invalid:
        raise RuntimeError(f"ScriptRevision {invalid[0]} has inconsistent exact provenance")


def _backfill_script_heads(bind) -> None:
    chapters = bind.execute(
        sa.text(
            "SELECT id, project_id, chapter_index, created_at "
            "FROM chapter_revisions ORDER BY project_id, created_at, id"
        )
    ).mappings().all()
    for chapter in chapters:
        head = bind.execute(
            sa.text(
                """
                SELECT id, project_id, chapter_index, current_revision_id
                  FROM chapter_script_heads
                 WHERE chapter_revision_id = :chapter_revision_id
                """
            ),
            {"chapter_revision_id": chapter["id"]},
        ).mappings().first()
        if head is None:
            head_id = _stable_id("script-head", chapter["id"])
            bind.execute(
                sa.text(
                    """
                    INSERT INTO chapter_script_heads (
                        id, project_id, chapter_index, chapter_revision_id,
                        current_revision_id, lock_version, updated_at
                    ) VALUES (
                        :id, :project_id, :chapter_index, :chapter_revision_id,
                        NULL, 1, :updated_at
                    )
                    """
                ),
                {
                    "id": head_id,
                    "project_id": chapter["project_id"],
                    "chapter_index": chapter["chapter_index"],
                    "chapter_revision_id": chapter["id"],
                    "updated_at": chapter["created_at"],
                },
            )
            _record_created(bind, "script_head", head_id)
            continue
        if (
            head["project_id"] != chapter["project_id"]
            or head["chapter_index"] != chapter["chapter_index"]
        ):
            raise RuntimeError(f"Script Head {head['id']} has inconsistent provenance")
        if head["current_revision_id"]:
            selected_source = bind.execute(
                sa.text(
                    "SELECT chapter_revision_id FROM chapter_script_revisions "
                    "WHERE id = :revision_id"
                ),
                {"revision_id": head["current_revision_id"]},
            ).scalar()
            if selected_source != chapter["id"]:
                raise RuntimeError(f"Script Head {head['id']} points outside its ChapterRevision")


def _validate_vn_graph_revisions(bind) -> None:
    invalid = bind.execute(
        sa.text(
            """
            SELECT graph.id
              FROM vn_graph_revisions AS graph
              LEFT JOIN chapter_script_revisions AS script
                ON script.id = graph.script_revision_id
             WHERE script.id IS NULL
                OR graph.project_id <> script.project_id
                OR graph.chapter_index <> script.chapter_index
                OR graph.chapter_revision_id <> script.chapter_revision_id
             LIMIT 1
            """
        )
    ).first()
    if invalid:
        raise RuntimeError(f"VNGraphRevision {invalid[0]} has inconsistent exact provenance")


def _backfill_vn_graph_heads(bind) -> None:
    scripts = bind.execute(
        sa.text(
            "SELECT id, project_id, chapter_index, created_at "
            "FROM chapter_script_revisions ORDER BY project_id, created_at, id"
        )
    ).mappings().all()
    for script in scripts:
        head = bind.execute(
            sa.text(
                """
                SELECT id, project_id, chapter_index, current_revision_id
                  FROM vn_graph_heads
                 WHERE script_revision_id = :script_revision_id
                """
            ),
            {"script_revision_id": script["id"]},
        ).mappings().first()
        if head is None:
            head_id = _stable_id("vn-graph-head", script["id"])
            bind.execute(
                sa.text(
                    """
                    INSERT INTO vn_graph_heads (
                        id, project_id, chapter_index, script_revision_id,
                        current_revision_id, lock_version, updated_at
                    ) VALUES (
                        :id, :project_id, :chapter_index, :script_revision_id,
                        NULL, 1, :updated_at
                    )
                    """
                ),
                {
                    "id": head_id,
                    "project_id": script["project_id"],
                    "chapter_index": script["chapter_index"],
                    "script_revision_id": script["id"],
                    "updated_at": script["created_at"],
                },
            )
            _record_created(bind, "vn_graph_head", head_id)
            continue
        if (
            head["project_id"] != script["project_id"]
            or head["chapter_index"] != script["chapter_index"]
        ):
            raise RuntimeError(f"VNGraph Head {head['id']} has inconsistent provenance")
        if head["current_revision_id"]:
            selected_source = bind.execute(
                sa.text(
                    "SELECT script_revision_id FROM vn_graph_revisions "
                    "WHERE id = :revision_id"
                ),
                {"revision_id": head["current_revision_id"]},
            ).scalar()
            if selected_source != script["id"]:
                raise RuntimeError(f"VNGraph Head {head['id']} points outside its ScriptRevision")


def upgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, "projects"):
        return
    _create_scratch_tables(bind)
    projects = bind.execute(
        sa.text("SELECT id, owner_id FROM projects ORDER BY id")
    ).mappings().all()
    for project in projects:
        root_id = _root_path_id(bind, project["id"])
        _bind_outline_revisions(bind, project["id"], root_id)
        _bind_outline_chapters(bind, root_id)
        _ensure_outline_head(bind, project["id"], root_id)
        _backfill_candidate_sets(
            bind,
            project["id"],
            project["owner_id"],
            root_id,
        )
    _validate_script_revisions(bind)
    _backfill_script_heads(bind)
    _validate_vn_graph_revisions(bind)
    _backfill_vn_graph_heads(bind)


def _delete_created(bind, object_type: str, target_table: str, id_column: str = "id") -> None:
    bind.execute(
        sa.text(
            f"""
            DELETE FROM {target_table}
             WHERE {id_column} IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = :object_type
             )
            """
        ),
        {"object_type": object_type},
    )


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    scratch_tables = (
        _CREATED_TABLE,
        _OUTLINE_TABLE,
        _OUTLINE_CHAPTER_TABLE,
        _OUTLINE_HEAD_TABLE,
        _CANDIDATE_TABLE,
    )
    if not all(_table_exists(bind, table_name) for table_name in scratch_tables):
        return

    _delete_created(bind, "vn_graph_head", "vn_graph_heads")
    _delete_created(bind, "script_head", "chapter_script_heads")
    bind.execute(
        sa.text(
            f"""
            UPDATE branch_candidates
               SET candidate_set_revision_id = (
                       SELECT old_candidate_set_revision_id
                         FROM {_CANDIDATE_TABLE}
                        WHERE candidate_id = branch_candidates.id
                   )
             WHERE id IN (SELECT candidate_id FROM {_CANDIDATE_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM candidate_set_heads
             WHERE (story_path_id || ':' || checkpoint_node_id) IN (
                 SELECT object_id FROM {_CREATED_TABLE}
                  WHERE object_type = 'candidate_head'
             )
            """
        )
    )
    _delete_created(bind, "candidate_set_revision", "candidate_set_revisions")
    _delete_created(bind, "state_snapshot", "state_snapshots")
    bind.execute(
        sa.text(
            f"""
            UPDATE story_path_outline_heads
               SET current_revision_id = (
                       SELECT old_current_revision_id
                         FROM {_OUTLINE_HEAD_TABLE}
                        WHERE story_path_id = story_path_outline_heads.story_path_id
                   ),
                   lock_version = (
                       SELECT old_lock_version
                         FROM {_OUTLINE_HEAD_TABLE}
                        WHERE story_path_id = story_path_outline_heads.story_path_id
                   ),
                   updated_at = (
                       SELECT old_updated_at
                         FROM {_OUTLINE_HEAD_TABLE}
                        WHERE story_path_id = story_path_outline_heads.story_path_id
                   )
             WHERE story_path_id IN (SELECT story_path_id FROM {_OUTLINE_HEAD_TABLE})
            """
        )
    )
    _delete_created(
        bind,
        "outline_head",
        "story_path_outline_heads",
        id_column="story_path_id",
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE outline_revision_chapters
               SET story_path_chapter_id = (
                       SELECT old_path_chapter_id
                         FROM {_OUTLINE_CHAPTER_TABLE}
                        WHERE outline_chapter_id = outline_revision_chapters.id
                   )
             WHERE id IN (SELECT outline_chapter_id FROM {_OUTLINE_CHAPTER_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE outline_revisions
               SET story_path_id = (
                       SELECT old_story_path_id
                         FROM {_OUTLINE_TABLE}
                        WHERE revision_id = outline_revisions.id
                   )
             WHERE id IN (SELECT revision_id FROM {_OUTLINE_TABLE})
            """
        )
    )
    for table_name in (
        _CANDIDATE_TABLE,
        _OUTLINE_HEAD_TABLE,
        _OUTLINE_CHAPTER_TABLE,
        _OUTLINE_TABLE,
        _CREATED_TABLE,
    ):
        bind.execute(sa.text(f"DROP TABLE {table_name}"))
