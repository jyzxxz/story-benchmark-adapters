"""
CosyVoice 3 秒复刻 - voice profile REST API。

挂在 ``/api/voice-clone`` 前缀下：

- POST   /profiles                         创建音色档案（multipart）
- GET    /profiles                         列出音色档案（可选 project_id）
- GET    /profiles/{voice_id}              获取单个档案
- DELETE /profiles/{voice_id}              删除档案
- POST   /profiles/{voice_id}/preview      生成试听样音
- POST   /synthesize                       用 voice_id 合成新语音（带缓存）

档案身份、授权确认和参考音频对象写入数据库；供应商兼容层在迁移期
继续保留原 metadata/reference.wav 目录，但这些文件不能通过 /static 读取。
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import User
from app.models_v2 import StorageObject, VoiceProfile, utcnow
from app.application.storage_service import (
    get_local_storage_backend,
    register_stored_file,
    soft_delete_storage_object,
)
from app.project_permissions import require_project_owner
from app.services.cosyvoice_clone_service import (
    CosyVoiceCloneError,
    CosyVoiceFeatureDisabledError,
    synthesize_zero_shot,
)
from app.services import tts_service as tts_module
from app.services.voice_clone_storage_service import (
    VoiceCloneStorageError,
    VoiceProfileMetadataError,
    VoiceProfileNotFoundError,
    VOICE_CLONE_UPLOAD_TMP_DIR,
    ensure_base_dirs,
    voice_clone_storage_service as storage,
)
from app.utils import logging as xlog

router = APIRouter()

VOICE_CLONE_MAX_UPLOAD_BYTES = int(os.getenv("VOICE_CLONE_MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
VOICE_CLONE_MAX_TTS_CHARS = int(os.getenv("VOICE_CLONE_MAX_TTS_CHARS", "600"))
VOICE_CLONE_UPLOAD_CHUNK_BYTES = 1024 * 1024

_ALLOWED_AUDIO_TYPES = {
    "audio/flac",
    "audio/mp4",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-flac",
    "audio/x-m4a",
    "audio/x-wav",
    "application/ogg",
}
_ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".mp4", ".flac", ".ogg", ".webm"}
_ALLOWED_GENDERS = {"male", "female", "neutral"}
_ALLOWED_AGE_GROUPS = {"child", "young", "middle", "elderly"}


# ---------- 请求 / 响应 schemas ----------


class ProfileSummary(BaseModel):
    """list / detail 返回的精简视图（实际返回字段更多，这里仅声明常用）。"""
    voice_id: str
    voice_name: str
    reference_audio_url: Optional[str] = None
    sample_audio_url: Optional[str] = None
    prompt_text: Optional[str] = None


class CreateProfileResponse(BaseModel):
    success: bool
    profile: Dict[str, Any]
    warnings: List[str] = Field(default_factory=list)


class ListProfilesResponse(BaseModel):
    success: bool
    profiles: List[Dict[str, Any]]


class GetProfileResponse(BaseModel):
    success: bool
    profile: Dict[str, Any]


class DeleteProfileResponse(BaseModel):
    success: bool
    voice_id: str
    deleted: bool


class PreviewRequest(BaseModel):
    tts_text: str = Field(
        "今夜之后，京城不会再太平。",
        min_length=1,
        max_length=VOICE_CLONE_MAX_TTS_CHARS,
        description="试听文本",
    )


class PreviewResponse(BaseModel):
    success: bool
    sample_audio_url: str
    sample_text: str


class SynthesizeRequest(BaseModel):
    voice_id: str = Field(min_length=14, max_length=38, pattern=r"^voice_[0-9a-f]{8,32}$")
    tts_text: str = Field(min_length=1, max_length=VOICE_CLONE_MAX_TTS_CHARS)
    use_cache: bool = True


class SynthesizeResponse(BaseModel):
    success: bool
    audio_url: str
    cached: bool


# ---------- 业务异常 → HTTPException ----------


def _to_http(exc: Exception, default_status: int = 400) -> HTTPException:
    """把 service 抛出的业务错误统一映射成 HTTPException，避免 traceback 泄漏。"""
    if isinstance(exc, CosyVoiceFeatureDisabledError):
        return HTTPException(status_code=503, detail="TTS 功能未启用")
    if isinstance(exc, VoiceProfileNotFoundError):
        return HTTPException(status_code=404, detail="音色档案不存在")
    if isinstance(exc, VoiceProfileMetadataError):
        return HTTPException(status_code=500, detail="音色档案暂时不可用")
    if isinstance(exc, VoiceCloneStorageError):
        return HTTPException(status_code=default_status, detail="参考音频无效或处理失败")
    if isinstance(exc, CosyVoiceCloneError):
        return HTTPException(status_code=default_status, detail="音色合成服务暂时不可用")
    # 兜底
    return HTTPException(status_code=500, detail="服务器内部错误")


def _require_tts_enabled() -> None:
    if not tts_module.TTS_ENABLED:
        raise HTTPException(status_code=503, detail="TTS 功能未启用")


def _validate_profile_fields(
    *,
    voice_name: str,
    prompt_text: str,
    gender: Optional[str],
    age_group: Optional[str],
    style_tags: Optional[str],
    description: Optional[str],
    sample_text: Optional[str],
) -> None:
    if not voice_name.strip() or len(voice_name.strip()) > 100:
        raise HTTPException(status_code=422, detail="voice_name 长度必须为 1-100 个字符")
    if not prompt_text.strip() or len(prompt_text.strip()) > 2000:
        raise HTTPException(status_code=422, detail="prompt_text 长度必须为 1-2000 个字符")
    if gender and gender not in _ALLOWED_GENDERS:
        raise HTTPException(status_code=422, detail="gender 参数无效")
    if age_group and age_group not in _ALLOWED_AGE_GROUPS:
        raise HTTPException(status_code=422, detail="age_group 参数无效")
    if description and len(description) > 1000:
        raise HTTPException(status_code=422, detail="description 不能超过 1000 个字符")
    if sample_text and len(sample_text) > VOICE_CLONE_MAX_TTS_CHARS:
        raise HTTPException(status_code=422, detail="sample_text 过长")
    tags = [tag.strip() for tag in (style_tags or "").split(",") if tag.strip()]
    if len(tags) > 10 or any(len(tag) > 32 for tag in tags):
        raise HTTPException(status_code=422, detail="style_tags 最多 10 项且每项不超过 32 个字符")


def _authorize_profile(db: Session, current_user: User, voice_id: str) -> Dict[str, Any]:
    record = (
        db.query(VoiceProfile)
        .filter(
            VoiceProfile.provider == "cosyvoice",
            VoiceProfile.provider_profile_id == voice_id,
            VoiceProfile.status != "deleted",
        )
        .first()
    )
    if record:
        if record.owner_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权访问该音色档案")
        require_project_owner(db, record.project_id, current_user)
        metadata = storage.get_profile(voice_id)
        metadata["project_id"] = record.project_id
        metadata["owner_id"] = record.owner_id
        metadata["reference_audio_url"] = f"/api/media/{record.reference_storage_object_id}"
        return metadata

    metadata = storage.get_profile(voice_id)
    owner_id = metadata.get("owner_id")
    project_id = metadata.get("project_id")

    if owner_id is not None:
        try:
            if int(owner_id) != current_user.id:
                raise HTTPException(status_code=403, detail="无权访问该音色档案")
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="音色档案暂时不可用") from exc
    if project_id not in (None, "", "null"):
        try:
            require_project_owner(db, int(project_id), current_user)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="音色档案暂时不可用") from exc
    elif owner_id is None:
        # 历史全局档案没有可验证的 owner，安全起见 fail closed。
        raise HTTPException(status_code=403, detail="该历史音色档案需要重新绑定后才能访问")
    return metadata


def _store_private_audio(
    db: Session,
    *,
    path: Path,
    owner_id: int,
    project_id: int,
    namespace: str,
) -> StorageObject:
    path = path.resolve()
    media_type = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".webm": "audio/webm",
    }.get(path.suffix.lower())
    if not path.is_file() or not media_type:
        raise HTTPException(status_code=500, detail="音色媒体文件不可用")
    backend = get_local_storage_backend()
    with path.open("rb") as handle:
        stored = backend.save_stream(
            handle,
            namespace=namespace,
            filename_or_suffix=path.suffix,
            media_type=media_type,
            max_bytes=VOICE_CLONE_MAX_UPLOAD_BYTES,
        )
    existing = (
        db.query(StorageObject)
        .filter(
            StorageObject.owner_id == owner_id,
            StorageObject.project_id == project_id,
            StorageObject.sha256 == stored.sha256,
            StorageObject.media_type == stored.media_type,
            StorageObject.status == "active",
        )
        .first()
    )
    if existing:
        backend.delete(stored.storage_key)
        return existing
    return register_stored_file(
        db,
        stored=stored,
        owner_id=owner_id,
        project_id=project_id,
        visibility="private",
        backend=backend,
    )


async def _stream_upload_to_temp(prompt_wav: UploadFile) -> Path:
    content_type = (prompt_wav.content_type or "").lower().split(";", 1)[0].strip()
    suffix = Path(prompt_wav.filename or "").suffix.lower()
    if content_type not in _ALLOWED_AUDIO_TYPES or suffix not in _ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(status_code=415, detail="仅支持 WAV、MP3、M4A、FLAC、OGG 或 WebM 音频")

    ensure_base_dirs()
    tmp_path: Optional[Path] = None
    total = 0
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix="voice-upload-",
            suffix=suffix,
            dir=VOICE_CLONE_UPLOAD_TMP_DIR,
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            while True:
                chunk = await prompt_wav.read(VOICE_CLONE_UPLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > VOICE_CLONE_MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="参考音频超过上传大小限制")
                tmp.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="prompt_wav 不能为空，请上传参考音频")
        return tmp_path
    except Exception:
        if tmp_path:
            tmp_path.unlink(missing_ok=True)
        raise
    finally:
        await prompt_wav.close()


# ---------- 1. 创建档案 ----------


# POST /api/voice-clone/profiles
@router.post("/profiles", response_model=CreateProfileResponse)
async def create_profile(
    voice_name: str = Form(...),
    prompt_text: str = Form(...),
    prompt_wav: UploadFile = File(...),
    project_id: int = Form(...),
    consent_confirmed: bool = Form(...),
    gender: Optional[str] = Form(None),
    age_group: Optional[str] = Form(None),
    style_tags: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    sample_text: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """创建一个 voice profile。multipart/form-data。

    会用 ffmpeg 把上传音频转成 16k mono wav 落到 reference.wav，并保存 metadata.json。
    """
    temp_path: Optional[Path] = None
    created_voice_id: Optional[str] = None
    try:
        require_project_owner(db, project_id, current_user)
        if consent_confirmed is not True:
            raise HTTPException(status_code=422, detail="必须确认已获得参考音频和音色授权")
        _validate_profile_fields(
            voice_name=voice_name,
            prompt_text=prompt_text,
            gender=gender,
            age_group=age_group,
            style_tags=style_tags,
            description=description,
            sample_text=sample_text,
        )
        temp_path = await _stream_upload_to_temp(prompt_wav)
        metadata, warnings = storage.create_profile_from_path(
            project_id=project_id,
            owner_id=current_user.id,
            voice_name=voice_name,
            prompt_text=prompt_text,
            prompt_wav_path=temp_path,
            prompt_wav_filename=prompt_wav.filename or "reference.wav",
            gender=gender,
            age_group=age_group,
            style_tags=style_tags,
            description=description,
            sample_text=sample_text,
        )
        created_voice_id = str(metadata["voice_id"])
        reference_wav = storage.reference_wav_for(created_voice_id)
        reference_object = _store_private_audio(
            db,
            path=reference_wav,
            owner_id=current_user.id,
            project_id=project_id,
            namespace="voice-reference",
        )
        profile_version = int(
            db.query(func.max(VoiceProfile.profile_version)).filter(
                VoiceProfile.project_id == project_id,
                VoiceProfile.display_name == voice_name.strip(),
            ).scalar()
            or 0
        ) + 1
        record = VoiceProfile(
            owner_id=current_user.id,
            project_id=project_id,
            display_name=voice_name.strip(),
            reference_storage_object_id=reference_object.id,
            provider="cosyvoice",
            provider_profile_id=created_voice_id,
            profile_version=profile_version,
            consent_confirmed_at=utcnow(),
            status="ready",
        )
        db.add(record)
        db.commit()
        metadata["reference_audio_url"] = f"/api/media/{reference_object.id}"
        return CreateProfileResponse(success=True, profile=metadata, warnings=warnings)
    except HTTPException:
        db.rollback()
        if created_voice_id:
            try:
                storage.delete_profile(created_voice_id)
            except Exception:
                pass
        raise
    except (VoiceCloneStorageError,) as e:
        db.rollback()
        if created_voice_id:
            try:
                storage.delete_profile(created_voice_id)
            except Exception:
                pass
        raise _to_http(e, 400) from e
    except Exception as e:
        db.rollback()
        if created_voice_id:
            try:
                storage.delete_profile(created_voice_id)
            except Exception:
                pass
        xlog.error(0, e, "[voice-clone] create_profile unexpected")
        raise _to_http(e) from e
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


# ---------- 2. 列表 ----------


# GET /api/voice-clone/profiles
@router.get("/profiles", response_model=ListProfilesResponse)
async def list_profiles(
    project_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        if project_id is not None:
            require_project_owner(db, project_id, current_user)
        profiles = storage.list_profiles(
            project_id=project_id,
            owner_id=None if project_id is not None else current_user.id,
        )
        records = db.query(VoiceProfile).filter(
            VoiceProfile.owner_id == current_user.id,
            VoiceProfile.status != "deleted",
        )
        if project_id is not None:
            records = records.filter(VoiceProfile.project_id == project_id)
        by_voice_id = {record.provider_profile_id: record for record in records.all()}
        profiles = [profile for profile in profiles if profile.get("voice_id") in by_voice_id]
        for profile in profiles:
            record = by_voice_id[profile["voice_id"]]
            profile["reference_audio_url"] = f"/api/media/{record.reference_storage_object_id}"
        return ListProfilesResponse(success=True, profiles=profiles)
    except HTTPException:
        raise
    except Exception as e:
        xlog.error(0, e, "[voice-clone] list_profiles unexpected")
        raise _to_http(e) from e


# ---------- 3. 详情 ----------


# GET /api/voice-clone/profiles/{voice_id}
@router.get("/profiles/{voice_id}", response_model=GetProfileResponse)
async def get_profile(
    voice_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        profile = _authorize_profile(db, current_user, voice_id)
        return GetProfileResponse(success=True, profile=profile)
    except HTTPException:
        raise
    except VoiceProfileNotFoundError as e:
        raise _to_http(e) from e
    except Exception as e:
        xlog.error(0, e, "[voice-clone] get_profile unexpected voice_id=%s", voice_id)
        raise _to_http(e) from e


# ---------- 4. 删除 ----------


# DELETE /api/voice-clone/profiles/{voice_id}
@router.delete("/profiles/{voice_id}", response_model=DeleteProfileResponse)
async def delete_profile(
    voice_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        _authorize_profile(db, current_user, voice_id)
        record = (
            db.query(VoiceProfile)
            .filter(
                VoiceProfile.provider == "cosyvoice",
                VoiceProfile.provider_profile_id == voice_id,
                VoiceProfile.owner_id == current_user.id,
                VoiceProfile.status != "deleted",
            )
            .with_for_update()
            .first()
        )
        if record:
            reference = db.query(StorageObject).filter(
                StorageObject.id == record.reference_storage_object_id
            ).first()
            if reference:
                soft_delete_storage_object(db, reference)
            record.status = "deleted"
            record.deleted_at = utcnow()
        result = storage.delete_profile(voice_id)
        db.commit()
        return DeleteProfileResponse(success=True, voice_id=result["voice_id"], deleted=True)
    except HTTPException:
        raise
    except VoiceProfileNotFoundError as e:
        raise _to_http(e) from e
    except Exception as e:
        db.rollback()
        xlog.error(0, e, "[voice-clone] delete_profile unexpected voice_id=%s", voice_id)
        raise _to_http(e) from e


# ---------- 5. 试听样音 ----------


# POST /api/voice-clone/profiles/{voice_id}/preview
@router.post("/profiles/{voice_id}/preview", response_model=PreviewResponse)
async def preview_profile(
    voice_id: str,
    body: PreviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成试听样音并写回 voice 目录下的 sample.wav，同时更新 metadata。"""
    _require_tts_enabled()
    try:
        metadata = _authorize_profile(db, current_user, voice_id)
        prompt_text = metadata.get("prompt_text") or ""
        if not prompt_text:
            raise HTTPException(status_code=400, detail="该档案缺少 prompt_text，无法生成试听")
        ref_wav = storage.reference_wav_for(voice_id)
        if not ref_wav.exists():
            raise HTTPException(status_code=400, detail="reference.wav 不存在，请重新上传参考音频")

        sample_text = (body.tts_text or "今夜之后，京城不会再太平。").strip()
        if not sample_text:
            raise HTTPException(status_code=422, detail="tts_text 不能为空")
        voice_dir = ref_wav.parent
        sample_wav = voice_dir / "sample.wav"

        await synthesize_zero_shot(
            tts_text=sample_text,
            prompt_text=prompt_text,
            prompt_wav_path=str(ref_wav),
            output_wav_path=sample_wav,
        )

        updated = storage.update_sample(voice_id, sample_wav, sample_text)
        sample_object = _store_private_audio(
            db,
            path=sample_wav,
            owner_id=current_user.id,
            project_id=int(metadata["project_id"]),
            namespace="voice-sample",
        )
        db.commit()
        sample_url = f"/api/media/{sample_object.id}"
        return PreviewResponse(success=True, sample_audio_url=sample_url, sample_text=sample_text)
    except HTTPException:
        raise
    except (VoiceProfileNotFoundError, VoiceProfileMetadataError) as e:
        raise _to_http(e) from e
    except CosyVoiceCloneError as e:
        raise _to_http(e, 502) from e
    except Exception as e:
        db.rollback()
        xlog.error(0, e, "[voice-clone] preview unexpected voice_id=%s", voice_id)
        raise _to_http(e) from e


# ---------- 6. 合成并缓存 ----------


# POST /api/voice-clone/synthesize
@router.post("/synthesize", response_model=SynthesizeResponse)
async def synthesize(
    body: SynthesizeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    ):
    _require_tts_enabled()
    try:
        metadata = _authorize_profile(db, current_user, body.voice_id)
        prompt_text = metadata.get("prompt_text") or ""
        if not prompt_text:
            raise HTTPException(status_code=400, detail="该档案缺少 prompt_text，无法合成")
        ref_wav = storage.reference_wav_for(body.voice_id)
        if not ref_wav.exists():
            raise HTTPException(status_code=400, detail="reference.wav 不存在，请重新上传参考音频")
        if not body.tts_text or not body.tts_text.strip():
            raise HTTPException(status_code=400, detail="tts_text 不能为空")

        cache_key = storage.build_cache_key(body.voice_id, body.tts_text, prompt_text)
        cache_path = storage.cache_path_for(body.voice_id, cache_key)
        if body.use_cache and cache_path.exists() and cache_path.stat().st_size > 0:
            xlog.info(0, "[voice-clone] synthesize cache hit voice_id=%s key=%s", body.voice_id, cache_key)
            audio_object = _store_private_audio(
                db,
                path=cache_path,
                owner_id=current_user.id,
                project_id=int(metadata["project_id"]),
                namespace="tts/voice-clone",
            )
            db.commit()
            return SynthesizeResponse(
                success=True,
                audio_url=f"/api/media/{audio_object.id}",
                cached=True,
            )

        await synthesize_zero_shot(
            tts_text=body.tts_text,
            prompt_text=prompt_text,
            prompt_wav_path=str(ref_wav),
            output_wav_path=cache_path,
        )
        audio_object = _store_private_audio(
            db,
            path=cache_path,
            owner_id=current_user.id,
            project_id=int(metadata["project_id"]),
            namespace="tts/voice-clone",
        )
        db.commit()
        return SynthesizeResponse(
            success=True,
            audio_url=f"/api/media/{audio_object.id}",
            cached=False,
        )
    except HTTPException:
        raise
    except (VoiceProfileNotFoundError, VoiceProfileMetadataError) as e:
        raise _to_http(e) from e
    except CosyVoiceCloneError as e:
        raise _to_http(e, 502) from e
    except Exception as e:
        db.rollback()
        xlog.error(0, e, "[voice-clone] synthesize unexpected voice_id=%s", body.voice_id)
        raise _to_http(e) from e
