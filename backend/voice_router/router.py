"""The voice tier state machine: gemini_live > livekit > local.

    router = VoiceRouter()
    tier = router.pick(state)        # the best tier that is configured and not tripped
    router.report(tier, outcome)     # tell it what happened (an Outcome, Failure,
                                     # NotAFailure, exception, status code or reason)
    router.status()                  # secret free dict for /health
    router.probe_all()               # cheap checks, trips or closes each breaker

Rules: a tier is tried only after every tier above it failed or is tripped or
is not configured. A genuine failure trips that tier's breaker for
breaker_seconds (llm_chain.breaker.Breaker, the same class the text chain
uses); when the window ends the next pick() returns the higher tier again, and
a success closes the breaker for good. Unknown errors are not genuine
failures, but 3 of them inside 5 minutes count as repeated_failure. The local
tier never trips: it is the last resort and always offered.
"""

import threading
import time
from collections import deque
from typing import Callable, Optional

from llm_chain.breaker import Breaker

from .reasons import classify
from .types import (
    DEFAULT_ORDER,
    FAILURE_REASONS,
    REPEATED_FAILURE,
    UNCLASSIFIED,
    Failure,
    NotAFailure,
    Outcome,
    Tier,
)

REPEAT_LIMIT = 3
REPEAT_WINDOW_SECONDS = 300.0
DEFAULT_BREAKER_SECONDS = 60.0

READY = "ready"
TRIPPED = "tripped"
NOT_CONFIGURED = "not_configured"


def _default_breaker_seconds() -> float:
    try:
        import config

        return float(getattr(config, "VOICE_ROUTER_BREAKER_SECONDS", DEFAULT_BREAKER_SECONDS))
    except Exception:  # noqa: BLE001
        return DEFAULT_BREAKER_SECONDS


def _default_configured() -> "dict[Tier, Callable[[], bool]]":
    def gemini() -> bool:
        import config

        return bool(getattr(config, "GEMINI_API_KEY", ""))

    def livekit() -> bool:
        import config

        return bool(config.livekit_configured())

    return {Tier.GEMINI_LIVE: gemini, Tier.LIVEKIT: livekit, Tier.LOCAL: lambda: True}


def _as_outcome(item: object) -> Outcome:
    if isinstance(item, Outcome):
        return item
    if item is True:
        return Outcome.success()
    result = classify(item)
    if isinstance(result, Failure):
        return Outcome.failed(result)
    return Outcome.neutral(result)


class _TierState:
    def __init__(self, breaker: Breaker):
        self.breaker = breaker
        self.last_error: Optional[str] = None
        self.last_ok_at: Optional[float] = None
        self.errors: "deque[float]" = deque()


class VoiceRouter:
    def __init__(
        self,
        order=DEFAULT_ORDER,
        breaker_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        probes: "Optional[dict[Tier, Callable[[], object]]]" = None,
        configured: "Optional[dict[Tier, Callable[[], bool]]]" = None,
    ):
        tiers = [Tier(item) for item in order]
        if Tier.LOCAL in tiers:
            tiers.remove(Tier.LOCAL)
        self.order = tuple(tiers) + (Tier.LOCAL,)  # local is always the last resort
        seconds = _default_breaker_seconds() if breaker_seconds is None else float(breaker_seconds)
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.RLock()
        self._probes = dict(probes or {})
        self._configured = _default_configured()
        self._configured.update(configured or {})
        self._state = {tier: _TierState(Breaker(seconds, clock)) for tier in self.order}

    # Selection

    def _is_configured(self, tier: Tier) -> bool:
        try:
            return bool(self._configured[tier]())
        except Exception:  # noqa: BLE001
            return False

    def _usable(self, tier: Tier) -> bool:
        if tier is Tier.LOCAL:
            return True
        return self._is_configured(tier) and not self._state[tier].breaker.open()

    def pick(self, state=None) -> Tier:
        """The highest tier that is configured and not tripped. `state` (a
        HandoverState) is accepted for the caller's convenience and not used to
        choose: the choice depends only on tier health."""
        with self._lock:
            for tier in self.order:
                if self._usable(tier):
                    return tier
        return Tier.LOCAL

    # Reporting

    def report(self, tier, outcome) -> None:
        """Records what happened on a tier. Never raises."""
        try:
            self._report(Tier(tier), _as_outcome(outcome))
        except Exception:  # noqa: BLE001
            pass

    def _report(self, tier: Tier, outcome: Outcome) -> None:
        with self._lock:
            entry = self._state[tier]
            if outcome.ok:
                entry.breaker.reset()
                entry.last_error = None
                entry.last_ok_at = self._wall()
                entry.errors.clear()
                return
            if outcome.failure is not None:
                self._fail(tier, outcome.failure.reason)
                return
            kind = outcome.not_failure.kind if outcome.not_failure else UNCLASSIFIED
            if kind != UNCLASSIFIED:
                return  # a valid outcome: no state change at all
            now = self._clock()
            entry.errors.append(now)
            while entry.errors and now - entry.errors[0] > REPEAT_WINDOW_SECONDS:
                entry.errors.popleft()
            if len(entry.errors) >= REPEAT_LIMIT:
                entry.errors.clear()
                self._fail(tier, REPEATED_FAILURE)

    def _fail(self, tier: Tier, reason: str) -> None:
        entry = self._state[tier]
        entry.last_error = reason if reason in FAILURE_REASONS else "outage"
        if tier is not Tier.LOCAL:  # the last resort never trips
            entry.breaker.trip(entry.last_error)

    # Probing

    def probe(self, tier) -> bool:
        """Runs one tier's probe (an injected callable that raises on failure).
        Returns True when the tier is usable. A tier with no probe, or one that
        is not configured, is reported without a call."""
        tier = Tier(tier)
        if tier is not Tier.LOCAL and not self._is_configured(tier):
            return False
        probe = self._probes.get(tier)
        if probe is None:
            return True
        try:
            probe()
        except Exception as exc:  # noqa: BLE001
            found = classify(exc)
            self.report(tier, found if isinstance(found, Failure) else Failure("outage", True))
            return False
        self.report(tier, Outcome.success())
        return True

    def probe_all(self) -> dict:
        """Probes every tier; returns {tier name: bool}."""
        return {tier.value: self.probe(tier) for tier in self.order}

    # Status

    def status(self) -> dict:
        """Secret free: only tier names, states, reason class names and times."""
        with self._lock:
            tiers = {}
            for tier in self.order:
                entry = self._state[tier]
                if tier is not Tier.LOCAL and not self._is_configured(tier):
                    state = NOT_CONFIGURED
                elif tier is not Tier.LOCAL and entry.breaker.open():
                    state = TRIPPED
                else:
                    state = READY
                tiers[tier.value] = {
                    "state": state,
                    "last_error": entry.last_error,
                    "last_ok_at": entry.last_ok_at,
                    "retry_in": round(entry.breaker.seconds_left(), 1) if state == TRIPPED else 0,
                }
            return {"order": [t.value for t in self.order], "active": self.pick().value, "tiers": tiers}
