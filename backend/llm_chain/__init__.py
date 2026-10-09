"""Ordered language model fallback: gemini, livekit, local (see chain.py).

agent.py must import this package lazily, because errors.py imports agent.
"""

from .breaker import Breaker
from .chain import APOLOGY, LlmChain
from .errors import SafetyBlocked, TierUnavailable, reason_from_exception
from .tiers import GeminiTier, LiveKitTier, LocalTier, Tier, Turn

__all__ = [
    "APOLOGY",
    "Breaker",
    "GeminiTier",
    "LiveKitTier",
    "LlmChain",
    "LocalTier",
    "SafetyBlocked",
    "Tier",
    "TierUnavailable",
    "Turn",
    "reason_from_exception",
]
