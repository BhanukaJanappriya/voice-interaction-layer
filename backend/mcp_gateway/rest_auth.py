"""The hub's own REST sign in (HUB_AUTH_MODE=token).

POST {hub}/api/otp/send    {telephone_no, otp_media}               -> a session id and the masked contacts
POST {hub}/api/otp/verify  {telephone_no, session_id, otp_value}   -> the access and refresh tokens

The hub is asked with the same API key header as the MCP calls. The success answers of both endpoints
were NOT seen when this was written (only the 400 answers were probed), so every answer is read
defensively: snake and camel case, values nested under data, tokens, token, result or auth, an expiry as
unix seconds, milliseconds, an ISO time or a number of seconds from now (expires_in). Anything that
cannot be read comes back as a failure result, never as an exception.

Rules kept here:
  * verify is never retried (the code is single use); send is not retried either, the caller decides;
  * bounded timeouts (HUB_REST_TIMEOUT_SECONDS), no redirects followed;
  * nothing is logged except a fixed vocabulary word: never a body, a token, a code or a number;
  * a transport style failure (404, 5xx, timeout, not JSON, 401/403/429) is told apart from an ordinary
    refusal (a JSON answer that says no), because only the first moves sign in to the connection mode
    for FALLBACK_SECONDS (module state, injectable clock).
"""

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

import config
from mcp_client.client import get_hub

FALLBACK_SECONDS = 1800.0
SEND_PATH = "/api/otp/send"
VERIFY_PATH = "/api/otp/verify"

_clock: Callable[[], float] = time.monotonic  # the fallback window (replaced in tests)
_wall: Callable[[], float] = time.time  # unix seconds, for token expiry (replaced in tests)
_state_lock = threading.Lock()
_fallback_until = 0.0


# Fallback window


def fallback_active() -> bool:
    with _state_lock:
        return _clock() < _fallback_until


def enter_fallback(reason: str) -> None:
    """Sign ins use the connection mode for FALLBACK_SECONDS. One line, a fixed word only."""
    global _fallback_until
    with _state_lock:
        was_active = _clock() < _fallback_until
        _fallback_until = _clock() + FALLBACK_SECONDS
    if not was_active:
        print(f"HUB REST sign in off for {int(FALLBACK_SECONDS // 60)} minutes, connection mode used: {str(reason)[:32]}")


def reset_fallback() -> None:
    global _fallback_until
    with _state_lock:
        _fallback_until = 0.0


# Results


@dataclass
class Tokens:
    """Held in memory only. repr hides the secrets so an accidental print or log shows nothing."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    subscriber_id: str = field(repr=False)
    account_number: str = field(repr=False)
    expires_at: float = 0.0  # unix seconds

    def expired(self, margin: float = 30.0) -> bool:
        return self.expires_at - margin <= _wall()

    def __repr__(self) -> str:
        return "Tokens(...)"


@dataclass
class RestSend:
    ok: bool
    reason: str = ""  # "" | rejected | unavailable
    transport: bool = False  # True: the endpoint itself failed (404, 5xx, timeout, not JSON)
    session_id: str = ""
    masked_mobile: str = ""
    masked_email: str = ""


@dataclass
class RestVerify:
    ok: bool
    reason: str = ""  # "" | invalid_code | expired | locked | unavailable
    transport: bool = False
    tokens: Optional[Tokens] = None  # None with ok=True: the answer said yes but no tokens could be read


# Transport


def _post(path: str, body: dict) -> "tuple[Optional[int], Any]":
    """(status, parsed JSON or None). status None means the request itself failed."""
    hub = get_hub()
    if not hub.configured:
        return None, None
    timeout = max(1.0, float(getattr(config, "HUB_REST_TIMEOUT_SECONDS", 15) or 15))
    try:
        response = requests.post(
            hub.url + path,
            json=body,
            headers={hub.auth_header: hub._header_value(), "Accept": "application/json"},
            timeout=(min(timeout, 5.0), timeout),
            allow_redirects=False,
        )
    except requests.RequestException:
        return None, None
    except Exception:  # noqa: BLE001
        return None, None
    try:
        data = response.json()
    except ValueError:
        data = None
    return response.status_code, data


def _transport_failure(status: Optional[int], data: Any) -> bool:
    if status is None or not isinstance(data, dict):
        return True
    return status == 404 or status >= 500 or status in (401, 403, 408, 429)


# Reading answers


_NESTED_KEYS = ("data", "tokens", "token", "result", "auth", "authentication", "session", "response")


def _layers(data: Any) -> "list[dict]":
    """The answer and the objects nested under the usual wrapper names, outermost first (two levels)."""
    found: "list[dict]" = []
    queue: "list[tuple[Any, int]]" = [(data, 0)]
    while queue:
        node, depth = queue.pop(0)
        if not isinstance(node, dict) or any(node is f for f in found):
            continue
        found.append(node)
        if depth < 2:
            for key in _NESTED_KEYS:
                for name, value in node.items():
                    if isinstance(name, str) and name.lower() == key and isinstance(value, dict):
                        queue.append((value, depth + 1))
    return found


def _pick(layers: "list[dict]", names: "tuple[str, ...]", scalar: bool = True) -> Any:
    """The first non empty value under one of the names (case and underscores ignored). The outermost
    layer wins, and within a layer the earlier name. scalar=True skips objects and lists."""
    for layer in layers:
        for name in names:
            wanted = name.replace("_", "").lower()
            for key, value in layer.items():
                if not (isinstance(key, str) and key.replace("_", "").lower() == wanted) or value in (None, ""):
                    continue
                if scalar and isinstance(value, (dict, list)):
                    continue
                return value
    return None


def _text(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value).strip()
    return ""


def _message(layers: "list[dict]") -> str:
    value = _pick(layers, ("error", "message", "detail", "reason", "error_description"), scalar=False)
    return value if isinstance(value, str) else (json.dumps(value) if isinstance(value, (dict, list)) else "")


def _success_flag(layers: "list[dict]") -> Optional[bool]:
    for layer in layers:
        for key, value in layer.items():
            if isinstance(key, str) and key.lower() == "success" and isinstance(value, bool):
                return value
    return None


def _contact(info: Any, keys: "tuple[str, ...]") -> str:
    if isinstance(info, dict):
        for key in keys:
            for name, value in info.items():
                if isinstance(name, str) and name.lower() == key and isinstance(value, str):
                    return value
        return ""
    for key in keys:
        match = re.search(r"""['"]%s['"]\s*:\s*['"]([^'"]*)['"]""" % re.escape(key), str(info or ""), re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _jwt_exp(token: str) -> Optional[float]:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        return float(exp) if isinstance(exp, (int, float)) and not isinstance(exp, bool) else None
    except (ValueError, UnicodeError):
        return None


def parse_expiry(layers: "list[dict]", access_token: str = "") -> Optional[float]:
    """Absolute unix seconds from expires_at (seconds, milliseconds, a numeric string or an ISO time),
    else expires_in (seconds from now), else the access token's own exp claim. None when unreadable."""
    raw = _pick(layers, ("expires_at", "expiry", "expiry_time", "expires", "expiration", "expire_at"))
    if raw is not None and not isinstance(raw, bool):
        number: Optional[float] = None
        if isinstance(raw, (int, float)):
            number = float(raw)
        elif isinstance(raw, str):
            stripped = raw.strip()
            try:
                number = float(stripped)
            except ValueError:
                try:
                    moment = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=timezone.utc)
                    number = moment.timestamp()
                except ValueError:
                    number = None
        if number is not None and number > 0:
            return number / 1000.0 if number > 1e11 else number
    seconds = _pick(layers, ("expires_in", "expiry_in", "ttl"))
    if seconds is not None and not isinstance(seconds, bool):
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return _wall() + value
    return _jwt_exp(access_token) if access_token else None


def parse_tokens(data: Any) -> Optional[Tokens]:
    """The tokens of a verify answer, or None when anything the seed tool needs is missing."""
    layers = _layers(data)
    if not layers:
        return None
    access = _pick(layers, ("access_token", "accesstoken", "access"))
    if access is None:  # a bare "token": "..." string
        for layer in layers:
            if isinstance(layer.get("token"), str) and layer["token"].strip():
                access = layer["token"]
                break
    refresh = _pick(layers, ("refresh_token", "refreshtoken", "refresh"))
    subscriber = _pick(layers, ("subscriber_id", "subscriberid"))
    account = _pick(layers, ("account_number", "accountnumber", "account_no", "accountno"))
    access_text, refresh_text = _text(access), _text(refresh)
    subscriber_text, account_text = _text(subscriber), _text(account)
    if not (access_text and refresh_text and subscriber_text and account_text):
        return None
    expires = parse_expiry(layers, access_text)
    if expires is None:
        return None
    return Tokens(access_text, refresh_text, subscriber_text, account_text, float(expires))


def classify_failure(message: str) -> str:
    """The reason of a refused code from the hub's error text. The same rule the connection mode uses."""
    lowered = (message or "").lower()
    if re.search(r"too many|locked|attempt", lowered):
        return "locked"
    if "expire" in lowered and not re.search(r"invalid|incorrect|wrong", lowered):
        return "expired"
    return "invalid_code"


# The two calls


def send(national: str, otp_media: int) -> RestSend:
    """Asks the hub to send a code. Never raises, never logs."""
    status, data = _post(SEND_PATH, {"telephone_no": national, "otp_media": int(otp_media)})
    if _transport_failure(status, data):
        return RestSend(False, "unavailable", transport=True)
    layers = _layers(data)
    flag = _success_flag(layers)
    session_id = _text(_pick(layers, ("session_id", "sessionid", "otp_session_id")))
    if flag is False or not (200 <= int(status or 0) < 300) or not session_id:
        # An answer that says no (a number the hub does not know, a malformed number) is an ordinary refusal.
        # A 2xx answer with no session id and no verdict cannot be used: treat that as the endpoint failing.
        if flag is False or 400 <= int(status or 0) < 500:
            return RestSend(False, "rejected")
        return RestSend(False, "unavailable", transport=True)
    info = _pick(layers, ("contact_info", "contactinfo", "contact", "contacts", "masked_contact"), scalar=False)
    mobile = _contact(info, ("mobile", "masked_mobile", "maskedmobile", "phone", "sms")) if info is not None else ""
    email = _contact(info, ("email", "masked_email", "maskedemail", "mail")) if info is not None else ""
    mobile = mobile or _text(_pick(layers, ("masked_mobile", "maskedmobile", "mobile", "masked_phone")))
    email = email or _text(_pick(layers, ("masked_email", "maskedemail", "email")))
    return RestSend(True, session_id=session_id, masked_mobile=mobile, masked_email=email)


def verify(national: str, session_id: str, code: str) -> RestVerify:
    """Checks the code once. No retry. Never raises, never logs."""
    status, data = _post(VERIFY_PATH, {"telephone_no": national, "session_id": session_id, "otp_value": code})
    if _transport_failure(status, data):
        return RestVerify(False, "unavailable", transport=True)
    layers = _layers(data)
    flag = _success_flag(layers)
    ok_status = 200 <= int(status or 0) < 300
    if flag is False or not ok_status:
        if 400 <= int(status or 0) < 500 or flag is False:
            return RestVerify(False, classify_failure(_message(layers)))
        return RestVerify(False, "unavailable", transport=True)
    tokens = parse_tokens(data)
    if tokens is None and flag is not True and _pick(layers, ("error",), scalar=False) is not None:
        return RestVerify(False, classify_failure(_message(layers)))
    return RestVerify(True, tokens=tokens)
