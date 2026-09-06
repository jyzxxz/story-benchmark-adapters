"""Backfill outline_revisions + outline_revision_chapters from v1 chapter_outlines.

Revision ID: 0014_backfill_outline_revisions
Revises: 0013_backfill_bible_revisions

把 v1 chapter_outlines 按 project_id 聚合成 v2 outline_revisions (每个 project 1 条
revision,revision_no=1 或 max+1),并把每章落到 outline_revision_chapters 子表.

依赖: 0013 必须先跑(OutlineRevision.bible_revision_id NOT NULL).

幂等性: 通过 legacy_source_table='chapter_outlines' + legacy_source_id=<v1 project_id>
       去重(注意 legacy_source_id 存的是 v1 project_id 而非 v1 row id,
       因为一个 outline_revisions 聚合了多个 v1 row).

downgrade: 通过 scratch 表 _alembic_backfill_0014 精确删除本次插入的 revision_id;
          marked rows 只清标签.

content_json: v2 OutlineRevision 没有 content_json 字段 (chapters 都在子表),
              所以 source_hash/content_hash 用聚合后的章节列 sha256 摘要.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op


revision = "0014_backfill_outline_revisions"
down_revision = "0013_backfill_bible_revisions"
branch_labels = None
depends_on = None


_LEGACY_TABLE = "chapter_outlines"
_SCRATCH_TABLE = "_alembic_backfill_0014"


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


def _chapter_payload(row) -> dict:
    """v2 outline_revision_chapters 的章节字段."""
    return {
        "chapter_index": row["chapter_index"],
        "title": row["title"],
        "summary": row["summary"],
        "conflict": row["conflict"],
        "characters": _load(row["characters"]) or [],
        "scene": row["scene"],
        "emotion": row["emotion"],
        "visual_keywords": _load(row["visual_keywords"]) or [],
    }


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

    # v1 源表可能不存在
    if not _table_exists(bind, _LEGACY_TABLE):
        return

    # 找所有 v1 project 还没被回填 outline 的
    projects = bind.execute(
        sa.text(
            """
            SELECT DISTINCT o.project_id, p.owner_id
            FROM chapter_outlines o
            JOIN projects p ON p.id = o.project_id
            WHERE NOT EXISTS (
                SELECT 1 FROM outline_revisions r
                WHERE r.project_id = o.project_id
                  AND r.legacy_source_table = :table
            )
            ORDER BY o.project_id
            """
        ),
        {"table": _LEGACY_TABLE},
    ).mappings().all()

    if not projects:
        return

    for p in projects:
        project_id = p["project_id"]
        owner_id = p["owner_id"]

        # 该项目的所有 v1 outline rows
        chapters = bind.execute(
            sa.text(
                """
                SELECT id, project_id, chapter_index, title, summary, conflict,
                       characters, scene, emotion, visual_keywords, status,
                       created_at, updated_at
                FROM chapter_outlines
                WHERE project_id = :pid
                ORDER BY chapter_index
                """
            ),
            {"pid": project_id},
        ).mappings().all()
        if not chapters:
            continue

        # 取项目当前 bible_revision_id 作为外键
        head = bind.execute(
            sa.text(
                "SELECT current_bible_revision_id FROM project_content_heads WHERE project_id = :pid"
            ),
            {"pid": project_id},
        ).first()
        bible_revision_id = head[0] if head and head[0] else None
        if not bible_revision_id:
            # fallback: 项目最新的 bible revision
            row = bind.execute(
                sa.text(
                    """
                    SELECT id FROM story_bible_revisions
                    WHERE project_id = :pid
                    ORDER BY revision_no DESC LIMIT 1
                    """
                ),
                {"pid": project_id},
            ).first()
            if row is None:
                # 项目既没 bible 也没 v2 bible revision,跳过(后续 0013 重跑会补)
                continue
            bible_revision_id = row[0]

        chapter_payloads = [_chapter_payload(c) for c in chapters]
        content_digest = _hash(chapter_payloads)
        source_payload = {
            "legacy_table": _LEGACY_TABLE,
            "project_id": project_id,
            "chapter_ids": [str(c["id"]) for c in chapters],
        }
        source_digest = _hash(source_payload)

        # 已存在相同 content 的 v2 outline revision?
        existing_by_hash = bind.execute(
            sa.text(
                """
                SELECT id FROM outline_revisions
                WHERE project_id = :pid AND content_hash = :ch
                """
            ),
            {"pid": project_id, "ch": content_digest},
        ).first()
        if existing_by_hash is not None:
            # 只补子表 + 标记
            _ensure_outline_chapters(bind, existing_by_hash[0], chapters)
            bind.execute(
                sa.text(
                    """
                    UPDATE outline_revisions
                    SET legacy_source_table = :table,
                        legacy_source_id = :lid
                    WHERE id = :rid AND legacy_source_table IS NULL
                    """
                ),
                {
                    "table": _LEGACY_TABLE,
                    "lid": str(project_id),
                    "rid": existing_by_hash[0],
                },
            )
            _ensure_head_points_outline(bind, project_id, existing_by_hash[0], chapters[0]["updated_at"] or chapters[0]["created_at"])
            continue

        # 计算 revision_no
        max_no = bind.execute(
            sa.text(
                "SELECT COALESCE(MAX(revision_no), 0) FROM outline_revisions WHERE project_id = :pid"
            ),
            {"pid": project_id},
        ).scalar() or 0
        revision_no = max_no + 1

        new_id = _next_uuid(bind)
        created_at = chapters[0]["updated_at"] or chapters[0]["created_at"]

        # 若 v1 全是 approved 状态,标记为 approved
        all_approved = all(c["status"] == "approved" for c in chapters)
        status = "approved" if all_approved else "draft"

        bind.execute(
            sa.text(
                """
                INSERT INTO outline_revisions
                    (id, project_id, bible_revision_id, parent_revision_id,
                     revision_no, source_hash, content_hash, status, approved_at,
                     generation_task_id, legacy_source_table, legacy_source_id,
                     created_by, created_at)
                VALUES
                    (:id, :pid, :brid, NULL,
                     :rev_no, :source_hash, :content_hash, :status, :approved_at,
                     NULL, :legacy_table, :legacy_id,
                     :created_by, :created_at)
                """
            ),
            {
                "id": new_id,
                "pid": project_id,
                "brid": bible_revision_id,
                "rev_no": revision_no,
                "source_hash": source_digest,
                "content_hash": content_digest,
                "status": status,
                "approved_at": created_at if status == "approved" else None,
                "legacy_table": _LEGACY_TABLE,
                "legacy_id": str(project_id),
                "created_by": owner_id,
                "created_at": created_at,
            },
        )

        _ensure_outline_chapters(bind, new_id, chapters)
        _ensure_head_points_outline(bind, project_id, new_id, created_at)

        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_TABLE} (revision_id) VALUES (:rid)"),
            {"rid": new_id},
        )


def _ensure_outline_chapters(bind, revision_id: str, chapters) -> None:
    """为该 outline_revision 补 outline_revision_chapters 子表行.

    幂等: 通过 (outline_revision_id, chapter_index) 唯一约束去重
          (uq_outline_revision_chapter).
    """
    for c in chapters:
        existing = bind.execute(
            sa.text(
                """
                SELECT id FROM outline_revision_chapters
                WHERE outline_revision_id = :rid AND chapter_index = :ci
                """
            ),
            {"rid": revision_id, "ci": c["chapter_index"]},
        ).first()
        if existing is not None:
            # 已存在(可能来自 v2 流程),只补 legacy_source_id 标签
            bind.execute(
                sa.text(
                    """
                    UPDATE outline_revision_chapters
                    SET legacy_source_id = :lid
                    WHERE id = :rid AND legacy_source_id IS NULL
                    """
                ),
                {"lid": str(c["id"]), "rid": existing[0]},
            )
            continue
        payload = _chapter_payload(c)
        ch_id = _next_uuid(bind)
        bind.execute(
            sa.text(
                """
                INSERT INTO outline_revision_chapters
                    (id, outline_revision_id, chapter_index, title, summary,
                     conflict, characters, scene, emotion, visual_keywords,
                     content_hash, legacy_source_id)
                VALUES
                    (:id, :rid, :ci, :title, :summary,
                     :conflict, :characters, :scene, :emotion, :vk,
                    :chash, :lid)
                """
            ),
            {
                "id": ch_id,
                "rid": revision_id,
                "ci": c["chapter_index"],
                "title": c["title"],
                "summary": c["summary"],
                "conflict": c["conflict"],
                "characters": json.dumps(payload["characters"], ensure_ascii=False),
                "scene": c["scene"],
                "emotion": c["emotion"],
                "vk": json.dumps(payload["visual_keywords"], ensure_ascii=False),
                "chash": _hash(payload),
                "lid": str(c["id"]),
            },
        )


def _ensure_head_points_outline(bind, project_id: int, revision_id: str, ts) -> None:
    """保证 project_content_heads.current_outline_revision_id 不为空."""
    existing = bind.execute(
        sa.text(
            "SELECT project_id, current_outline_revision_id FROM project_content_heads WHERE project_id = :pid"
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
                    (:pid, NULL, :rid, NULL, 'draft', 1, :ts)
                """
            ),
            {"pid": project_id, "rid": revision_id, "ts": ts},
        )
    elif not existing[1]:
        bind.execute(
            sa.text(
                "UPDATE project_content_heads SET current_outline_revision_id = :rid, updated_at = :ts WHERE project_id = :pid"
            ),
            {"pid": project_id, "rid": revision_id, "ts": ts},
        )


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, _SCRATCH_TABLE):
        return

    # 1) 解绑 head
    bind.execute(
        sa.text(
            f"""
            UPDATE project_content_heads
            SET current_outline_revision_id = NULL
            WHERE current_outline_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    # 2) 删 outline_revision_chapters 子表 (cascade 不一定生效,batch 模式下手动删)
    bind.execute(
        sa.text(
            f"""
            DELETE FROM outline_revision_chapters
            WHERE outline_revision_id IN (
                SELECT revision_id FROM {_SCRATCH_TABLE}
            )
            """
        )
    )
    # 3) 删 outline_revisions
    bind.execute(
        sa.text(
            f"""
            DELETE FROM outline_revisions
            WHERE id IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        )
    )
    # 4) marked rows 清标签
    bind.execute(
        sa.text(
            f"""
            UPDATE outline_revisions
            SET legacy_source_table = NULL, legacy_source_id = NULL
            WHERE legacy_source_table = :table
              AND id NOT IN (SELECT revision_id FROM {_SCRATCH_TABLE})
            """
        ),
        {"table": _LEGACY_TABLE},
    )
    # 5) marked rows 的子表 legacy_source_id 清掉 (我们打过标的)
    bind.execute(
        sa.text(
            """
            UPDATE outline_revision_chapters
            SET legacy_source_id = NULL
            WHERE legacy_source_id IS NOT NULL
              AND outline_revision_id IN (
                SELECT id FROM outline_revisions
                WHERE legacy_source_table IS NULL
              )
            """
        )
    )
    # 6) drop scratch
    bind.execute(sa.text(f"DROP TABLE {_SCRATCH_TABLE}"))
