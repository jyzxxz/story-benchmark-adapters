"""Drop uq_vngraph_revision_hash to allow LLM-produced revisions.

Revision ID: 0018_drop_uq_vngraph_revision_hash
Revises: 0017_backfill_asset_versions_and_tasks

The unique constraint (project_id, chapter_index, graph_hash) assumed
compile output was a deterministic function of input — same hash for same
input. The LLM generator path breaks that assumption: same input can
legitimately produce different graphs across runs.

Revision_no uniqueness (uq_vngraph_revision_no) is enough to keep rows
distinct; we no longer need graph_hash uniqueness.

Upgrade: drop the constraint.
Downgrade: re-add it (caller must first clean up duplicate graph_hash rows).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0018_drop_uq_vngraph_revision_hash"
down_revision = "0017_backfill_asset_versions_and_tasks"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "uq_vngraph_revision_hash"
TABLE = "vn_graph_revisions"


def upgrade() -> None:
    ctx = op.get_context()
    bind = op.get_bind()

    if bind.dialect.name == "sqlite":
        # SQLite does not support ALTER TABLE DROP CONSTRAINT cleanly.
        # The constraint only exists on Postgres in practice; on sqlite dev DBs
        # we leave the table as-is (sqlite ignores unknown constraints).
        return

    if ctx.as_sql:
        # Offline SQL generation: MockConnection has no inspector, so emit a
        # guarded ALTER ... IF EXISTS directly. PG accepts the IF EXISTS clause;
        # SQLite is handled by the dialect guard above.
        op.execute(
            f"ALTER TABLE {TABLE} DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
        )
        return

    inspector = sa.inspect(bind)
    existing_constraints = [c["name"] for c in inspector.get_unique_constraints(TABLE)]
    if CONSTRAINT_NAME in existing_constraints:
        op.drop_constraint(CONSTRAINT_NAME, TABLE, type_="unique")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        return

    inspector = sa.inspect(bind)
    existing_constraints = [c["name"] for c in inspector.get_unique_constraints(TABLE)]
    if CONSTRAINT_NAME not in existing_constraints:
        op.create_unique_constraint(
            CONSTRAINT_NAME,
            TABLE,
            ["project_id", "chapter_index", "graph_hash"],
        )
