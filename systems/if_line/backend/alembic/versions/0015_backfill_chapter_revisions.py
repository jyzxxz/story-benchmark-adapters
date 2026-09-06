"""Backfill chapter_revisions + chapter_segments + chapter_heads from v1 chapter_contents.

Revision ID: 0015_backfill_chapter_revisions
Revises: 0014_backfill_outline_revisions

把 v1 chapter_contents (一行 = 一章正文) 回填到 v2:
- chapter_revisions: 一行 v1 = 一行 v2 (revision_no=1 或 max+1)
- chapter_segments: 一行 v1 = 一行 v2 (整章作为单段,segment_key='legacy-v1-<id>')
- chapter_heads: 每个 (project_id, chapter_index) 一行,指向 revision

依赖: 0013 (bible) + 0014 (outline) — ChapterRevision.bible_revision_id +
      outline_revision_id 都 NOT NULL.

幂等性: legacy_source_table='chapter_contents' + legacy_source_id=<v1 id>.
       (chapter_revision_id, order_index) 唯一约束保证 segment 幂等.
       (project_id, chapter_index, revision_no) 唯一约束保证 revision 幂等.

downgrade: scratch 表 _alembic_backfill_0015 精确删除.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op


revision = "0015_backfill_chapter_revisions"
down_revision = "0014_backfill_outline_revisions"
branch_labels = None
depends_on = None


_LEGACY_TABLE = "chapter_contents"
_SCRATCH_TABLE = "_alembic_backfill_0015"


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _next_uuid(bind) -> str:
    if bind.dialect.name == "postgresql":
        row = bind.execute(sa.text("SELECT gen_random_uuid()::text")).first()
        return row[0]
    row = bind.execute(sa.text("SELECT lower(hex(randomblob(16)))")).first()
    raw = row[0]
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _table_exists(bind, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return _inspect(bind).has_table(name)


def _resolve_outline_chapter_revision(bind, project_id, chapter_index):
    """返回该 project.chapter_index 的 (bible_revision_id, outline_revision_id).

    若没找到 (项目既无 v2 bible 也无 v2 outline),返回 (None, None) 跳过该 row.
    """
    head = bind.execute(
        sa.text(
            "SELECT current_bible_revision_id, current_outline_revision_id FROM project_content_heads WHERE project_id = :pid"
        ),
        {"pid": project_id},
    ).first()
    if head and head[0] and head[1]:
        return head[0], head[1]
    # fallback: 项目最新 bible 和 outline
    bible = bind.execute(
        sa.text(
            "SELECT id FROM story_bible_revisions WHERE project_id = :pid ORDER BY revision_no DESC LIMIT 1"
        ),
        {"pid": project_id},
    ).first()
    outline = bind.execute(
        sa.text(
            "SELECT id FROM outline_revisions WHERE project_id = :pid ORDER BY revision_no DESC LIMIT 1"
        ),
        {"pid": project_id},
    ).first()
    return (bible[0] if bible else None, outline[0] if outline else None)


def upgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCRATCH_TABLE} (
                revision_id TEXT PRIMARY KEY
            )
            """
        )
    )

    if not _table_exists(bind, _LEGACY_TABLE):
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT c.id, c.project_id, c.chapter_index, c.content, c.version,
                   c.status, c.created_at, c.updated_at, p.owner_id
            FROM chapter_contents c
            JOIN projects p ON p.id = c.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM chapter_revisions r
                WHERE r.legacy_source_table = :table
                  AND r.legacy_source_id = CAST(c.id AS TEXT)
            )
            ORDER BY c.project_id, c.chapter_index, c.id
            """
        ),
        {"table": _LEGACY_TABLE},
    ).mappings().all()

    if not rows:
        return

    for row in rows:
        project_id = row["project_id"]
        chapter_index = row["chapter_index"]
        content = row["content"] or ""
        bible_id, outline_id = _resolve_outline_chapter_revision(bind, project_id, chapter_index)
        if not bible_id or not outline_id:
            # 项目还没 bible/outline revision,跳过 (前两个 migration 会先跑)
            continue

        content_digest = _hash(content)
        source_payload = {
            "legacy_table": _LEGACY_TABLE,
            "legacy_id": row["id"],
            "project_id": project_id,
            "chapter_index": chapter_index,
            "version": row["version"],
        }
        source_digest = _hash(source_payload)

        # 已有 v2 chapter_revision 内容匹配?
        existing_by_hash = bind.execute(
            sa.text(
                """
                SELECT id FROM chapter_revisions
                WHERE project_id = :pid AND chapter_index = :ci
                  AND content_hash = :ch
                """
            ),
            {"pid": project_id, "ci": chapter_index, "ch": content_digest},
        ).first()
        if existing_by_hash is not None:
            _ensure_segment(bind, existing_by_hash[0], row)
            bind.execute(
                sa.text(
                    """
                    UPDATE chapter_revisions
                    SET legacy_source_table = :table, legacy_source_id = :lid
                    WHERE id = :rid AND legacy_source_table IS NULL
                    """
                ),
                {
                    "table": _LEGACY_TABLE,
                    "lid": str(row["id"]),
                    "rid": existing_by_hash[0],
                },
            )
            _ensure_chapter_head(bind, project_id, chapter_index, existing_by_hash[0], row["updated_at"] or row["created_at"])
            continue

        # revision_no
        max_no = bind.execute(
            sa.text(
                """
                SELECT COALESCE(MAX(revision_no), 0)
                FROM chapter_revisions
                WHERE project_id = :pid AND chapter_index = :ci
                """
            ),
            {"pid": project_id, "ci": chapter_index},
        ).scalar() or 0
        revision_no = max_no + 1

        new_id = _next_uuid(bind)
        created_at = row["updated_at"] or row["created_at"]
        # v1 status: pending / generating / completed / failed
        # v2 status: generating / ready / archived (StoryBibleRevision 模型定义)
        v2_status = "ready" if row["status"] == "completed" else "generating"

        bind.execute(
            sa.text(
                """
                INSERT INTO chapter_revisions
                    (id, project_id, chapter_index, parent_revision_id,
                     bible_revision_id, outline_revision_id, state_snapshot_id,
                     revision_no, source_hash, content_hash, content, status,
                     generation_task_id, legacy_source_table, legacy_source_id,
                     created_by, created_at)
                VALUES
                    (:id, :pid, :ci, NULL,
                     :brid, :orid, NULL,
                     :rev_no, :source_hash, :content_hash, :content, :status,
                     NULL, :legacy_table, :legacy_id,
                     :created_by, :created_at)
                """
            ),
            {
                "id": new_id,
                "pid": project_id,
                "ci": chapter_index,
                "brid": bible_id,
                "orid": outline_id,
                "rev_no": revision_no,
                "source_hash": source_digest,
                "content_hash": content_digest,
                "content": content,
                "status": v2_status,
                "legacy_table": _LEGACY_TABLE,
                "legacy_id": str(row["id"]),
                "created_by": row["owner_id"],
                "created_at": created_at,
            },
        )

        _ensure_segment(bind, new_id, row)
        _ensure_chapter_head(bind, project_id, chapter_index, new_id, created_at)

        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_TABLE} (revision_id) VALUES (:rid)"),
            {"rid": new_id},
        )


def _ensure_segment(bind, revision_id: str, row) -> None:
    """为 chapter_revision 补 1 条 segment (整章作为单段).

    幂等: 通过 (chapter_revision_id, segment_key) 唯一约束.
    """
    segment_key = f"legacy-v1-{row['id']}"
    existing = bind.execute(
        sa.text(
            """
            SELECT id FROM chapter_segments
            WHERE chapter_revision_id = :rid AND segment_key = :key
            """
        ),
        {"rid": revision_id, "key": segment_key},
    ).first()
    if existing is not None:
        return
    bind.execute(
        sa.text(
            """
            INSERT INTO chapter_segments
                (id, chapter_revision_id, order_index, segment_key,
                 content, status, created_at)
            VALUES
                (:id, :rid, 0, :key, :content, 'ready', :ts)
            """
        ),
        {
            "id": _next_uuid(bind),
            "rid": revision_id,
            "key": segment_key,
            "content": row["content"] or "",
            "ts": row["updated_at"] or row["created_at"],
        },
    )


def _ensure_chapter_head(bind, project_id: int, chapter_index: int, revision_id: str, ts) -> None:
    """保证 chapter_heads 有 (project_id, chapter_index) 一行指向 revision_id."""
    existing = bind.execute(
        sa.text(
            """
            SELECT current_revision_id FROM chapter_heads
            WHERE project_id = :pid AND chapter_index = :ci
            """
        ),
        {"pid": project_id, "ci": chapter_index},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO chapter_heads
                    (project_id, chapter_index, current_revision_id,
                     lock_version, updated_at)
                VALUES
                    (:pid, :ci, :rid, 1, :ts)
                """
            ),
            {"pid": project_id, "ci": chapter_index, "rid": revision_id, "ts": ts},
        )
    # 不覆盖已有 head (v2 流程优先)


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, _SCRATCH_TABLE):
        return

    # 1) 解绑指向本次插入 revision 的 chapter_heads (整行删,因为 head 是为我们建的)
    bind.execute(
        sa.text(
            f"""
            DELETE FROM chapter_heads
            WHERE current_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    # 2) 删 segments (revision_id 关联)
    bind.execute(
        sa.text(
            f"""
            DELETE FROM chapter_segments
            WHERE chapter_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    # 3) 删 chapter_revisions
    bind.execute(
        sa.text(
            f"""
            DELETE FROM chapter_revisions
            WHERE id IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        )
    )
    # 4) marked rows 清标签
    bind.execute(
        sa.text(
            f"""
            UPDATE chapter_revisions
            SET legacy_source_table = NULL, legacy_source_id = NULL
            WHERE legacy_source_table = :table
              AND id NOT IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        ),
        {"table": _LEGACY_TABLE},
    )
    # 5) marked rows 的 segment 清掉我们打的 key (segment_key 以 'legacy-v1-' 开头)
    bind.execute(
        sa.text(
            """
            DELETE FROM chapter_segments
            WHERE segment_key LIKE 'legacy-v1-%'
              AND chapter_revision_id IN (
                SELECT id FROM chapter_revisions WHERE legacy_source_table IS NULL
              )
              AND chapter_revision_id NOT IN (
                SELECT id FROM chapter_revisions WHERE legacy_source_table = :table
              )
            """
        ),
        {"table": _LEGACY_TABLE},
    )
    # 6) drop scratch
    bind.execute(sa.text(f"DROP TABLE {_SCRATCH_TABLE}"))
