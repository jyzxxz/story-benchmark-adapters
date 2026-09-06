"""AI immersive novel API application factory."""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

# Keep legacy modules that still read os.environ compatible during the
# additive migration.  Typed settings below also reads the same backend .env.
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from redis import Redis

from app.core.config import AppSettings, get_settings
from app.core.errors import install_error_handlers
from app.core.metrics import READINESS_CHECKS, render_metrics
from app.core.middleware import install_http_middleware
from app.database import check_database_connection, check_database_schema, engine
from app.observability import (
    setup_agent_otel,
    setup_opentelemetry,
    shutdown_agent_otel,
    shutdown_opentelemetry,
)
from app.routers import (
    auth,
    image_generation,
    llm,
    server_agent,
    social,
    tts,
    voice_clone,
)
from app.routers.v2 import authoring_bible as authoring_bible_v2
from app.routers.v2 import authoring_chapters as authoring_chapters_v2
from app.routers.v2 import authoring_candidates as authoring_candidates_v2
from app.routers.v2 import authoring_outlines as authoring_outlines_v2
from app.routers.v2 import authoring_projects as authoring_projects_v2
from app.routers.v2 import authoring_releases as authoring_releases_v2
from app.routers.v2 import authoring_scripts as authoring_scripts_v2
from app.routers.v2 import authoring_story_paths as authoring_story_paths_v2
from app.routers.v2 import authoring_vn_graphs as authoring_vn_graphs_v2
from app.routers.v2 import assets as assets_v2
from app.routers.v2 import media as media_v2
from app.routers.v2 import reading as reading_v2
from app.routers.v2 import tasks as tasks_v2
from app.routers.v2 import voice_lines as voice_lines_v2
from app.routers.v2 import visual_assets as visual_assets_v2
from app.static_files import ProjectAwareStaticFiles
from app.utils import logging as xlog


def _initialize_runtime_directories(settings: AppSettings) -> None:
    static_dir = settings.resolved_static_dir
    (static_dir / "tts_cache").mkdir(parents=True, exist_ok=True)
    assets_dir = static_dir / "assets"
    for subdir in ("portraits", "backgrounds", "keyframes"):
        (assets_dir / subdir).mkdir(parents=True, exist_ok=True)

    # The legacy voice-clone store remains during the compatibility window.
    # Directory creation belongs to startup, never module import.
    from app.services.voice_clone_storage_service import ensure_base_dirs

    ensure_base_dirs()


def _check_redis_connection(settings: AppSettings) -> None:
    client = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
    )
    try:
        if client.ping() is not True:
            raise RuntimeError("Redis ping returned an unexpected response")
    finally:
        client.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: AppSettings = app.state.settings
    xlog.setup_logger()
    setup_opentelemetry()
    setup_agent_otel()
    _initialize_runtime_directories(settings)

    # Production always checks the database before accepting traffic.  Local
    # development can opt in without making an unavailable provider prevent
    # module imports and CLI utilities.
    if settings.is_production or settings.check_dependencies_on_startup:
        check_database_connection()
        if settings.auth_ratelimit_backend == "redis":
            _check_redis_connection(settings)
    check_database_schema()

    xlog.info(0, "[startup] application ready env=%s", settings.app_env)
    try:
        yield
    finally:
        engine.dispose()
        shutdown_agent_otel()
        shutdown_opentelemetry()
        xlog.info(0, "[shutdown] database pool disposed")
        xlog.close()


def create_app(settings: AppSettings | None = None) -> FastAPI:
    settings = settings or get_settings()
    application = FastAPI(
        title=settings.app_name,
        description="AI 沉浸式小说续写与角色可视化 Agent 系统",
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.state.settings = settings

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_http_middleware(application, settings)
    install_error_handlers(application)

    static_dir = settings.resolved_static_dir
    application.mount(
        "/static",
        ProjectAwareStaticFiles(directory=str(static_dir), check_dir=False),
        name="static",
    )
    # The shared, curated library is public product content rather than a
    # project-owned generated asset, so it has a dedicated read-only mount.
    library_dir = (
        Path(__file__).resolve().parents[2]
        / "static/assets/universal_visual_novel_latest/planned_assets"
    )
    application.mount(
        "/library-assets",
        StaticFiles(directory=str(library_dir), check_dir=False),
        name="library-assets",
    )
    # V2 remains an independently validated working library until the full
    # task ledger is complete.  Mount it separately so v1.1 URLs stay stable.
    v2_library_dir = (
        Path(__file__).resolve().parents[2]
        / "static/assets/universal_visual_novel_v2_working"
    )
    application.mount(
        "/library-assets-v2",
        StaticFiles(directory=str(v2_library_dir), check_dir=False),
        name="library-assets-v2",
    )

    # Serve the production web build on the already-open API port.  This gives
    # public story readers a stable URL that does not depend on a long-lived
    # SSH tunnel to the Vite development server.
    frontend_dist_dir = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    application.mount(
        "/assets",
        StaticFiles(directory=str(frontend_dist_dir / "assets"), check_dir=False),
        name="frontend-assets",
    )
    # =========================================================================
    # v2 routers
    # 全部挂在 /api 下(无 /v2 前缀),靠子路径区分。v2 router 内部已自带
    # prefix="/projects/{project_id}/..." 等子前缀,因此顶层只给 "/api"。
    #
    # 注册顺序: v2 先注册, v1 兼容路由后注册。新增同 path 接口时必须先确认
    # 运行时命中顺序,避免文档和实际 handler 不一致.
    # =========================================================================
    application.include_router(tasks_v2.router, prefix="/api")
    application.include_router(assets_v2.router, prefix="/api")
    application.include_router(voice_lines_v2.router, prefix="/api")
    application.include_router(media_v2.router, prefix="/api")
    application.include_router(reading_v2.router, prefix="/api")
    application.include_router(visual_assets_v2.router, prefix="/api")
    application.include_router(authoring_projects_v2.router, prefix="/api")
    application.include_router(authoring_bible_v2.router, prefix="/api")
    application.include_router(authoring_story_paths_v2.router, prefix="/api")
    application.include_router(authoring_outlines_v2.router, prefix="/api")
    application.include_router(authoring_chapters_v2.router, prefix="/api")
    application.include_router(authoring_candidates_v2.router, prefix="/api")
    application.include_router(authoring_scripts_v2.router, prefix="/api")
    application.include_router(authoring_vn_graphs_v2.router, prefix="/api")
    application.include_router(authoring_releases_v2.router, prefix="/api")

    # =========================================================================
    # v1 兼容路由
    # 全部挂在 /api 下, 无 /v1 前缀. 多个 router 共用 /api/projects 前缀, 靠子路径区分.
    #
    # 兼容接口仅在前端尚未完成迁移的领域保留。
    # =========================================================================
    application.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    application.include_router(llm.router, prefix="/api/llm", tags=["llm"])
    application.include_router(tts.router, prefix="/api/tts", tags=["tts"])
    application.include_router(
        image_generation.router,
        prefix="/api/image-generation",
        tags=["image-generation"],
    )
    application.include_router(server_agent.router, prefix="/api/server-agent", tags=["server-agent"])
    application.include_router(social.router, prefix="/api", tags=["social"])
    application.include_router(
        voice_clone.router,
        prefix="/api/voice-clone",
        tags=["voice-clone"],
    )

    @application.get("/health/live", tags=["health"], include_in_schema=False)
    async def health_live():
        return {"status": "ok"}

    @application.get("/health/ready", tags=["health"], include_in_schema=False)
    async def health_ready():
        checks: dict[str, str] = {}
        current_check = "database"
        try:
            check_database_connection()
            checks["database"] = "ok"
            current_check = "schema"
            check_database_schema()
            checks["schema"] = "ok"
            if settings.auth_ratelimit_backend == "redis":
                current_check = "redis"
                _check_redis_connection(settings)
                checks["redis"] = "ok"
        except Exception as exc:
            READINESS_CHECKS.labels("failed").inc()
            xlog.error(0, exc, "[health] dependency readiness failed")
            checks[current_check] = "failed"
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "checks": checks},
                headers={"Cache-Control": "no-store"},
            )
        READINESS_CHECKS.labels("ok").inc()
        return {"status": "ready", "checks": checks}

    @application.get("/metrics", include_in_schema=False)
    async def metrics(request: Request):
        if not settings.metrics_enabled:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if settings.metrics_token is not None:
            expected = f"Bearer {settings.metrics_token.get_secret_value()}"
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied, expected):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
        payload, content_type = render_metrics(include_database=True)
        return Response(content=payload, media_type=content_type)

    @application.get("/")
    async def root():
        return {"message": settings.app_name, "version": settings.app_version}

    return application


app = create_app()
