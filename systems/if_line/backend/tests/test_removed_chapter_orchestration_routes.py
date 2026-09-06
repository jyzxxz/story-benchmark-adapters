from __future__ import annotations

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

from app.core.config import AppSettings
from app.main import create_app


def test_only_image_service_health_route_remains(tmp_path):
    application = create_app(
        AppSettings(
            _env_file=None,
            app_env="test",
            static_dir=tmp_path / "static",
            metrics_enabled=False,
        )
    )
    route_keys = {
        (method, route.path)
        for route in application.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }
    base = "/api/image-generation/{project_id}"

    assert ("GET", "/api/image-generation/status") in route_keys
    assert {
        ("POST", f"{base}/portraits/generate"),
        ("POST", f"{base}/backgrounds/generate"),
        ("POST", f"{base}/backgrounds/generate-chapter/{{chapter_index}}"),
        ("POST", f"{base}/keyframes/generate"),
        ("POST", f"{base}/keyframes/generate-chapter/{{chapter_index}}"),
        ("POST", f"{base}/generate-all"),
    }.isdisjoint(route_keys)
    status = TestClient(application).get("/api/image-generation/status")
    assert status.status_code == 200
    assert set(status.json()) == {"enabled", "model"}


def test_generic_asset_write_bypasses_are_absent(tmp_path):
    application = create_app(
        AppSettings(
            _env_file=None,
            app_env="test",
            static_dir=tmp_path / "static",
            metrics_enabled=False,
        )
    )
    route_keys = {
        (method, route.path)
        for route in application.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }
    assert {
        ("POST", "/api/projects/{project_id}/assets/{asset_id}/variants"),
        ("POST", "/api/projects/{project_id}/asset-bindings"),
        ("DELETE", "/api/projects/{project_id}/asset-bindings/{binding_id}"),
    }.isdisjoint(route_keys)
