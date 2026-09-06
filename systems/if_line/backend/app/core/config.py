"""Typed application settings.

Development remains intentionally compatible with the existing SQLite-first
deployment.  Production is fail-closed: an explicitly production environment
must opt into a production database, restrictive CORS, secure cookies and real
provider credentials before the process can start.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[2]
_PLACEHOLDER_PARTS = (
    "your-api-key",
    "replace-me",
    "changeme",
    "placeholder",
    "example",
    "sk-xxx",
    "***",
    "mock",
    "default",
)


class AppSettings(BaseSettings):
    """Settings consumed by the runtime foundation.

    Legacy services may continue reading environment variables during the
    additive migration.  New infrastructure should depend on this object.
    """

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: Literal["development", "test", "staging", "production"] = "development"
    app_name: str = "AI Novel Agent API"
    app_version: str = "1.0.0"
    debug: bool = False

    host: str = "0.0.0.0"
    port: int = Field(default=60002, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=64)

    database_url: str = "sqlite:///./novel_agent.db"
    database_pool_size: int = Field(default=20, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(default=1800, ge=30)
    database_statement_timeout_ms: int = Field(default=30_000, ge=1_000)
    check_dependencies_on_startup: bool = False

    redis_url: str = "redis://127.0.0.1:6379/0"
    celery_broker_url: str | None = None
    task_lease_seconds: int = Field(default=180, ge=30, le=3600)
    task_dependency_poll_seconds: int = Field(default=5, ge=1, le=60)
    task_dependency_max_retries: int = Field(default=360, ge=1, le=720)
    task_event_poll_seconds: float = Field(default=1.0, ge=0.1, le=10)
    outbox_batch_size: int = Field(default=50, ge=1, le=500)

    cors_allow_origins: str = (
        "http://localhost:5173,http://localhost:3000,"
        "http://127.0.0.1:5173,http://127.0.0.1:3000"
    )
    auth_cookie_secure: bool = False
    auth_ratelimit_backend: Literal["memory", "redis"] = "memory"
    auth_trusted_proxy_ips: str = "127.0.0.1,::1"
    security_hsts_enabled: bool = False
    metrics_enabled: bool = True
    metrics_token: Optional[SecretStr] = None

    static_dir: Path = BACKEND_ROOT / "static"

    openai_api_key: Optional[SecretStr] = None
    openai_api_keys: Optional[SecretStr] = None
    tts_enabled: bool = False
    tts_engine: str = "minimax"
    dashscope_api_key: Optional[SecretStr] = None
    # MiniMax 同步语音合成：既可作为 TTS_ENGINE 主引擎，也可由 fallback 调用。
    # Provider 自身仍直接读 MINIMAX_TTS_API_KEY env，本字段仅用于：
    # (1) 生产环境 fail-closed 校验；(2) /status 暴露是否已配置（不返回 Key 本身）。
    minimax_tts_api_key: Optional[SecretStr] = None
    xunfei_tts_appid: Optional[SecretStr] = None
    xunfei_tts_api_key: Optional[SecretStr] = None
    xunfei_tts_api_secret: Optional[SecretStr] = None
    tts_fallback_enabled: bool = False
    tts_fallback_engine: str = "minimax"
    image_generation_enabled: bool = False
    ai_image_api_key: Optional[SecretStr] = None
    ai_image_api_keys: Optional[SecretStr] = None
    bg_vision_api_key: Optional[SecretStr] = None
    bg_vision_api_keys: Optional[SecretStr] = None

    legacy_sync_api_enabled: bool = True
    aivn_remote_asset_sources_enabled: bool = False
    agent_compose_enabled: bool = False
    visual_asset_catalog_version: str = "v1.1"
    visual_asset_matcher_version: str = "hash-ngram-v1"
    visual_asset_upload_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1)

    # --- 并行化开关（默认 false = 行为不变，可逐项放量）---
    # H2: generate_all_portraits 循环两段式（gather + 串行提交）
    parallel_portrait_generation: bool = False
    # H3/H5: generate_chapter_backgrounds(_v2) 循环两段式
    parallel_background_generation: bool = False
    # H4: generate_chapter_keyframes 循环两段式
    parallel_keyframe_generation: bool = False

    # Stage_Keyframe_Identity_AR_Blueprint A03 — 角色身份契约版本号。
    # 一旦升级，所有用旧版本写入的立绘 generation_params.is_identity_master
    # 都会失效，强制重新生成身份母版。
    character_identity_contract_version: str = "character-identity-v2"

    # Stage_Keyframe_Identity_AR_Blueprint C01 — 关键帧参考图条件生成配置。
    # 不直接覆盖全局 AI_IMAGE_MODEL；关键帧走独立模型，便于切换到支持
    # 多图输入的 qwen-image-edit / qwen-image-2 等。
    keyframe_reference_mode: str = "edit"  # edit / text_only
    keyframe_image_model: str = ""  # 空字符串时回退到 AI_IMAGE_MODEL
    keyframe_reference_max_images: int = 3
    # 默认身份验收失败后直接使用本地 sprite_composite，避免单帧重复付费。
    # 显式放量时也最多允许 1 次额外远程重试。
    keyframe_identity_max_retries: int = Field(default=0, ge=0, le=1)
    keyframe_allow_text_only_fallback: bool = False
    keyframe_identity_validator_version: str = "kf-validator-v1"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def resolved_static_dir(self) -> Path:
        value = self.static_dir.expanduser()
        if not value.is_absolute():
            value = BACKEND_ROOT / value
        return value.resolve()

    @property
    def resolved_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @staticmethod
    def _is_real_secret(value: Optional[SecretStr]) -> bool:
        if value is None:
            return False
        raw = value.get_secret_value().strip().lower()
        return bool(raw) and not any(part in raw for part in _PLACEHOLDER_PARTS)

    @staticmethod
    def _has_secret_value(value: Optional[SecretStr]) -> bool:
        return value is not None and bool(value.get_secret_value().strip())

    @classmethod
    def _is_real_secret_pool(cls, value: Optional[SecretStr]) -> bool:
        if value is None:
            return False
        items = [
            item.strip()
            for item in re.split(r"[,\r\n]+", value.get_secret_value())
            if item.strip()
        ]
        # Fail closed if even one production pool entry is a known placeholder;
        # otherwise it would still receive live traffic during rotation.
        return bool(items) and all(cls._is_real_secret(SecretStr(item)) for item in items)

    @model_validator(mode="after")
    def validate_production_safety(self) -> "AppSettings":
        if not self.is_production:
            return self

        errors: list[str] = []
        if self.database_url.lower().startswith("sqlite"):
            errors.append("DATABASE_URL must use PostgreSQL in production")
        if not self.cors_origins or "*" in self.cors_origins:
            errors.append("CORS_ALLOW_ORIGINS must be an explicit non-empty allowlist")
        if not self.auth_cookie_secure:
            errors.append("AUTH_COOKIE_SECURE must be true in production")
        if self.auth_ratelimit_backend != "redis":
            errors.append("AUTH_RATELIMIT_BACKEND must be redis in production")
        if self.metrics_enabled and not self._is_real_secret(self.metrics_token):
            errors.append("METRICS_TOKEN is required when metrics are enabled in production")
        if self.debug:
            errors.append("DEBUG must be false in production")
        if not self.resolved_celery_broker_url:
            errors.append("CELERY_BROKER_URL or REDIS_URL is required")
        openai_single_set = self._has_secret_value(self.openai_api_key)
        openai_pool_set = self._has_secret_value(self.openai_api_keys)
        if (
            not (openai_single_set or openai_pool_set)
            or (openai_single_set and not self._is_real_secret(self.openai_api_key))
            or (openai_pool_set and not self._is_real_secret_pool(self.openai_api_keys))
        ):
            errors.append(
                "OPENAI_API_KEY or OPENAI_API_KEYS must be configured with non-placeholder values"
            )
        tts_engine = self.tts_engine.strip().lower()
        if self.tts_enabled:
            if tts_engine == "aliyun":
                if not self._is_real_secret(self.dashscope_api_key):
                    errors.append(
                        "DASHSCOPE_API_KEY is required when Aliyun TTS is enabled"
                    )
            elif tts_engine == "minimax":
                if not self._is_real_secret(self.minimax_tts_api_key):
                    errors.append(
                        "MINIMAX_TTS_API_KEY is required when MiniMax TTS is enabled"
                    )
            elif tts_engine == "xunfei":
                if not all(
                    self._is_real_secret(value)
                    for value in (
                        self.xunfei_tts_appid,
                        self.xunfei_tts_api_key,
                        self.xunfei_tts_api_secret,
                    )
                ):
                    errors.append(
                        "XUNFEI_TTS_APPID, XUNFEI_TTS_API_KEY and "
                        "XUNFEI_TTS_API_SECRET are required when Xunfei TTS is enabled"
                    )
            else:
                errors.append(
                    "TTS_ENGINE must be one of aliyun, minimax or xunfei when TTS is enabled"
                )
        if (
            self.tts_enabled
            and self.tts_fallback_enabled
        ):
            fallback_engine = self.tts_fallback_engine.strip().lower()
            if fallback_engine != "minimax":
                errors.append(
                    "TTS_FALLBACK_ENGINE must be minimax when TTS fallback is enabled"
                )
            elif not self._is_real_secret(self.minimax_tts_api_key):
                errors.append(
                    "MINIMAX_TTS_API_KEY is required with a non-placeholder value "
                    "when TTS_FALLBACK_ENABLED is true and TTS_FALLBACK_ENGINE=minimax"
                )
        if self.image_generation_enabled:
            image_single_set = self._has_secret_value(self.ai_image_api_key)
            image_pool_set = self._has_secret_value(self.ai_image_api_keys)
            dashscope_set = self._has_secret_value(self.dashscope_api_key)
            if (
                not (image_single_set or image_pool_set or dashscope_set)
                or (image_single_set and not self._is_real_secret(self.ai_image_api_key))
                or (image_pool_set and not self._is_real_secret_pool(self.ai_image_api_keys))
                or (dashscope_set and not self._is_real_secret(self.dashscope_api_key))
            ):
                errors.append(
                    "AI_IMAGE_API_KEY, AI_IMAGE_API_KEYS or DASHSCOPE_API_KEY is required "
                    "with non-placeholder values when image generation is enabled"
                )
        bg_vision_single_set = self._has_secret_value(self.bg_vision_api_key)
        bg_vision_pool_set = self._has_secret_value(self.bg_vision_api_keys)
        if (
            (bg_vision_single_set and not self._is_real_secret(self.bg_vision_api_key))
            or (bg_vision_pool_set and not self._is_real_secret_pool(self.bg_vision_api_keys))
        ):
            errors.append(
                "BG_VISION_API_KEY and BG_VISION_API_KEYS must contain only "
                "non-placeholder values when configured"
            )
        if errors:
            raise ValueError("unsafe production configuration: " + "; ".join(errors))
        return self


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return one validated settings snapshot for the current process."""

    return AppSettings()
