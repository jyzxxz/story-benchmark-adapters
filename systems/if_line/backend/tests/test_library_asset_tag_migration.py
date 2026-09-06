from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import User
from app.models_v2 import LibraryAsset, StorageObject
from test_runtime_foundation import _run_alembic


def _engine(database: Path):
    engine = create_engine(f"sqlite:///{database.as_posix()}")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _assert_ok(result) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_library_tag_migration_backfills_and_round_trips(tmp_path: Path):
    pytest.importorskip("alembic")
    database = tmp_path / "library-tags.sqlite3"
    _assert_ok(
        _run_alembic(database, "upgrade", "0046_detach_legacy_ghost_chapters")
    )

    engine = _engine(database)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        owner = User(
            email="migration-facets@example.com",
            password_hash="hash",
            display_name="migration facets",
            is_active=True,
            quota_total=100,
            quota_daily=100,
        )
        db.add(owner)
        db.flush()
        storage = StorageObject(
            owner_id=owner.id,
            project_id=None,
            storage_backend="local",
            storage_key="migration/catalog.png",
            media_type="image/png",
            byte_size=64,
            sha256="1" * 64,
            visibility="public",
            status="active",
        )
        db.add(storage)
        db.flush()
        asset = LibraryAsset(
            id="10000000-0000-0000-0000-000000000001",
            catalog_version="migration-v1",
            stable_key="migration-one",
            asset_type="portrait",
            storage_object_id=storage.id,
            description_cn="原始中文",
            description_en="original english",
            tags=["ordinary-keyword"],
            taxonomy={
                "genre": ["Historical", "Fantasy"],
                "location": " Night   Street ",
                "tokens": ["never", "facet"],
            },
            identity_group="Hero One",
            expression="Happy",
            pose="Standing",
            style="XianXia",
            quality_score=0.9,
            safety_status="approved",
            rights_metadata={"license": "test"},
            enabled=True,
        )
        db.add(asset)
        storage_v2 = StorageObject(
            owner_id=owner.id,
            project_id=None,
            storage_backend="local",
            storage_key="migration/catalog-v2.png",
            media_type="image/png",
            byte_size=64,
            sha256="2" * 64,
            visibility="public",
            status="active",
        )
        db.add(storage_v2)
        db.flush()
        db.add(
            LibraryAsset(
                id="20000000-0000-0000-0000-000000000002",
                catalog_version="migration-v2",
                stable_key="migration-two",
                asset_type="background",
                storage_object_id=storage_v2.id,
                description_cn="",
                description_en="second catalog",
                tags=["second-keyword"],
                taxonomy={"weather": "Rain"},
                style="XianXia",
                quality_score=0.8,
                safety_status="approved",
                rights_metadata={"license": "test"},
                enabled=True,
            )
        )
        db.commit()

    with engine.connect() as connection:
        original = connection.execute(
            text(
                "SELECT taxonomy, tags, style, identity_group, expression, pose "
                "FROM library_assets WHERE id = :asset_id"
            ),
            {"asset_id": "10000000-0000-0000-0000-000000000001"},
        ).one()
    engine.dispose()

    _assert_ok(_run_alembic(database, "upgrade", "0047_library_asset_tags"))
    engine = _engine(database)
    inspector = inspect(engine)
    assert {"library_tags", "library_asset_tags"}.issubset(inspector.get_table_names())
    assert inspector.get_pk_constraint("library_asset_tags")["constrained_columns"] == [
        "library_asset_id",
        "tag_id",
    ]
    assert any(
        constraint["column_names"] == ["category", "value"]
        for constraint in inspector.get_unique_constraints("library_tags")
    )
    assert any(
        index["name"] == "ix_library_asset_tags_tag_asset"
        and index["column_names"] == ["tag_id", "library_asset_id"]
        for index in inspector.get_indexes("library_asset_tags")
    )
    foreign_keys = inspector.get_foreign_keys("library_asset_tags")
    assert {tuple(item["constrained_columns"]) for item in foreign_keys} == {
        ("library_asset_id",),
        ("tag_id",),
    }
    assert all(item.get("options", {}).get("ondelete") == "CASCADE" for item in foreign_keys)

    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                "SELECT taxonomy, tags, style, identity_group, expression, pose "
                "FROM library_assets WHERE id = :asset_id"
            ),
            {"asset_id": "10000000-0000-0000-0000-000000000001"},
        ).one()
        assert migrated == original
        tags = {
            (row.category, row.value, row.display_name)
            for row in connection.execute(
                text("SELECT category, value, display_name FROM library_tags")
            )
        }
        assert tags == {
            ("expression", "happy", "Happy"),
            ("genre", "fantasy", "Fantasy"),
            ("genre", "historical", "Historical"),
            ("identity_group", "hero one", "Hero One"),
            ("location", "night street", "Night Street"),
            ("pose", "standing", "Standing"),
            ("style", "xianxia", "XianXia"),
            ("weather", "rain", "Rain"),
        }
        assert connection.execute(
            text("SELECT COUNT(*) FROM library_asset_tags")
        ).scalar_one() == 9

    with engine.begin() as connection:
        with pytest.raises(IntegrityError):
            connection.execute(
                text(
                    "INSERT INTO library_tags(id, category, value, display_name) "
                    "VALUES ('duplicate', 'style', 'xianxia', 'duplicate')"
                )
            )
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("DELETE FROM library_assets WHERE id = :asset_id"),
            {"asset_id": "10000000-0000-0000-0000-000000000001"},
        )
        assert connection.execute(
            text(
                "SELECT COUNT(*) FROM library_asset_tags "
                "WHERE library_asset_id = :asset_id"
            ),
            {"asset_id": "10000000-0000-0000-0000-000000000001"},
        ).scalar_one() == 0
        transaction.rollback()
    engine.dispose()

    _assert_ok(
        _run_alembic(database, "downgrade", "0046_detach_legacy_ghost_chapters")
    )
    engine = _engine(database)
    assert "library_tags" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(
            text(
                "SELECT taxonomy, tags FROM library_assets "
                "WHERE id = :asset_id"
            ),
            {"asset_id": "10000000-0000-0000-0000-000000000001"},
        ).one() == original[:2]
    engine.dispose()

    _assert_ok(_run_alembic(database, "upgrade", "0047_library_asset_tags"))
    engine = _engine(database)
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM library_asset_tags")
        ).scalar_one() == 9
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        assert revision == "0047_library_asset_tags"
    engine.dispose()
