"""Stable, non-leaking errors for new application services."""
from __future__ import annotations

from typing import Any, Mapping, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An expected application failure safe to expose to API clients."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status_code: int = 400,
        details: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = dict(details or {})


def error_payload(request: Request, error: AppError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": error.code,
            "message": error.message,
            "request_id": getattr(request.state, "request_id", None),
        }
    }
    if error.details:
        payload["error"]["details"] = error.details
    return payload


def install_error_handlers(app: FastAPI) -> None:
    """Install the v2 error foundation without changing legacy HTTP errors."""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(request, exc),
            headers={"Cache-Control": "no-store"},
        )
