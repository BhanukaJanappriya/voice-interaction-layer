"""Why a language model tier could not answer, in a form the chain can act on.

Two outcomes are kept strictly apart. TierUnavailable means the provider could
not do the job (bad key, no credit, outage, timeout), so the next tier should
get the turn. SafetyBlocked means the provider did its job and declined the
question; that is a valid answer, and asking another model the same question
would route around a safety decision, so it never causes a fallback.

Messages here are fixed sentences built from the reason and a status code. The
original exception text is never copied in: provider errors can echo request
details, and user speech or keys must not reach logs or /health.

This module imports agent at the top, so agent.py must import llm_chain lazily
(inside a function), never at its own top level.
"""

import socket
from typing import Optional

from agent import CloudUnavailableError

MISSING_KEY = "missing_key"
AUTH = "auth"
QUOTA = "quota"
NOT_FOUND = "not_found"
OUTAGE = "outage"
TIMEOUT = "timeout"
NETWORK = "network"
OTHER = "other"

REASONS = (MISSING_KEY, AUTH, QUOTA, NOT_FOUND, OUTAGE, TIMEOUT, NETWORK, OTHER)

_REASON_TEXT = {
    MISSING_KEY: "no API key or credentials are configured",
    AUTH: "the key was refused (invalid, expired or not allowed)",
    QUOTA: "out of credit or rate limited",
    NOT_FOUND: "the model or endpoint was not found",
    OUTAGE: "the provider had a server error",
    TIMEOUT: "the provider took too long to answer",
    NETWORK: "the provider could not be reached",
    OTHER: "the call failed for an unexpected reason",
}


class TierUnavailable(CloudUnavailableError):
    """A tier could not produce a reply. Subclasses CloudUnavailableError so
    code written for the old cloud path (the Orchestrator) still catches it."""

    def __init__(self, reason: str, message: Optional[str] = None):
        self.reason = reason if reason in REASONS else OTHER
        super().__init__(message or _REASON_TEXT[self.reason])


class SafetyBlocked(Exception):
    """The provider refused the question for safety reasons. A valid answer."""


def _status_of(exc: BaseException) -> Optional[int]:
    """The HTTP status an SDK exception carries, if any. The OpenAI SDK sets
    status_code, google-genai sets code, and some wrappers keep the response."""
    candidates = (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    )
    for value in candidates:
        # bool is an int subclass; a flag must never read as a status.
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _reason_for_status(status: int) -> Optional[str]:
    if status in (401, 403):
        return AUTH
    if status in (402, 429):
        return QUOTA
    if status == 404:
        return NOT_FOUND
    if status >= 500:
        return OUTAGE
    return None


def _reason_for_type(exc: BaseException) -> Optional[str]:
    name = type(exc).__name__.lower()
    if isinstance(exc, (TimeoutError, socket.timeout)) or "timeout" in name or "timedout" in name:
        return TIMEOUT
    if isinstance(exc, (ConnectionError, socket.gaierror)) or any(
        word in name for word in ("connection", "connect", "network", "dns", "ssl")
    ):
        return NETWORK
    return None


def reason_from_exception(exc: BaseException) -> str:
    """Classifies an exception by walking its cause chain (the agents wrap the
    SDK error in a CloudUnavailableError with `from exc`). A status code wins
    over the exception type, since a timeout wrapper around a 429 is a quota
    problem to fix, not a slow network."""
    if isinstance(exc, TierUnavailable):
        return exc.reason
    chain = []
    seen = set()
    current: Optional[BaseException] = exc
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__

    for item in chain:
        status = _status_of(item)
        if status is not None:
            reason = _reason_for_status(status)
            if reason is not None:
                return reason
    for item in chain:
        reason = _reason_for_type(item)
        if reason is not None:
            return reason
    return OTHER


def unavailable_from_exception(exc: BaseException) -> TierUnavailable:
    """Builds a TierUnavailable whose message holds only the reason and, when
    there is one, the status code."""
    if isinstance(exc, TierUnavailable):
        return exc
    reason = reason_from_exception(exc)
    status = None
    current: Optional[BaseException] = exc
    for _ in range(8):
        if current is None:
            break
        status = _status_of(current)
        if status is not None:
            break
        current = current.__cause__ or current.__context__
    text = _REASON_TEXT[reason]
    if status is not None:
        text = f"{text} (HTTP {status})"
    return TierUnavailable(reason, text)
