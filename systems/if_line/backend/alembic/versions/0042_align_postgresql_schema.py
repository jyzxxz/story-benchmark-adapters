"""Align PostgreSQL asset seed storage with the ORM model.

Revision ID: 0042_align_postgresql_schema
Revises: 0041_fix_outline_json_guard
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0042_align_postgresql_schema"
down_revision = "0041_fix_outline_json_guard"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_ASSET_VERSION_TABLE = "asset_versions"
_PUBLIC_CONTINUATION_INDEX = "ix_public_continuation_chapter"
_PUBLIC_CONTINUATION_TABLE = "reading_continuations"
_PUBLIC_CONTINUATION_COLUMNS = (
    "release_id",
    "chapter_number",
    "status",
    "confirmed_at",
)


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_table(bind, table_name: str) -> bool:
    return _is_offline() or inspect(bind).has_table(table_name)


def _asset_seed_is_bigint(bind) -> bool:
    if _is_offline():
        return False
    columns = {
        str(column["name"]): column["type"]
        for column in inspect(bind).get_columns(_ASSET_VERSION_TABLE)
    }
    return isinstance(columns.get("seed"), sa.BigInteger)


def _align_asset_version_seed(bind, *, use_bigint: bool) -> None:
    if bind.dialect.name != "postgresql" or not _has_table(
        bind, _ASSET_VERSION_TABLE
    ):
        return
    if not _is_offline() and _asset_seed_is_bigint(bind) == use_bigint:
        return
    if not use_bigint and not _is_offline():
        overflow = bind.execute(
            sa.text(
                "SELECT 1 FROM asset_versions "
                "WHERE seed < -2147483648 OR seed > 2147483647 LIMIT 1"
            )
        ).first()
        if overflow is not None:
            raise RuntimeError(
                "asset_versions.seed contains values outside the INTEGER range"
            )
    op.alter_column(
        _ASSET_VERSION_TABLE,
        "seed",
        existing_type=sa.Integer() if use_bigint else sa.BigInteger(),
        type_=sa.BigInteger() if use_bigint else sa.Integer(),
        existing_nullable=True,
    )


def _repair_public_continuation_index(bind) -> None:
    if not _has_table(bind, _PUBLIC_CONTINUATION_TABLE):
        return
    if _is_offline():
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS ix_public_continuation_chapter "
                "ON reading_continuations "
                "(release_id, chapter_number, status, confirmed_at)"
            )
        )
        return
    inspector = inspect(bind)
    columns = {
        str(column["name"])
        for column in inspector.get_columns(_PUBLIC_CONTINUATION_TABLE)
    }
    if not set(_PUBLIC_CONTINUATION_COLUMNS).issubset(columns):
        return
    indexes = {
        str(index["name"])
        for index in inspector.get_indexes(_PUBLIC_CONTINUATION_TABLE)
        if index.get("name")
    }
    if _PUBLIC_CONTINUATION_INDEX in indexes:
        return
    op.create_index(
        _PUBLIC_CONTINUATION_INDEX,
        _PUBLIC_CONTINUATION_TABLE,
        list(_PUBLIC_CONTINUATION_COLUMNS),
        unique=False,
    )


def upgrade() -> None:
    bind = op.get_bind()
    _align_asset_version_seed(bind, use_bigint=True)
    _repair_public_continuation_index(bind)


def downgrade() -> None:
    # Migration 0009 owns the continuation index, so 0042 must leave it intact.
    _align_asset_version_seed(op.get_bind(), use_bigint=False)
