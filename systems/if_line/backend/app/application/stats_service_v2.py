"""
v2 stats service:从 GenerationTask + ProviderUsageRecord 聚合项目统计。

替代 v1 GenerationStatService(v1 走 GenerationStat 单表,v2 没有).
返回结构与 v1 ProjectStatsResponse 兼容,便于前端无感切换.

关键映射:
- v1 generation_type ∈ {chapter, portrait, background, keyframe, asset}
  → v2 task.kind ∈ {chapter.generate, chapter.batch} + asset.render with
    parameters.asset_type ∈ {portrait, background, keyframe}
- v1 status='completed' → v2 status='succeeded'
- v1 duration_seconds → v2 (finished_at - started_at).total_seconds()
  (ProviderUsageRecord.latency_ms 更准但需要 join,这里先用减法,
  与 v1 行为对齐)
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Project
from app.models_v2 import (
    ChapterHead,
    ChapterRevision,
    GenerationTask,
    OutlineRevision,
    ProjectContentHead,
)


_ASSET_KIND = "asset.render"
_CHAPTER_KINDS = ("chapter.generate", "chapter.batch")
_SUCCESS = "succeeded"


def _safe_duration(task: GenerationTask) -> float:
    """finished - started in seconds, defensive against None."""
    if not task.started_at or not task.finished_at:
        return 0.0
    delta = task.finished_at - task.started_at
    return max(delta.total_seconds(), 0.0)


def _asset_tasks(db: Session, project_id: int) -> list[GenerationTask]:
    return (
        db.query(GenerationTask)
        .filter(
            GenerationTask.project_id == project_id,
            GenerationTask.kind == _ASSET_KIND,
            GenerationTask.status == _SUCCESS,
        )
        .all()
    )


def _chapter_tasks(db: Session, project_id: int) -> list[GenerationTask]:
    return (
        db.query(GenerationTask)
        .filter(
            GenerationTask.project_id == project_id,
            GenerationTask.kind.in_(_CHAPTER_KINDS),
            GenerationTask.status == _SUCCESS,
        )
        .all()
    )


def _asset_type_of(task: GenerationTask) -> str | None:
    params = task.parameters or {}
    return params.get("asset_type")


def _first_chapter_generated_at(db: Session, project_id: int) -> datetime | None:
    """Earliest succeeded chapter task's finished_at."""
    tasks = _chapter_tasks(db, project_id)
    finished = [t.finished_at for t in tasks if t.finished_at]
    return min(finished) if finished else None


def _outline_approved_at(db: Session, project_id: int) -> datetime | None:
    rev = (
        db.query(OutlineRevision)
        .filter(
            OutlineRevision.project_id == project_id,
            OutlineRevision.approved_at.isnot(None),
        )
        .order_by(OutlineRevision.approved_at.asc())
        .first()
    )
    return rev.approved_at if rev else None


def get_project_stats_v2(db: Session, project_id: int) -> dict[str, Any]:
    """Compute project stats from v2 tables. Returns dict matching v1 schema."""
    project = db.query(Project).filter(Project.id == project_id).first()
    project_title = project.title if project else ""
    project_status = project.status if project else "unknown"

    chapter_tasks = _chapter_tasks(db, project_id)
    asset_tasks = _asset_tasks(db, project_id)

    portraits = [t for t in asset_tasks if _asset_type_of(t) == "portrait"]
    backgrounds = [t for t in asset_tasks if _asset_type_of(t) == "background"]
    keyframes = [t for t in asset_tasks if _asset_type_of(t) == "keyframe"]

    chapter_durations = [_safe_duration(t) for t in chapter_tasks]
    portrait_durations = [_safe_duration(t) for t in portraits]
    background_durations = [_safe_duration(t) for t in backgrounds]
    keyframe_durations = [_safe_duration(t) for t in keyframes]

    total_chapter_time = sum(chapter_durations)
    total_portrait_time = sum(portrait_durations)
    total_background_time = sum(background_durations)
    total_keyframe_time = sum(keyframe_durations)
    total_asset_time = (
        total_portrait_time + total_background_time + total_keyframe_time
    )

    total_assets = len(portraits) + len(backgrounds) + len(keyframes)

    outline_at = _outline_approved_at(db, project_id)
    first_chapter_at = _first_chapter_generated_at(db, project_id)
    first_chapter_duration: float | None = None
    if outline_at and first_chapter_at:
        first_chapter_duration = max(
            (first_chapter_at - outline_at).total_seconds(), 0.0
        )

    # 已生成章节数:基于 ChapterHead(每章一条),不数 task(避免 chapter.batch
    # 父任务被重复计数)
    generated_chapters = (
        db.query(func.count(ChapterHead.chapter_index.distinct()))
        .filter(ChapterHead.project_id == project_id)
        .scalar()
        or 0
    )

    def _stat_rows(tasks: list[GenerationTask]) -> list[dict[str, Any]]:
        """Build per-task rows matching v1 GenerationStatResponse schema.

        v1 fields like id/asset_id/target_name/error_message don't have direct
        v2 equivalents, so we fill them with sensible defaults (id from task
        hash, asset_id None, error_message None). The frontend StatsView only
        reads a subset of these fields.
        """
        rows: list[dict[str, Any]] = []
        for t in tasks:
            params = t.parameters or {}
            render_spec = params.get("render_spec", {}) if isinstance(params.get("render_spec"), dict) else {}
            rows.append(
                {
                    "id": hash(t.id) & 0xFFFFFFFF,  # v1 expects int id, synthesize stable one
                    "project_id": project_id,
                    "generation_type": _asset_type_of(t) or t.kind,
                    "asset_type": _asset_type_of(t),
                    "chapter_index": (t.source_refs or {}).get("chapter_index"),
                    "asset_id": None,
                    "target_name": params.get("logical_key"),
                    "variation_info": render_spec,
                    "start_time": t.started_at or t.created_at or datetime.now(timezone.utc),
                    "end_time": t.finished_at,
                    "duration_seconds": _safe_duration(t),
                    "status": t.status,
                    "error_message": t.error_detail if t.status != _SUCCESS else None,
                }
            )
        rows.sort(key=lambda r: r["start_time"] or datetime.min.replace(tzinfo=timezone.utc))
        return rows

    return {
        "project_id": project_id,
        "project_title": project_title,
        "status": project_status,
        "outline_approved_at": outline_at,
        "first_chapter_generated_at": first_chapter_at,
        "first_chapter_duration": first_chapter_duration,
        "total_chapters_generated": generated_chapters,
        "total_chapter_time": total_chapter_time,
        "total_portraits_generated": len(portraits),
        "total_portrait_time": total_portrait_time,
        "avg_portrait_time": (
            total_portrait_time / len(portraits) if portraits else 0.0
        ),
        "total_backgrounds_generated": len(backgrounds),
        "total_background_time": total_background_time,
        "avg_background_time": (
            total_background_time / len(backgrounds) if backgrounds else 0.0
        ),
        "total_keyframes_generated": len(keyframes),
        "total_keyframe_time": total_keyframe_time,
        "avg_keyframe_time": (
            total_keyframe_time / len(keyframes) if keyframes else 0.0
        ),
        "total_assets_generated": total_assets,
        "total_asset_time": total_asset_time,
        "generation_stats": _stat_rows(chapter_tasks),
        "portrait_stats": _stat_rows(portraits),
        "background_stats": _stat_rows(backgrounds),
        "keyframe_stats": _stat_rows(keyframes),
    }


def get_asset_time_breakdown_v2(db: Session, project_id: int) -> dict[str, Any]:
    """Compute asset-time breakdown (by emotion / mood / chapter) from v2 tables.

    v1 used GenerationStat.variation_info JSON; v2 reads task.parameters.render_spec
    (emotion for portraits, mood for backgrounds).
    """
    asset_tasks = _asset_tasks(db, project_id)
    portraits = [t for t in asset_tasks if _asset_type_of(t) == "portrait"]
    backgrounds = [t for t in asset_tasks if _asset_type_of(t) == "background"]

    def _by_key(tasks: list[GenerationTask], key: str) -> list[dict[str, Any]]:
        buckets: dict[str, list[float]] = {}
        for t in tasks:
            value = (t.parameters or {}).get("render_spec", {}).get(key)
            if not value:
                value = "unknown"
            buckets.setdefault(str(value), []).append(_safe_duration(t))
        return [
            {
                "key": k,
                "count": len(times),
                "avg_time": sum(times) / len(times) if times else 0.0,
                "total_time": sum(times),
            }
            for k, times in sorted(buckets.items())
        ]

    def _by_chapter(tasks: list[GenerationTask]) -> list[dict[str, Any]]:
        buckets: dict[int | str, list[float]] = {}
        for t in tasks:
            ch = (t.source_refs or {}).get("chapter_index")
            buckets.setdefault(ch if ch is not None else "unknown", []).append(
                _safe_duration(t)
            )
        return [
            {
                "key": str(k),
                "count": len(times),
                "avg_time": sum(times) / len(times) if times else 0.0,
                "total_time": sum(times),
            }
            for k, times in sorted(
                buckets.items(), key=lambda kv: (kv[0] == "unknown", kv[0])
            )
        ]

    return {
        "portrait_by_emotion": _by_key(portraits, "emotion"),
        "background_by_mood": _by_key(backgrounds, "mood"),
        "background_by_chapter": _by_chapter(backgrounds),
    }
