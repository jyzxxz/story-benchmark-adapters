"""Chapter Script IR, semantic resource slots, and VNGraph source linkage.

Revision ID: 0020_chapter_script_semantic_pipeline
Revises: 0019_change_assets_seed_to_bigint
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0020_chapter_script_semantic_pipeline"
down_revision = "0019_change_assets_seed_to_bigint"
branch_labels = None
depends_on = None

TABLES = (
    "chapter_script_revisions",
    "chapter_script_heads",
    "script_resource_slots",
)


def _table(name: str):
    from app.orm_base import Base
    import app.models  # noqa: F401
    import app.models_v2  # noqa: F401

    return Base.metadata.tables[name]


def _load_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
            return loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _load_datetime(value):
    if not isinstance(value, str):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.utcnow()


def _create_historical_chapter_script_heads() -> None:
    op.create_table(
        "chapter_script_heads",
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("chapter_index", sa.Integer(), primary_key=True),
        sa.Column(
            "current_revision_id",
            sa.String(36),
            sa.ForeignKey("chapter_script_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    bind = op.get_bind()
    offline = op.get_context().as_sql
    for name in TABLES:
        if name == "chapter_script_heads":
            _create_historical_chapter_script_heads()
        else:
            _table(name).create(bind=bind, checkfirst=not offline)

    script_revision_column = sa.Column(
        "script_revision_id",
        sa.String(36),
        sa.ForeignKey(
            "chapter_script_revisions.id",
            name="fk_vn_graph_revisions_script_revision_id",
            ondelete="RESTRICT",
        ),
        nullable=True,
    )
    if offline:
        op.add_column("vn_graph_revisions", script_revision_column)
        op.create_index(
            "ix_vn_graph_revisions_script_revision_id",
            "vn_graph_revisions",
            ["script_revision_id"],
        )
        return

    inspector = inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("vn_graph_revisions")}
    if "script_revision_id" not in columns:
        if bind.dialect.name == "sqlite":
            # A nullable REFERENCES column can be added directly. Avoiding a
            # batch rebuild also keeps reflected constraint order deterministic.
            op.execute(
                "ALTER TABLE vn_graph_revisions ADD COLUMN script_revision_id VARCHAR(36) "
                "CONSTRAINT fk_vn_graph_revisions_script_revision_id "
                "REFERENCES chapter_script_revisions (id) ON DELETE RESTRICT"
            )
        else:
            op.add_column("vn_graph_revisions", script_revision_column)
    indexes = {item["name"] for item in inspect(bind).get_indexes("vn_graph_revisions")}
    if "ix_vn_graph_revisions_script_revision_id" not in indexes:
        op.create_index(
            "ix_vn_graph_revisions_script_revision_id",
            "vn_graph_revisions",
            ["script_revision_id"],
        )

    _backfill(bind)


def _backfill(bind) -> None:
    from app.application.hashing import content_hash
    from app.services.chapter_script_ir import (
        SCRIPT_IR_SCHEMA_VERSION,
        build_script_ir,
    )

    script_table = sa.Table(
        "chapter_script_revisions",
        sa.MetaData(),
        autoload_with=bind,
    )
    rows = bind.execute(
        sa.text(
            """
            SELECT r.id, r.project_id, r.chapter_index, r.bible_revision_id,
                   r.outline_revision_id, r.content_hash, r.content, r.created_by,
                   r.created_at, b.content_json AS bible_content
            FROM chapter_revisions r
            JOIN story_bible_revisions b ON b.id = r.bible_revision_id
            WHERE r.content IS NOT NULL AND r.content <> ''
            ORDER BY r.project_id, r.chapter_index, r.revision_no
            """
        )
    ).mappings().all()
    by_chapter_revision: dict[str, str] = {}
    next_revision_no: dict[tuple[int, int], int] = {}
    for row in rows:
        existing = bind.execute(
            sa.text(
                "SELECT id FROM chapter_script_revisions "
                "WHERE chapter_revision_id = :chapter_revision_id LIMIT 1"
            ),
            {"chapter_revision_id": row["id"]},
        ).first()
        if existing:
            by_chapter_revision[str(row["id"])] = str(existing[0])
            continue
        bible = _load_json(row["bible_content"])
        # script-ir-v3 契约：无 LLM 标注的存量正文按整段 narration 定位回填
        script_ir = build_script_ir(
            chapter_revision_id=str(row["id"]),
            chapter_content=str(row["content"]),
            chapter_content_hash=str(row["content_hash"]),
            bible_revision_id=str(row["bible_revision_id"]),
            outline_revision_id=str(row["outline_revision_id"]),
            characters=bible.get("characters") or [],
            llm_output={"segments": [{"text": str(row["content"])}]},
        )
        chapter_key = (int(row["project_id"]), int(row["chapter_index"]))
        if chapter_key not in next_revision_no:
            next_revision_no[chapter_key] = int(
                bind.execute(
                    sa.text(
                        "SELECT COALESCE(MAX(revision_no), 0) FROM chapter_script_revisions "
                        "WHERE project_id = :project_id AND chapter_index = :chapter_index"
                    ),
                    {"project_id": chapter_key[0], "chapter_index": chapter_key[1]},
                ).scalar()
                or 0
            )
        next_revision_no[chapter_key] += 1
        script_id = str(uuid.uuid4())
        bind.execute(
            script_table.insert().values(
                id=script_id,
                project_id=row["project_id"],
                chapter_index=row["chapter_index"],
                chapter_revision_id=row["id"],
                bible_revision_id=row["bible_revision_id"],
                outline_revision_id=row["outline_revision_id"],
                parent_revision_id=None,
                revision_no=next_revision_no[chapter_key],
                source_hash=content_hash(
                    {
                        "origin": "chapter-revision-backfill",
                        "chapter_revision_id": str(row["id"]),
                        "content_hash": str(row["content_hash"]),
                        "schema_version": SCRIPT_IR_SCHEMA_VERSION,
                    }
                ),
                script_hash=content_hash(script_ir),
                script_json=script_ir,
                coverage_json=script_ir["coverage"],
                schema_version=SCRIPT_IR_SCHEMA_VERSION,
                generator_version="deterministic-backfill-v1",
                status="complete",
                generation_task_id=None,
                legacy_source_table="chapter_revisions",
                legacy_source_id=str(row["id"]),
                created_by=row["created_by"],
                created_at=_load_datetime(row["created_at"]),
            )
        )
        by_chapter_revision[str(row["id"])] = script_id

    heads = bind.execute(
        sa.text(
            "SELECT project_id, chapter_index, current_revision_id, updated_at "
            "FROM chapter_heads"
        )
    ).mappings().all()
    head_table = sa.Table(
        "chapter_script_heads",
        sa.MetaData(),
        autoload_with=bind,
    )
    for head in heads:
        script_id = by_chapter_revision.get(str(head["current_revision_id"]))
        if not script_id:
            continue
        exists = bind.execute(
            sa.text(
                "SELECT 1 FROM chapter_script_heads "
                "WHERE project_id = :project_id AND chapter_index = :chapter_index"
            ),
            {"project_id": head["project_id"], "chapter_index": head["chapter_index"]},
        ).first()
        if not exists:
            bind.execute(
                head_table.insert().values(
                    project_id=head["project_id"],
                    chapter_index=head["chapter_index"],
                    current_revision_id=script_id,
                    lock_version=1,
                    updated_at=_load_datetime(head["updated_at"]),
                )
            )

    for chapter_revision_id, script_id in by_chapter_revision.items():
        bind.execute(
            sa.text(
                "UPDATE vn_graph_revisions SET script_revision_id = :script_id "
                "WHERE chapter_revision_id = :chapter_revision_id "
                "AND script_revision_id IS NULL"
            ),
            {"script_id": script_id, "chapter_revision_id": chapter_revision_id},
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite cannot drop a column while its reflected foreign key still
        # references it. Batch mode rebuilds the table without both objects.
        with op.batch_alter_table("vn_graph_revisions") as batch:
            batch.drop_index("ix_vn_graph_revisions_script_revision_id")
            batch.drop_column("script_revision_id")
    else:
        op.drop_index("ix_vn_graph_revisions_script_revision_id", table_name="vn_graph_revisions")
        op.drop_column("vn_graph_revisions", "script_revision_id")
    for name in ("script_resource_slots", "chapter_script_heads", "chapter_script_revisions"):
        _table(name).drop(bind=bind, checkfirst=False)
