"""Brings the database schema up to date, from app startup or by hand.

server.py calls upgrade_to_head() at startup, before anything touches the
database, so a fresh clone gets its tables and an existing database gets
any new migrations, with nobody having to remember a separate command.
It is safe on a database that is already current: Alembic reads the
alembic_version table and does nothing.

Paths to alembic.ini and the migrations folder are absolute (worked out
from this file's location), so this works whatever the current directory
is: from inside backend/, from the repository root, or from a test runner.

Standalone, from the repository root:
    python -m backend.db.migrate
"""

import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

import config

_DB_DIR = Path(__file__).resolve().parent
_MIGRATIONS_DIR = _DB_DIR / "migrations"
_ALEMBIC_INI = config.PROJECT_ROOT / "alembic.ini"


def _alembic_config() -> Config:
    # alembic.ini is optional here (the script location is set explicitly),
    # so a deployment that ships only backend/ still migrates.
    alembic_cfg = Config(str(_ALEMBIC_INI)) if _ALEMBIC_INI.exists() else Config()
    # configparser treats % as interpolation, so a literal % in the path
    # (possible in a Windows user folder) has to be doubled.
    alembic_cfg.set_main_option("script_location", str(_MIGRATIONS_DIR).replace("%", "%%"))
    # Leave the server's logging alone (see env.py).
    alembic_cfg.attributes["configure_logger"] = False
    return alembic_cfg


def upgrade_to_head() -> None:
    """Applies every pending migration. Raises on failure: the app should
    not start against a database whose schema it does not understand."""
    started = time.perf_counter()
    command.upgrade(_alembic_config(), "head")
    status = database_status()
    print(
        f"Database ready at revision {status['revision']} "
        f"({status['dialect']}, {(time.perf_counter() - started) * 1000:.0f} ms)"
    )


def database_status() -> dict:
    """For /health: whether the database answers and whether its schema
    is at the newest migration. Never raises."""
    from db.base import engine  # imported here so importing this module stays cheap

    head = ScriptDirectory.from_config(_alembic_config()).get_current_head()
    try:
        with engine.connect() as connection:
            revision = MigrationContext.configure(connection).get_current_revision()
        return {
            "ok": revision == head,
            "dialect": engine.dialect.name,
            "revision": revision,
            "head": head,
        }
    except Exception as exc:
        return {"ok": False, "dialect": engine.dialect.name, "revision": None, "head": head, "error": str(exc)}


if __name__ == "__main__":
    print(f"Database: {config.DATABASE_URL.split('@')[-1]}")
    upgrade_to_head()
