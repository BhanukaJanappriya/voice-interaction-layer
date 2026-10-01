"""Alembic environment for the application database.

The URL comes from config.DATABASE_URL, never from alembic.ini, so the
running app, `alembic upgrade head` and the seed script can never point at
different databases. The engine itself is db.base.engine, so migrations
get the same SQLite settings as the app (foreign keys on, WAL journal).

render_as_batch is on for SQLite: SQLite cannot ALTER most things in place,
so Alembic copies the table, changes the copy and swaps it in. On
PostgreSQL the normal ALTER statements are used.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context

# backend/ must be importable by bare name (import config, from db.base
# import Base), the way every backend module imports its neighbours. This
# file lives in backend/db/migrations/, so backend/ is two levels up.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import config  # noqa: E402
import db.models  # noqa: E402,F401  (registers every table on Base.metadata)
from db.base import Base, engine  # noqa: E402

alembic_config = context.config

# Only the alembic command line sets up logging from alembic.ini. When the
# app runs migrations at startup (db/migrate.py) it passes
# configure_logger=False, because fileConfig would otherwise replace the
# server's own logging setup.
if alembic_config.config_file_name is not None and alembic_config.attributes.get("configure_logger", True):
    fileConfig(alembic_config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return config.DATABASE_URL.startswith("sqlite")


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: prints the SQL instead of running it."""
    context.configure(
        url=config.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = alembic_config.attributes.get("connection")
    if connection is not None:
        _run_with(connection)
        return
    with engine.connect() as connection:
        _run_with(connection)


def _run_with(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=_is_sqlite(),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
