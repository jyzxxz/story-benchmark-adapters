from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.api_key_pool import (
    PooledAsyncOpenAI,
    PooledOpenAI,
    SanitizedProviderError,
    request_api_key_context,
    request_byok_context,
)


class RawProviderFailure(RuntimeError):
    def __init__(self, status_code: int, raw_detail: str):
        super().__init__(raw_detail)
        self.status_code = status_code
        self.raw_detail = raw_detail


class _AsyncCompletions:
    def __init__(self, client: "FakeAsyncClient") -> None:
        self.client = client

    async def create(self, **kwargs: Any) -> Any:
        self.client.create_calls.append(kwargs)
        outcome = self.client.outcome
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return outcome()
        return outcome


class FakeAsyncClient:
    def __init__(self, api_key: str, outcome: Any) -> None:
        self.api_key = api_key
        self.outcome = outcome
        self.create_calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = SimpleNamespace(completions=_AsyncCompletions(self))

    async def close(self) -> None:
        self.closed = True


class AsyncClientFactory:
    def __init__(
        self,
        outcomes: dict[str, Any],
        constructor_failures: dict[str, BaseException] | None = None,
    ) -> None:
        self.outcomes = outcomes
        self.constructor_failures = constructor_failures or {}
        self.calls: list[str] = []
        self.clients: list[FakeAsyncClient] = []

    def __call__(self, *, api_key: str, **_kwargs: Any) -> FakeAsyncClient:
        self.calls.append(api_key)
        if api_key in self.constructor_failures:
            raise self.constructor_failures[api_key]
        client = FakeAsyncClient(api_key, self.outcomes[api_key])
        self.clients.append(client)
        return client


class _SyncCompletions:
    def __init__(self, client: "FakeSyncClient") -> None:
        self.client = client

    def create(self, **kwargs: Any) -> Any:
        self.client.create_calls.append(kwargs)
        if isinstance(self.client.outcome, BaseException):
            raise self.client.outcome
        return self.client.outcome


class FakeSyncClient:
    def __init__(self, api_key: str, outcome: Any) -> None:
        self.api_key = api_key
        self.outcome = outcome
        self.create_calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = SimpleNamespace(completions=_SyncCompletions(self))

    def close(self) -> None:
        self.closed = True


class SyncClientFactory:
    def __init__(self, outcomes: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.clients: list[FakeSyncClient] = []

    def __call__(self, *, api_key: str, **_kwargs: Any) -> FakeSyncClient:
        self.calls.append(api_key)
        client = FakeSyncClient(api_key, self.outcomes[api_key])
        self.clients.append(client)
        return client


def _assert_sanitized_exception(exc: SanitizedProviderError, *raw_values: str) -> None:
    rendered = f"{exc!s}\n{exc!r}"
    for value in raw_values:
        assert value not in rendered
    # `raise ... from None` only hides a context from normal traceback output
    # when executed inside `except`; it does not remove the retained object.
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.asyncio
async def test_environment_pool_works_without_legacy_key_and_merges_fallback_in_order(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEYS", "pool-a,pool-b,pool-a")
    factory = AsyncClientFactory(
        {"pool-a": {"ok": True}, "pool-b": {"unused": True}}
    )
    without_legacy = PooledAsyncOpenAI(
        api_key=None,
        pool_env="OPENAI_API_KEYS",
        _client_factory=factory,
    )
    with_legacy = PooledAsyncOpenAI(
        api_key="legacy-c",
        pool_env="OPENAI_API_KEYS",
        _client_factory=lambda **_kwargs: None,
    )

    assert without_legacy.pool.size == 2
    assert [lease.api_key for lease in with_legacy.pool.get_candidates(None)] == [
        "pool-a",
        "pool-b",
        "legacy-c",
    ]
    assert await without_legacy.chat.completions.create(model="server-model", messages=[]) == {
        "ok": True
    }
    assert factory.calls == ["pool-a"]


@pytest.mark.asyncio
async def test_async_platform_failure_switches_key_and_cools_failed_credential():
    raw_detail = "raw upstream body for platform-a"
    factory = AsyncClientFactory(
        {
            "platform-a": RawProviderFailure(429, raw_detail),
            "platform-b": {"ok": True},
        }
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a", "platform-b"],
        _client_factory=factory,
    )

    response = await client.chat.completions.create(model="server-model", messages=[])

    assert response == {"ok": True}
    assert factory.calls == ["platform-a", "platform-b"]
    assert [lease.api_key for lease in client.pool.get_candidates(None)] == ["platform-b"]


@pytest.mark.asyncio
async def test_async_client_uses_pool_env_without_legacy_scalar(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEYS", "env-platform-a,env-platform-b")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    factory = AsyncClientFactory(
        {
            "env-platform-a": {"ok": "from-env-pool"},
            "env-platform-b": {"ok": "unused"},
        }
    )
    client = PooledAsyncOpenAI(_client_factory=factory)

    response = await client.chat.completions.create(model="server-model", messages=[])

    assert response == {"ok": "from-env-pool"}
    assert factory.calls == ["env-platform-a"]


def test_pool_env_and_legacy_scalar_merge_in_stable_deduplicated_order(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("OPENAI_API_KEYS", "pool-a,pool-b,pool-a")
    client = PooledAsyncOpenAI(
        api_key="pool-b",
        _client_factory=lambda **_kwargs: None,
    )
    assert [lease.api_key for lease in client.pool.get_candidates(None)] == [
        "pool-a",
        "pool-b",
    ]

    with_scalar_append = PooledAsyncOpenAI(
        api_key="legacy-c",
        _client_factory=lambda **_kwargs: None,
    )
    assert [lease.api_key for lease in with_scalar_append.pool.get_candidates(None)] == [
        "pool-a",
        "pool-b",
        "legacy-c",
    ]


@pytest.mark.asyncio
async def test_async_byok_failure_never_falls_back_and_does_not_retain_raw_error():
    byok = "user-secret-never-fallback"
    raw_detail = f"provider response accidentally contains {byok}"
    factory = AsyncClientFactory(
        {
            byok: RawProviderFailure(401, raw_detail),
            "platform-a": {"must_not": "run"},
        }
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    with request_api_key_context(byok):
        with pytest.raises(SanitizedProviderError) as caught:
            await client.chat.completions.create(model="server-model", messages=[])

    assert factory.calls == [byok]
    assert factory.clients[0].closed is True
    assert caught.value.credential_source == "byok"
    _assert_sanitized_exception(caught.value, byok, raw_detail)


@pytest.mark.asyncio
async def test_async_nonretryable_error_does_not_switch_platform_key_or_retain_context():
    raw_detail = "raw 422 response body"
    factory = AsyncClientFactory(
        {
            "platform-a": RawProviderFailure(422, raw_detail),
            "platform-b": {"must_not": "run"},
        }
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a", "platform-b"],
        _client_factory=factory,
    )

    with pytest.raises(SanitizedProviderError) as caught:
        await client.chat.completions.create(model="server-model", messages=[])

    assert factory.calls == ["platform-a"]
    assert caught.value.retryable is False
    _assert_sanitized_exception(caught.value, raw_detail)


@pytest.mark.asyncio
async def test_client_constructor_failure_is_sanitized_and_can_switch_platform_key():
    raw_detail = "constructor leaked platform-a"
    factory = AsyncClientFactory(
        {"platform-b": {"ok": True}},
        constructor_failures={
            "platform-a": RawProviderFailure(401, raw_detail),
        },
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a", "platform-b"],
        _client_factory=factory,
    )

    response = await client.chat.completions.create(model="server-model", messages=[])

    assert response == {"ok": True}
    assert factory.calls == ["platform-a", "platform-b"]
    assert "platform-a" not in repr(client)


@pytest.mark.asyncio
async def test_byok_constructor_failure_is_sanitized_without_platform_fallback():
    byok = "constructor-byok-secret"
    raw_detail = f"invalid auth header {byok}"
    factory = AsyncClientFactory(
        {"platform-a": {"must_not": "run"}},
        constructor_failures={byok: RawProviderFailure(401, raw_detail)},
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    with request_api_key_context(byok):
        with pytest.raises(SanitizedProviderError) as caught:
            await client.chat.completions.create(model="server-model", messages=[])

    assert factory.calls == [byok]
    _assert_sanitized_exception(caught.value, byok, raw_detail)


class FailingAsyncStream:
    def __init__(self, failure_factory: Callable[[], BaseException]) -> None:
        self.failure_factory = failure_factory
        self.index = 0
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.index == 0:
            self.index += 1
            return {"chunk": 1}
        raise self.failure_factory()

    async def close(self) -> None:
        self.closed = True


class SuccessfulAsyncStream:
    def __init__(self, *chunks: Any) -> None:
        self.chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_partial_stream_failure_is_sanitized_cooled_and_never_replayed():
    raw_detail = "raw streaming response for platform-a"
    stream = FailingAsyncStream(lambda: RawProviderFailure(503, raw_detail))
    factory = AsyncClientFactory(
        {
            "platform-a": stream,
            "platform-b": {"must_not": "start"},
        }
    )
    client = PooledAsyncOpenAI(
        api_keys=["platform-a", "platform-b"],
        _client_factory=factory,
    )

    response_stream = await client.chat.completions.create(
        model="server-model",
        messages=[],
        stream=True,
    )
    assert await response_stream.__anext__() == {"chunk": 1}
    with pytest.raises(SanitizedProviderError) as caught:
        await response_stream.__anext__()

    # Once a chunk was emitted, switching keys would duplicate content/cost.
    assert factory.calls == ["platform-a"]
    assert [lease.api_key for lease in client.pool.get_candidates(None)] == ["platform-b"]
    assert stream.closed is True
    assert factory.clients[0].closed is False
    _assert_sanitized_exception(caught.value, raw_detail)


@pytest.mark.asyncio
async def test_successful_stream_reports_success_and_closes_stream_and_byok_client():
    byok = "successful-stream-byok"
    stream = SuccessfulAsyncStream({"chunk": 1}, {"chunk": 2})
    factory = AsyncClientFactory({byok: stream})
    client = PooledAsyncOpenAI(api_keys=["platform-a"], _client_factory=factory)
    successful_leases: list[Any] = []
    original_report_success = client.pool.report_success

    def record_success(lease: Any) -> None:
        successful_leases.append(lease)
        original_report_success(lease)

    client.pool.report_success = record_success  # type: ignore[method-assign]

    with request_api_key_context(byok):
        response_stream = await client.chat.completions.create(
            model="server-model",
            messages=[],
            stream=True,
        )
        chunks = [chunk async for chunk in response_stream]

    assert chunks == [{"chunk": 1}, {"chunk": 2}]
    assert len(successful_leases) == 1
    assert successful_leases[0].is_byok is True
    assert stream.closed is True
    assert factory.clients[0].closed is True


def test_sync_byok_error_is_sanitized_closed_and_never_falls_back():
    byok = "sync-user-secret"
    raw_detail = f"raw sync response contains {byok}"
    factory = SyncClientFactory(
        {
            byok: RawProviderFailure(403, raw_detail),
            "platform-a": {"must_not": "run"},
        }
    )
    client = PooledOpenAI(api_keys=["platform-a"], _client_factory=factory)

    with request_api_key_context(byok):
        with pytest.raises(SanitizedProviderError) as caught:
            client.chat.completions.create(model="server-model", messages=[])

    assert factory.calls == [byok]
    assert factory.clients[0].closed is True
    _assert_sanitized_exception(caught.value, byok, raw_detail)


class _RecordingAsyncFactory:
    """AsyncClientFactory variant that captures base_url for BYOK override tests."""

    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.clients: list[FakeAsyncClient] = []

    def __call__(self, *, api_key: str, **kwargs: Any) -> FakeAsyncClient:
        self.calls.append({"api_key": api_key, **kwargs})
        client = FakeAsyncClient(api_key, self.outcome)
        self.clients.append(client)
        return client

    @property
    def last_create_kwargs(self) -> dict[str, Any]:
        assert self.clients, "no client was constructed"
        assert self.clients[0].create_calls, "create() was never called"
        return self.clients[0].create_calls[0]


@pytest.mark.asyncio
async def test_byok_base_url_override_is_passed_to_new_client():
    byok = "user-byok-key"
    base_url = "https://byok.example.com/v1"
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        base_url="https://platform.example.com/v1",
        _client_factory=factory,
    )

    with request_byok_context(byok, base_url=base_url):
        response = await client.chat.completions.create(model="any-model", messages=[])

    assert response == {"ok": True}
    assert len(factory.calls) == 1
    assert factory.calls[0]["api_key"] == byok
    assert factory.calls[0]["base_url"] == base_url


@pytest.mark.asyncio
async def test_byok_without_base_url_uses_constructor_base_url():
    byok = "user-byok-key"
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        base_url="https://platform.example.com/v1",
        _client_factory=factory,
    )

    with request_byok_context(byok):
        await client.chat.completions.create(model="any-model", messages=[])

    assert len(factory.calls) == 1
    assert factory.calls[0]["base_url"] == "https://platform.example.com/v1"


@pytest.mark.asyncio
async def test_platform_path_ignores_byok_base_url():
    """Platform leases reuse cached clients and never read request base_url."""
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        base_url="https://platform.example.com/v1",
        _client_factory=factory,
    )

    # No BYOK context — platform path
    await client.chat.completions.create(model="any-model", messages=[])

    assert len(factory.calls) == 1
    assert factory.calls[0]["api_key"] == "platform-a"
    assert factory.calls[0]["base_url"] == "https://platform.example.com/v1"


@pytest.mark.asyncio
async def test_byok_json_mode_injects_hint_when_prompt_lacks_json_word():
    """Strict providers (e.g. GLM) require the literal word 'JSON' in the prompt."""
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    with request_byok_context("byok-key"):
        await client.chat.completions.create(
            model="any-model",
            messages=[{"role": "system", "content": "你是一位专业的小说策划师。"}],
            response_format={"type": "json_object"},
        )

    sent_messages = factory.last_create_kwargs["messages"]
    assert len(sent_messages) == 2
    assert "JSON" in sent_messages[0]["content"] or "json" in sent_messages[0]["content"].lower()


@pytest.mark.asyncio
async def test_byok_json_mode_does_not_double_inject_when_prompt_already_mentions_json():
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    with request_byok_context("byok-key"):
        await client.chat.completions.create(
            model="any-model",
            messages=[{"role": "system", "content": "请输出 JSON 格式的结果。"}],
            response_format={"type": "json_object"},
        )

    assert len(factory.last_create_kwargs["messages"]) == 1


@pytest.mark.asyncio
async def test_byok_without_json_mode_is_left_alone():
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    with request_byok_context("byok-key"):
        await client.chat.completions.create(
            model="any-model",
            messages=[{"role": "system", "content": "随便聊聊天"}],
        )

    assert len(factory.last_create_kwargs["messages"]) == 1


@pytest.mark.asyncio
async def test_platform_json_mode_is_not_modified():
    """JSON hint injection must only apply to BYOK, never platform."""
    factory = _RecordingAsyncFactory({"ok": True})
    client = PooledAsyncOpenAI(
        api_keys=["platform-a"],
        _client_factory=factory,
    )

    await client.chat.completions.create(
        model="any-model",
        messages=[{"role": "system", "content": "你是一位专业的小说策划师。"}],
        response_format={"type": "json_object"},
    )

    # Platform path: messages untouched.
    assert len(factory.last_create_kwargs["messages"]) == 1
