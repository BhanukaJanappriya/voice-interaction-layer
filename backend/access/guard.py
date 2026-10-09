"""The policy guard that runs before any model sees a guest's message.

Order for a guest:
1. public overrides: general information phrases (how to pay online,
   contact numbers, package prices) are cut out of the text first;
2. keyword and regex rules per topic (English, Sinhala, Tamil, and the
   Latin letter forms), run on whatever is left;
3. the heuristic classifier, only when no rule matched;
4. any error blocks the guest (fail closed).
A verified user is always allowed. The declared language is not used for
matching on purpose: people mix languages and scripts in one sentence.
"""

import time
from dataclasses import dataclass
from typing import Optional

from . import classifier
from .context import GUEST, AccessContext
from .policy import get_policy, normalize

BLOCKED_KEY = "access.customer_service_blocked"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    topic: Optional[str]
    source: str  # verified, empty, override, rule, classifier, default, error
    message_key: Optional[str]
    elapsed_ms: float


def _decision(allowed: bool, topic: Optional[str], source: str, started: float) -> Decision:
    return Decision(
        allowed,
        topic,
        source,
        None if allowed else BLOCKED_KEY,
        (time.perf_counter() - started) * 1000.0,
    )


def evaluate(text: str, language: Optional[str] = None, ctx: Optional[AccessContext] = None) -> Decision:
    started = time.perf_counter()
    ctx = ctx or GUEST
    if ctx.is_verified:
        return _decision(True, None, "verified", started)
    try:
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        policy = get_policy()
        rest = normalize(text)
        if not rest:
            return _decision(True, None, "empty", started)
        overridden = False
        for override in policy.overrides:
            rest, count = override.subn(" ", rest)
            overridden = overridden or count > 0
        for topic in policy.topics:
            if topic.matcher is not None and topic.matcher.search(rest):
                return _decision(False, topic.id, "rule", started)
            for rx in topic.regexes:
                if rx.search(rest):
                    return _decision(False, topic.id, "rule", started)
        verdict = classifier.classify(rest, policy.threshold)
        if verdict.needs_customer_data:
            return _decision(False, "classifier", "classifier", started)
        return _decision(True, None, "override" if overridden else "default", started)
    except Exception as exc:  # noqa: BLE001 - fail closed for guests
        print(f"Access guard error, blocking the guest: {type(exc).__name__}: {exc}")
        return _decision(False, "error", "error", started)
