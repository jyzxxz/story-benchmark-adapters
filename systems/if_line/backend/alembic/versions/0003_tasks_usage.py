"""Durable generation tasks, outbox and usage ledger.

Revision ID: 0003_tasks_usage
Revises: 0002_runtime_foundation
"""
from __future__ import annotations

from alembic import op


revision = "0003_tasks_usage"
down_revision = "0002_runtime_foundation"
branch_labels = None
depends_on = None


TABLES = (
    "generation_tasks",
    "generation_task_dependencies",
    "task_events",
    "outbox_events",
    "usage_reservations",
    "usage_ledger_entries",
    "provider_usage_records",
)


def _table(name: str):
    # See alembic/README.md: the exact schema emitted from these model-backed
    # migrations is protected by a hard-coded migration fingerprint test.
    from app.orm_base import Base
    import app.models  # noqa: F401 -- register additive v2 metadata

    return Base.metadata.tables[name]


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        _table(name).create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for name in reversed(TABLES):
        _table(name).drop(bind=bind, checkfirst=False)
