from __future__ import annotations

from copy import deepcopy

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from app.application.hashing import content_hash
from app.application.story_context_resolver import (
    StoryContextResolutionError,
    StoryContextResolver,
)
from app.models import Project, User
from app.models_v2 import (
    BranchCandidate,
    ChapterRevision,
    ChapterSlot,
    OutlineChapter,
    OutlineRevision,
    ProjectContentHead,
    StateSnapshot,
    StoryBibleRevision,
    StoryNode,
    StoryPath,
    StoryPathChapter,
    StoryPathOutlineHead,
)
from app.orm_base import Base


def _chapter_revision(
    db,
    *,
    project,
    path,
    path_chapter,
    bible,
    outline,
    revision_no,
    content,
):
    context = {
        "path": path.id,
        "path_chapter": path_chapter.id,
        "revision_no": revision_no,
    }
    revision = ChapterRevision(
        project_id=project.id,
        chapter_slot_id=path_chapter.chapter_slot_id,
        created_for_story_path_id=path.id,
        chapter_index=path_chapter.display_index,
        bible_revision_id=bible.id,
        outline_revision_id=outline.id,
        revision_no=revision_no,
        source_hash=content_hash(context),
        context_manifest=context,
        context_hash=content_hash(context),
        content_hash=content_hash(content),
        content=content,
        status="complete",
    )
    db.add(revision)
    db.flush()
    return revision


def _outline_for_target(
    db,
    *,
    project,
    path,
    bible,
    target,
    revision_no=1,
    title="Target",
):
    source = {
        "path": path.id,
        "bible": bible.id,
        "revision_no": revision_no,
    }
    outline = OutlineRevision(
        project_id=project.id,
        story_path_id=path.id,
        bible_revision_id=bible.id,
        revision_no=revision_no,
        source_hash=content_hash(source),
        content_hash=content_hash({**source, "title": title}),
        status="approved",
    )
    db.add(outline)
    db.flush()
    chapter_payload = {
        "story_path_chapter_id": target.id,
        "display_index": target.display_index,
        "title": title,
        "summary": f"Summary for {title}",
        "conflict": None,
        "characters": ["A"],
        "scene": None,
        "emotion": None,
        "visual_keywords": ["station"],
    }
    db.add(
        OutlineChapter(
            outline_revision_id=outline.id,
            chapter_index=target.display_index,
            **chapter_payload,
            content_hash=content_hash(chapter_payload),
        )
    )
    db.flush()
    return outline


@pytest.fixture()
def seeded():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    user = User(
        email="story-context@example.com",
        password_hash="hash",
        display_name="Story Context",
    )
    db.add(user)
    db.flush()
    project = Project(
        owner_id=user.id,
        title="Context project",
        story_start="Start",
        story_end="End",
    )
    db.add(project)
    db.flush()
    state_json = {"route": "root", "trust": 3}
    state = StateSnapshot(
        project_id=project.id,
        state_hash=content_hash(state_json),
        state_json=state_json,
    )
    db.add(state)
    db.flush()
    path = StoryPath(
        project_id=project.id,
        title="Main",
        base_state_snapshot_id=state.id,
    )
    bible = StoryBibleRevision(
        project_id=project.id,
        revision_no=1,
        source_hash="a" * 64,
        content_hash=content_hash({"world": "first"}),
        content_json={"world": "first"},
    )
    db.add_all([path, bible])
    db.flush()
    db.add(
        ProjectContentHead(
            project_id=project.id,
            current_bible_revision_id=bible.id,
        )
    )

    slots = [
        ChapterSlot(project_id=project.id, created_for_story_path_id=path.id)
        for _ in range(3)
    ]
    db.add_all(slots)
    db.flush()
    first = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=slots[0].id,
        display_index=1,
    )
    db.add(first)
    db.flush()
    second = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=slots[1].id,
        display_index=2,
        predecessor_path_chapter_id=first.id,
    )
    db.add(second)
    db.flush()
    target = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=slots[2].id,
        display_index=3,
        predecessor_path_chapter_id=second.id,
    )
    db.add(target)
    db.flush()
    outline = _outline_for_target(
        db,
        project=project,
        path=path,
        bible=bible,
        target=target,
    )
    db.add(
        StoryPathOutlineHead(
            story_path_id=path.id,
            current_revision_id=outline.id,
        )
    )
    first_revision = _chapter_revision(
        db,
        project=project,
        path=path,
        path_chapter=first,
        bible=bible,
        outline=outline,
        revision_no=1,
        content="Main chapter one",
    )
    second_revision = _chapter_revision(
        db,
        project=project,
        path=path,
        path_chapter=second,
        bible=bible,
        outline=outline,
        revision_no=1,
        content="Main chapter two",
    )
    first.current_revision_id = first_revision.id
    second.current_revision_id = second_revision.id
    db.commit()

    seed = {
        "db": db,
        "user": user,
        "project": project,
        "state": state,
        "path": path,
        "bible": bible,
        "outline": outline,
        "first": first,
        "second": second,
        "target": target,
        "first_revision": first_revision,
        "second_revision": second_revision,
    }
    try:
        yield seed
    finally:
        db.close()
        engine.dispose()


def _forked_path(seed, *, option_key):
    db = seed["db"]
    project = seed["project"]
    root = seed["path"]
    state = seed["state"]
    checkpoint = StoryNode(
        project_id=project.id,
        node_type="checkpoint",
        checkpoint_key=f"checkpoint-{option_key}",
        content_revision_id=seed["second_revision"].id,
        payload={"question": "Choose"},
    )
    db.add(checkpoint)
    db.flush()
    candidate = BranchCandidate(
        project_id=project.id,
        checkpoint_node_id=checkpoint.id,
        option_key=option_key,
        state_delta={"route": option_key},
    )
    db.add(candidate)
    db.flush()
    path = StoryPath(
        project_id=project.id,
        parent_path_id=root.id,
        fork_path_chapter_id=seed["second"].id,
        fork_checkpoint_node_id=checkpoint.id,
        fork_candidate_id=candidate.id,
        base_state_snapshot_id=state.id,
        title=f"Branch {option_key}",
    )
    db.add(path)
    db.flush()

    target_slot = ChapterSlot(
        project_id=project.id,
        created_for_story_path_id=path.id,
    )
    db.add(target_slot)
    db.flush()
    first = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=seed["first"].chapter_slot_id,
        display_index=1,
        inherited_from_path_chapter_id=seed["first"].id,
        current_revision_id=seed["first_revision"].id,
    )
    db.add(first)
    db.flush()
    second = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=seed["second"].chapter_slot_id,
        display_index=2,
        predecessor_path_chapter_id=first.id,
        inherited_from_path_chapter_id=seed["second"].id,
        current_revision_id=seed["second_revision"].id,
    )
    db.add(second)
    db.flush()
    target = StoryPathChapter(
        story_path_id=path.id,
        chapter_slot_id=target_slot.id,
        display_index=3,
        predecessor_path_chapter_id=second.id,
    )
    db.add(target)
    db.flush()
    outline = _outline_for_target(
        db,
        project=project,
        path=path,
        bible=seed["bible"],
        target=target,
        title=f"Target {option_key}",
    )
    db.add(
        StoryPathOutlineHead(
            story_path_id=path.id,
            current_revision_id=outline.id,
        )
    )
    db.commit()
    return {
        "path": path,
        "first": first,
        "second": second,
        "target": target,
        "outline": outline,
    }


def test_resolver_builds_stable_manifest_in_predecessor_order(seeded):
    resolved = StoryContextResolver(seeded["db"]).resolve(seeded["target"].id)

    outline_payload = {
        "story_path_chapter_id": seeded["target"].id,
        "display_index": 3,
        "title": "Target",
        "summary": "Summary for Target",
        "conflict": None,
        "characters": ["A"],
        "scene": None,
        "emotion": None,
        "visual_keywords": ["station"],
    }

    assert resolved.manifest == {
        "project_id": seeded["project"].id,
        "story_path_id": seeded["path"].id,
        "path_chapter_id": seeded["target"].id,
        "bible_revision_id": seeded["bible"].id,
        "bible_content_hash": content_hash({"world": "first"}),
        "outline_revision_id": seeded["outline"].id,
        "outline_chapter_hash": content_hash(outline_payload),
        "state_snapshot_id": seeded["state"].id,
        "state_hash": content_hash({"route": "root", "trust": 3}),
        "fork": {
            "parent_path_id": None,
            "checkpoint_node_id": None,
            "candidate_id": None,
        },
        "fork_choice": None,
        "ancestors": [
            {
                "path_chapter_id": seeded["first"].id,
                "chapter_revision_id": seeded["first_revision"].id,
                "display_index": 1,
                "content_hash": content_hash("Main chapter one"),
            },
            {
                "path_chapter_id": seeded["second"].id,
                "chapter_revision_id": seeded["second_revision"].id,
                "display_index": 2,
                "content_hash": content_hash("Main chapter two"),
            },
        ],
    }
    assert resolved.context_hash == content_hash(resolved.manifest)
    assert [item.content for item in resolved.ancestors] == [
        "Main chapter one",
        "Main chapter two",
    ]
    assert StoryContextResolver(seeded["db"]).resolve(
        seeded["target"].id
    ).context_hash == resolved.context_hash


def test_frozen_context_does_not_follow_later_head_changes(seeded):
    db = seeded["db"]
    frozen = StoryContextResolver(db).resolve(seeded["target"].id)
    bible = StoryBibleRevision(
        project_id=seeded["project"].id,
        revision_no=2,
        source_hash="c" * 64,
        content_hash=content_hash({"world": "second"}),
        content_json={"world": "second"},
    )
    db.add(bible)
    db.flush()
    outline = _outline_for_target(
        db,
        project=seeded["project"],
        path=seeded["path"],
        bible=bible,
        target=seeded["target"],
        revision_no=2,
        title="Changed target",
    )
    first_revision = _chapter_revision(
        db,
        project=seeded["project"],
        path=seeded["path"],
        path_chapter=seeded["first"],
        bible=bible,
        outline=outline,
        revision_no=2,
        content="Changed chapter one",
    )
    content_head = db.query(ProjectContentHead).filter_by(
        project_id=seeded["project"].id
    ).one()
    outline_head = db.query(StoryPathOutlineHead).filter_by(
        story_path_id=seeded["path"].id
    ).one()
    content_head.current_bible_revision_id = bible.id
    outline_head.current_revision_id = outline.id
    seeded["first"].current_revision_id = first_revision.id
    db.commit()

    replayed = StoryContextResolver(db).resolve_frozen(
        frozen.manifest,
        expected_context_hash=frozen.context_hash,
    )
    current = StoryContextResolver(db).resolve(seeded["target"].id)

    assert replayed.story_bible == {"world": "first"}
    assert replayed.ancestors[0].chapter_revision_id == seeded["first_revision"].id
    assert replayed.ancestors[0].content == "Main chapter one"
    assert current.story_bible == {"world": "second"}
    assert current.ancestors[0].chapter_revision_id == first_revision.id
    assert current.context_hash != frozen.context_hash


def test_child_context_ignores_parent_and_sibling_head_changes(seeded):
    db = seeded["db"]
    branch_a = _forked_path(seeded, option_key="a")
    branch_b = _forked_path(seeded, option_key="b")
    sibling_revision = _chapter_revision(
        db,
        project=seeded["project"],
        path=branch_b["path"],
        path_chapter=branch_b["second"],
        bible=seeded["bible"],
        outline=branch_b["outline"],
        revision_no=2,
        content="Sibling-only chapter two",
    )
    branch_b["second"].current_revision_id = sibling_revision.id
    seeded["second"].current_revision_id = sibling_revision.id
    db.commit()

    resolved = StoryContextResolver(db).resolve(branch_a["target"].id)

    assert resolved.manifest["story_path_id"] == branch_a["path"].id
    assert [item.path_chapter_id for item in resolved.ancestors] == [
        branch_a["first"].id,
        branch_a["second"].id,
    ]
    assert [item.chapter_revision_id for item in resolved.ancestors] == [
        seeded["first_revision"].id,
        seeded["second_revision"].id,
    ]
    assert "Sibling-only" not in " ".join(item.content for item in resolved.ancestors)


def test_frozen_child_context_without_fork_choice_remains_replayable(seeded):
    branch = _forked_path(seeded, option_key="legacy")
    resolver = StoryContextResolver(seeded["db"])
    current = resolver.resolve(branch["target"].id)
    legacy_manifest = deepcopy(current.manifest)
    legacy_manifest.pop("fork_choice")

    replayed = resolver.resolve_frozen(
        legacy_manifest,
        expected_context_hash=content_hash(legacy_manifest),
    )

    assert replayed.fork_choice is None
    assert replayed.manifest == legacy_manifest


def test_frozen_context_without_payload_hash_fields_remains_replayable(seeded):
    db = seeded["db"]
    resolver = StoryContextResolver(db)
    current = resolver.resolve(seeded["target"].id)
    legacy_manifest = deepcopy(current.manifest)
    legacy_manifest.pop("bible_content_hash")
    legacy_manifest.pop("outline_chapter_hash")
    legacy_manifest.pop("state_hash")
    for ancestor in legacy_manifest["ancestors"]:
        ancestor.pop("content_hash")
    legacy_hash = content_hash(legacy_manifest)
    outline_chapter = db.query(OutlineChapter).filter_by(
        outline_revision_id=seeded["outline"].id,
        story_path_chapter_id=seeded["target"].id,
    ).one()
    pre_reconciliation_outline = {
        **current.outline_chapter,
        "story_path_chapter_id": None,
    }
    db.execute(
        update(OutlineChapter)
        .where(OutlineChapter.id == outline_chapter.id)
        .values(content_hash=content_hash(pre_reconciliation_outline))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()

    replayed = resolver.resolve_frozen(
        legacy_manifest,
        expected_context_hash=legacy_hash,
    )

    assert replayed.story_bible == {"world": "first"}
    assert [ancestor.content for ancestor in replayed.ancestors] == [
        "Main chapter one",
        "Main chapter two",
    ]

    db.execute(
        update(ChapterRevision)
        .where(ChapterRevision.id == seeded["first_revision"].id)
        .values(content="Tampered legacy ancestor")
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    with pytest.raises(StoryContextResolutionError) as captured:
        resolver.resolve_frozen(
            legacy_manifest,
            expected_context_hash=legacy_hash,
        )

    assert captured.value.code == "context.ancestor_payload_mismatch"


def test_resolver_rejects_predecessor_from_sibling_path(seeded):
    db = seeded["db"]
    branch_a = _forked_path(seeded, option_key="a")
    branch_b = _forked_path(seeded, option_key="b")
    db.execute(
        update(StoryPathChapter)
        .where(StoryPathChapter.id == branch_a["target"].id)
        .values(predecessor_path_chapter_id=branch_b["second"].id)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()

    with pytest.raises(StoryContextResolutionError) as captured:
        StoryContextResolver(db).resolve(branch_a["target"].id)

    assert captured.value.code == "context.predecessor_outside_path"


def test_frozen_context_rejects_hash_tampering_and_wrong_revision_slot(seeded):
    resolver = StoryContextResolver(seeded["db"])
    frozen = resolver.resolve(seeded["target"].id)
    tampered = deepcopy(frozen.manifest)
    tampered["ancestors"][0]["chapter_revision_id"] = seeded["second_revision"].id

    with pytest.raises(StoryContextResolutionError) as captured:
        resolver.resolve_frozen(
            tampered,
            expected_context_hash=frozen.context_hash,
        )
    assert captured.value.code == "context.hash_mismatch"

    with pytest.raises(StoryContextResolutionError) as captured:
        resolver.resolve_frozen(
            tampered,
            expected_context_hash=content_hash(tampered),
        )
    assert captured.value.code == "context.ancestor_revision_mismatch"


@pytest.mark.parametrize(
    ("payload", "error_code"),
    [
        ("bible", "context.bible_payload_mismatch"),
        ("outline", "context.outline_payload_mismatch"),
        ("state", "context.state_payload_mismatch"),
        ("ancestor", "context.ancestor_payload_mismatch"),
    ],
)
def test_frozen_context_rejects_referenced_payload_tampering(
    seeded,
    payload,
    error_code,
):
    db = seeded["db"]
    frozen = StoryContextResolver(db).resolve(seeded["target"].id)

    if payload == "bible":
        statement = (
            update(StoryBibleRevision)
            .where(StoryBibleRevision.id == seeded["bible"].id)
            .values(content_json={"world": "tampered"})
        )
    elif payload == "outline":
        outline_chapter = db.query(OutlineChapter).filter_by(
            outline_revision_id=seeded["outline"].id,
            story_path_chapter_id=seeded["target"].id,
        ).one()
        statement = (
            update(OutlineChapter)
            .where(OutlineChapter.id == outline_chapter.id)
            .values(summary="Tampered outline summary")
        )
    elif payload == "state":
        statement = (
            update(StateSnapshot)
            .where(StateSnapshot.id == seeded["state"].id)
            .values(state_json={"route": "tampered", "trust": 0})
        )
    else:
        statement = (
            update(ChapterRevision)
            .where(ChapterRevision.id == seeded["first_revision"].id)
            .values(content="Tampered ancestor content")
        )
    db.execute(statement.execution_options(synchronize_session=False))
    db.commit()
    db.expire_all()

    with pytest.raises(StoryContextResolutionError) as captured:
        StoryContextResolver(db).resolve_frozen(
            frozen.manifest,
            expected_context_hash=frozen.context_hash,
        )

    assert captured.value.code == error_code


def test_resolver_rejects_predecessor_cycles(seeded):
    seeded["first"].predecessor_path_chapter_id = seeded["target"].id
    seeded["db"].commit()

    with pytest.raises(StoryContextResolutionError) as captured:
        StoryContextResolver(seeded["db"]).resolve(seeded["target"].id)

    assert captured.value.code == "context.predecessor_cycle"


def test_resolver_requires_every_ancestor_to_have_selected_revision(seeded):
    seeded["second"].current_revision_id = None
    seeded["db"].commit()

    with pytest.raises(StoryContextResolutionError) as captured:
        StoryContextResolver(seeded["db"]).resolve(seeded["target"].id)

    assert captured.value.code == "context.ancestor_head_missing"
