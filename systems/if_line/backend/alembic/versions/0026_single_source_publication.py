"""Add the single source of truth for project publication state.

Revision ID: 0026_single_source_publication
Revises: 0025_revision_keyed_artifact_heads
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0026_single_source_publication"
down_revision = "0025_revision_keyed_artifact_heads"
branch_labels = None
depends_on = None


TABLES = ("project_publications",)

_LEGACY_STATUS_TABLE = "_alembic_0026_release_status"


def _create_legacy_status_snapshot() -> None:
    op.create_table(
        _LEGACY_STATUS_TABLE,
        sa.Column("release_id", sa.String(36), primary_key=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        sa.text(
            f"INSERT INTO {_LEGACY_STATUS_TABLE} (release_id, withdrawn_at) "
            "SELECT id, withdrawn_at FROM project_releases WHERE status = 'draft'"
        )
    )


def _create_project_publications() -> None:
    op.create_table(
        "project_publications",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("active_release_id", sa.String(36)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id", "active_release_id"],
            ["project_releases.project_id", "project_releases.id"],
            name="fk_project_publication_active_release",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "active_release_id",
            name="uq_project_publication_active_release",
        ),
        sa.CheckConstraint(
            "(active_release_id IS NULL AND published_at IS NULL) OR "
            "(active_release_id IS NOT NULL AND published_at IS NOT NULL)",
            name="ck_project_publication_activation",
        ),
        sa.CheckConstraint(
            "lock_version > 0",
            name="ck_project_publication_lock_version",
        ),
    )


def _upgrade_release_constraints(bind) -> None:
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_releases", recreate="always") as batch:
            batch.drop_constraint("ck_project_release_status", type_="check")
            batch.create_check_constraint(
                "ck_project_release_status",
                "status IN ('published','superseded','withdrawn')",
            )
            batch.create_unique_constraint(
                "uq_project_release_project_id_id",
                ["project_id", "id"],
            )
        return

    op.drop_constraint(
        "ck_project_release_status",
        "project_releases",
        type_="check",
    )
    op.create_check_constraint(
        "ck_project_release_status",
        "project_releases",
        "status IN ('published','superseded','withdrawn')",
    )
    op.create_unique_constraint(
        "uq_project_release_project_id_id",
        "project_releases",
        ["project_id", "id"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    _create_legacy_status_snapshot()
    op.execute(
        sa.text(
            "UPDATE project_releases "
            "SET status = 'withdrawn', withdrawn_at = COALESCE(withdrawn_at, created_at) "
            "WHERE status = 'draft'"
        )
    )
    op.add_column(
        "project_releases",
        sa.Column("authoring_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "project_releases",
        sa.Column("release_notes", sa.Text(), nullable=True),
    )
    _upgrade_release_constraints(bind)
    _create_project_publications()


def _drop_target_release_constraints(bind) -> None:
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_releases", recreate="always") as batch:
            batch.drop_constraint("ck_project_release_status", type_="check")
            batch.drop_constraint(
                "uq_project_release_project_id_id",
                type_="unique",
            )
        return

    op.drop_constraint(
        "ck_project_release_status",
        "project_releases",
        type_="check",
    )
    op.drop_constraint(
        "uq_project_release_project_id_id",
        "project_releases",
        type_="unique",
    )


def _restore_legacy_release_schema(bind) -> None:
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("project_releases", recreate="always") as batch:
            batch.drop_column("release_notes")
            batch.drop_column("authoring_fingerprint")
            batch.create_check_constraint(
                "ck_project_release_status",
                "status IN ('draft','published','withdrawn')",
            )
        return

    op.drop_column("project_releases", "release_notes")
    op.drop_column("project_releases", "authoring_fingerprint")
    op.create_check_constraint(
        "ck_project_release_status",
        "project_releases",
        "status IN ('draft','published','withdrawn')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_table("project_publications")
    _drop_target_release_constraints(bind)
    op.execute(
        sa.text(
            "UPDATE project_releases SET status = 'published' "
            "WHERE status = 'superseded'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE project_releases "
            "SET status = 'draft', withdrawn_at = ("
            f"SELECT legacy.withdrawn_at FROM {_LEGACY_STATUS_TABLE} AS legacy "
            "WHERE legacy.release_id = project_releases.id"
            ") WHERE id IN ("
            f"SELECT release_id FROM {_LEGACY_STATUS_TABLE}"
            ")"
        )
    )
    _restore_legacy_release_schema(bind)
    op.drop_table(_LEGACY_STATUS_TABLE)
