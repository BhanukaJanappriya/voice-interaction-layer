"""A short system prompt clause stating the caller's access level."""

from typing import Optional

from .context import GUEST, AccessContext
from .policy import PolicyError, get_policy

_LANGUAGE_NAMES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}


def _clean(value: Optional[str], limit: int = 60) -> str:
    # The name comes from the database, but keep control characters and
    # newlines out of the prompt anyway.
    return "".join(ch for ch in (value or "") if ch.isprintable())[:limit].strip()


def _mask(phone: Optional[str]) -> str:
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    return "ending " + digits[-3:] if len(digits) >= 3 else "on file"


def level_clause(ctx: Optional[AccessContext], language: Optional[str] = None) -> str:
    ctx = ctx or GUEST
    lang = _LANGUAGE_NAMES.get((language or "").strip().lower()[:2], "")
    reply_in = f" Reply in {lang}." if lang else ""
    if ctx.is_verified:
        name = _clean(ctx.display_name)
        who = f"verified customer {name}" if name else "a verified customer"
        return (
            f"ACCESS LEVEL: verified. The caller is {who} (number {_mask(ctx.phone_e164)}), "
            "confirmed by a one time code. You may discuss this customer's own account data that "
            "tools return. Never discuss any other customer's data." + reply_in
        )
    try:
        dial = get_policy().dial
        hotline = dial["hotline"]
    except (PolicyError, KeyError):
        hotline = "1212"
    return (
        "ACCESS LEVEL: guest. The caller is not verified. You may chat about general topics and give "
        "public information (packages, coverage, how to pay online in general, contact details). "
        "Never state, guess or invent account details, bills, payments, balance, usage, connection "
        "status, faults or package changes. If asked, say verification is needed or they can call the "
        f"hotline {hotline}." + reply_in
    )
