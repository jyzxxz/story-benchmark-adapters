from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute
import yaml

from app.core.config import AppSettings
from app.main import create_app


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})

PROJECT = "/api/projects/{project_id}"
CHAPTER = f"{PROJECT}/chapters/{{chapter_index}}"
SCRIPT = f"{CHAPTER}/chapter-scripts"

# This is an operation set, not only a path set. GET release history remains on
# the same path where legacy POST release creation was removed.
LEGACY_AUTHORING_OPERATIONS = frozenset(
    {
        ("POST", f"{PROJECT}/claim"),
        ("GET", f"{PROJECT}/status"),
        ("GET", f"{PROJECT}/readiness"),
        ("GET", f"{PROJECT}/stats"),
        ("GET", f"{PROJECT}/stats/breakdown"),
        ("GET", f"{PROJECT}/bible/current"),
        ("GET", f"{PROJECT}/bible/revisions"),
        ("POST", f"{PROJECT}/bible/generations"),
        ("POST", f"{PROJECT}/bible/revisions"),
        ("POST", f"{PROJECT}/bible/revisions/{{revision_id}}/activate"),
        ("GET", f"{PROJECT}/outline/current"),
        ("GET", f"{PROJECT}/outline/revisions"),
        ("POST", f"{PROJECT}/outline/generations"),
        ("POST", f"{PROJECT}/outline/revisions"),
        ("POST", f"{PROJECT}/outline/revisions/{{revision_id}}/approve"),
        ("GET", f"{CHAPTER}/current"),
        ("GET", f"{CHAPTER}/revisions"),
        ("POST", f"{CHAPTER}/generations"),
        ("POST", f"{CHAPTER}/revisions"),
        ("POST", f"{CHAPTER}/revisions/{{revision_id}}/activate"),
        ("POST", f"{PROJECT}/chapter-generation-batches"),
        ("POST", f"{PROJECT}/branch-candidate-generations"),
        ("GET", f"{PROJECT}/checkpoints/{{checkpoint_node_id}}/candidates"),
        ("GET", f"{SCRIPT}/current"),
        ("GET", f"{SCRIPT}/revisions"),
        ("POST", f"{SCRIPT}/generations"),
        (
            "GET",
            f"{SCRIPT}/revisions/{{script_revision_id}}/resource-slots",
        ),
        (
            "POST",
            f"{SCRIPT}/revisions/{{script_revision_id}}/resource-renders",
        ),
        (
            "PUT",
            f"{SCRIPT}/revisions/{{script_revision_id}}/resource-slots/{{slot_id}}",
        ),
        ("POST", f"{CHAPTER}/vn-graphs/compile"),
        ("GET", f"{CHAPTER}/vn-graphs/current"),
        ("GET", f"{CHAPTER}/vn-graphs/revisions"),
        ("GET", f"{CHAPTER}/vn-graphs/revisions/{{revision_id}}"),
        ("POST", f"{PROJECT}/releases"),
        ("POST", f"{PROJECT}/releases/{{release_id}}/withdraw"),
        ("PUT", f"{PROJECT}/publication"),
        (
            "GET",
            "/api/public/releases/{release_id}/chapters/{chapter_index}/manifest",
        ),
        (
            "GET",
            "/api/public/releases/{release_id}/chapters/{chapter_index}/vn-graph",
        ),
        ("POST", f"{CHAPTER}/generate-vn-graph"),
        ("POST", f"{CHAPTER}/generate-vn-graph-llm"),
        ("GET", f"{CHAPTER}/vn-graph"),
        ("PUT", f"{CHAPTER}/vn-graph"),
        ("GET", f"{CHAPTER}/vn-graph/export"),
        ("GET", f"{CHAPTER}/full-vn-graph"),
        ("POST", f"{CHAPTER}/full-vn-graph/assemble"),
        ("GET", f"{CHAPTER}/full-vn-graph/export"),
        ("POST", f"{CHAPTER}/resources/generate"),
        ("POST", f"{CHAPTER}/generate-assets-batch"),
        ("POST", f"{CHAPTER}/workflow-generations"),
    }
)

SHARED_PATH_REMOVED_OPERATIONS = frozenset({("POST", f"{PROJECT}/releases")})


def _application(tmp_path):
    settings = AppSettings(
        _env_file=None,
        app_env="test",
        static_dir=tmp_path / "static",
        task_system_enabled=True,
        revision_read_enabled=True,
    )
    return create_app(settings)


def _route_operations(application) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in application.routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method in HTTP_METHODS
    }


def _schema_operations(schema: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, path_item in schema.get("paths", {}).items()
        for method in path_item
        if method.upper() in HTTP_METHODS
    }


def _effective_security(
    schema: dict[str, Any],
    *,
    path: str,
    method: str,
) -> list[dict[str, list[str]]]:
    operation = schema["paths"][path][method.lower()]
    return operation.get("security", schema.get("security", []))


def _target_authoring_operations() -> set[tuple[str, str]]:
    contract_path = REPOSITORY_ROOT / "docs/api/story-path-authoring.openapi.yaml"
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    return {
        (method, f"/api{path}")
        for method, path in _schema_operations(contract)
    }


def _snapshot_operations() -> set[tuple[str, str]]:
    snapshot = json.loads(
        (BACKEND_ROOT / "openapi.v2.json").read_text(encoding="utf-8")
    )
    return _schema_operations(snapshot)


def _concrete_path(path: str) -> str:
    values = {
        "project_id": "1",
        "chapter_index": "1",
        "revision_id": "revision-id",
        "script_revision_id": "script-revision-id",
        "slot_id": "slot-id",
        "checkpoint_node_id": "checkpoint-id",
        "release_id": "release-id",
    }
    return re.sub(
        r"\{([^}]+)\}",
        lambda match: values.get(match.group(1), "resource-id"),
        path,
    )


def test_route_table_has_no_duplicate_method_path_pairs(tmp_path):
    operations = [
        (method, route.path)
        for route in _application(tmp_path).routes
        if isinstance(route, APIRoute)
        for method in route.methods or set()
        if method in HTTP_METHODS
    ]
    duplicates = sorted(key for key, count in Counter(operations).items() if count > 1)

    assert duplicates == []


def test_runtime_and_snapshot_include_all_frozen_authoring_operations(tmp_path):
    application = _application(tmp_path)
    target = _target_authoring_operations()
    runtime = _route_operations(application)
    dynamic_schema = _schema_operations(application.openapi())
    snapshot = _snapshot_operations()

    assert len(target) == 58
    assert sorted(target - runtime) == []
    assert sorted(target - dynamic_schema) == []
    assert sorted(target - snapshot) == []


def test_runtime_snapshot_and_frozen_contract_use_sid_cookie_auth(tmp_path):
    frozen = yaml.safe_load(
        (REPOSITORY_ROOT / "docs/api/story-path-authoring.openapi.yaml").read_text(
            encoding="utf-8"
        )
    )
    runtime = _application(tmp_path).openapi()
    snapshot = json.loads(
        (BACKEND_ROOT / "openapi.v2.json").read_text(encoding="utf-8")
    )

    for schema in (frozen, runtime, snapshot):
        schemes = schema["components"]["securitySchemes"]
        assert "bearerAuth" not in schemes
        cookie_auth = schemes["cookieAuth"]
        assert cookie_auth["type"] == "apiKey"
        assert cookie_auth["in"] == "cookie"
        assert cookie_auth["name"] == "sid"

    for path, path_item in frozen["paths"].items():
        for method in path_item:
            if method.upper() not in HTTP_METHODS:
                continue
            expected = _effective_security(frozen, path=path, method=method)
            runtime_path = f"/api{path}"
            assert _effective_security(
                runtime,
                path=runtime_path,
                method=method,
            ) == expected
            assert _effective_security(
                snapshot,
                path=runtime_path,
                method=method,
            ) == expected


def test_frozen_release_schema_matches_runtime_required_fields(tmp_path):
    frozen = yaml.safe_load(
        (REPOSITORY_ROOT / "docs/api/story-path-authoring.openapi.yaml").read_text(
            encoding="utf-8"
        )
    )
    runtime = _application(tmp_path).openapi()
    snapshot = json.loads(
        (BACKEND_ROOT / "openapi.v2.json").read_text(encoding="utf-8")
    )

    frozen_release = frozen["components"]["schemas"]["Release"]
    runtime_release = runtime["components"]["schemas"]["Release"]
    snapshot_release = snapshot["components"]["schemas"]["Release"]
    expected_required = {
        "id",
        "project_id",
        "version",
        "status",
        "manifest_hash",
        "authoring_fingerprint",
        "release_notes",
        "published_at",
        "withdrawn_at",
    }
    for release_schema in (frozen_release, runtime_release, snapshot_release):
        assert set(release_schema["required"]) == expected_required

    def allows_null(property_schema):
        property_type = property_schema.get("type")
        if isinstance(property_type, list) and "null" in property_type:
            return True
        return any(
            item.get("type") == "null"
            for keyword in ("anyOf", "oneOf")
            for item in property_schema.get(keyword, [])
        )

    for field in (
        "authoring_fingerprint",
        "release_notes",
        "published_at",
        "withdrawn_at",
    ):
        assert all(
            allows_null(release_schema["properties"][field])
            for release_schema in (frozen_release, runtime_release, snapshot_release)
        )


def test_legacy_operations_are_absent_from_dynamic_and_snapshot_openapi(tmp_path):
    application = _application(tmp_path)
    dynamic_schema = _schema_operations(application.openapi())
    snapshot = _snapshot_operations()

    assert len(LEGACY_AUTHORING_OPERATIONS) == 49
    assert sorted(LEGACY_AUTHORING_OPERATIONS & dynamic_schema) == []
    assert sorted(LEGACY_AUTHORING_OPERATIONS & snapshot) == []
    assert "/api/projects/" not in application.openapi()["paths"]


def test_fully_removed_legacy_paths_return_404(tmp_path):
    requests = LEGACY_AUTHORING_OPERATIONS - SHARED_PATH_REMOVED_OPERATIONS
    unexpected: dict[str, int] = {}

    client = TestClient(_application(tmp_path))
    try:
        for method, path in sorted(requests):
            response = client.request(
                method,
                _concrete_path(path),
                json={} if method in {"POST", "PUT", "PATCH"} else None,
                follow_redirects=False,
            )
            if response.status_code != 404:
                unexpected[f"{method} {path}"] = response.status_code
    finally:
        client.close()

    assert unexpected == {}


def test_removed_release_create_is_405_on_the_preserved_history_path(tmp_path):
    client = TestClient(_application(tmp_path))
    try:
        response = client.post(
            "/api/projects/1/releases",
            json={},
            follow_redirects=False,
        )
    finally:
        client.close()

    assert response.status_code == 405
    assert ("POST", f"{PROJECT}/releases") not in _snapshot_operations()
    assert ("GET", f"{PROJECT}/releases") in _snapshot_operations()


def test_assets_and_metrics_paths_are_owned_by_preserved_v2_routers(tmp_path):
    application = _application(tmp_path)
    expected_modules = {
        "/api/projects/{project_id}/assets": "app.routers.v2.assets",
        "/api/projects/{project_id}/metrics": "app.routers.v2.authoring_projects",
        "/api/projects/{project_id}/metrics/artifacts": "app.routers.v2.authoring_projects",
    }

    for path, module in expected_modules.items():
        matches = [
            route
            for route in application.routes
            if isinstance(route, APIRoute)
            and route.path == path
            and "GET" in (route.methods or set())
        ]
        assert len(matches) == 1
        assert matches[0].endpoint.__module__ == module
