"""Database engine and session handling.

Plain SQLAlchemy rather than an ORM integration: there are four tables and the
interesting behaviour is in the constraints, so an extra layer would only hide
the transaction boundaries.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Config
from .models import Base

_engine: Engine | None = None
_sessionmaker: sessionmaker[Session] | None = None


def init_engine(config: Config) -> Engine:
    """Create the process-wide engine. Idempotent."""
    global _engine, _sessionmaker
    if _engine is not None:
        return _engine

    kwargs: dict = {"pool_pre_ping": True, "future": True}
    if config.database_url.startswith("sqlite"):
        # Serverless and multi-threaded SQLite needs this relaxed, and the
        # in-memory form used by tests needs the same connection shared.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 5
        kwargs["pool_recycle"] = 300

    _engine = create_engine(config.database_url, **kwargs)

    if config.database_url.startswith("sqlite"):

        @event.listens_for(_engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver glue
            cursor = dbapi_connection.cursor()
            # Referential integrity is off by default in SQLite, which would
            # silently break the comment reply cascade.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    _sessionmaker = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("engine not initialised; call init_engine() first")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    if _sessionmaker is None:
        raise RuntimeError("engine not initialised; call init_engine() first")
    session = _sessionmaker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine() -> None:
    """Drop cached engine/session. Used between tests."""
    global _engine, _sessionmaker
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _sessionmaker = None


def create_schema() -> None:
    Base.metadata.create_all(get_engine())


def drop_schema() -> None:
    Base.metadata.drop_all(get_engine())