"""Fixed texts of the customer service layer.

canned(reason, language) comes from the i18n keys customer.reauth_needed,
customer.unavailable and customer.changes_not_supported, with a built in
English text when i18n has none. The official numbers always come from the
access policy dial section (the same {hotline}, {sms}, {prepaid} placeholders
as access.customer_service_blocked). The Sinhala and Tamil texts live in
backend/i18n and are machine written.
"""

from typing import Optional

REASONS = ("reauth_needed", "unavailable", "changes_not_supported")

_DEFAULT_DIAL = {"hotline": "1212", "sms": "0113121212", "prepaid": "1222"}

_BUILTIN = {
    "reauth_needed": (
        "To show your account details I need you to verify again. "
        "Please verify with a one time code and then ask me again."
    ),
    "unavailable": (
        "I cannot reach your account details right now. Please try again later, or contact SLT directly: "
        "call {hotline} (toll free from SLT lines), send an SMS to {sms}, or call {prepaid} for prepaid."
    ),
    "changes_not_supported": (
        "I can only show account information here, I cannot make changes. To buy, activate, change or cancel "
        "anything, please call {hotline} (toll free from SLT lines), send an SMS to {sms}, "
        "or call {prepaid} for prepaid."
    ),
}

_LANGUAGE_NAMES = {"en": "English", "si": "Sinhala", "ta": "Tamil"}


def _dial() -> dict:
    try:
        from access.policy import get_policy

        dial = get_policy().dial
        return {k: dial.get(k) or _DEFAULT_DIAL[k] for k in _DEFAULT_DIAL}
    except Exception:  # noqa: BLE001
        return dict(_DEFAULT_DIAL)


def _short(language: Optional[str]) -> str:
    lang = (language or "en").strip().lower()[:2]
    return lang if lang in _LANGUAGE_NAMES else "en"


def canned(reason: str, language: Optional[str] = None) -> str:
    """The fixed reply for reauth_needed, unavailable or changes_not_supported
    (an unknown reason gives the unavailable text)."""
    if reason not in REASONS:
        reason = "unavailable"
    dial = _dial()
    key = f"customer.{reason}"
    try:
        from i18n import t

        text = t(key, _short(language), **dial)
    except Exception:  # noqa: BLE001
        text = key
    needs_numbers = reason != "reauth_needed"
    if not isinstance(text, str) or not text or text == key or (
        needs_numbers and not all(number in text for number in dial.values())
    ):
        text = _BUILTIN[reason].format(**dial)
    return text


def system_clause(language: Optional[str] = None, changes_requested: bool = False) -> str:
    """Instructions appended to the system prompt for a turn that carries
    account data. English on purpose (the model's working language); the reply
    is asked for in the customer's language."""
    name = _LANGUAGE_NAMES[_short(language)]
    lines = [
        "The customer is verified and the SLT ACCOUNT DATA block below belongs to them.",
        "Answer only from that block. If the figure asked for is not in it, say you do not have it and "
        "do not guess or invent amounts, dates, balances or statuses.",
        "Give only what was asked, in short spoken sentences, and say amounts with their unit.",
        "Never read out or repeat the telephone number, account numbers, tokens or codes.",
        "You can only read account information. You cannot buy, activate, change, add, subscribe or cancel anything.",
        f"Reply in {name}.",
    ]
    if changes_requested:
        lines.append(
            "The customer asked for a change. Say plainly that changes cannot be made here and that they are made "
            "through the official numbers: " + canned("changes_not_supported", language)
        )
    return " ".join(lines)
