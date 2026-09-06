"""Backfill asset_versions + generation_tasks from v1.

Revision ID: 0017_backfill_asset_versions_and_tasks
Revises: 0016a_expand_version_col

Part A: v1 assets → v2 asset_versions
- v1 assets 表保留 (v2 asset_versions.asset_id 仍引用 assets.id)
- 每个 v1 asset 创建 1 个 v2 asset_version (version_no=1)
- 不创建 asset_bindings (per plan §3.5,后续 v2 UI 流程自然生成)
- 必填字段 prompt_hash/prompt_version/cache_key 用 sha256(prompt) + sentinel 合成

Part B: v1 generation_stats → v2 generation_tasks (best-effort)
- v2 user_id NOT NULL,用 Project.owner_id fallback;若 owner_id IS NULL 跳过
- status 映射: running→running, completed→succeeded, failed→failed, 其它→succeeded
- generation_type → kind: chapter→chapter.generate, portrait/background/keyframe→asset.render
- asset_type / variation_info / chapter_index 落到 parameters + source_refs

幂等性:
- asset_versions: legacy_source_table 不存在该列,通过 asset_id + version_no=1 + cache_key 前缀去重
- generation_tasks: idempotency_key='legacy:<v1_id>' 去重

downgrade: scratch 表分别精确删除.
"""
from __future__ import annotations

import hashlib
import json

import sqlalchemy as sa
from alembic import op


revision = "0017_backfill_asset_versions_and_tasks"
down_revision = "0016a_expand_version_col"
branch_labels = None
depends_on = None


_SCRATCH_ASSET = "_alembic_backfill_0017_asset"
_SCRATCH_TASK = "_alembic_backfill_0017_task"

_PROMPT_VERSION_LEGACY = "legacy-v1"
_CACHE_KEY_PREFIX = "legacy-v1-"


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    if isinstance(value, str):
        payload = value
    else:
        payload = _canonical_json(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load(raw):
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _table_has_columns(bind, table: str, columns: set[str]) -> bool:
    """检查表是否存在且包含所有指定列. test fixture 可能建了表但缺列.

    跨方言实现: SQLite 上 inspect 反射走 PRAGMA, PG 上走
    information_schema.columns, 对调用方透明. 表不存在时 inspect
    抛 NoSuchTableError, 这里降级为 False 与旧 PRAGMA 行为对齐.
    """
    from sqlalchemy import inspect as _inspect
    from sqlalchemy.exc import NoSuchTableError

    try:
        actual = {col["name"] for col in _inspect(bind).get_columns(table)}
    except NoSuchTableError:
        return False
    if not actual:
        return False
    return columns.issubset(actual)


def _table_exists(bind, name: str) -> bool:
    from sqlalchemy import inspect as _inspect

    return _inspect(bind).has_table(name)


def _next_uuid(bind) -> str:
    if bind.dialect.name == "postgresql":
        row = bind.execute(sa.text("SELECT gen_random_uuid()::text")).first()
        return row[0]
    row = bind.execute(sa.text("SELECT lower(hex(randomblob(16)))")).first()
    raw = row[0]
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"


# ============================================================
# Part A: asset_versions
# ============================================================

def _backfill_asset_versions(bind) -> None:
    required_cols = {
        "id", "prompt", "image_url", "seed", "generation_params", "created_at", "updated_at"
    }
    if not _table_has_columns(bind, "assets", required_cols):
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT a.id, a.prompt, a.image_url, a.seed,
                   a.generation_params, a.created_at, a.updated_at
            FROM assets a
            WHERE NOT EXISTS (
                SELECT 1 FROM asset_versions v
                WHERE v.asset_id = a.id AND v.version_no = 1
            )
            ORDER BY a.id
            """
        )
    ).mappings().all()

    if not rows:
        return

    for row in rows:
        prompt = row["prompt"] or ""
        prompt_hash = _hash(prompt) if prompt else _hash(f"empty:{row['id']}")
        cache_key = f"{_CACHE_KEY_PREFIX}{row['id']}"
        params = _load(row["generation_params"]) or {}

        new_id = _next_uuid(bind)
        created_at = row["updated_at"] or row["created_at"]

        bind.execute(
            sa.text(
                """
                INSERT INTO asset_versions
                    (id, asset_id, source_revision_id, generation_task_id,
                     storage_object_id, version_no, cache_key, provider, model,
                     prompt, prompt_hash, prompt_version, negative_prompt_hash,
                     seed, width, height, postprocess_version,
                     validator_version, quality_score, safety_status,
                     rights_metadata, created_at)
                VALUES
                    (:id, :asset_id, NULL, NULL,
                     NULL, 1, :cache_key, :provider, :model,
                     :prompt, :prompt_hash, :prompt_version, NULL,
                     :seed, NULL, NULL, NULL,
                     NULL, NULL, 'pending',
                     :rights, :created_at)
                """
            ),
            {
                "id": new_id,
                "asset_id": row["id"],
                "cache_key": cache_key,
                "provider": params.get("provider") if isinstance(params, dict) else None,
                "model": params.get("model") if isinstance(params, dict) else None,
                "prompt": prompt,
                "prompt_hash": prompt_hash,
                "prompt_version": _PROMPT_VERSION_LEGACY,
                "seed": row["seed"],
                "rights": json.dumps({"origin": "legacy-v1", "image_url": row["image_url"]}, ensure_ascii=False),
                "created_at": created_at,
            },
        )

        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_ASSET} (version_id) VALUES (:vid)"),
            {"vid": new_id},
        )


# ============================================================
# Part B: generation_tasks
# ============================================================

_V1_TYPE_TO_V2_KIND = {
    "chapter": "chapter.generate",
    "portrait": "asset.render",
    "background": "asset.render",
    "keyframe": "asset.render",
    "asset": "asset.render",
}

_V1_STATUS_TO_V2 = {
    "running": "running",
    "completed": "succeeded",
    "failed": "failed",
    "pending": "queued",
    # 兜底
}


def _backfill_generation_tasks(bind) -> None:
    required_cols = {
        "id", "project_id", "generation_type", "asset_type", "chapter_index",
        "asset_id", "target_name", "variation_info", "start_time", "end_time",
        "duration_seconds", "status", "error_message", "created_at",
    }
    if not _table_has_columns(bind, "generation_stats", required_cols):
        return
    # projects 表的 owner_id 是必需 (我们用它作为 task.user_id)
    if not _table_has_columns(bind, "projects", {"id", "owner_id"}):
        return

    rows = bind.execute(
        sa.text(
            """
            SELECT s.id, s.project_id, s.generation_type, s.asset_type,
                   s.chapter_index, s.asset_id, s.target_name, s.variation_info,
                   s.start_time, s.end_time, s.duration_seconds, s.status,
                   s.error_message, s.created_at, p.owner_id
            FROM generation_stats s
            JOIN projects p ON p.id = s.project_id
            WHERE p.owner_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM generation_tasks t
                  WHERE t.idempotency_key = :idem
              )
            ORDER BY s.id
            """
        ),
        {"idem": "legacy-v1"},  # placeholder, real prefix below
    ).mappings().all()

    if not rows:
        return

    for row in rows:
        idem = f"legacy-v1-stats-{row['id']}"
        # double check (above NOT EXISTS was generic; check precise)
        already = bind.execute(
            sa.text(
                "SELECT id FROM generation_tasks WHERE idempotency_key = :idem"
            ),
            {"idem": idem},
        ).first()
        if already is not None:
            continue

        kind = _V1_TYPE_TO_V2_KIND.get(row["generation_type"] or "", "asset.render")
        v2_status = _V1_STATUS_TO_V2.get(row["status"] or "", "succeeded")
        if v2_status not in ("queued", "running", "partial", "succeeded", "failed", "cancelled"):
            v2_status = "succeeded"

        variation = _load(row["variation_info"]) or {}
        parameters = {
            "asset_type": row["asset_type"] or row["generation_type"],
            "target_name": row["target_name"],
            "variation_info": variation,
            "legacy_v1_stats_id": row["id"],
        }
        source_refs = {}
        if row["chapter_index"] is not None:
            source_refs["chapter_index"] = row["chapter_index"]
        if row["asset_id"] is not None:
            source_refs["legacy_asset_id"] = row["asset_id"]

        parameters_hash = _hash(parameters)
        new_id = _next_uuid(bind)
        created_at = row["created_at"]
        started_at = row["start_time"]
        finished_at = row["end_time"]
        error_detail = row["error_message"] if v2_status == "failed" else None

        bind.execute(
            sa.text(
                """
                INSERT INTO generation_tasks
                    (id, root_task_id, parent_task_id, user_id, project_id,
                     kind, status, stage, progress, idempotency_key,
                     source_refs, result_refs, parameters, parameters_hash,
                     attempt, max_attempts, cancel_requested_at, lease_owner,
                     heartbeat_at, error_code, error_detail,
                     estimated_cost, reserved_cost, actual_cost,
                     queued_at, started_at, finished_at,
                     created_at, updated_at)
                VALUES
                    (:id, NULL, NULL, :user_id, :pid,
                     :kind, :status, NULL, :progress, :idem,
                     :source_refs, :result_refs, :parameters, :parameters_hash,
                     0, 1, NULL, NULL,
                     NULL, NULL, :error_detail,
                     0, 0, 0,
                     :queued_at, :started_at, :finished_at,
                     :created_at, :updated_at)
                """
            ),
            {
                "id": new_id,
                "user_id": row["owner_id"],
                "pid": row["project_id"],
                "kind": kind,
                "status": v2_status,
                "progress": 100.0 if v2_status == "succeeded" else (0.0 if v2_status == "queued" else 50.0),
                "idem": idem,
                "source_refs": json.dumps(source_refs, ensure_ascii=False),
                "result_refs": "{}",
                "parameters": json.dumps(parameters, ensure_ascii=False),
                "parameters_hash": parameters_hash,
                "error_detail": error_detail,
                "queued_at": created_at,
                "started_at": started_at,
                "finished_at": finished_at,
                "created_at": created_at,
                "updated_at": finished_at or started_at or created_at,
            },
        )

        bind.execute(
            sa.text(f"INSERT INTO {_SCRATCH_TASK} (task_id) VALUES (:tid)"),
            {"tid": new_id},
        )


# ============================================================
# Main
# ============================================================

def upgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCRATCH_ASSET} (
                version_id TEXT PRIMARY KEY
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_SCRATCH_TASK} (
                task_id TEXT PRIMARY KEY
            )
            """
        )
    )

    _backfill_asset_versions(bind)
    _backfill_generation_tasks(bind)


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()

    # Part A: asset_versions
    if _table_exists(bind, _SCRATCH_ASSET):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM asset_versions
                WHERE id IN (SELECT version_id FROM {_SCRATCH_ASSET})
                """
            )
        )
        bind.execute(sa.text(f"DROP TABLE {_SCRATCH_ASSET}"))

    # Part B: generation_tasks
    if _table_exists(bind, _SCRATCH_TASK):
        bind.execute(
            sa.text(
                f"""
                DELETE FROM generation_tasks
                WHERE id IN (SELECT task_id FROM {_SCRATCH_TASK})
                """
            )
        )
        bind.execute(sa.text(f"DROP TABLE {_SCRATCH_TASK}"))
