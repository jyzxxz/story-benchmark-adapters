"""Publish existing system-generated continuation media.

Revision ID: 0010_publish_continuation_media
Revises: 0009_public_continuation_tree
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op


revision = "0010_publish_continuation_media"
down_revision = "0009_public_continuation_tree"
branch_labels = None
depends_on = None


def _json_value(value: object, fallback: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return fallback


def upgrade() -> None:
    if op.get_context().as_sql:
        # This revision only updates existing rows. Offline schema output has
        # no database state to inspect and therefore no deterministic updates.
        return

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT rc.frozen_asset_version_ids
            FROM reading_continuations AS rc
            WHERE rc.status = 'confirmed'
            """
        )
    ).mappings()
    version_ids: set[str] = set()
    for row in rows:
        raw_ids = _json_value(row["frozen_asset_version_ids"], [])
        if isinstance(raw_ids, list):
            version_ids.update(str(item) for item in raw_ids if item)

    for version_id in version_ids:
        version = bind.execute(
            sa.text(
                """
                SELECT storage_object_id, rights_metadata
                FROM asset_versions
                WHERE id = :version_id
                """
            ),
            {"version_id": version_id},
        ).mappings().first()
        if not version:
            continue
        rights = _json_value(version["rights_metadata"], {})
        if isinstance(rights, dict) and rights.get("origin") == "upload":
            continue
        bind.execute(
            sa.text(
                """
                UPDATE storage_objects
                SET visibility = 'public', status = 'active'
                WHERE id = :storage_object_id
                """
            ),
            {"storage_object_id": version["storage_object_id"]},
        )


def downgrade() -> None:
    # Public media may already be referenced elsewhere, so visibility is not
    # reduced automatically on downgrade.
    pass
