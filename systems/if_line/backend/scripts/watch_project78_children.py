"""Watch project 78 requeued children; on failure, requeue again (up to N times)."""
import sys
import time
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models_v2 import GenerationTask
from app.application.task_service import retry_task

CHILD_IDS = [
    "80a4fad5-35e6-4cb9-8c92-361435e2c268",
    "0cce3573-57a0-4500-8b3d-cb6408c7f0a3",
    "4a82ce4d-228c-425a-9a22-fa9c1b8bc838",
]
MAX_REQUEUE = 5


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    db = SessionLocal()
    requeues = 0
    while True:
        time.sleep(30)
        tasks = {t.id: t for t in db.query(GenerationTask).filter(GenerationTask.id.in_(CHILD_IDS)).all()}
        statuses = {tid: tasks[tid].status for tid in CHILD_IDS}
        log(str(statuses))
        db.rollback()
        if all(s == "succeeded" for s in statuses.values()):
            log("all succeeded")
            return 0
        if all(s in ("succeeded", "failed") for s in statuses.values()):
            if requeues >= MAX_REQUEUE:
                log("requeue budget exhausted")
                return 1
            requeues += 1
            wait = min(60 * requeues, 300)
            log(f"terminal failure, cooling down {wait}s before requeue #{requeues}")
            time.sleep(wait)
            for tid in CHILD_IDS:
                t = db.query(GenerationTask).filter(GenerationTask.id == tid).one()
                if t.status == "failed":
                    if t.attempt >= t.max_attempts:
                        t.max_attempts = t.attempt + 3
                        db.commit()
                    try:
                        retry_task(db, t)
                        db.commit()
                        log(f"requeued {tid}")
                    except Exception as exc:
                        db.rollback()
                        log(f"requeue {tid} failed: {exc}")
            continue


if __name__ == "__main__":
    sys.exit(main())
