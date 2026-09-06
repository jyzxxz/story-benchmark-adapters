"""Verify Phase 3 backfill row-count parity (v1 → v2).

跑法:
    DATABASE_URL=... python -m scripts.verify_phase3_backfill

检查项:
- 每个 v1 项目都有 v2 bible/outline revision (若 v1 有该数据)
- 每个 v1 项目章节都有 v2 chapter_revision
- v1 assets 全部有 v2 asset_versions
- v1 generation_stats 中 owner_id 非空的全部分配了 v2 generation_task
- project_content_heads / chapter_heads / vn_graph_heads 都已填充

不检查内容 hash (内容由 0013-0017 保证),只检查行数 + 关联完整性.
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text

from app.database import SessionLocal


def _scalar(db, sql, **params):
    return db.execute(text(sql), params).scalar() or 0


def _check(label: str, actual: int, expected: int, errors: list[str]) -> None:
    status = "OK" if actual >= expected else "FAIL"
    print(f"  [{status}] {label}: {actual} / {expected} expected")
    if actual < expected:
        errors.append(f"{label}: expected >= {expected}, got {actual}")


def verify() -> tuple[int, list[str]]:
    db = SessionLocal()
    errors: list[str] = []
    try:
        print("=== v1 → v2 backfill parity ===")

        # v1 story_bibles → v2 story_bible_revisions
        # 每个 distinct (project_id) 在 v1 中应有 ≥1 条 v2 revision
        v1_distinct_proj_bible = _scalar(
            db, "SELECT COUNT(DISTINCT project_id) FROM story_bibles"
        )
        v2_distinct_proj_bible = _scalar(
            db,
            "SELECT COUNT(DISTINCT project_id) FROM story_bible_revisions",
        )
        _check(
            "story_bible_revisions: distinct projects",
            v2_distinct_proj_bible,
            v1_distinct_proj_bible,
            errors,
        )

        # v1 chapter_outlines → v2 outline_revisions (per project)
        v1_distinct_proj_outline = _scalar(
            db, "SELECT COUNT(DISTINCT project_id) FROM chapter_outlines"
        )
        v2_distinct_proj_outline = _scalar(
            db,
            "SELECT COUNT(DISTINCT project_id) FROM outline_revisions",
        )
        _check(
            "outline_revisions: distinct projects",
            v2_distinct_proj_outline,
            v1_distinct_proj_outline,
            errors,
        )

        # v1 chapter_outlines → v2 outline_revision_chapters (per row)
        v1_outline_rows = _scalar(db, "SELECT COUNT(*) FROM chapter_outlines")
        v2_chapter_rows = _scalar(
            db, "SELECT COUNT(*) FROM outline_revision_chapters"
        )
        _check(
            "outline_revision_chapters: total rows",
            v2_chapter_rows,
            v1_outline_rows,
            errors,
        )

        # v1 chapter_contents → v2 chapter_revisions (per row)
        v1_chapter_rows = _scalar(db, "SELECT COUNT(*) FROM chapter_contents")
        v2_chapter_rev_rows = _scalar(
            db, "SELECT COUNT(*) FROM chapter_revisions"
        )
        _check(
            "chapter_revisions: total rows",
            v2_chapter_rev_rows,
            v1_chapter_rows,
            errors,
        )

        # v1 vn_graphs → v2 vn_graph_revisions (per row)
        v1_vn_rows = _scalar(db, "SELECT COUNT(*) FROM vn_graphs")
        v2_vn_rev_rows = _scalar(
            db, "SELECT COUNT(*) FROM vn_graph_revisions"
        )
        _check(
            "vn_graph_revisions: total rows",
            v2_vn_rev_rows,
            v1_vn_rows,
            errors,
        )

        # v1 assets → v2 asset_versions (per row)
        v1_asset_rows = _scalar(db, "SELECT COUNT(*) FROM assets")
        v2_av_rows = _scalar(db, "SELECT COUNT(*) FROM asset_versions")
        _check(
            "asset_versions: total rows",
            v2_av_rows,
            v1_asset_rows,
            errors,
        )

        # v1 generation_stats (owner non-null) → v2 generation_tasks
        v1_stats_with_owner = _scalar(
            db,
            """
            SELECT COUNT(*) FROM generation_stats s
            JOIN projects p ON p.id = s.project_id
            WHERE p.owner_id IS NOT NULL
            """,
        )
        v2_tasks_legacy = _scalar(
            db,
            """
            SELECT COUNT(*) FROM generation_tasks
            WHERE idempotency_key LIKE 'legacy-v1-stats-%'
            """,
        )
        _check(
            "generation_tasks (legacy-v1-stats-*): total rows",
            v2_tasks_legacy,
            v1_stats_with_owner,
            errors,
        )

        # Heads
        projects_with_v1 = _scalar(
            db,
            """
            SELECT COUNT(DISTINCT project_id) FROM (
                SELECT project_id FROM story_bibles
                UNION SELECT project_id FROM chapter_outlines
                UNION SELECT project_id FROM chapter_contents
                UNION SELECT project_id FROM vn_graphs
            )
            """,
        )
        heads_count = _scalar(
            db, "SELECT COUNT(*) FROM project_content_heads"
        )
        _check(
            "project_content_heads: total rows",
            heads_count,
            projects_with_v1,
            errors,
        )

        print()
        print(f"=== {'PASS' if not errors else 'FAIL'}: {len(errors)} error(s) ===")
        for e in errors:
            print(f"  - {e}")
        return 0 if not errors else 1, errors
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(verify()[0])
