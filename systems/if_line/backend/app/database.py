"""Database engine, sessions and transaction ownership.

Schema changes are deliberately absent from this module.  Deployments must run
Alembic before starting the API; importing the application never mutates the
database schema.
"""
from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional, Type

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.orm_base import Base


settings = get_settings()


@lru_cache(maxsize=1)
def get_alembic_head_revision() -> str:
    """Return the repository's single Alembic head revision."""

    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            "database migrations must have exactly one Alembic head; "
            f"found {sorted(heads)}"
        )
    return heads[0]


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp for all new persistence code."""

    return datetime.now(timezone.utc)


def _normalized_database_url(raw: str) -> URL:
    url = make_url(raw)
    # SQLAlchemy's unqualified PostgreSQL URL historically selects psycopg2.
    # This project standardises on psycopg 3 for Python 3.10/3.12 support.
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url


def _engine_kwargs(url: URL) -> dict:
    if url.get_backend_name() == "sqlite":
        return {
            "connect_args": {"check_same_thread": False},
        }

    kwargs: dict = {
        "pool_pre_ping": True,
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
    }
    if url.get_backend_name() == "postgresql":
        kwargs["connect_args"] = {
            "options": (
                "-c timezone=UTC "
                f"-c statement_timeout={settings.database_statement_timeout_ms}"
            )
        }
    return kwargs


DATABASE_URL = settings.database_url
_database_url = _normalized_database_url(DATABASE_URL)
engine = create_engine(_database_url, **_engine_kwargs(_database_url))


if _database_url.get_backend_name() == "sqlite":

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
def get_db():
    """FastAPI dependency providing a request-scoped SQLAlchemy session."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class UnitOfWork(AbstractContextManager["UnitOfWork"]):
    """Own one explicit application-service transaction.

    Repositories should call ``flush`` only.  The application service calls
    ``commit`` after all domain changes are ready, or ``rollback`` on failure.
    Merely leaving the context never commits implicitly.
    """

    def __init__(self, session_factory=SessionLocal) -> None:
        self._session_factory = session_factory
        self.session: Optional[Session] = None
        self._committed = False

    def __enter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        self._committed = False
        return self

    def commit(self) -> None:
        if self.session is None:
            raise RuntimeError("UnitOfWork has not been entered")
        self.session.commit()
        self._committed = True

    def rollback(self) -> None:
        if self.session is not None:
            self.session.rollback()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback,
    ) -> None:
        if self.session is None:
            return None
        try:
            if exc_type is not None or not self._committed:
                self.session.rollback()
        finally:
            self.session.close()
            self.session = None
        return None


def check_database_connection(target_engine: Engine = engine) -> None:
    """Raise when the configured database cannot serve a trivial query."""

    with target_engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def check_database_schema(
    target_engine: Engine = engine,
    *,
    expected_revision: str | None = None,
) -> None:
    """Fail when feature-gated v2 APIs are enabled before Alembic upgrade."""

    expected_revision = expected_revision or get_alembic_head_revision()

    with target_engine.connect() as connection:
        if "alembic_version" not in inspect(connection).get_table_names():
            raise RuntimeError("database is not managed by Alembic")
        revisions = {
            row[0]
            for row in connection.execute(text("SELECT version_num FROM alembic_version"))
        }
        if revisions != {expected_revision}:
            raise RuntimeError(
                "database schema revision mismatch: "
                f"expected {[expected_revision]}, actual {sorted(revisions)}"
            )
