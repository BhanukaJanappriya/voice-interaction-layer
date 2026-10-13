"""Hub tools as extra context for the Orchestrator, plus customer lookup.

hub_context_for(text) has the Orchestrator's tool_callback shape (text in,
Optional[str] out) and is meant to sit next to the knowledge retriever:

    Orchestrator(tool_callback=compose_tool_callbacks(
        retriever.context_for, hub_context_for), ...)

Which tools it calls (safety first, because the real hub also offers
tools that buy packages, log faults or send one time codes):
  * by default ONLY search_knowledgebase_tool (DEFAULT_TOOLS), a read only
    knowledge search with one text argument (query);
  * MCP_HUB_TOOLS replaces that list when set;
  * whatever is listed, a tool whose name starts with purchase_, add_,
    subscribe_, log_ or refresh_ is refused (DENIED_PREFIXES), as are the
    two OTP tools and the customer lookup tool, which have their own
    entry points and must never be driven by a spoken sentence.
There is deliberately no "every tool with one text argument" mode: most of
the hub's customer tools take one required telephone_no string and some of
them change things.

The user's words are sent to the hub, truncated to MAX_QUERY_CHARS.

Why a background event loop thread: the Orchestrator is synchronous and
calls its tool_callback from worker threads (the voice pipeline's thread
pool, FastAPI's thread pool), while the MCP SDK is async. One daemon
thread owns one event loop for the process; callers submit coroutines to
it and wait with a timeout. No caller ever needs a loop of its own, and an
already running loop (the one under FastAPI) is never blocked or nested.

Failure policy: hub_context_for NEVER raises and returns None when the hub
is off, not configured, paused by the breaker, slow, failing or simply has
nothing to say, because it sits in front of every spoken reply.
"""

import asyncio
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Optional

import config
from mcp_client.client import HubClient, HubTool, get_hub
from mcp_client.errors import HubError

HUB_HEADER = "Notes from the SLT MCP hub (reference data from tools, may be incomplete; use only if it answers the question):"
MAX_CHARS_PER_TOOL = 700
MAX_TOOLS_PER_TURN = 4
MAX_QUERY_CHARS = 500
_CACHE_SECONDS = 60.0
_CACHE_SIZE = 32

DEFAULT_TOOLS = ("search_knowledgebase_tool",)
DENIED_PREFIXES = ("purchase_", "add_", "subscribe_", "log_", "refresh_")
# Tools reserved for sign in (otp package); never offered to the model.
RESERVED_TOOLS = ("validate_customer_and_send_otp", "verify_otp_and_authenticate", "authenticate_with_token")
_TEXT_ARGUMENTS = ("query", "text", "question")

# Argument names tried, in order, when a lookup tool has several strings.
_PHONE_ARGUMENTS = ("phone", "phone_number", "msisdn", "number", "landline", "telephone", "contact_number")


class _LoopThread:
    """One daemon thread running one event loop, started on first use."""

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or not self._thread or not self._thread.is_alive():
                loop = asyncio.new_event_loop()
                ready = threading.Event()

                def run() -> None:
                    asyncio.set_event_loop(loop)
                    loop.call_soon(ready.set)
                    loop.run_forever()

                self._loop = loop
                self._thread = threading.Thread(target=run, name="mcp-hub-loop", daemon=True)
                self._thread.start()
                ready.wait(5)
            return self._loop

    def run(self, coro, timeout: float):
        """Runs the coroutine on the loop thread and waits for its result.
        Raises TimeoutError (after cancelling it) or whatever it raised."""
        future: Future = asyncio.run_coroutine_threadsafe(coro, self._ensure())
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise
        except BaseException:
            future.cancel()
            raise


_runner = _LoopThread()
_cache: "OrderedDict[str, tuple[float, str]]" = OrderedDict()
_cache_lock = threading.Lock()
_logged_selection: Optional[tuple] = None


def _budget() -> float:
    return max(0.5, float(config.MCP_HUB_TIMEOUT_SECONDS))


def _context_budget() -> float:
    """How long a spoken reply may wait for hub notes (never more than the
    hub's own call timeout)."""
    value = float(getattr(config, "MCP_HUB_CONTEXT_BUDGET_SECONDS", 1.0))
    return max(0.2, min(value, _budget()))


# Tool choice


def eligible_tools(
    tools: list[HubTool], names: Optional[list[str]] = None, exclude: Optional[str] = None
) -> list[tuple[HubTool, str]]:
    """(tool, argument name) pairs to call with the user's text: the tools
    named in names (default DEFAULT_TOOLS) that exist on the hub, are not
    denied, and have a usable string argument. The hub's own order is kept."""
    wanted = {n.strip() for n in (names or DEFAULT_TOOLS) if n.strip()}
    chosen: list[tuple[HubTool, str]] = []
    for tool in tools:
        if tool.name not in wanted or tool.name == exclude:
            continue
        if tool.name.startswith(DENIED_PREFIXES) or tool.name in RESERVED_TOOLS:
            continue
        argument = tool.required_string_argument() or tool.string_argument(_TEXT_ARGUMENTS)
        if argument:
            chosen.append((tool, argument))
    return chosen[:MAX_TOOLS_PER_TURN]


def _cap(text: str, limit: int = MAX_CHARS_PER_TOOL) -> str:
    text = re.sub(r"[ \t]+", " ", text.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = max(cut.rfind(". "), cut.rfind("\n"), cut.rfind(" "))
    if boundary > limit * 0.6:
        cut = cut[:boundary]
    return cut.rstrip(" ,;:.\n") + "..."


def format_notes(results: list[tuple[str, str]]) -> Optional[str]:
    """Heading plus one block per tool that said something, each capped
    like knowledge.retriever.format_context caps its excerpts."""
    blocks = [f"[{name}]\n{_cap(text)}" for name, text in results if text and text.strip()]
    if not blocks:
        return None
    return "\n\n".join([HUB_HEADER, *blocks])


# Context for replies


async def _gather(hub: HubClient, text: str, budget: float) -> Optional[str]:
    global _logged_selection
    started = time.monotonic()
    tools = await hub.list_tools(budget=budget)
    selection = eligible_tools(tools, config.MCP_HUB_TOOLS, exclude=config.MCP_HUB_CUSTOMER_LOOKUP_TOOL or None)
    key = tuple(t.name for t, _ in selection)
    if key != _logged_selection:
        _logged_selection = key
        print(f"MCP hub tools in use: {', '.join(key) if key else 'none (no eligible tool)'}")
    if not selection:
        return None

    async def one(tool: HubTool, argument: str) -> tuple[str, str]:
        remaining = max(0.3, budget - (time.monotonic() - started))
        try:
            # An operator chosen search tool gets the user's words and no phone
            # number, so it is trusted, unless it is one of the policy's named
            # customer tools: those go through the access gate as a guest.
            from access.policy import get_policy

            trusted = tool.name not in get_policy().customer_tools
            return tool.name, await hub.call_tool(tool.name, {argument: text}, budget=remaining, trusted=trusted)
        except HubError as exc:
            print(f"MCP hub tool {tool.name} gave nothing: {type(exc).__name__}")
            return tool.name, ""

    results = await asyncio.gather(*(one(t, a) for t, a in selection))
    return format_notes(list(results))


def hub_context_for(text: str) -> Optional[str]:
    """Orchestrator tool_callback. Sync, never raises."""
    try:
        # Off by default: against the real hub a knowledge search takes 3 to 7
        # seconds, far beyond a first word budget, and it overlaps the local
        # knowledge base. A failure here would also open the breaker that the
        # hub's one time code tools share (see MCP_HUB_IN_REPLIES in config.py).
        if not getattr(config, "MCP_HUB_IN_REPLIES", False):
            return None
        query = " ".join((text or "").split())[:MAX_QUERY_CHARS]
        if not query:
            return None
        hub = get_hub()
        if not hub.configured or hub.breaker_seconds_left() > 0:
            return None
        now = time.monotonic()
        with _cache_lock:
            hit = _cache.get(query)
            if hit and now - hit[0] < _CACHE_SECONDS:
                _cache.move_to_end(query)
                return hit[1]
        budget = _context_budget()
        started = time.perf_counter()
        try:
            notes = _runner.run(_gather(hub, query, budget), timeout=budget + 0.3)
        except Exception as exc:  # noqa: BLE001  (never raise into the reply path)
            print(f"MCP hub unavailable for this turn: {type(exc).__name__}")
            notes = None

        finally:
            print(f"MCP hub latency: {(time.perf_counter() - started) * 1000:.0f} ms")
        if notes:
            with _cache_lock:
                # Also answers the local model's retry within the same turn
                # (cloud first, local second) without asking the hub twice.
                _cache[query] = (time.monotonic(), notes)
                while len(_cache) > _CACHE_SIZE:
                    _cache.popitem(last=False)
        return notes or None
    except Exception:  # noqa: BLE001
        return None


def warm_up() -> None:
    """Detects the hub's transport and lists its tools once at startup, so
    the first spoken reply does not pay for that inside its short budget.
    Never raises."""
    try:
        hub = get_hub()
        if not hub.configured:
            return
        started = time.perf_counter()
        _runner.run(hub.list_tools(budget=_budget()), timeout=_budget() + 1.0)
        print(f"MCP hub warm up: {(time.perf_counter() - started) * 1000:.0f} ms")
    except Exception as exc:  # noqa: BLE001
        print(f"MCP hub warm up failed ({type(exc).__name__}).")


# Composition

_compose_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-callback")


def compose_tool_callbacks(*callbacks: Optional[Callable[[str], Optional[str]]]) -> Callable[[str], Optional[str]]:
    """One tool_callback from several. Callbacks run side by side (the
    hub call is the slow one; running it after retrieval would add the
    two), their outputs are joined in the order the callbacks were given
    (retrieval first), and a callback that raises or returns nothing is
    simply left out: the result is None only when all of them are."""
    active = [cb for cb in callbacks if cb is not None]

    def safe(callback: Callable[[str], Optional[str]], text: str) -> Optional[str]:
        try:
            value = callback(text)
        except Exception:  # noqa: BLE001
            return None
        return value.strip() if isinstance(value, str) and value.strip() else None

    def composed(text: str) -> Optional[str]:
        if not active:
            return None
        if len(active) == 1:
            return safe(active[0], text)
        futures = [_compose_pool.submit(safe, cb, text) for cb in active]
        parts = [f.result() for f in futures]
        joined = "\n\n".join(p for p in parts if p)
        return joined or None

    return composed


def call_tool_sync(name: str, arguments: dict, budget: Optional[float] = None, access=None):
    """Blocking call of one hub tool from any thread. Returns
    client.ToolOutput; raises the typed HubError family (and TimeoutError past
    the budget) and access.AccessDenied. Not subject to the allow and deny
    lists above.

    Without access this is the repository's own login flow (the hub OTP backend)
    and is trusted. With access (an access.AccessContext) it is a request made
    on behalf of a user and goes through the access gate first: a customer tool
    needs a verified session and its telephone_no is forced to the verified
    number."""
    budget = _budget() if budget is None else budget
    hub = get_hub()
    return _runner.run(
        hub.call_tool_output(name, arguments, budget=budget, access=access, trusted=access is None),
        timeout=budget + 1.0,
    )


# Customer lookup

_NAME_KEYS = ("display_name", "name", "full_name", "customer_name", "fullname")
_MOBILE_KEYS = ("mobile", "mobile_number", "mobile_e164", "msisdn", "mobile_no", "contact_number", "phone_mobile")
_EMAIL_KEYS = ("email", "email_address", "e_mail")
_WRAPPER_KEYS = ("customer", "data", "result", "user", "record", "account", "profile")
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,}$")


def normalize_mobile(value: Any) -> Optional[str]:
    """+947XXXXXXXX from 07XXXXXXXX, 947XXXXXXXX, +947XXXXXXXX or
    7XXXXXXXX (separators ignored); None for anything else."""
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("0094"):
        digits = digits[4:]
    elif digits.startswith("94"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = digits[1:]
    return f"+94{digits}" if re.fullmatch(r"7\d{8}", digits) else None


def _first(record: dict, keys: tuple) -> Any:
    lowered = {str(k).lower(): v for k, v in record.items()}
    for key in keys:
        value = lowered.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _find_record(value: Any, depth: int = 0) -> Optional[dict]:
    """The dict that holds the customer's fields, looking through the
    usual wrappers ({"customer": {...}}, {"data": [{...}]}, a list)."""
    if depth > 4:
        return None
    if isinstance(value, list):
        return _find_record(value[0], depth + 1) if value else None
    if not isinstance(value, dict):
        return None
    lowered = {str(k).lower(): v for k, v in value.items()}
    if any(k in lowered for k in _NAME_KEYS + _MOBILE_KEYS + _EMAIL_KEYS):
        return value
    for key in _WRAPPER_KEYS:
        if key in lowered:
            found = _find_record(lowered[key], depth + 1)
            if found is not None:
                return found
    return None


def parse_customer(structured: Any, text: str) -> Optional[dict]:
    """Minimum fields {display_name, mobile, email} from a hub result, or
    None. Accepts structured content, or JSON text; anything else (free
    text) is not guessed at. Every other field the hub sends is dropped
    here on purpose: nothing beyond what sign in needs may travel on."""
    candidates: list[Any] = []
    if structured not in (None, {}, []):
        candidates.append(structured)
    if text and text.strip()[:1] in "{[":
        try:
            candidates.append(json.loads(text))
        except ValueError:
            pass
    for candidate in candidates:
        if isinstance(candidate, dict):
            lowered = {str(k).lower(): v for k, v in candidate.items()}
            if lowered.get("found") is False or lowered.get("exists") is False:
                return None
        record = _find_record(candidate)
        if record is None:
            continue
        name = _first(record, _NAME_KEYS)
        mobile = normalize_mobile(_first(record, _MOBILE_KEYS))
        email = _first(record, _EMAIL_KEYS)
        email = email.strip() if isinstance(email, str) and _EMAIL.match(email.strip()) else None
        name = " ".join(str(name).split())[:80] if name else None
        if name or mobile or email:
            return {"display_name": name, "mobile": mobile, "email": email}
    return None


async def _lookup(hub: HubClient, tool_name: str, phone_e164: str, budget: float) -> Optional[dict]:
    tools = await hub.list_tools(budget=budget)
    tool = next((t for t in tools if t.name == tool_name), None)
    if tool is None:
        print(f"MCP hub customer lookup tool {tool_name!r} is not in the hub's tool list.")
        return None
    argument = tool.string_argument(_PHONE_ARGUMENTS)
    if argument is None:
        print(f"MCP hub customer lookup tool {tool_name!r} has no string argument to put the number in.")
        return None
    output = await hub.call_tool_output(tool_name, {argument: phone_e164}, budget=budget, trusted=True)
    return parse_customer(output.structured, output.text)


def lookup_customer_via_hub(phone_e164: str) -> Optional[dict]:
    """Finds a customer by landline number through the hub tool named by
    MCP_HUB_CUSTOMER_LOOKUP_TOOL. Returns {display_name, mobile (E.164 or
    None), email or None}, or None when the setting is empty, the hub is
    off, paused, unreachable, does not know the customer, or answers in a
    shape this cannot read. None therefore means "ask somewhere else", and
    the caller falls back to the local users table. Never raises, and
    logs neither the number nor anything the hub returned. The landline is
    sent in E.164 (+94112345678); whether the real hub wants that format
    is unverified."""
    try:
        tool_name = config.MCP_HUB_CUSTOMER_LOOKUP_TOOL
        if not tool_name or not phone_e164:
            return None
        hub = get_hub()
        if not hub.configured or hub.breaker_seconds_left() > 0:
            return None
        budget = _budget()
        return _runner.run(_lookup(hub, tool_name, phone_e164, budget), timeout=budget + 1.0)
    except Exception as exc:  # noqa: BLE001
        print(f"MCP hub customer lookup failed: {type(exc).__name__}")
        return None


# Status


def status() -> dict:
    """For /health under "mcp_hub". Never raises, never contains the key."""
    try:
        info = get_hub().status()
        info["tool_selection"] = list(config.MCP_HUB_TOOLS) if config.MCP_HUB_TOOLS else "auto"
        info["customer_lookup_tool"] = bool(config.MCP_HUB_CUSTOMER_LOOKUP_TOOL)
        return info
    except Exception as exc:  # noqa: BLE001
        return {"configured": False, "error": type(exc).__name__}
