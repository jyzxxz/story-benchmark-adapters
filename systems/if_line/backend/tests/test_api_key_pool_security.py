from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import AppSettings
from app.services.api_key_pool import (
    ApiKeyPool,
    BYOK_HEADER,
    get_request_api_key,
    request_api_key_context,
)


class ProviderFailure(RuntimeError):
    def __init__(self, status_code: int | None):
        super().__init__(f"provider failed with status={status_code}")
        self.status_code = status_code


def _lease_key(lease: Any) -> str:
    """Read the opaque lease in tests without coupling to its field spelling."""

    for name in ("api_key", "key"):
        value = getattr(lease, name, None)
        if isinstance(value, str):
            return value
    if is_dataclass(lease):
        values = asdict(lease)
        for name in ("api_key", "key"):
            value = values.get(name)
            if isinstance(value, str):
                return value
    raise AssertionError("ApiKeyLease must keep the selected key for provider construction")


def _candidate_keys(pool: ApiKeyPool, request_api_key: str | None = None) -> list[str]:
    return [_lease_key(item) for item in pool.get_candidates(request_api_key)]


@contextmanager
def _request_key(value: str | None):
    """Keep the assertion sites readable while exercising the public context API."""

    with request_api_key_context(value):
        yield


def test_public_header_name_is_stable_and_provider_neutral():
    assert BYOK_HEADER == "X-LLM-API-Key"


def _production_settings(**overrides: Any) -> AppSettings:
    values: dict[str, Any] = {
        "_env_file": None,
        "app_env": "production",
        "debug": False,
        "database_url": "postgresql+psycopg://user:pass@localhost/if_line",
        "cors_allow_origins": "https://if-line.invalid",
        "auth_cookie_secure": True,
        "auth_ratelimit_backend": "redis",
        "metrics_enabled": False,
        "tts_enabled": False,
        "image_generation_enabled": False,
        "task_system_enabled": False,
        "revision_read_enabled": False,
        "media_gateway_enabled": False,
        "branching_enabled": False,
        # pydantic-settings 仍会读 os.environ / .env，前面的测试可能残留 env；
        # 显式锁住这几个会被 cross-feature 校验依赖的字段，避免污染。
        "visual_asset_actions_enabled": False,
        "agent_compose_enabled": False,
        "reader_dynamic_continuation_enabled": False,
        "openai_api_key": None,
    }
    values.update(overrides)
    return AppSettings(**values)


def test_production_accepts_a_real_key_pool_without_legacy_single_key():
    secret_a = "pool-a-real-secret-for-validation"
    secret_b = "pool-b-real-secret-for-validation"

    settings = _production_settings(openai_api_keys=f"{secret_a},{secret_b}")

    assert secret_a not in repr(settings)
    assert secret_b not in repr(settings)


def test_production_rejects_a_pool_containing_only_placeholders():
    with pytest.raises(ValidationError, match="OPENAI_API_KEYS|OPENAI_API_KEY"):
        _production_settings(openai_api_keys="your-api-key-here,sk-xxx")


@pytest.mark.parametrize(
    ("openai_api_key", "openai_api_keys"),
    [
        ("your-api-key-here", "pool-real-secret-for-validation"),
        ("single-real-secret-for-validation", "sk-xxx"),
    ],
)
def test_production_rejects_any_configured_placeholder_even_if_other_source_is_real(
    openai_api_key: str,
    openai_api_keys: str,
):
    # Runtime appends the legacy single key to the pool. Accepting one real
    # source must not allow a second configured placeholder to receive traffic.
    with pytest.raises(ValidationError, match="OPENAI_API_KEYS|OPENAI_API_KEY"):
        _production_settings(
            openai_api_key=openai_api_key,
            openai_api_keys=openai_api_keys,
        )


def test_optional_production_vision_pool_is_secret_and_rejects_placeholders():
    vision_secret = "vision-real-secret-for-validation"
    settings = _production_settings(
        openai_api_keys="text-real-secret-for-validation",
        bg_vision_api_keys=vision_secret,
    )
    assert vision_secret not in repr(settings)

    with pytest.raises(ValidationError, match="BG_VISION_API_KEYS|BG_VISION_API_KEY"):
        _production_settings(
            openai_api_keys="text-real-secret-for-validation",
            bg_vision_api_keys="sk-xxx",
        )


def test_production_rejects_minimax_fallback_enabled_without_real_key():
    """TTS fallback 开启但 MINIMAX_TTS_API_KEY 为占位符 → 启动失败。"""
    with pytest.raises(ValidationError, match="MINIMAX_TTS_API_KEY"):
        _production_settings(
            openai_api_keys="text-real-secret-for-validation",
            tts_enabled=True,
            tts_engine="aliyun",
            dashscope_api_key="dashscope-real-secret-for-validation",
            tts_fallback_enabled=True,
            tts_fallback_engine="minimax",
            minimax_tts_api_key="sk-xxx",  # 占位符
        )


def test_production_accepts_minimax_fallback_with_real_key():
    """TTS fallback 开启 + 真实 MiniMax Key → 通过。"""
    settings = _production_settings(
        openai_api_keys="text-real-secret-for-validation",
        tts_enabled=True,
        tts_engine="aliyun",
        dashscope_api_key="dashscope-real-secret-for-validation",
        tts_fallback_enabled=True,
        tts_fallback_engine="minimax",
        minimax_tts_api_key="mm-real-production-key-secret",
    )
    # SecretStr 不在 repr 中暴露真实值
    assert "mm-real-production-key-secret" not in repr(settings)


def test_production_accepts_fallback_disabled_without_minimax_key():
    """TTS fallback 关闭时不需要 MiniMax Key（默认场景）。"""
    settings = _production_settings(
        openai_api_keys="text-real-secret-for-validation",
        tts_enabled=True,
        tts_engine="aliyun",
        dashscope_api_key="dashscope-real-secret-for-validation",
        tts_fallback_enabled=False,
    )
    assert settings.tts_fallback_enabled is False


@pytest.mark.parametrize("engine", ["unknown", "", "azure"])
def test_production_rejects_unknown_enabled_tts_engine(engine: str):
    with pytest.raises(ValidationError, match="TTS_ENGINE"):
        _production_settings(
            openai_api_keys="text-real-secret-for-validation",
            tts_enabled=True,
            tts_engine=engine,
        )


def test_production_validates_minimax_primary_credentials():
    with pytest.raises(ValidationError, match="MINIMAX_TTS_API_KEY"):
        _production_settings(
            openai_api_keys="text-real-secret-for-validation",
            tts_enabled=True,
            tts_engine="minimax",
            minimax_tts_api_key="",
        )

    settings = _production_settings(
        openai_api_keys="text-real-secret-for-validation",
        tts_enabled=True,
        tts_engine="minimax",
        minimax_tts_api_key="mm-real-production-key-secret",
    )
    assert settings.tts_engine == "minimax"


def test_production_validates_all_xunfei_credentials():
    common = {
        "openai_api_keys": "text-real-secret-for-validation",
        "tts_enabled": True,
        "tts_engine": "xunfei",
        "xunfei_tts_appid": "xunfei-real-app-id",
        "xunfei_tts_api_key": "xunfei-real-api-key",
        "xunfei_tts_api_secret": "xunfei-real-api-secret",
    }
    assert _production_settings(**common).tts_engine == "xunfei"

    for missing in (
        "xunfei_tts_appid",
        "xunfei_tts_api_key",
        "xunfei_tts_api_secret",
    ):
        invalid = {**common, missing: ""}
        with pytest.raises(ValidationError, match="XUNFEI_TTS"):
            _production_settings(**invalid)


def test_production_rejects_unsupported_tts_fallback_engine():
    with pytest.raises(ValidationError, match="TTS_FALLBACK_ENGINE"):
        _production_settings(
            openai_api_keys="text-real-secret-for-validation",
            tts_enabled=True,
            tts_engine="aliyun",
            dashscope_api_key="dashscope-real-secret-for-validation",
            tts_fallback_enabled=True,
            tts_fallback_engine="xunfei",
        )


def test_pool_normalizes_empty_and_duplicate_keys_and_rotates():
    pool = ApiKeyPool([" platform-a ", "", "platform-b", "platform-a"])

    assert pool.configured is True
    assert pool.size == 2
    assert _candidate_keys(pool) == ["platform-a", "platform-b"]
    assert _candidate_keys(pool) == ["platform-b", "platform-a"]


def test_empty_platform_pool_is_explicitly_unconfigured():
    pool = ApiKeyPool(["", "   "])

    assert pool.configured is False
    assert pool.size == 0
    assert pool.get_candidates() == ()


def test_byok_is_the_only_candidate_and_never_joins_platform_rotation():
    pool = ApiKeyPool(["platform-a", "platform-b"])

    assert _candidate_keys(pool, "user-owned-secret") == ["user-owned-secret"]
    assert _candidate_keys(pool) == ["platform-a", "platform-b"]

    byok_lease = pool.get_candidates("user-owned-secret")[0]
    pool.report_failure(byok_lease, ProviderFailure(401))

    # A bad user key must neither cool a platform key nor silently fall back to
    # platform credit on the next selection attempt.
    assert _candidate_keys(pool, "user-owned-secret") == ["user-owned-secret"]
    assert set(_candidate_keys(pool)) == {"platform-a", "platform-b"}


def test_pool_and_lease_representations_do_not_expose_secrets():
    platform_secret = "platform-secret-that-must-not-be-in-repr"
    byok_secret = "byok-secret-that-must-not-be-in-repr"
    pool = ApiKeyPool([platform_secret])

    platform_lease = pool.get_candidates()[0]
    byok_lease = pool.get_candidates(byok_secret)[0]

    assert platform_secret not in repr(pool)
    assert platform_secret not in repr(platform_lease)
    assert byok_secret not in repr(byok_lease)


@pytest.mark.parametrize("status_code", [401, 403, 429, 500, 503])
def test_retryable_or_credential_failures_temporarily_remove_platform_key(
    status_code: int,
):
    now = [100.0]
    pool = ApiKeyPool(
        ["platform-a", "platform-b"],
        cooldown_seconds=10,
        auth_cooldown_seconds=30,
        max_cooldown_seconds=60,
        clock=lambda: now[0],
    )
    failed = pool.get_candidates()[0]
    failed_key = _lease_key(failed)

    pool.report_failure(failed, ProviderFailure(status_code))

    assert failed_key not in _candidate_keys(pool)
    now[0] += 61
    assert failed_key in _candidate_keys(pool)


@pytest.mark.parametrize("failure", [ConnectionError("offline"), TimeoutError("slow")])
def test_transport_failures_temporarily_remove_platform_key(failure: Exception):
    now = [100.0]
    pool = ApiKeyPool(
        ["platform-a", "platform-b"],
        cooldown_seconds=10,
        clock=lambda: now[0],
    )
    failed = pool.get_candidates()[0]
    failed_key = _lease_key(failed)

    decision = pool.report_failure(failed, failure)

    assert getattr(decision, "retryable", False) is True
    assert failed_key not in _candidate_keys(pool)


def test_all_cooled_platform_keys_fail_fast_without_reusing_a_failed_key():
    now = [100.0]
    pool = ApiKeyPool(
        ["platform-a", "platform-b"],
        cooldown_seconds=10,
        clock=lambda: now[0],
    )

    for lease in pool.get_candidates():
        pool.report_failure(lease, ProviderFailure(500))

    assert pool.get_candidates() == ()


@pytest.mark.parametrize("status_code", [400, 404, 409, 422])
def test_non_retryable_request_errors_do_not_rotate_or_cool_key(status_code: int):
    now = [100.0]
    pool = ApiKeyPool(
        ["platform-a", "platform-b"],
        cooldown_seconds=10,
        auth_cooldown_seconds=30,
        max_cooldown_seconds=60,
        clock=lambda: now[0],
    )
    failed = pool.get_candidates()[0]
    failed_key = _lease_key(failed)

    decision = pool.report_failure(failed, ProviderFailure(status_code))

    assert failed_key in _candidate_keys(pool)
    assert getattr(decision, "retryable", False) is False


def test_success_clears_accumulated_failure_backoff():
    now = [100.0]
    pool = ApiKeyPool(
        ["platform-a", "platform-b"],
        cooldown_seconds=10,
        max_cooldown_seconds=60,
        clock=lambda: now[0],
    )
    lease = pool.get_candidates()[0]
    key = _lease_key(lease)
    pool.report_failure(lease, ProviderFailure(500))
    now[0] += 61

    recovered = next(item for item in pool.get_candidates() if _lease_key(item) == key)
    pool.report_success(recovered)
    pool.report_failure(recovered, ProviderFailure(500))

    now[0] += 9
    assert key not in _candidate_keys(pool)
    now[0] += 2
    assert key in _candidate_keys(pool)


def test_request_context_is_reset_after_success_and_exception():
    assert get_request_api_key() is None

    with _request_key("request-secret"):
        assert get_request_api_key() == "request-secret"
    assert get_request_api_key() is None

    with pytest.raises(RuntimeError, match="boom"):
        with _request_key("exception-secret"):
            assert get_request_api_key() == "exception-secret"
            raise RuntimeError("boom")
    assert get_request_api_key() is None


def test_nested_request_context_restores_the_outer_value():
    with _request_key("outer-secret"):
        with _request_key("inner-secret"):
            assert get_request_api_key() == "inner-secret"
        assert get_request_api_key() == "outer-secret"
    assert get_request_api_key() is None


@pytest.mark.asyncio
async def test_concurrent_request_contexts_do_not_cross_contaminate():
    ready = 0
    all_ready = asyncio.Event()
    lock = asyncio.Lock()

    async def observe(secret: str) -> tuple[str | None, str | None]:
        nonlocal ready
        with _request_key(secret):
            async with lock:
                ready += 1
                if ready == 2:
                    all_ready.set()
            await asyncio.wait_for(all_ready.wait(), timeout=1)
            before_yield = get_request_api_key()
            await asyncio.sleep(0)
            return before_yield, get_request_api_key()

    first, second = await asyncio.gather(
        observe("first-request-secret"),
        observe("second-request-secret"),
    )

    assert first == ("first-request-secret", "first-request-secret")
    assert second == ("second-request-secret", "second-request-secret")
    assert get_request_api_key() is None


@pytest.mark.asyncio
async def test_detached_child_task_cannot_retain_key_after_request_scope_exits():
    """A plain ContextVar value leaks into create_task's copied context.

    The request scope therefore needs revocation semantics in addition to a
    token reset: a child task copied before reset must observe the scope as
    inactive once the owning HTTP request has finished.
    """

    release_child = asyncio.Event()
    child_started = asyncio.Event()

    async def observe_after_parent_exit() -> str | None:
        child_started.set()
        await release_child.wait()
        return get_request_api_key()

    with _request_key("must-not-outlive-request"):
        child = asyncio.create_task(observe_after_parent_exit())
        await asyncio.wait_for(child_started.wait(), timeout=1)
        assert get_request_api_key() == "must-not-outlive-request"

    release_child.set()
    assert await asyncio.wait_for(child, timeout=1) is None
    assert get_request_api_key() is None
