"""Copy legacy generated media into StorageObject/AssetVersion.

Dry-run is the default.  Apply mode copies files (it never moves or deletes
legacy files), creates immutable versions and binds visual assets to current
revisions.  Legacy voice lines are ordered by their first matching text offset
in the immutable chapter; unmatched lines keep legacy id order and are counted
as uncertain.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import func, inspect

from app.application.hashing import content_hash
from app.application.storage_service import (
    LocalStorageBackend,
    StorageServiceError,
    register_stored_file,
)
from app.database import SessionLocal, engine
from app.models import Asset, Project
from app.models_v2 import (
    AssetBinding,
    AssetVersion,
    ChapterHead,
    ChapterRevision,
    ProjectContentHead,
    StorageObject,
    VoiceLine,
)


STATIC_ROOT = (BACKEND_ROOT / "static").resolve()
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".webm": "audio/webm",
}


@dataclass
class Counters:
    legacy_assets_seen: int = 0
    local_files_found: int = 0
    external_or_missing_skipped: int = 0
    owner_missing_skipped: int = 0
    source_revision_missing_skipped: int = 0
    storage_objects_created: int = 0
    asset_versions_created: int = 0
    asset_bindings_created: int = 0
    voice_lines_created: int = 0
    voice_groups_skipped_existing: int = 0
    voice_order_uncertain: int = 0
    skipped_existing: int = 0


def _assert_schema() -> None:
    required = {"storage_objects", "asset_versions", "asset_bindings", "voice_lines"}
    missing = sorted(required - set(inspect(engine).get_table_names()))
    if missing:
        raise RuntimeError(f"v2 media schema missing; run alembic upgrade head first: {missing}")


def _resolve_legacy_url(url: str | None) -> Path | None:
    if not url:
        return None
    parsed = urlparse(str(url))
    if parsed.scheme and parsed.scheme not in {"http", "https"}:
        return None
    path = unquote(parsed.path if parsed.scheme else str(url)).replace("\\", "/")
    if path.startswith("/static/"):
        relative = path[len("/static/") :]
    elif path.startswith("static/"):
        relative = path[len("static/") :]
    else:
        return None
    candidate = (STATIC_ROOT / relative).resolve()
    try:
        candidate.relative_to(STATIC_ROOT)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _source_for_asset(db, asset: Asset) -> tuple[str, str] | None:
    if asset.chapter_index is not None:
        chapter_head = (
            db.query(ChapterHead)
            .filter(
                ChapterHead.project_id == asset.project_id,
                ChapterHead.chapter_index == asset.chapter_index,
            )
            .first()
        )
        if chapter_head:
            return "chapter_revision", chapter_head.current_revision_id
    project_head = db.query(ProjectContentHead).filter(ProjectContentHead.project_id == asset.project_id).first()
    if project_head and project_head.current_bible_revision_id:
        return "bible_revision", project_head.current_bible_revision_id
    return None


def _legacy_version(db, asset_id: int) -> AssetVersion | None:
    for version in db.query(AssetVersion).filter(AssetVersion.asset_id == asset_id).all():
        rights = version.rights_metadata or {}
        if rights.get("legacy_asset_id") == asset_id:
            return version
    return None


def _copy_asset(
    db,
    *,
    asset: Asset,
    project: Project,
    source_kind: str,
    source_revision_id: str,
    source_path: Path,
    backend: LocalStorageBackend,
    counters: Counters,
    created_storage_keys: list[str],
) -> AssetVersion:
    existing = _legacy_version(db, asset.id)
    if existing:
        counters.skipped_existing += 1
        return existing
    media_type = MEDIA_TYPES.get(source_path.suffix.lower()) or mimetypes.guess_type(source_path.name)[0]
    if media_type not in set(MEDIA_TYPES.values()):
        raise StorageServiceError("legacy media type is unsupported")
    namespace = "legacy-audio" if media_type.startswith("audio/") else "legacy-image"
    stored = None
    try:
        with source_path.open("rb") as handle:
            stored = backend.save_stream(
                handle,
                namespace=namespace,
                filename_or_suffix=source_path.suffix,
                media_type=media_type,
            )
        created_storage_keys.append(stored.storage_key)
        obj = register_stored_file(
            db,
            stored=stored,
            owner_id=project.owner_id,
            project_id=project.id,
            visibility="release",
            backend=backend,
        )
        version_no = int(
            db.query(func.max(AssetVersion.version_no)).filter(AssetVersion.asset_id == asset.id).scalar() or 0
        ) + 1
        version = AssetVersion(
            asset_id=asset.id,
            source_revision_id=source_revision_id,
            storage_object_id=obj.id,
            version_no=version_no,
            cache_key=content_hash(
                {"legacy_asset_id": asset.id, "sha256": stored.sha256, "source_revision_id": source_revision_id}
            ),
            provider="legacy-import",
            model="unknown",
            prompt=asset.prompt,
            prompt_hash=content_hash(asset.prompt or ""),
            prompt_version="legacy",
            seed=asset.seed,
            postprocess_version="legacy",
            validator_version="legacy-unverified",
            safety_status="legacy-unreviewed",
            rights_metadata={
                "origin": "legacy-import",
                "legacy_asset_id": asset.id,
                "legacy_url_hash": hashlib.sha256((asset.image_url or "").encode("utf-8")).hexdigest(),
                "rights_review_required": True,
            },
        )
        db.add(version)
        db.flush()
        counters.storage_objects_created += 1
        counters.asset_versions_created += 1
        return version
    except Exception:
        if stored is not None:
            backend.delete(stored.storage_key)
            try:
                created_storage_keys.remove(stored.storage_key)
            except ValueError:
                pass
        raise


def _ensure_visual_binding(
    db,
    *,
    asset: Asset,
    source_kind: str,
    source_revision_id: str,
    version: AssetVersion,
    counters: Counters,
) -> None:
    role = "portrait" if asset.asset_type == "portrait" else asset.asset_type
    segment_key = f"legacy-asset-{asset.id}"
    existing = (
        db.query(AssetBinding)
        .filter(
            AssetBinding.project_id == asset.project_id,
            AssetBinding.source_kind == source_kind,
            AssetBinding.source_id == source_revision_id,
            AssetBinding.segment_key == segment_key,
            AssetBinding.role == role,
        )
        .first()
    )
    if existing:
        return
    db.add(
        AssetBinding(
            project_id=asset.project_id,
            source_kind=source_kind,
            source_id=source_revision_id,
            segment_key=segment_key,
            role=role,
            asset_version_id=version.id,
            order_index=asset.id,
            required=False,
        )
    )
    counters.asset_bindings_created += 1


def _voice_order(chapter: ChapterRevision, assets: list[Asset], counters: Counters) -> list[Asset]:
    positions: list[tuple[int, int, Asset]] = []
    cursor_by_text: dict[str, int] = defaultdict(int)
    for asset in assets:
        text = (asset.prompt or "").strip()
        start = cursor_by_text[text]
        position = chapter.content.find(text, start) if text else -1
        if position >= 0:
            cursor_by_text[text] = position + len(text)
            positions.append((position, asset.id, asset))
        else:
            counters.voice_order_uncertain += 1
            positions.append((10**12 + asset.id, asset.id, asset))
    return [item[2] for item in sorted(positions, key=lambda item: (item[0], item[1]))]


def migrate(*, apply: bool) -> Counters:
    _assert_schema()
    counters = Counters()
    db = SessionLocal()
    backend = LocalStorageBackend()
    # The migration is one database transaction. Keep a compensating list so
    # a late database failure cannot leave hundreds of copied files orphaned
    # after the transaction is rolled back.
    created_storage_keys: list[str] = []
    try:
        projects = {row.id: row for row in db.query(Project).all()}
        voice_assets: dict[tuple[int, int], list[Asset]] = defaultdict(list)
        for asset in db.query(Asset).order_by(Asset.project_id, Asset.chapter_index, Asset.id).all():
            counters.legacy_assets_seen += 1
            project = projects.get(asset.project_id)
            if not project or not project.owner_id:
                counters.owner_missing_skipped += 1
                continue
            source = _source_for_asset(db, asset)
            if not source:
                counters.source_revision_missing_skipped += 1
                continue
            path = _resolve_legacy_url(asset.image_url)
            if not path:
                counters.external_or_missing_skipped += 1
                continue
            counters.local_files_found += 1
            if asset.asset_type == "voice_line" and asset.chapter_index is not None:
                voice_assets[(asset.project_id, asset.chapter_index)].append(asset)
                continue
            if not apply:
                continue
            version = _copy_asset(
                db,
                asset=asset,
                project=project,
                source_kind=source[0],
                source_revision_id=source[1],
                source_path=path,
                backend=backend,
                counters=counters,
                created_storage_keys=created_storage_keys,
            )
            _ensure_visual_binding(
                db,
                asset=asset,
                source_kind=source[0],
                source_revision_id=source[1],
                version=version,
                counters=counters,
            )

        for (project_id, chapter_index), assets in sorted(voice_assets.items()):
            chapter_head = db.query(ChapterHead).filter(
                ChapterHead.project_id == project_id,
                ChapterHead.chapter_index == chapter_index,
            ).first()
            chapter = (
                db.query(ChapterRevision).filter(ChapterRevision.id == chapter_head.current_revision_id).first()
                if chapter_head
                else None
            )
            if not chapter:
                counters.source_revision_missing_skipped += len(assets)
                continue
            if db.query(VoiceLine).filter(VoiceLine.chapter_revision_id == chapter.id).count():
                counters.voice_groups_skipped_existing += 1
                continue
            ordered = _voice_order(chapter, assets, counters)
            if not apply:
                continue
            project = projects[project_id]
            for order_index, asset in enumerate(ordered):
                path = _resolve_legacy_url(asset.image_url)
                if not path:
                    continue
                version = _copy_asset(
                    db,
                    asset=asset,
                    project=project,
                    source_kind="chapter_revision",
                    source_revision_id=chapter.id,
                    source_path=path,
                    backend=backend,
                    counters=counters,
                    created_storage_keys=created_storage_keys,
                )
                speaker = (asset.target_name or "旁白").strip()
                text = asset.prompt or ""
                occurrence = content_hash(
                    {
                        "chapter_revision_id": chapter.id,
                        "order_index": order_index,
                        "speaker": speaker,
                        "text": text,
                        "legacy_asset_id": asset.id,
                    }
                )
                db.add(
                    VoiceLine(
                        chapter_revision_id=chapter.id,
                        occurrence_id=occurrence,
                        order_index=order_index,
                        kind="narration" if speaker.lower() in {"旁白", "narrator"} else "dialogue",
                        text=text,
                        speaker_name=speaker,
                        emotion=asset.emotion,
                        audio_asset_version_id=version.id,
                        status="ready",
                    )
                )
                counters.voice_lines_created += 1
        if apply:
            db.commit()
        else:
            db.rollback()
        return counters
    except BaseException:
        db.rollback()
        for storage_key in reversed(created_storage_keys):
            backend.delete(storage_key)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = migrate(apply=bool(args.apply))
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", **asdict(result)}, ensure_ascii=False, indent=2))
