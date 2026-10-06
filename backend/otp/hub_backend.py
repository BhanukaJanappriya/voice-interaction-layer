"""One time codes through the SLT MCP hub (OTP_BACKEND=hub or auto).

The hub does the customer lookup AND sends the code itself:
    validate_customer_and_send_otp(telephone_no, otp_media=2)
        looks the customer up and sends them a code; answers with a
        session id.
    verify_otp_and_authenticate(telephone_no, session_id, otp_value)
        checks the code for that session.
Only the argument names and defaults are known (from the hub's tool list).
The RESPONSE shapes are NOT: nothing here could be tried against the real
hub without sending a real customer a real SMS, so parsing is defensive
and was verified against the mock hub only.

Rules the parsing follows:
  * JSON text or structured content are both accepted, also wrapped as
    {"data": ...}, {"result": ...} or {"customer": ...}.
  * A session id is looked for under session_id, sessionId, session, sid.
  * A masked destination is used only if the hub sends one that really is
    masked (at most 5 digits visible (07 plus the last three) and mask characters present); otherwise
    the page shows a generic "your registered mobile number". A number is
    never taken from the hub and shown unmasked.
  * Failure is explicit (a false success/authenticated flag, a failure
    status word, or an error field). For the request that means "not a
    customer", which the service turns into a decoy challenge. For the
    verify it means a wrong code.
  * A response that is neither clearly a success nor clearly a failure
    raises HubOtpUnknownResponse. It is never guessed at, and the service
    answers 503 for it.

The format of telephone_no is not documented either; HubOtpBackend sends
the national form 0112345678 by default (phone_format="national"; "e164"
and "msisdn" are available). That default is unverified.
"""

import json
from dataclasses import dataclass
from typing import Any, Optional

from mcp_client.errors import HubError

TOOL_REQUEST = "validate_customer_and_send_otp"
TOOL_VERIFY = "verify_otp_and_authenticate"
GENERIC_DESTINATION = "your registered mobile number"

_SESSION_KEYS = ("session_id", "sessionid", "session", "sid")
_MASK_KEYS = ("masked_destination", "masked_mobile", "masked_number", "masked_telephone", "masked_phone", "destination", "mobile")
_NAME_KEYS = ("display_name", "customer_name", "name", "full_name")
_FLAG_KEYS = ("success", "ok", "authenticated", "verified", "valid")
_FAILURE_WORDS = {"error", "failed", "failure", "fail", "not_found", "notfound", "invalid", "denied", "false", "unauthorized"}
_SUCCESS_WORDS = {"success", "ok", "sent", "verified", "authenticated", "valid", "true", "200"}
_WRAPPERS = ("data", "result", "customer", "response", "payload")


class HubOtpUnknownResponse(Exception):
    """The hub answered in a shape this code does not recognise."""


@dataclass(frozen=True)
class HubOtpStart:
    session_id: str
    masked_destination: Optional[str]  # None: show GENERIC_DESTINATION
    display_name: Optional[str]


def _payload(output) -> Any:
    structured = getattr(output, "structured", None)
    if isinstance(structured, dict) and set(structured) == {"result"}:
        structured = structured["result"]
    if isinstance(structured, (dict, list)) and structured:
        return structured
    text = (getattr(output, "text", "") or "").strip()
    if text[:1] in "{[":
        try:
            return json.loads(text)
        except ValueError:
            pass
    if isinstance(structured, str) and structured.strip()[:1] in "{[":
        try:
            return json.loads(structured)
        except ValueError:
            pass
    return None


def _dicts(value: Any, depth: int = 0):
    """The payload and the dicts nested under the usual wrappers."""
    if depth > 3:
        return
    if isinstance(value, list):
        if value:
            yield from _dicts(value[0], depth + 1)
        return
    if not isinstance(value, dict):
        return
    yield value
    lowered = {str(k).lower(): v for k, v in value.items()}
    for key in _WRAPPERS:
        if key in lowered:
            yield from _dicts(lowered[key], depth + 1)


def _find(value: Any, keys: tuple) -> Any:
    for record in _dicts(value):
        lowered = {str(k).lower(): v for k, v in record.items()}
        for key in keys:
            found = lowered.get(key)
            if found not in (None, "", [], {}):
                return found
    return None


def _verdict(value: Any) -> Optional[bool]:
    """True success, False failure, None unrecognised."""
    for record in _dicts(value):
        lowered = {str(k).lower(): v for k, v in record.items()}
        for key in _FLAG_KEYS:
            if isinstance(lowered.get(key), bool):
                return lowered[key]
        status = lowered.get("status")
        if isinstance(status, bool):
            return status
        if isinstance(status, (str, int)):
            word = str(status).strip().lower()
            if word in _FAILURE_WORDS:
                return False
            if word in _SUCCESS_WORDS:
                return True
        if lowered.get("error") not in (None, "", False, [], {}):
            return False
    return None


def _clean_mask(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    digits = sum(ch.isdigit() for ch in text)
    masked = sum(ch in "*Xx" for ch in text)
    if 2 <= len(text) <= 40 and masked >= 3 and digits <= 5 and "@" not in text.replace("*", ""):
        return text
    if "@" in text and "*" in text and len(text) <= 60:
        return text
    return None


def parse_start(output) -> Optional[HubOtpStart]:
    """None: the hub says this is not a customer. Raises
    HubOtpUnknownResponse for a shape that is neither."""
    payload = _payload(output)
    if payload is None:
        raise HubOtpUnknownResponse("the request answer is not JSON")
    verdict = _verdict(payload)
    if verdict is False:
        return None
    session = _find(payload, _SESSION_KEYS)
    if verdict is True and session in (None, ""):
        raise HubOtpUnknownResponse("success without a session id")
    if session in (None, "") or isinstance(session, (dict, list)):
        raise HubOtpUnknownResponse("no session id in the request answer")
    name = _find(payload, _NAME_KEYS)
    return HubOtpStart(
        session_id=str(session)[:200],
        masked_destination=_clean_mask(_find(payload, _MASK_KEYS)),
        display_name=" ".join(str(name).split())[:80] if isinstance(name, str) and name.strip() else None,
    )


def parse_verify(output) -> tuple[bool, Optional[str]]:
    """(accepted, display name or None). Raises HubOtpUnknownResponse."""
    payload = _payload(output)
    if payload is None:
        raise HubOtpUnknownResponse("the verify answer is not JSON")
    verdict = _verdict(payload)
    if verdict is None:
        raise HubOtpUnknownResponse("the verify answer has no success flag")
    name = _find(payload, _NAME_KEYS)
    return verdict, (" ".join(name.split())[:80] if isinstance(name, str) and name.strip() else None)


class HubOtpBackend:
    def __init__(self, phone_format: str = "national"):
        self.phone_format = phone_format

    def available(self) -> bool:
        """Configured and not paused by the breaker. No network call."""
        try:
            from mcp_client.client import get_hub

            hub = get_hub()
            return hub.configured and hub.breaker_seconds_left() <= 0
        except Exception:  # noqa: BLE001
            return False

    def _telephone(self, phone_e164: str) -> str:
        digits = phone_e164.lstrip("+")
        if self.phone_format == "e164":
            return "+" + digits
        if self.phone_format == "msisdn":
            return digits
        return "0" + digits[2:] if digits.startswith("94") else digits

    def request(self, phone_e164: str) -> Optional[HubOtpStart]:
        """Asks the hub to look the customer up and send a code. None when
        it is not a customer. Raises HubError (hub trouble) or
        HubOtpUnknownResponse."""
        from mcp_client.tools import call_tool_sync

        output = call_tool_sync(TOOL_REQUEST, {"telephone_no": self._telephone(phone_e164), "otp_media": 2})
        return parse_start(output)

    def verify(self, phone_e164: str, session_id: str, code: str) -> tuple[bool, Optional[str]]:
        from mcp_client.tools import call_tool_sync

        output = call_tool_sync(
            TOOL_VERIFY, {"telephone_no": self._telephone(phone_e164), "session_id": session_id, "otp_value": code}
        )
        return parse_verify(output)


__all__ = ["HubOtpBackend", "HubOtpStart", "HubOtpUnknownResponse", "HubError", "GENERIC_DESTINATION"]
