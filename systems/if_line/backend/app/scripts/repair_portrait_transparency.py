"""Audit or repair completed portrait Assets that lack effective transparency.

Dry-run is the default. ``--apply`` preserves the provider source beside the
portrait, replaces only the presentation PNG, and updates identity-reference
metadata for every Asset row that shares the URL.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.models import Asset
from app.services.image_generation_service import IMAGE_BASE_URL, ImageGenerationService


BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _portrait_path(image_url: str) -> Path | None:
    prefix = f"{IMAGE_BASE_URL}/portraits/"
    if not image_url.startswith(prefix):
        return None
    name = Path(image_url).name
    if not name.lower().endswith(".png") or ".source." in name or ".identity." in name:
        return None
    path = (BACKEND_ROOT / image_url.lstrip("/")).resolve()
    portrait_root = (BACKEND_ROOT / "static/assets/portraits").resolve()
    try:
        path.relative_to(portrait_root)
    except ValueError:
        return None
    return path


def repair_portraits(*, apply: bool, project_id: int | None = None) -> dict[str, Any]:
    db = SessionLocal()
    service = ImageGenerationService()
    counts: dict[str, Any] = {
        "dry_run": not apply,
        "project_id": project_id,
        "asset_rows": 0,
        "unique_files": 0,
        "already_transparent": 0,
        "needs_repair": 0,
        "repaired": 0,
        "missing": 0,
        "failed": 0,
        "failures": [],
    }
    try:
        query = db.query(Asset).filter(
            Asset.asset_type == "portrait",
            Asset.status == "completed",
            Asset.image_url.isnot(None),
        )
        if project_id is not None:
            query = query.filter(Asset.project_id == project_id)
        rows = query.all()
        counts["asset_rows"] = len(rows)
        rows_by_url: dict[str, list[Asset]] = {}
        for asset in rows:
            url = str(asset.image_url or "")
            if _portrait_path(url) is not None:
                rows_by_url.setdefault(url, []).append(asset)
        counts["unique_files"] = len(rows_by_url)

        for image_url, assets in sorted(rows_by_url.items()):
            path = _portrait_path(image_url)
            if path is None or not path.is_file():
                counts["missing"] += 1
                continue
            if service.portrait_has_effective_alpha(path):
                counts["already_transparent"] += 1
                continue
            counts["needs_repair"] += 1
            if not apply:
                continue
            try:
                service._remove_background_sync(path, keep_source=True)
                metrics = service._portrait_alpha_metrics(path)
                service._assert_portrait_transparency(metrics, path)
                source_path = path.with_suffix(path.suffix + ".source.png")
                source_url = f"{IMAGE_BASE_URL}/portraits/{source_path.name}"
                identity_url = service._compose_identity_reference(path)
                source_sha = service._file_sha256(source_path)
                for asset in assets:
                    params = dict(asset.generation_params or {})
                    params.update(
                        {
                            "source_image_url": source_url,
                            "identity_reference_url": identity_url,
                            "presentation_url": image_url,
                            "reference_image_sha256": source_sha,
                            "portrait_alpha": metrics,
                        }
                    )
                    # A cached normalized presentation was derived from the old
                    # opaque bytes and must not outrank the repaired source.
                    params.pop("presentation", None)
                    asset.generation_params = params
                    db.add(asset)
                db.commit()
                counts["repaired"] += 1
            except Exception as exc:
                db.rollback()
                counts["failed"] += 1
                counts["failures"].append({"image_url": image_url, "error": str(exc)})
        return counts
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair opaque portrait presentation PNGs")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project-id", type=int)
    args = parser.parse_args()
    print(
        json.dumps(
            repair_portraits(apply=args.apply, project_id=args.project_id),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
