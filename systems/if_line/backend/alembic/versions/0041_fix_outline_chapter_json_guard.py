"""Fix PostgreSQL JSON comparisons in the OutlineChapter update guard.

Revision ID: 0041_fix_outline_json_guard
Revises: 0040_unified_usage_ledger
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0041_fix_outline_json_guard"
down_revision = "0040_unified_usage_ledger"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_TABLE = "outline_revision_chapters"
_FUNCTION = "fn_0039_outline_chapter_update"
_TRIGGER = "trg_0039_outline_chapter_update_update"
_REQUIRED_TABLES = (
    _TABLE,
    "outline_revisions",
    "story_path_chapters",
)
_MESSAGE = "OutlineChapter is immutable except for one PathChapter reconciliation"


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_required_tables(bind) -> bool:
    if _is_offline():
        return True
    inspector = inspect(bind)
    return all(inspector.has_table(table_name) for table_name in _REQUIRED_TABLES)


def _outline_chapter_update_condition(*, cast_json_to_jsonb: bool) -> str:
    new_characters = (
        "CAST(NEW.characters AS JSONB)" if cast_json_to_jsonb else "NEW.characters"
    )
    old_characters = (
        "CAST(OLD.characters AS JSONB)" if cast_json_to_jsonb else "OLD.characters"
    )
    new_visual_keywords = (
        "CAST(NEW.visual_keywords AS JSONB)"
        if cast_json_to_jsonb
        else "NEW.visual_keywords"
    )
    old_visual_keywords = (
        "CAST(OLD.visual_keywords AS JSONB)"
        if cast_json_to_jsonb
        else "OLD.visual_keywords"
    )
    return (
        "NOT (OLD.story_path_chapter_id IS NULL "
        "AND NEW.story_path_chapter_id IS NOT NULL "
        "AND NEW.id IS NOT DISTINCT FROM OLD.id "
        "AND NEW.outline_revision_id IS NOT DISTINCT FROM OLD.outline_revision_id "
        "AND NEW.chapter_index IS NOT DISTINCT FROM OLD.chapter_index "
        "AND NEW.display_index IS NOT DISTINCT FROM OLD.display_index "
        "AND NEW.title IS NOT DISTINCT FROM OLD.title "
        "AND NEW.summary IS NOT DISTINCT FROM OLD.summary "
        "AND NEW.conflict IS NOT DISTINCT FROM OLD.conflict "
        f"AND {new_characters} IS NOT DISTINCT FROM {old_characters} "
        "AND NEW.scene IS NOT DISTINCT FROM OLD.scene "
        "AND NEW.emotion IS NOT DISTINCT FROM OLD.emotion "
        f"AND {new_visual_keywords} IS NOT DISTINCT FROM {old_visual_keywords} "
        "AND NEW.content_hash IS NOT DISTINCT FROM OLD.content_hash "
        "AND NEW.legacy_source_id IS NOT DISTINCT FROM OLD.legacy_source_id "
        "AND EXISTS (SELECT 1 FROM outline_revisions o "
        "JOIN story_path_chapters pc ON pc.story_path_id = o.story_path_id "
        "WHERE o.id = NEW.outline_revision_id "
        "AND pc.id = NEW.story_path_chapter_id))"
    )


def _replace_postgresql_guard(bind, *, cast_json_to_jsonb: bool) -> None:
    if bind.dialect.name != "postgresql" or not _has_required_tables(bind):
        return

    condition = _outline_chapter_update_condition(
        cast_json_to_jsonb=cast_json_to_jsonb
    )
    escaped_message = _MESSAGE.replace("'", "''")
    op.execute(
        sa.text(
            f"CREATE OR REPLACE FUNCTION {_FUNCTION}() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            f"IF {condition} THEN RAISE EXCEPTION '{escaped_message}' "
            "USING ERRCODE = '23514'; END IF; "
            "IF TG_OP = 'DELETE' THEN RETURN OLD; END IF; "
            "RETURN NEW; END; $$"
        )
    )
    op.execute(sa.text(f"DROP TRIGGER IF EXISTS {_TRIGGER} ON {_TABLE}"))
    op.execute(
        sa.text(
            f"CREATE TRIGGER {_TRIGGER} BEFORE UPDATE ON {_TABLE} "
            f"FOR EACH ROW EXECUTE FUNCTION {_FUNCTION}()"
        )
    )


def upgrade() -> None:
    _replace_postgresql_guard(op.get_bind(), cast_json_to_jsonb=True)


def downgrade() -> None:
    _replace_postgresql_guard(op.get_bind(), cast_json_to_jsonb=False)
