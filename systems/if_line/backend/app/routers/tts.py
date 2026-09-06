"""
TTS API 路由
提供语音合成接口
"""
import hashlib
import os
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.database import get_db
from app.models import Asset, User
from app.project_permissions import require_project_owner
from app.services.tts_service import (
    tts_service,
    TTS_AUDIO_BASE_URL,
    TTS_ENABLED,
    TTS_ENGINE,
    TTS_FALLBACK_ENABLED,
    TTS_FALLBACK_ENGINE,
    TTS_MAX_TEXT_LENGTH,
)
from app.services.minimax_tts_provider import MINIMAX_TTS_DEFAULT_VOICE_ID
from app.application.storage_service import get_local_storage_backend, register_stored_file
from app.models_v2 import StorageObject
from app.utils import logging as xlog

router = APIRouter()

_ALLOWED_GENDERS = {"male", "female"}
_ALLOWED_AGE_GROUPS = {"young", "middle", "elderly"}
_ALLOWED_EMOTIONS = {"calm", "happy", "sad", "angry", "excited"}
_LEGACY_ALLOWED_SPEAKERS = {
    value.strip().lower()
    for value in os.getenv("TTS_ALLOWED_SPEAKERS", "x4_mingge").split(",")
    if value.strip()
}


def _store_tts_result(db: Session, *, result: dict, user: User, project_id: int | None) -> str:
    audio_url = str(result.get("audio_url") or "")
    prefix = TTS_AUDIO_BASE_URL.rstrip("/") + "/"
    if not audio_url.startswith(prefix):
        raise HTTPException(status_code=502, detail="语音服务返回了无效的媒体位置")
    filename = audio_url[len(prefix) :]
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=502, detail="语音服务返回了无效的媒体文件")
    source_root = Path(tts_service.output_dir).resolve()
    source = (source_root / filename).resolve()
    try:
        source.relative_to(source_root)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="语音服务返回了无效的媒体文件") from exc
    media_type = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
    }.get(source.suffix.lower())
    if not source.is_file() or not media_type:
        raise HTTPException(status_code=502, detail="语音媒体文件不可用")
    backend = get_local_storage_backend()
    with source.open("rb") as handle:
        stored = backend.save_stream(
            handle,
            namespace="tts-preview",
            filename_or_suffix=source.suffix,
            media_type=media_type,
        )
    existing = (
        db.query(StorageObject)
        .filter(
            StorageObject.owner_id == user.id,
            StorageObject.project_id == project_id,
            StorageObject.sha256 == stored.sha256,
            StorageObject.media_type == stored.media_type,
            StorageObject.status == "active",
        )
        .first()
    )
    if existing:
        backend.delete(stored.storage_key)
        return f"/api/media/{existing.id}"
    obj = register_stored_file(
        db,
        stored=stored,
        owner_id=user.id,
        project_id=project_id,
        visibility="private",
        backend=backend,
    )
    return f"/api/media/{obj.id}"


class TTSSynthesizeRequest(BaseModel):
    """TTS 合成请求"""
    text: str = Field(..., min_length=1, max_length=TTS_MAX_TEXT_LENGTH, description="要合成的文本")
    character_name: Optional[str] = Field(None, max_length=100, description="角色名")
    character_voice: Optional[str] = Field(None, max_length=200, description="角色卡里的 voice 字段")
    gender: Optional[str] = Field(None, description="性别：male/female")
    age: Optional[str] = Field(None, description="年龄分组：young/middle/elderly")
    emotion: Optional[str] = Field(None, description="情绪：calm/happy/sad/angry/excited")
    speaker: Optional[str] = Field(None, max_length=100, description="直接指定已配置的 TTS 音色 ID")
    emotion_prompt: Optional[str] = Field(None, max_length=50, description="直接指定已配置的情绪 prompt")
    project_id: Optional[int] = Field(
        None,
        description="项目 ID。传了之后会走项目级音色绑定：同项目内不同角色优先独占不同音色，"
                    "不够再走参数差异化。character_name 也必须传，character_id 由后端从 name+project_id 派生。",
    )


class TTSSynthesizeResponse(BaseModel):
    """TTS 合成响应"""
    success: bool
    audio_url: Optional[str] = None
    cached: Optional[bool] = None
    speaker: Optional[str] = None
    emotion_prompt: Optional[str] = None
    # 实际使用的引擎：aliyun / xunfei / minimax。便于前端区分主路径与 fallback。
    engine: Optional[str] = None
    # 实际使用的模型（minimax 必有；aliyun/xunfei 可空）
    model: Optional[str] = None
    # 是否经过 fallback（true 时 engine 是兜底引擎，primary_engine 是原主引擎）
    fallback_used: Optional[bool] = None
    # fallback 时填原主引擎；否则 == engine
    primary_engine: Optional[str] = None
    # fallback 时填主供应商错误文案；否则空。帮助排查主供应商故障
    primary_error: Optional[str] = None
    error: Optional[str] = None


# POST /api/tts/synthesize
@router.post("/synthesize", response_model=TTSSynthesizeResponse)
async def synthesize_speech(
    request: TTSSynthesizeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    合成语音

    - 传 ``project_id`` + ``character_name`` → 走项目级绑定（同项目内不同角色优先独占不同音色）
    - 不传 ``project_id`` → 走 matcher 路径（仍然要求登录）

    返回音频 URL，支持缓存。
    """
    if not TTS_ENABLED:
        xlog.warn(0, "[tts] synthesize rejected disabled")
        raise HTTPException(status_code=503, detail="TTS 功能未启用")

    text_value = (request.text or "").strip()
    if not text_value:
        raise HTTPException(status_code=422, detail="text 不能为空")
    if request.gender and request.gender not in _ALLOWED_GENDERS:
        raise HTTPException(status_code=422, detail="gender 参数无效")
    if request.age and request.age not in _ALLOWED_AGE_GROUPS:
        raise HTTPException(status_code=422, detail="age 参数无效")
    if request.emotion and request.emotion not in _ALLOWED_EMOTIONS:
        raise HTTPException(status_code=422, detail="emotion 参数无效")

    configured_speakers = {
        str(profile.get("speaker") or profile.get("vcn") or "").strip().lower()
        for profile in getattr(tts_service, "voice_profiles", [])
        if profile.get("speaker") or profile.get("vcn")
    } | {
        str(fallback_ids.get("minimax") or "").strip().lower()
        for profile in getattr(tts_service, "voice_profiles", [])
        if isinstance((fallback_ids := profile.get("fallback_voice_ids")), dict)
        and fallback_ids.get("minimax")
    } | _LEGACY_ALLOWED_SPEAKERS | {MINIMAX_TTS_DEFAULT_VOICE_ID.lower()}
    if request.speaker and request.speaker.strip().lower() not in configured_speakers:
        raise HTTPException(status_code=422, detail="speaker 参数无效")

    configured_emotions = {
        str(profile.get("emotion_prompt") or "").strip().lower()
        for profile in getattr(tts_service, "voice_profiles", [])
        if profile.get("emotion_prompt")
    } | _ALLOWED_EMOTIONS
    if request.emotion_prompt and request.emotion_prompt.strip().lower() not in configured_emotions:
        raise HTTPException(status_code=422, detail="emotion_prompt 参数无效")

    xlog.info(
        0, "[tts] synthesize start text_chars=%d character=%s speaker=%s project=%s gender=%s age=%s",
        len(request.text or ""), request.character_name or "", request.speaker or "",
        request.project_id, request.gender, request.age,
    )

    project = None
    if request.project_id is not None:
        project = require_project_owner(db, request.project_id, current_user)

    binding = None
    if project and request.character_name and not request.speaker:
        xlog.info(0, "[tts] entering project binding path project=%d char=%s",
                  request.project_id, request.character_name)
        # 走项目级绑定：让同项目内不同角色尽量用不同 vcn
        # （character_id 算法与 prompt_builder_service.generate_character_id 一致）
        character_id = hashlib.md5(
            f"{request.character_name}|{request.project_id}".encode("utf-8")
        ).hexdigest()[:12]
        try:
            from app.services.voice_binding_service import VoiceBindingService
            binding = VoiceBindingService(db).get_or_allocate(
                project_id=request.project_id,
                character_id=character_id,
                character_name=request.character_name,
                gender=request.gender,
                age=request.age,
                character_voice=request.character_voice,
                emotion=request.emotion,
            )
        except Exception as e:
            # 绑定失败不阻塞合成：降级到原 matcher 路径
            xlog.warn(0, "[tts] synthesize voice_binding failed project=%d char=%s err=%s",
                      request.project_id, request.character_name, e)
            binding = None

    if binding:
        try:
            result = await tts_service.synthesize(
                text=text_value,
                character_name=request.character_name,
                speaker=binding.vcn,
                emotion_prompt=binding.emotion,
                speed=binding.speed,
                pitch=binding.pitch,
                volume=binding.volume,
                minimax_voice_id=binding.minimax_voice_id,
                minimax_speed=binding.minimax_speed,
                minimax_pitch=binding.minimax_pitch,
                minimax_volume=binding.minimax_volume,
            )
        except Exception as exc:
            xlog.error(0, exc, "[tts] synthesize provider exception")
            raise HTTPException(status_code=502, detail="语音合成服务暂时不可用") from exc
    else:
        try:
            result = await tts_service.synthesize(
                text=text_value,
                character_name=request.character_name,
                character_voice=request.character_voice,
                gender=request.gender,
                age=request.age,
                emotion=request.emotion,
                speaker=request.speaker,
                emotion_prompt=request.emotion_prompt,
            )
        except Exception as exc:
            xlog.error(0, exc, "[tts] synthesize provider exception")
            raise HTTPException(status_code=502, detail="语音合成服务暂时不可用") from exc

    if result.get("success"):
        result = dict(result)
        if result.get("audio_url"):
            try:
                result["audio_url"] = _store_tts_result(
                    db,
                    result=result,
                    user=current_user,
                    project_id=project.id if project else None,
                )
            except Exception:
                db.rollback()
                raise
        if project and result.get("audio_url"):
            existing_asset = db.query(Asset).filter(
                Asset.project_id == project.id,
                Asset.asset_type == "voice_preview",
                Asset.image_url == result["audio_url"],
            ).first()
            if not existing_asset:
                db.add(Asset(
                    project_id=project.id,
                    chapter_index=None,
                    asset_type="voice_preview",
                    target_name=request.character_name or "preview",
                    prompt=text_value,
                    image_url=result["audio_url"],
                    character_id=None,
                    emotion=request.emotion,
                    status="completed",
                    processed=1,
                ))
        db.commit()
        xlog.info(0, "[tts] synthesize ok cached=%s speaker=%s", result.get("cached"), result.get("speaker"))
    else:
        xlog.warn(0, "[tts] synthesize failed error=%s", result.get("error"))
        result = dict(result)
        result["error"] = "语音合成失败，请稍后重试"
    return TTSSynthesizeResponse(**result)


# GET /api/tts/status
@router.get("/status")
async def get_tts_status():
    """获取 TTS 服务状态。

    返回字段：
    - ``enabled`` / ``engine``：主引擎开关与名称
    - ``fallback_enabled`` / ``fallback_engine``：兜底开关与目标引擎
    - ``fallback_configured``：兜底引擎的 Key 是否已配置（**不返回 Key 本身**）。
      仅当 fallback_enabled=true 且对应 Key 非占位符时为 true。
    """
    # 检测 MiniMax Key 是否非占位符配置。Provider 自身读 env，这里复用同口径判断。
    fallback_configured = False
    if TTS_FALLBACK_ENABLED and TTS_FALLBACK_ENGINE == "minimax":
        try:
            from app.services.minimax_tts_provider import minimax_tts_provider
            fallback_configured = bool(minimax_tts_provider.configured)
        except Exception:
            fallback_configured = False
    xlog.info(
        0, "[tts] status enabled=%s engine=%s fallback_enabled=%s fallback_engine=%s fallback_configured=%s",
        TTS_ENABLED, TTS_ENGINE, TTS_FALLBACK_ENABLED, TTS_FALLBACK_ENGINE, fallback_configured,
    )
    return {
        "enabled": TTS_ENABLED,
        "engine": TTS_ENGINE,
        "fallback_enabled": TTS_FALLBACK_ENABLED,
        "fallback_engine": TTS_FALLBACK_ENGINE if TTS_FALLBACK_ENABLED else None,
        "fallback_configured": fallback_configured,
    }
