"""Additive v2 persistence models.

These tables deliberately coexist with the legacy production tables.  They are
used by the durable task, immutable revision, artifact, branching and release
APIs.  No legacy row is overwritten or deleted when a v2 object is created.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    BigInteger,
    JSON,
    Numeric,
    select,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect as sa_inspect,
    text,
)

from app.orm_base import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def source_hash_default(context) -> str:
    """Bridge legacy writers until all generation services send context hashes."""

    return str(context.get_current_parameters().get("source_hash") or "")


def binding_manifest_default(context) -> dict[str, str]:
    params = context.get_current_parameters()
    return {
        "manifest_version": "legacy-writer-v1",
        "script_revision_id": str(params.get("script_revision_id") or ""),
        "legacy_source_manifest_hash": str(params.get("source_manifest_hash") or ""),
        "graph_hash": str(params.get("graph_hash") or ""),
    }


def binding_manifest_hash_default(context) -> str:
    manifest = context.get_current_parameters().get("binding_manifest") or {}
    payload = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class StorageObject(Base):
    __tablename__ = "storage_objects"

    id = Column(String(36), primary_key=True, default=new_uuid)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # Keep the media tombstone after a project is deleted so the deferred GC
    # still knows which physical object to remove.
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    storage_backend = Column(String(32), nullable=False, default="local")
    storage_key = Column(String(1024), nullable=False, unique=True)
    media_type = Column(String(255), nullable=False)
    byte_size = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False, index=True)
    visibility = Column(String(24), nullable=False, default="private")
    status = Column(String(24), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("byte_size >= 0", name="ck_storage_object_size"),
        CheckConstraint(
            "visibility IN ('private','release','public')",
            name="ck_storage_object_visibility",
        ),
        CheckConstraint(
            "status IN ('pending','active','quarantined','deleted')",
            name="ck_storage_object_status",
        ),
    )


class LibraryAsset(Base):
    """One immutable entry in a published visual-asset catalog."""

    __tablename__ = "library_assets"

    id = Column(String(36), primary_key=True, default=new_uuid)
    catalog_version = Column(String(64), nullable=False, index=True)
    stable_key = Column(String(255), nullable=False)
    asset_type = Column(String(32), nullable=False, index=True)
    storage_object_id = Column(
        String(36), ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False
    )
    description_cn = Column(Text, nullable=False, default="")
    description_en = Column(Text, nullable=False, default="")
    tags = Column(JSON, nullable=False, default=list)
    taxonomy = Column(JSON, nullable=False, default=dict)
    identity_group = Column(String(128), index=True)
    expression = Column(String(64))
    pose = Column(String(64))
    style = Column(String(64), index=True)
    quality_score = Column(Float, nullable=False, default=0.0)
    safety_status = Column(String(24), nullable=False, default="pending")
    rights_metadata = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("catalog_version", "stable_key", name="uq_library_asset_catalog_key"),
        CheckConstraint(
            "asset_type IN ('portrait','background','keyframe')",
            name="ck_library_asset_type",
        ),
        CheckConstraint(
            "safety_status IN ('pending','approved','rejected')",
            name="ck_library_asset_safety",
        ),
        CheckConstraint(
            "quality_score >= 0 AND quality_score <= 1",
            name="ck_library_asset_quality",
        ),
        Index("ix_library_asset_catalog_filter", "catalog_version", "asset_type", "enabled"),
    )


class LibraryTag(Base):
    """One normalized structured facet shared by catalog versions."""

    __tablename__ = "library_tags"

    id = Column(String(36), primary_key=True, default=new_uuid)
    category = Column(String(64), nullable=False)
    value = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)

    __table_args__ = (
        UniqueConstraint("category", "value", name="uq_library_tags_category_value"),
    )


class LibraryAssetTag(Base):
    """Many-to-many index from immutable catalog assets to structured facets."""

    __tablename__ = "library_asset_tags"

    library_asset_id = Column(
        String(36),
        ForeignKey("library_assets.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag_id = Column(
        String(36),
        ForeignKey("library_tags.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (
        Index("ix_library_asset_tags_tag_asset", "tag_id", "library_asset_id"),
    )


class LibraryAssetEmbedding(Base):
    """Provider-neutral vectors for the frozen in-memory matcher."""

    __tablename__ = "library_asset_embeddings"

    id = Column(String(36), primary_key=True, default=new_uuid)
    library_asset_id = Column(
        String(36), ForeignKey("library_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_version = Column(String(128), nullable=False)
    dimensions = Column(Integer, nullable=False)
    embedding = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "library_asset_id", "model_version", name="uq_library_asset_embedding_model"
        ),
        CheckConstraint("dimensions > 0", name="ck_library_asset_embedding_dimensions"),
    )


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"

    id = Column(String(36), primary_key=True, default=new_uuid)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name = Column(String(200), nullable=False)
    reference_storage_object_id = Column(
        String(36), ForeignKey("storage_objects.id", ondelete="RESTRICT"), nullable=False
    )
    provider = Column(String(64), nullable=False, default="cosyvoice")
    provider_profile_id = Column(String(255))
    profile_version = Column(Integer, nullable=False, default=1)
    consent_confirmed_at = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(24), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("project_id", "display_name", "profile_version", name="uq_voice_profile_version"),
        CheckConstraint(
            "status IN ('pending','ready','failed','deleted')",
            name="ck_voice_profile_status",
        ),
    )


class GenerationTask(Base):
    __tablename__ = "generation_tasks"

    id = Column(String(36), primary_key=True, default=new_uuid)
    root_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), index=True)
    parent_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind = Column(String(100), nullable=False, index=True)
    status = Column(String(24), nullable=False, default="queued", index=True)
    stage = Column(String(100))
    progress = Column(Float, nullable=False, default=0.0)
    idempotency_key = Column(String(255), nullable=False)
    source_refs = Column(JSON, nullable=False, default=dict)
    result_refs = Column(JSON, nullable=False, default=dict)
    parameters = Column(JSON, nullable=False, default=dict)
    parameters_hash = Column(String(64), nullable=False)
    attempt = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    cancel_requested_at = Column(DateTime(timezone=True))
    lease_owner = Column(String(255))
    lease_token = Column(String(36))
    heartbeat_at = Column(DateTime(timezone=True), index=True)
    error_code = Column(String(100))
    error_detail = Column(Text)
    estimated_cost = Column(Numeric(18, 6), nullable=False, default=0)
    reserved_cost = Column(Numeric(18, 6), nullable=False, default=0)
    actual_cost = Column(Numeric(18, 6), nullable=False, default=0)
    queued_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_generation_task_user_idempotency"),
        CheckConstraint(
            "status IN ('queued','running','partial','succeeded','failed','cancelled')",
            name="ck_generation_task_status",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_generation_task_progress"),
        CheckConstraint("attempt >= 0 AND max_attempts >= 1", name="ck_generation_task_attempts"),
        Index("ix_generation_task_active_source", "project_id", "kind", "parameters_hash", "status"),
    )


class GenerationTaskDependency(Base):
    __tablename__ = "generation_task_dependencies"

    task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="CASCADE"), primary_key=True)
    depends_on_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="CASCADE"), primary_key=True
    )
    required_status = Column(String(24), nullable=False, default="succeeded")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class TaskEvent(Base):
    __tablename__ = "task_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="CASCADE"), nullable=False)
    seq = Column(Integer, nullable=False)
    event_type = Column(String(64), nullable=False)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_task_event_seq"),
        Index("ix_task_event_replay", "task_id", "seq"),
    )


class AgentTraceEvent(Base):
    __tablename__ = "agent_trace_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    thread_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(64), index=True)
    run_seq = Column(Integer)
    event_type = Column(String(100), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="agent", index=True)
    visibility = Column(String(32), nullable=False, default="ui")
    span_id = Column(String(64), nullable=False, index=True)
    parent_span_id = Column(String(64), index=True)
    tool_call_id = Column(String(128), index=True)
    tool_name = Column(String(128), index=True)
    task_id = Column(String(36), index=True)
    model = Column(String(128))
    payload = Column(JSON, nullable=False, default=dict)
    model_view = Column(JSON, nullable=False, default=dict)
    full_ref = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_agent_trace_thread_run_seq", "thread_id", "run_id", "run_seq"),
        Index("ix_agent_trace_type_created", "event_type", "created_at"),
        CheckConstraint(
            "source IN ('agent','model','tool','task','user','frontend','system')",
            name="ck_agent_trace_source",
        ),
        CheckConstraint(
            "visibility IN ('model','ui','admin','debug')",
            name="ck_agent_trace_visibility",
        ),
    )


class AgentThread(Base):
    """服务端 Agent 会话。"""

    __tablename__ = "agent_threads"

    thread_id = Column(String(64), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    agent_kind = Column(String(64), nullable=False, default="server_agent")
    mode = Column(String(32), nullable=False, default="restricted")
    status = Column(String(32), nullable=False, default="created", index=True)
    stop_reason = Column(String(64))
    model = Column(String(128))
    current_turn_id = Column(String(64), index=True)
    last_turn_id = Column(String(64), index=True)
    persisted_message_count = Column(Integer, nullable=False, default=0)
    event_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("mode IN ('restricted','solo','default')", name="ck_agent_thread_mode"),
        CheckConstraint("persisted_message_count >= 0", name="ck_agent_thread_persisted_message_count"),
        CheckConstraint("event_count >= 0", name="ck_agent_thread_event_count"),
    )


class AgentTurn(Base):
    """一次用户输入触发的 Agent 运行。"""

    __tablename__ = "agent_turns"

    turn_id = Column(String(64), primary_key=True)
    thread_id = Column(
        String(64),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mode = Column(String(32), nullable=False, default="restricted")
    status = Column(String(32), nullable=False, default="running", index=True)
    stop_reason = Column(String(64))
    input = Column(JSON, nullable=False, default=dict)
    message_start_index = Column(Integer, nullable=False, default=0)
    message_end_index = Column(Integer)
    event_start_seq = Column(Integer, nullable=False, default=0)
    event_end_seq = Column(Integer)
    sampling_steps = Column(Integer)
    tool_call_count = Column(Integer)
    error = Column(JSON)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("mode IN ('restricted','solo','default')", name="ck_agent_turn_mode"),
        CheckConstraint(
            "status IN ('running','waiting_user','completed','failed','interrupted')",
            name="ck_agent_turn_status",
        ),
        Index("ix_agent_turn_thread_created", "thread_id", "created_at"),
    )


class AgentMessage(Base):
    """发给模型的规范化消息。"""

    __tablename__ = "agent_messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    thread_id = Column(
        String(64),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id = Column(String(64), index=True)
    message_index = Column(Integer, nullable=False)
    role = Column(String(32), nullable=False)
    message = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("thread_id", "message_index", name="uq_agent_message_thread_index"),
        Index("ix_agent_message_thread_index", "thread_id", "message_index"),
    )


class AgentEvent(Base):
    """前端 SSE 事件回放记录。"""

    __tablename__ = "agent_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    thread_id = Column(
        String(64),
        ForeignKey("agent_threads.thread_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    turn_id = Column(String(64), index=True)
    seq = Column(Integer, nullable=False)
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("thread_id", "seq", name="uq_agent_event_thread_seq"),
        Index("ix_agent_event_thread_seq", "thread_id", "seq"),
        Index("ix_agent_event_thread_turn_seq", "thread_id", "turn_id", "seq"),
    )


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id = Column(String(36), primary_key=True, default=new_uuid)
    aggregate_type = Column(String(64), nullable=False)
    aggregate_id = Column(String(64), nullable=False, index=True)
    event_type = Column(String(100), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="pending", index=True)
    attempt = Column(Integer, nullable=False, default=0)
    available_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    lease_owner = Column(String(255))
    leased_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    last_error = Column(Text)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','publishing','published','failed')",
            name="ck_outbox_event_status",
        ),
        Index("ix_outbox_dispatch", "status", "available_at"),
    )


class UsageReservation(Base):
    __tablename__ = "usage_reservations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="SET NULL"), index=True)
    task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    status = Column(String(24), nullable=False, default="reserved")
    currency = Column(String(16), nullable=False, default="credit")
    estimated_amount = Column(Numeric(18, 6), nullable=False)
    reserved_amount = Column(Numeric(18, 6), nullable=False)
    settled_amount = Column(Numeric(18, 6), nullable=False, default=0)
    refunded_amount = Column(Numeric(18, 6), nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    settled_at = Column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved','settled','released','cancelled')",
            name="ck_usage_reservation_status",
        ),
    )


class UsageLedgerEntry(Base):
    __tablename__ = "usage_ledger_entries"

    id = Column(String(36), primary_key=True, default=new_uuid)
    reservation_id = Column(
        String(36),
        ForeignKey(
            "usage_reservations.id",
            name="fk_usage_ledger_entries_reservation_id_usage_reservations",
            ondelete="SET NULL",
        ),
        index=True,
    )
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(
        Integer,
        ForeignKey(
            "projects.id",
            name="fk_usage_ledger_entries_project_id_projects",
            ondelete="SET NULL",
        ),
        index=True,
    )
    task_id = Column(
        String(36),
        ForeignKey(
            "generation_tasks.id",
            name="fk_usage_ledger_entries_task_id_generation_tasks",
            ondelete="SET NULL",
        ),
        index=True,
    )
    activity_type = Column(String(100), nullable=False, default="usage")
    entry_type = Column(String(32), nullable=False)
    amount = Column(Numeric(18, 6), nullable=False)
    currency = Column(String(16), nullable=False, default="credit")
    event_metadata = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "entry_type IN ('reserve','settle','refund','adjustment')",
            name="ck_usage_ledger_entry_type",
        ),
    )


class ProviderUsageRecord(Base):
    __tablename__ = "provider_usage_records"

    id = Column(String(36), primary_key=True, default=new_uuid)
    task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="CASCADE"), nullable=False, index=True)
    provider = Column(String(64), nullable=False)
    model = Column(String(128), nullable=False)
    provider_request_id = Column(String(255))
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    image_count = Column(Integer, nullable=False, default=0)
    audio_seconds = Column(Numeric(18, 3), nullable=False, default=0)
    latency_ms = Column(Integer)
    cost_amount = Column(Numeric(18, 6), nullable=False, default=0)
    cost_currency = Column(String(16), nullable=False, default="CNY")
    cache_hit = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("provider", "provider_request_id", name="uq_provider_request_usage"),
    )


class ProjectContentHead(Base):
    """Current authoring pointers kept outside the legacy Project row.

    This makes the migration additive for SQLite while still providing a
    single, transactionally updated head for all current v2 revisions.
    """

    __tablename__ = "project_content_heads"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    current_bible_revision_id = Column(String(36), index=True)
    current_outline_revision_id = Column(String(36), index=True)
    published_release_id = Column(String(36), index=True)
    lifecycle_status = Column(String(32), nullable=False, default="draft")
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AuthoringProjectDeleteScope(Base):
    """Transaction-local authorization for deleting one complete authoring aggregate."""

    __tablename__ = "authoring_project_delete_scopes"

    project_id = Column(Integer, primary_key=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class StoryPath(Base):
    """An authoring timeline with immutable fork provenance."""

    __tablename__ = "story_paths"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    parent_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="RESTRICT"), index=True
    )
    # PathChapter and Candidate both depend on StoryPath. Keep these provenance
    # IDs cycle-free in DDL; promotion validates ownership atomically.
    fork_path_chapter_id = Column(String(36), index=True)
    fork_checkpoint_node_id = Column(
        String(36), ForeignKey("story_nodes.id", ondelete="RESTRICT"), index=True
    )
    fork_candidate_id = Column(String(36))
    base_state_snapshot_id = Column(
        String(36), ForeignKey("state_snapshots.id", ondelete="RESTRICT"), index=True
    )
    title = Column(String(200), nullable=False)
    status = Column(String(24), nullable=False, default="active")
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("fork_candidate_id", name="uq_story_path_fork_candidate"),
        CheckConstraint("status IN ('active','archived')", name="ck_story_path_status"),
        CheckConstraint("lock_version > 0", name="ck_story_path_lock_version"),
        CheckConstraint("length(trim(title)) > 0", name="ck_story_path_title"),
        CheckConstraint(
            "parent_path_id IS NULL OR parent_path_id <> id",
            name="ck_story_path_not_self_parent",
        ),
        CheckConstraint(
            "(parent_path_id IS NULL "
            "AND fork_path_chapter_id IS NULL "
            "AND fork_checkpoint_node_id IS NULL "
            "AND fork_candidate_id IS NULL) "
            "OR (parent_path_id IS NOT NULL "
            "AND fork_path_chapter_id IS NOT NULL "
            "AND fork_checkpoint_node_id IS NOT NULL "
            "AND fork_candidate_id IS NOT NULL "
            "AND base_state_snapshot_id IS NOT NULL)",
            name="ck_story_path_fork_provenance",
        ),
        Index(
            "uq_story_path_root_project",
            "project_id",
            unique=True,
            sqlite_where=text("parent_path_id IS NULL"),
            postgresql_where=text("parent_path_id IS NULL"),
        ),
        Index("ix_story_path_project_status", "project_id", "status"),
    )


class ChapterSlot(Base):
    """Stable revision family for one logical chapter in a path lineage."""

    __tablename__ = "chapter_slots"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_for_story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_chapter_slot_project_path", "project_id", "created_for_story_path_id"),
    )


class StoryPathChapter(Base):
    """Placement and display order of a ChapterSlot on one StoryPath."""

    __tablename__ = "story_path_chapters"

    id = Column(String(36), primary_key=True, default=new_uuid)
    story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chapter_slot_id = Column(
        String(36), ForeignKey("chapter_slots.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    display_index = Column(Integer, nullable=False)
    predecessor_path_chapter_id = Column(
        String(36), ForeignKey("story_path_chapters.id", ondelete="RESTRICT"), index=True
    )
    inherited_from_path_chapter_id = Column(
        String(36), ForeignKey("story_path_chapters.id", ondelete="RESTRICT"), index=True
    )
    current_revision_id = Column(
        String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), index=True
    )
    status = Column(
        String(24), nullable=False, default="active", server_default="active"
    )
    detached_at = Column(DateTime(timezone=True))
    detached_by_outline_revision_id = Column(
        String(36),
        ForeignKey("outline_revisions.id", ondelete="RESTRICT"),
        index=True,
    )
    lock_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "story_path_id", "chapter_slot_id", name="uq_story_path_chapter_slot"
        ),
        CheckConstraint(
            "status IN ('active','detached')",
            name="ck_story_path_chapter_status",
        ),
        CheckConstraint(
            "(status = 'active' AND detached_at IS NULL "
            "AND detached_by_outline_revision_id IS NULL) OR "
            "(status = 'detached' AND detached_at IS NOT NULL "
            "AND detached_by_outline_revision_id IS NOT NULL)",
            name="ck_story_path_chapter_detachment",
        ),
        CheckConstraint("display_index > 0", name="ck_story_path_chapter_display_index"),
        CheckConstraint("lock_version > 0", name="ck_story_path_chapter_lock_version"),
        CheckConstraint(
            "predecessor_path_chapter_id IS NULL OR predecessor_path_chapter_id <> id",
            name="ck_story_path_chapter_not_self_predecessor",
        ),
        CheckConstraint(
            "inherited_from_path_chapter_id IS NULL OR inherited_from_path_chapter_id <> id",
            name="ck_story_path_chapter_not_self_inherited",
        ),
        Index("ix_story_path_chapter_order", "story_path_id", "display_index"),
        Index(
            "uq_story_path_chapter_display_index",
            "story_path_id",
            "display_index",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )


class StoryBibleRevision(Base):
    __tablename__ = "story_bible_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_revision_id = Column(String(36), ForeignKey("story_bible_revisions.id", ondelete="SET NULL"))
    revision_no = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=False)
    content_json = Column(JSON, nullable=False)
    status = Column(String(24), nullable=False, default="complete")
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    legacy_source_table = Column(String(64))
    legacy_source_id = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "revision_no", name="uq_bible_revision_no"),
        UniqueConstraint(
            "generation_task_id", name="uq_bible_revision_generation_task"
        ),
    )


class OutlineRevision(Base):
    __tablename__ = "outline_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="CASCADE"), index=True
    )
    bible_revision_id = Column(String(36), ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"), nullable=False)
    parent_revision_id = Column(String(36), ForeignKey("outline_revisions.id", ondelete="SET NULL"))
    revision_no = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False, default="draft")
    approved_at = Column(DateTime(timezone=True))
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    legacy_source_table = Column(String(64))
    legacy_source_id = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("story_path_id", "revision_no", name="uq_outline_path_revision_no"),
        # Identical chapter text based on a different Bible is a distinct
        # revision.  Deduplicate only when both content and source match.
        UniqueConstraint(
            "story_path_id", "source_hash", "content_hash",
            name="uq_outline_path_source_content",
        ),
        Index("ix_outline_revision_path_created", "story_path_id", "created_at"),
    )


class StoryPathOutlineHead(Base):
    __tablename__ = "story_path_outline_heads"

    story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="CASCADE"), primary_key=True
    )
    current_revision_id = Column(
        String(36), ForeignKey("outline_revisions.id", ondelete="RESTRICT"), nullable=True
    )
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("lock_version > 0", name="ck_story_path_outline_head_lock_version"),
    )


class OutlineChapter(Base):
    __tablename__ = "outline_revision_chapters"

    id = Column(String(36), primary_key=True, default=new_uuid)
    outline_revision_id = Column(String(36), ForeignKey("outline_revisions.id", ondelete="CASCADE"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    story_path_chapter_id = Column(
        String(36), ForeignKey("story_path_chapters.id", ondelete="RESTRICT"), index=True
    )
    display_index = Column(
        Integer,
        nullable=False,
        default=lambda context: context.get_current_parameters().get("chapter_index"),
    )
    title = Column(String(200))
    summary = Column(Text)
    conflict = Column(Text)
    characters = Column(JSON, nullable=False, default=list)
    scene = Column(String(500))
    emotion = Column(String(100))
    visual_keywords = Column(JSON, nullable=False, default=list)
    content_hash = Column(String(64), nullable=False)
    legacy_source_id = Column(String(64))

    __table_args__ = (
        UniqueConstraint("outline_revision_id", "chapter_index", name="uq_outline_revision_chapter"),
        UniqueConstraint(
            "outline_revision_id",
            "story_path_chapter_id",
            name="uq_outline_revision_path_chapter",
        ),
        UniqueConstraint(
            "outline_revision_id", "display_index", name="uq_outline_revision_display_index"
        ),
        CheckConstraint("display_index > 0", name="ck_outline_revision_display_index"),
    )


class ChapterRevision(Base):
    __tablename__ = "chapter_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_slot_id = Column(
        String(36), ForeignKey("chapter_slots.id", ondelete="CASCADE"), index=True
    )
    created_for_story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="RESTRICT"), index=True
    )
    chapter_index = Column(Integer, nullable=False)
    parent_revision_id = Column(String(36), ForeignKey("chapter_revisions.id", ondelete="SET NULL"))
    bible_revision_id = Column(String(36), ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"), nullable=False)
    outline_revision_id = Column(String(36), ForeignKey("outline_revisions.id", ondelete="RESTRICT"), nullable=False)
    state_snapshot_id = Column(
        String(36), ForeignKey("state_snapshots.id", ondelete="SET NULL"), index=True
    )
    revision_no = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    context_manifest = Column(JSON, nullable=False, default=dict)
    context_hash = Column(String(64), nullable=False, default=source_hash_default)
    content_hash = Column(String(64), nullable=False)
    content = Column(Text, nullable=False, default="")
    status = Column(String(24), nullable=False, default="generating")
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    legacy_source_table = Column(String(64))
    legacy_source_id = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("chapter_slot_id", "revision_no", name="uq_chapter_slot_revision_no"),
        # The same prose regenerated from another Bible/Outline/state must not
        # reactivate an obsolete revision.
        UniqueConstraint(
            "chapter_slot_id", "context_hash", "content_hash",
            name="uq_chapter_slot_context_content",
        ),
        Index("ix_chapter_revision_lookup", "project_id", "chapter_index", "created_at"),
        Index("ix_chapter_revision_slot_created", "chapter_slot_id", "created_at"),
    )


class ChapterHead(Base):
    __tablename__ = "chapter_heads"

    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    chapter_index = Column(Integer, primary_key=True)
    current_revision_id = Column(String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), nullable=False)
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class ChapterSegment(Base):
    __tablename__ = "chapter_segments"

    id = Column(String(36), primary_key=True, default=new_uuid)
    chapter_revision_id = Column(String(36), ForeignKey("chapter_revisions.id", ondelete="CASCADE"), nullable=False)
    order_index = Column(Integer, nullable=False)
    segment_key = Column(String(100), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(24), nullable=False, default="ready")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("chapter_revision_id", "order_index", name="uq_chapter_segment_order"),
        UniqueConstraint("chapter_revision_id", "segment_key", name="uq_chapter_segment_key"),
    )


class ChapterScriptRevision(Base):
    """Immutable, provider-annotated Script IR for one immutable chapter."""

    __tablename__ = "chapter_script_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_index = Column(Integer, nullable=False)
    chapter_revision_id = Column(
        String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    bible_revision_id = Column(
        String(36), ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    outline_revision_id = Column(
        String(36), ForeignKey("outline_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    parent_revision_id = Column(
        String(36), ForeignKey("chapter_script_revisions.id", ondelete="SET NULL")
    )
    revision_no = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    script_hash = Column(String(64), nullable=False)
    script_json = Column(JSON, nullable=False)
    coverage_json = Column(JSON, nullable=False)
    schema_version = Column(String(32), nullable=False)
    generator_version = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, default="complete")
    generation_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), unique=True
    )
    legacy_source_table = Column(String(64))
    legacy_source_id = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "chapter_revision_id",
            "revision_no",
            name="uq_chapter_script_chapter_revision_no",
        ),
        Index(
            "ix_chapter_script_revision_chapter_created",
            "chapter_revision_id",
            "created_at",
        ),
    )


class ChapterScriptHead(Base):
    __tablename__ = "chapter_script_heads"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    chapter_revision_id = Column(
        String(36),
        ForeignKey("chapter_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_revision_id = Column(
        String(36),
        ForeignKey("chapter_script_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "chapter_revision_id", name="uq_chapter_script_head_chapter_revision"
        ),
        CheckConstraint("lock_version > 0", name="ck_chapter_script_head_lock_version"),
        Index(
            "ix_chapter_script_head_legacy_lookup",
            "project_id",
            "chapter_index",
        ),
    )


class AssetVersion(Base):
    __tablename__ = "asset_versions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    source_kind = Column(String(64))
    source_revision_id = Column(String(36), index=True)
    source_hash = Column(String(64))
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    storage_object_id = Column(String(36), ForeignKey("storage_objects.id", ondelete="RESTRICT"))
    version_no = Column(Integer, nullable=False)
    cache_key = Column(String(128), nullable=False, index=True)
    provider = Column(String(64))
    model = Column(String(128))
    prompt = Column(Text)
    prompt_hash = Column(String(64), nullable=False)
    prompt_version = Column(String(64), nullable=False)
    negative_prompt_hash = Column(String(64))
    seed = Column(BigInteger)
    width = Column(Integer)
    height = Column(Integer)
    postprocess_version = Column(String(64))
    validator_version = Column(String(64))
    quality_score = Column(Float)
    safety_status = Column(String(24), nullable=False, default="pending")
    rights_metadata = Column(JSON, nullable=False, default=dict)
    asset_spec_json = Column(JSON, nullable=False, default=dict)
    render_spec_json = Column(JSON, nullable=False, default=dict)
    render_spec_hash = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("asset_id", "version_no", name="uq_asset_version_no"),
        UniqueConstraint("asset_id", "cache_key", name="uq_asset_version_cache_key"),
    )


class AssetPlan(Base):
    __tablename__ = "asset_plans"

    plan_key = Column(String(64), primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    source_kind = Column(String(64), nullable=False)
    source_revision_id = Column(String(64), nullable=False, index=True)
    source_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_asset_plan_source", "project_id", "source_kind", "source_revision_id"),
    )


class AssetPlanItem(Base):
    __tablename__ = "asset_plan_items"

    plan_key = Column(
        String(64), ForeignKey("asset_plans.plan_key", ondelete="CASCADE"), primary_key=True
    )
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    asset_type = Column(String(32), nullable=False)
    logical_key = Column(String(255), nullable=False)
    asset_spec_json = Column(JSON, nullable=False, default=dict)
    render_spec_json = Column(JSON, nullable=False, default=dict)
    spec_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "asset_type IN ('portrait','background','keyframe','audio')",
            name="ck_asset_plan_item_type",
        ),
        Index("ix_asset_plan_item_asset", "asset_id"),
    )


class ScriptResourceSlot(Base):
    """A semantic visual slot allocated before an asset is rendered."""

    __tablename__ = "script_resource_slots"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_script_revision_id = Column(
        String(36),
        ForeignKey("chapter_script_revisions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slot_key = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False)
    scene_id = Column(String(128), nullable=False)
    paragraph_id = Column(String(128))
    character_id = Column(String(128))
    order_index = Column(Integer, nullable=False, default=0)
    required = Column(Boolean, nullable=False, default=True)
    status = Column(String(24), nullable=False, default="planned")
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), index=True)
    asset_version_id = Column(
        String(36), ForeignKey("asset_versions.id", ondelete="RESTRICT"), index=True
    )
    generation_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), index=True
    )
    spec_json = Column(JSON, nullable=False, default=dict)
    lock_version = Column(
        Integer,
        nullable=False,
        default=1,
        server_default=text("1"),
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "chapter_script_revision_id", "slot_key", name="uq_script_resource_slot_key"
        ),
        CheckConstraint(
            "role IN ('background','portrait','keyframe')", name="ck_script_resource_slot_role"
        ),
        CheckConstraint(
            "status IN ('planned','generating','bound','failed')",
            name="ck_script_resource_slot_status",
        ),
        CheckConstraint(
            "lock_version > 0",
            name="ck_script_resource_slot_lock_version",
        ),
        Index(
            "ix_script_resource_slot_semantic",
            "chapter_script_revision_id",
            "scene_id",
            "paragraph_id",
            "character_id",
        ),
    )


class AssetBinding(Base):
    __tablename__ = "asset_bindings"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    source_kind = Column(String(64), nullable=False)
    source_id = Column(String(64), nullable=False)
    node_key = Column(String(128))
    segment_key = Column(String(128))
    role = Column(String(64), nullable=False)
    asset_version_id = Column(String(36), ForeignKey("asset_versions.id", ondelete="RESTRICT"), nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    required = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    deleted_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "source_kind", "source_id", "node_key", "segment_key", "role", "order_index",
            name="uq_asset_binding_slot",
        ),
        Index("ix_asset_binding_source", "source_kind", "source_id"),
    )


class SceneManifest(Base):
    """Frozen visual composition used by creator previews and reader overlays."""

    __tablename__ = "scene_manifests"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    reading_session_id = Column(
        String(36), ForeignKey("reading_sessions.id", ondelete="CASCADE"), index=True
    )
    bindings = Column(JSON, nullable=False, default=list)
    layout = Column(JSON, nullable=False, default=dict)
    match_summary = Column(JSON, nullable=False, default=dict)
    catalog_version = Column(String(64))
    matcher_version = Column(String(64), nullable=False)
    content_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "content_hash", name="uq_scene_manifest_project_hash"),
    )


class AssetAction(Base):
    """One durable, idempotent record for every creator visual-source action."""

    __tablename__ = "asset_actions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    reading_session_id = Column(
        String(36), ForeignKey("reading_sessions.id", ondelete="CASCADE"), index=True
    )
    mode = Column(String(32), nullable=False)
    selection_method = Column(String(32), nullable=False)
    origin = Column(String(32))
    status = Column(String(32), nullable=False, default="processing", index=True)
    stage = Column(String(64), nullable=False, default="accepted")
    progress = Column(Float, nullable=False, default=0.0)
    target = Column(JSON, nullable=False, default=dict)
    input = Column(JSON, nullable=False, default=dict)
    request_hash = Column(String(64), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    generation_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), index=True
    )
    result_asset_version_ids = Column(JSON, nullable=False, default=list)
    scene_manifest_id = Column(
        String(36), ForeignKey("scene_manifests.id", ondelete="SET NULL"), index=True
    )
    base_graph_hash = Column(String(64))
    result_graph_hash = Column(String(64))
    vngraph_patch = Column(JSON, nullable=False, default=list)
    requires_confirmation = Column(Boolean, nullable=False, default=True)
    error = Column(JSON)
    confirmed_at = Column(DateTime(timezone=True))
    cancelled_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_asset_action_user_idempotency"),
        CheckConstraint(
            "mode IN ('upload','agent_compose','direct_generate','library_select')",
            name="ck_asset_action_mode",
        ),
        CheckConstraint(
            "selection_method IN ('manual_upload','agent','direct_generate','manual_library','system_generate')",
            name="ck_asset_action_selection_method",
        ),
        CheckConstraint(
            "origin IS NULL OR origin IN ('upload','library','ai_generated')",
            name="ck_asset_action_origin",
        ),
        CheckConstraint(
            "status IN ('processing','awaiting_confirmation','ready','applied','failed','cancelled')",
            name="ck_asset_action_status",
        ),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_asset_action_progress"),
    )


class VoiceLine(Base):
    __tablename__ = "voice_lines"

    id = Column(String(36), primary_key=True, default=new_uuid)
    chapter_revision_id = Column(String(36), ForeignKey("chapter_revisions.id", ondelete="CASCADE"), nullable=False)
    occurrence_id = Column(String(128), nullable=False)
    order_index = Column(Integer, nullable=False)
    kind = Column(String(24), nullable=False)
    text = Column(Text, nullable=False)
    speaker_character_id = Column(String(100))
    speaker_name = Column(String(200))
    emotion = Column(String(64))
    voice_profile_id = Column(String(36), ForeignKey("voice_profiles.id", ondelete="SET NULL"))
    voice_profile_version = Column(Integer)
    audio_asset_version_id = Column(String(36), ForeignKey("asset_versions.id", ondelete="SET NULL"))
    status = Column(String(24), nullable=False, default="planned")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("chapter_revision_id", "occurrence_id", name="uq_voice_line_occurrence"),
        UniqueConstraint("chapter_revision_id", "order_index", name="uq_voice_line_order"),
    )


class VoiceLineVersion(Base):
    """Immutable snapshot of one editable VoiceLine consumed by a graph."""

    __tablename__ = "voice_line_versions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    voice_line_id = Column(
        String(36), ForeignKey("voice_lines.id", ondelete="RESTRICT"), nullable=False
    )
    chapter_revision_id = Column(
        String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    version_no = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    content_json = Column(JSON, nullable=False)
    audio_asset_version_id = Column(
        String(36), ForeignKey("asset_versions.id", ondelete="RESTRICT")
    )
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "voice_line_id", "version_no", name="uq_voice_line_version_no"
        ),
        UniqueConstraint(
            "voice_line_id", "content_hash", name="uq_voice_line_version_content"
        ),
        Index(
            "ix_voice_line_version_chapter_created",
            "chapter_revision_id",
            "created_at",
        ),
    )


class VNGraphRevision(Base):
    __tablename__ = "vn_graph_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    chapter_index = Column(Integer, nullable=False)
    chapter_revision_id = Column(String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), nullable=False)
    script_revision_id = Column(
        String(36),
        ForeignKey("chapter_script_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_revision_id = Column(String(36), ForeignKey("vn_graph_revisions.id", ondelete="SET NULL"))
    revision_no = Column(Integer, nullable=False)
    binding_manifest = Column(JSON, nullable=False, default=binding_manifest_default)
    binding_manifest_hash = Column(
        String(64), nullable=False, default=binding_manifest_hash_default
    )
    # Retained for one observation cycle; active code uses binding_manifest_hash.
    source_manifest_hash = Column(String(64), nullable=False)
    graph_hash = Column(String(64), nullable=False)
    graph_json = Column(JSON, nullable=False)
    schema_version = Column(String(32), nullable=False)
    compiler_version = Column(String(32), nullable=False)
    tachi_policy_version = Column(String(32), nullable=False)
    status = Column(String(24), nullable=False, default="complete")
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    legacy_source_table = Column(String(64))
    legacy_source_id = Column(String(64))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "script_revision_id",
            "revision_no",
            name="uq_vngraph_script_revision_no",
        ),
        UniqueConstraint(
            "script_revision_id",
            "binding_manifest_hash",
            "compiler_version",
            "schema_version",
            name="uq_vngraph_binding_compile",
        ),
        Index(
            "ix_vngraph_revision_script_created",
            "script_revision_id",
            "created_at",
        ),
    )


class VNGraphHead(Base):
    __tablename__ = "vn_graph_heads"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    chapter_index = Column(Integer, nullable=False)
    script_revision_id = Column(
        String(36),
        ForeignKey("chapter_script_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_revision_id = Column(
        String(36),
        ForeignKey("vn_graph_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "script_revision_id", name="uq_vn_graph_head_script_revision"
        ),
        CheckConstraint("lock_version > 0", name="ck_vn_graph_head_lock_version"),
        Index("ix_vn_graph_head_legacy_lookup", "project_id", "chapter_index"),
    )


@event.listens_for(ChapterScriptRevision, "before_update")
def _reject_chapter_script_revision_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("ChapterScriptRevision is immutable")


@event.listens_for(ChapterScriptRevision, "before_delete")
def _reject_chapter_script_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("ChapterScriptRevision is immutable")


@event.listens_for(AssetVersion, "before_update")
def _reject_asset_version_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("AssetVersion is immutable")


@event.listens_for(AssetVersion, "before_delete")
def _reject_asset_version_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("AssetVersion is immutable")


@event.listens_for(VoiceLineVersion, "before_update")
def _reject_voice_line_version_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("VoiceLineVersion is immutable")


@event.listens_for(VoiceLineVersion, "before_delete")
def _reject_voice_line_version_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("VoiceLineVersion is immutable")


@event.listens_for(VNGraphRevision, "before_update")
def _reject_vn_graph_revision_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("VNGraphRevision is immutable")


@event.listens_for(VNGraphRevision, "before_delete")
def _reject_vn_graph_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("VNGraphRevision is immutable")


class StateSnapshot(Base):
    __tablename__ = "state_snapshots"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    parent_snapshot_id = Column(String(36), ForeignKey("state_snapshots.id", ondelete="SET NULL"))
    state_hash = Column(String(64), nullable=False)
    state_json = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("project_id", "state_hash", name="uq_state_snapshot_hash"),
    )


class StoryNode(Base):
    __tablename__ = "story_nodes"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id = Column(String(36), index=True)
    node_type = Column(String(32), nullable=False)
    checkpoint_key = Column(String(128), index=True)
    merge_key = Column(String(128), index=True)
    content_revision_id = Column(String(36), index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class CandidateSetRevision(Base):
    """Immutable alternatives generated for one path checkpoint and source."""

    __tablename__ = "candidate_set_revisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    story_path_id = Column(
        String(36),
        ForeignKey(
            "story_paths.id",
            name="fk_candidate_set_revisions_story_path_id",
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    checkpoint_node_id = Column(
        String(36), ForeignKey("story_nodes.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    chapter_revision_id = Column(
        String(36), ForeignKey("chapter_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    state_snapshot_id = Column(
        String(36), ForeignKey("state_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    parent_revision_id = Column(
        String(36), ForeignKey("candidate_set_revisions.id", ondelete="SET NULL")
    )
    revision_no = Column(Integer, nullable=False)
    source_hash = Column(String(64), nullable=False)
    candidate_count = Column(Integer, nullable=False)
    instructions = Column(Text, nullable=False, default="")
    # Nullable only for rows created before the StoryPath cutover. New target
    # writers always persist and verify the complete ordered candidate payload.
    candidates_json = Column(JSON)
    content_hash = Column(String(64), index=True)
    generation_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL")
    )
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "story_path_id",
            "checkpoint_node_id",
            "revision_no",
            name="uq_candidate_set_path_checkpoint_revision",
        ),
        UniqueConstraint(
            "generation_task_id", name="uq_candidate_set_generation_task"
        ),
        CheckConstraint(
            "candidate_count >= 2 AND candidate_count <= 4",
            name="ck_candidate_set_candidate_count",
        ),
        Index(
            "ix_candidate_set_path_checkpoint_created",
            "story_path_id",
            "checkpoint_node_id",
            "created_at",
        ),
        Index(
            "ix_candidate_set_source",
            "chapter_revision_id",
            "state_snapshot_id",
            "source_hash",
        ),
    )


class CandidateSetHead(Base):
    __tablename__ = "candidate_set_heads"

    story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="CASCADE"), primary_key=True
    )
    checkpoint_node_id = Column(
        String(36), ForeignKey("story_nodes.id", ondelete="CASCADE"), primary_key=True
    )
    current_revision_id = Column(
        String(36),
        ForeignKey("candidate_set_revisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("lock_version > 0", name="ck_candidate_set_head_lock_version"),
    )


class BranchCandidate(Base):
    __tablename__ = "branch_candidates"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_set_revision_id = Column(
        String(36),
        ForeignKey(
            "candidate_set_revisions.id",
            name="fk_branch_candidates_candidate_set_revision_id",
            ondelete="RESTRICT",
        ),
        index=True,
    )
    checkpoint_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="CASCADE"), nullable=False)
    option_key = Column(String(128), nullable=False)
    preview_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="SET NULL"))
    preview_revision_id = Column(String(36), index=True)
    state_delta = Column(JSON, nullable=False, default=dict)
    candidate_status = Column(String(24), nullable=False, default="preview_ready")
    predicted_probability = Column(Float)
    expires_at = Column(DateTime(timezone=True))
    generation_task_id = Column(String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "candidate_set_revision_id",
            "option_key",
            name="uq_branch_candidate_set_option",
        ),
    )


class StoryPathPromotionRecord(Base):
    """Immutable idempotency record for one synchronous candidate promotion."""

    __tablename__ = "story_path_promotion_records"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id = Column(
        String(36), ForeignKey("branch_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    story_path_id = Column(
        String(36), ForeignKey("story_paths.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key = Column(String(255), nullable=False)
    request_json = Column(JSON, nullable=False)
    request_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_story_path_promotion_user_idempotency",
        ),
        Index("ix_story_path_promotion_candidate", "candidate_id", "story_path_id"),
    )


@event.listens_for(CandidateSetRevision, "before_update")
def _reject_candidate_set_revision_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("CandidateSetRevision is immutable")


@event.listens_for(CandidateSetRevision, "before_delete")
def _reject_candidate_set_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("CandidateSetRevision is immutable")


@event.listens_for(BranchCandidate, "before_update")
def _reject_versioned_branch_candidate_update(_mapper, _connection, target) -> None:
    if target.candidate_set_revision_id is not None:
        raise RuntimeError("Versioned BranchCandidate is immutable")


@event.listens_for(BranchCandidate, "before_delete")
def _reject_versioned_branch_candidate_delete(_mapper, _connection, target) -> None:
    if target.candidate_set_revision_id is not None:
        raise RuntimeError("Versioned BranchCandidate is immutable")


@event.listens_for(StoryPathPromotionRecord, "before_update")
def _reject_story_path_promotion_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("StoryPathPromotionRecord is immutable")


@event.listens_for(StoryPathPromotionRecord, "before_delete")
def _reject_story_path_promotion_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("StoryPathPromotionRecord is immutable")


class BranchEdge(Base):
    __tablename__ = "branch_edges"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    from_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="CASCADE"), nullable=False)
    to_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="CASCADE"), nullable=False)
    option_key = Column(String(128))
    state_delta = Column(JSON, nullable=False, default=dict)
    edge_type = Column(String(24), nullable=False, default="branch")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("from_node_id", "option_key", name="uq_branch_edge_option"),
    )


class ProjectRelease(Base):
    __tablename__ = "project_releases"

    id = Column(String(36), primary_key=True, default=new_uuid)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    status = Column(String(24), nullable=False, default="published")
    bible_revision_id = Column(String(36), ForeignKey("story_bible_revisions.id", ondelete="RESTRICT"), nullable=False)
    outline_revision_id = Column(String(36), ForeignKey("outline_revisions.id", ondelete="RESTRICT"), nullable=False)
    manifest_json = Column(JSON, nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    authoring_fingerprint = Column(String(64))
    release_notes = Column(Text)
    publication_idempotency_key = Column(String(255))
    publication_request_hash = Column(String(64))
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_at = Column(DateTime(timezone=True))
    withdrawn_at = Column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_project_release_version"),
        UniqueConstraint("project_id", "manifest_hash", name="uq_project_release_manifest"),
        UniqueConstraint("project_id", "id", name="uq_project_release_project_id_id"),
        UniqueConstraint(
            "project_id",
            "publication_idempotency_key",
            name="uq_project_release_publication_idempotency",
        ),
        CheckConstraint(
            "status IN ('published','superseded','withdrawn')",
            name="ck_project_release_status",
        ),
    )


class ProjectPublication(Base):
    """The sole pointer from an authoring project to its public Release."""

    __tablename__ = "project_publications"

    project_id = Column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    active_release_id = Column(String(36))
    published_at = Column(DateTime(timezone=True))
    lock_version = Column(Integer, nullable=False, default=1)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        ForeignKeyConstraint(
            ["project_id", "active_release_id"],
            ["project_releases.project_id", "project_releases.id"],
            name="fk_project_publication_active_release",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "active_release_id", name="uq_project_publication_active_release"
        ),
        CheckConstraint(
            "(active_release_id IS NULL AND published_at IS NULL) OR "
            "(active_release_id IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_project_publication_activation",
        ),
        CheckConstraint(
            "lock_version > 0", name="ck_project_publication_lock_version"
        ),
    )


class ReadingSession(Base):
    __tablename__ = "reading_sessions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id = Column(String(36), ForeignKey("project_releases.id", ondelete="RESTRICT"), nullable=False)
    head_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="SET NULL"))
    state_snapshot_id = Column(String(36), ForeignKey("state_snapshots.id", ondelete="SET NULL"))
    selected_continuation_id = Column(String(36), index=True)
    parent_session_id = Column(String(36), ForeignKey("reading_sessions.id", ondelete="SET NULL"))
    forked_from_decision_id = Column(String(36), index=True)
    lock_version = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('active','completed','archived')", name="ck_reading_session_status"),
    )


class ReadingContinuation(Base):
    """A generated branch that becomes public and immutable once confirmed."""

    __tablename__ = "reading_continuations"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(
        String(36), ForeignKey("reading_sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    release_id = Column(String(36), ForeignKey("project_releases.id", ondelete="RESTRICT"), index=True)
    chapter_number = Column(Integer, index=True)
    parent_continuation_id = Column(
        String(36), ForeignKey("reading_continuations.id", ondelete="SET NULL"), index=True
    )
    parent_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="SET NULL"), index=True)
    direction = Column(Text, nullable=False)
    visual_mode = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="processing", index=True)
    continuation_text = Column(Text, nullable=False, default="")
    state_delta = Column(JSON, nullable=False, default=dict)
    scene_manifest_id = Column(
        String(36), ForeignKey("scene_manifests.id", ondelete="SET NULL"), index=True
    )
    vngraph_patch = Column(JSON, nullable=False, default=list)
    uploaded_asset_version_ids = Column(JSON, nullable=False, default=list)
    frozen_asset_version_ids = Column(JSON, nullable=False, default=list)
    generation_task_id = Column(
        String(36), ForeignKey("generation_tasks.id", ondelete="SET NULL"), index=True
    )
    asset_action_id = Column(
        String(36), ForeignKey("asset_actions.id", ondelete="SET NULL"), index=True
    )
    base_session_lock_version = Column(Integer, nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)
    error = Column(JSON)
    confirmed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_reading_continuation_idempotency"),
        CheckConstraint(
            "visual_mode IN ('user_upload','system_generate')",
            name="ck_reading_continuation_visual_mode",
        ),
        CheckConstraint(
            "status IN ('processing','preview_ready','confirmed','failed','cancelled')",
            name="ck_reading_continuation_status",
        ),
        Index("ix_reading_continuation_parent", "session_id", "parent_continuation_id"),
        Index(
            "ix_public_continuation_chapter",
            "release_id",
            "chapter_number",
            "status",
            "confirmed_at",
        ),
    )


class ChoiceDecision(Base):
    __tablename__ = "choice_decisions"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), ForeignKey("reading_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    checkpoint_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="RESTRICT"), nullable=False)
    option_key = Column(String(128), nullable=False)
    candidate_id = Column(String(36), ForeignKey("branch_candidates.id", ondelete="RESTRICT"), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    previous_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="SET NULL"))
    result_node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="SET NULL"))
    previous_snapshot_id = Column(String(36), ForeignKey("state_snapshots.id", ondelete="SET NULL"))
    result_snapshot_id = Column(String(36), ForeignKey("state_snapshots.id", ondelete="SET NULL"))
    result_revision_id = Column(String(36), index=True)
    undone_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_id", "idempotency_key", name="uq_choice_decision_idempotency"),
        Index("ix_choice_decision_timeline", "session_id", "created_at"),
    )


class ReadingBookmark(Base):
    __tablename__ = "reading_bookmarks"

    id = Column(String(36), primary_key=True, default=new_uuid)
    session_id = Column(String(36), ForeignKey("reading_sessions.id", ondelete="CASCADE"), nullable=False)
    node_id = Column(String(36), ForeignKey("story_nodes.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(200))
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_id", "node_id", name="uq_reading_bookmark_node"),
    )


def _require_related_row(connection, statement, message: str) -> None:
    if connection.execute(statement.limit(1)).first() is None:
        raise ValueError(message)


def _changed_columns(target) -> set[str]:
    state = sa_inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


@event.listens_for(ChapterSlot, "before_insert")
@event.listens_for(ChapterSlot, "before_update")
def _validate_chapter_slot_ownership(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryPath.id).where(
            StoryPath.id == target.created_for_story_path_id,
            StoryPath.project_id == target.project_id,
        ),
        "ChapterSlot must belong to the Project that owns its StoryPath",
    )


@event.listens_for(StoryPathChapter, "before_insert")
@event.listens_for(StoryPathChapter, "before_update")
def _validate_story_path_chapter_ownership(_mapper, connection, target) -> None:
    path_project = connection.execute(
        select(StoryPath.project_id).where(StoryPath.id == target.story_path_id)
    ).scalar_one_or_none()
    if path_project is None:
        raise ValueError("StoryPathChapter must reference an existing StoryPath")
    _require_related_row(
        connection,
        select(ChapterSlot.id).where(
            ChapterSlot.id == target.chapter_slot_id,
            ChapterSlot.project_id == path_project,
        ),
        "StoryPathChapter cannot place a ChapterSlot from another Project",
    )
    if target.predecessor_path_chapter_id is not None:
        _require_related_row(
            connection,
            select(StoryPathChapter.id).where(
                StoryPathChapter.id == target.predecessor_path_chapter_id,
                StoryPathChapter.story_path_id == target.story_path_id,
            ),
            "StoryPathChapter predecessor must be on the same StoryPath",
        )
    if target.inherited_from_path_chapter_id is not None:
        _require_related_row(
            connection,
            select(StoryPathChapter.id)
            .join(StoryPath, StoryPath.id == StoryPathChapter.story_path_id)
            .where(
                StoryPathChapter.id == target.inherited_from_path_chapter_id,
                StoryPath.project_id == path_project,
            ),
            "Inherited PathChapter must belong to the same Project",
        )
    if target.current_revision_id is not None:
        _require_related_row(
            connection,
            select(ChapterRevision.id).where(
                ChapterRevision.id == target.current_revision_id,
                ChapterRevision.project_id == path_project,
                ChapterRevision.chapter_slot_id == target.chapter_slot_id,
            ),
            "PathChapter Head must select a Revision from its ChapterSlot",
        )


@event.listens_for(StoryPathOutlineHead, "before_insert")
@event.listens_for(StoryPathOutlineHead, "before_update")
def _validate_story_path_outline_head(_mapper, connection, target) -> None:
    if target.current_revision_id is None:
        return
    _require_related_row(
        connection,
        select(OutlineRevision.id).where(
            OutlineRevision.id == target.current_revision_id,
            OutlineRevision.story_path_id == target.story_path_id,
        ),
        "Outline Head must select a Revision from its StoryPath",
    )


@event.listens_for(ChapterScriptHead, "before_insert")
@event.listens_for(ChapterScriptHead, "before_update")
def _validate_chapter_script_head(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(ChapterRevision.id).where(
            ChapterRevision.id == target.chapter_revision_id,
            ChapterRevision.project_id == target.project_id,
        ),
        "Script Head source ChapterRevision must belong to its Project",
    )
    if target.current_revision_id is not None:
        _require_related_row(
            connection,
            select(ChapterScriptRevision.id).where(
                ChapterScriptRevision.id == target.current_revision_id,
                ChapterScriptRevision.project_id == target.project_id,
                ChapterScriptRevision.chapter_revision_id
                == target.chapter_revision_id,
            ),
            "Script Head must select a Revision from its ChapterRevision",
        )


@event.listens_for(VNGraphHead, "before_insert")
@event.listens_for(VNGraphHead, "before_update")
def _validate_vn_graph_head(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(ChapterScriptRevision.id).where(
            ChapterScriptRevision.id == target.script_revision_id,
            ChapterScriptRevision.project_id == target.project_id,
        ),
        "VNGraph Head source ScriptRevision must belong to its Project",
    )
    if target.current_revision_id is not None:
        _require_related_row(
            connection,
            select(VNGraphRevision.id).where(
                VNGraphRevision.id == target.current_revision_id,
                VNGraphRevision.project_id == target.project_id,
                VNGraphRevision.script_revision_id == target.script_revision_id,
            ),
            "VNGraph Head must select a Revision from its ScriptRevision",
        )


@event.listens_for(CandidateSetHead, "before_insert")
@event.listens_for(CandidateSetHead, "before_update")
def _validate_candidate_set_head(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryPath.id)
        .join(StoryNode, StoryNode.project_id == StoryPath.project_id)
        .where(
            StoryPath.id == target.story_path_id,
            StoryNode.id == target.checkpoint_node_id,
        ),
        "CandidateSet Head path and checkpoint must belong to one Project",
    )
    if target.current_revision_id is not None:
        _require_related_row(
            connection,
            select(CandidateSetRevision.id).where(
                CandidateSetRevision.id == target.current_revision_id,
                CandidateSetRevision.story_path_id == target.story_path_id,
                CandidateSetRevision.checkpoint_node_id
                == target.checkpoint_node_id,
            ),
            "CandidateSet Head must select a Revision from its checkpoint family",
        )


@event.listens_for(StoryBibleRevision, "before_insert")
def _validate_bible_revision_parent(_mapper, connection, target) -> None:
    if target.parent_revision_id is not None:
        _require_related_row(
            connection,
            select(StoryBibleRevision.id).where(
                StoryBibleRevision.id == target.parent_revision_id,
                StoryBibleRevision.project_id == target.project_id,
            ),
            "Bible parent Revision must belong to the same Project",
        )


@event.listens_for(OutlineRevision, "before_insert")
def _validate_outline_revision_sources(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryBibleRevision.id).where(
            StoryBibleRevision.id == target.bible_revision_id,
            StoryBibleRevision.project_id == target.project_id,
        ),
        "Outline Bible Revision must belong to the same Project",
    )
    if target.story_path_id is not None:
        _require_related_row(
            connection,
            select(StoryPath.id).where(
                StoryPath.id == target.story_path_id,
                StoryPath.project_id == target.project_id,
            ),
            "Outline StoryPath must belong to the same Project",
        )
    if target.parent_revision_id is not None:
        parent = connection.execute(
            select(
                OutlineRevision.project_id,
                OutlineRevision.story_path_id,
            ).where(OutlineRevision.id == target.parent_revision_id)
        ).first()
        if parent is None or parent.project_id != target.project_id or (
            parent.story_path_id != target.story_path_id
        ):
            raise ValueError("Outline parent Revision must be in the same path family")


@event.listens_for(ChapterRevision, "before_insert")
def _validate_chapter_revision_sources(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryBibleRevision.id).where(
            StoryBibleRevision.id == target.bible_revision_id,
            StoryBibleRevision.project_id == target.project_id,
        ),
        "Chapter Bible Revision must belong to the same Project",
    )
    _require_related_row(
        connection,
        select(OutlineRevision.id).where(
            OutlineRevision.id == target.outline_revision_id,
            OutlineRevision.project_id == target.project_id,
        ),
        "Chapter Outline Revision must belong to the same Project",
    )
    if target.chapter_slot_id is not None:
        _require_related_row(
            connection,
            select(ChapterSlot.id).where(
                ChapterSlot.id == target.chapter_slot_id,
                ChapterSlot.project_id == target.project_id,
            ),
            "ChapterSlot must belong to the same Project as its Revision",
        )
    if target.created_for_story_path_id is not None:
        _require_related_row(
            connection,
            select(StoryPath.id).where(
                StoryPath.id == target.created_for_story_path_id,
                StoryPath.project_id == target.project_id,
            ),
            "Chapter StoryPath must belong to the same Project",
        )
    if target.state_snapshot_id is not None:
        _require_related_row(
            connection,
            select(StateSnapshot.id).where(
                StateSnapshot.id == target.state_snapshot_id,
                StateSnapshot.project_id == target.project_id,
            ),
            "Chapter state snapshot must belong to the same Project",
        )
    if target.parent_revision_id is not None:
        parent = connection.execute(
            select(
                ChapterRevision.project_id,
                ChapterRevision.chapter_slot_id,
                ChapterRevision.chapter_index,
            ).where(ChapterRevision.id == target.parent_revision_id)
        ).first()
        same_family = parent is not None and parent.project_id == target.project_id
        if target.chapter_slot_id is not None:
            same_family = same_family and parent.chapter_slot_id == target.chapter_slot_id
        else:
            same_family = same_family and parent.chapter_index == target.chapter_index
        if not same_family:
            raise ValueError("Chapter parent Revision must be in the same chapter family")


@event.listens_for(ChapterScriptRevision, "before_insert")
def _validate_script_revision_sources(_mapper, connection, target) -> None:
    source = connection.execute(
        select(
            ChapterRevision.project_id,
            ChapterRevision.bible_revision_id,
            ChapterRevision.outline_revision_id,
        ).where(ChapterRevision.id == target.chapter_revision_id)
    ).first()
    if source is None or (
        source.project_id != target.project_id
        or source.bible_revision_id != target.bible_revision_id
        or source.outline_revision_id != target.outline_revision_id
    ):
        raise ValueError("Script Revision sources must match its ChapterRevision")
    if target.parent_revision_id is not None:
        _require_related_row(
            connection,
            select(ChapterScriptRevision.id).where(
                ChapterScriptRevision.id == target.parent_revision_id,
                ChapterScriptRevision.chapter_revision_id
                == target.chapter_revision_id,
            ),
            "Script parent Revision must be in the same chapter family",
        )


@event.listens_for(VNGraphRevision, "before_insert")
def _validate_vn_graph_revision_sources(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(ChapterScriptRevision.id).where(
            ChapterScriptRevision.id == target.script_revision_id,
            ChapterScriptRevision.project_id == target.project_id,
            ChapterScriptRevision.chapter_revision_id
            == target.chapter_revision_id,
        ),
        "VNGraph Revision must match its exact Script and Chapter Revisions",
    )
    if target.parent_revision_id is not None:
        _require_related_row(
            connection,
            select(VNGraphRevision.id).where(
                VNGraphRevision.id == target.parent_revision_id,
                VNGraphRevision.script_revision_id == target.script_revision_id,
            ),
            "VNGraph parent Revision must be in the same Script family",
        )


@event.listens_for(CandidateSetRevision, "before_insert")
def _validate_candidate_set_revision_sources(_mapper, connection, target) -> None:
    related_projects = (
        connection.execute(
            select(
                StoryPath.project_id,
                StoryNode.project_id,
                ChapterRevision.project_id,
                StateSnapshot.project_id,
            )
            .select_from(StoryPath)
            .join(StoryNode, StoryNode.id == target.checkpoint_node_id)
            .join(ChapterRevision, ChapterRevision.id == target.chapter_revision_id)
            .join(StateSnapshot, StateSnapshot.id == target.state_snapshot_id)
            .where(StoryPath.id == target.story_path_id)
        ).first()
    )
    if related_projects is None or any(
        project_id != target.project_id for project_id in related_projects
    ):
        raise ValueError("CandidateSet sources must belong to the same Project")
    if target.parent_revision_id is not None:
        _require_related_row(
            connection,
            select(CandidateSetRevision.id).where(
                CandidateSetRevision.id == target.parent_revision_id,
                CandidateSetRevision.story_path_id == target.story_path_id,
                CandidateSetRevision.checkpoint_node_id
                == target.checkpoint_node_id,
            ),
            "CandidateSet parent Revision must be in the same checkpoint family",
        )


@event.listens_for(OutlineChapter, "before_insert")
def _validate_outline_chapter_insert(_mapper, connection, target) -> None:
    if target.story_path_chapter_id is not None:
        _validate_outline_chapter_reconciliation(connection, target)


def _validate_outline_chapter_reconciliation(connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryPathChapter.id)
        .join(
            OutlineRevision,
            OutlineRevision.story_path_id == StoryPathChapter.story_path_id,
        )
        .where(
            StoryPathChapter.id == target.story_path_chapter_id,
            OutlineRevision.id == target.outline_revision_id,
        ),
        "OutlineChapter must bind to a PathChapter on its Outline StoryPath",
    )


@event.listens_for(OutlineChapter, "before_update")
def _protect_outline_chapter_update(_mapper, connection, target) -> None:
    changed = _changed_columns(target)
    if not changed:
        return
    if changed != {"story_path_chapter_id"}:
        raise RuntimeError("OutlineChapter is immutable after creation")
    history = sa_inspect(target).attrs.story_path_chapter_id.history
    old_value = history.deleted[0] if history.deleted else None
    if old_value is not None or target.story_path_chapter_id is None:
        raise RuntimeError("OutlineChapter PathChapter binding is write-once")
    _validate_outline_chapter_reconciliation(connection, target)


@event.listens_for(OutlineChapter, "before_delete")
def _reject_outline_chapter_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("OutlineChapter is immutable")


@event.listens_for(StoryBibleRevision, "before_update")
def _reject_story_bible_revision_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("StoryBibleRevision is immutable")


@event.listens_for(StoryBibleRevision, "before_delete")
def _reject_story_bible_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("StoryBibleRevision is immutable")


@event.listens_for(OutlineRevision, "before_update")
def _protect_outline_revision_update(_mapper, _connection, target) -> None:
    if _changed_columns(target) - {"status", "approved_at"}:
        raise RuntimeError("OutlineRevision payload and provenance are immutable")


@event.listens_for(OutlineRevision, "before_delete")
def _reject_outline_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("OutlineRevision is immutable")


@event.listens_for(ChapterRevision, "before_update")
def _reject_chapter_revision_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("ChapterRevision is immutable")


@event.listens_for(ChapterRevision, "before_delete")
def _reject_chapter_revision_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("ChapterRevision is immutable")


@event.listens_for(StateSnapshot, "before_update")
def _reject_state_snapshot_update(_mapper, _connection, _target) -> None:
    raise RuntimeError("StateSnapshot is immutable")


@event.listens_for(StateSnapshot, "before_delete")
def _reject_state_snapshot_delete(_mapper, _connection, _target) -> None:
    raise RuntimeError("StateSnapshot is immutable")


@event.listens_for(ProjectPublication, "before_insert")
@event.listens_for(ProjectPublication, "before_update")
def _validate_project_publication_target(_mapper, connection, target) -> None:
    if target.active_release_id is None:
        return
    _require_related_row(
        connection,
        select(ProjectRelease.id).where(
            ProjectRelease.id == target.active_release_id,
            ProjectRelease.project_id == target.project_id,
            ProjectRelease.status == "published",
            ProjectRelease.published_at.is_not(None),
            ProjectRelease.withdrawn_at.is_(None),
        ),
        "ProjectPublication must point to an active published Release",
    )


@event.listens_for(ProjectRelease, "before_update")
def _protect_active_release_lifecycle(_mapper, connection, target) -> None:
    if (
        target.status == "published"
        and target.published_at is not None
        and target.withdrawn_at is None
    ):
        return
    active = connection.execute(
        select(ProjectPublication.project_id).where(
            ProjectPublication.project_id == target.project_id,
            ProjectPublication.active_release_id == target.id,
        ).limit(1)
    ).first()
    if active is not None:
        raise RuntimeError("An active Release must be unpublished before withdrawal")


@event.listens_for(StoryPath, "before_insert")
@event.listens_for(StoryPath, "before_update")
def _validate_story_path_fork_sources(_mapper, connection, target) -> None:
    if target.parent_path_id is None:
        return
    _require_related_row(
        connection,
        select(StoryPath.id).where(
            StoryPath.id == target.parent_path_id,
            StoryPath.project_id == target.project_id,
        ),
        "StoryPath parent must belong to the same Project",
    )
    _require_related_row(
        connection,
        select(StoryPathChapter.id).where(
            StoryPathChapter.id == target.fork_path_chapter_id,
            StoryPathChapter.story_path_id == target.parent_path_id,
        ),
        "StoryPath fork PathChapter must belong to its parent path",
    )
    _require_related_row(
        connection,
        select(StoryNode.id).where(
            StoryNode.id == target.fork_checkpoint_node_id,
            StoryNode.project_id == target.project_id,
        ),
        "StoryPath fork checkpoint must belong to the same Project",
    )
    _require_related_row(
        connection,
        select(StateSnapshot.id).where(
            StateSnapshot.id == target.base_state_snapshot_id,
            StateSnapshot.project_id == target.project_id,
        ),
        "StoryPath base state must belong to the same Project",
    )
@event.listens_for(StateSnapshot, "before_insert")
def _validate_state_snapshot_parent(_mapper, connection, target) -> None:
    if target.parent_snapshot_id is not None:
        _require_related_row(
            connection,
            select(StateSnapshot.id).where(
                StateSnapshot.id == target.parent_snapshot_id,
                StateSnapshot.project_id == target.project_id,
            ),
            "StateSnapshot parent must belong to the same Project",
        )


@event.listens_for(ProjectRelease, "before_insert")
@event.listens_for(ProjectRelease, "before_update")
def _validate_project_release_sources(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(StoryBibleRevision.id).where(
            StoryBibleRevision.id == target.bible_revision_id,
            StoryBibleRevision.project_id == target.project_id,
        ),
        "Release Bible Revision must belong to the same Project",
    )
    _require_related_row(
        connection,
        select(OutlineRevision.id)
        .join(StoryPath, StoryPath.id == OutlineRevision.story_path_id)
        .where(
            OutlineRevision.id == target.outline_revision_id,
            OutlineRevision.project_id == target.project_id,
            StoryPath.project_id == target.project_id,
        ),
        "Release Outline Revision must belong to a StoryPath in the same Project",
    )


@event.listens_for(BranchEdge, "before_insert")
@event.listens_for(BranchEdge, "before_update")
def _validate_branch_edge_ownership(_mapper, connection, target) -> None:
    for node_id in (target.from_node_id, target.to_node_id):
        _require_related_row(
            connection,
            select(StoryNode.id).where(
                StoryNode.id == node_id,
                StoryNode.project_id == target.project_id,
            ),
            "BranchEdge nodes must belong to the same Project",
        )


@event.listens_for(ReadingSession, "before_insert")
@event.listens_for(ReadingSession, "before_update")
def _validate_reading_session_ownership(_mapper, connection, target) -> None:
    _require_related_row(
        connection,
        select(ProjectRelease.id).where(
            ProjectRelease.id == target.release_id,
            ProjectRelease.project_id == target.project_id,
        ),
        "ReadingSession Release must belong to the same Project",
    )
    if target.head_node_id is not None:
        _require_related_row(
            connection,
            select(StoryNode.id).where(
                StoryNode.id == target.head_node_id,
                StoryNode.project_id == target.project_id,
            ),
            "ReadingSession Head node must belong to the same Project",
        )
    if target.state_snapshot_id is not None:
        _require_related_row(
            connection,
            select(StateSnapshot.id).where(
                StateSnapshot.id == target.state_snapshot_id,
                StateSnapshot.project_id == target.project_id,
            ),
            "ReadingSession state must belong to the same Project",
        )
