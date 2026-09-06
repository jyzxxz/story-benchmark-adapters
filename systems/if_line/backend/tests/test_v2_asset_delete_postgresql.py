from __future__ import annotations

import uuid

import pytest
from sqlalchemy.orm import Session

from app.application.asset_service import delete_project_asset
from app.database import engine
from app.models import Asset, Project, User
from app.models_v2 import AssetBinding, AssetVersion


def test_postgresql_asset_delete_archives_without_dropping_version_bindings():
    if engine.dialect.name != "postgresql":
        pytest.skip("requires PostgreSQL")

    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, expire_on_commit=False)
    suffix = uuid.uuid4().hex
    try:
        user = User(
            email=f"asset-delete-{suffix}@example.com",
            password_hash="test",
            display_name="asset delete test",
        )
        db.add(user)
        db.flush()
        project = Project(
            owner_id=user.id,
            title=f"asset delete {suffix}",
            story_start="start",
            story_end="end",
        )
        db.add(project)
        db.flush()
        bound = Asset(
            project_id=project.id,
            chapter_index=1,
            asset_type="background",
            target_name="bound",
            status="completed",
        )
        unbound = Asset(
            project_id=project.id,
            chapter_index=1,
            asset_type="background",
            target_name="unbound",
            status="completed",
        )
        db.add_all([bound, unbound])
        db.flush()
        version = AssetVersion(
            asset_id=bound.id,
            source_revision_id=suffix,
            version_no=1,
            cache_key=f"cache-{suffix}",
            prompt_hash=f"prompt-{suffix}",
            prompt_version="test-v1",
            safety_status="passed",
        )
        unbound_version = AssetVersion(
            asset_id=unbound.id,
            source_revision_id=suffix,
            version_no=1,
            cache_key=f"unbound-cache-{suffix}",
            prompt_hash=f"unbound-prompt-{suffix}",
            prompt_version="test-v1",
            safety_status="passed",
        )
        db.add_all([version, unbound_version])
        db.flush()
        db.add(
            AssetBinding(
                project_id=project.id,
                source_kind="chapter_revision",
                source_id=suffix,
                role="background",
                asset_version_id=version.id,
            )
        )
        db.flush()

        delete_project_asset(db, project.id, bound.id)
        delete_project_asset(db, project.id, unbound.id)
        db.flush()
        assert db.query(Asset).filter(Asset.id == bound.id).one().archived_at is not None
        assert db.query(Asset).filter(Asset.id == unbound.id).one().archived_at is not None
        assert db.query(AssetVersion).filter(AssetVersion.id == version.id).one()
        assert db.query(AssetVersion).filter(AssetVersion.id == unbound_version.id).one()
        assert db.query(AssetBinding).filter(AssetBinding.asset_version_id == version.id).one()
    finally:
        db.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()
