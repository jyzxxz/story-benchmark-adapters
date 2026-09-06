"""Requeue the failed asset.render children of project 78 via retry_task."""
from app.database import SessionLocal
from app.models_v2 import GenerationTask
from app.application.task_service import retry_task

CHILD_IDS = [
    "80a4fad5-35e6-4cb9-8c92-361435e2c268",  # keyframe asset 1601
    "0cce3573-57a0-4500-8b3d-cb6408c7f0a3",  # portrait asset 1617
    "4a82ce4d-228c-425a-9a22-fa9c1b8bc838",  # portrait asset 1607
]

db = SessionLocal()
for tid in CHILD_IDS:
    t = db.query(GenerationTask).filter(GenerationTask.id == tid).one()
    print(tid, t.kind, t.status, f"attempt={t.attempt}/{t.max_attempts}")
    if t.status != "failed":
        print("  skip: not failed")
        continue
    if t.attempt >= t.max_attempts:
        t.max_attempts = t.attempt + 3
        db.commit()
    retry_task(db, t)
    db.commit()
    print("  requeued ->", t.status)
