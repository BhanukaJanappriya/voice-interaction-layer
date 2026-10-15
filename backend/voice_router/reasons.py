"""Decides whether something that went wrong on a voice tier is a genuine failure.

classify() takes an exception, an HTTP status, a websocket close code or a
reason string and returns Failure(reason, retryable) or NotAFailure(kind).
Only genuine failures send the conversation to the next tier. A safety block,
a refusal, an "I don't know", a hang up, a normal close and a barge in are
valid outcomes: falling back on them would route around a safety decision or
punish the user for ending the call.

The reason strings match what backend/gemini_live/errors.py emits; this module
does not import it. Raw provider text is only inspected, never stored.
"""

from typing import Optional, Union

from .types import (
    BARGE_IN,
    BILLING_OR_QUOTA,
    DONT_KNOW,
    INVALID_KEY,
    MISSING_KEY,
    MODEL_UNAVAILABLE,
    NORMAL_CLOSE,
    OUTAGE,
    REFUSAL,
    REPEATED_FAILURE,
    SAFETY_BLOCK,
    TIMEOUT,
    UNCLASSIFIED,
    USER_CLOSED,
    Failure,
    NotAFailure,
)

Result = Union[Failure, NotAFailure]

_RETRYABLE = {OUTAGE: True, TIMEOUT: True}

# Exact reason names (case, spaces and dashes ignored).
_EXACT_FAILURE = {
    MISSING_KEY, INVALID_KEY, BILLING_OR_QUOTA, MODEL_UNAVAILABLE, OUTAGE, TIMEOUT, REPEATED_FAILURE,
}
_EXACT_NOT = {
    SAFETY_BLOCK: SAFETY_BLOCK,
    USER_CLOSED: USER_CLOSED,
    REFUSAL: REFUSAL,
    DONT_KNOW: DONT_KNOW,
    NORMAL_CLOSE: NORMAL_CLOSE,
    BARGE_IN: BARGE_IN,
    "interrupted": BARGE_IN,
}

_SAFETY_WORDS = ("safety", "prohibited_content", "blocklist", "recitation", "blocked by")
_BILLING_WORDS = (
    "prepayment credits are depleted",
    "credits are depleted",
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "billing",
    "rate limit",
    "payment required",
)
_AUTH_WORDS = (
    "api key not valid",
    "invalid api key",
    "api_key_invalid",
    "unauthenticated",
    "permission_denied",
    "permission denied",
    "unauthorized",
    "forbidden",
)
_MISSING_WORDS = ("no api key", "missing api key", "not configured", "no credentials")
_TIMEOUT_WORDS = ("timed out", "timeout", "deadline exceeded", "deadline_exceeded")
_OUTAGE_WORDS = ("internal error", "service unavailable", "overloaded", "bad gateway", "server error")
_NOT_FOUND_WORDS = ("model not found", "is not found", "not_found", "no such model", "not supported for")
_REFUSAL_WORDS = ("i can't help", "i cannot help", "i can't assist", "i cannot assist", "i'm not able to help")
_DONT_KNOW_WORDS = ("i don't know", "i do not know")


def _failure(reason: str) -> Failure:
    return Failure(reason, _RETRYABLE.get(reason, False))


def _from_code(code: int) -> Optional[Result]:
    """HTTP status or websocket close code."""
    if code in (1000, 1001):
        return NotAFailure(NORMAL_CLOSE)
    if code in (1011, 1012, 1013, 1014):
        return _failure(OUTAGE)
    if code in (401, 403):
        return _failure(INVALID_KEY)
    if code in (402, 429):
        return _failure(BILLING_OR_QUOTA)
    if code == 404:
        return _failure(MODEL_UNAVAILABLE)
    if code in (408, 504):
        return _failure(TIMEOUT)
    if 500 <= code <= 599:
        return _failure(OUTAGE)
    return None


def _from_text(text: str) -> Optional[Result]:
    low = text.strip().lower()
    if not low:
        return None
    if low.isdigit() and len(low) <= 4:
        return _from_code(int(low))
    exact = low.replace("-", "_").replace(" ", "_")
    if exact in _EXACT_FAILURE:
        return _failure(exact)
    if exact in _EXACT_NOT:
        return NotAFailure(_EXACT_NOT[exact])
    if any(word in low for word in _BILLING_WORDS):
        return _failure(BILLING_OR_QUOTA)
    if any(word in low for word in _SAFETY_WORDS):
        return NotAFailure(SAFETY_BLOCK)
    if any(word in low for word in _MISSING_WORDS):
        return _failure(MISSING_KEY)
    if any(word in low for word in _AUTH_WORDS):
        return _failure(INVALID_KEY)
    if any(word in low for word in _NOT_FOUND_WORDS):
        return _failure(MODEL_UNAVAILABLE)
    if any(word in low for word in _TIMEOUT_WORDS):
        return _failure(TIMEOUT)
    if any(word in low for word in _OUTAGE_WORDS):
        return _failure(OUTAGE)
    if any(word in low for word in _REFUSAL_WORDS):
        return NotAFailure(REFUSAL)
    if any(word in low for word in _DONT_KNOW_WORDS):
        return NotAFailure(DONT_KNOW)
    return None


def _int_attr(exc: BaseException) -> Optional[int]:
    for value in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _from_exception(exc: BaseException) -> Result:
    chain: list = []
    current: Optional[BaseException] = exc
    while current is not None and all(current is not seen for seen in chain) and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    # Text first: a close code 1011 whose reason says the credit is gone is a
    # billing problem, not an outage.
    for item in chain:
        found = _from_text(str(item))
        if found is None and isinstance(getattr(item, "reason", None), str):
            found = _from_text(item.reason)
        if found is not None:
            return found
    for item in chain:
        code = _int_attr(item)
        if code is not None:
            found = _from_code(code)
            if found is not None:
                return found
    for item in chain:
        if isinstance(item, TimeoutError) or "timeout" in type(item).__name__.lower():
            return _failure(TIMEOUT)
    for item in chain:
        if isinstance(item, OSError):
            return _failure(OUTAGE)
    return NotAFailure(UNCLASSIFIED)


def classify(item: object) -> Result:
    """Failure(reason, retryable) for a genuine failure, else NotAFailure(kind).
    Never raises."""
    try:
        if isinstance(item, (Failure, NotAFailure)):
            return item
        if item is None:
            return NotAFailure(NORMAL_CLOSE)
        if isinstance(item, bool):
            return NotAFailure(UNCLASSIFIED)
        if isinstance(item, int):
            return _from_code(item) or NotAFailure(UNCLASSIFIED)
        if isinstance(item, str):
            return _from_text(item) or NotAFailure(UNCLASSIFIED)
        if isinstance(item, BaseException):
            return _from_exception(item)
        return NotAFailure(UNCLASSIFIED)
    except Exception:  # noqa: BLE001
        return NotAFailure(UNCLASSIFIED)
