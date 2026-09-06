"""Shared response helpers for replacement authoring routers."""
from __future__ import annotations

from fastapi import Response

from app.models_v2 import GenerationTask
from app.schemas_v2 import TaskAccepted


def accepted_task(task: GenerationTask, *, created: bool) -> TaskAccepted:
    return TaskAccepted(
        task_id=task.id,
        status=task.status,
        events_url=f"/api/tasks/{task.id}/events",
        created=created,
    )


def set_lock_etag(response: Response, lock_version: int) -> None:
    response.headers["ETag"] = f'"{lock_version}"'


__all__ = ["accepted_task", "set_lock_etag"]
