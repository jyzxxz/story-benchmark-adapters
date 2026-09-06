"""Backfill story_bible_revisions from v1 story_bibles.

Revision ID: 0013_backfill_bible_revisions
Revises: 0012_project_source_work

把 v1 story_bibles 中尚未迁移的行回填到 v2 story_bible_revisions,
并把这些 revision 注册到 project_content_heads.current_bible_revision_id.

幂等性: 用 legacy_source_table='story_bibles' + legacy_source_id=<v1 id>
做去重. 多次跑只补漏.

设计:
- content_json 直接序列化 v1 的所有字段(worldview / characters / 等)
  保持与 v1 GenerationStatService 读取的 schema 一致
- source_hash / content_hash 用 sha256(canonical_json) 与 v2 service 对齐
- revision_no 取项目内已有 max + 1,避免破坏 uq_bible_revision_no
- status='complete' / created_by 取 Project.owner_id (v1 没有 created_by 概念)
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op


revision = "0013_backfill_bible_revisions"
down_revision = "0012_project_source_work"
branch_labels = None
depends_on = None


_LEGACY_TABLE = "story_bibles"
# 暂存本次 migration 插入的 v2 revision_id,供 downgrade 精确删除.
# marked(只更新 legacy_source_*)的不进 scratch,downgrade 时只清标签不删 row.
_SCRATCH_TABLE = "_alembic_backfill_0013"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _hash_value(value: object) -> str:
    if isinstance(value, str):
        return _sha256_hex(value)
    return _sha256_hex(_canonical_json(value))


def _next_uuid(bind) -> str:
    """生成 36 字符 UUID. SQLite 走 randomblob; PostgreSQL 走 gen_random_uuid."""
    if bind.dialect.name == "postgresql":
        row = bind.execute(sa.text("SELECT gen_random_uuid()::text")).first()
        return row[0]
    row = bind.execute(sa.text("SELECT lower(hex(randomblob(16)))")).first()
    raw = row[0]
    # 格式化为 8-4-4-4-12
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


def _table_exists(bind, name: str) -> bool:
    """跨方言的表存在性探测. 替代 sqlite_master 查询.

    PG 上 has_table() 直接命中 information_schema; SQLite 上 has_table() 同样
    走 PRAGMA table_info 的反射层, 因此比手写 sqlite_master 更通用.
    """
    from sqlalchemy import inspect as _inspect

    return _inspect(bind).has_table(name)


def _load_existing_json(raw):
    """v1 JSON 列可能存为 str / bytes / 已解析的 list/dict."""
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


def _bible_payload(row) -> dict:
    """把 v1 story_bibles 行序列化为 v2 content_json 字典.

    字段集与 app.services.prompt_builder_service / 前端 StoryBibleView 对齐.
    """
    return {
        "worldview": row["worldview"],
        "characters": _load_existing_json(row["characters"]) or [],
        "character_relations": row["character_relations"],
        "main_conflict": row["main_conflict"],
        "emotional_line": row["emotional_line"],
        "style_rules": row["style_rules"],
        "ending_constraints": row["ending_constraints"],
        "forbidden_points": _load_existing_json(row["forbidden_points"]) or [],
        "writing_notes": _load_existing_json(row["writing_notes"]) or {},
        "raw_json": _load_existing_json(row["raw_json"]),
    }


def upgrade() -> None:
    if op.get_context().as_sql:
        return  # 数据迁移需要真实 bind,离线 SQL 生成无意义

    bind = op.get_bind()
    # 建 scratch 表 (if not exists)
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCRATCH_TABLE} (
                revision_id TEXT PRIMARY KEY
            )
            """
        )
    )
    # v1 源表可能不存在 (test fixture 只建了部分表)
    if not _table_exists(bind, _LEGACY_TABLE):
        return
    # 找所有尚未迁移的 v1 story_bibles
    rows = bind.execute(
        sa.text(
            """
            SELECT s.id, s.project_id, s.worldview, s.characters,
                   s.character_relations, s.main_conflict, s.emotional_line,
                   s.style_rules, s.ending_constraints, s.forbidden_points,
                   s.writing_notes, s.raw_json, s.created_at, s.updated_at,
                   p.owner_id
            FROM story_bibles AS s
            JOIN projects AS p ON p.id = s.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM story_bible_revisions AS r
                WHERE r.legacy_source_table = :table
                  AND r.legacy_source_id = CAST(s.id AS TEXT)
            )
            ORDER BY s.project_id, s.id
            """
        ),
        {"table": _LEGACY_TABLE},
    ).mappings().all()

    if not rows:
        return

    for row in rows:
        payload = _bible_payload(row)
        content_digest = _hash_value(payload)
        source_payload = {
            "legacy_table": _LEGACY_TABLE,
            "legacy_id": row["id"],
            "project_id": row["project_id"],
        }
        source_digest = _hash_value(source_payload)

        # 已被 v2 流程预导入的情况:content_hash 已存在但 legacy_source_table=NULL.
        # 把已存在 row 的 legacy_source_table 打上标记 (跳过插入),让 idempotency 检查下次生效.
        existing_by_hash = bind.execute(
            sa.text(
                """
                SELECT id FROM story_bible_revisions
                WHERE project_id = :pid AND content_hash = :ch
                """
            ),
            {"pid": row["project_id"], "ch": content_digest},
        ).first()
        if existing_by_hash is not None:
            bind.execute(
                sa.text(
                    """
                    UPDATE story_bible_revisions
                    SET legacy_source_table = :table, legacy_source_id = :lid
                    WHERE id = :rid AND legacy_source_table IS NULL
                    """
                ),
                {"table": _LEGACY_TABLE, "lid": str(row["id"]), "rid": existing_by_hash[0]},
            )
            # 仍然确保 project_content_heads 指向它
            _ensure_head_points_bible(bind, row["project_id"], existing_by_hash[0], row["updated_at"] or row["created_at"])
            continue

        # 计算 revision_no: 项目内已有 max + 1
        max_no = bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(revision_no), 0) FROM story_bible_revisions WHERE project_id = :pid"
            ),
            {"pid": row["project_id"]},
        ).scalar() or 0
        revision_no = max_no + 1

        new_id = _next_uuid(bind)
        created_at = row["updated_at"] or row["created_at"]

        bind.execute(
            sa.text(
                """
                INSERT INTO story_bible_revisions
                    (id, project_id, parent_revision_id, revision_no,
                     source_hash, content_hash, content_json, status,
                     generation_task_id, legacy_source_table, legacy_source_id,
                     created_by, created_at)
                VALUES
                    (:id, :pid, NULL, :rev_no,
                     :source_hash, :content_hash, :content_json, 'complete',
                     NULL, :legacy_table, :legacy_id,
                     :created_by, :created_at)
                """
            ),
            {
                "id": new_id,
                "pid": row["project_id"],
                "rev_no": revision_no,
                "source_hash": source_digest,
                "content_hash": content_digest,
                "content_json": json.dumps(payload, ensure_ascii=False),
                "legacy_table": _LEGACY_TABLE,
                "legacy_id": str(row["id"]),
                "created_by": row["owner_id"],
                "created_at": created_at,
            },
        )

        _ensure_head_points_bible(bind, row["project_id"], new_id, created_at)
        # 记录到 scratch,downgrade 据此精确删除
        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_TABLE} (revision_id) VALUES (:rid)"),
            {"rid": new_id},
        )


def _ensure_head_points_bible(bind, project_id: int, revision_id: str, ts) -> None:
    """保证 project_content_heads.current_bible_revision_id 不为空且指向 revision_id.

    若 head 不存在:建一个;
    若 head 存在但 current_bible_revision_id 为空:填上;
    若 head 已指向其它 revision:不动 (v2 流程优先).
    """
    existing = bind.execute(
        sa.text(
            "SELECT project_id, current_bible_revision_id FROM project_content_heads WHERE project_id = :pid"
        ),
        {"pid": project_id},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO project_content_heads
                    (project_id, current_bible_revision_id,
                     current_outline_revision_id, published_release_id,
                     lifecycle_status, lock_version, updated_at)
                VALUES
                    (:pid, :rid, NULL, NULL, 'draft', 1, :ts)
                """
            ),
            {"pid": project_id, "rid": revision_id, "ts": ts},
        )
    elif not existing[1]:
        bind.execute(
            sa.text(
                "UPDATE project_content_heads SET current_bible_revision_id = :rid, updated_at = :ts WHERE project_id = :pid"
            ),
            {"pid": project_id, "rid": revision_id, "ts": ts},
        )


def downgrade() -> None:
    """精确回滚本次迁移插入的 v2 rows.

    用 _alembic_backfill_0013 scratch 表里记录的 revision_id 列表做精确删除;
    对于只更新了 legacy_source_* 标签的 marked rows,只清标签不删 row (因为
    那些是 v2 流程自产的,不该删).
    project_content_heads.current_bible_revision_id 若指向被删的 revision,置 NULL.
    """
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    # 检查 scratch 表存在 (可能没跑过 upgrade)
    if not _table_exists(bind, _SCRATCH_TABLE):
        return

    # 1) 把 head 指向本次插入的 revision 解绑
    bind.execute(
        sa.text(
            f"""
            UPDATE project_content_heads
            SET current_bible_revision_id = NULL
            WHERE current_bible_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    # 2) 删除本次插入的 revision
    bind.execute(
        sa.text(
            f"""
            DELETE FROM story_bible_revisions
            WHERE id IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        )
    )
    # 3) 对于 marked rows,清掉我们打的 legacy_source_* 标签 (恢复到升级前状态)
    bind.execute(
        sa.text(
            """
            UPDATE story_bible_revisions
            SET legacy_source_table = NULL, legacy_source_id = NULL
            WHERE legacy_source_table = :table
              AND id NOT IN (SELECT revision_id FROM __scratch_placeholder__)
            """.replace("__scratch_placeholder__", _SCRATCH_TABLE)
        ),
        {"table": _LEGACY_TABLE},
    )
    # 4) 删 scratch 表
    bind.execute(sa.text(f"DROP TABLE {_SCRATCH_TABLE}"))
