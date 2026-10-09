"""Where the wizard finds a customer by landline number.

CustomerLookup is the seam. CustomerDirectory reads the local
customer_directory table (owner decision, ADR 3.1). HubCustomerLookup is a
documented stub: the MCP hub has no lookup tool, so there is nothing to call.
"""

from dataclasses import dataclass
from typing import Optional, Protocol

import sqlalchemy as sa

from db.base import session_scope
from verification.models import CustomerDirectoryRow
from verification.namematch import normalize_name


@dataclass(frozen=True)
class CustomerRecord:
    """Plain data. mobile_e164 and email stay on the server; only masked
    forms ever reach a response."""

    id: int
    phone_e164: str
    display_name: str
    name_normalized: str
    mobile_e164: Optional[str]
    email: Optional[str]
    preferred_language: Optional[str]


class CustomerLookup(Protocol):
    def find(self, phone_e164: str) -> Optional[CustomerRecord]:
        """The active customer with this landline (E.164), or None."""
        ...

    def get(self, record_id: int) -> Optional[CustomerRecord]:
        """The active customer with this record id (what a ticket and a
        challenge carry instead of a number), or None."""
        ...


class CustomerDirectory:
    """The local database directory."""

    def find(self, phone_e164: str) -> Optional[CustomerRecord]:
        with session_scope() as session:
            row = session.scalar(
                sa.select(CustomerDirectoryRow).where(CustomerDirectoryRow.phone_e164 == phone_e164)
            )
            return self._record(row)

    def get(self, record_id: int) -> Optional[CustomerRecord]:
        with session_scope() as session:
            return self._record(session.get(CustomerDirectoryRow, record_id))

    @staticmethod
    def _record(row) -> Optional[CustomerRecord]:
        if row is None or not row.active:
            return None
        return CustomerRecord(
            id=row.id,
            phone_e164=row.phone_e164,
            display_name=row.display_name,
            name_normalized=row.name_normalized,
            mobile_e164=row.mobile_e164 or None,
            email=row.email or None,
            preferred_language=row.preferred_language,
        )

    def upsert(
        self,
        phone_e164: str,
        display_name: str,
        mobile_e164: Optional[str] = None,
        email: Optional[str] = None,
        preferred_language: Optional[str] = None,
    ) -> int:
        """Adds or updates one row (used by seed_demo and tests)."""
        with session_scope() as session:
            row = session.scalar(
                sa.select(CustomerDirectoryRow).where(CustomerDirectoryRow.phone_e164 == phone_e164)
            )
            if row is None:
                row = CustomerDirectoryRow(phone_e164=phone_e164)
                session.add(row)
            row.display_name = display_name[:80]
            row.name_normalized = normalize_name(display_name)
            row.mobile_e164 = mobile_e164
            row.email = email
            row.preferred_language = preferred_language
            row.active = True
            session.flush()
            return row.id


class HubCustomerLookup:
    """Stub. The MCP hub (https://mcphub.sltdigitallab.lk) has no tool that
    looks a customer up by landline, name, mobile or email. Its only entry
    point, validate_customer_and_send_otp, validates a number and sends the
    hub's own code in one call and returns no name or contacts first, so the
    wizard cannot check a name or offer masked methods on it. Always None.
    If the hub ever gains a lookup tool, implement find() here and pass it
    to VerificationService(lookup=...)."""

    def find(self, phone_e164: str) -> Optional[CustomerRecord]:
        return None

    def get(self, record_id: int) -> Optional[CustomerRecord]:
        return None
