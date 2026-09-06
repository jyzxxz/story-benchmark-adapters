"""Bible 生效时角色名单回写 Project.characters（列表页「核心人物」数据源）。

覆盖两条生效门口：
- create_bible_revision(activate=True)（草稿物化 / agent 更新）
- activate_bible_revision_head（采用建议 / 切换版本，router 与 agent 共用）
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.story_bible_service import (
    activate_bible_revision_head,
    create_bible_revision,
    get_or_create_content_head,
)
from app.models import Project, User
from app.models_v2 import Base


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(
        email="bible-sync@example.com",
        password_hash="hash",
        display_name="Bible Sync",
        quota_total=100,
        quota_daily=100,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="角色回写验证",
        story_start="Start",
        story_end="End",
        characters=[],
    )
    db.add(project)
    db.commit()
    return db, user, project


def _roster(content):
    return [(entry["name"], entry["role"]) for entry in content["characters"]]


def test_materialize_path_writes_roster_on_activation():
    db, user, project = _db()
    content = {
        "worldview": "深海",
        "characters": [
            {"name": "林潮生", "role": "主角"},
            {"name": "江珊", "role": "重要配角"},
        ],
    }
    create_bible_revision(
        db,
        project_id=project.id,
        content=content,
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    db.commit()
    refreshed = db.query(Project).filter(Project.id == project.id).one()
    assert _roster({"characters": refreshed.characters}) == [
        ("林潮生", "主角"),
        ("江珊", "重要配角"),
    ]


def test_first_revision_without_explicit_activation_auto_adopts_and_writes():
    # head 为空时首个修订自动采用（防死锁规则），characters 随之回写。
    db, user, project = _db()
    content = {"worldview": "深海", "characters": [{"name": "林潮生", "role": "主角"}]}
    create_bible_revision(
        db,
        project_id=project.id,
        content=content,
        source={"origin": "test"},
        user_id=user.id,
        activate=False,
    )
    db.commit()
    refreshed = db.query(Project).filter(Project.id == project.id).one()
    assert _roster({"characters": refreshed.characters}) == [("林潮生", "主角")]


def test_subsequent_generation_without_activation_keeps_head_roster():
    # head 已有值时 activate=False 维持审阅制：不推 head、不回写。
    db, user, project = _db()
    r1 = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "深海", "characters": [
            {"name": "林潮生", "role": "主角"},
            {"name": "江珊", "role": "重要配角"},
        ]},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "深海", "characters": [
            {"name": "林潮生", "role": "主角"},
            {"name": "江珊", "role": "重要配角"},
            {"name": "母亲", "role": "配角"},
        ]},
        source={"origin": "test"},
        user_id=user.id,
        activate=False,
        parent_revision_id=r1.id,
    )
    db.commit()
    refreshed = db.query(Project).filter(Project.id == project.id).one()
    names = {entry["name"] for entry in refreshed.characters}
    assert names == {"林潮生", "江珊"}


def test_activate_head_path_writes_and_reverts_roster():
    db, user, project = _db()
    r1 = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "深海", "characters": [
            {"name": "林潮生", "role": "主角"},
            {"name": "江珊", "role": "重要配角"},
        ]},
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    r2 = create_bible_revision(
        db,
        project_id=project.id,
        content={"worldview": "深海", "characters": [
            {"name": "林潮生", "role": "主角"},
            {"name": "江珊", "role": "重要配角"},
            {"name": "母亲", "role": "配角"},
        ]},
        source={"origin": "test"},
        user_id=user.id,
        activate=False,
        parent_revision_id=r1.id,
    )
    db.commit()

    head = get_or_create_content_head(db, project.id)
    activate_bible_revision_head(
        db,
        project_id=project.id,
        revision_id=r2.id,
        expected_lock_version=head.lock_version,
    )
    db.commit()
    names = {entry["name"] for entry in db.query(Project).one().characters}
    assert names == {"林潮生", "江珊", "母亲"}

    head = get_or_create_content_head(db, project.id)
    activate_bible_revision_head(
        db,
        project_id=project.id,
        revision_id=r1.id,
        expected_lock_version=head.lock_version,
    )
    db.commit()
    names = {entry["name"] for entry in db.query(Project).one().characters}
    assert names == {"林潮生", "江珊"}


def test_same_roster_activation_skips_rewrite():
    db, user, project = _db()
    content = {"worldview": "深海", "characters": [{"name": "林潮生", "role": "主角"}]}
    create_bible_revision(
        db,
        project_id=project.id,
        content=content,
        source={"origin": "test"},
        user_id=user.id,
        activate=True,
    )
    db.commit()
    project = db.query(Project).one()
    project.characters = [{"name": "林潮生", "role": "另一个定位"}]  # 姓名集合相同
    original = project.characters
    db.commit()

    head = get_or_create_content_head(db, project.id)
    revision_id = head.current_bible_revision_id
    activate_bible_revision_head(
        db,
        project_id=project.id,
        revision_id=revision_id,
        expected_lock_version=head.lock_version,
    )
    db.commit()
    refreshed = db.query(Project).one()
    # 姓名集合未变 → 不覆盖用户自定义的 role 文案
    assert refreshed.characters is original or refreshed.characters == original
