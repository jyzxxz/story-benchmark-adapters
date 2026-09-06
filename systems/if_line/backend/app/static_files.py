"""
Static file serving with project-aware access checks for generated assets.
"""
from __future__ import annotations

from http.cookies import SimpleCookie
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from app.auth import AUTH_COOKIE_NAME, get_user_for_session_token
from app.database import SessionLocal
from app.models import Asset
from app.project_permissions import is_project_owner, is_project_publicly_readable


class ProjectAwareStaticFiles(StaticFiles):
    """Protect legacy generated media under ``/static``.

    New private media is delivered through ``/api/media/{id}``. Reference
    audio and cloned-voice caches must never fall through the legacy static
    mount, even when a caller knows their physical path.
    """

    PROTECTED_PREFIXES = ("assets/", "tts_cache/")
    DENIED_PREFIXES = (
        "voice_refs/",
        "tts_cache/voice_clone/",
        "storage/",
        ".tmp/",
    )

    async def get_response(self, path: str, scope: Scope):
        # Starlette uses OS-native separators internally on Windows. Convert
        # before matching so the policy is identical on Windows and Linux.
        normalized = path.replace("\\", "/").lstrip("/")
        lowered = normalized.casefold()
        if any(
            lowered == prefix.rstrip("/") or lowered.startswith(prefix)
            for prefix in self.DENIED_PREFIXES
        ):
            return PlainTextResponse("Not Found", status_code=404)
        if lowered.startswith(self.PROTECTED_PREFIXES):
            if not self._can_access_generated_asset(normalized, scope):
                return PlainTextResponse("Not Found", status_code=404)
        return await super().get_response(path, scope)

    @staticmethod
    def _cookie_value(scope: Scope, name: str) -> Optional[str]:
        headers = Headers(scope=scope)
        raw_cookie = headers.get("cookie")
        if not raw_cookie:
            return None
        cookie = SimpleCookie()
        cookie.load(raw_cookie)
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    def _can_access_generated_asset(self, path: str, scope: Scope) -> bool:
        url = f"/static/{path}"
        db = SessionLocal()
        try:
            try:
                assets = db.query(Asset).filter(Asset.image_url == url).all()
                # 归一化产物（portrait_normalization_service 把立绘合成到
                # generic-portrait-canvas-v2 上）的 URL 不在 Asset.image_url，
                # 而是嵌在 generation_params.presentation.url / presentation_url /
                # source_image_url 里。前端拿到的展示 URL 经常是归一化产物，
                # 所以这里要补一层匹配，否则已登录 owner 也会看到 404。
                if not assets:
                    from sqlalchemy import text as _sql_text

                    sql = (
                        "SELECT id FROM assets "
                        "WHERE CAST(generation_params AS TEXT) LIKE :pat "
                        "LIMIT 20"
                    )
                    candidate_ids = [
                        row[0]
                        for row in db.execute(
                            _sql_text(sql), {"pat": f'%"{url}"%'}
                        )
                    ]
                    if candidate_ids:
                        assets = (
                            db.query(Asset)
                            .filter(Asset.id.in_(candidate_ids))
                            .all()
                        )
                        assets = [
                            a for a in assets
                            if self._asset_exposes_url(a, url)
                        ]
            except SQLAlchemyError:
                # Static mounts used by health checks or isolated tests may not
                # have a database schema. Protected paths fail closed.
                return False
            projects = [asset.project for asset in assets if asset.project]
            if not projects:
                return False
            if any(is_project_publicly_readable(db, project) for project in projects):
                return True

            token = self._cookie_value(scope, AUTH_COOKIE_NAME)
            user = get_user_for_session_token(db, token, update_last_seen=False)
            return any(is_project_owner(project, user) for project in projects)
        finally:
            db.close()

    @staticmethod
    def _asset_exposes_url(asset: Asset, url: str) -> bool:
        """True if ``url`` is reachable via this asset's known URL fields.

        Covers: ``image_url``, ``generation_params.presentation_url``, and
        ``generation_params.presentation.url`` / ``source_image_url`` /
        ``identity_reference_url`` (which the normalization pipeline writes
        for the canvas-composited preview).
        """
        import json as _json

        if (asset.image_url or "") == url:
            return True
        raw = asset.generation_params
        if isinstance(raw, str):
            try:
                params = _json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                params = {}
        elif isinstance(raw, dict):
            params = raw
        else:
            params = {}
        if (params.get("presentation_url") or "") == url:
            return True
        presentation = params.get("presentation")
        if isinstance(presentation, dict):
            if (presentation.get("url") or "") == url:
                return True
            if (presentation.get("source_image_url") or "") == url:
                return True
        return False
