"""Where a customer's one time codes go: the users.mobile_e164 and
users.email columns (added by revision 0002).

Kept apart from service.py so an operator script or an import job can set
contact details without pulling in the whole sign in service.
"""

import re
from typing import Optional

import sqlalchemy as sa

from auth.phone import normalize_sl_landline
from db.base import session_scope
from otp.tables import users

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")


def normalize_mobile(value: str) -> str:
    """+947XXXXXXXX from 07XXXXXXXX, 947XXXXXXXX or +947XXXXXXXX (spaces
    and dashes ignored). Raises ValueError for anything else."""
    digits = re.sub(r"\D", "", value or "")
    if digits.startswith("0094"):
        digits = digits[4:]
    elif digits.startswith("94"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if not re.fullmatch(r"7\d{8}", digits):
        raise ValueError("A mobile number looks like 071 234 5678.")
    return f"+94{digits}"


def normalize_email(value: str) -> str:
    value = (value or "").strip()
    if not _EMAIL.match(value):
        raise ValueError("That does not look like an email address.")
    return value


def set_contact(phone: str, mobile: Optional[str] = None, email: Optional[str] = None) -> bool:
    """Sets the mobile and/or email of the customer with this landline.
    None leaves a field as it is, "" clears it. Returns False when there is
    no such customer. Raises ValueError for a malformed value."""
    phone_e164 = normalize_sl_landline(phone)
    changes: dict = {}
    if mobile is not None:
        changes["mobile_e164"] = normalize_mobile(mobile) if mobile else None
    if email is not None:
        changes["email"] = normalize_email(email) if email else None
    if not changes:
        return True
    with session_scope() as session:
        return session.execute(sa.update(users).where(users.c.phone_e164 == phone_e164).values(**changes)).rowcount > 0
