from __future__ import annotations

from datetime import timedelta
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.application.story_bible_service import create_bible_revision
from app.application.task_service import (
    TaskLeaseLostError,
    claim_task,
    complete_task,
    create_generation_task,
    lease_from_task,
)
from app.database import Base
from app.models import Project, User
from app.models_v2 import GenerationTask, StoryBibleRevision, utcnow


@pytest.fixture()
def postgres_task_factory():
    database_url = os.getenv("TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("requires explicit TEST_DATABASE_URL")

    url = make_url(database_url)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    admin_engine = create_engine(url)
    if admin_engine.dialect.name != "postgresql":
        admin_engine.dispose()
        pytest.skip("TEST_DATABASE_URL must point to PostgreSQL")

    schema = f"test_task_lease_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_engine = create_engine(
        url,
        connect_args={"options": f"-csearch_path={schema}"},
    )
    try:
        Base.metadata.create_all(isolated_engine)
        yield sessionmaker(
            bind=isolated_engine,
            autoflush=False,
            expire_on_commit=False,
        )
    finally:
        isolated_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


def test_late_worker_cannot_commit_revision_after_postgresql_lease_reclaim(
    postgres_task_factory,
):
    setup = postgres_task_factory()
    try:
        user = User(
            email="postgres-fencing@example.com",
            password_hash="hash",
            display_name="PostgreSQL fencing",
            quota_total=20,
            quota_daily=20,
            quota_used_total=0,
            quota_used_daily=0,
        )
        setup.add(user)
        setup.flush()
        project = Project(
            owner_id=user.id,
            title="Fenced project",
            story_start="start",
            story_end="end",
        )
        setup.add(project)
        setup.flush()
        task, _ = create_generation_task(
            setup,
            user_id=user.id,
            project_id=project.id,
            kind="bible.generate",
            idempotency_key="postgres-lease-fencing",
            estimated_cost=0,
        )
        setup.commit()
        task_id = task.id
        project_id = project.id
        user_id = user.id
    finally:
        setup.close()

    worker_a = postgres_task_factory()
    try:
        claimed_a = claim_task(worker_a, task_id, "provider-worker")
        assert claimed_a is not None
        lease_a = lease_from_task(claimed_a)
        worker_a.commit()
    finally:
        worker_a.close()

    expire = postgres_task_factory()
    try:
        stale = expire.get(GenerationTask, task_id)
        assert stale is not None
        stale.heartbeat_at = utcnow() - timedelta(minutes=10)
        expire.commit()
    finally:
        expire.close()

    worker_b = postgres_task_factory()
    try:
        claimed_b = claim_task(
            worker_b,
            task_id,
            "provider-worker",
            lease_seconds=1,
        )
        assert claimed_b is not None
        lease_b = lease_from_task(claimed_b)
        assert lease_b.owner == lease_a.owner
        assert lease_b.token != lease_a.token
        worker_b.commit()
    finally:
        worker_b.close()

    late_worker = postgres_task_factory()
    try:
        late_task = late_worker.get(GenerationTask, task_id, with_for_update=True)
        assert late_task is not None
        late_revision = create_bible_revision(
            late_worker,
            project_id=project_id,
            content={"worldview": "late provider result"},
            source={"prompt_version": "late"},
            user_id=user_id,
            task_id=task_id,
            activate=False,
        )
        assert late_revision.id is not None
        with pytest.raises(TaskLeaseLostError):
            complete_task(
                late_worker,
                late_task,
                result_refs={"bible_revision_id": late_revision.id},
                actual_cost=0,
                lease=lease_a,
            )
        late_worker.rollback()
    finally:
        late_worker.close()

    verify_reclaim = postgres_task_factory()
    try:
        task = verify_reclaim.get(GenerationTask, task_id)
        assert task is not None
        assert task.status == "running"
        assert task.lease_token == lease_b.token
        assert (
            verify_reclaim.query(StoryBibleRevision)
            .filter(StoryBibleRevision.generation_task_id == task_id)
            .count()
            == 0
        )
    finally:
        verify_reclaim.close()

    winner = postgres_task_factory()
    try:
        task = winner.get(GenerationTask, task_id, with_for_update=True)
        assert task is not None
        revision = create_bible_revision(
            winner,
            project_id=project_id,
            content={"worldview": "winning provider result"},
            source={"prompt_version": "winner"},
            user_id=user_id,
            task_id=task_id,
            activate=False,
        )
        complete_task(
            winner,
            task,
            result_refs={"bible_revision_id": revision.id},
            actual_cost=0,
            lease=lease_b,
        )
        winner.commit()
    finally:
        winner.close()

    verify_winner = postgres_task_factory()
    try:
        task = verify_winner.get(GenerationTask, task_id)
        revisions = (
            verify_winner.query(StoryBibleRevision)
            .filter(StoryBibleRevision.generation_task_id == task_id)
            .all()
        )
        assert task is not None
        assert task.status == "succeeded"
        assert task.lease_owner is None
        assert task.lease_token is None
        assert len(revisions) == 1
        assert revisions[0].content_json == {"worldview": "winning provider result"}
        assert task.result_refs == {"bible_revision_id": revisions[0].id}
    finally:
        verify_winner.close()
