from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.library_tag_service import create_library_asset_tag_links
from app.application.visual_asset_service import (
    MATCHER_VERSION,
    get_library_facets,
    parse_library_tag_ids,
    search_library_assets,
)
from app.auth import get_current_user
from app.database import Base, get_db
from app.library_tags import extract_library_facet_tags
from app.models import User
from app.models_v2 import LibraryAsset, LibraryTag, StorageObject
from app.routers.v2.visual_assets import router


CATALOG = "facet-v1"


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture()
def catalog(db):
    owner = User(
        email="facets@example.com",
        password_hash="hash",
        display_name="facets",
        is_active=True,
        quota_total=100,
        quota_daily=100,
    )
    db.add(owner)
    db.flush()

    street_rain = _add_asset(
        db,
        owner,
        "street-rain",
        quality=0.9,
        facets={
            "style": "historical",
            "location": "street",
            "weather": "rain",
            "mood": "calm",
        },
    )
    street_sun = _add_asset(
        db,
        owner,
        "street-sun",
        quality=0.8,
        facets={
            "style": "historical",
            "location": "street",
            "weather": "sunny",
        },
    )
    forest_rain = _add_asset(
        db,
        owner,
        "forest-rain",
        quality=0.7,
        facets={
            "style": "xianxia",
            "location": "forest",
            "weather": "rain",
        },
    )
    _add_asset(
        db,
        owner,
        "disabled-rain",
        enabled=False,
        facets={"style": "historical", "weather": "rain"},
    )
    _add_asset(
        db,
        owner,
        "rejected-rain",
        safety_status="rejected",
        facets={"style": "historical", "weather": "rain"},
    )
    _add_asset(
        db,
        owner,
        "quarantined-rain",
        storage_status="quarantined",
        facets={"style": "historical", "weather": "rain"},
    )
    _add_asset(
        db,
        owner,
        "deleted-rain",
        storage_deleted=True,
        facets={"style": "historical", "weather": "rain"},
    )
    _add_asset(
        db,
        owner,
        "other-catalog",
        catalog_version="facet-v2",
        facets={"location": "moonbase"},
    )
    _add_asset(
        db,
        owner,
        "portrait-only",
        asset_type="portrait",
        facets={"expression": "smirk"},
    )
    db.commit()
    return SimpleNamespace(
        owner=owner,
        street_rain=street_rain,
        street_sun=street_sun,
        forest_rain=forest_rain,
    )


def _add_asset(
    db,
    owner: User,
    stable_key: str,
    *,
    facets: dict[str, str],
    catalog_version: str = CATALOG,
    asset_type: str = "background",
    quality: float = 1.0,
    enabled: bool = True,
    storage_status: str = "active",
    storage_deleted: bool = False,
    safety_status: str = "approved",
) -> LibraryAsset:
    storage = StorageObject(
        owner_id=owner.id,
        project_id=None,
        storage_backend="local",
        storage_key=f"facets/{catalog_version}/{asset_type}/{stable_key}.png",
        media_type="image/png",
        byte_size=64,
        sha256=sha256(
            f"{catalog_version}:{asset_type}:{stable_key}".encode("utf-8")
        ).hexdigest(),
        visibility="public",
        status=storage_status,
        created_at=datetime.now(timezone.utc),
        deleted_at=datetime.now(timezone.utc) if storage_deleted else None,
    )
    db.add(storage)
    db.flush()
    asset = LibraryAsset(
        catalog_version=catalog_version,
        stable_key=stable_key,
        asset_type=asset_type,
        storage_object_id=storage.id,
        description_cn="",
        description_en=" ".join(facets.values()),
        tags=list(facets.values()),
        taxonomy={},
        style=facets.get("style"),
        identity_group=facets.get("identity_group"),
        expression=facets.get("expression"),
        pose=facets.get("pose"),
        quality_score=quality,
        safety_status=safety_status,
        rights_metadata={"license": "test"},
        enabled=enabled,
    )
    db.add(asset)
    db.flush()
    create_library_asset_tag_links(
        db,
        asset=asset,
        specs=extract_library_facet_tags(
            taxonomy={},
            style=asset.style,
            identity_group=asset.identity_group,
            expression=asset.expression,
            pose=asset.pose,
            explicit_facet_tags=[
                {"category": category, "value": value}
                for category, value in facets.items()
            ],
        ),
    )
    return asset


def _tag(db, category: str, value: str) -> LibraryTag:
    return (
        db.query(LibraryTag)
        .filter(LibraryTag.category == category, LibraryTag.value == value)
        .one()
    )


def _search(db, **overrides):
    params = {
        "catalog_version": CATALOG,
        "matcher_version": MATCHER_VERSION,
        "query": None,
        "asset_type": "background",
        "identity_group": None,
        "limit": 20,
        "offset": 0,
    }
    params.update(overrides)
    return search_library_assets(db, **params)


def test_tag_search_uses_strict_and_and_deduplicates_ids(db, catalog):
    historical = _tag(db, "style", "historical")
    xianxia = _tag(db, "style", "xianxia")
    rain = _tag(db, "weather", "rain")

    items, total = _search(
        db,
        required_tag_ids=[historical.id, historical.id, rain.id],
    )
    assert total == 1
    assert [item["stable_key"] for item in items] == ["street-rain"]

    items, total = _search(
        db,
        required_tag_ids=[historical.id, xianxia.id],
    )
    assert items == []
    assert total == 0


def test_legacy_facets_match_tag_ids_and_unknown_values_are_empty(db, catalog):
    historical = _tag(db, "style", "historical")
    tagged_items, tagged_total = _search(db, required_tag_ids=[historical.id])
    legacy_items, legacy_total = _search(
        db,
        required_facets={"style": " HISTORICAL "},
    )

    assert legacy_total == tagged_total == 2
    assert [item["id"] for item in legacy_items] == [
        item["id"] for item in tagged_items
    ]
    assert _search(db, required_facets={"style": "not-published"}) == ([], 0)


@pytest.mark.parametrize(
    ("tag_id", "detail"),
    [
        ("not-a-uuid", "UUID"),
        ("00000000-0000-0000-0000-000000000001", "current catalog"),
    ],
)
def test_invalid_or_unknown_tag_ids_return_422(db, catalog, tag_id, detail):
    with pytest.raises(HTTPException) as exc_info:
        _search(db, required_tag_ids=[tag_id])
    assert exc_info.value.status_code == 422
    assert detail in str(exc_info.value.detail)


def test_cross_catalog_and_cross_type_tags_return_422(db, catalog):
    for tag in (
        _tag(db, "location", "moonbase"),
        _tag(db, "expression", "smirk"),
    ):
        with pytest.raises(HTTPException) as exc_info:
            _search(db, required_tag_ids=[tag.id])
        assert exc_info.value.status_code == 422


def test_fast_path_pages_in_sql_and_text_scoring_only_sees_sql_candidates(
    db, catalog, monkeypatch
):
    historical = _tag(db, "style", "historical")

    def fail_if_scored(*_args, **_kwargs):
        raise AssertionError("no-text fast path must not load embeddings")

    monkeypatch.setattr(
        "app.application.visual_asset_service._embedding_for_asset",
        fail_if_scored,
    )
    items, total = _search(
        db,
        required_tag_ids=[historical.id],
        limit=1,
        offset=1,
    )
    assert total == 2
    assert len(items) == 1

    seen: list[str] = []

    def track_candidates(_db, asset, _matcher_version):
        seen.append(asset.id)
        return [0.0] * 256

    monkeypatch.setattr(
        "app.application.visual_asset_service._embedding_for_asset",
        track_candidates,
    )
    _search(db, query="street", required_tag_ids=[historical.id])
    assert set(seen) == {catalog.street_rain.id, catalog.street_sun.id}


def test_preferred_style_remains_soft_while_public_style_tag_is_hard(db, catalog):
    items, total = _search(db, preferred_style="historical")
    assert total == 3
    assert {item["stable_key"] for item in items} == {
        "street-rain",
        "street-sun",
        "forest-rain",
    }
    xianxia = next(item for item in items if item["stable_key"] == "forest-rain")
    assert "style_mismatch" in xianxia["conflicts"]

    historical = _tag(db, "style", "historical")
    hard_items, hard_total = _search(db, required_tag_ids=[historical.id])
    assert hard_total == 2
    assert {item["stable_key"] for item in hard_items} == {
        "street-rain",
        "street-sun",
    }


def test_hard_tag_terms_remain_in_soft_scoring_context_without_query(
    db, catalog, monkeypatch
):
    historical = _tag(db, "style", "historical")
    expanded_inputs: list[str] = []

    def capture_expansion(value: str) -> str:
        expanded_inputs.append(value)
        return value

    monkeypatch.setattr(
        "app.application.visual_asset_service._expand_visual_query",
        capture_expansion,
    )
    items, total = _search(
        db,
        required_tag_ids=[historical.id],
        recent_library_asset_ids=[catalog.street_rain.id],
    )

    assert total == 2
    assert len(items) == 2
    assert expanded_inputs == ["historical"]


def test_facets_return_selected_zero_and_dynamic_option_counts(db, catalog):
    xianxia = _tag(db, "style", "xianxia")
    response = get_library_facets(
        db,
        catalog_version=CATALOG,
        asset_type="background",
        selected_tag_ids=[xianxia.id],
    )

    assert response["selected_tag_ids"] == [xianxia.id]
    assert response["total"] == 1
    groups = {
        group["category"]: {option["value"]: option for option in group["options"]}
        for group in response["facets"]
    }
    assert "mood" in groups
    assert groups["style"]["xianxia"] == {
        "id": xianxia.id,
        "value": "xianxia",
        "display_name": "xianxia",
        "count": 1,
        "selected": True,
    }
    assert groups["style"]["historical"]["count"] == 0
    assert groups["location"]["street"]["count"] == 0
    assert groups["weather"]["rain"]["count"] == 1

    items, total = _search(db, required_tag_ids=[xianxia.id])
    assert len(items) == total == response["total"]


def test_facet_options_are_counted_set_wise_without_n_plus_one(db, catalog):
    xianxia = _tag(db, "style", "xianxia")
    statements: list[str] = []

    def capture_selects(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith(("SELECT", "WITH")):
            statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", capture_selects)
    try:
        response = get_library_facets(
            db,
            catalog_version=CATALOG,
            asset_type="background",
            selected_tag_ids=[xianxia.id],
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", capture_selects)

    assert response["facets"]
    assert len(statements) == 3
    assert "facet_option_counts" in statements[-1]


def test_facets_apply_strict_and_even_within_one_category(db, catalog):
    historical = _tag(db, "style", "historical")
    xianxia = _tag(db, "style", "xianxia")
    response = get_library_facets(
        db,
        catalog_version=CATALOG,
        asset_type="background",
        selected_tag_ids=[historical.id, xianxia.id],
    )

    assert response["total"] == 0
    style_options = next(
        group["options"]
        for group in response["facets"]
        if group["category"] == "style"
    )
    selected = [option for option in style_options if option["selected"]]
    assert {option["id"] for option in selected} == {historical.id, xianxia.id}
    assert all(option["count"] == 0 for option in selected)


def test_parse_tag_ids_preserves_order_and_enforces_limit():
    first = "00000000-0000-0000-0000-000000000001"
    second = "00000000-0000-0000-0000-000000000002"
    assert parse_library_tag_ids(f" {first},{second},{first} ") == [first, second]

    too_many = ",".join(
        f"00000000-0000-0000-0000-{number:012d}" for number in range(51)
    )
    with pytest.raises(HTTPException) as exc_info:
        parse_library_tag_ids(too_many)
    assert exc_info.value.status_code == 422


def test_query_logs_only_structured_counts(db, catalog, caplog):
    historical = _tag(db, "style", "historical")
    caplog.set_level(
        logging.INFO,
        logger="app.application.visual_asset_service",
    )
    _search(
        db,
        query="private-user-query",
        required_tag_ids=[historical.id],
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "catalog_version=facet-v1" in log_text
    assert "selected_tag_count=1" in log_text
    assert "query_present=True" in log_text
    assert "private-user-query" not in log_text
    assert historical.id not in log_text


def test_sqlite_candidate_plan_uses_reverse_tag_index(db, catalog):
    historical = _tag(db, "style", "historical")
    plan = db.execute(
        text(
            "EXPLAIN QUERY PLAN "
            "SELECT library_asset_id FROM library_asset_tags "
            "WHERE tag_id IN (:tag_id) GROUP BY library_asset_id "
            "HAVING COUNT(DISTINCT tag_id) = 1"
        ),
        {"tag_id": historical.id},
    ).all()
    details = "\n".join(str(row[-1]) for row in plan)
    assert "ix_library_asset_tags_tag_asset" in details


def test_facet_route_is_static_and_legacy_filters_are_removed(db, catalog):
    application = FastAPI()
    application.state.settings = SimpleNamespace(
        visual_asset_catalog_version=CATALOG,
        visual_asset_matcher_version=MATCHER_VERSION,
    )
    application.include_router(router, prefix="/api")
    application.dependency_overrides[get_db] = lambda: db
    application.dependency_overrides[get_current_user] = lambda: catalog.owner

    with TestClient(application) as client:
        missing_type = client.get("/api/library-assets/facets")
        assert missing_type.status_code == 422
        response = client.get(
            "/api/library-assets/facets",
            params={"asset_type": "background"},
        )
        assert response.status_code == 200
        assert response.json()["asset_type"] == "background"
        assert client.get(
            "/api/library-assets/facets",
            params={"asset_type": "character"},
        ).status_code == 422
        # 1fe5ea7 起路由不再声明 legacy 过滤参数：未知 query 被静默忽略，
        # 检索一律走 facets + tag_ids。
        legacy = client.get(
            "/api/library-assets",
            params={"asset_type": "background", "style": "historical"},
        )
        assert legacy.status_code == 200
        assert legacy.json()["total"] == 3
        xianxia = _tag(db, "style", "xianxia")
        merged = client.get(
            "/api/library-assets",
            params={
                "asset_type": "background",
                "tag_ids": xianxia.id,
            },
        )
        assert merged.status_code == 200
        assert merged.json()["total"] == 1

    schema = application.openapi()
    assert "/api/library-assets/facets" in schema["paths"]
    facet_parameters = {
        item["name"]: item
        for item in schema["paths"]["/api/library-assets/facets"]["get"]["parameters"]
    }
    assert facet_parameters["asset_type"]["required"] is True
    search_parameters = {
        item["name"]: item
        for item in schema["paths"]["/api/library-assets"]["get"]["parameters"]
    }
    assert set(search_parameters) == {
        "query",
        "asset_type",
        "tag_ids",
        "limit",
        "offset",
    }
    legacy_parameters = {
        "style",
        "identity_group",
        "genre",
        "location",
        "time",
        "weather",
        "atmosphere",
        "camera",
        "age",
        "gender",
        "outfit",
        "expression",
        "pose",
    }
    assert not (set(search_parameters) & legacy_parameters)
