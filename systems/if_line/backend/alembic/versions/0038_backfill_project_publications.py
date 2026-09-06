"""Preserve effective legacy public Releases in ProjectPublication.

Revision ID: 0038_backfill_project_publications
Revises: 0037_backfill_immutable_artifact_relations
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0038_backfill_project_publications"
down_revision = "0037_backfill_immutable_artifact_relations"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = ()

_STATUS_TABLE = "_alembic_0038_release_status"
_PUBLICATION_TABLE = "_alembic_0038_created_publication"


def _table_exists(bind, table_name: str) -> bool:
    return inspect(bind).has_table(table_name)


def _create_scratch_tables(bind) -> None:
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_STATUS_TABLE} (
                release_id VARCHAR(36) PRIMARY KEY,
                old_status VARCHAR(24) NOT NULL
            )
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {_PUBLICATION_TABLE} (
                project_id INTEGER PRIMARY KEY
            )
            """
        )
    )


def _record_status(bind, release_id: str, old_status: str) -> None:
    exists = bind.execute(
        sa.text(f"SELECT 1 FROM {_STATUS_TABLE} WHERE release_id = :release_id"),
        {"release_id": release_id},
    ).first()
    if exists is None:
        bind.execute(
            sa.text(
                f"INSERT INTO {_STATUS_TABLE} (release_id, old_status) "
                "VALUES (:release_id, :old_status)"
            ),
            {"release_id": release_id, "old_status": old_status},
        )


def _release(bind, project_id: int, release_id: str):
    return bind.execute(
        sa.text(
            """
            SELECT id, project_id, status, published_at, withdrawn_at, created_at
              FROM project_releases
             WHERE id = :release_id AND project_id = :project_id
            """
        ),
        {"release_id": release_id, "project_id": project_id},
    ).mappings().first()


def _legacy_release_pointer(bind, project_id: int) -> str | None:
    row = bind.execute(
        sa.text(
            "SELECT published_release_id FROM project_content_heads "
            "WHERE project_id = :project_id"
        ),
        {"project_id": project_id},
    ).first()
    return row[0] if row and row[0] else None


def _publication(bind, project_id: int):
    return bind.execute(
        sa.text(
            """
            SELECT project_id, active_release_id, published_at,
                   lock_version, updated_at
              FROM project_publications
             WHERE project_id = :project_id
            """
        ),
        {"project_id": project_id},
    ).mappings().first()


def _published_candidates(bind, project_id: int) -> list[str]:
    return [
        row[0]
        for row in bind.execute(
            sa.text(
                """
                SELECT id
                  FROM project_releases
                 WHERE project_id = :project_id
                   AND status = 'published'
                   AND withdrawn_at IS NULL
                 ORDER BY version DESC, published_at DESC, created_at DESC, id
                """
            ),
            {"project_id": project_id},
        )
    ]


def _resolve_active_release(bind, project_id: int, publication) -> str | None:
    legacy_pointer = _legacy_release_pointer(bind, project_id)
    if publication and publication["active_release_id"]:
        active_id = publication["active_release_id"]
        release = _release(bind, project_id, active_id)
        if (
            release is None
            or release["status"] != "published"
            or release["withdrawn_at"] is not None
        ):
            raise RuntimeError(
                f"project {project_id} has an invalid existing ProjectPublication"
            )
        if legacy_pointer and legacy_pointer != active_id:
            raise RuntimeError(
                f"project {project_id} publication pointers disagree"
            )
        return active_id

    if legacy_pointer:
        if publication is not None:
            raise RuntimeError(
                f"project {project_id} has an inactive target publication "
                "and an active legacy pointer"
            )
        release = _release(bind, project_id, legacy_pointer)
        if (
            release is None
            or release["status"] not in {"published", "superseded"}
            or release["withdrawn_at"] is not None
        ):
            raise RuntimeError(
                f"project {project_id} legacy publication points to an invalid Release"
            )
        return legacy_pointer

    candidates = _published_candidates(bind, project_id)
    if not candidates:
        return None
    if len(candidates) > 1:
        raise RuntimeError(
            f"project {project_id} has multiple published Releases and no active pointer"
        )
    if publication is not None:
        raise RuntimeError(
            f"project {project_id} has an inactive target publication and a published Release"
        )
    return candidates[0]


def _normalize_release_statuses(bind, project_id: int, active_release_id: str | None) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT id, status, withdrawn_at FROM project_releases "
            "WHERE project_id = :project_id"
        ),
        {"project_id": project_id},
    ).mappings().all()
    for row in rows:
        if row["id"] == active_release_id:
            if row["withdrawn_at"] is not None:
                raise RuntimeError(
                    f"project {project_id} active Release {row['id']} is withdrawn"
                )
            if row["status"] != "published":
                _record_status(bind, row["id"], row["status"])
                bind.execute(
                    sa.text(
                        "UPDATE project_releases SET status = 'published' WHERE id = :id"
                    ),
                    {"id": row["id"]},
                )
        elif row["status"] == "published":
            _record_status(bind, row["id"], row["status"])
            bind.execute(
                sa.text(
                    "UPDATE project_releases SET status = 'superseded' WHERE id = :id"
                ),
                {"id": row["id"]},
            )


def _create_publication(bind, project, release) -> None:
    published_at = (
        release["published_at"]
        or project["published_at"]
        or release["created_at"]
    )
    if published_at is None:
        raise RuntimeError(
            f"project {project['id']} active Release has no publication timestamp"
        )
    bind.execute(
        sa.text(
            """
            INSERT INTO project_publications (
                project_id, active_release_id, published_at,
                lock_version, updated_at
            ) VALUES (
                :project_id, :release_id, :published_at, 1, :updated_at
            )
            """
        ),
        {
            "project_id": project["id"],
            "release_id": release["id"],
            "published_at": published_at,
            "updated_at": published_at,
        },
    )
    bind.execute(
        sa.text(
            f"INSERT INTO {_PUBLICATION_TABLE} (project_id) VALUES (:project_id)"
        ),
        {"project_id": project["id"]},
    )


def upgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, "projects"):
        return
    _create_scratch_tables(bind)
    projects = bind.execute(
        sa.text("SELECT id, published_at FROM projects ORDER BY id")
    ).mappings().all()
    for project in projects:
        publication = _publication(bind, project["id"])
        active_release_id = _resolve_active_release(
            bind,
            project["id"],
            publication,
        )
        _normalize_release_statuses(bind, project["id"], active_release_id)
        if active_release_id and publication is None:
            release = _release(bind, project["id"], active_release_id)
            if release is None:
                raise RuntimeError(
                    f"project {project['id']} active Release disappeared during migration"
                )
            _create_publication(bind, project, release)


def downgrade() -> None:
    if op.get_context().as_sql:
        return

    bind = op.get_bind()
    if not _table_exists(bind, _STATUS_TABLE) or not _table_exists(
        bind,
        _PUBLICATION_TABLE,
    ):
        return
    bind.execute(
        sa.text(
            f"""
            DELETE FROM project_publications
             WHERE project_id IN (SELECT project_id FROM {_PUBLICATION_TABLE})
            """
        )
    )
    bind.execute(
        sa.text(
            f"""
            UPDATE project_releases
               SET status = (
                   SELECT old_status FROM {_STATUS_TABLE}
                    WHERE release_id = project_releases.id
               )
             WHERE id IN (SELECT release_id FROM {_STATUS_TABLE})
            """
        )
    )
    bind.execute(sa.text(f"DROP TABLE {_PUBLICATION_TABLE}"))
    bind.execute(sa.text(f"DROP TABLE {_STATUS_TABLE}"))
