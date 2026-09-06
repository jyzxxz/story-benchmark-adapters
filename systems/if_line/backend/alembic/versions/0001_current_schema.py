"""Current legacy schema baseline.

Revision ID: 0001_current_schema
Revises: none
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_current_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("avatar_url", sa.String(500)),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("quota_total", sa.Integer(), nullable=False),
        sa.Column("quota_daily", sa.Integer(), nullable=False),
        sa.Column("quota_used_total", sa.Integer(), nullable=False),
        sa.Column("quota_used_daily", sa.Integer(), nullable=False),
        sa.Column("quota_reset_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_users_id", "users", ["id"])
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("characters", sa.JSON()),
        sa.Column("story_start", sa.Text(), nullable=False),
        sa.Column("story_end", sa.Text(), nullable=False),
        sa.Column("style", sa.String(100)),
        sa.Column("pace", sa.String(50)),
        sa.Column("extra_requirements", sa.Text()),
        sa.Column("status", sa.String(50)),
        sa.Column("visibility", sa.String(20), nullable=False),
        sa.Column("is_draft", sa.Boolean(), nullable=False),
        sa.Column("published_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("outline_approved_at", sa.DateTime()),
        sa.Column("first_chapter_generated_at", sa.DateTime()),
        sa.Column("total_chapter_generate_time", sa.Float()),
        sa.Column("total_asset_generate_time", sa.Float()),
    )
    op.create_index("ix_projects_id", "projects", ["id"])
    op.create_index("ix_projects_owner_id", "projects", ["owner_id"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("session_token_hash", sa.String(128), nullable=False),
        sa.Column("device_id", sa.String(128)),
        sa.Column("user_agent", sa.String(500)),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("last_seen_at", sa.DateTime()),
        sa.Column("revoked_at", sa.DateTime()),
    )
    op.create_index("ix_user_sessions_id", "user_sessions", ["id"])
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_session_token_hash", "user_sessions", ["session_token_hash"], unique=True)
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "story_bibles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("worldview", sa.Text()),
        sa.Column("characters", sa.JSON()),
        sa.Column("character_relations", sa.Text()),
        sa.Column("main_conflict", sa.Text()),
        sa.Column("emotional_line", sa.Text()),
        sa.Column("style_rules", sa.Text()),
        sa.Column("ending_constraints", sa.Text()),
        sa.Column("forbidden_points", sa.JSON()),
        sa.Column("writing_notes", sa.JSON()),
        sa.Column("raw_json", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_story_bibles_id", "story_bibles", ["id"])

    op.create_table(
        "chapter_outlines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200)),
        sa.Column("summary", sa.Text()),
        sa.Column("conflict", sa.Text()),
        sa.Column("characters", sa.JSON()),
        sa.Column("scene", sa.String(200)),
        sa.Column("emotion", sa.String(100)),
        sa.Column("visual_keywords", sa.JSON()),
        sa.Column("status", sa.String(50)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_chapter_outlines_id", "chapter_outlines", ["id"])

    op.create_table(
        "chapter_contents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("version", sa.Integer()),
        sa.Column("status", sa.String(50)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_chapter_contents_id", "chapter_contents", ["id"])

    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_index", sa.Integer()),
        sa.Column("asset_type", sa.String(50)),
        sa.Column("target_name", sa.String(200)),
        sa.Column("prompt", sa.Text()),
        sa.Column("image_url", sa.String(500)),
        sa.Column("status", sa.String(50)),
        sa.Column("character_id", sa.String(100)),
        sa.Column("emotion", sa.String(50)),
        sa.Column("outfit", sa.String(100)),
        sa.Column("pose", sa.String(100)),
        sa.Column("scene_location", sa.String(200)),
        sa.Column("mood", sa.String(50)),
        sa.Column("event_name", sa.String(200)),
        sa.Column("seed", sa.Integer()),
        sa.Column("generation_params", sa.JSON()),
        sa.Column("generation_time", sa.Float()),
        sa.Column("processed", sa.Integer()),
        sa.Column("genre", sa.String(50)),
        sa.Column("description_cn", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_assets_id", "assets", ["id"])

    op.create_table(
        "vn_graphs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("chapter_index", sa.Integer(), nullable=False),
        sa.Column("graph_json", sa.JSON()),
        sa.Column("status", sa.String(50)),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_vn_graphs_id", "vn_graphs", ["id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("current_step", sa.String(100)),
        sa.Column("input_json", sa.JSON()),
        sa.Column("output_json", sa.JSON()),
        sa.Column("status", sa.String(50)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_workflow_runs_id", "workflow_runs", ["id"])

    op.create_table(
        "generation_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("generation_type", sa.String(50)),
        sa.Column("asset_type", sa.String(50)),
        sa.Column("chapter_index", sa.Integer()),
        sa.Column("asset_id", sa.Integer()),
        sa.Column("target_name", sa.String(200)),
        sa.Column("variation_info", sa.JSON()),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("status", sa.String(50)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_generation_stats_id", "generation_stats", ["id"])

    op.create_table(
        "project_likes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_like_user"),
    )
    op.create_index("ix_project_likes_id", "project_likes", ["id"])
    op.create_index("ix_project_likes_project_id", "project_likes", ["project_id"])
    op.create_index("ix_project_likes_user_id", "project_likes", ["user_id"])

    op.create_table(
        "project_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
    )
    op.create_index("ix_project_comments_id", "project_comments", ["id"])
    op.create_index("ix_project_comments_project_id", "project_comments", ["project_id"])
    op.create_index("ix_project_comments_user_id", "project_comments", ["user_id"])

    op.create_table(
        "user_notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("comment_id", sa.Integer(), sa.ForeignKey("project_comments.id")),
        sa.Column("notification_type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("payload", sa.JSON()),
        sa.Column("read_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_user_notifications_id", "user_notifications", ["id"])
    op.create_index("ix_user_notifications_user_id", "user_notifications", ["user_id"])
    op.create_index("ix_user_notifications_actor_user_id", "user_notifications", ["actor_user_id"])
    op.create_index("ix_user_notifications_project_id", "user_notifications", ["project_id"])
    op.create_index("ix_user_notifications_comment_id", "user_notifications", ["comment_id"])

    op.create_table(
        "api_usage_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id")),
        sa.Column("event_metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime()),
    )
    op.create_index("ix_api_usage_events_id", "api_usage_events", ["id"])
    op.create_index("ix_api_usage_events_user_id", "api_usage_events", ["user_id"])
    op.create_index("ix_api_usage_events_project_id", "api_usage_events", ["project_id"])

    op.create_table(
        "character_voice_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("character_id", sa.String(100), nullable=False),
        sa.Column("character_name", sa.String(200)),
        sa.Column("vcn", sa.String(100), nullable=False),
        sa.Column("speed", sa.Integer(), nullable=False),
        sa.Column("pitch", sa.Integer(), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=False),
        sa.Column("emotion", sa.String(50), nullable=False),
        sa.Column("bound_at", sa.DateTime()),
        sa.UniqueConstraint("project_id", "character_id", name="uq_pj_char"),
        sa.UniqueConstraint("project_id", "vcn", "speed", "pitch", name="uq_pj_voice_slot"),
    )
    op.create_index("ix_character_voice_bindings_id", "character_voice_bindings", ["id"])
    op.create_index("ix_character_voice_bindings_project_id", "character_voice_bindings", ["project_id"])


def downgrade() -> None:
    for table_name in (
        "character_voice_bindings",
        "api_usage_events",
        "user_notifications",
        "project_comments",
        "project_likes",
        "generation_stats",
        "workflow_runs",
        "vn_graphs",
        "assets",
        "chapter_contents",
        "chapter_outlines",
        "story_bibles",
        "user_sessions",
        "projects",
        "users",
    ):
        op.drop_table(table_name)
