"""Verify v2 media rows and local object bytes without exposing identifiers."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.storage_service import LocalStorageBackend
from app.database import SessionLocal
from app.models import Asset, Project
from app.models_v2 import AssetBinding, AssetVersion, StorageObject, VoiceLine


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify() -> dict:
    db = SessionLocal()
    backend = LocalStorageBackend()
    errors: Counter[str] = Counter()
    try:
        objects = db.query(StorageObject).filter(StorageObject.storage_backend == "local").all()
        for obj in objects:
            try:
                path = backend.resolve_key(obj.storage_key)
            except Exception:
                errors["invalid_storage_key"] += 1
                continue
            if not path.is_file():
                errors["missing_file"] += 1
                continue
            if path.stat().st_size != obj.byte_size:
                errors["size_mismatch"] += 1
                continue
            if _sha256(path) != obj.sha256:
                errors["sha256_mismatch"] += 1

        all_versions = db.query(AssetVersion).all()
        legacy_versions = [version for version in all_versions if version.provider == "legacy-import"]
        object_ids = {obj.id for obj in objects}
        if any(not version.storage_object_id or version.storage_object_id not in object_ids for version in legacy_versions):
            errors["legacy_version_storage_missing"] += 1

        version_ids = {version.id for version in all_versions}
        if any(binding.asset_version_id not in version_ids for binding in db.query(AssetBinding).all()):
            errors["binding_version_missing"] += 1
        if any(
            line.status == "ready" and line.audio_asset_version_id not in version_ids
            for line in db.query(VoiceLine).all()
        ):
            errors["voice_audio_version_missing"] += 1

        deferred_ownerless = (
            db.query(Asset)
            .join(Project, Project.id == Asset.project_id)
            .filter(Project.owner_id.is_(None))
            .count()
        )
        return {
            "summary": {
                "storage_objects": len(objects),
                "legacy_asset_versions": len(legacy_versions),
                "asset_bindings": db.query(AssetBinding).count(),
                "voice_lines": db.query(VoiceLine).count(),
                "deferred_ownerless_legacy_assets": deferred_ownerless,
                "errors": sum(errors.values()),
            },
            "error_categories": dict(sorted(errors.items())),
        }
    finally:
        db.close()


if __name__ == "__main__":
    report = verify()
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["summary"]["errors"] == 0 else 1)
