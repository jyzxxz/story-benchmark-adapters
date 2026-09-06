"""Persist idempotency identity for atomic publication commands.

Revision ID: 0034_release_publication_idempotency
Revises: 0033_immutable_vn_graph_manifest
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0034_release_publication_idempotency"
down_revision = "0033_immutable_vn_graph_manifest"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_releases", recreate="always") as batch:
            batch.add_column(
                sa.Column("publication_idempotency_key", sa.String(255))
            )
            batch.add_column(sa.Column("publication_request_hash", sa.String(64)))
            batch.create_unique_constraint(
                "uq_project_release_publication_idempotency",
                ["project_id", "publication_idempotency_key"],
            )
        return

    op.add_column(
        "project_releases",
        sa.Column("publication_idempotency_key", sa.String(255)),
    )
    op.add_column(
        "project_releases",
        sa.Column("publication_request_hash", sa.String(64)),
    )
    op.create_unique_constraint(
        "uq_project_release_publication_idempotency",
        "project_releases",
        ["project_id", "publication_idempotency_key"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_releases", recreate="always") as batch:
            batch.drop_constraint(
                "uq_project_release_publication_idempotency",
                type_="unique",
            )
            batch.drop_column("publication_request_hash")
            batch.drop_column("publication_idempotency_key")
        return

    op.drop_constraint(
        "uq_project_release_publication_idempotency",
        "project_releases",
        type_="unique",
    )
    op.drop_column("project_releases", "publication_request_hash")
    op.drop_column("project_releases", "publication_idempotency_key")
