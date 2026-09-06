"""Add optimistic concurrency to Script resource slots.

Revision ID: 0035_script_resource_slot_lock
Revises: 0034_release_publication_idempotency
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0035_script_resource_slot_lock"
down_revision = "0034_release_publication_idempotency"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    columns: set[str] = set()
    checks: set[str | None] = set()
    if not offline:
        inspector = inspect(bind)
        columns = {item["name"] for item in inspector.get_columns("script_resource_slots")}
        checks = {
            item["name"]
            for item in inspector.get_check_constraints("script_resource_slots")
        }
        if (
            "lock_version" in columns
            and "ck_script_resource_slot_lock_version" in checks
        ):
            return

    if bind.dialect.name == "sqlite":
        if offline or "lock_version" not in columns:
            op.execute(
                "ALTER TABLE script_resource_slots "
                "ADD COLUMN lock_version INTEGER NOT NULL DEFAULT 1 "
                "CONSTRAINT ck_script_resource_slot_lock_version "
                "CHECK (lock_version > 0)"
            )
        elif "ck_script_resource_slot_lock_version" not in checks:
            with op.batch_alter_table(
                "script_resource_slots",
                recreate="always",
            ) as batch:
                batch.create_check_constraint(
                    "ck_script_resource_slot_lock_version",
                    "lock_version > 0",
                )
        return

    if offline or "lock_version" not in columns:
        op.add_column(
            "script_resource_slots",
            sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )
    if offline or "ck_script_resource_slot_lock_version" not in checks:
        op.create_check_constraint(
            "ck_script_resource_slot_lock_version",
            "script_resource_slots",
            "lock_version > 0",
        )


def downgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    if not offline:
        inspector = inspect(bind)
        columns = {item["name"] for item in inspector.get_columns("script_resource_slots")}
        if "lock_version" not in columns:
            return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("script_resource_slots", recreate="always") as batch:
            batch.drop_constraint(
                "ck_script_resource_slot_lock_version",
                type_="check",
            )
            batch.drop_column("lock_version")
        return

    op.drop_constraint(
        "ck_script_resource_slot_lock_version",
        "script_resource_slots",
        type_="check",
    )
    op.drop_column("script_resource_slots", "lock_version")
