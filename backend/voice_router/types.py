"""Shared types for the voice tier router.

Tier, HandoverState and the outcome classes are the contract between the
router, the Gemini Live session and the server wiring.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class Tier(str, Enum):
    GEMINI_LIVE = "gemini_live"
    LIVEKIT = "livekit"
    LOCAL = "local"


DEFAULT_ORDER = (Tier.GEMINI_LIVE, Tier.LIVEKIT, Tier.LOCAL)

# Reason class names. The same strings are emitted by the Gemini Live session.
MISSING_KEY = "missing_key"
INVALID_KEY = "invalid_key"
BILLING_OR_QUOTA = "billing_or_quota"
MODEL_UNAVAILABLE = "model_unavailable"
OUTAGE = "outage"
TIMEOUT = "timeout"
REPEATED_FAILURE = "repeated_failure"

FAILURE_REASONS = (MISSING_KEY, INVALID_KEY, BILLING_OR_QUOTA, MODEL_UNAVAILABLE, OUTAGE, TIMEOUT, REPEATED_FAILURE)

# Kinds of NotAFailure.
SAFETY_BLOCK = "safety_block"
REFUSAL = "refusal"
DONT_KNOW = "dont_know"
USER_CLOSED = "user_closed"
NORMAL_CLOSE = "normal_close"
BARGE_IN = "barge_in"
UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Failure:
    """A genuine reason a tier could not serve: the next tier should."""

    reason: str
    retryable: bool = False


@dataclass(frozen=True)
class NotAFailure:
    """The tier did its job (or the user ended it). Never causes a fallback.
    kind == "unclassified" means the cause is unknown; the router counts those
    toward repeated_failure but never falls back on one alone."""

    kind: str = NORMAL_CLOSE


@dataclass(frozen=True)
class Outcome:
    """What happened on a tier: ok, or a Failure / NotAFailure."""

    ok: bool = True
    failure: Optional[Failure] = None
    not_failure: Optional[NotAFailure] = None

    @staticmethod
    def success() -> "Outcome":
        return Outcome(ok=True)

    @staticmethod
    def failed(failure: Failure) -> "Outcome":
        return Outcome(ok=False, failure=failure)

    @staticmethod
    def neutral(item: NotAFailure) -> "Outcome":
        return Outcome(ok=False, not_failure=item)


@dataclass
class HandoverState:
    """Everything the next tier needs to carry on the same conversation."""

    language: str = "en"
    voice_style: str = "female_natural"
    access: Any = None  # opaque: the caller's AccessContext, kept by identity
    summary: str = ""
    pending_confirmation: Optional[dict] = None
