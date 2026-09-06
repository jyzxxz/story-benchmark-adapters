"""Freeze VNGraph bindings and version editable voice lines.

Revision ID: 0033_immutable_vn_graph_manifest
Revises: 0032_vn_graph_script_revision_skeleton
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Mapping

import sqlalchemy as sa
from alembic import op


revision = "0033_immutable_vn_graph_manifest"
down_revision = "0032_vn_graph_script_revision_skeleton"
branch_labels = None
depends_on = None


TABLES = ("voice_line_versions",)


def _content_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _voice_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "chapter_revision_id": row["chapter_revision_id"],
        "occurrence_id": row["occurrence_id"],
        "order_index": row["order_index"],
        "kind": row["kind"],
        "text": row["text"],
        "speaker_character_id": row["speaker_character_id"],
        "speaker_name": row["speaker_name"],
        "emotion": row["emotion"],
        "voice_profile_id": row["voice_profile_id"],
        "voice_profile_version": row["voice_profile_version"],
        "audio_asset_version_id": row["audio_asset_version_id"],
        "status": row["status"],
    }


def _legacy_binding_manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "manifest_version": "legacy-vngraph-v1",
        "graph_revision_id": row["id"],
        "project_id": row["project_id"],
        "chapter_index": row["chapter_index"],
        "chapter_revision": {"id": row["chapter_revision_id"]},
        "script_revision": {"id": row["script_revision_id"]},
        "asset_bindings": [],
        "voice_line_versions": [],
        "versions": {
            "schema": row["schema_version"],
            "compiler": row["compiler_version"],
            "tachi_policy": row["tachi_policy_version"],
        },
        "legacy_source_manifest_hash": row["source_manifest_hash"],
    }


def _create_voice_line_versions() -> None:
    op.create_table(
        "voice_line_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "voice_line_id",
            sa.String(36),
            sa.ForeignKey("voice_lines.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "chapter_revision_id",
            sa.String(36),
            sa.ForeignKey("chapter_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("content_json", sa.JSON(), nullable=False),
        sa.Column(
            "audio_asset_version_id",
            sa.String(36),
            sa.ForeignKey("asset_versions.id", ondelete="RESTRICT"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "voice_line_id", "version_no", name="uq_voice_line_version_no"
        ),
        sa.UniqueConstraint(
            "voice_line_id", "content_hash", name="uq_voice_line_version_content"
        ),
    )
    op.create_index(
        "ix_voice_line_version_chapter_created",
        "voice_line_versions",
        ["chapter_revision_id", "created_at"],
    )


def _backfill_online(bind) -> None:
    voice_lines = sa.table(
        "voice_lines",
        sa.column("id", sa.String(36)),
        sa.column("chapter_revision_id", sa.String(36)),
        sa.column("occurrence_id", sa.String(128)),
        sa.column("order_index", sa.Integer()),
        sa.column("kind", sa.String(24)),
        sa.column("text", sa.Text()),
        sa.column("speaker_character_id", sa.String(100)),
        sa.column("speaker_name", sa.String(200)),
        sa.column("emotion", sa.String(64)),
        sa.column("voice_profile_id", sa.String(36)),
        sa.column("voice_profile_version", sa.Integer()),
        sa.column("audio_asset_version_id", sa.String(36)),
        sa.column("status", sa.String(24)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    voice_versions = sa.table(
        "voice_line_versions",
        sa.column("id", sa.String(36)),
        sa.column("voice_line_id", sa.String(36)),
        sa.column("chapter_revision_id", sa.String(36)),
        sa.column("version_no", sa.Integer()),
        sa.column("content_hash", sa.String(64)),
        sa.column("content_json", sa.JSON()),
        sa.column("audio_asset_version_id", sa.String(36)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    for row in bind.execute(sa.select(voice_lines)).mappings():
        payload = _voice_payload(row)
        version_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"if-line:voice-line-version:{row['id']}:1")
        )
        bind.execute(
            voice_versions.insert().values(
                id=version_id,
                voice_line_id=row["id"],
                chapter_revision_id=row["chapter_revision_id"],
                version_no=1,
                content_hash=_content_hash(payload),
                content_json=payload,
                audio_asset_version_id=row["audio_asset_version_id"],
                created_at=row["created_at"],
            )
        )

    graphs = sa.table(
        "vn_graph_revisions",
        sa.column("id", sa.String(36)),
        sa.column("project_id", sa.Integer()),
        sa.column("chapter_index", sa.Integer()),
        sa.column("chapter_revision_id", sa.String(36)),
        sa.column("script_revision_id", sa.String(36)),
        sa.column("source_manifest_hash", sa.String(64)),
        sa.column("schema_version", sa.String(32)),
        sa.column("compiler_version", sa.String(32)),
        sa.column("tachi_policy_version", sa.String(32)),
        sa.column("binding_manifest", sa.JSON()),
        sa.column("binding_manifest_hash", sa.String(64)),
    )
    for row in bind.execute(
        sa.select(
            graphs.c.id,
            graphs.c.project_id,
            graphs.c.chapter_index,
            graphs.c.chapter_revision_id,
            graphs.c.script_revision_id,
            graphs.c.source_manifest_hash,
            graphs.c.schema_version,
            graphs.c.compiler_version,
            graphs.c.tachi_policy_version,
        )
    ).mappings():
        manifest = _legacy_binding_manifest(row)
        bind.execute(
            graphs.update()
            .where(graphs.c.id == row["id"])
            .values(
                binding_manifest=manifest,
                binding_manifest_hash=_content_hash(manifest),
            )
        )


def _backfill_offline() -> None:
    op.execute(
        sa.text(
            """
            WITH snapshots AS (
                SELECT voice_lines.*,
                       '{"audio_asset_version_id":' ||
                       coalesce(to_json(audio_asset_version_id)::text, 'null') ||
                       ',"chapter_revision_id":' || to_json(chapter_revision_id)::text ||
                       ',"emotion":' || coalesce(to_json(emotion)::text, 'null') ||
                       ',"id":' || to_json(id)::text ||
                       ',"kind":' || to_json(kind)::text ||
                       ',"occurrence_id":' || to_json(occurrence_id)::text ||
                       ',"order_index":' || order_index::text ||
                       ',"speaker_character_id":' ||
                       coalesce(to_json(speaker_character_id)::text, 'null') ||
                       ',"speaker_name":' ||
                       coalesce(to_json(speaker_name)::text, 'null') ||
                       ',"status":' || to_json(status)::text ||
                       ',"text":' || to_json(text)::text ||
                       ',"voice_profile_id":' ||
                       coalesce(to_json(voice_profile_id)::text, 'null') ||
                       ',"voice_profile_version":' ||
                       coalesce(voice_profile_version::text, 'null') || '}'
                       AS canonical_payload
                  FROM voice_lines
            )
            INSERT INTO voice_line_versions (
                id, voice_line_id, chapter_revision_id, version_no,
                content_hash, content_json, audio_asset_version_id, created_at
            ) SELECT
                substr(md5('if-line:voice:' || id),1,8) || '-' ||
                substr(md5('if-line:voice:' || id),9,4) || '-5' ||
                substr(md5('if-line:voice:' || id),14,3) || '-a' ||
                substr(md5('if-line:voice:' || id),18,3) || '-' ||
                substr(md5('if-line:voice:' || id),21,12),
                id, chapter_revision_id, 1,
                encode(sha256(convert_to(canonical_payload, 'UTF8')), 'hex'),
                canonical_payload::json, audio_asset_version_id, created_at
              FROM snapshots
            """
        )
    )
    op.execute(
        sa.text(
            """
            WITH snapshots AS (
                SELECT id,
                       '{"asset_bindings":[],"chapter_index":' ||
                       chapter_index::text ||
                       ',"chapter_revision":{"id":' ||
                       to_json(chapter_revision_id)::text || '}' ||
                       ',"graph_revision_id":' || to_json(id)::text ||
                       ',"legacy_source_manifest_hash":' ||
                       to_json(source_manifest_hash)::text ||
                       ',"manifest_version":"legacy-vngraph-v1"' ||
                       ',"project_id":' || project_id::text ||
                       ',"script_revision":{"id":' ||
                       to_json(script_revision_id)::text || '}' ||
                       ',"versions":{"compiler":' ||
                       to_json(compiler_version)::text || ',"schema":' ||
                       to_json(schema_version)::text || ',"tachi_policy":' ||
                       to_json(tachi_policy_version)::text || '}' ||
                       ',"voice_line_versions":[]}' AS canonical_payload
                  FROM vn_graph_revisions
            )
            UPDATE vn_graph_revisions AS graph
               SET binding_manifest = snapshots.canonical_payload::json,
                   binding_manifest_hash = encode(
                       sha256(convert_to(snapshots.canonical_payload, 'UTF8')),
                       'hex'
                   )
              FROM snapshots
             WHERE snapshots.id = graph.id
            """
        )
    )


def upgrade() -> None:
    _create_voice_line_versions()
    op.add_column(
        "vn_graph_revisions",
        sa.Column("binding_manifest", sa.JSON(), nullable=True),
    )
    op.add_column(
        "vn_graph_revisions",
        sa.Column("binding_manifest_hash", sa.String(64), nullable=True),
    )

    if op.get_context().as_sql:
        _backfill_offline()
    else:
        _backfill_online(op.get_bind())

    with op.batch_alter_table("vn_graph_revisions") as batch:
        batch.alter_column(
            "binding_manifest",
            existing_type=sa.JSON(),
            nullable=False,
        )
        batch.alter_column(
            "binding_manifest_hash",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.create_unique_constraint(
            "uq_vngraph_binding_compile",
            [
                "script_revision_id",
                "binding_manifest_hash",
                "compiler_version",
                "schema_version",
            ],
        )


def downgrade() -> None:
    with op.batch_alter_table("vn_graph_revisions") as batch:
        batch.drop_constraint("uq_vngraph_binding_compile", type_="unique")
        batch.drop_column("binding_manifest_hash")
        batch.drop_column("binding_manifest")
    op.drop_index(
        "ix_voice_line_version_chapter_created",
        table_name="voice_line_versions",
    )
    op.drop_table("voice_line_versions")
