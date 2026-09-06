"""Style-lock freezing into script resource render specs.

Covers the fix for style drift on the worker render chain: the project's
locked visual bible must be resolved at plan time and frozen into every
role's render_spec.extra_parameters so the renderers can pass
visual_style_prompt / style_fingerprint to the image services.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.application.chapter_script_service import (
    _project_visual_style_fields,
    _script_resource_render_spec,
)
from app.database import Base
from app.models import Project, User
from app.models_v2 import StoryBibleRevision


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_project_with_bible(db, worldview: str = "现代都市校园"):
    user = User(
        email="style-lock@example.com",
        password_hash="hash",
        display_name="owner",
        is_active=True,
        quota_total=100,
        quota_daily=100,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="style-lock",
        story_start="开始",
        story_end="结束",
        visibility="private",
        is_draft=True,
    )
    db.add(project)
    db.flush()
    revision = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash="0" * 64,
        content_hash="1" * 64,
        content_json={"worldview": worldview, "characters": []},
        status="complete",
    )
    db.add(revision)
    db.commit()
    return project, revision


def test_style_fields_resolve_from_latest_bible_revision(db):
    project, revision = _seed_project_with_bible(db)
    fields = _project_visual_style_fields(db, project.id)
    assert set(fields) == {
        "visual_style_prompt",
        "style_fingerprint",
        "visual_bible_version",
    }
    assert "[PROJECT STYLE SIGNATURE]" in fields["visual_style_prompt"]
    # revision 不可变:不回写锁,但重复解析结果确定性一致(依赖分类器 temp 0.2 + 进程缓存)
    again = _project_visual_style_fields(db, project.id)
    assert again["style_fingerprint"] == fields["style_fingerprint"]


def test_style_fields_empty_without_bible(db):
    user = User(
        email="no-bible@example.com",
        password_hash="hash",
        display_name="owner",
        is_active=True,
        quota_total=100,
        quota_daily=100,
        quota_used_total=0,
        quota_used_daily=0,
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="no-bible",
        story_start="开始",
        story_end="结束",
        visibility="private",
        is_draft=True,
    )
    db.add(project)
    db.commit()
    assert _project_visual_style_fields(db, project.id) == {}


def _revision_stub():
    return SimpleNamespace(
        chapter_index=1,
        script_json={
            "paragraphs": [
                {
                    "paragraph_id": "paragraph-0001",
                    "speaker_name": "阿宁",
                    "speaker_character_id": "character-an",
                    "emotion": "tense",
                    "scene_id": "scene-001",
                }
            ]
        },
    )


def test_render_spec_freezes_style_fields_for_all_roles(db):
    style_fields = {
        "visual_style_prompt": "[PROJECT STYLE SIGNATURE] shared-style-prompt-v2 / fingerprint=fp",
        "style_fingerprint": "fp",
        "visual_bible_version": "project-visual-bible-v5",
    }
    cases = {
        "portrait": {
            "slot_key": "portrait:character-an:tense",
            "role": "portrait",
            "scene_id": "scene-001",
            "paragraph_id": "paragraph-0001",
            "character_id": "character-an",
            "required": True,
            "target_name": "阿宁",
            "prompt": "角色立绘，阿宁，tense，透明背景，无文字",
            "emotion": "tense",
            "canonical_identity": {"franchise_name": "Naruto"},
        },
        "background": {
            "slot_key": "background:scene-001",
            "role": "background",
            "scene_id": "scene-001",
            "paragraph_id": "paragraph-0001",
            "character_id": None,
            "required": True,
            "target_name": "雨夜车站",
            "prompt": "视觉小说背景，雨夜车站，无人，无文字",
        },
        "keyframe": {
            "slot_key": "keyframe:paragraph-0001",
            "role": "keyframe",
            "scene_id": "scene-001",
            "paragraph_id": "paragraph-0001",
            "character_id": "character-an",
            "required": True,
            "target_name": "关键帧 paragraph-0001",
            "prompt": "视觉小说剧情关键帧，灯光熄灭，无文字",
        },
    }
    for role, spec in cases.items():
        render_spec = _script_resource_render_spec(
            db, _revision_stub(), spec, style_fields
        )
        extra = render_spec["extra_parameters"]
        assert extra["visual_style_prompt"] == style_fields["visual_style_prompt"], role
        assert extra["style_fingerprint"] == "fp", role
        assert extra["visual_bible_version"] == "project-visual-bible-v5", role
    # portrait 原有字段不丢
    portrait_extra = _script_resource_render_spec(
        db, _revision_stub(), cases["portrait"], style_fields
    )["extra_parameters"]
    assert portrait_extra["canonical_identity"] == {"franchise_name": "Naruto"}
    # 不传 style_fields 时行为与旧版一致
    legacy_extra = _script_resource_render_spec(
        db, _revision_stub(), cases["portrait"]
    )["extra_parameters"]
    assert "visual_style_prompt" not in legacy_extra
    assert legacy_extra["canonical_identity"] == {"franchise_name": "Naruto"}


def test_portrait_task_renderer_passes_style_fields(monkeypatch):
    from app.workers import asset_tasks

    captured = {}

    async def fake_generate_portrait(**kwargs):
        captured.update(kwargs)
        return {"success": False}

    monkeypatch.setattr(
        asset_tasks.image_generation_service,
        "generate_portrait",
        fake_generate_portrait,
    )
    with pytest.raises(RuntimeError, match="image provider did not return an image"):
        asset_tasks.PortraitTaskRenderer().render(
            {
                "asset_id": 210,
                "asset_type": "portrait",
                "cache_material": {
                    "normalized_asset_spec": {
                        "target_name": "周衡",
                        "taxonomy": {"character_id": "character-zhou"},
                    }
                },
                "render_spec": {
                    "prompt": "角色立绘，周衡，透明背景",
                    "extra_parameters": {
                        "visual_style_prompt": "[PROJECT STYLE SIGNATURE] fp",
                        "style_fingerprint": "fp",
                    },
                },
            }
        )
    assert captured["visual_style_prompt"] == "[PROJECT STYLE SIGNATURE] fp"
    assert captured["style_fingerprint"] == "fp"


def test_apply_visual_style_lock_prefixes_portrait_prompt():
    from app.services.image_generation_service import image_generation_service

    style = "[PROJECT STYLE SIGNATURE] v / fingerprint=fp\n[SHARED MEDIUM] anime cel"
    locked = image_generation_service._apply_visual_style_lock("A calm librarian", style)
    assert locked.startswith(style)
    assert "A calm librarian" in locked
    # 已含风格块时不重复拼接
    assert image_generation_service._apply_visual_style_lock(locked, style) == locked
    # 无风格时原样返回
    assert image_generation_service._apply_visual_style_lock("x", "") == "x"
