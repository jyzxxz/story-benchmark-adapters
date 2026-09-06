"""Immutable resource snapshots used by deterministic VNGraph compilation."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.application.hashing import content_hash
from app.models import Asset
from app.models_v2 import (
    AssetVersion,
    StorageObject,
    VoiceLine,
    VoiceLineVersion,
)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def asset_version_payload(version: AssetVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "asset_id": version.asset_id,
        "source_kind": version.source_kind,
        "source_revision_id": version.source_revision_id,
        "source_hash": version.source_hash,
        "generation_task_id": version.generation_task_id,
        "storage_object_id": version.storage_object_id,
        "version_no": version.version_no,
        "cache_key": version.cache_key,
        "provider": version.provider,
        "model": version.model,
        "prompt": version.prompt,
        "prompt_hash": version.prompt_hash,
        "prompt_version": version.prompt_version,
        "negative_prompt_hash": version.negative_prompt_hash,
        "seed": version.seed,
        "width": version.width,
        "height": version.height,
        "postprocess_version": version.postprocess_version,
        "validator_version": version.validator_version,
        "quality_score": version.quality_score,
        "safety_status": version.safety_status,
        "rights_metadata": dict(version.rights_metadata or {}),
        "asset_spec_json": dict(version.asset_spec_json or {}),
        "render_spec_json": dict(version.render_spec_json or {}),
        "render_spec_hash": version.render_spec_hash,
    }


def storage_object_payload(storage: StorageObject) -> dict[str, Any]:
    return {
        "id": storage.id,
        "project_id": storage.project_id,
        "media_type": storage.media_type,
        "byte_size": storage.byte_size,
        "sha256": storage.sha256,
        "visibility": storage.visibility,
        "status": storage.status,
        "deleted_at": _timestamp(storage.deleted_at),
    }


def asset_version_reference(
    version: AssetVersion,
    storage: StorageObject | None,
) -> dict[str, Any]:
    version_payload = asset_version_payload(version)
    storage_payload = storage_object_payload(storage) if storage else None
    return {
        "asset_version_id": version.id,
        "asset_id": version.asset_id,
        "version_no": version.version_no,
        "asset_source_kind": version.source_kind,
        "asset_source_revision_id": version.source_revision_id,
        "asset_source_hash": version.source_hash,
        "cache_key": version.cache_key,
        "prompt_hash": version.prompt_hash,
        "prompt_version": version.prompt_version,
        "storage_object_id": version.storage_object_id,
        "safety_status": version.safety_status,
        "quality_score": version.quality_score,
        "asset_version_hash": content_hash(version_payload),
        "storage_object_hash": content_hash(storage_payload) if storage_payload else None,
        "storage_status": storage.status if storage else None,
        "media_type": storage.media_type if storage else None,
        "storage_sha256": storage.sha256 if storage else None,
        "storage_byte_size": storage.byte_size if storage else None,
    }


def validate_asset_version_reference(
    db: Session,
    reference: dict[str, Any],
    *,
    expected_project_id: int,
) -> None:
    version_id = str(reference.get("asset_version_id") or "")
    if not version_id:
        raise ValueError("resource manifest is missing asset_version_id")
    version = db.query(AssetVersion).filter(AssetVersion.id == version_id).one_or_none()
    if not version:
        raise ValueError(f"AssetVersion does not exist: {version_id}")
    asset = db.query(Asset).filter(Asset.id == version.asset_id).one_or_none()
    if not asset or asset.project_id != expected_project_id:
        raise ValueError(f"AssetVersion belongs to another project: {version_id}")
    storage = (
        db.query(StorageObject)
        .filter(StorageObject.id == version.storage_object_id)
        .one_or_none()
        if version.storage_object_id
        else None
    )
    if storage and storage.project_id not in {None, expected_project_id}:
        raise ValueError(f"AssetVersion storage belongs to another project: {version_id}")
    expected = asset_version_reference(version, storage)
    for key, value in expected.items():
        if reference.get(key) != value:
            raise ValueError(f"AssetVersion snapshot mismatch: {version_id}:{key}")


def voice_line_payload(line: VoiceLine) -> dict[str, Any]:
    return {
        "id": line.id,
        "chapter_revision_id": line.chapter_revision_id,
        "occurrence_id": line.occurrence_id,
        "order_index": line.order_index,
        "kind": line.kind,
        "text": line.text,
        "speaker_character_id": line.speaker_character_id,
        "speaker_name": line.speaker_name,
        "emotion": line.emotion,
        "voice_profile_id": line.voice_profile_id,
        "voice_profile_version": line.voice_profile_version,
        "audio_asset_version_id": line.audio_asset_version_id,
        "status": line.status,
    }


def ensure_voice_line_version(db: Session, line: VoiceLine) -> VoiceLineVersion:
    payload = voice_line_payload(line)
    digest = content_hash(payload)
    existing = (
        db.query(VoiceLineVersion)
        .filter(
            VoiceLineVersion.voice_line_id == line.id,
            VoiceLineVersion.content_hash == digest,
        )
        .one_or_none()
    )
    if existing:
        if (
            content_hash(existing.content_json) != existing.content_hash
            or existing.voice_line_id != line.id
            or existing.chapter_revision_id != line.chapter_revision_id
            or existing.audio_asset_version_id != line.audio_asset_version_id
        ):
            raise ValueError(f"VoiceLineVersion is corrupt: {existing.id}")
        return existing

    version_no = int(
        db.query(func.max(VoiceLineVersion.version_no))
        .filter(VoiceLineVersion.voice_line_id == line.id)
        .scalar()
        or 0
    ) + 1
    version = VoiceLineVersion(
        voice_line_id=line.id,
        chapter_revision_id=line.chapter_revision_id,
        version_no=version_no,
        content_hash=digest,
        content_json=payload,
        audio_asset_version_id=line.audio_asset_version_id,
    )
    db.add(version)
    db.flush()
    return version


def voice_line_version_manifest_item(
    version: VoiceLineVersion,
    audio_version: AssetVersion | None,
    storage: StorageObject | None,
    *,
    audio_url: str | None,
) -> dict[str, Any]:
    item = dict(version.content_json or {})
    item.update(
        {
            "voice_line_version_id": version.id,
            "voice_line_version_no": version.version_no,
            "voice_line_version_hash": version.content_hash,
            "audio_asset_version": (
                asset_version_reference(audio_version, storage)
                if audio_version
                else None
            ),
            "audio_url": audio_url,
        }
    )
    return item


def validate_voice_line_version_manifest_item(
    db: Session,
    item: dict[str, Any],
    *,
    expected_project_id: int,
    expected_chapter_revision_id: str,
) -> None:
    version_id = str(item.get("voice_line_version_id") or "")
    version = (
        db.query(VoiceLineVersion)
        .filter(VoiceLineVersion.id == version_id)
        .one_or_none()
    )
    if not version:
        raise ValueError(f"VoiceLineVersion does not exist: {version_id}")
    payload = dict(version.content_json or {})
    if (
        content_hash(version.content_json) != version.content_hash
        or item.get("voice_line_version_hash") != version.content_hash
        or item.get("voice_line_version_no") != version.version_no
        or version.voice_line_id != payload.get("id")
        or version.chapter_revision_id != payload.get("chapter_revision_id")
        or version.audio_asset_version_id != payload.get("audio_asset_version_id")
        or version.chapter_revision_id != expected_chapter_revision_id
    ):
        raise ValueError(f"VoiceLineVersion snapshot mismatch: {version_id}")
    for key, value in payload.items():
        if item.get(key) != value:
            raise ValueError(f"VoiceLineVersion content mismatch: {version_id}:{key}")

    audio_reference = item.get("audio_asset_version")
    if version.audio_asset_version_id:
        if not isinstance(audio_reference, dict):
            raise ValueError(f"VoiceLineVersion audio snapshot missing: {version_id}")
        if audio_reference.get("asset_version_id") != version.audio_asset_version_id:
            raise ValueError(f"VoiceLineVersion audio mismatch: {version_id}")
        validate_asset_version_reference(
            db,
            audio_reference,
            expected_project_id=expected_project_id,
        )
    elif audio_reference is not None:
        raise ValueError(f"VoiceLineVersion has unexpected audio snapshot: {version_id}")
