from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.story_bible_service import (
    append_auto_registered_characters,
    collect_unregistered_speaker_names,
    create_bible_revision,
    get_or_create_content_head,
)
from app.models import Project, User
from app.orm_base import Base


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'bible-writeback.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(User(email="t@example.com", password_hash="x", display_name="t"))
    db.flush()
    db.add(Project(id=82, owner_id=1, title="第八十二号项目", story_start="s", story_end="e"))
    db.commit()
    yield db
    db.close()


def _head_bible(db, characters):
    revision = create_bible_revision(
        db,
        project_id=82,
        content={"title": "t", "characters": characters},
        source={"kind": "test"},
        user_id=1,
    )
    db.commit()
    return revision


def test_collect_unregistered_speaker_names_filters_known_and_labels():
    script_ir = {
        "paragraphs": [
            {"unregistered_speaker": True, "speaker_name": "刘洋"},
            {"unregistered_speaker": True, "speaker_name": "刘洋"},  # 去重
            {"unregistered_speaker": True, "speaker_name": "旁白"},  # 非角色称呼
            {"unregistered_speaker": True, "speaker_name": "？？？"},
            {"unregistered_speaker": False, "speaker_name": "林一鸣"},
            {"unregistered_speaker": True, "speaker_name": "林一鸣"},  # 已在 bible
        ]
    }
    names = collect_unregistered_speaker_names(
        script_ir, known_characters=[{"name": "林一鸣"}, {"name": "苏婉清"}]
    )
    assert names == ["刘洋"]


def test_append_auto_registered_characters_appends_and_activates(session):
    _head_bible(session, [{"name": "林一鸣", "role": "主角"}])
    revision = append_auto_registered_characters(
        session,
        project_id=82,
        names=["林一鸣", "刘洋", "旁白"],
        source_note="chapter_script:test",
        task_id="task-1",
    )
    assert revision is not None
    names = [c["name"] for c in revision.content_json["characters"]]
    assert names == ["林一鸣", "刘洋"]
    appended = revision.content_json["characters"][1]
    assert appended["auto_registered"] is True
    assert appended["visual_profile_source"] == "pending"
    assert get_or_create_content_head(session, 82).current_bible_revision_id == revision.id


def test_append_auto_registered_characters_noop_when_all_known(session):
    _head_bible(session, [{"name": "林一鸣"}])
    assert (
        append_auto_registered_characters(
            session, project_id=82, names=["林一鸣"], source_note="s"
        )
        is None
    )
