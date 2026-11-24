"""Database engine/session setup.

SQLite is the default (zero setup on Windows). The models only use portable column types,
so pointing SKILLDEV_DATABASE_URL at PostgreSQL works without model changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine: Engine | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")  # safe concurrent reads while writing
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def init_engine(url: str) -> Engine:
    global _engine
    if _engine is not None:
        _engine.dispose()
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        event.listen(_engine, "connect", _sqlite_pragmas)
    SessionLocal.configure(bind=_engine)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised")
    return _engine


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, rolled back on error."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def now() -> datetime:
    """Local, second-precision timestamp. The app is single-user and single-machine, so
    naive local time keeps 'today' and day-grouping correct without timezone conversion."""
    return datetime.now().replace(microsecond=0)
