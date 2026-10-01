"""Database engine and session handling, shared by every module that
stores anything (users, admins, external agents, MCP clients, audit log).

One database for the whole app, reached through SQLAlchemy so the same
code runs on the default SQLite file or on PostgreSQL by changing only
DATABASE_URL (see config.py). The schema itself is created and upgraded
by Alembic migrations (backend/db/migrations), not by create_all(), so a
deployed database can be moved forward without losing data.
"""

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

import config

# Deterministic constraint names. SQLite cannot ALTER a constraint, so
# Alembic rebuilds the table ("batch" mode) and has to refer to each
# constraint by name; unnamed ones cannot be dropped or changed later.
# PostgreSQL benefits the same way: migrations name what they touch.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _make_engine(url: str):
    if url.startswith("sqlite"):
        if ":memory:" not in url:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        # FastAPI runs blocking handlers on a thread pool, so the same
        # SQLite connection may be used from more than one thread.
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


engine = _make_engine(config.DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """A session that commits on success and rolls back on any error, for
    service code outside a request (startup seeding, MCP tools, scripts)."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the
    handler itself when it changes something."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
