"""Retry failed resource renders for project 78 until success or max attempts."""
import sys
import time
import uuid
from datetime import datetime, timezone

from app.database import SessionLocal
from app.application.chapter_script_service import request_script_resource_render
from app.models_v2 import GenerationTask

PROJECT_ID = 78
USER_ID = 87  # 999@qq.com
REVISION_ID = "ad8e8a54-e464-44fc-b1ab-3a6a859f8872"
ROLES = ["portrait", "keyframe"]
MAX_ROUNDS = 10


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    db = SessionLocal()
    for round_no in range(1, MAX_ROUNDS + 1):
        parents = {}
        for role in ROLES:
            result = request_script_resource_render(
                db,
                user_id=USER_ID,
                project_id=PROJECT_ID,
                script_revision_id=REVISION_ID,
                role=role,
                idempotency_key=f"retry-project78-{uuid.uuid4()}",
            )
            db.commit()
            parent = result["parent"]
            parents[parent.id] = role
            log(f"round {round_no} role={role} parent_task={parent.id} created={bool(result.get('parent_created'))}")

        done = False
        deadline = time.time() + 900
        while time.time() < deadline:
            time.sleep(20)
            rows = (
                db.query(GenerationTask.id, GenerationTask.status)
                .filter(GenerationTask.id.in_(list(parents)))
                .all()
            )
            statuses = {parents[r[0]]: r[1] for r in rows}
            if all(s in ("succeeded", "failed", "partial") for s in statuses.values()):
                log(f"round {round_no} finished: {statuses}")
                if all(s == "succeeded" for s in statuses.values()):
                    return 0
                done = True
                break
            log(f"round {round_no} in progress: {statuses}")

        if not done:
            log(f"round {round_no} timed out waiting, statuses may still be running")
            return 1

        wait = min(60 * round_no, 300)
        log(f"round {round_no} had failures, backing off {wait}s before retry")
        time.sleep(wait)

    log("exhausted rounds, still failing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
