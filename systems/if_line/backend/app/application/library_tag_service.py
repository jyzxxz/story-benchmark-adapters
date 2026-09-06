"""Persistence helpers for public-library facet tags."""
from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.orm import Session

from app.library_tags import FacetTagSpec, equivalent_display_names
from app.models_v2 import LibraryAsset, LibraryAssetTag, LibraryTag


def _spec_map(specs: Iterable[FacetTagSpec]) -> dict[tuple[str, str], FacetTagSpec]:
    return {(spec.category, spec.value): spec for spec in specs}


def validate_library_tag_specs(
    db: Session,
    specs: Iterable[FacetTagSpec],
) -> set[tuple[str, str]]:
    """Validate global display labels and return tag keys not yet persisted."""

    expected = _spec_map(specs)
    missing: set[tuple[str, str]] = set()
    for key, spec in expected.items():
        tag = (
            db.query(LibraryTag)
            .filter(LibraryTag.category == key[0], LibraryTag.value == key[1])
            .first()
        )
        if tag is None:
            missing.add(key)
            continue
        if not equivalent_display_names(tag.display_name, spec.display_name):
            raise ValueError(
                "conflicting display_name for existing facet tag "
                f"{spec.category}={spec.value!r}"
            )
    return missing


def create_library_asset_tag_links(
    db: Session,
    *,
    asset: LibraryAsset,
    specs: Iterable[FacetTagSpec],
) -> tuple[int, int]:
    """Create normalized tags and links for a newly inserted catalog asset."""

    created_tags = 0
    created_links = 0
    for key, spec in _spec_map(specs).items():
        tag = (
            db.query(LibraryTag)
            .filter(LibraryTag.category == key[0], LibraryTag.value == key[1])
            .first()
        )
        if tag is None:
            tag = LibraryTag(
                category=spec.category,
                value=spec.value,
                display_name=spec.display_name,
            )
            db.add(tag)
            db.flush()
            created_tags += 1
        elif not equivalent_display_names(tag.display_name, spec.display_name):
            raise ValueError(
                "conflicting display_name for existing facet tag "
                f"{spec.category}={spec.value!r}"
            )

        link = db.get(LibraryAssetTag, (asset.id, tag.id))
        if link is None:
            db.add(LibraryAssetTag(library_asset_id=asset.id, tag_id=tag.id))
            created_links += 1
    return created_tags, created_links


def validate_frozen_library_asset_tags(
    db: Session,
    *,
    asset: LibraryAsset,
    specs: Iterable[FacetTagSpec],
) -> None:
    """Reject metadata drift for an already-published catalog asset."""

    expected = _spec_map(specs)
    rows = (
        db.query(LibraryTag)
        .join(LibraryAssetTag, LibraryAssetTag.tag_id == LibraryTag.id)
        .filter(LibraryAssetTag.library_asset_id == asset.id)
        .all()
    )
    actual = {(tag.category, tag.value): tag for tag in rows}
    if set(actual) != set(expected):
        raise RuntimeError(
            "frozen catalog facet conflict for "
            f"{asset.catalog_version}/{asset.stable_key}; publish a new version"
        )
    for key, spec in expected.items():
        if not equivalent_display_names(actual[key].display_name, spec.display_name):
            raise RuntimeError(
                "frozen catalog display_name conflict for "
                f"{asset.catalog_version}/{asset.stable_key}; publish a new version"
            )
