"""The language model fallback chain: gemini, then livekit, then local.

Rules the rest of the app can rely on:

* All or nothing per turn. A tier that fails before its first piece of text
  trips its own breaker and the next tier receives the whole turn. Text from
  two models is never mixed.
* A failure after text has started is not retried elsewhere (the user is
  already hearing the answer); a short apology is added instead.
* A safety block or refusal is a valid answer: the turn ends with a notice and
  no other tier is asked.
* The local tier is the last resort and ignores its breaker.

Nothing here logs or stores user text, keys or provider error bodies, only the
tier name, the failure reason and timings.
"""

import math
import threading
import time
from typing import Callable, Iterator, Optional

import config

from .breaker import Breaker
from .errors import SafetyBlocked, TierUnavailable, unavailable_from_exception
from .tiers import LOCAL, Tier, Turn, default_tiers

APOLOGY = " Sorry, I lost my connection there. Could you ask that again?"
SAFETY_NOTICE = "Sorry, I can't help with that request."
UNAVAILABLE_NOTICE = "Sorry, I can't reach my language service right now. Please try again in a moment."
_NOT_CONFIGURED = "no API key or credentials are configured"

SafetyMessage = Callable[[Optional[str]], str]


def _localized(key: str, language: Optional[str], default: str) -> str:
    """The text for `key` in `language` from the i18n module; `default` (the English
    constant) if the lookup fails or the key is missing."""
    try:
        import i18n  # type: ignore

        text = i18n.t(key, language)
        if isinstance(text, str) and text and text != key:
            return text
    except Exception:  # noqa: BLE001
        pass
    return default


def _default_safety_message(language: Optional[str]) -> str:
    """The localized notice (i18n key llm.safety_blocked) from the i18n module; the
    English sentence if the lookup fails."""
    return safety_notice(language)


def apology(language: Optional[str]) -> str:
    return " " + _localized("llm.lost_connection", language, APOLOGY.strip())


def safety_notice(language: Optional[str]) -> str:
    return _localized("llm.safety_blocked", language, SAFETY_NOTICE)


def unavailable_notice(language: Optional[str]) -> str:
    return _localized("llm.unavailable", language, UNAVAILABLE_NOTICE)


class LlmChain:
    def __init__(
        self,
        order: Optional[list[str]] = None,
        tiers: Optional[dict[str, Tier]] = None,
        breaker_seconds: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        safety_message: Optional[SafetyMessage] = None,
    ):
        wanted = list(order) if order is not None else list(config.LLM_CHAIN)
        self.tiers: dict[str, Tier] = tiers if tiers is not None else default_tiers()
        unknown = [name for name in wanted if name not in self.tiers]
        if unknown:
            print(f"LLM chain: ignoring unknown tier names {unknown}.")
        self.order = [name for name in dict.fromkeys(wanted) if name in self.tiers]
        seconds = config.LLM_BREAKER_SECONDS if breaker_seconds is None else breaker_seconds
        self.breakers = {name: Breaker(seconds, clock) for name in self.order}
        self._safety_message = safety_message or _default_safety_message
        self._lock = threading.Lock()
        self.last_mode: str = "unknown"
        self.last_errors: dict[str, Optional[str]] = {name: None for name in self.order}
        # Seconds to the first piece of text, per tier, for its last success.
        self.latencies: dict[str, float] = {}

    # Turn handling

    def respond(self, turn: Turn) -> str:
        return "".join(self.stream(turn)).strip()

    def respond_stream(self, turn: Turn) -> Iterator[str]:
        return self.stream(turn)

    def stream(self, turn: Turn) -> Iterator[str]:
        for name in self.order:
            tier = self.tiers[name]
            breaker = self.breakers[name]
            last_resort = name == LOCAL

            if turn.private and tier.cloud:
                # Customer account data never goes to a hosted model. This is not
                # a failure, so the tier's error and breaker are left alone.
                print(f"LLM chain: skipping {name}, this turn carries customer data.")
                continue
            if not tier.configured():
                self._record_error(name, _NOT_CONFIGURED)
                continue
            if not last_resort and breaker.open():
                print(f"LLM chain: skipping {name}, retry in {breaker.seconds_left():.0f}s.")
                continue

            start = time.perf_counter()
            pieces: Optional[Iterator[str]] = None
            first = ""
            try:
                pieces = iter(tier.stream(turn))
                first = self._first_piece(pieces)
            except SafetyBlocked:
                self._succeed(name, tier, time.perf_counter() - start)
                yield self._safety_message(turn.language)
                return
            except Exception as exc:  # noqa: BLE001
                self._fail(name, breaker, unavailable_from_exception(exc), time.perf_counter() - start, last_resort)
                self._close(pieces)
                continue

            if not first:
                elapsed = time.perf_counter() - start
                if last_resort:
                    # Nothing came back and there is nowhere left to go.
                    self._succeed(name, tier, elapsed)
                    return
                self._fail(name, breaker, TierUnavailable("other", "the provider returned no text"), elapsed, last_resort)
                continue

            self._succeed(name, tier, time.perf_counter() - start)
            try:
                yield first
                yield from pieces
            except SafetyBlocked:
                yield " " + self._safety_message(turn.language)
            except Exception as exc:  # noqa: BLE001
                # Mid reply: switching models now would sound worse than
                # admitting the drop. The breaker is not tripped, because a
                # dropped stream is often a one off.
                self._record_error(name, unavailable_from_exception(exc).args[0])
                print(f"LLM chain: {name} stream dropped mid reply.")
                yield apology(turn.language)
            finally:
                self._close(pieces)
            return

        self.last_mode = "unavailable"
        print("LLM chain: no tier could answer this turn.")
        yield unavailable_notice(turn.language)

    @staticmethod
    def _first_piece(pieces: Iterator[str]) -> str:
        for piece in pieces:
            if piece:
                return piece
        return ""

    @staticmethod
    def _close(pieces: Optional[Iterator[str]]) -> None:
        close = getattr(pieces, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    def _record_error(self, name: str, message: Optional[str]) -> None:
        with self._lock:
            self.last_errors[name] = message

    def _succeed(self, name: str, tier: Tier, elapsed: float) -> None:
        self.breakers[name].reset()
        with self._lock:
            self.last_errors[name] = None
            self.latencies[name] = elapsed
            self.last_mode = tier.label()
        print(f"LLM chain: {name} first text after {elapsed:.2f}s ({self.last_mode}).")

    def _fail(self, name: str, breaker: Breaker, failure: TierUnavailable, elapsed: float, last_resort: bool) -> None:
        if not last_resort:
            breaker.trip(failure.reason)
        self._record_error(name, failure.args[0])
        print(f"LLM chain: {name} unavailable ({failure.reason}) after {elapsed:.2f}s.")

    # Health

    def status(self) -> dict:
        tiers = {}
        for name in self.order:
            tier = self.tiers[name]
            configured = tier.configured()
            left = 0.0 if name == LOCAL else self.breakers[name].seconds_left()
            if not configured:
                state = "not_configured"
            elif left > 0:
                state = "tripped"
            else:
                state = "ready"
            tiers[name] = {
                "label": tier.label(),
                "configured": configured,
                "state": state,
                "last_error": self.last_errors.get(name),
                "retry_in": math.ceil(left),
            }
        return {"order": list(self.order), "last_mode": self.last_mode, "tiers": tiers}

    def probe_all(self) -> dict:
        """One tiny real call per configured tier, for startup: a dead key is
        found now and its breaker tripped, instead of on the first question."""
        for name in self.order:
            tier = self.tiers[name]
            if not tier.configured():
                self._record_error(name, _NOT_CONFIGURED)
                continue
            start = time.perf_counter()
            try:
                tier.probe()
            except Exception as exc:  # noqa: BLE001
                self._fail(name, self.breakers[name], unavailable_from_exception(exc), time.perf_counter() - start, name == LOCAL)
            else:
                self.breakers[name].reset()
                self._record_error(name, None)
                print(f"LLM chain: probe {name} ok in {time.perf_counter() - start:.2f}s.")
        return self.status()
