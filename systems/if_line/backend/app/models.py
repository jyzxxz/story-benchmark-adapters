"""
数据库模型
"""
from sqlalchemy import BigInteger, Column, Integer, String, Text, JSON, DateTime, ForeignKey, Enum, Float, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime
from app.orm_base import Base
import enum


class ProjectStatus(str, enum.Enum):
    draft_input = "draft_input"
    bible_generated = "bible_generated"
    outline_generated = "outline_generated"
    outline_reviewing = "outline_reviewing"
    outline_approved = "outline_approved"
    chapter_generating = "chapter_generating"
    asset_generating = "asset_generating"
    vn_graph_generating = "vn_graph_generating"
    reading = "reading"
    pre_generating = "pre_generating"
    completed = "completed"


class User(Base):
    """账号用户"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    password_hash = Column(String(512), nullable=False)
    display_name = Column(String(100), nullable=False)
    avatar_url = Column(String(500))
    is_active = Column(Boolean, nullable=False, default=True)

    quota_total = Column(Integer, nullable=False, default=1000)
    quota_daily = Column(Integer, nullable=False, default=100)
    quota_used_total = Column(Integer, nullable=False, default=0)
    quota_used_daily = Column(Integer, nullable=False, default=0)
    quota_reset_at = Column(DateTime)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    project_likes = relationship("ProjectLike", back_populates="user", cascade="all, delete-orphan")
    project_comments = relationship("ProjectComment", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship(
        "UserNotification",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserNotification.user_id",
    )


class UserSession(Base):
    """服务端维护的登录会话；cookie 内只保存原始 token，DB 内保存 token hash。"""
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_token_hash = Column(String(128), nullable=False, unique=True, index=True)
    device_id = Column(String(128))
    user_agent = Column(String(500))
    ip_address = Column(String(64))
    expires_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow)
    revoked_at = Column(DateTime)

    user = relationship("User", back_populates="sessions")


class Project(Base):
    """小说项目"""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(200), nullable=False)
    characters = Column(JSON)  # 核心人物列表
    story_start = Column(Text, nullable=False)
    story_end = Column(Text, nullable=False)
    style = Column(String(100))
    source_work = Column(String(200))  # 二创原作；原创项目留空
    pace = Column(String(50))  # fast / medium / slow
    extra_requirements = Column(Text)
    status = Column(String(50), default=ProjectStatus.draft_input.value)
    visibility = Column(String(20), nullable=False, default="private")  # private / public
    is_draft = Column(Boolean, nullable=False, default=True)
    published_at = Column(DateTime)
    deleted_at = Column(DateTime(timezone=True), index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 时间统计字段
    outline_approved_at = Column(DateTime)  # 大纲确认时间
    first_chapter_generated_at = Column(DateTime)  # 第一章生成完成时间
    total_chapter_generate_time = Column(Float, default=0.0)  # 章节生成总耗时（秒）
    total_asset_generate_time = Column(Float, default=0.0)  # 素材生成总耗时（秒）

    # 关系
    owner = relationship("User")
    story_bible = relationship("StoryBible", back_populates="project", uselist=False)
    chapter_outlines = relationship("ChapterOutline", back_populates="project", cascade="all, delete-orphan")
    chapter_contents = relationship("ChapterContent", back_populates="project", cascade="all, delete-orphan")
    assets = relationship("Asset", back_populates="project", cascade="all, delete-orphan")
    vn_graphs = relationship("VNGraph", back_populates="project", cascade="all, delete-orphan")
    workflow_runs = relationship("WorkflowRun", back_populates="project", cascade="all, delete-orphan")
    generation_stats = relationship("GenerationStat", back_populates="project", cascade="all, delete-orphan")
    likes = relationship("ProjectLike", back_populates="project", cascade="all, delete-orphan")
    comments = relationship("ProjectComment", back_populates="project", cascade="all, delete-orphan")


class StoryBible(Base):
    """故事总设定"""
    __tablename__ = "story_bibles"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    worldview = Column(Text)
    characters = Column(JSON)  # 人物设定列表
    character_relations = Column(Text)
    main_conflict = Column(Text)
    emotional_line = Column(Text)
    style_rules = Column(Text)
    ending_constraints = Column(Text)
    forbidden_points = Column(JSON)
    writing_notes = Column(JSON)
    raw_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="story_bible")


class ChapterOutline(Base):
    """章节大纲"""
    __tablename__ = "chapter_outlines"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    title = Column(String(200))
    summary = Column(Text)
    conflict = Column(Text)
    characters = Column(JSON)  # 出场人物列表
    scene = Column(String(200))
    emotion = Column(String(100))
    visual_keywords = Column(JSON)
    status = Column(String(50), default="pending")  # pending / approved / generating / completed
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="chapter_outlines")


class ChapterContent(Base):
    """章节正文"""
    __tablename__ = "chapter_contents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    content = Column(Text)
    version = Column(Integer, default=1)
    status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="chapter_contents")


class Asset(Base):
    """视觉素材"""
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chapter_index = Column(Integer)
    asset_type = Column(String(50))  # portrait / background / keyframe
    target_name = Column(String(200))  # 角色名或场景名
    prompt = Column(Text)
    image_url = Column(String(500))
    status = Column(String(50), default="pending")  # pending / generating / completed / failed
    # V2 authoritative logical identity. Legacy rows are backfilled with
    # ``legacy:{id}``; new writes never derive identity from mutable prompts.
    logical_key = Column(String(255))
    taxonomy_json = Column(JSON, nullable=False, default=dict)
    archived_at = Column(DateTime)

    # 立绘专用字段
    character_id = Column(String(100))  # 角色唯一标识
    emotion = Column(String(50))        # 情绪变体
    outfit = Column(String(100))        # 装束变体
    pose = Column(String(100))          # 姿态变体

    # 背景图专用字段
    scene_location = Column(String(200))  # 场景地点
    mood = Column(String(50))             # 氛围：day/night/dusk/dawn/rain/snow/fog

    # 关键帧专用字段
    event_name = Column(String(200))  # 事件名称

    # 生成参数
    seed = Column(BigInteger)                # 生成种子（一致性保证）
    generation_params = Column(JSON)      # 完整生成参数 JSON
    generation_time = Column(Float)       # 生成耗时（秒）
    processed = Column(Integer, default=0)  # 是否已后处理（0/1）

    # 画风相关
    genre = Column(String(50))            # 题材类型: historical/modern/sci-fi/fantasy/anime/realistic/oil_painting/watercolor/sketch
    description_cn = Column(Text)         # 中文描述

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="assets")

    __table_args__ = (
        UniqueConstraint(
            "project_id", "asset_type", "logical_key", name="uq_project_asset_logical_key"
        ),
    )


class VNGraph(Base):
    """VNGraph JSON"""
    __tablename__ = "vn_graphs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    graph_json = Column(JSON)
    status = Column(String(50), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="vn_graphs")


class WorkflowRun(Base):
    """工作流运行记录"""
    __tablename__ = "workflow_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    current_step = Column(String(100))
    input_json = Column(JSON)
    output_json = Column(JSON)
    status = Column(String(50))  # running / completed / failed
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="workflow_runs")


class GenerationStat(Base):
    """生成统计记录"""
    __tablename__ = "generation_stats"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    generation_type = Column(String(50))  # chapter / portrait / background / keyframe
    asset_type = Column(String(50))       # portrait / background / keyframe (用于素材细分)
    chapter_index = Column(Integer)
    asset_id = Column(Integer)            # 关联的素材 ID
    target_name = Column(String(200))     # 角色名/场景名（用于显示）
    variation_info = Column(JSON)         # 变体信息 {"emotion": "happy", "outfit": "armor", "mood": "day"}
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime)
    duration_seconds = Column(Float)
    status = Column(String(50), default="running")  # running / completed / failed
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关系
    project = relationship("Project", back_populates="generation_stats")


class ProjectLike(Base):
    """用户对公开/可读作品的点赞。"""
    __tablename__ = "project_likes"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="likes")
    user = relationship("User", back_populates="project_likes")

    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_like_user"),
    )


class ProjectComment(Base):
    """用户对作品的评论。"""
    __tablename__ = "project_comments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    body = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="comments")
    user = relationship("User", back_populates="project_comments")


class UserNotification(Base):
    """面向用户客户端轮询的持久化通知。"""
    __tablename__ = "user_notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    comment_id = Column(Integer, ForeignKey("project_comments.id"), nullable=True, index=True)
    notification_type = Column(String(50), nullable=False)
    title = Column(String(200), nullable=False)
    body = Column(Text)
    payload = Column(JSON)
    read_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="notifications", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])
    project = relationship("Project")
    comment = relationship("ProjectComment")


class CharacterVoiceBinding(Base):
    """项目级角色音色绑定。

    ``character_id`` 在项目内永久绑一组 ``(vcn, speed, pitch, volume, emotion)``，
    跨章节复用。``uq_pj_voice_slot`` 在 DB 层兜底"参数差异化独占"：同项目内
    不允许两条 binding 共享完全相同的 ``(vcn, speed, pitch)`` 三元组——这样
    共用 vcn 的两个角色靠参数差异化区分。``volume`` 不进约束（仅作音量微调）。

    character_id 算法：``md5(name|project_id)[:12]``，与
    ``prompt_builder_service.generate_character_id`` 一致。
    """
    __tablename__ = "character_voice_bindings"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    character_id = Column(String(100), nullable=False)
    character_name = Column(String(200))
    vcn = Column(String(100), nullable=False)
    speed = Column(Integer, nullable=False, default=50)
    pitch = Column(Integer, nullable=False, default=50)
    volume = Column(Integer, nullable=False, default=50)
    emotion = Column(String(50), nullable=False, default="calm")
    # MiniMax fallback 侧的独立绑定（lazy 补全，老 binding 为 NULL）
    # voice_id 是 MiniMax 自有 ID（如 male-qn-qingse）；speed/pitch/volume
    # 用 MiniMax 的 [0,100] 标度，与主引擎 aliyun 标度口径一致但语义独立。
    minimax_voice_id = Column(String(120), nullable=True)
    minimax_speed = Column(Integer, nullable=True)
    minimax_pitch = Column(Integer, nullable=True)
    minimax_volume = Column(Integer, nullable=True)
    bound_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "character_id", name="uq_pj_char"),
        UniqueConstraint("project_id", "vcn", "speed", "pitch", name="uq_pj_voice_slot"),
    )


# Register the additive v2 tables in the same SQLAlchemy metadata.  Keeping the
# import at the end avoids circular imports while preserving legacy imports of
# ``app.models`` throughout the application and tests.
from app import models_v2 as _models_v2  # noqa: E402,F401
