"""Backfill vn_graph_revisions + vn_graph_heads from v1 vn_graphs.

Revision ID: 0016_backfill_vn_graph_revisions
Revises: 0015_backfill_chapter_revisions

把 v1 vn_graphs (一行 = 一章一个 graph_json) 回填到 v2:
- vn_graph_revisions: 一行 v1 = 一行 v2 (revision_no=1 或 max+1)
- vn_graph_heads: 每个 (project_id, chapter_index) 一行指向 revision

依赖: 0015 (vn_graph_revisions.chapter_revision_id NOT NULL).

策略 (per API_UNIFICATION_PLAN.md §3.4):
不调用 v2 compiler 重新编译 (compiler 可能在 v1 格式上失败,见 plan 风险点),
直接复制 graph_json,sentinel 版本号标记为 'legacy-v1-backfill'.
后续若需要 v2 schema 校验,可由前端触发 regenerate.

幂等性: legacy_source_table='vn_graphs' + legacy_source_id=<v1 id>.
downgrade: scratch 表精确删除.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op


revision = "0016_backfill_vn_graph_revisions"
down_revision = "0015_backfill_chapter_revisions"
branch_labels = None
depends_on = None


_LEGACY_TABLE = "vn_graphs"
_SCRATCH_TABLE = "_alembic_backfill_0016"

# Sentinel 版本号,标记 v1 backfill 来源 (不冒充 v2 compiler 产出)
_SCHEMA_VERSION = "legacy-v1"
_COMPILER_VERSION = "legacy-v1-backfill"
_TACHI_POLICY_VERSION = "legacy-v1"


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


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


def _resolve_chapter_revision(bind, project_id, chapter_index):
    """返回 (chapter_revision_id or None)."""
    head = bind.execute(
        sa.text(
            """
            SELECT current_revision_id FROM chapter_heads
            WHERE project_id = :pid AND chapter_index = :ci
            """
        ),
        {"pid": project_id, "ci": chapter_index},
    ).first()
    if head and head[0]:
        return head[0]
    # fallback: 项目该章最新 revision
    row = bind.execute(
        sa.text(
            """
            SELECT id FROM chapter_revisions
            WHERE project_id = :pid AND chapter_index = :ci
            ORDER BY revision_no DESC LIMIT 1
            """
        ),
        {"pid": project_id, "ci": chapter_index},
    ).first()
    return row[0] if row else None


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
            SELECT g.id, g.project_id, g.chapter_index, g.graph_json, g.status,
                   g.created_at, g.updated_at, p.owner_id
            FROM vn_graphs g
            JOIN projects p ON p.id = g.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM vn_graph_revisions r
                WHERE r.legacy_source_table = :table
                  AND r.legacy_source_id = CAST(g.id AS TEXT)
            )
            ORDER BY g.project_id, g.chapter_index, g.id
            """
        ),
        {"table": _LEGACY_TABLE},
    ).mappings().all()

    if not rows:
        return

    for row in rows:
        project_id = row["project_id"]
        chapter_index = row["chapter_index"]
        graph_data = _load(row["graph_json"])
        if graph_data is None:
            continue

        chapter_revision_id = _resolve_chapter_revision(bind, project_id, chapter_index)
        if not chapter_revision_id:
            # 章节没 v2 revision,跳过 (0015 会先跑)
            continue

        graph_json_str = (
            graph_data if isinstance(graph_data, str) else json.dumps(graph_data, ensure_ascii=False)
        )
        graph_digest = _hash(graph_data)
        source_manifest = {
            "legacy_table": _LEGACY_TABLE,
            "legacy_id": row["id"],
            "chapter_revision_id": chapter_revision_id,
        }
        source_digest = _hash(source_manifest)

        # 已有 v2 vn_graph_revisions 内容匹配?
        existing_by_hash = bind.execute(
            sa.text(
                """
                SELECT id FROM vn_graph_revisions
                WHERE project_id = :pid AND chapter_index = :ci
                  AND graph_hash = :gh
                """
            ),
            {"pid": project_id, "ci": chapter_index, "gh": graph_digest},
        ).first()
        if existing_by_hash is not None:
            bind.execute(
                sa.text(
                    """
                    UPDATE vn_graph_revisions
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
            _ensure_vn_graph_head(bind, project_id, chapter_index, existing_by_hash[0], row["updated_at"] or row["created_at"])
            continue

        # revision_no
        max_no = bind.execute(
            sa.text(
                """
                SELECT COALESCE(MAX(revision_no), 0)
                FROM vn_graph_revisions
                WHERE project_id = :pid AND chapter_index = :ci
                """
            ),
            {"pid": project_id, "ci": chapter_index},
        ).scalar() or 0
        revision_no = max_no + 1

        new_id = _next_uuid(bind)
        created_at = row["updated_at"] or row["created_at"]

        bind.execute(
            sa.text(
                """
                INSERT INTO vn_graph_revisions
                    (id, project_id, chapter_index, chapter_revision_id,
                     parent_revision_id, revision_no, source_manifest_hash,
                     graph_hash, graph_json, schema_version, compiler_version,
                     tachi_policy_version, status, generation_task_id,
                     legacy_source_table, legacy_source_id, created_at)
                VALUES
                    (:id, :pid, :ci, :crid,
                     NULL, :rev_no, :source_hash,
                     :graph_hash, :graph_json, :schema_ver, :compiler_ver,
                     :tachi_ver, 'complete', NULL,
                     :legacy_table, :legacy_id, :created_at)
                """
            ),
            {
                "id": new_id,
                "pid": project_id,
                "ci": chapter_index,
                "crid": chapter_revision_id,
                "rev_no": revision_no,
                "source_hash": source_digest,
                "graph_hash": graph_digest,
                "graph_json": graph_json_str,
                "schema_ver": _SCHEMA_VERSION,
                "compiler_ver": _COMPILER_VERSION,
                "tachi_ver": _TACHI_POLICY_VERSION,
                "legacy_table": _LEGACY_TABLE,
                "legacy_id": str(row["id"]),
                "created_at": created_at,
            },
        )

        _ensure_vn_graph_head(bind, project_id, chapter_index, new_id, created_at)
        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_TABLE} (revision_id) VALUES (:rid)"),
            {"rid": new_id},
        )


def _ensure_vn_graph_head(bind, project_id: int, chapter_index: int, revision_id: str, ts) -> None:
    existing = bind.execute(
        sa.text(
            """
            SELECT current_revision_id FROM vn_graph_heads
            WHERE project_id = :pid AND chapter_index = :ci
            """
        ),
        {"pid": project_id, "ci": chapter_index},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO vn_graph_heads
                    (project_id, chapter_index, current_revision_id,
                     lock_version, updated_at)
                VALUES
                    (:pid, :ci, :rid, 1, :ts)
                """
            ),
            {"pid": project_id, "ci": chapter_index, "rid": revision_id, "ts": ts},
        )


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, _SCRATCH_TABLE):
        return

    bind.execute(
        sa.text(
            f"""
            DELETE FROM vn_graph_heads
            WHERE current_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            DELETE FROM vn_graph_revisions
            WHERE id IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE vn_graph_revisions
            SET legacy_source_table = NULL, legacy_source_id = NULL
            WHERE legacy_source_table = :table
              AND id NOT IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        ),
        {"table": _LEGACY_TABLE},
    )
    bind.execute(sa.text(f"DROP TABLE {_SCRATCH_TABLE}"))
