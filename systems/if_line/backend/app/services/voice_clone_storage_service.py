"""
CosyVoice 3 秒复刻 - 文件存储版音色档案管理（独立模块，不接数据库）。

每个音色目录的形状：

    backend/static/voice_refs/project_{project_id|default}/voice_{voice_id}/
        reference.wav     # 用户上传的参考音频（统一转 16k mono wav）
        sample.wav        # 可选，preview 接口生成的试听样音
        metadata.json     # 档案元数据

合成结果缓存在：

    backend/static/tts_cache/voice_clone/voice_{voice_id}/{hash}.wav

注意：本模块故意不依赖 SQLAlchemy，也不导入任何 if_line 业务模型，方便后续稳定后整体迁入数据库。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.utils import logging as xlog

# ---------- 路径常量 ----------

# backend/app/services/voice_clone_storage_service.py -> backend/
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = _BACKEND_ROOT / "static"
VOICE_REFS_DIR = STATIC_DIR / "voice_refs"
TTS_CACHE_DIR = STATIC_DIR / "tts_cache"
VOICE_CLONE_CACHE_DIR = TTS_CACHE_DIR / "voice_clone"
VOICE_CLONE_UPLOAD_TMP_DIR = _BACKEND_ROOT / "storage" / "tmp" / "voice_clone"

DEFAULT_PROJECT_KEY = "project_default"

ENGINE_NAME = "cosyvoice"
MODE_NAME = "zero_shot"

# 参考音频建议时长（秒）—— 仅作 warning，不强制
REFERENCE_MIN_SECONDS = 3
REFERENCE_MAX_SECONDS = 30

_VOICE_ID_RE = re.compile(r"^voice_[0-9a-f]{8,32}$")
_CACHE_KEY_RE = re.compile(r"^[0-9a-f]{32,64}$")
_ALLOWED_UPLOAD_SUFFIXES = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".flac",
    ".ogg",
    ".webm",
}

# ---------- 工具函数 ----------


def _now_iso() -> str:
    """本地时间的 ISO 字符串（不带毫秒），方便人读。"""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _gen_voice_id() -> str:
    """生成不可预测的服务端 voice id。"""
    return f"voice_{uuid.uuid4().hex}"


def _safe_path(root: Path, *parts: str) -> Path:
    """构造并验证路径始终位于指定根目录内。"""
    resolved_root = root.resolve()
    candidate = root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise VoiceCloneStorageError("非法存储路径") from exc
    return candidate


def _validate_voice_id(voice_id: str) -> str:
    value = (voice_id or "").strip().lower()
    if not _VOICE_ID_RE.fullmatch(value):
        raise VoiceProfileNotFoundError("音色档案不存在")
    return value


def _project_dir_name(project_id: Optional[int | str]) -> str:
    """project_id 缺省走 project_default；否则走 project_{id}。"""
    if project_id in (None, "", "null"):
        return DEFAULT_PROJECT_KEY
    if isinstance(project_id, bool):
        raise VoiceCloneStorageError("project_id 无效")
    try:
        pid = int(str(project_id).strip())
    except (TypeError, ValueError) as exc:
        raise VoiceCloneStorageError("project_id 无效") from exc
    if pid <= 0:
        raise VoiceCloneStorageError("project_id 无效")
    return f"project_{pid}"


def _project_dir(project_id: Optional[int | str]) -> Path:
    return _safe_path(VOICE_REFS_DIR, _project_dir_name(project_id))


def _voice_dir(project_id: Optional[int | str], voice_id: str) -> Path:
    return _safe_path(_project_dir(project_id), _validate_voice_id(voice_id))


def _metadata_path(project_id: Optional[int | str], voice_id: str) -> Path:
    return _voice_dir(project_id, voice_id) / "metadata.json"


def _reference_path(project_id: Optional[int | str], voice_id: str) -> Path:
    return _voice_dir(project_id, voice_id) / "reference.wav"


def _sample_path(project_id: Optional[int | str], voice_id: str) -> Path:
    return _voice_dir(project_id, voice_id) / "sample.wav"


def _relative_to_static(p: Path) -> str:
    """返回 ``static/voice_refs/...`` 风格的相对路径，写进 metadata 用。"""
    try:
        return p.relative_to(STATIC_DIR).as_posix()
    except ValueError as exc:
        raise VoiceCloneStorageError("存储路径异常") from exc


def _to_url(static_relative_or_path: str) -> str:
    """把 ``static/xxx`` 或 ``/static/xxx`` 统一成 ``/static/xxx`` URL。"""
    s = static_relative_or_path.replace("\\", "/")
    if s.startswith("/"):
        return s
    if s.startswith("static/"):
        return "/" + s
    return s


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _probe_audio_duration_seconds(path: Path) -> Optional[float]:
    """用 ffprobe 探测音频时长。失败返回 None。"""
    if shutil.which("ffprobe") is None:
        return None
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            stderr=subprocess.STDOUT,
            timeout=10,
        )
        return float(out.decode("utf-8", "ignore").strip())
    except Exception:
        return None


# ---------- 目录初始化 ----------


def ensure_base_dirs() -> None:
    """在 app 启动时调用，确保目录存在。"""
    VOICE_REFS_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_CLONE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    VOICE_CLONE_UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)


# ---------- service ----------


class VoiceProfileNotFoundError(Exception):
    pass


class VoiceProfileMetadataError(Exception):
    pass


class VoiceCloneStorageError(Exception):
    """音频处理 / 文件保存类业务错误，message 已是中文，可直接抛给前端。"""


class VoiceCloneStorageService:
    """文件存储版音色档案 service。

    本类不持有数据库连接，所有状态都落在 ``static/voice_refs`` 下的
    ``metadata.json`` 上。所有路径都基于 ``pathlib.Path``，URL 通过
    ``/static/...`` 暴露给前端。
    """

    # ---------- 创建 ----------

    def create_profile(
        self,
        *,
        project_id: Optional[int | str],
        owner_id: Optional[int] = None,
        voice_name: str,
        prompt_text: str,
        prompt_wav_bytes: bytes,
        prompt_wav_filename: str,
        gender: Optional[str] = None,
        age_group: Optional[str] = None,
        style_tags: Optional[List[str] | str] = None,
        description: Optional[str] = None,
        sample_text: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """兼容旧调用；HTTP 路由应使用流式的 ``create_profile_from_path``。"""
        if not prompt_wav_bytes:
            raise VoiceCloneStorageError("prompt_wav 不能为空，请上传参考音频")
        ensure_base_dirs()
        suffix = self._validated_upload_suffix(prompt_wav_filename)
        tmp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="voice-upload-",
                suffix=suffix,
                dir=VOICE_CLONE_UPLOAD_TMP_DIR,
                delete=False,
            ) as tmp:
                tmp.write(prompt_wav_bytes)
                tmp_path = Path(tmp.name)
            return self.create_profile_from_path(
                project_id=project_id,
                owner_id=owner_id,
                voice_name=voice_name,
                prompt_text=prompt_text,
                prompt_wav_path=tmp_path,
                prompt_wav_filename=prompt_wav_filename,
                gender=gender,
                age_group=age_group,
                style_tags=style_tags,
                description=description,
                sample_text=sample_text,
            )
        finally:
            if tmp_path:
                tmp_path.unlink(missing_ok=True)

    def create_profile_from_path(
        self,
        *,
        project_id: Optional[int | str],
        owner_id: Optional[int],
        voice_name: str,
        prompt_text: str,
        prompt_wav_path: Path,
        prompt_wav_filename: str,
        gender: Optional[str] = None,
        age_group: Optional[str] = None,
        style_tags: Optional[List[str] | str] = None,
        description: Optional[str] = None,
        sample_text: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """从受控临时文件创建音色档案，不把上传内容一次性读入内存。"""
        warnings: List[str] = []

        # 基本校验 —— 错误信息走业务异常，不抛 500
        if not voice_name or not voice_name.strip():
            raise VoiceCloneStorageError("voice_name 不能为空")
        if not prompt_text or not prompt_text.strip():
            raise VoiceCloneStorageError("prompt_text 不能为空，必须是参考音频对应的原文")
        prompt_wav_path = prompt_wav_path.resolve()
        try:
            prompt_wav_path.relative_to(VOICE_CLONE_UPLOAD_TMP_DIR.resolve())
        except ValueError as exc:
            raise VoiceCloneStorageError("上传临时文件路径无效") from exc
        if not prompt_wav_path.is_file() or prompt_wav_path.stat().st_size <= 0:
            raise VoiceCloneStorageError("prompt_wav 不能为空，请上传参考音频")
        if owner_id is None or int(owner_id) <= 0:
            raise VoiceCloneStorageError("owner_id 无效")

        # style_tags 入参可能是逗号分隔字符串
        style_tags_list: List[str] = []
        if style_tags:
            if isinstance(style_tags, str):
                style_tags_list = [s.strip() for s in style_tags.split(",") if s.strip()]
            else:
                style_tags_list = [s.strip() for s in style_tags if s and s.strip()]

        voice_id = _gen_voice_id()
        project_dir_name = _project_dir_name(project_id)
        voice_dir = _safe_path(VOICE_REFS_DIR, project_dir_name, voice_id)
        voice_dir.mkdir(parents=True, exist_ok=True)

        reference_wav = _safe_path(voice_dir, "reference.wav")
        original_suffix = self._validated_upload_suffix(prompt_wav_filename)
        original_path = _safe_path(
            voice_dir,
            f"upload_{uuid.uuid4().hex}{original_suffix}",
        )

        # 先存原始上传文件，方便 ffmpeg 失败时还能保留原始素材做排查
        shutil.copyfile(prompt_wav_path, original_path)

        try:
            self._convert_to_reference_wav(original_path, reference_wav)
        except VoiceCloneStorageError as e:
            # 没有 ffmpeg / 转换失败：删掉半成品目录，避免下次扫到没 reference 的 voice
            shutil.rmtree(voice_dir, ignore_errors=True)
            raise

        # 探测时长做 warning（不强制）
        duration = _probe_audio_duration_seconds(reference_wav)
        if duration is not None:
            if duration < REFERENCE_MIN_SECONDS:
                warnings.append(
                    f"参考音频时长 {duration:.1f}s，建议 {REFERENCE_MIN_SECONDS}-{REFERENCE_MAX_SECONDS}s 复刻效果更好"
                )
            elif duration > REFERENCE_MAX_SECONDS:
                warnings.append(
                    f"参考音频时长 {duration:.1f}s 偏长，建议 {REFERENCE_MIN_SECONDS}-{REFERENCE_MAX_SECONDS}s"
                )

        # 清理掉原始文件，节省空间（reference.wav 已生成）
        try:
            original_path.unlink()
        except Exception:
            pass

        rel_ref = _relative_to_static(reference_wav)
        metadata: Dict[str, Any] = {
            "voice_id": voice_id,
            "project_id": None if project_id in (None, "", "null") else project_id,
            "owner_id": int(owner_id),
            "project_dir_name": project_dir_name,
            "voice_name": voice_name.strip(),
            "engine": ENGINE_NAME,
            "mode": MODE_NAME,
            "reference_audio_path": rel_ref,
            "reference_audio_url": _to_url(rel_ref),
            "prompt_text": prompt_text.strip(),
            "gender": (gender or None),
            "age_group": (age_group or None),
            "style_tags": style_tags_list,
            "description": description or "",
            "sample_text": None,
            "sample_audio_path": None,
            "sample_audio_url": None,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._write_metadata(voice_dir, metadata)
        xlog.info(0, "[voice-clone] profile created voice_id=%s project=%s name=%s",
                  voice_id, project_dir_name, metadata["voice_name"])
        return metadata, warnings

    @staticmethod
    def _validated_upload_suffix(filename: str) -> str:
        suffix = Path(filename or "").suffix.lower()
        if suffix not in _ALLOWED_UPLOAD_SUFFIXES:
            raise VoiceCloneStorageError("不支持的参考音频格式")
        return suffix

    def _convert_to_reference_wav(self, src: Path, dst: Path) -> None:
        """优先 ffmpeg 转 16k mono wav；无 ffmpeg 时给出清晰错误。"""
        if not _ffmpeg_available():
            raise VoiceCloneStorageError(
                "未检测到 ffmpeg，无法把上传音频转换成 16k mono wav。请安装 ffmpeg 后重试。"
            )
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(src),
                    "-ac", "1",
                    "-ar", "16000",
                    str(dst),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            xlog.warn(0, "[voice-clone] ffmpeg rejected uploaded reference audio")
            raise VoiceCloneStorageError("参考音频格式无效或无法解码") from e
        except subprocess.TimeoutExpired as e:
            raise VoiceCloneStorageError("ffmpeg 转换参考音频超时（>60s）") from e

        if not dst.exists() or dst.stat().st_size == 0:
            raise VoiceCloneStorageError("ffmpeg 转换后未生成有效 reference.wav")

    # ---------- 读取 ----------

    def list_profiles(
        self,
        project_id: Optional[int | str] = None,
        *,
        owner_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """扫描 voice_refs，按 created_at 倒序返回。

        传 project_id 时只返回该 project 的 voice（含 default 还是仅 project 由调用方决定，
        本函数严格按 ``project_{id}`` 目录过滤，不混 default）。
        """
        profiles: List[Dict[str, Any]] = []
        if not VOICE_REFS_DIR.exists():
            return profiles

        if project_id in (None, "", "null"):
            project_dirs = [p for p in VOICE_REFS_DIR.iterdir() if p.is_dir()]
        else:
            target = VOICE_REFS_DIR / _project_dir_name(project_id)
            project_dirs = [target] if target.exists() else []

        for proj_dir in project_dirs:
            for voice_dir in proj_dir.iterdir():
                if not voice_dir.is_dir():
                    continue
                meta_path = voice_dir / "metadata.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = self._read_metadata(voice_dir)
                except VoiceProfileMetadataError as e:
                    xlog.warn(0, "[voice-clone] skip corrupted metadata path=%s err=%s", meta_path, e)
                    continue
                if owner_id is not None and meta.get("owner_id") != int(owner_id):
                    continue
                profiles.append(meta)

        profiles.sort(key=lambda m: m.get("created_at") or "", reverse=True)
        return profiles

    def get_profile(self, voice_id: str) -> Dict[str, Any]:
        voice_dir = self._find_voice_dir(voice_id)
        if voice_dir is None:
            raise VoiceProfileNotFoundError(f"未找到 voice_id={voice_id}")
        return self._read_metadata(voice_dir)

    def _find_voice_dir(self, voice_id: str) -> Optional[Path]:
        """在所有 project_* 目录下查找匹配 voice_id 的目录。"""
        voice_id = _validate_voice_id(voice_id)
        if not VOICE_REFS_DIR.exists():
            return None
        for proj_dir in VOICE_REFS_DIR.iterdir():
            if not proj_dir.is_dir():
                continue
            candidate = _safe_path(VOICE_REFS_DIR, proj_dir.name, voice_id)
            if candidate.is_dir() and (candidate / "metadata.json").exists():
                return candidate
        return None

    # ---------- 删除 ----------

    def delete_profile(self, voice_id: str) -> Dict[str, Any]:
        voice_dir = self._find_voice_dir(voice_id)
        if voice_dir is None:
            raise VoiceProfileNotFoundError(f"未找到 voice_id={voice_id}")

        # 删档案目录
        shutil.rmtree(voice_dir)
        # 删对应 cache 目录
        cache_dir = _safe_path(VOICE_CLONE_CACHE_DIR, _validate_voice_id(voice_id))
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

        xlog.info(0, "[voice-clone] profile deleted voice_id=%s", voice_id)
        return {"voice_id": voice_id, "deleted": True}

    # ---------- 更新（仅 metadata 局部字段）----------

    def update_sample(
        self,
        voice_id: str,
        sample_wav_path: Path,
        sample_text: str,
    ) -> Dict[str, Any]:
        """把 preview 生成的 sample.wav 路径与样音文本写回 metadata。"""
        voice_dir = self._find_voice_dir(voice_id)
        if voice_dir is None:
            raise VoiceProfileNotFoundError(f"未找到 voice_id={voice_id}")
        sample_wav_path = sample_wav_path.resolve()
        try:
            sample_wav_path.relative_to(voice_dir.resolve())
        except ValueError as exc:
            raise VoiceCloneStorageError("样音存储路径无效") from exc
        metadata = self._read_metadata(voice_dir)
        rel = _relative_to_static(sample_wav_path)
        metadata["sample_text"] = sample_text
        metadata["sample_audio_path"] = rel
        metadata["sample_audio_url"] = _to_url(rel)
        metadata["updated_at"] = _now_iso()
        self._write_metadata(voice_dir, metadata)
        return metadata

    # ---------- metadata 读写 ----------

    def _write_metadata(self, voice_dir: Path, metadata: Dict[str, Any]) -> None:
        tmp = voice_dir / "metadata.json.tmp"
        tmp.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(voice_dir / "metadata.json")

    def _read_metadata(self, voice_dir: Path) -> Dict[str, Any]:
        meta_path = voice_dir / "metadata.json"
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise VoiceProfileNotFoundError(f"未找到 metadata.json：{meta_path}") from e
        except json.JSONDecodeError as e:
            raise VoiceProfileMetadataError(f"metadata.json 损坏：{meta_path} ({e})") from e
        if not isinstance(data, dict) or "voice_id" not in data:
            raise VoiceProfileMetadataError(f"metadata.json 结构异常：{meta_path}")
        return data

    # ---------- 合成缓存 ----------

    def cache_path_for(self, voice_id: str, cache_key: str) -> Path:
        voice_id = _validate_voice_id(voice_id)
        cache_key = (cache_key or "").strip().lower()
        if not _CACHE_KEY_RE.fullmatch(cache_key):
            raise VoiceCloneStorageError("缓存键无效")
        return _safe_path(VOICE_CLONE_CACHE_DIR, voice_id, f"{cache_key}.wav")

    @staticmethod
    def build_cache_key(voice_id: str, tts_text: str, prompt_text: str) -> str:
        import hashlib
        h = hashlib.md5(
            f"{voice_id}|{tts_text}|{prompt_text}".encode("utf-8")
        ).hexdigest()
        return h

    def reference_wav_for(self, voice_id: str) -> Path:
        voice_dir = self._find_voice_dir(voice_id)
        if voice_dir is None:
            raise VoiceProfileNotFoundError(f"未找到 voice_id={voice_id}")
        return _safe_path(voice_dir, "reference.wav")


# 模块级单例
voice_clone_storage_service = VoiceCloneStorageService()
