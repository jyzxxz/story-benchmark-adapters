"""Key Script and VNGraph heads by immutable source revisions.

Revision ID: 0025_revision_keyed_artifact_heads
Revises: 0024_candidate_set_versioning
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = "0025_revision_keyed_artifact_heads"
down_revision = "0024_candidate_set_versioning"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_ID_NAMESPACE = uuid.UUID("7df2fc6f-d5c2-48db-bdf6-21dcfcb579bb")
_HEADS = (
    {
        "table": "chapter_script_heads",
        "source_column": "chapter_revision_id",
        "source_table": "chapter_revisions",
        "source_fk": "fk_chapter_script_heads_chapter_revision_id",
        "source_unique": "uq_chapter_script_head_chapter_revision",
        "lock_check": "ck_chapter_script_head_lock_version",
        "legacy_index": "ix_chapter_script_head_legacy_lookup",
    },
    {
        "table": "vn_graph_heads",
        "source_column": "script_revision_id",
        "source_table": "chapter_script_revisions",
        "source_fk": "fk_vn_graph_heads_script_revision_id",
        "source_unique": "uq_vn_graph_head_script_revision",
        "lock_check": "ck_vn_graph_head_lock_version",
        "legacy_index": "ix_vn_graph_head_legacy_lookup",
    },
)


def _postgres_uuid_expression(table: str) -> str:
    digest = f"md5('if-line:{table}:' || project_id::text || ':' || chapter_index::text)"
    return (
        f"substr({digest}, 1, 8) || '-' || substr({digest}, 9, 4) || '-' || "
        f"substr({digest}, 13, 4) || '-' || substr({digest}, 17, 4) || '-' || "
        f"substr({digest}, 21, 12)"
    )


def _populate_head_ids(bind, table: str, *, offline: bool) -> None:
    if offline:
        op.execute(sa.text(f"UPDATE {table} SET id = {_postgres_uuid_expression(table)}"))
        return

    rows = bind.execute(
        sa.text(f"SELECT project_id, chapter_index FROM {table}")
    ).mappings().all()
    for row in rows:
        stable_id = str(
            uuid.uuid5(
                _ID_NAMESPACE,
                f"{table}:{row['project_id']}:{row['chapter_index']}",
            )
        )
        bind.execute(
            sa.text(
                f"UPDATE {table} SET id = :id "
                "WHERE project_id = :project_id AND chapter_index = :chapter_index"
            ),
            {
                "id": stable_id,
                "project_id": row["project_id"],
                "chapter_index": row["chapter_index"],
            },
        )


def _add_source_column(bind, spec: dict[str, str]) -> None:
    table = spec["table"]
    source_column = spec["source_column"]
    if bind.dialect.name == "sqlite":
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ADD COLUMN {source_column} VARCHAR(36) "
                f"CONSTRAINT {spec['source_fk']} REFERENCES {spec['source_table']} (id) "
                "ON DELETE CASCADE"
            )
        )
        return

    op.add_column(
        table,
        sa.Column(
            source_column,
            sa.String(36),
            sa.ForeignKey(
                f"{spec['source_table']}.id",
                name=spec["source_fk"],
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )


def _upgrade_sqlite_head(spec: dict[str, str]) -> None:
    table = spec["table"]
    naming_convention = {
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
    }
    with op.batch_alter_table(
        table,
        recreate="always",
        naming_convention=naming_convention,
    ) as batch:
        reflected_source_fk = (
            f"fk_{table}_{spec['source_column']}_{spec['source_table']}"
        )
        batch.drop_constraint(reflected_source_fk, type_="foreignkey")
        batch.drop_constraint(f"pk_{table}", type_="primary")
        batch.alter_column("id", existing_type=sa.String(36), nullable=False)
        batch.create_primary_key(f"pk_{table}", ["id"])
        batch.create_foreign_key(
            spec["source_fk"],
            spec["source_table"],
            [spec["source_column"]],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_unique_constraint(spec["source_unique"], [spec["source_column"]])
        batch.create_check_constraint(spec["lock_check"], "lock_version > 0")


def _upgrade_postgresql_head(spec: dict[str, str]) -> None:
    table = spec["table"]
    op.alter_column(table, "id", existing_type=sa.String(36), nullable=False)
    op.drop_constraint(f"{table}_pkey", table, type_="primary")
    op.create_primary_key(f"pk_{table}", table, ["id"])
    op.create_unique_constraint(spec["source_unique"], table, [spec["source_column"]])
    op.create_check_constraint(spec["lock_check"], table, "lock_version > 0")


def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    for spec in _HEADS:
        table = spec["table"]
        op.add_column(table, sa.Column("id", sa.String(36), nullable=True))
        _add_source_column(bind, spec)
        _populate_head_ids(bind, table, offline=offline)
        if bind.dialect.name == "sqlite":
            _upgrade_sqlite_head(spec)
        else:
            _upgrade_postgresql_head(spec)
        op.create_index(
            spec["legacy_index"],
            table,
            ["project_id", "chapter_index"],
        )


def _downgrade_sqlite_head(spec: dict[str, str]) -> None:
    table = spec["table"]
    naming_convention = {
        "pk": "pk_%(table_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
    }
    with op.batch_alter_table(
        table,
        recreate="always",
        naming_convention=naming_convention,
    ) as batch:
        batch.drop_constraint(spec["lock_check"], type_="check")
        batch.drop_constraint(spec["source_unique"], type_="unique")
        batch.drop_constraint(spec["source_fk"], type_="foreignkey")
        batch.drop_constraint(f"pk_{table}", type_="primary")
        batch.create_primary_key(f"pk_{table}", ["project_id", "chapter_index"])
        batch.drop_column(spec["source_column"])
        batch.drop_column("id")


def _downgrade_postgresql_head(spec: dict[str, str]) -> None:
    table = spec["table"]
    op.drop_constraint(spec["lock_check"], table, type_="check")
    op.drop_constraint(spec["source_unique"], table, type_="unique")
    op.drop_constraint(spec["source_fk"], table, type_="foreignkey")
    op.drop_constraint(f"pk_{table}", table, type_="primary")
    op.create_primary_key(f"{table}_pkey", table, ["project_id", "chapter_index"])
    op.drop_column(table, spec["source_column"])
    op.drop_column(table, "id")


def downgrade() -> None:
    bind = op.get_bind()
    for spec in reversed(_HEADS):
        op.drop_index(spec["legacy_index"], table_name=spec["table"])
        if bind.dialect.name == "sqlite":
            _downgrade_sqlite_head(spec)
        else:
            _downgrade_postgresql_head(spec)
