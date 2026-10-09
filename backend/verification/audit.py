"""Audit rows for the wizard. Only keyed hashes: no code, no raw landline,
no mobile, no email, no client address. A failure to write an audit row
never breaks the wizard (it is logged by class name only)."""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa

from db.base import session_scope
from verification.models import VerificationAuditRow

EVENTS = ("lookup", "send", "verify", "logout")


def record(
    event: str,
    outcome: str,
    at: datetime,
    phone_hash: Optional[str] = None,
    ip_hash: Optional[str] = None,
    channel: Optional[str] = None,
) -> None:
    try:
        with session_scope() as session:
            session.add(
                VerificationAuditRow(
                    at=at, event=event, phone_hash=phone_hash, ip_hash=ip_hash, outcome=outcome[:32], channel=channel
                )
            )
    except sa.exc.SQLAlchemyError as exc:
        print(f"VERIFY audit write failed: {type(exc).__name__}")
