"""Audit generated background image files.

Default mode is read-only: report invalid PNGs, non-16:9 images, and files not
referenced by the Asset table. Use --delete-invalid-unreferenced to remove only
invalid files that are not referenced by the database.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from PIL import Image

from app.database import SessionLocal
from app.models import Asset
from app.services.image_generation_service import IMAGE_OUTPUT_DIR


def _background_dir() -> Path:
    return Path(IMAGE_OUTPUT_DIR) / "backgrounds"


def _referenced_background_filenames() -> set[str]:
    db = SessionLocal()
    try:
        rows = db.query(Asset).filter(Asset.asset_type == "background").all()
        return {
            Path(a.image_url or "").name
            for a in rows
            if a.image_url
        }
    finally:
        db.close()


def _inspect_file(path: Path, referenced: set[str]) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "path": str(path),
        "filename": path.name,
        "bytes": path.stat().st_size,
        "referenced": path.name in referenced,
        "valid": False,
        "size": None,
        "ratio": None,
        "is_16_9": False,
        "error": None,
    }
    try:
        with Image.open(path) as img:
            width, height = img.size
            item["valid"] = True
            item["size"] = [width, height]
            item["ratio"] = round(width / height, 6) if height else None
            item["is_16_9"] = bool(height and abs((width / height) - (16 / 9)) < 0.01)
    except Exception as exc:
        item["error"] = f"{type(exc).__name__}: {exc}"
    return item


def audit(delete_invalid_unreferenced: bool = False) -> Dict[str, Any]:
    bg_dir = _background_dir()
    referenced = _referenced_background_filenames()
    files = sorted(bg_dir.glob("*.png")) if bg_dir.exists() else []
    items: List[Dict[str, Any]] = [_inspect_file(path, referenced) for path in files]

    deleted: List[str] = []
    if delete_invalid_unreferenced:
        for item in items:
            if item["valid"] or item["referenced"]:
                continue
            path = Path(item["path"])
            try:
                path.unlink()
                deleted.append(str(path))
            except Exception as exc:
                item["delete_error"] = f"{type(exc).__name__}: {exc}"

    return {
        "background_dir": str(bg_dir),
        "total_files": len(items),
        "valid_files": sum(1 for item in items if item["valid"]),
        "invalid_files": [item for item in items if not item["valid"]],
        "non_16_9_files": [item for item in items if item["valid"] and not item["is_16_9"]],
        "unreferenced_files": [item for item in items if not item["referenced"]],
        "deleted": deleted,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete-invalid-unreferenced",
        action="store_true",
        help="Delete invalid PNG files only when they are not referenced by the DB.",
    )
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.delete_invalid_unreferenced), ensure_ascii=False, indent=2))
    except BrokenPipeError:
        try:
            sys.stdout.close()
        finally:
            return


if __name__ == "__main__":
    main()
