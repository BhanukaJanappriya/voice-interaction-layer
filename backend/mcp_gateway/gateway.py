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

Token mode (HUB_AUTH_MODE=token, see rest_auth.py): the sign in uses the hub's REST endpoints, which issue
tokens, and the customer's connection is seeded with the authenticate_with_token tool. The tokens are kept
in memory (_tokens) until they expire, so a connection that was closed, evicted for idleness or lost can be
seeded again on demand without asking for a new code: a read only call is then tried once more, a state
changing call never is (the customer is told the sign in ended and nothing was changed).
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
from mcp_gateway import access_matrix, audit, rest_auth
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
SEED_TOOL = "authenticate_with_token"
MAX_TOKEN_ENTRIES = 500
_tokens: "dict[str, rest_auth.Tokens]" = {}  # national number -> tokens, memory only, never logged or sent anywhere
_tokens_lock = threading.Lock()
_reseed_locks: "dict[str, threading.Lock]" = {}
_SEED_REFUSED = re.compile(
    r"not authenticated|\b(fail|fails|failed|failure|invalid|expired|unauthori[sz]ed|denied|error|rejected)\b", re.IGNORECASE
)


# Helpers


def _pool() -> SessionPool:
    global _hooked
    pool = get_pool()
    if pool is not _hooked:
        pool.add_sweep_hook(_sweep_pending)
        pool.add_sweep_hook(_sweep_tokens)
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
        # A connection that is gone (closed, evicted for idleness, lost) is seeded again from the stored
        # tokens when they are still good. A state changing call is not run on a connection built just
        # now: the customer is asked again, the session is ready for the next call.
        session, why = _reseed(national)
        if session is None:
            return _fail(tool, UNAVAILABLE if why == "unavailable" else REAUTH, "reseed_unavailable" if why == "unavailable" else "no_session", started, national)
        if changing:
            return _fail(tool, REAUTH, "reseeded", started, national)
    service = _cached_service_type(national)
    if matrix.restricted(tool):
        service = service_type(access)
        fresh = _pool().get(national)
        if fresh is None or (fresh is not session and _valid_tokens(national) is None):
            return _fail(tool, REAUTH, "login_lost", started, national, service)
        if fresh is not session:
            # The service lookup seeded the connection again. A state changing call is not run on it.
            if changing:
                return _fail(tool, REAUTH, "reseeded", started, national, service)
            session = fresh
        if not matrix.allowed(tool, tier, service if service != "unknown" else FALLBACK_SERVICE_TYPE):
            return _fail(tool, REFUSED, "service_type", started, national, service)
    return _pooled(tool, bound, national, session, budget, started, service)


def _pooled(tool: str, bound: dict, national: str, session, budget: float, started: float, service: str) -> GatewayResult:
    """One call on the customer's connection. Without stored tokens there is no retry: a retry cannot
    bring a lost login back. With valid stored tokens (token mode) a read only call whose connection is
    gone or whose login the hub lost is tried ONCE more on a connection seeded again. A state changing
    call is never repeated, a repeated purchase would be worse."""
    pool = _pool()
    try:
        output = session.call(tool, bound, budget)
    except TimeoutError:
        return _fail(tool, TIMEOUT, "timeout", started, national, service)
    except SessionDead:
        pool.discard(national, session)
        _forget_service_type(national)
        retried = _retry_read(tool, bound, national, budget, started, service)
        return retried or _fail(tool, UNAVAILABLE, "connection_lost", started, national, service)
    except HubToolError:
        return _fail(tool, ERROR, "tool_error", started, national, service)
    result = _answer(tool, output.text, output.is_error, True, started, national, service)
    if result.kind == REAUTH:
        pool.discard(national, session)
        _forget_service_type(national)
        retried = _retry_read(tool, bound, national, budget, started, service)
        return retried or result
    return result


def _retry_read(tool: str, bound: dict, national: str, budget: float, started: float, service: str) -> Optional[GatewayResult]:
    """The one second try of a read only call on a connection seeded again, or None (no valid tokens, a
    state changing tool, or the seed failed; the first answer then stands). Never raises."""
    try:
        if _valid_tokens(national) is None:
            return None
        session, _ = _reseed(national)
        if session is None or access_matrix.load().is_state_changing(tool):
            return None  # a state changing call is never repeated; the next call finds a seeded connection
        try:
            output = session.call(tool, bound, budget)
        except TimeoutError:
            return _fail(tool, TIMEOUT, "timeout", started, national, service)
        except SessionDead:
            _pool().discard(national, session)
            return _fail(tool, UNAVAILABLE, "connection_lost", started, national, service)
        except HubToolError:
            return _fail(tool, ERROR, "tool_error", started, national, service)
        again = _answer(tool, output.text, output.is_error, True, started, national, service)
        if again.kind == REAUTH:
            _pool().discard(national, session)
            _forget_tokens(national)  # the hub refuses the seeded login too: sign in again
            _forget_service_type(national)
        return again
    except Exception:  # noqa: BLE001
        return None


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
    if not changing and config.HUB_WARM_SHARED:
        # An already open connection first: no connect, no initialise, no slow first call. On any
        # trouble the short lived connection below answers as it always did.
        from . import warm

        remaining = deadline - time.monotonic()
        try:
            output = warm.get_pool().call(tool, args, budget=min(remaining, max(2.5, remaining * 0.55)))
        except HubToolError:
            return _fail(tool, ERROR, "tool_error", started, national)
        except (warm.WarmUnavailable, Exception):  # noqa: BLE001
            try:
                get_hub().reset_breaker()  # a failed warm open may have opened the breaker
            except Exception:  # noqa: BLE001
                pass
        else:
            text = output.text
            structured = output.structured
            if not text and isinstance(structured, dict) and isinstance(structured.get("result"), str):
                text = structured["result"].strip()
            return _answer(tool, text, False, False, started, national, "")
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


# Tokens and seeding (token mode)


def _valid_tokens(national: str) -> Optional["rest_auth.Tokens"]:
    with _tokens_lock:
        found = _tokens.get(national)
        if found is not None and found.expired():
            del _tokens[national]
            found = None
    return found


def _store_tokens(national: str, tokens: "rest_auth.Tokens") -> None:
    with _tokens_lock:
        _tokens.pop(national, None)
        _tokens[national] = tokens
        while len(_tokens) > MAX_TOKEN_ENTRIES:
            _tokens.pop(next(iter(_tokens)))


def _forget_tokens(national: str) -> None:
    with _tokens_lock:
        _tokens.pop(national, None)


def _sweep_tokens() -> None:
    """Idle sweep: tokens past their own expiry go. Tokens of a connection that was merely closed for
    idleness stay until then, so that connection can be seeded again on demand."""
    with _tokens_lock:
        for key in [k for k, t in _tokens.items() if t.expired()]:
            del _tokens[key]


def has_tokens(access) -> bool:
    national = _national_of(access)
    return bool(national and _valid_tokens(national) is not None)


def _seed(national: str, tokens: "rest_auth.Tokens") -> "tuple[Optional[Any], str]":
    """A new pooled style connection seeded with authenticate_with_token. (session, "ok"), or
    (None, "rejected") when the hub answered no, or (None, "unavailable") when it could not be asked."""
    session = None
    try:
        session = _pool().open_detached()
        output = session.call(
            SEED_TOOL,
            {
                "telephone_no": national,
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "subscriber_id": tokens.subscriber_id,
                "account_number": tokens.account_number,
                "expires_at": float(tokens.expires_at),
            },
            max(LOGIN_BUDGET_SECONDS, float(config.MCP_HUB_TIMEOUT_SECONDS)),
        )
    except Exception:  # noqa: BLE001
        if session is not None:
            session.close()
        return None, "unavailable"
    data = _parse(output.text)
    text = output.text or ""
    refused = output.is_error or text.startswith("Error executing tool")
    if isinstance(data, dict):
        refused = refused or data.get("success") is False or bool(data.get("error"))
    elif text and not refused:
        refused = bool(_SEED_REFUSED.search(text))
    if refused:
        session.close()
        return None, "rejected"
    return session, "ok"


def _reseed(national: str) -> "tuple[Optional[Any], str]":
    """A pooled connection for national from the stored tokens. (session, "ok") (an existing pooled one
    is used when another thread was first), (None, "no_tokens"), (None, "rejected") (the hub refuses the
    tokens: they are dropped, the customer signs in again) or (None, "unavailable") (tokens kept)."""
    if _valid_tokens(national) is None:
        return None, "no_tokens"
    with _tokens_lock:
        lock = _reseed_locks.setdefault(national, threading.Lock())
    with lock:
        existing = _pool().get(national)
        if existing is not None:
            return existing, "ok"
        tokens = _valid_tokens(national)
        if tokens is None:
            return None, "no_tokens"
        started = time.perf_counter()
        session, verdict = _seed(national, tokens)
        audit.record(national, SEED_TOOL, "ok" if session is not None else verdict, _ms(started))
        if session is None:
            if verdict == "rejected":
                _forget_tokens(national)
            return None, verdict
        _forget_service_type(national)
        _pool().put(national, session)
        return session, "ok"


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


class _RestLogin:
    """Stands in for the connection of a pending login that the hub's REST endpoint sent the code for:
    nothing is open, so there is nothing to keep alive or to close."""

    alive = True

    def close(self) -> None:
        return None


def _sweep_pending() -> None:
    now = _clock()
    with _pending_lock:
        gone = [k for k, p in _pending.items() if now - p.created > PENDING_TTL_SECONDS or not p.session.alive]
    for key in gone:
        _close_pending(key)


def _rest_mode() -> bool:
    return getattr(config, "HUB_AUTH_MODE", "connection") == "token" and not rest_auth.fallback_active()


def _begin_login_rest(national: str, otp_media: int) -> Optional[LoginStart]:
    """The code is sent by the hub's REST endpoint. None means the endpoint itself failed: the connection
    mode then does this send (and the next ones for a while). Never raises."""
    global _last_error
    started = time.perf_counter()
    tool = "rest_otp_send"
    try:
        _sweep_pending()
        _close_pending(national)
        sent = rest_auth.send(national, otp_media)
        if sent.transport:
            rest_auth.enter_fallback("send")
            audit.record(national, tool, "fallback", _ms(started))
            return None
        if not sent.ok:
            audit.record(national, tool, "rejected", _ms(started))
            return LoginStart(False, reason="rejected")
        with _pending_lock:
            _pending[national] = _Pending(_RestLogin(), sent.session_id, _clock())
            while len(_pending) > max(1, int(config.HUB_SESSION_MAX)):
                oldest = min(_pending, key=lambda k: _pending[k].created)
                _pending.pop(oldest)
        audit.record(national, tool, "ok", _ms(started))
        _last_error = None
        return LoginStart(True, sent.session_id, sent.masked_mobile, sent.masked_email)
    except Exception:  # noqa: BLE001
        rest_auth.enter_fallback("send_error")
        return None


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
    if _rest_mode():
        started_rest = _begin_login_rest(national, int(otp_media))
        if started_rest is not None:
            return started_rest
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
    if isinstance(entry.session, _RestLogin):
        return _finish_login_rest(national, entry, code, started)
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
        if not name and config.HUB_NAME_LOOKUP_SECONDS > 0:
            # The verify answer has no name: ask the customer's own read only tools (bounded, best effort).
            try:
                from . import profile

                name = profile.fetch_name(entry.session, national, config.HUB_NAME_LOOKUP_SECONDS)
            except Exception:  # noqa: BLE001
                name = None
        return LoginResult(True, "", name, bool(data.get("subscriber_id")))
    message = str(data.get("error") or "") if isinstance(data, dict) else output.text
    return _refused_code(national, entry, rest_auth.classify_failure(message), tool, started)


def _refused_code(national: str, entry: "_Pending", reason: str, tool: str, started: float) -> LoginResult:
    """A wrong, expired or locked code: the pending login stays for another try only after a wrong code."""
    if reason != "invalid_code" or entry.tries >= MAX_CODE_TRIES:
        _close_pending(national)
        if reason == "invalid_code":
            reason = "too_many_attempts"
    audit.record(national, tool, reason, _ms(started))
    return LoginResult(False, reason)


def _finish_login_rest(national: str, entry: "_Pending", code: str, started: float) -> LoginResult:
    """The code is checked by the hub's REST endpoint, which answers with the tokens; a new connection is
    seeded with them and becomes the customer's session. Trouble after the code was accepted (no readable
    tokens, the seed refused) is "unavailable": the code is used up, and the next sign ins use the
    connection mode for a while."""
    global _last_error
    tool = "rest_otp_verify"
    done = rest_auth.verify(national, entry.hub_session_id, str(code or "").strip())
    if done.transport:
        _close_pending(national)
        rest_auth.enter_fallback("verify")
        _last_error = "unavailable"
        audit.record(national, tool, "unavailable", _ms(started))
        return LoginResult(False, "unavailable")
    if not done.ok:
        return _refused_code(national, entry, done.reason or "invalid_code", tool, started)
    _close_pending(national)  # the code is used whatever happens next
    tokens = done.tokens
    if tokens is None or tokens.expired():
        rest_auth.enter_fallback("verify_shape")
        _last_error = "unavailable"
        audit.record(national, tool, "no_tokens", _ms(started))
        return LoginResult(False, "unavailable")
    session, verdict = _seed(national, tokens)
    if session is None:
        rest_auth.enter_fallback("seed_" + verdict)
        _last_error = "unavailable"
        audit.record(national, SEED_TOOL, verdict, _ms(started))
        return LoginResult(False, "unavailable")
    _store_tokens(national, tokens)
    _forget_service_type(national)
    _pool().put(national, session)
    audit.record(national, tool, "ok", _ms(started))
    _last_error = None
    name = None
    if config.HUB_NAME_LOOKUP_SECONDS > 0:
        try:
            from . import profile

            name = profile.fetch_name(session, national, config.HUB_NAME_LOOKUP_SECONDS)
        except Exception:  # noqa: BLE001
            name = None
    return LoginResult(True, "", name, bool(tokens.subscriber_id))


def end_login(phone_national: str) -> None:
    """Closes a pending login (the wizard was cancelled)."""
    national = _national(phone_national)
    if national:
        _close_pending(national)


# Sessions and status


def has_session(access) -> bool:
    """True when the customer has a live connection. Without one, stored tokens that are still good seed a
    new connection now (token mode), so a connection lost to idleness or a restart of the hub's session
    does not end the visit."""
    national = _national_of(access)
    if not national:
        return False
    if _pool().get(national) is not None:
        return True
    return _reseed(national)[0] is not None


def drop_session(access) -> None:
    national = _national_of(access)
    if national:
        _pool().close(national)
        _forget_service_type(national)
        _forget_tokens(national)


def warm_status() -> dict:
    try:
        from . import warm

        return warm.get_pool().status() if config.HUB_WARM_SHARED else {"enabled": False}
    except Exception:  # noqa: BLE001
        return {"enabled": False}


def prewarm() -> None:
    """Opens the warm shared connections now (start up), in the background, so the first guest question
    does not pay for the connection. Never raises."""
    if not config.HUB_WARM_SHARED:
        return
    try:
        from . import warm

        warm.get_pool().prewarm(count=2)
    except Exception:  # noqa: BLE001
        pass


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
