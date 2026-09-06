"""Add indexed public-library facets and backfill existing catalogs.

Revision ID: 0047_library_asset_tags
Revises: 0046_detach_legacy_ghost_chapters
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

from app.library_tags import equivalent_display_names, extract_library_facet_tags


revision = "0047_library_asset_tags"
down_revision = "0046_detach_legacy_ghost_chapters"
branch_labels = None
depends_on = None


TABLES: tuple[str, ...] = (
    "library_tags",
    "library_asset_tags",
)


def _backfill(bind) -> None:
    if op.get_context().as_sql or not sa.inspect(bind).has_table("library_assets"):
        return

    metadata = sa.MetaData()
    assets = sa.Table("library_assets", metadata, autoload_with=bind)
    tags = sa.table(
        "library_tags",
        sa.column("id", sa.String(36)),
        sa.column("category", sa.String(64)),
        sa.column("value", sa.String(255)),
        sa.column("display_name", sa.String(255)),
    )
    links = sa.table(
        "library_asset_tags",
        sa.column("library_asset_id", sa.String(36)),
        sa.column("tag_id", sa.String(36)),
    )

    tag_cache: dict[tuple[str, str], tuple[str, str]] = {}
    rows = bind.execute(
        sa.select(
            assets.c.id,
            assets.c.taxonomy,
            assets.c.style,
            assets.c.identity_group,
            assets.c.expression,
            assets.c.pose,
        ).order_by(assets.c.id)
    ).mappings().all()
    for row in rows:
        specs = extract_library_facet_tags(
            taxonomy=row["taxonomy"] if isinstance(row["taxonomy"], dict) else {},
            style=row["style"],
            identity_group=row["identity_group"],
            expression=row["expression"],
            pose=row["pose"],
        )
        for spec in specs:
            key = (spec.category, spec.value)
            cached = tag_cache.get(key)
            if cached is None:
                tag_id = str(uuid.uuid4())
                bind.execute(
                    sa.insert(tags).values(
                        id=tag_id,
                        category=spec.category,
                        value=spec.value,
                        display_name=spec.display_name,
                    )
                )
                tag_cache[key] = (tag_id, spec.display_name)
            else:
                tag_id, display_name = cached
                if not equivalent_display_names(display_name, spec.display_name):
                    raise RuntimeError(
                        "conflicting display_name while backfilling facet tag "
                        f"{spec.category}={spec.value!r}"
                    )
            bind.execute(
                sa.insert(links).values(
                    library_asset_id=str(row["id"]),
                    tag_id=tag_id,
                )
            )


def upgrade() -> None:
    op.create_table(
        "library_tags",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_library_tags"),
        sa.UniqueConstraint(
            "category", "value", name="uq_library_tags_category_value"
        ),
    )
    op.create_table(
        "library_asset_tags",
        sa.Column("library_asset_id", sa.String(length=36), nullable=False),
        sa.Column("tag_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["library_asset_id"],
            ["library_assets.id"],
            name="fk_library_asset_tags_library_asset_id_library_assets",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_id"],
            ["library_tags.id"],
            name="fk_library_asset_tags_tag_id_library_tags",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "library_asset_id", "tag_id", name="pk_library_asset_tags"
        ),
    )
    op.create_index(
        "ix_library_asset_tags_tag_asset",
        "library_asset_tags",
        ["tag_id", "library_asset_id"],
        unique=False,
    )
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_table("library_asset_tags")
    op.drop_table("library_tags")
