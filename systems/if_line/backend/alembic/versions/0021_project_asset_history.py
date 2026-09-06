"""Project-scoped logical assets, immutable provenance and durable plans.

Revision ID: 0021_project_asset_history
Revises: 0020_chapter_script_semantic_pipeline
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0021_project_asset_history"
down_revision = "0020_chapter_script_semantic_pipeline"
branch_labels = None
depends_on = None

TABLES = ("asset_plans", "asset_plan_items")
CACHE_KEY_MAX_LENGTH = 128


def _create_asset_plans() -> None:
    op.create_table(
        "asset_plans",
        sa.Column("plan_key", sa.String(64), primary_key=True),
        sa.Column(
            "project_id",
            sa.Integer(),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_revision_id", sa.String(64), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_asset_plans_project_id", "asset_plans", ["project_id"])
    op.create_index(
        "ix_asset_plans_source_revision_id",
        "asset_plans",
        ["source_revision_id"],
    )
    op.create_index(
        "ix_asset_plan_source",
        "asset_plans",
        ["project_id", "source_kind", "source_revision_id"],
    )


def _create_asset_plan_items() -> None:
    op.create_table(
        "asset_plan_items",
        sa.Column(
            "plan_key",
            sa.String(64),
            sa.ForeignKey("asset_plans.plan_key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "asset_id",
            sa.Integer(),
            sa.ForeignKey("assets.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("logical_key", sa.String(255), nullable=False),
        sa.Column("asset_spec_json", sa.JSON(), nullable=False),
        sa.Column("render_spec_json", sa.JSON(), nullable=False),
        sa.Column("spec_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "asset_type IN ('portrait','background','keyframe','audio')",
            name="ck_asset_plan_item_type",
        ),
    )
    op.create_index("ix_asset_plan_item_asset", "asset_plan_items", ["asset_id"])


def _json(value):
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


def _hash(value) -> str:
    payload = value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _legacy_duplicate_cache_key(value: str, version_id: str) -> str:
    suffix = f":legacy-duplicate:{version_id}"
    prefix_length = max(0, CACHE_KEY_MAX_LENGTH - len(suffix))
    return f"{value[:prefix_length]}{suffix}"


def _columns(bind, table: str) -> set[str]:
    return {item["name"] for item in inspect(bind).get_columns(table)}


def _unique_names(bind, table: str) -> set[str]:
    return {item["name"] for item in inspect(bind).get_unique_constraints(table) if item["name"]}


def _add_columns(bind) -> None:
    asset_columns = _columns(bind, "assets")
    additions = {
        "logical_key": sa.Column("logical_key", sa.String(255), nullable=True),
        "taxonomy_json": sa.Column(
            "taxonomy_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "archived_at": sa.Column("archived_at", sa.DateTime(), nullable=True),
    }
    for name, column in additions.items():
        if name not in asset_columns:
            op.add_column("assets", column)

    version_columns = _columns(bind, "asset_versions")
    version_additions = {
        "source_kind": sa.Column("source_kind", sa.String(64), nullable=True),
        "source_hash": sa.Column("source_hash", sa.String(64), nullable=True),
        "asset_spec_json": sa.Column(
            "asset_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "render_spec_json": sa.Column(
            "render_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
        "render_spec_hash": sa.Column("render_spec_hash", sa.String(64), nullable=True),
    }
    for name, column in version_additions.items():
        if name not in version_columns:
            op.add_column("asset_versions", column)


def _backfill_assets(bind) -> None:
    columns = _columns(bind, "assets")
    required = {
        "asset_type",
        "target_name",
        "character_id",
        "emotion",
        "outfit",
        "pose",
        "scene_location",
        "mood",
        "event_name",
        "generation_params",
    }
    if not required.issubset(columns):
        # Some installations stamped the original baseline over a hand-built
        # minimal legacy schema. There is not enough evidence to infer a
        # semantic identity in that case, so retain every row independently.
        rows = bind.execute(sa.text("SELECT id FROM assets ORDER BY id")).mappings()
        for row in rows:
            bind.execute(
                sa.text(
                    "UPDATE assets SET logical_key = :logical_key, taxonomy_json = :taxonomy "
                    "WHERE id = :asset_id"
                ),
                {
                    "asset_id": row["id"],
                    "logical_key": f"legacy:{row['id']}",
                    "taxonomy": json.dumps({}, ensure_ascii=False),
                },
            )
        return

    bind.execute(
        sa.text(
            "UPDATE assets SET asset_type = 'audio' "
            "WHERE asset_type IN ('voice', 'voice_line', 'voice_line_v2')"
        )
    )
    rows = bind.execute(
        sa.text(
            "SELECT id, project_id, asset_type, target_name, character_id, emotion, outfit, "
            "pose, scene_location, mood, event_name, generation_params, logical_key "
            "FROM assets ORDER BY project_id, asset_type, id"
        )
    ).mappings()
    seen: set[tuple[int, str, str]] = set()
    for row in rows:
        params = _json(row["generation_params"])
        meta = _json(params.get("v2")) if isinstance(params, dict) else {}
        logical_key = str(row["logical_key"] or meta.get("logical_key") or f"legacy:{row['id']}")
        identity = (int(row["project_id"]), str(row["asset_type"]), logical_key)
        if identity in seen:
            logical_key = f"legacy:{row['id']}"
            identity = (identity[0], identity[1], logical_key)
        seen.add(identity)
        taxonomy = _json(meta.get("taxonomy"))
        if not taxonomy:
            taxonomy = {
                key: row[key]
                for key in (
                    "character_id", "emotion", "outfit", "pose", "scene_location", "mood", "event_name"
                )
                if row[key] is not None
            }
        bind.execute(
            sa.text(
                "UPDATE assets SET logical_key = :logical_key, taxonomy_json = :taxonomy "
                "WHERE id = :asset_id"
            ),
            {
                "asset_id": row["id"],
                "logical_key": logical_key,
                "taxonomy": json.dumps(taxonomy, ensure_ascii=False),
            },
        )


def _backfill_versions(bind) -> None:
    asset_columns = _columns(bind, "assets")
    required_asset_columns = {
        "asset_type",
        "logical_key",
        "target_name",
        "taxonomy_json",
        "generation_params",
    }
    if not required_asset_columns.issubset(asset_columns):
        rows = bind.execute(
            sa.text(
                "SELECT id, asset_id, cache_key, source_revision_id "
                "FROM asset_versions ORDER BY asset_id, version_no, id"
            )
        ).mappings()
        seen_cache: set[tuple[int, str]] = set()
        empty_hash = _hash({})
        for row in rows:
            cache_key = str(row["cache_key"])
            identity = (int(row["asset_id"]), cache_key)
            if identity in seen_cache:
                cache_key = _legacy_duplicate_cache_key(cache_key, str(row["id"]))
            seen_cache.add((int(row["asset_id"]), cache_key))
            bind.execute(
                sa.text(
                    "UPDATE asset_versions SET source_kind = 'legacy', asset_spec_json = :spec, "
                    "render_spec_json = :spec, render_spec_hash = :spec_hash, cache_key = :cache_key "
                    "WHERE id = :version_id"
                ),
                {
                    "version_id": row["id"],
                    "spec": json.dumps({}, ensure_ascii=False),
                    "spec_hash": empty_hash,
                    "cache_key": cache_key,
                },
            )
        return

    rows = bind.execute(
        sa.text(
            "SELECT v.id, v.asset_id, v.cache_key, v.source_revision_id, v.generation_task_id, "
            "v.prompt, v.prompt_version, v.negative_prompt_hash, v.seed, v.width, v.height, "
            "v.postprocess_version, v.validator_version, a.asset_type, a.logical_key, "
            "a.target_name, a.taxonomy_json, a.generation_params, t.source_refs, t.parameters "
            "FROM asset_versions v JOIN assets a ON a.id = v.asset_id "
            "LEFT JOIN generation_tasks t ON t.id = v.generation_task_id "
            "ORDER BY v.asset_id, v.version_no, v.id"
        )
    ).mappings().all()
    seen_cache: set[tuple[int, str]] = set()
    for row in rows:
        source_refs = _json(row["source_refs"])
        parameters = _json(row["parameters"])
        params = _json(row["generation_params"])
        meta = _json(params.get("v2")) if isinstance(params, dict) else {}
        cache_material = _json(parameters.get("cache_material"))
        asset_spec = _json(cache_material.get("normalized_asset_spec")) or {
            "asset_type": row["asset_type"],
            "logical_key": row["logical_key"],
            "target_name": row["target_name"],
            "taxonomy": _json(row["taxonomy_json"]),
        }
        render_spec = _json(parameters.get("render_spec")) or {
            key: row[key]
            for key in (
                "prompt", "prompt_version", "negative_prompt_hash", "seed", "width", "height",
                "postprocess_version", "validator_version",
            )
            if row[key] is not None
        }
        source_kind = str(
            source_refs.get("source_kind") or asset_spec.get("source_kind") or meta.get("source_kind") or "legacy"
        )
        source_revision_id = (
            source_refs.get("source_revision_id")
            or asset_spec.get("source_revision_id")
            or row["source_revision_id"]
        )
        source_hash = source_refs.get("source_hash") or asset_spec.get("source_hash") or meta.get("source_hash")
        cache_key = str(row["cache_key"])
        cache_identity = (int(row["asset_id"]), cache_key)
        if cache_identity in seen_cache:
            cache_key = _legacy_duplicate_cache_key(cache_key, str(row["id"]))
        seen_cache.add((int(row["asset_id"]), cache_key))
        bind.execute(
            sa.text(
                "UPDATE asset_versions SET source_kind = :source_kind, "
                "source_revision_id = :source_revision_id, source_hash = :source_hash, "
                "asset_spec_json = :asset_spec, render_spec_json = :render_spec, "
                "render_spec_hash = :render_spec_hash, cache_key = :cache_key WHERE id = :version_id"
            ),
            {
                "version_id": row["id"],
                "source_kind": source_kind,
                "source_revision_id": source_revision_id,
                "source_hash": source_hash,
                "asset_spec": json.dumps(asset_spec, ensure_ascii=False),
                "render_spec": json.dumps(render_spec, ensure_ascii=False),
                "render_spec_hash": _hash(render_spec),
                "cache_key": cache_key,
            },
        )


def _create_plan_tables(bind) -> None:
    inspector = inspect(bind)
    if not inspector.has_table("asset_plans"):
        _create_asset_plans()
    if not inspect(bind).has_table("asset_plan_items"):
        _create_asset_plan_items()

    required = {
        "asset_type",
        "logical_key",
        "target_name",
        "taxonomy_json",
        "generation_params",
    }
    if not required.issubset(_columns(bind, "assets")):
        return

    assets = bind.execute(
        sa.text("SELECT id, project_id, asset_type, logical_key, target_name, taxonomy_json, generation_params FROM assets")
    ).mappings()
    for asset in assets:
        params = _json(asset["generation_params"])
        meta = _json(params.get("v2")) if isinstance(params, dict) else {}
        for plan_key, raw in _json(meta.get("plans")).items():
            plan = _json(raw)
            if not isinstance(plan_key, str) or len(plan_key) > 64:
                continue
            exists = bind.execute(
                sa.text("SELECT 1 FROM asset_plans WHERE plan_key = :plan_key"), {"plan_key": plan_key}
            ).first()
            if not exists:
                bind.execute(
                    sa.text(
                        "INSERT INTO asset_plans(plan_key, project_id, source_kind, source_revision_id, source_hash, created_at) "
                        "VALUES (:plan_key, :project_id, :source_kind, :source_revision_id, :source_hash, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "plan_key": plan_key,
                        "project_id": asset["project_id"],
                        "source_kind": plan.get("source_kind") or "legacy",
                        "source_revision_id": plan.get("source_revision_id") or f"legacy:{asset['id']}",
                        "source_hash": plan.get("source_hash") or _hash(f"legacy:{asset['id']}"),
                    },
                )
            asset_spec = _json(plan.get("asset_spec")) or {
                "asset_type": asset["asset_type"],
                "logical_key": asset["logical_key"],
                "target_name": asset["target_name"],
                "taxonomy": _json(asset["taxonomy_json"]),
            }
            render_spec = _json(plan.get("render_spec"))
            item_exists = bind.execute(
                sa.text(
                    "SELECT 1 FROM asset_plan_items "
                    "WHERE plan_key = :plan_key AND asset_id = :asset_id"
                ),
                {"plan_key": plan_key, "asset_id": asset["id"]},
            ).first()
            if item_exists:
                continue
            bind.execute(
                sa.text(
                    "INSERT INTO asset_plan_items(plan_key, asset_id, asset_type, logical_key, asset_spec_json, "
                    "render_spec_json, spec_hash, created_at) VALUES (:plan_key, :asset_id, :asset_type, "
                    ":logical_key, :asset_spec, :render_spec, :spec_hash, CURRENT_TIMESTAMP)"
                ),
                {
                    "plan_key": plan_key,
                    "asset_id": asset["id"],
                    "asset_type": asset["asset_type"],
                    "logical_key": asset["logical_key"],
                    "asset_spec": json.dumps(asset_spec, ensure_ascii=False),
                    "render_spec": json.dumps(render_spec, ensure_ascii=False),
                    "spec_hash": plan.get("spec_hash") or _hash({"asset_spec": asset_spec, "render_spec": render_spec}),
                },
            )


def _constraints(bind) -> None:
    asset_uniques = _unique_names(bind, "assets")
    asset_columns = _columns(bind, "assets")
    if "asset_type" in asset_columns and "uq_project_asset_logical_key" not in asset_uniques:
        with op.batch_alter_table("assets") as batch:
            batch.create_unique_constraint(
                "uq_project_asset_logical_key", ["project_id", "asset_type", "logical_key"]
            )
    version_uniques = _unique_names(bind, "asset_versions")
    if "uq_asset_version_cache_key" not in version_uniques:
        with op.batch_alter_table("asset_versions") as batch:
            batch.create_unique_constraint("uq_asset_version_cache_key", ["asset_id", "cache_key"])


def _storage_project_fk(bind) -> None:
    if "project_id" not in _columns(bind, "storage_objects"):
        return
    foreign_keys = inspect(bind).get_foreign_keys("storage_objects")
    project_fk = next(
        (item for item in foreign_keys if item.get("referred_table") == "projects" and item.get("constrained_columns") == ["project_id"]),
        None,
    )
    if not project_fk or str((project_fk.get("options") or {}).get("ondelete") or "").upper() == "SET NULL":
        return
    naming_convention = None
    constraint_name = project_fk.get("name")
    if not constraint_name and bind.dialect.name == "sqlite":
        naming_convention = {
            "fk": "legacy_fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"
        }
        constraint_name = "legacy_fk_storage_objects_project_id_projects"
    with op.batch_alter_table(
        "storage_objects", naming_convention=naming_convention
    ) as batch:
        if constraint_name:
            batch.drop_constraint(constraint_name, type_="foreignkey")
        batch.create_foreign_key(
            "fk_storage_objects_project_id_projects",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _offline_upgrade(bind) -> None:
    op.add_column("assets", sa.Column("logical_key", sa.String(255), nullable=True))
    op.add_column(
        "assets",
        sa.Column(
            "taxonomy_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column("assets", sa.Column("archived_at", sa.DateTime(), nullable=True))
    op.add_column("asset_versions", sa.Column("source_kind", sa.String(64), nullable=True))
    op.add_column("asset_versions", sa.Column("source_hash", sa.String(64), nullable=True))
    op.add_column(
        "asset_versions",
        sa.Column(
            "asset_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "asset_versions",
        sa.Column(
            "render_spec_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")
        ),
    )
    op.add_column(
        "asset_versions", sa.Column("render_spec_hash", sa.String(64), nullable=True)
    )

    op.execute(
        "UPDATE assets SET asset_type = 'audio' "
        "WHERE asset_type IN ('voice', 'voice_line', 'voice_line_v2')"
    )
    op.execute(
        "UPDATE assets SET logical_key = COALESCE(NULLIF(logical_key, ''), "
        "NULLIF(generation_params -> 'v2' ->> 'logical_key', ''), "
        "'legacy:' || id::text)"
    )
    op.execute(
        "UPDATE assets SET taxonomy_json = COALESCE("
        "generation_params -> 'v2' -> 'taxonomy', '{}'::json)"
    )
    op.execute(
        "WITH ranked AS (SELECT id, ROW_NUMBER() OVER (PARTITION BY project_id, asset_type, "
        "logical_key ORDER BY id) AS row_no FROM assets) "
        "UPDATE assets SET logical_key = 'legacy:' || assets.id::text FROM ranked "
        "WHERE assets.id = ranked.id AND ranked.row_no > 1"
    )
    op.execute(
        "UPDATE asset_versions SET source_kind = COALESCE(NULLIF(source_kind, ''), 'legacy'), "
        "asset_spec_json = COALESCE(asset_spec_json, '{}'::json), "
        "render_spec_json = COALESCE(render_spec_json, '{}'::json), "
        "render_spec_hash = COALESCE(render_spec_hash, "
        "'44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a')"
    )
    op.execute(
        "WITH ranked AS (SELECT id, ROW_NUMBER() OVER (PARTITION BY asset_id, cache_key "
        "ORDER BY version_no, id) AS row_no FROM asset_versions) "
        "UPDATE asset_versions SET cache_key = LEFT(asset_versions.cache_key, "
        "GREATEST(0, 128 - LENGTH('-legacy-duplicate-' || asset_versions.id))) || "
        "'-legacy-duplicate-' || asset_versions.id FROM ranked "
        "WHERE asset_versions.id = ranked.id AND ranked.row_no > 1"
    )

    _create_asset_plans()
    _create_asset_plan_items()
    op.create_unique_constraint(
        "uq_project_asset_logical_key",
        "assets",
        ["project_id", "asset_type", "logical_key"],
    )
    op.create_unique_constraint(
        "uq_asset_version_cache_key", "asset_versions", ["asset_id", "cache_key"]
    )
    op.drop_constraint(
        "storage_objects_project_id_fkey", "storage_objects", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_storage_objects_project_id_projects",
        "storage_objects",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def upgrade() -> None:
    bind = op.get_bind()
    if op.get_context().as_sql:
        _offline_upgrade(bind)
        return
    _add_columns(bind)
    _backfill_assets(bind)
    _backfill_versions(bind)
    _create_plan_tables(bind)
    _constraints(bind)
    _storage_project_fk(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if "project_id" in _columns(bind, "storage_objects"):
        project_fk = next(
            (
                item
                for item in inspect(bind).get_foreign_keys("storage_objects")
                if item.get("referred_table") == "projects"
                and item.get("constrained_columns") == ["project_id"]
            ),
            None,
        )
        if project_fk and str((project_fk.get("options") or {}).get("ondelete") or "").upper() != "CASCADE":
            with op.batch_alter_table("storage_objects") as batch:
                if project_fk.get("name"):
                    batch.drop_constraint(project_fk["name"], type_="foreignkey")
                batch.create_foreign_key(
                    "storage_objects_project_id_fkey",
                    "projects",
                    ["project_id"],
                    ["id"],
                    ondelete="CASCADE",
                )
    for table in ("asset_plan_items", "asset_plans"):
        if inspect(bind).has_table(table):
            op.drop_table(table)
    with op.batch_alter_table("asset_versions") as batch:
        if "uq_asset_version_cache_key" in _unique_names(bind, "asset_versions"):
            batch.drop_constraint("uq_asset_version_cache_key", type_="unique")
        for name in ("render_spec_hash", "render_spec_json", "asset_spec_json", "source_hash", "source_kind"):
            if name in _columns(bind, "asset_versions"):
                batch.drop_column(name)
    with op.batch_alter_table("assets") as batch:
        if "uq_project_asset_logical_key" in _unique_names(bind, "assets"):
            batch.drop_constraint("uq_project_asset_logical_key", type_="unique")
        for name in ("archived_at", "taxonomy_json", "logical_key"):
            if name in _columns(bind, "assets"):
                batch.drop_column(name)
