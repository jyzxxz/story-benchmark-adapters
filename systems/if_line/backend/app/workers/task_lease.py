from __future__ import annotations

import logging
import threading

from sqlalchemy.orm import sessionmaker

from app.application.task_service import TaskLease, renew_task_lease
from app.core.config import get_settings
from app.database import SessionLocal


logger = logging.getLogger(__name__)


class TaskLeaseHeartbeat:
    """Renew one fenced task lease without holding the provider transaction open."""

    def __init__(
        self,
        task_id: str,
        lease: TaskLease,
        *,
        session_factory: sessionmaker = SessionLocal,
        interval_seconds: int | None = None,
    ) -> None:
        self.task_id = task_id
        self.lease = lease
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds or max(
            10,
            get_settings().task_lease_seconds // 3,
        )
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lease_lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> "TaskLeaseHeartbeat":
        if self._thread is not None:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"task-heartbeat-{self.task_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def __enter__(self) -> "TaskLeaseHeartbeat":
        return self.start()

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop()

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            session = self.session_factory()
            try:
                if not renew_task_lease(session, self.task_id, self.lease):
                    session.rollback()
                    self._lost.set()
                    return
                session.commit()
            except Exception:
                session.rollback()
                logger.exception(
                    "task heartbeat failed; will retry task_id=%s lease_owner=%s",
                    self.task_id,
                    self.lease.owner,
                )
            finally:
                session.close()
