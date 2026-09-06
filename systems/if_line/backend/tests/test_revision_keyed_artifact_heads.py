from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.models import Project, User
from app.models_v2 import (
    ChapterRevision,
    ChapterScriptHead,
    ChapterScriptRevision,
    OutlineRevision,
    StoryBibleRevision,
    VNGraphHead,
    VNGraphRevision,
)
from app.orm_base import Base


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(email="artifact-heads@example.com", password_hash="hash", display_name="Heads")
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Revision heads",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    bible = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash="a" * 64,
        content_hash="b" * 64,
        content_json={},
    )
    db.add(bible)
    db.flush()
    outline = OutlineRevision(
        project_id=project.id,
        bible_revision_id=bible.id,
        revision_no=1,
        source_hash="c" * 64,
        content_hash="d" * 64,
    )
    db.add(outline)
    db.flush()

    chapters = []
    scripts = []
    graphs = []
    for index in range(2):
        chapter = ChapterRevision(
            project_id=project.id,
            chapter_index=1,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            revision_no=index + 1,
            source_hash=str(index + 1) * 64,
            context_manifest={"branch": index},
            context_hash=str(index + 3) * 64,
            content_hash=str(index + 5) * 64,
            content=f"Branch {index}",
            status="complete",
        )
        db.add(chapter)
        db.flush()
        script = ChapterScriptRevision(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapter.id,
            bible_revision_id=bible.id,
            outline_revision_id=outline.id,
            revision_no=1,
            source_hash=str(index + 7) * 64,
            script_hash=("9" if index == 0 else "a") * 64,
            script_json={"branch": index},
            coverage_json={"coverage_ratio": 1.0},
            schema_version="test-v1",
            generator_version="test-v1",
            status="complete",
        )
        db.add(script)
        db.flush()
        graph = VNGraphRevision(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapter.id,
            script_revision_id=script.id,
            revision_no=1,
            source_manifest_hash=("b" if index == 0 else "c") * 64,
            graph_hash=("d" if index == 0 else "e") * 64,
            graph_json={"branch": index},
            schema_version="test-v1",
            compiler_version="test-v1",
            tachi_policy_version="test-v1",
            status="complete",
        )
        db.add(graph)
        db.flush()
        chapters.append(chapter)
        scripts.append(script)
        graphs.append(graph)

    db.commit()
    try:
        yield db, project, chapters, scripts, graphs
    finally:
        db.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_exact_revision_heads_can_share_legacy_chapter_index(seeded):
    db, project, chapters, scripts, graphs = seeded
    db.add_all(
        [
            ChapterScriptHead(
                project_id=project.id,
                chapter_index=1,
                chapter_revision_id=chapter.id,
                current_revision_id=script.id,
            )
            for chapter, script in zip(chapters, scripts, strict=True)
        ]
        + [
            VNGraphHead(
                project_id=project.id,
                chapter_index=1,
                script_revision_id=script.id,
                current_revision_id=graph.id,
            )
            for script, graph in zip(scripts, graphs, strict=True)
        ]
    )
    db.commit()

    script_heads = db.query(ChapterScriptHead).all()
    graph_heads = db.query(VNGraphHead).all()
    assert len(script_heads) == 2
    assert len(graph_heads) == 2
    assert len({head.id for head in script_heads + graph_heads}) == 4
    assert {head.chapter_revision_id for head in script_heads} == {
        chapter.id for chapter in chapters
    }
    assert {head.script_revision_id for head in graph_heads} == {
        script.id for script in scripts
    }
    assert {script.revision_no for script in scripts} == {1}
    assert {graph.revision_no for graph in graphs} == {1}


@pytest.mark.parametrize("head_kind", ["script", "graph"])
def test_exact_head_source_is_unique(seeded, head_kind):
    db, project, chapters, scripts, graphs = seeded
    if head_kind == "script":
        source = chapters[0].id
        first = ChapterScriptHead(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=source,
            current_revision_id=scripts[0].id,
        )
        duplicate = ChapterScriptHead(
            project_id=project.id,
            chapter_index=99,
            chapter_revision_id=source,
            current_revision_id=scripts[0].id,
        )
    else:
        source = scripts[0].id
        first = VNGraphHead(
            project_id=project.id,
            chapter_index=1,
            script_revision_id=source,
            current_revision_id=graphs[0].id,
        )
        duplicate = VNGraphHead(
            project_id=project.id,
            chapter_index=99,
            script_revision_id=source,
            current_revision_id=graphs[0].id,
        )

    db.add(first)
    db.commit()
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("head_kind", ["script", "graph"])
def test_head_lock_version_must_be_positive(seeded, head_kind):
    db, project, chapters, scripts, graphs = seeded
    if head_kind == "script":
        head = ChapterScriptHead(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapters[0].id,
            current_revision_id=scripts[0].id,
            lock_version=0,
        )
    else:
        head = VNGraphHead(
            project_id=project.id,
            chapter_index=1,
            script_revision_id=scripts[0].id,
            current_revision_id=graphs[0].id,
            lock_version=0,
        )

    db.add(head)
    with pytest.raises(IntegrityError):
        db.commit()


@pytest.mark.parametrize("head_kind", ["script", "graph"])
def test_head_rejects_revision_from_another_source_family(seeded, head_kind):
    db, project, chapters, scripts, graphs = seeded
    if head_kind == "script":
        head = ChapterScriptHead(
            project_id=project.id,
            chapter_index=1,
            chapter_revision_id=chapters[0].id,
            current_revision_id=scripts[1].id,
        )
        message = "ChapterRevision"
    else:
        head = VNGraphHead(
            project_id=project.id,
            chapter_index=1,
            script_revision_id=scripts[0].id,
            current_revision_id=graphs[1].id,
        )
        message = "ScriptRevision"
    db.add(head)

    with pytest.raises(ValueError, match=message):
        db.flush()
