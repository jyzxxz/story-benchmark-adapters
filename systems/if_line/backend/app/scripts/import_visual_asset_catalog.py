"""Import a frozen curated catalog into v2 storage.

The command is dry-run by default.  It never scans the library during a user
request and it never mutates an already-published catalog row.  Use ``--apply``
only after reviewing the printed counts and catalog version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from PIL import Image

from app.application.storage_service import (
    get_local_storage_backend,
    register_stored_file,
)
from app.application.library_tag_service import (
    create_library_asset_tag_links,
    validate_frozen_library_asset_tags,
    validate_library_tag_specs,
)
from app.application.visual_asset_service import MATCHER_VERSION, build_hash_embedding
from app.database import SessionLocal
from app.library_tags import equivalent_display_names, extract_library_facet_tags
from app.models import User
from app.models_v2 import LibraryAsset, LibraryAssetEmbedding, StorageObject


BACKEND_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_INDEX = (
    PROJECT_ROOT
    / "static/assets/universal_visual_novel_latest/data/runtime_asset_index.json"
)


def _split_key(key: str, asset_type: str) -> dict[str, Any]:
    normalized = key.strip().lower()
    expression = None
    identity_group = None
    base = normalized
    if asset_type == "portrait":
        base, separator, expression = normalized.partition("__")
        expression = expression or None
        identity_group = base
    tokens = [
        token
        for token in base.replace("portrait_", "").replace("bg_", "").split("_")
        if token
    ]
    style = next(
        (
            token
            for token in tokens
            if token
            in {
                "modern",
                "historical",
                "xianxia",
                "cyberpunk",
                "scifi",
                "space",
                "fantasy",
                "rural",
                "romance",
                "mystery",
                "supernatural",
            }
        ),
        None,
    )
    return {
        "description_en": " ".join(tokens + ([expression] if expression else [])),
        "tags": list(dict.fromkeys(tokens + ([expression] if expression else []))),
        "taxonomy": {
            "tokens": tokens,
            "genre": style,
            **({"expression": expression} if expression else {}),
        },
        "identity_group": identity_group,
        "expression": expression,
        "style": style,
    }


def _file_metadata(path: Path) -> tuple[str, int, int, int, str, bool]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        has_alpha_channel = "A" in image.getbands() or (
            image.mode == "P" and "transparency" in image.info
        )
        has_alpha = False
        if has_alpha_channel:
            alpha = image.convert("RGBA").getchannel("A")
            histogram = alpha.histogram()
            pixel_count = max(1, width * height)
            transparent_fraction = sum(histogram[:8]) / pixel_count
            opaque_fraction = sum(histogram[248:]) / pixel_count
            has_alpha = bool(
                transparent_fraction >= 0.01
                and opaque_fraction >= 0.01
            )
        media_type = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
        }.get(str(image.format or "").upper())
    if not media_type:
        raise ValueError(f"unsupported catalog image: {path}")
    return digest.hexdigest(), size, width, height, media_type, has_alpha


def import_catalog(
    *,
    index_path: Path,
    owner_id: int,
    catalog_version: str | None,
    apply: bool,
) -> dict[str, int | str | bool]:
    index_path = index_path.expanduser().resolve()
    payload = json.loads(index_path.read_text(encoding="utf-8"))
    version = str(catalog_version or payload.get("library_version") or "").strip()
    if not version:
        raise ValueError("catalog version is required")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("runtime asset index has no assets list")
    manifest_sha = hashlib.sha256(index_path.read_bytes()).hexdigest()
    counts = {
        "seen": 0,
        "created": 0,
        "reused": 0,
        "skipped": 0,
        "tags_created": 0,
        "links_created": 0,
    }
    planned_tags: dict[tuple[str, str], str] = {}
    planned_assets: dict[
        str, tuple[str, str, dict[tuple[str, str], str]]
    ] = {}
    backend = get_local_storage_backend()
    db = SessionLocal()
    try:
        owner = db.query(User).filter(User.id == owner_id, User.is_active.is_(True)).first()
        if not owner:
            raise ValueError("owner_id must reference an active user")
        for item in assets:
            if not isinstance(item, dict):
                counts["skipped"] += 1
                continue
            asset_type = str(item.get("asset_type") or "").strip().lower()
            if asset_type not in {"background", "portrait", "keyframe"}:
                counts["skipped"] += 1
                continue
            stable_key = str(item.get("key") or item.get("variant_key") or "").strip()
            relative = str(
                item.get("versioned_project_relative_path")
                or item.get("project_relative_path")
                or ""
            ).replace("\\", "/")
            if not stable_key or not relative:
                counts["skipped"] += 1
                continue
            source = (PROJECT_ROOT / relative).resolve()
            try:
                source.relative_to(PROJECT_ROOT.resolve())
            except ValueError as exc:
                raise ValueError(f"catalog path escapes project root: {relative}") from exc
            if not source.is_file():
                raise FileNotFoundError(source)
            counts["seen"] += 1
            parsed = _split_key(stable_key, asset_type)
            sha, byte_size, width, height, media_type, has_alpha = _file_metadata(source)
            if asset_type == "portrait" and not has_alpha:
                raise ValueError(
                    f"catalog portrait must contain effective transparency: {source}"
                )
            parsed["taxonomy"].update(
                {"width": width, "height": height, "has_alpha": has_alpha}
            )
            facet_specs = extract_library_facet_tags(
                taxonomy=parsed["taxonomy"],
                style=parsed["style"],
                identity_group=parsed["identity_group"],
                expression=parsed["expression"],
                pose=parsed.get("pose"),
                explicit_facet_tags=item.get("facet_tags"),
            )
            existing = (
                db.query(LibraryAsset)
                .filter(
                    LibraryAsset.catalog_version == version,
                    LibraryAsset.stable_key == stable_key,
                )
                .first()
            )
            if existing:
                storage = db.query(StorageObject).filter(StorageObject.id == existing.storage_object_id).one()
                if storage.sha256 != sha or existing.asset_type != asset_type:
                    raise RuntimeError(
                        f"frozen catalog conflict for {version}/{stable_key}; publish a new version"
                    )
                validate_frozen_library_asset_tags(
                    db,
                    asset=existing,
                    specs=facet_specs,
                )
                counts["reused"] += 1
                continue
            if not apply:
                facet_signature = {
                    (spec.category, spec.value): spec.display_name
                    for spec in facet_specs
                }
                planned_asset = planned_assets.get(stable_key)
                if planned_asset is not None:
                    if planned_asset != (sha, asset_type, facet_signature):
                        raise RuntimeError(
                            f"frozen catalog conflict for {version}/{stable_key}; "
                            "publish a new version"
                        )
                    counts["reused"] += 1
                    continue
                missing = validate_library_tag_specs(db, facet_specs)
                new_keys = missing - set(planned_tags)
                for spec in facet_specs:
                    key = (spec.category, spec.value)
                    planned_display = planned_tags.get(key)
                    if planned_display is not None and not equivalent_display_names(
                        planned_display, spec.display_name
                    ):
                        raise ValueError(
                            "conflicting display_name within dry-run catalog for "
                            f"{spec.category}={spec.value!r}"
                        )
                    planned_tags.setdefault(key, spec.display_name)
                planned_assets[stable_key] = (sha, asset_type, facet_signature)
                counts["tags_created"] += len(new_keys)
                counts["links_created"] += len(facet_specs)
                counts["created"] += 1
                continue
            storage = (
                db.query(StorageObject)
                .filter(
                    StorageObject.sha256 == sha,
                    StorageObject.status == "active",
                    StorageObject.visibility == "public",
                    StorageObject.deleted_at.is_(None),
                )
                .first()
            )
            if storage is None:
                with source.open("rb") as handle:
                    stored = backend.save_stream(
                        handle,
                        namespace=f"catalog-{version.replace('.', '-')}",
                        filename_or_suffix=source.suffix,
                        media_type=media_type,
                    )
                storage = register_stored_file(
                    db,
                    stored=stored,
                    owner_id=owner_id,
                    project_id=None,
                    visibility="public",
                    status="active",
                    backend=backend,
                )
            library = LibraryAsset(
                catalog_version=version,
                stable_key=stable_key,
                asset_type=asset_type,
                storage_object_id=storage.id,
                description_cn="",
                description_en=parsed["description_en"],
                tags=parsed["tags"],
                taxonomy=parsed["taxonomy"],
                identity_group=parsed["identity_group"],
                expression=parsed["expression"],
                style=parsed["style"],
                quality_score=1.0,
                safety_status="approved",
                rights_metadata={
                    "origin": "curated_internal_catalog",
                    "manifest_sha256": manifest_sha,
                    "source_version": item.get("source_version"),
                    "source_relative_path": relative,
                    "has_alpha": has_alpha,
                },
                enabled=True,
            )
            db.add(library)
            db.flush()
            created_tags, created_links = create_library_asset_tag_links(
                db,
                asset=library,
                specs=facet_specs,
            )
            db.flush()
            counts["tags_created"] += created_tags
            counts["links_created"] += created_links
            text = " ".join([parsed["description_en"], *parsed["tags"]])
            embedding = build_hash_embedding(text)
            db.add(
                LibraryAssetEmbedding(
                    library_asset_id=library.id,
                    model_version=MATCHER_VERSION,
                    dimensions=len(embedding),
                    embedding=embedding,
                )
            )
            counts["created"] += 1
        if apply:
            db.commit()
        else:
            db.rollback()
        return {
            "catalog_version": version,
            "dry_run": not apply,
            **counts,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Import the frozen v2 visual asset catalog")
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--owner-id", type=int, required=True)
    parser.add_argument("--catalog-version")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = import_catalog(
        index_path=args.index,
        owner_id=args.owner_id,
        catalog_version=args.catalog_version,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
