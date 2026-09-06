"""Eval 用的用户/项目 seed。造出"已知状态", 让指令可回答。

关键: seed 使用**固定 project_id** 并幂等清理旧数据。这样 cassette 回放时,
录制的工具参数(如 project_id)和当前 seed 完全一致, 跨进程 / 多次 run 都稳定。

注意: 工具层同时读 app.models(ChapterOutline/ChapterContent)和 app.models_v2
(ChapterHead/ChapterRevision)。M1/M2 只测只读 query 工具, 这些工具读旧表,
因此 seed 只需造旧表。后续 generate 类任务再补 v2 表。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import (
    Asset,
    ChapterContent,
    ChapterOutline,
    Project,
    StoryBible,
    User,
)


def ensure_user(db: Session, email: str = "eval@qq.com") -> int:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user.id
    user = User(
        email=email,
        password_hash="eval-not-a-real-hash",
        display_name="eval",
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user.id


def make_query_project_seed(fixed_id: int = 1001):
    """返回一个 seed 函数: 造一个含 bible + 1 章 outline + 1 章正文 + 1 素材的项目。"""

    def seed(db: Session, user_id: int) -> dict:
        # 幂等清理: 先删同 id 旧项目及其子表(依赖顺序: 子表先, 父表后)。
        for model in (ChapterContent, ChapterOutline, Asset, StoryBible):
            db.query(model).filter(model.project_id == fixed_id).delete()
        db.query(Project).filter(Project.id == fixed_id).delete()
        db.commit()

        project = Project(
            id=fixed_id,
            owner_id=user_id,
            title="测试项目",
            characters=["林夜", "阿澄"],
            story_start="开场" * 300,
            story_end="结局",
            style="轻科幻",
            pace="medium",
            extra_requirements="少一点旁白",
            status="reading",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(project)
        db.flush()

        db.add(StoryBible(
            project_id=project.id,
            worldview="城市上空有折叠海。",
            characters=[{"name": "林夜"}],
            main_conflict="主角必须找回丢失的信号。",
        ))
        db.add(ChapterOutline(
            project_id=project.id,
            chapter_index=1,
            title="第一章",
            summary="主角抵达码头。",
            conflict="信号中断。",
            characters=["林夜"],
            scene="旧码头",
            emotion="紧张",
            visual_keywords=["雨夜"],
            status="completed",
        ))
        db.add(ChapterContent(
            project_id=project.id,
            chapter_index=1,
            content="正文" * 400,
            version=1,
            status="completed",
        ))
        db.add(Asset(
            project_id=project.id,
            chapter_index=1,
            asset_type="background",
            target_name="旧码头",
            prompt="雨夜旧码头",
            status="completed",
            image_url="/static/assets/backgrounds/a.png",
        ))
        db.commit()
        return {"project_id": fixed_id}

    return seed
