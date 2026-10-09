"""Tables of the verification wizard (plan 3.3, R3).

customer_directory   who may be verified: landline, normalized name, the
                     mobile and email codes may go to. FAKE rows only until
                     the owner supplies a real source (see seed_demo.py).
verification_audit   one row per wizard event. Keyed hashes only: no code,
                     no raw landline, no mobile, no email, no address.

The wizard reuses otp_challenges (ADR 3.9). Two nullable columns are added
by revision 0003: purpose ("verify" for the wizard, NULL for the older
/auth/otp flow) and wizard_ref (the directory id and the language of the
message, written by this package). _challenges below describes the whole
table, old and new columns, on a private MetaData that is never used to
create tables in production (Alembic does), the same approach as
otp/tables.py, so this package works whether or not the ORM class
db.models.OtpChallenge has been patched yet.
"""

from datetime import datetime, timezone
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base

PURPOSE = "verify"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CustomerDirectoryRow(Base):
    __tablename__ = "customer_directory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    phone_e164: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name_normalized: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(80))
    mobile_e164: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(254), nullable=True)
    preferred_language: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class VerificationAuditRow(Base):
    __tablename__ = "verification_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    event: Mapped[str] = mapped_column(String(24))
    phone_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    ip_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(32))
    channel: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)


_metadata = sa.MetaData()
_TIME = sa.DateTime(timezone=True)

challenges = sa.Table(
    "otp_challenges",
    _metadata,
    sa.Column("id", sa.String(32), primary_key=True),
    sa.Column("user_id", sa.Integer, nullable=True),
    sa.Column("phone_hash", sa.String(64), nullable=False),
    sa.Column("code_hash", sa.String(64), nullable=False),
    sa.Column("destination_hash", sa.String(64), nullable=False),
    sa.Column("channel", sa.String(8), nullable=False),
    sa.Column("attempts", sa.Integer, nullable=False),
    sa.Column("expires_at", _TIME, nullable=False),
    sa.Column("consumed_at", _TIME, nullable=True),
    sa.Column("created_at", _TIME, nullable=False),
    sa.Column("client_ip_hash", sa.String(64), nullable=False),
    sa.Column("hub_ref", sa.Text, nullable=True),
    sa.Column("purpose", sa.String(16), nullable=True),
    sa.Column("wizard_ref", sa.String(64), nullable=True),
)

users = sa.table(
    "users",
    sa.column("id", sa.Integer),
    sa.column("phone_e164", sa.String),
    sa.column("display_name", sa.String),
    sa.column("pin_hash", sa.String),
    sa.column("preferred_language", sa.String),
    sa.column("failed_attempts", sa.Integer),
    sa.column("created_at", _TIME),
    sa.column("last_login_at", _TIME),
)


def ensure_schema(engine) -> None:
    """Creates what this package needs when revision 0003 has not been
    applied: for tests, the self check and seed_demo on a scratch database.
    Production uses Alembic. Safe to call repeatedly."""
    Base.metadata.create_all(
        engine, tables=[CustomerDirectoryRow.__table__, VerificationAuditRow.__table__]
    )
    inspector = sa.inspect(engine)
    if not inspector.has_table("otp_challenges"):
        from db import models as _db_models  # noqa: F401  (registers users on Base)

        Base.metadata.create_all(engine, tables=[Base.metadata.tables["users"]], checkfirst=True)
        challenges.create(engine, checkfirst=True)
        return
    existing = {c["name"] for c in inspector.get_columns("otp_challenges")}
    with engine.begin() as conn:
        for name, ddl in (("purpose", "VARCHAR(16)"), ("wizard_ref", "VARCHAR(64)")):
            if name not in existing:
                conn.execute(sa.text(f"ALTER TABLE otp_challenges ADD COLUMN {name} {ddl}"))
