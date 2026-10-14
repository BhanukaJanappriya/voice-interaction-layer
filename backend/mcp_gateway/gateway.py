"""The one place every hub call goes through.

call() is for tools a model or a service asks for. begin_login() and
finish_login() are for the sign in wizard only and are never reachable from a
model. Nothing in here raises into the caller; every outcome is a result
object with a short reason code.

Order of checks in call(), cheapest and safest first:
  1. the tool is on the "never" list: refused;
  2. the tier (guest, or authenticated for a verified caller) must list the tool;
  3. a state changing tool is refused unless confirmed=True AND
     STATE_CHANGING_TOOLS_ENABLED. Only the confirmations module may pass
     confirmed=True, after the customer said yes to one exact action. These
     calls are never retried;
  4. a customer tool runs on that customer's own pooled connection with
     telephone_no forced to the verified number; with no such connection the
     answer is "reauth" and the hub is not asked;
  5. a public tool (the knowledge search) runs on a short lived shared
     connection and needs no login.
A hub answer that says the login is gone ("No active session") closes and
drops the pooled connection and comes back as "reauth".
"""

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import config
from access.tool_gate import AccessDenied, authorize_tool, bind_arguments, is_public_tool, normalize_phone
from mcp_client.client import HubTool, get_hub
from mcp_client.errors import HubAuthError, HubBreakerOpen, HubError, HubToolError, HubUnavailable
from mcp_client.tools import _runner, call_tool_sync
from mcp_gateway import access_matrix, audit
from mcp_gateway.pool import SessionPool, get_pool
from mcp_gateway.session import SessionDead

PENDING_TTL_SECONDS = 300.0
MAX_CODE_TRIES = 5
SERVICE_TYPE_CACHE_SECONDS = 600.0
OTP_MEDIA = (1, 2, 3)  # 1 SMS, 2 email, 3 voice call
LOGIN_BUDGET_SECONDS = 15.0

_REAUTH = re.compile(r"No active session|authenticate|not authenticated|unauthor|token", re.IGNORECASE)
_clock = time.monotonic  # replaced in tests

OK, ERROR, REAUTH, UNAVAILABLE, REFUSED, TIMEOUT = "ok", "error", "reauth", "unavailable", "refused", "timeout"


@dataclass
class GatewayResult:
    kind: str
    text: str = ""
    data: Optional[dict] = None
    ms: int = 0
    tool: str = ""
    reason: str = ""
    service_type: str = ""

    @property
    def ok(self) -> bool:
        return self.kind == OK


@dataclass
class LoginStart:
    ok: bool
    hub_session_id: str = ""
    masked_mobile: str = ""
    masked_email: str = ""
    reason: str = ""


@dataclass
class LoginResult:
    ok: bool
    reason: str = ""
    name: Optional[str] = None
    subscriber_present: bool = False


@dataclass
class _Pending:
    session: Any
    hub_session_id: str
    created: float
    tries: int = 0


_pending: "dict[str, _Pending]" = {}
_pending_lock = threading.Lock()
_service_types: "dict[str, tuple[float, str]]" = {}
_service_lock = threading.Lock()
# When the service type cannot be read (the hub's show_service_detail failed), the
# customer is treated as postpaid, the family most lines belong to, so balance and
# bill questions still get an answer. The failed lookup is remembered for a short
# time so a broken detail tool is not asked again on every question.
FALLBACK_SERVICE_TYPE = "postpaid"
UNKNOWN_RETRY_SECONDS = 60.0
_last_error: Optional[str] = None
_hooked: Optional[SessionPool] = None


# Helpers


def _pool() -> SessionPool:
    global _hooked
    pool = get_pool()
    if pool is not _hooked:
        pool.add_sweep_hook(_sweep_pending)
        _hooked = pool
    return pool


def _national(value: Any) -> Optional[str]:
    """0112345678 from any usual way of writing a Sri Lankan number, or None."""
    plus = normalize_phone(value if isinstance(value, str) else None)
    return "0" + plus[3:] if plus else None


def _national_of(access) -> Optional[str]:
    if access is None or not getattr(access, "is_verified", False):
        return None
    return _national(getattr(access, "phone_e164", None))


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _parse(text: str) -> Optional[dict]:
    stripped = (text or "").strip()
    if stripped[:1] != "{":
        return None
    try:
        value = json.loads(stripped)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _clean_arguments(arguments: Any) -> dict:
    """The model's arguments without anything that looks like a credential."""
    if not isinstance(arguments, dict):
        return {}
    return {k: v for k, v in arguments.items() if "token" not in str(k).lower() and "otp" not in str(k).lower()}


def _cap(text: str) -> str:
    limit = int(getattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000))
    return text if len(text) <= limit else text[:limit]


def _fail(tool: str, kind: str, reason: str, started: float, session: Optional[str], service: str = "") -> GatewayResult:
    global _last_error
    ms = _ms(started)
    if kind in (UNAVAILABLE, TIMEOUT):
        _last_error = reason
    audit.record(session, tool, kind, ms)
    return GatewayResult(kind=kind, ms=ms, tool=tool, reason=reason, service_type=service)


def _answer(tool: str, text: str, is_error: bool, pooled: bool, started: float, session: Optional[str], service: str) -> GatewayResult:
    """Turns the hub's text into a result. The reauth rule applies to the
    customer's own connection only; a public search is never a login matter."""
    global _last_error
    data = _parse(text)
    kind, reason = OK, ""
    failed_json = isinstance(data, dict) and data.get("success") is False
    failed_text = text.startswith("Error executing tool")
    if failed_json or failed_text or is_error:
        message = str(data.get("error") or "") if failed_json else text
        if pooled and _REAUTH.search(message):
            kind, reason = REAUTH, "login_lost"
        else:
            kind, reason = ERROR, "hub_error" if failed_json else "tool_error"
    if kind == OK:
        _last_error = None
    ms = _ms(started)
    audit.record(session, tool, kind, ms)
    shown = _cap(text) if kind in (OK, ERROR) else ""
    return GatewayResult(kind=kind, text=shown, data=data if kind in (OK, ERROR) else None, ms=ms, tool=tool, reason=reason, service_type=service)


# Calls


def call(name: str, arguments: Optional[dict], access, *, budget: Optional[float] = None, confirmed: bool = False) -> GatewayResult:
    """Blocking, never raises. See the module text for the order of checks."""
    started = time.perf_counter()
    tool = str(name or "")
    national = _national_of(access)
    try:
        return _call(tool, arguments, access, national, budget, confirmed, started)
    except Exception as exc:  # noqa: BLE001
        return _fail(tool, UNAVAILABLE, type(exc).__name__.lower()[:24] or "internal", started, national)


# Customer tier tools whose hub schema takes no telephone_no (docs/MCP_TOOLS.md).
NO_PHONE_TOOLS = frozenset({"compare_package"})


def _call(tool: str, arguments, access, national: Optional[str], budget, confirmed: bool, started: float) -> GatewayResult:
    matrix = access_matrix.load()
    if matrix.never(tool):
        return _fail(tool, REFUSED, "never", started, national)
    tier = "authenticated" if national else "guest"
    if not matrix.in_tier(tool, tier) and not (tier == "authenticated" and matrix.allowed(tool, tier)):
        return _fail(tool, REFUSED, "tier", started, national)
    changing = matrix.is_state_changing(tool)
    if changing and not (confirmed and getattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)):
        return _fail(tool, REFUSED, "state_changing", started, national)
    budget = float(budget if budget is not None else config.MCP_HUB_TIMEOUT_SECONDS)
    clean = _clean_arguments(arguments)

    if tool in matrix.tool_names_for("guest") or is_public_tool(tool):
        return _shared(tool, clean, budget, changing, started, national)

    # A customer tool: only on the customer's own connection.
    try:
        bound = bind_arguments(tool, clean, access)
        authorize_tool(tool, bound, access)
    except AccessDenied:
        return _fail(tool, REFUSED, "access", started, national)
    if tool in NO_PHONE_TOOLS:
        bound.pop("telephone_no", None)  # the hub's schema for this tool has no such argument
    else:
        bound["telephone_no"] = national  # the hub documents the national form
    session = _pool().get(national)
    if session is None:
        return _fail(tool, REAUTH, "no_session", started, national)
    service = _cached_service_type(national)
    if matrix.restricted(tool):
        service = service_type(access)
        if _pool().get(national) is not session:
            return _fail(tool, REAUTH, "login_lost", started, national, service)
        if not matrix.allowed(tool, tier, service if service != "unknown" else FALLBACK_SERVICE_TYPE):
            return _fail(tool, REFUSED, "service_type", started, national, service)
    return _pooled(tool, bound, national, session, budget, started, service)


def _pooled(tool: str, bound: dict, national: str, session, budget: float, started: float, service: str) -> GatewayResult:
    """One call on the customer's connection. No retry, ever: a retry cannot
    bring a lost login back, and a repeated purchase would be worse."""
    pool = _pool()
    try:
        output = session.call(tool, bound, budget)
    except TimeoutError:
        return _fail(tool, TIMEOUT, "timeout", started, national, service)
    except SessionDead:
        pool.discard(national, session)
        _forget_service_type(national)
        return _fail(tool, UNAVAILABLE, "connection_lost", started, national, service)
    except HubToolError:
        return _fail(tool, ERROR, "tool_error", started, national, service)
    result = _answer(tool, output.text, output.is_error, True, started, national, service)
    if result.kind == REAUTH:
        pool.discard(national, session)
        _forget_service_type(national)
    return result


def _shared(tool: str, args: dict, budget: float, changing: bool, started: float, national: Optional[str]) -> GatewayResult:
    """A public tool on a short lived connection. A read only tool gets a second
    try when the first stalls or fails: the hub was seen to stall on about one
    call in four (a connection that opens and then says nothing), and a second
    connection answers in two seconds. The first try gets half of the time so
    the second still has room, and the client's breaker, which a failed first
    try would have opened, is cleared so the second try can run."""
    attempts = 1 if changing else 2
    deadline = time.monotonic() + max(budget, 0.5)
    last, kind = "unavailable", UNAVAILABLE
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0.3:
            break
        this_budget = remaining if attempt + 1 == attempts else max(3.0, remaining * 0.5)
        try:
            output = call_tool_sync(tool, args, budget=this_budget)
        except HubBreakerOpen:
            return _fail(tool, UNAVAILABLE, "breaker", started, national)
        except HubAuthError:
            return _fail(tool, UNAVAILABLE, "hub_auth", started, national)
        except HubToolError:
            return _fail(tool, ERROR, "tool_error", started, national)
        except TimeoutError:
            last, kind = "timeout", TIMEOUT
        except HubError:
            last, kind = "unavailable", UNAVAILABLE
        else:
            text = output.text
            structured = output.structured
            if not text and isinstance(structured, dict) and isinstance(structured.get("result"), str):
                text = structured["result"].strip()
            return _answer(tool, text, False, False, started, national, "")
        if attempt + 1 < attempts:
            try:
                get_hub().reset_breaker()
            except Exception:  # noqa: BLE001
                pass
    return _fail(tool, kind, last, started, national)


# Service type


def _cached_service_type(national: str) -> str:
    with _service_lock:
        entry = _service_types.get(national)
    if entry:
        lifetime = UNKNOWN_RETRY_SECONDS if entry[1] == "unknown" else SERVICE_TYPE_CACHE_SECONDS
        if _clock() - entry[0] < lifetime:
            return entry[1]
    return ""


def _forget_service_type(national: str) -> None:
    with _service_lock:
        _service_types.pop(national, None)


def service_type(access) -> str:
    """postpaid, lte_prepaid or unknown, from show_service_detail on the
    customer's own connection, cached for 10 minutes. Unknown when there is
    no connection or the answer cannot be read."""
    national = _national_of(access)
    if national is None:
        return "unknown"
    cached = _cached_service_type(national)
    if cached:
        return cached
    session = _pool().get(national)
    if session is None:
        return "unknown"
    started = time.perf_counter()
    result = _pooled("show_service_detail", {"telephone_no": national}, national, session, float(config.MCP_HUB_TIMEOUT_SECONDS), started, "")
    found = "unknown"
    if result.kind == OK and isinstance(result.data, dict) and result.data.get("success", True) is not False:
        lowered = result.text.lower()
        if "lte" in lowered and "prepaid" in lowered:
            found = "lte_prepaid"
        elif result.data:
            found = "postpaid"
    with _service_lock:
        _service_types[national] = (_clock(), found)  # "unknown" is kept too, for UNKNOWN_RETRY_SECONDS
    return found


# Model facing tool list

_resolve_service_type = service_type  # tool_schemas has a parameter of the same name


def tool_schemas(access, service_type: Optional[str] = None, include_state_changing: bool = False) -> list:
    """The hub tools the model may see for this caller, as {"name",
    "description","input_schema"}. telephone_no is removed from every schema
    (the gateway injects it), sign in tools are never listed, and tools that
    change an account are left out unless include_state_changing is set (they
    are driven by the confirmation step, not offered to the model)."""
    try:
        matrix = access_matrix.load()
        national = _national_of(access)
        tier = "authenticated" if national else "guest"
        kind = service_type or (_resolve_service_type(access) if national else "unknown")
        if kind == "unknown" and national:
            kind = FALLBACK_SERVICE_TYPE
        names = [n for n in matrix.tool_names_for(tier, kind) if include_state_changing or not matrix.is_state_changing(n)]
        if not names:
            return []
        hub = get_hub()
        if not hub.configured:
            return []
        budget = float(config.MCP_HUB_TIMEOUT_SECONDS)
        listed: list[HubTool] = _runner.run(hub.list_tools(budget=budget), timeout=budget + 2.0)
        by_name = {t.name: t for t in listed}
        out = []
        for name in names:
            tool = by_name.get(name)
            if tool is None:
                continue
            schema = json.loads(json.dumps(tool.input_schema or {"type": "object", "properties": {}}))
            props = schema.get("properties")
            if isinstance(props, dict):
                props.pop("telephone_no", None)
            if isinstance(schema.get("required"), list):
                schema["required"] = [r for r in schema["required"] if r != "telephone_no"]
            out.append({"name": tool.name, "description": tool.description, "input_schema": schema})
        return out
    except Exception:  # noqa: BLE001
        return []


# Login path (the wizard only)


def _contact(info: Any, key: str) -> str:
    if isinstance(info, dict):
        return str(info.get(key) or "")
    match = re.search(r"""['"]%s['"]\s*:\s*['"]([^'"]*)['"]""" % key, str(info or ""))
    return match.group(1) if match else ""


def _close_pending(national: str) -> None:
    with _pending_lock:
        entry = _pending.pop(national, None)
    if entry is not None:
        try:
            entry.session.close()
        except Exception:  # noqa: BLE001
            pass


def _sweep_pending() -> None:
    now = _clock()
    with _pending_lock:
        gone = [k for k, p in _pending.items() if now - p.created > PENDING_TTL_SECONDS or not p.session.alive]
    for key in gone:
        _close_pending(key)


def begin_login(phone_national: str, otp_media: int) -> LoginStart:
    """Opens a new connection, asks the hub to send a one time code over the
    chosen channel (1 SMS, 2 email, 3 voice) and keeps the connection open for
    finish_login. Never raises."""
    started = time.perf_counter()
    tool = "validate_customer_and_send_otp"
    national = _national(phone_national)
    if isinstance(otp_media, bool) or otp_media not in OTP_MEDIA:
        return LoginStart(False, reason="refused")
    if national is None:
        return LoginStart(False, reason="invalid_number")
    global _last_error
    session = None
    try:
        _sweep_pending()
        _close_pending(national)
        session = _pool().open_detached()
        output = session.call(tool, {"telephone_no": national, "otp_media": int(otp_media)}, max(LOGIN_BUDGET_SECONDS, float(config.MCP_HUB_TIMEOUT_SECONDS)))
        data = _parse(output.text)
        if output.is_error or not isinstance(data, dict) or data.get("success") is not True or not data.get("session_id"):
            session.close()
            audit.record(national, tool, "rejected", _ms(started))
            return LoginStart(False, reason="rejected")
        info = data.get("contact_info")
        with _pending_lock:
            _pending[national] = _Pending(session, str(data["session_id"]), _clock())
            while len(_pending) > max(1, int(config.HUB_SESSION_MAX)):
                oldest = min(_pending, key=lambda k: _pending[k].created)
                dropped = _pending.pop(oldest)
                threading.Thread(target=dropped.session.close, daemon=True).start()
        audit.record(national, tool, "ok", _ms(started))
        _last_error = None
        return LoginStart(True, str(data["session_id"]), _contact(info, "mobile"), _contact(info, "email"))
    except Exception as exc:  # noqa: BLE001
        if session is not None:
            session.close()
        reason = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        _last_error = reason
        audit.record(national, tool, reason, _ms(started))
        return LoginStart(False, reason="unavailable")


def finish_login(phone_national: str, hub_session_id: str, code: str) -> LoginResult:
    """Checks the code on the SAME connection begin_login opened. On success
    the connection becomes the customer's pooled session (any earlier one is
    closed). On a wrong code it stays pending for another try until the
    pending time runs out. Never raises."""
    started = time.perf_counter()
    tool = "verify_otp_and_authenticate"
    national = _national(phone_national)
    global _last_error
    if national is None:
        return LoginResult(False, "invalid_number")
    _sweep_pending()
    with _pending_lock:
        entry = _pending.get(national)
    if entry is None:
        return LoginResult(False, "no_pending")
    if str(hub_session_id or "") != entry.hub_session_id:
        return LoginResult(False, "mismatch")
    entry.tries += 1
    try:
        output = entry.session.call(
            tool,
            {"telephone_no": national, "session_id": entry.hub_session_id, "otp_value": str(code or "").strip()},
            max(LOGIN_BUDGET_SECONDS, float(config.MCP_HUB_TIMEOUT_SECONDS)),
        )
    except Exception as exc:  # noqa: BLE001
        _close_pending(national)
        reason = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
        _last_error = reason
        audit.record(national, tool, reason, _ms(started))
        return LoginResult(False, "unavailable")
    data = _parse(output.text)
    if isinstance(data, dict) and data.get("success") is True and not output.is_error:
        with _pending_lock:
            _pending.pop(national, None)
        _forget_service_type(national)
        _pool().put(national, entry.session)
        audit.record(national, tool, "ok", _ms(started))
        _last_error = None
        name = next((str(data[k]) for k in ("name", "subscriber_name", "customer_name") if data.get(k)), None)
        return LoginResult(True, "", name, bool(data.get("subscriber_id")))
    message = str(data.get("error") or "") if isinstance(data, dict) else output.text
    lowered = message.lower()
    if re.search(r"too many|locked|attempt", lowered):
        reason = "locked"
    elif "expire" in lowered and not re.search(r"invalid|incorrect|wrong", lowered):
        reason = "expired"
    else:
        reason = "invalid_code"
    if reason != "invalid_code" or entry.tries >= MAX_CODE_TRIES:
        _close_pending(national)
        if reason == "invalid_code":
            reason = "too_many_attempts"
    audit.record(national, tool, reason, _ms(started))
    return LoginResult(False, reason)


def end_login(phone_national: str) -> None:
    """Closes a pending login (the wizard was cancelled)."""
    national = _national(phone_national)
    if national:
        _close_pending(national)


# Sessions and status


def has_session(access) -> bool:
    national = _national_of(access)
    return bool(national and _pool().get(national) is not None)


def drop_session(access) -> None:
    national = _national_of(access)
    if national:
        _pool().close(national)
        _forget_service_type(national)


def status() -> dict:
    """For /health. Never raises, holds no number."""
    try:
        _sweep_pending()
        with _pending_lock:
            waiting = len(_pending)
        return {
            "sessions": _pool().size(),
            "pending_logins": waiting,
            "last_error": _last_error,
            "matrix_loaded": bool(access_matrix.load().loaded),
        }
    except Exception:  # noqa: BLE001
        return {"sessions": 0, "pending_logins": 0, "last_error": "status_failed", "matrix_loaded": False}


def latencies() -> dict:
    """get_function_latencies through the shared path. Admin page only."""
    started = time.perf_counter()
    try:
        output = call_tool_sync("get_function_latencies", {}, budget=float(config.MCP_HUB_TIMEOUT_SECONDS))
        text = output.text
        if not text and isinstance(output.structured, dict) and isinstance(output.structured.get("result"), str):
            text = output.structured["result"]
        audit.record(None, "get_function_latencies", "ok", _ms(started))
        return _parse(text) or {}
    except Exception:  # noqa: BLE001
        audit.record(None, "get_function_latencies", "unavailable", _ms(started))
        return {}
