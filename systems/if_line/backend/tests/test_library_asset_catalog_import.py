from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.application.storage_service import LocalStorageBackend
from app.database import Base
from app.models import User
from app.models_v2 import LibraryAsset, LibraryAssetEmbedding, LibraryAssetTag, LibraryTag
from app.scripts import import_visual_asset_catalog as importer


@pytest.fixture()
def import_environment(tmp_path: Path, monkeypatch):
    database = tmp_path / "catalog.sqlite3"
    engine = create_engine(
        f"sqlite:///{database.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        owner = User(
            email="catalog-import@example.com",
            password_hash="hash",
            display_name="catalog importer",
            is_active=True,
            quota_total=100,
            quota_daily=100,
        )
        db.add(owner)
        db.commit()
        owner_id = owner.id

    project_root = tmp_path / "project"
    project_root.mkdir()
    source = project_root / "catalog.png"
    Image.new("RGB", (96, 80), (20, 40, 80)).save(source, format="PNG")
    monkeypatch.setattr(importer, "PROJECT_ROOT", project_root)
    monkeypatch.setattr(importer, "SessionLocal", factory)
    monkeypatch.setattr(
        importer,
        "get_local_storage_backend",
        lambda: LocalStorageBackend(tmp_path / "objects"),
    )
    try:
        yield {
            "factory": factory,
            "owner_id": owner_id,
            "project_root": project_root,
            "source": source,
            "index": project_root / "index.json",
        }
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def _asset(key: str, *, facet_tags: list[dict[str, str]]) -> dict:
    return {
        "key": key,
        "asset_type": "background",
        "project_relative_path": "catalog.png",
        "facet_tags": facet_tags,
    }


def _write_index(path: Path, *, version: str, assets: list[dict]) -> None:
    path.write_text(
        json.dumps({"library_version": version, "assets": assets}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_import_dry_run_reports_tags_and_links_without_writes(import_environment):
    env = import_environment
    _write_index(
        env["index"],
        version="import-v1",
        assets=[
            _asset(
                "bg_historical_street",
                facet_tags=[
                    {"category": "genre", "value": "mystery", "display_name": "悬疑"},
                    {"category": "location", "value": "street", "display_name": "街道"},
                ],
            ),
            _asset(
                "bg_historical_forest",
                facet_tags=[
                    {"category": "location", "value": "street", "display_name": "街道"},
                ],
            ),
        ],
    )

    result = importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=False,
    )

    assert result == {
        "catalog_version": "import-v1",
        "dry_run": True,
        "seen": 2,
        "created": 2,
        "reused": 0,
        "skipped": 0,
        "tags_created": 4,
        "links_created": 6,
    }
    with env["factory"]() as db:
        assert db.query(LibraryAsset).count() == 0
        assert db.query(LibraryTag).count() == 0
        assert db.query(LibraryAssetTag).count() == 0


def test_import_is_transactional_idempotent_and_rejects_frozen_tag_drift(
    import_environment,
):
    env = import_environment
    assets = [
        _asset(
            "bg_historical_street",
            facet_tags=[
                {"category": "genre", "value": "mystery", "display_name": "悬疑"},
                {"category": "location", "value": "street", "display_name": "街道"},
            ],
        )
    ]
    _write_index(env["index"], version="import-v1", assets=assets)

    first = importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=True,
    )
    assert first["created"] == 1
    assert first["tags_created"] == 3
    assert first["links_created"] == 3
    with env["factory"]() as db:
        assert db.query(LibraryAsset).count() == 1
        assert db.query(LibraryAssetEmbedding).count() == 1
        assert db.query(LibraryTag).count() == 3
        assert db.query(LibraryAssetTag).count() == 3

    second = importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=True,
    )
    assert second["reused"] == 1
    assert second["created"] == 0
    assert second["tags_created"] == 0
    assert second["links_created"] == 0

    assets[0]["facet_tags"][0] = {
        "category": "genre",
        "value": "fantasy",
        "display_name": "奇幻",
    }
    _write_index(env["index"], version="import-v1", assets=assets)
    with pytest.raises(RuntimeError, match="publish a new version"):
        importer.import_catalog(
            index_path=env["index"],
            owner_id=env["owner_id"],
            catalog_version=None,
            apply=True,
        )


def test_dry_run_rejects_display_name_conflicts_within_one_batch(import_environment):
    env = import_environment
    _write_index(
        env["index"],
        version="conflict-v1",
        assets=[
            _asset(
                "bg_modern_one",
                facet_tags=[
                    {"category": "location", "value": "street", "display_name": "街道"},
                ],
            ),
            _asset(
                "bg_modern_two",
                facet_tags=[
                    {"category": "location", "value": "STREET", "display_name": "道路"},
                ],
            ),
        ],
    )

    with pytest.raises(ValueError, match="within dry-run catalog"):
        importer.import_catalog(
            index_path=env["index"],
            owner_id=env["owner_id"],
            catalog_version=None,
            apply=False,
        )


def test_duplicate_manifest_entries_are_idempotent_in_dry_run_and_apply(
    import_environment,
):
    env = import_environment
    duplicate = _asset(
        "bg_historical_duplicate",
        facet_tags=[
            {"category": "location", "value": "street", "display_name": "街道"},
        ],
    )
    _write_index(
        env["index"],
        version="duplicate-v1",
        assets=[duplicate, dict(duplicate)],
    )

    dry_run = importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=False,
    )
    assert dry_run["created"] == 1
    assert dry_run["reused"] == 1
    assert dry_run["tags_created"] == 3
    assert dry_run["links_created"] == 3

    applied = importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=True,
    )
    assert applied["created"] == 1
    assert applied["reused"] == 1
    assert applied["tags_created"] == 3
    assert applied["links_created"] == 3
    with env["factory"]() as db:
        assert db.query(LibraryAsset).count() == 1
        assert db.query(LibraryTag).count() == 3
        assert db.query(LibraryAssetTag).count() == 3


def test_new_catalog_rejects_global_display_name_drift(import_environment):
    env = import_environment
    _write_index(
        env["index"],
        version="labels-v1",
        assets=[
            _asset(
                "bg_modern_label_one",
                facet_tags=[
                    {"category": "location", "value": "street", "display_name": "街道"},
                ],
            )
        ],
    )
    importer.import_catalog(
        index_path=env["index"],
        owner_id=env["owner_id"],
        catalog_version=None,
        apply=True,
    )

    _write_index(
        env["index"],
        version="labels-v2",
        assets=[
            _asset(
                "bg_modern_label_two",
                facet_tags=[
                    {"category": "location", "value": "STREET", "display_name": "道路"},
                ],
            )
        ],
    )
    with pytest.raises(ValueError, match="existing facet tag"):
        importer.import_catalog(
            index_path=env["index"],
            owner_id=env["owner_id"],
            catalog_version=None,
            apply=False,
        )
