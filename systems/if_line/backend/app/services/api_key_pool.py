"""Request-scoped BYOK and resilient API-key pools.

The module deliberately keeps credentials out of logs, reprs and exception
messages.  A user supplied key lives in a ContextVar for the current HTTP
request only; platform keys come from server-side configuration.
"""
from __future__ import annotations

import inspect
import ipaddress
import logging
import os
import re
import socket
import threading
import time
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Iterator, Literal, Sequence

logger = logging.getLogger(__name__)
from urllib.parse import urlparse


BYOK_HEADER = "X-LLM-API-Key"
BYOK_BASE_URL_HEADER = "X-LLM-Base-Url"
BYOK_MODEL_HEADER = "X-LLM-Model"
MAX_BYOK_API_KEY_LENGTH = 2048
MAX_BYOK_BASE_URL_LENGTH = 512
MAX_BYOK_MODEL_LENGTH = 256

_BYOK_MODEL_RE = re.compile(r"\A[A-Za-z0-9._\-/:]{1,256}\Z")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
# Hosts that resolving-based checks may not flag but we always reject.
_SSRF_BLOCKED_HOSTNAMES = {
    "localhost",
    "metadata",
    "metadata.google.internal",
}
_SSRF_BLOCKED_SUFFIXES = (".internal", ".local")

@dataclass(repr=False)
class _RequestByokScope:
    api_key: str | None
    base_url: str | None = None
    model: str | None = None
    active: bool = True


_request_byok_scope: ContextVar[_RequestByokScope | None] = ContextVar(
    "if_line_request_byok",
    default=None,
)
_USE_REQUEST_CONTEXT = object()


class InvalidRequestApiKey(ValueError):
    """Raised when a BYOK request header is unsafe to forward upstream."""


class ApiKeyPoolUnavailable(RuntimeError):
    """No configured platform credential is currently available."""

    def __init__(self) -> None:
        super().__init__("LLM credential is not configured or is temporarily unavailable")


class SanitizedProviderError(RuntimeError):
    """Stable provider failure that never embeds raw upstream data."""

    def __init__(
        self,
        *,
        category: str,
        status_code: int | None,
        credential_source: Literal["byok", "platform"],
        retryable: bool,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.credential_source = credential_source
        self.retryable = retryable
        source_label = "BYOK" if credential_source == "byok" else "platform"
        super().__init__(f"{source_label} LLM provider request failed ({category})")


def normalize_request_api_key(value: str) -> str:
    """Validate a header value without assuming a provider-specific prefix."""

    key = value.strip()
    if not key or len(key) > MAX_BYOK_API_KEY_LENGTH:
        raise InvalidRequestApiKey("Invalid LLM API key header")
    # Some HTTP client/proxy versions merge duplicate fields into a single
    # comma-delimited value. Provider API keys do not use commas, so rejecting
    # one also keeps duplicate-header detection stable across ASGI stacks.
    if "," in key or _CONTROL_CHAR_RE.search(key):
        raise InvalidRequestApiKey("Invalid LLM API key header")
    return key


def normalize_request_base_url(value: str) -> str:
    """Validate a user-supplied LLM base URL.

    Rejects anything that is not an http(s) URL or that resolves to a private
    / loopback / link-local address.  This is the SSRF boundary for BYOK.
    """

    raw = (value or "").strip()
    if not raw or len(raw) > MAX_BYOK_BASE_URL_LENGTH:
        raise InvalidRequestApiKey("Invalid LLM base URL header")
    if _CONTROL_CHAR_RE.search(raw):
        raise InvalidRequestApiKey("Invalid LLM base URL header")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise InvalidRequestApiKey("Invalid LLM base URL header")

    host = (parsed.hostname or "").lower()
    if not host:
        raise InvalidRequestApiKey("Invalid LLM base URL header")

    if host in _SSRF_BLOCKED_HOSTNAMES or host.endswith(_SSRF_BLOCKED_SUFFIXES):
        raise InvalidRequestApiKey("Invalid LLM base URL header")

    # If the host is already an IP literal, validate it directly.  Otherwise
    # resolve it and reject if any resolved address lands in a private range.
    # Resolution uses a short timeout so a dead host cannot stall a request.
    candidate_addresses: list[str] = []
    try:
        ip_obj = ipaddress.ip_address(host)
    except ValueError:
        ip_obj = None

    if ip_obj is not None:
        candidate_addresses = [str(ip_obj)]
    else:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            raise InvalidRequestApiKey("Invalid LLM base URL header")
        for info in infos:
            sockaddr = info[4]
            if sockaddr and len(sockaddr) >= 1:
                candidate_addresses.append(str(sockaddr[0]))

    for addr_str in candidate_addresses:
        try:
            ip = ipaddress.ip_address(addr_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise InvalidRequestApiKey("Invalid LLM base URL header")

    return raw


def normalize_request_model(value: str) -> str:
    """Validate a user-supplied LLM model identifier."""

    model = (value or "").strip()
    if not model or len(model) > MAX_BYOK_MODEL_LENGTH:
        raise InvalidRequestApiKey("Invalid LLM model header")
    if "," in model or _CONTROL_CHAR_RE.search(model):
        raise InvalidRequestApiKey("Invalid LLM model header")
    if not _BYOK_MODEL_RE.match(model):
        raise InvalidRequestApiKey("Invalid LLM model header")
    return model


def get_request_api_key() -> str | None:
    """Return the current request's BYOK API key, if one was supplied."""

    scope = _request_byok_scope.get()
    if scope is None or not scope.active:
        return None
    return scope.api_key


def get_request_base_url() -> str | None:
    """Return the current request's BYOK base URL, if one was supplied."""

    scope = _request_byok_scope.get()
    if scope is None or not scope.active:
        return None
    return scope.base_url


def get_request_model() -> str | None:
    """Return the current request's BYOK model override, if one was supplied."""

    scope = _request_byok_scope.get()
    if scope is None or not scope.active:
        return None
    return scope.model


@contextmanager
def request_byok_context(
    api_key: str | None,
    base_url: str | None = None,
    model: str | None = None,
) -> Iterator[None]:
    """Bind BYOK values to one execution context and always restore it.

    ``asyncio.create_task`` copies ContextVars.  A mutable, revocable scope
    ensures a detached child observes None after the parent request ends,
    instead of retaining copied secrets indefinitely.
    """

    scope = _RequestByokScope(api_key=api_key, base_url=base_url, model=model)
    token = _request_byok_scope.set(scope)
    try:
        yield
    finally:
        scope.active = False
        scope.api_key = None
        scope.base_url = None
        scope.model = None
        _request_byok_scope.reset(token)


@contextmanager
def request_api_key_context(value: str | None) -> Iterator[None]:
    """Backwards-compatible wrapper around :func:`request_byok_context`."""

    with request_byok_context(value):
        yield


class RequestApiKeyMiddleware:
    """Pure ASGI middleware that keeps BYOK alive through streamed bodies.

    The BYOK headers (API key, base URL, model) are removed from the downstream
    ASGI scope after being captured, which prevents ordinary request/header
    logging from seeing them.  A pure ASGI wrapper (rather than
    BaseHTTPMiddleware) ensures cleanup runs only after a StreamingResponse has
    finished sending.
    """

    # (header_name, normalizer, max_byte_length, scope_attr)
    _HEADER_RULES: tuple[tuple[str, Callable[[str], str], int, str], ...] = (
        (BYOK_HEADER, normalize_request_api_key, MAX_BYOK_API_KEY_LENGTH, "api_key"),
        (BYOK_BASE_URL_HEADER, normalize_request_base_url, MAX_BYOK_BASE_URL_LENGTH, "base_url"),
        (BYOK_MODEL_HEADER, normalize_request_model, MAX_BYOK_MODEL_LENGTH, "model"),
    )

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = list(scope.get("headers") or ())

        captured: dict[str, str | None] = {"api_key": None, "base_url": None, "model": None}
        stripped_names = {rule[0].lower().encode("ascii") for rule in self._HEADER_RULES}
        invalid_status: int | None = None

        for header_name, normalizer, max_length, attr in self._HEADER_RULES:
            lower_name = header_name.lower().encode("ascii")
            values = [value for name, value in raw_headers if name.lower() == lower_name]
            if not values:
                continue
            if len(values) > 1:
                invalid_status = 400
                break
            raw_value = values[0]
            if len(raw_value) > max_length:
                invalid_status = 431
                break
            try:
                captured[attr] = normalizer(raw_value.decode("latin-1"))
            except (UnicodeError, InvalidRequestApiKey):
                invalid_status = 400
                break

        if invalid_status is not None:
            from starlette.responses import JSONResponse

            response = JSONResponse(
                status_code=invalid_status,
                content={"detail": "Invalid LLM API key header"},
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                    "Referrer-Policy": "no-referrer",
                },
            )
            await response(scope, receive, send)
            return

        # Keep the original scope immutable for outer middleware, while hiding
        # the credential from routers and downstream middleware.
        downstream_scope = dict(scope)
        downstream_scope["headers"] = [
            (name, value)
            for name, value in raw_headers
            if name.lower() not in stripped_names
        ]
        if captured["api_key"] or captured["base_url"] or captured["model"]:
            from app.utils import logging as _xlog
            _xlog.info(
                0,
                "[byok-debug] capture api_key=%s base_url=%s model=%s",
                "<set>" if captured["api_key"] else "<none>",
                captured["base_url"] or "<none>",
                captured["model"] or "<none>",
            )
        with request_byok_context(
            api_key=captured["api_key"],
            base_url=captured["base_url"],
            model=captured["model"],
        ):
            await self.app(downstream_scope, receive, send)


def _apply_byok_json_compat(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Inject a JSON-mode hint when BYOK targets a strict provider.

    Some OpenAI-compatible providers (e.g. Zhipu GLM) reject
    ``response_format={"type": "json_object"}`` unless the prompt literally
    contains the word "JSON".  When BYOK is active and the caller requested
    JSON mode, append a short system-side hint so any user-supplied base URL
    works without forcing every caller to remember the constraint.

    No-op for platform leases and for calls that do not request JSON mode.
    """

    if not get_request_api_key():
        return kwargs
    response_format = kwargs.get("response_format")
    if not isinstance(response_format, dict) or response_format.get("type") != "json_object":
        return kwargs
    messages = kwargs.get("messages")
    if not isinstance(messages, list) or not messages:
        return kwargs

    def _has_json_hint(text: Any) -> bool:
        if not isinstance(text, str):
            return False
        return "json" in text.lower()

    already_tagged = False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, list):
            already_tagged = any(_has_json_hint(part.get("text")) for part in content if isinstance(part, dict))
        else:
            already_tagged = _has_json_hint(content)
        if already_tagged:
            break
    if already_tagged:
        return kwargs

    new_messages = list(messages)
    new_messages.insert(
        0,
        {"role": "system", "content": "请严格以 JSON 格式输出，不要包含任何额外说明或 markdown 代码块标记。"},
    )
    new_kwargs = dict(kwargs)
    new_kwargs["messages"] = new_messages
    return new_kwargs


def parse_api_keys(value: str | Iterable[str] | None) -> tuple[str, ...]:
    """Normalize, de-duplicate and preserve the order of configured keys."""

    if value is None:
        values: Iterable[str] = ()
    elif isinstance(value, str):
        values = re.split(r"[,\r\n]+", value)
    else:
        values = value

    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        key = str(raw or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return tuple(result)


def configured_api_keys(
    *,
    pool_env: str | Sequence[str] | None,
    fallback_key: str | None = None,
) -> tuple[str, ...]:
    """Resolve the first configured pool and append the legacy single key."""

    env_names = (pool_env,) if isinstance(pool_env, str) else tuple(pool_env or ())
    keys: tuple[str, ...] = ()
    for name in env_names:
        keys = parse_api_keys(os.getenv(name))
        if keys:
            break
    return parse_api_keys((*keys, fallback_key or ""))


def api_key_available(
    fallback_key: str | None = None,
    *,
    pool_env: str | Sequence[str] | None = "OPENAI_API_KEYS",
    allow_byok: bool = True,
) -> bool:
    """Check dynamic credentials for feature gates without exposing a value."""

    if allow_byok and get_request_api_key():
        return True
    keys = configured_api_keys(pool_env=pool_env, fallback_key=fallback_key)
    return any(
        key.lower() not in {"sk-mock-key", "sk-xxx", "your-api-key-here"}
        for key in keys
    )


@dataclass(frozen=True, repr=False)
class ApiKeyLease:
    api_key: str
    source: Literal["byok", "platform"]
    slot: int | None = None

    @property
    def is_byok(self) -> bool:
        return self.source == "byok"

    def __repr__(self) -> str:
        return f"ApiKeyLease(source={self.source!r}, slot={self.slot!r}, api_key=<redacted>)"


@dataclass(frozen=True)
class FailureDecision:
    retryable: bool
    category: str
    status_code: int | None


@dataclass(repr=False)
class _KeyState:
    api_key: str
    failure_count: int = 0
    available_at: float = 0.0


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if isinstance(value, int):
        return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _failure_decision(exc: BaseException) -> FailureDecision:
    status = _status_code(exc)
    name = type(exc).__name__.lower()
    if status in (401, 403):
        return FailureDecision(True, "authentication", status)
    if status == 429:
        return FailureDecision(True, "rate_limited", status)
    if status in (408, 425) or (status is not None and status >= 500):
        return FailureDecision(True, "provider_unavailable", status)
    if status is not None and 400 <= status < 500:
        return FailureDecision(False, "request_rejected", status)
    if "timeout" in name:
        return FailureDecision(True, "timeout", status)
    if "connection" in name or "network" in name:
        return FailureDecision(True, "connection", status)
    # Provider SDKs do not consistently attach a status to connection errors.
    # Treat an unknown call-time failure as transient, but still bound attempts
    # to the number of distinct keys.
    return FailureDecision(True, "provider_unavailable", status)


class ApiKeyPool:
    """Thread-safe round-robin selection with bounded per-key cooldown."""

    def __init__(
        self,
        keys: Iterable[str],
        *,
        cooldown_seconds: float = 15.0,
        auth_cooldown_seconds: float = 300.0,
        max_cooldown_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        normalized = parse_api_keys(keys)
        self._states = tuple(_KeyState(key) for key in normalized)
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._auth_cooldown_seconds = max(0.0, float(auth_cooldown_seconds))
        self._max_cooldown_seconds = max(0.0, float(max_cooldown_seconds))
        self._clock = clock
        self._cursor = 0
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self._states)

    @property
    def size(self) -> int:
        return len(self._states)

    def __repr__(self) -> str:
        return f"ApiKeyPool(size={self.size}, credentials=<redacted>)"

    def get_candidates(
        self,
        request_api_key: str | None | object = _USE_REQUEST_CONTEXT,
    ) -> tuple[ApiKeyLease, ...]:
        if request_api_key is _USE_REQUEST_CONTEXT:
            request_api_key = get_request_api_key()
        if isinstance(request_api_key, str) and request_api_key:
            return (ApiKeyLease(request_api_key, "byok"),)

        with self._lock:
            size = len(self._states)
            if not size:
                return ()
            now = self._clock()
            start = self._cursor % size
            candidates = tuple(
                ApiKeyLease(state.api_key, "platform", slot)
                for offset in range(size)
                for slot in ((start + offset) % size,)
                for state in (self._states[slot],)
                if state.available_at <= now
            )
            if candidates:
                self._cursor = (start + 1) % size
            return candidates

    def report_success(self, lease: ApiKeyLease) -> None:
        if lease.is_byok or lease.slot is None:
            return
        with self._lock:
            if not 0 <= lease.slot < len(self._states):
                return
            state = self._states[lease.slot]
            if state.api_key != lease.api_key:
                return
            state.failure_count = 0
            state.available_at = 0.0

    def report_failure(self, lease: ApiKeyLease, exc: BaseException) -> FailureDecision:
        decision = _failure_decision(exc)
        if lease.is_byok or lease.slot is None or not decision.retryable:
            return decision

        with self._lock:
            if not 0 <= lease.slot < len(self._states):
                return decision
            state = self._states[lease.slot]
            if state.api_key != lease.api_key:
                return decision
            state.failure_count += 1
            base = (
                self._auth_cooldown_seconds
                if decision.category == "authentication"
                else self._cooldown_seconds
            )
            delay = base * (2 ** max(0, state.failure_count - 1))
            if self._max_cooldown_seconds:
                delay = min(delay, self._max_cooldown_seconds)
            state.available_at = self._clock() + delay
        return decision


async def _close_async_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        pass


def _close_sync_client(client: Any) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        pass


class _ClosingAsyncStream:
    def __init__(
        self,
        stream: Any,
        client: Any,
        pool: ApiKeyPool,
        lease: ApiKeyLease,
        *,
        close_client: bool,
    ) -> None:
        self._stream = stream
        self._client = client
        self._pool = pool
        self._lease = lease
        self._close_client = close_client
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __aiter__(self):
        return self

    async def __anext__(self):
        ended = False
        raw_error: BaseException | None = None
        try:
            return await self._stream.__anext__()
        except StopAsyncIteration:
            ended = True
        except BaseException as exc:
            raw_error = exc

        if ended:
            self._pool.report_success(self._lease)
            await self.aclose()
            raise StopAsyncIteration

        assert raw_error is not None
        if not isinstance(raw_error, Exception):
            await self.aclose()
            raise raw_error
        decision = self._pool.report_failure(self._lease, raw_error)
        sanitized = SanitizedProviderError(
            category=decision.category,
            status_code=decision.status_code,
            credential_source=self._lease.source,
            retryable=decision.retryable,
        )
        raw_error = None
        await self.aclose()
        # A stream must never be replayed on another key after yielding data.
        raise sanitized from None

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream = self._stream
        client = self._client
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                result = close()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                pass
        if self._close_client:
            await _close_async_client(client)
            self._client = None

    async def __aenter__(self):
        enter = getattr(self._stream, "__aenter__", None)
        if callable(enter):
            await enter()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()


class _ClosingSyncStream:
    def __init__(
        self,
        stream: Any,
        client: Any,
        pool: ApiKeyPool,
        lease: ApiKeyLease,
        *,
        close_client: bool,
    ) -> None:
        self._stream = stream
        self._client = client
        self._pool = pool
        self._lease = lease
        self._close_client = close_client
        self._iterator = iter(stream)
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def __iter__(self):
        return self

    def __next__(self):
        ended = False
        raw_error: BaseException | None = None
        try:
            return next(self._iterator)
        except StopIteration:
            ended = True
        except BaseException as exc:
            raw_error = exc

        if ended:
            self._pool.report_success(self._lease)
            self.close()
            raise StopIteration

        assert raw_error is not None
        if not isinstance(raw_error, Exception):
            self.close()
            raise raw_error
        decision = self._pool.report_failure(self._lease, raw_error)
        sanitized = SanitizedProviderError(
            category=decision.category,
            status_code=decision.status_code,
            credential_source=self._lease.source,
            retryable=decision.retryable,
        )
        raw_error = None
        self.close()
        raise sanitized from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        stream_close = getattr(self._stream, "close", None)
        if callable(stream_close):
            try:
                stream_close()
            except Exception:
                pass
        if self._close_client:
            _close_sync_client(self._client)
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class _AsyncCreateEndpoint:
    def __init__(self, owner: "PooledAsyncOpenAI", path: tuple[str, ...]) -> None:
        self._owner = owner
        self._path = path

    async def create(self, **kwargs: Any) -> Any:
        return await self._owner._execute(self._path, "create", kwargs)

    async def generate(self, **kwargs: Any) -> Any:
        return await self._owner._execute(self._path, "generate", kwargs)


class _SyncCreateEndpoint:
    def __init__(self, owner: "PooledOpenAI", path: tuple[str, ...]) -> None:
        self._owner = owner
        self._path = path

    def create(self, **kwargs: Any) -> Any:
        return self._owner._execute(self._path, "create", kwargs)

    def generate(self, **kwargs: Any) -> Any:
        return self._owner._execute(self._path, "generate", kwargs)


class _Namespace:
    pass


def _resolve_endpoint(
    client: Any,
    path: tuple[str, ...],
    method: str,
) -> Callable[..., Any]:
    target = client
    for item in path:
        target = getattr(target, item)
    return getattr(target, method)


@contextmanager
def _llm_provider_span(
    *,
    path: tuple[str, ...],
    method: str,
    kwargs: dict[str, Any],
    lease: ApiKeyLease,
    client_kwargs: dict[str, Any],
) -> Iterator[Any]:
    try:
        from app.observability import get_tracer, tracing_enabled

        if not tracing_enabled():
            with nullcontext(None) as span:
                yield span
            return
        tracer = get_tracer("if-line.llm")
        if tracer is None:
            with nullcontext(None) as span:
                yield span
            return
        from opentelemetry import context, trace
    except Exception:
        with nullcontext(None) as span:
            yield span
        return

    operation = ".".join((*path, method))
    span = tracer.start_span(
        f"llm {operation}",
        attributes=_llm_request_attributes(
            path=path,
            method=method,
            kwargs=kwargs,
            lease=lease,
            client_kwargs=client_kwargs,
        ),
    )
    token = None
    try:
        token = context.attach(trace.set_span_in_context(span))
        yield span
    except Exception as exc:
        _mark_llm_span_failed(span, exc)
        raise
    finally:
        if token is not None:
            try:
                context.detach(token)
            except Exception:
                pass
        try:
            span.end()
        except Exception:
            pass


def _llm_request_attributes(
    *,
    path: tuple[str, ...],
    method: str,
    kwargs: dict[str, Any],
    lease: ApiKeyLease,
    client_kwargs: dict[str, Any],
) -> dict[str, Any]:
    base_url = str(get_request_base_url() if lease.is_byok else client_kwargs.get("base_url") or "")
    parsed = urlparse(base_url) if base_url else None
    attributes: dict[str, Any] = {
        "gen_ai.system": "openai-compatible",
        "gen_ai.operation.name": ".".join((*path, method)),
        "llm.request.type": ".".join(path),
        "llm.request.method": method,
        "llm.request.model": str(kwargs.get("model") or ""),
        "llm.request.stream": bool(kwargs.get("stream")),
        "llm.credential_source": lease.source,
    }
    if parsed is not None:
        attributes["server.address"] = parsed.hostname or ""
        attributes["url.scheme"] = parsed.scheme or ""
        attributes["url.path"] = parsed.path or ""
    return attributes


def _finish_llm_span(span: Any, response: Any) -> None:
    if span is None:
        return
    try:
        response_id = getattr(response, "id", None)
        response_model = getattr(response, "model", None)
        if response_id:
            span.set_attribute("llm.response.id", str(response_id))
        if response_model:
            span.set_attribute("llm.response.model", str(response_model))

        usage = getattr(response, "usage", None)
        if usage is not None:
            for attr_name, field_name in (
                ("llm.usage.prompt_tokens", "prompt_tokens"),
                ("llm.usage.completion_tokens", "completion_tokens"),
                ("llm.usage.total_tokens", "total_tokens"),
            ):
                value = getattr(usage, field_name, None)
                if value is not None:
                    span.set_attribute(attr_name, int(value))

        choices = getattr(response, "choices", None) or []
        if choices:
            finish_reason = getattr(choices[0], "finish_reason", None)
            if finish_reason:
                span.set_attribute("llm.response.finish_reason", str(finish_reason))
    except Exception:
        pass


def _mark_llm_span_failed(span: Any, exc: Exception) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        span.set_attribute("error.type", type(exc).__name__)
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    except Exception:
        pass


class PooledAsyncOpenAI:
    """Small AsyncOpenAI-compatible facade for the resources used by the app."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        pool_env: str | Sequence[str] | None = "OPENAI_API_KEYS",
        allow_byok: bool = True,
        api_keys: Iterable[str] | None = None,
        _client_factory: Callable[..., Any] | None = None,
        **client_kwargs: Any,
    ) -> None:
        keys = parse_api_keys(api_keys) if api_keys is not None else configured_api_keys(
            pool_env=pool_env,
            fallback_key=api_key,
        )
        self.pool = ApiKeyPool(keys)
        self.allow_byok = allow_byok
        self._client_factory = _client_factory
        self._client_kwargs = client_kwargs
        self._platform_clients: dict[int, Any] = {}
        self._client_lock = threading.RLock()

        self.chat = _Namespace()
        self.chat.completions = _AsyncCreateEndpoint(self, ("chat", "completions"))
        self.images = _AsyncCreateEndpoint(self, ("images",))

    def _new_client(self, api_key: str, *, base_url_override: str | None = None) -> Any:
        if base_url_override is not None:
            kwargs = {**self._client_kwargs, "base_url": base_url_override}
        else:
            kwargs = self._client_kwargs
        if self._client_factory is not None:
            return self._client_factory(api_key=api_key, **kwargs)
        from openai import AsyncOpenAI

        try:
            return AsyncOpenAI(api_key=api_key, **kwargs)
        except ImportError as exc:
            if "SOCKS proxy" not in str(exc) or "http_client" in kwargs:
                raise
            import httpx

            sock_kwargs = dict(kwargs)
            sock_kwargs["http_client"] = httpx.AsyncClient(trust_env=False)
            return AsyncOpenAI(api_key=api_key, **sock_kwargs)

    def _client_for(self, lease: ApiKeyLease) -> Any:
        if lease.is_byok or lease.slot is None:
            return self._new_client(
                lease.api_key,
                base_url_override=get_request_base_url() if lease.is_byok else None,
            )
        with self._client_lock:
            client = self._platform_clients.get(lease.slot)
            if client is None:
                client = self._new_client(lease.api_key)
                self._platform_clients[lease.slot] = client
            return client

    async def _execute(
        self,
        path: tuple[str, ...],
        method: str,
        kwargs: dict[str, Any],
    ) -> Any:
        request_key = get_request_api_key() if self.allow_byok else None
        candidates = self.pool.get_candidates(request_key)
        if not candidates:
            with self.pool._lock:
                now = self.pool._clock()
                remaining = [
                    round(max(0.0, state.available_at - now), 1)
                    for state in self.pool._states
                ]
            logger.warning(
                "api key pool exhausted: %d keys all cooling down (remaining seconds: %s)",
                len(remaining),
                remaining,
            )
            raise ApiKeyPoolUnavailable()

        last_error: SanitizedProviderError | None = None
        for lease in candidates:
            client: Any = None
            raw_error: BaseException | None = None
            try:
                client = self._client_for(lease)
                effective_kwargs = _apply_byok_json_compat(kwargs) if lease.is_byok else kwargs
                with _llm_provider_span(
                    path=path,
                    method=method,
                    kwargs=effective_kwargs,
                    lease=lease,
                    client_kwargs=self._client_kwargs,
                ) as span:
                    response = await _resolve_endpoint(client, path, method)(**effective_kwargs)
                    if not effective_kwargs.get("stream"):
                        _finish_llm_span(span, response)
            except BaseException as exc:
                raw_error = exc

            if raw_error is not None:
                if lease.is_byok and client is not None:
                    await _close_async_client(client)
                if not isinstance(raw_error, Exception):
                    raise raw_error
                decision = self.pool.report_failure(lease, raw_error)
                # TEMP DEBUG: surface upstream status/body for BYOK diagnosis.
                if lease.is_byok:
                    from app.utils import logging as _xlog
                    _upstream_status = getattr(raw_error, "status_code", None)
                    _upstream_resp = getattr(raw_error, "response", None)
                    _upstream_body = ""
                    if _upstream_resp is not None:
                        try:
                            _upstream_body = _upstream_resp.text[:500] if hasattr(_upstream_resp, "text") else ""
                        except Exception:
                            _upstream_body = ""
                    _xlog.info(
                        0,
                        "[byok-debug] upstream err type=%s status=%s category=%s body=%s",
                        type(raw_error).__name__,
                        _upstream_status,
                        decision.category,
                        _upstream_body,
                    )
                last_error = SanitizedProviderError(
                    category=decision.category,
                    status_code=decision.status_code,
                    credential_source=lease.source,
                    retryable=decision.retryable,
                )
                should_raise = lease.is_byok or not decision.retryable
                raw_error = None
                if should_raise:
                    raise last_error from None
                continue

            if kwargs.get("stream"):
                return _ClosingAsyncStream(
                    response,
                    client,
                    self.pool,
                    lease,
                    close_client=lease.is_byok,
                )

            self.pool.report_success(lease)
            if lease.is_byok:
                await _close_async_client(client)
            return response

        if last_error is not None:
            raise last_error from None
        raise ApiKeyPoolUnavailable()


class PooledOpenAI:
    """Synchronous counterpart used by the legacy vision validator."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        pool_env: str | Sequence[str] | None = "OPENAI_API_KEYS",
        allow_byok: bool = True,
        api_keys: Iterable[str] | None = None,
        _client_factory: Callable[..., Any] | None = None,
        **client_kwargs: Any,
    ) -> None:
        keys = parse_api_keys(api_keys) if api_keys is not None else configured_api_keys(
            pool_env=pool_env,
            fallback_key=api_key,
        )
        self.pool = ApiKeyPool(keys)
        self.allow_byok = allow_byok
        self._client_factory = _client_factory
        self._client_kwargs = client_kwargs
        self._platform_clients: dict[int, Any] = {}
        self._client_lock = threading.RLock()

        self.chat = _Namespace()
        self.chat.completions = _SyncCreateEndpoint(self, ("chat", "completions"))
        self.images = _SyncCreateEndpoint(self, ("images",))

    def _new_client(self, api_key: str, *, base_url_override: str | None = None) -> Any:
        if base_url_override is not None:
            kwargs = {**self._client_kwargs, "base_url": base_url_override}
        else:
            kwargs = self._client_kwargs
        if self._client_factory is not None:
            return self._client_factory(api_key=api_key, **kwargs)
        from openai import OpenAI

        try:
            return OpenAI(api_key=api_key, **kwargs)
        except ImportError as exc:
            if "SOCKS proxy" not in str(exc) or "http_client" in kwargs:
                raise
            import httpx

            sock_kwargs = dict(kwargs)
            sock_kwargs["http_client"] = httpx.Client(trust_env=False)
            return OpenAI(api_key=api_key, **sock_kwargs)

    def _client_for(self, lease: ApiKeyLease) -> Any:
        if lease.is_byok or lease.slot is None:
            return self._new_client(
                lease.api_key,
                base_url_override=get_request_base_url() if lease.is_byok else None,
            )
        with self._client_lock:
            client = self._platform_clients.get(lease.slot)
            if client is None:
                client = self._new_client(lease.api_key)
                self._platform_clients[lease.slot] = client
            return client

    def _execute(
        self,
        path: tuple[str, ...],
        method: str,
        kwargs: dict[str, Any],
    ) -> Any:
        request_key = get_request_api_key() if self.allow_byok else None
        candidates = self.pool.get_candidates(request_key)
        if not candidates:
            with self.pool._lock:
                now = self.pool._clock()
                remaining = [
                    round(max(0.0, state.available_at - now), 1)
                    for state in self.pool._states
                ]
            logger.warning(
                "api key pool exhausted: %d keys all cooling down (remaining seconds: %s)",
                len(remaining),
                remaining,
            )
            raise ApiKeyPoolUnavailable()

        last_error: SanitizedProviderError | None = None
        for lease in candidates:
            client: Any = None
            raw_error: BaseException | None = None
            try:
                client = self._client_for(lease)
                effective_kwargs = _apply_byok_json_compat(kwargs) if lease.is_byok else kwargs
                with _llm_provider_span(
                    path=path,
                    method=method,
                    kwargs=effective_kwargs,
                    lease=lease,
                    client_kwargs=self._client_kwargs,
                ) as span:
                    response = _resolve_endpoint(client, path, method)(**effective_kwargs)
                    if not effective_kwargs.get("stream"):
                        _finish_llm_span(span, response)
            except BaseException as exc:
                raw_error = exc

            if raw_error is not None:
                if lease.is_byok and client is not None:
                    _close_sync_client(client)
                if not isinstance(raw_error, Exception):
                    raise raw_error
                decision = self.pool.report_failure(lease, raw_error)
                last_error = SanitizedProviderError(
                    category=decision.category,
                    status_code=decision.status_code,
                    credential_source=lease.source,
                    retryable=decision.retryable,
                )
                should_raise = lease.is_byok or not decision.retryable
                raw_error = None
                if should_raise:
                    raise last_error from None
                continue

            if kwargs.get("stream"):
                return _ClosingSyncStream(
                    response,
                    client,
                    self.pool,
                    lease,
                    close_client=lease.is_byok,
                )

            self.pool.report_success(lease)
            if lease.is_byok:
                _close_sync_client(client)
            return response

        if last_error is not None:
            raise last_error from None
        raise ApiKeyPoolUnavailable()
