from __future__ import annotations

from pathlib import Path

from app.workers.celery_app import celery_app


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_every_periodic_task_routes_to_the_consumed_maintenance_queue():
    """Beat must not enqueue work on Celery's unconsumed default queue."""

    routes = celery_app.conf.task_routes
    scheduled_tasks = {
        entry["task"] for entry in celery_app.conf.beat_schedule.values()
    }

    assert scheduled_tasks == {
        "if_line.dispatch_outbox",
        "if_line.recover_stale_tasks",
        "if_line.purge_deleted_media",
    }
    assert {
        task_name: routes.get(task_name) for task_name in scheduled_tasks
    } == {
        task_name: {"queue": "maintenance"} for task_name in scheduled_tasks
    }

    worker_script = (BACKEND_ROOT / "start_worker.sh").read_text(encoding="utf-8")
    assert "text,image,audio,compile,maintenance,celery" in worker_script
