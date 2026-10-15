"""Maps google-genai errors, websocket close codes and server texts to a
reason class.

A reason class is a short word the voice router can act on. Some are genuine
failures (the next tier should take over), some are not (a safety block, a
refusal, a normal close, the user hanging up). Nothing here logs; the detail
text is only ever matched, never stored with a key or audio.

    classify(exc_or_code, detail="") -> Outcome
    classify_turn_reason(value)      -> Outcome or None    (turn_complete_reason)
    FailureWindow                    repeated_failure after N failures in M seconds
"""

import asyncio
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

MISSING_KEY = "missing_key"
INVALID_KEY = "invalid_key"
BILLING_OR_QUOTA = "billing_or_quota"
MODEL_UNAVAILABLE = "model_unavailable"
OUTAGE = "outage"
TIMEOUT = "timeout"
REPEATED_FAILURE = "repeated_failure"
BAD_REQUEST = "bad_request"  # our own configuration was rejected; extra class, a genuine failure
SAFETY_BLOCK = "safety_block"
USER_CLOSED = "user_closed"

GENUINE = frozenset({MISSING_KEY, INVALID_KEY, BILLING_OR_QUOTA, MODEL_UNAVAILABLE, OUTAGE, TIMEOUT, REPEATED_FAILURE, BAD_REQUEST})
NOT_FAILURES = frozenset({SAFETY_BLOCK, USER_CLOSED})

_BILLING = re.compile(r"prepayment|credits? (are |is )?(depleted|exhausted)|billing|quota|resource[_ ]exhausted|rate limit|payment required|insufficient", re.I)
_KEY = re.compile(r"api key|api_key|unauthenticated|permission[_ ]denied|invalid[_ ]credential|forbidden|unauthori[sz]ed", re.I)
_MODEL = re.compile(r"not[_ ]found|is not found|not supported for|model .*(unavailable|not available|does not exist)|unknown model", re.I)
_SAFETY = re.compile(r"safety|prohibited|blocked|blocklist|recitation", re.I)
_OUTAGE = re.compile(r"unavailable|internal|overloaded|deadline|service|temporar", re.I)


@dataclass(frozen=True)
class Outcome:
    reason_class: str
    detail: str = ""

    @property
    def genuine(self) -> bool:
        """True when this should make the router move to the next tier."""
        return self.reason_class in GENUINE


def is_failure(reason_class: str) -> bool:
    return reason_class in GENUINE


def _text_class(text: str) -> Optional[str]:
    if not text:
        return None
    if _BILLING.search(text):
        return BILLING_OR_QUOTA
    if _KEY.search(text):
        return INVALID_KEY
    if _MODEL.search(text):
        return MODEL_UNAVAILABLE
    return None


def _from_http(code: int, text: str) -> str:
    if code == 402 or code == 429:
        return BILLING_OR_QUOTA
    if code in (401, 403):
        return _text_class(text) if _text_class(text) == BILLING_OR_QUOTA else INVALID_KEY
    if code == 404:
        return MODEL_UNAVAILABLE
    if code == 400:
        return _text_class(text) or BAD_REQUEST
    if code in (408, 504):
        return TIMEOUT
    if code >= 500:
        return OUTAGE
    return _text_class(text) or OUTAGE


def _from_close(code: int, text: str) -> str:
    """WebSocket close codes. The text wins over the code: the Live API closes
    an out of credit session with 1011, which is a billing matter."""
    hinted = _text_class(text)
    if hinted:
        return hinted
    if code in (1000, 1005):
        return USER_CLOSED
    if _SAFETY.search(text or ""):
        return SAFETY_BLOCK
    if code == 1008:  # policy violation: usually a key or model problem, never safe to retry
        return INVALID_KEY if _KEY.search(text or "") else BAD_REQUEST
    if code in (1001, 1006, 1011, 1012, 1013, 1014, 1015):
        return OUTAGE
    return OUTAGE


def classify(error: Any, detail: str = "") -> Outcome:
    """error may be an exception, an int (http status or close code), a str
    (the server's close text) or None (a clean end)."""
    text = str(detail or "")
    # An int: 1000..1015 are close codes, anything else is an http status.
    if isinstance(error, bool):
        return Outcome(OUTAGE, text)
    if isinstance(error, int):
        reason = _from_close(error, text) if 1000 <= error <= 1015 else _from_http(error, text)
        return Outcome(reason, text[:200])
    if error is None:
        return Outcome(USER_CLOSED, text[:200])
    if isinstance(error, str):
        reason = _text_class(error) or (SAFETY_BLOCK if _SAFETY.search(error) else OUTAGE)
        return Outcome(reason, error[:200])
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        return Outcome(TIMEOUT, text[:200])
    if isinstance(error, asyncio.CancelledError):
        return Outcome(USER_CLOSED, text[:200])

    name = type(error).__name__
    # websockets ConnectionClosed (rcvd / sent close frames) without importing it.
    rcvd = getattr(error, "rcvd", None) or getattr(error, "sent", None)
    code = getattr(rcvd, "code", None) if rcvd is not None else getattr(error, "code", None)
    reason_text = (getattr(rcvd, "reason", "") if rcvd is not None else "") or str(error)
    if name.startswith("ConnectionClosed") and isinstance(code, int):
        return Outcome(_from_close(code, reason_text), reason_text[:200])
    # google.genai.errors.APIError family: code is the http status.
    if name in ("ClientError", "ServerError", "APIError") or (isinstance(code, int) and hasattr(error, "status")):
        status = getattr(error, "code", None)
        message = str(error) + " " + str(getattr(error, "message", "") or "") + " " + str(getattr(error, "status", "") or "") + " " + text
        if isinstance(status, int):
            if 1000 <= status <= 1015:
                return Outcome(_from_close(status, message), message.strip()[:200])
            return Outcome(_from_http(status, message), message.strip()[:200])
    if isinstance(error, (ConnectionError, OSError)):
        return Outcome(_text_class(str(error)) or OUTAGE, str(error)[:200])
    if isinstance(error, (ValueError, TypeError)):
        return Outcome(BAD_REQUEST, str(error)[:200])
    return Outcome(_text_class(str(error)) or OUTAGE, str(error)[:200])


def classify_turn_reason(value: Any) -> Optional[Outcome]:
    """turn_complete_reason on a server_content message. A safety or content
    rejection is a normal end of a turn, never a failure. None when the value
    says nothing special."""
    name = str(getattr(value, "value", value) or "")
    if not name or name in ("TURN_COMPLETE_REASON_UNSPECIFIED", "NEED_MORE_INPUT"):
        return None
    if _SAFETY.search(name) or "RESPONSE_REJECTED" in name or "PROHIBITED" in name or "BLOCKLIST" in name:
        return Outcome(SAFETY_BLOCK, name)
    return None


class FailureWindow:
    """repeated_failure: `limit` failures within `seconds`."""

    def __init__(self, limit: int = 3, seconds: float = 300.0, clock=time.monotonic):
        self.limit = limit
        self.seconds = seconds
        self._clock = clock
        self._times: Deque[float] = deque()

    def record(self) -> bool:
        """Records one failure; True when the window is now full."""
        now = self._clock()
        self._times.append(now)
        while self._times and now - self._times[0] > self.seconds:
            self._times.popleft()
        return len(self._times) >= self.limit

    def reset(self) -> None:
        self._times.clear()
