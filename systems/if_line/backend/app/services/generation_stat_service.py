"""
生成统计服务 - 记录和统计生成时间
"""
import time
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import Project, GenerationStat, Asset


class GenerationStatService:
    """生成统计服务"""

    def __init__(self, db: Session):
        self.db = db

    def start_generation(
        self,
        project_id: int,
        generation_type: str,
        chapter_index: Optional[int] = None,
        asset_type: Optional[str] = None,
        asset_id: Optional[int] = None,
        target_name: Optional[str] = None,
        variation_info: Optional[Dict[str, Any]] = None
    ) -> GenerationStat:
        """开始记录生成"""
        stat = GenerationStat(
            project_id=project_id,
            generation_type=generation_type,
            asset_type=asset_type,
            chapter_index=chapter_index,
            asset_id=asset_id,
            target_name=target_name,
            variation_info=variation_info,
            start_time=datetime.utcnow(),
            status="running"
        )
        self.db.add(stat)
        self.db.commit()
        self.db.refresh(stat)
        return stat

    def end_generation(
        self,
        stat_id: int,
        success: bool = True,
        error_message: Optional[str] = None
    ) -> GenerationStat:
        """结束记录生成"""
        stat = self.db.query(GenerationStat).filter(GenerationStat.id == stat_id).first()
        if not stat:
            return None

        stat.end_time = datetime.utcnow()
        stat.duration_seconds = (stat.end_time - stat.start_time).total_seconds()
        stat.status = "completed" if success else "failed"
        stat.error_message = error_message

        # 更新项目总时间
        project = self.db.query(Project).filter(Project.id == stat.project_id).first()
        if project:
            if stat.generation_type == "chapter":
                project.total_chapter_generate_time = (
                    project.total_chapter_generate_time or 0
                ) + stat.duration_seconds

                # 如果是第一章，记录首次生成时间
                if stat.chapter_index == 1 and success:
                    project.first_chapter_generated_at = stat.end_time

            elif stat.generation_type in ["portrait", "background", "keyframe", "asset"]:
                project.total_asset_generate_time = (
                    project.total_asset_generate_time or 0
                ) + stat.duration_seconds

        self.db.commit()
        self.db.refresh(stat)
        return stat

    def record_outline_approved(self, project_id: int):
        """记录大纲确认时间"""
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.outline_approved_at = datetime.utcnow()
            self.db.commit()

    def get_project_stats(self, project_id: int) -> dict:
        """获取项目统计信息"""
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return None

        stats = self.db.query(GenerationStat).filter(
            GenerationStat.project_id == project_id
        ).order_by(GenerationStat.start_time).all()

        # 计算首次章节生成时间（从大纲确认到第一章完成）
        first_chapter_duration = None
        if project.outline_approved_at and project.first_chapter_generated_at:
            first_chapter_duration = (
                project.first_chapter_generated_at - project.outline_approved_at
            ).total_seconds()

        chapter_stats = [s for s in stats if s.generation_type == "chapter"]
        portrait_stats = [s for s in stats if s.generation_type == "portrait"]
        background_stats = [s for s in stats if s.generation_type == "background"]
        keyframe_stats = [s for s in stats if s.generation_type == "keyframe"]

        return {
            "project_id": project_id,
            "project_title": project.title,
            "status": project.status,
            "outline_approved_at": project.outline_approved_at,
            "first_chapter_generated_at": project.first_chapter_generated_at,
            "first_chapter_duration": first_chapter_duration,
            # 章节统计
            "total_chapters_generated": len([s for s in chapter_stats if s.status == "completed"]),
            "total_chapter_time": project.total_chapter_generate_time or 0,
            # 立绘统计
            "total_portraits_generated": len([s for s in portrait_stats if s.status == "completed"]),
            "total_portrait_time": sum(s.duration_seconds or 0 for s in portrait_stats),
            "avg_portrait_time": self._calc_avg(portrait_stats),
            # 背景图统计
            "total_backgrounds_generated": len([s for s in background_stats if s.status == "completed"]),
            "total_background_time": sum(s.duration_seconds or 0 for s in background_stats),
            "avg_background_time": self._calc_avg(background_stats),
            # 关键帧统计
            "total_keyframes_generated": len([s for s in keyframe_stats if s.status == "completed"]),
            "total_keyframe_time": sum(s.duration_seconds or 0 for s in keyframe_stats),
            "avg_keyframe_time": self._calc_avg(keyframe_stats),
            # 总素材统计
            "total_assets_generated": len([s for s in stats if s.generation_type in ["portrait", "background", "keyframe", "asset"] and s.status == "completed"]),
            "total_asset_time": project.total_asset_generate_time or 0,
            # 详细记录
            "generation_stats": stats,
            "portrait_stats": portrait_stats,
            "background_stats": background_stats,
            "keyframe_stats": keyframe_stats
        }

    def _calc_avg(self, stats: list) -> float:
        """计算平均时间"""
        completed = [s for s in stats if s.status == "completed" and s.duration_seconds]
        if not completed:
            return 0
        return sum(s.duration_seconds for s in completed) / len(completed)

    def get_asset_time_breakdown(self, project_id: int) -> dict:
        """获取素材生成时间分解（按类型、变体等）"""
        # 立绘按情绪统计
        portrait_by_emotion = self.db.query(
            GenerationStat.variation_info["emotion"].label("emotion"),
            func.count(GenerationStat.id).label("count"),
            func.avg(GenerationStat.duration_seconds).label("avg_time"),
            func.sum(GenerationStat.duration_seconds).label("total_time")
        ).filter(
            GenerationStat.project_id == project_id,
            GenerationStat.generation_type == "portrait",
            GenerationStat.status == "completed"
        ).group_by("emotion").all()

        # 背景图按氛围统计
        background_by_mood = self.db.query(
            GenerationStat.variation_info["mood"].label("mood"),
            func.count(GenerationStat.id).label("count"),
            func.avg(GenerationStat.duration_seconds).label("avg_time"),
            func.sum(GenerationStat.duration_seconds).label("total_time")
        ).filter(
            GenerationStat.project_id == project_id,
            GenerationStat.generation_type == "background",
            GenerationStat.status == "completed"
        ).group_by("mood").all()

        # 背景图按章节统计
        background_by_chapter = self.db.query(
            GenerationStat.chapter_index,
            func.count(GenerationStat.id).label("count"),
            func.avg(GenerationStat.duration_seconds).label("avg_time"),
            func.sum(GenerationStat.duration_seconds).label("total_time")
        ).filter(
            GenerationStat.project_id == project_id,
            GenerationStat.generation_type == "background",
            GenerationStat.status == "completed"
        ).group_by(GenerationStat.chapter_index).all()

        return {
            "portrait_by_emotion": [
                {
                    "emotion": r.emotion,
                    "count": r.count,
                    "avg_time": r.avg_time,
                    "total_time": r.total_time
                } for r in portrait_by_emotion
            ],
            "background_by_mood": [
                {
                    "mood": r.mood,
                    "count": r.count,
                    "avg_time": r.avg_time,
                    "total_time": r.total_time
                } for r in background_by_mood
            ],
            "background_by_chapter": [
                {
                    "chapter_index": r.chapter_index,
                    "count": r.count,
                    "avg_time": r.avg_time,
                    "total_time": r.total_time
                } for r in background_by_chapter
            ]
        }

    def get_summary(self, project_id: int) -> dict:
        """获取统计摘要"""
        stats = self.get_project_stats(project_id)
        if not stats:
            return None

        chapter_count = stats["total_chapters_generated"]
        portrait_count = stats["total_portraits_generated"]
        background_count = stats["total_backgrounds_generated"]
        keyframe_count = stats["total_keyframes_generated"]

        return {
            "total_chapters": chapter_count,
            "total_chapter_time": stats["total_chapter_time"],
            "avg_chapter_time": stats["total_chapter_time"] / chapter_count if chapter_count > 0 else 0,
            "total_portraits": portrait_count,
            "total_portrait_time": stats["total_portrait_time"],
            "avg_portrait_time": stats["avg_portrait_time"],
            "total_backgrounds": background_count,
            "total_background_time": stats["total_background_time"],
            "avg_background_time": stats["avg_background_time"],
            "total_keyframes": keyframe_count,
            "total_keyframe_time": stats["total_keyframe_time"],
            "avg_keyframe_time": stats["avg_keyframe_time"],
            "first_chapter_time": stats["first_chapter_duration"]
        }