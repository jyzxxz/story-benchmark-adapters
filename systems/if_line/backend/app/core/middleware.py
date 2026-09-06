"""Request correlation, safe headers, access logging and HTTP metrics."""
from __future__ import annotations

import re
import time
import uuid
from contextlib import nullcontext
from http import HTTPStatus
from typing import Any
from typing import Awaitable, Callable

from fastapi import Request, Response

from app.core.config import AppSettings
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from app.observability import get_tracer, tracing_enabled
from app.services.api_key_pool import RequestApiKeyMiddleware
from app.utils import logging as xlog


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


def _request_id(request: Request) -> str:
    candidate = (request.headers.get("x-request-id") or "").strip()
    if candidate and _REQUEST_ID_RE.fullmatch(candidate):
        return candidate
    return str(uuid.uuid4())


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return path or "unmatched"


def _http_status_text(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return str(status_code)


def _request_span(request: Request):
    if not tracing_enabled():
        return nullcontext(None)
    tracer = get_tracer("if-line.http")
    if tracer is None:
        return nullcontext(None)
    return tracer.start_as_current_span(
        f"{request.method} {request.url.path}",
        attributes={
            "http.request.method": request.method,
            "url.path": request.url.path,
            "url.scheme": request.url.scheme,
            "server.address": request.url.hostname or "",
            "server.port": request.url.port or 0,
        },
    )


def _finish_request_span(span: Any, request: Request, request_id: str, status_code: int, elapsed: float) -> None:
    if span is None:
        return
    route = _route_label(request)
    try:
        span.update_name(f"{request.method} {route}")
        span.set_attribute("http.route", route)
        span.set_attribute("http.response.status_code", status_code)
        span.set_attribute("http.response.status_text", _http_status_text(status_code))
        span.set_attribute("http.request_id", request_id)
        span.set_attribute("ifline.elapsed_ms", int(elapsed * 1000))
        if status_code >= 500:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.ERROR, _http_status_text(status_code)))
    except Exception:
        pass


def _fail_request_span(span: Any, request: Request, request_id: str, exc: Exception, elapsed: float) -> None:
    if span is None:
        return
    try:
        span.record_exception(exc)
        span.set_attribute("http.request_id", request_id)
        span.set_attribute("ifline.elapsed_ms", int(elapsed * 1000))
        from opentelemetry.trace import Status, StatusCode

        span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
    except Exception:
        pass


def install_http_middleware(app, settings: AppSettings) -> None:
    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.monotonic()

        with _request_span(request) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                elapsed = time.monotonic() - started
                _fail_request_span(span, request, request_id, exc, elapsed)
                xlog.error(
                    request_id,
                    exc,
                    "[http] request failed method=%s path=%s elapsed_ms=%d",
                    request.method,
                    request.url.path,
                    int(elapsed * 1000),
                )
                raise

            elapsed = time.monotonic() - started
            route = _route_label(request)
            _finish_request_span(span, request, request_id, response.status_code, elapsed)
            HTTP_REQUESTS.labels(request.method, route, str(response.status_code)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(elapsed)

            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
            if settings.security_hsts_enabled:
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"

            xlog.info(
                request_id,
                "[http] request ok method=%s path=%s status=%d elapsed_ms=%d",
                request.method,
                request.url.path,
                response.status_code,
                int(elapsed * 1000),
            )
            return response

    # Starlette inserts newly registered middleware at the front. Register the
    # pure ASGI credential capture last so it is outermost: all downstream
    # middleware sees a scope from which the secret header has been removed.
    app.add_middleware(RequestApiKeyMiddleware)
