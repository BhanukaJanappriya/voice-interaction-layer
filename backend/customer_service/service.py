"""Fetch read only account data for a verified customer from the MCP hub.

fetch(text, language, access) is blocking and never raises. It returns a
CustomerOutcome whose kind tells the caller what to do:

    none         not an account question, disabled, or not verified: do nothing
    data         context holds the text block for the model
    reauth       the hub says the customer is not signed in: answer with canned
    unavailable  hub down, breaker open, timeout, nothing answered: answer with canned

Privacy: only tool names, outcomes and milliseconds are ever printed. Customer
text, numbers and payloads are never logged. Only tools.ALLOWED_TOOLS are called,
and the allow list is checked again right before each call.
"""

import asyncio
import concurrent.futures as futures
import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import config
from access.tool_gate import normalize_phone
from mcp_client import tools as hub_tools
from mcp_client.errors import HubAuthError, HubBreakerOpen, HubToolError, HubUnavailable

from . import intent as intent_module
from . import render
from .messages import canned
from .tools import LTE_PREPAID, POSTPAID, ToolCall, ToolRefused, calls_for, require_allowed

PRODUCT_CACHE_SECONDS = 600.0
_CACHE_LIMIT = 2000

_AUTH_ERROR = re.compile(r"(?i)authenticat|not logged in|token|session expired|unauthor|\b401\b")
_NOT_APPLICABLE = re.compile(
    r"(?i)not applicable|not supported|not an? (lte|postpaid|prepaid|fixed)|invalid service|wrong (service|product)"
)
_LTE_WORD = re.compile(r"\blte\b")
_FIXED_WORDS = re.compile(r"fibre|fiber|ftth|copper|adsl|vdsl|pstn|fixed line|broadband")

_pool = futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="customer-tools")
_cache: dict = {}
_cache_lock = threading.Lock()
_now = time.monotonic  # replaceable in tests


@dataclass
class CustomerOutcome:
    kind: str = "none"
    context: str = ""
    canned: str = ""
    tools: tuple = ()  # names of the tools that answered
    errors: dict = field(default_factory=dict)  # tool name -> reauth | unavailable | timeout | error | denied
    changes_requested: bool = False
    topics: tuple = ()
    product: Optional[str] = None
    timings_ms: dict = field(default_factory=dict)  # tool name -> milliseconds (names and numbers only)


@dataclass
class _Result:
    name: str
    status: str  # ok | reauth | unavailable | error | denied
    detail: str = ""  # timeout | not_applicable | ""
    output: Any = None
    ms: float = 0.0


# Product type cache (in memory, per verified number, 10 minutes)


def _key(number: str) -> str:
    return hashlib.sha256(number.encode("ascii", "ignore")).hexdigest()[:20]


def _cached_product(number: str) -> Optional[str]:
    with _cache_lock:
        entry = _cache.get(_key(number))
        if entry and entry[0] > _now():
            return entry[1]
        _cache.pop(_key(number), None)
    return None


def _remember_product(number: str, product: str) -> None:
    with _cache_lock:
        if len(_cache) >= _CACHE_LIMIT:
            _cache.pop(next(iter(_cache)))
        _cache[_key(number)] = (_now() + PRODUCT_CACHE_SECONDS, product)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def product_from(output: Any) -> Optional[str]:
    """postpaid, lte_prepaid or None from the text of show_service_detail."""
    text = render.output_text(output)
    if _LTE_WORD.search(text) and "prepaid" in text and "postpaid" not in text:
        return LTE_PREPAID
    if "postpaid" in text or _FIXED_WORDS.search(text):
        return POSTPAID
    return None


# Failure classification


def classify_failure(exc: BaseException) -> tuple:
    """(status, detail) of an exception from a hub call."""
    from access import AccessDenied

    if isinstance(exc, AccessDenied):
        return "denied", ""
    if isinstance(exc, HubAuthError):
        # The hub refused OUR api key (HTTP 401 or 403). That is a setup problem
        # the customer cannot fix, so it must not ask them to verify again.
        return "unavailable", "auth"
    if isinstance(exc, (HubBreakerOpen, HubUnavailable)):
        reason = getattr(exc, "reason", "")
        return "unavailable", "timeout" if reason in ("timeout", "budget") else ""
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError, futures.TimeoutError)):
        return "unavailable", "timeout"
    if isinstance(exc, HubToolError):
        message = str(exc)
        if _AUTH_ERROR.search(message):
            return "reauth", ""
        return "error", "not_applicable" if _NOT_APPLICABLE.search(message) else ""
    return "error", ""


def _one(call: ToolCall, access, number: str, deadline: float) -> _Result:
    started = time.perf_counter()
    try:
        require_allowed(call.name)  # checked again right before the call
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return _Result(call.name, "unavailable", "timeout", ms=0.0)
        arguments = dict(call.arguments)
        # The access gate requires the verified customer's own number here and
        # rewrites it to the national form; no other number is ever sent.
        arguments["telephone_no"] = number
        output = hub_tools.call_tool_sync(call.name, arguments, remaining, access=access)
        message = render.payload_error(render.extract(output))
        ms = (time.perf_counter() - started) * 1000.0
        if message is None:
            return _Result(call.name, "ok", output=output, ms=ms)
        if _AUTH_ERROR.search(message):
            return _Result(call.name, "reauth", ms=ms)
        return _Result(call.name, "error", "not_applicable" if _NOT_APPLICABLE.search(message) else "", ms=ms)
    except ToolRefused:
        return _Result(call.name, "denied", ms=(time.perf_counter() - started) * 1000.0)
    except BaseException as exc:  # noqa: BLE001 - classified, never raised
        status, detail = classify_failure(exc)
        return _Result(call.name, status, detail, ms=(time.perf_counter() - started) * 1000.0)


def _run(calls: list, access, number: str, deadline: float) -> list:
    """All calls in parallel inside the shared deadline, results in call order."""
    if not calls:
        return []
    pending = [(call, _pool.submit(_one, call, access, number, deadline)) for call in calls]
    wait_for = max(deadline - time.monotonic(), 0.0) + 0.3
    done, _ = futures.wait([f for _, f in pending], timeout=wait_for)
    results = []
    for call, future in pending:
        if future in done:
            results.append(future.result())
        else:
            future.cancel()
            results.append(_Result(call.name, "unavailable", "timeout"))
    return results


def plan(text: str, language: Optional[str], access) -> list:
    """The ToolCalls (without telephone_no) a verified customer's message
    needs, using the cached product type when there is one."""
    found = intent_module.detect(text, language, access)
    number = normalize_phone(getattr(access, "phone_e164", None))
    return calls_for(found.topics, _cached_product(number) if number else None)


def _outcome(kind: str, language, found, results, product, started: float) -> CustomerOutcome:
    errors = {r.name: (r.detail or r.status) if r.status == "unavailable" else r.status
              for r in results if r.status != "ok"}
    out = CustomerOutcome(
        kind=kind,
        tools=tuple(r.name for r in results if r.status == "ok"),
        errors=errors,
        changes_requested=found.changes,
        topics=found.topics,
        product=product,
        timings_ms={r.name: round(r.ms, 1) for r in results},
    )
    if kind in ("reauth", "unavailable"):
        out.canned = canned("reauth_needed" if kind == "reauth" else "unavailable", language)
    total = (time.perf_counter() - started) * 1000.0
    timing = " ".join(f"{name}={ms:.0f}ms" for name, ms in out.timings_ms.items())
    print(f"Customer data: kind={kind} ok={list(out.tools)} errors={errors} {timing} total={total:.0f}ms")
    return out


def _fetch(text: str, language, access) -> CustomerOutcome:
    started = time.perf_counter()
    if not getattr(config, "CUSTOMER_TOOLS_ENABLED", True):
        return CustomerOutcome()
    if access is None or not getattr(access, "is_verified", False):
        return CustomerOutcome()
    e164 = normalize_phone(getattr(access, "phone_e164", None))
    if e164 is None:
        return CustomerOutcome()
    number = "0" + e164[3:]
    found = intent_module.detect(text, language, access)
    if not found.topics:
        return CustomerOutcome()

    deadline = time.monotonic() + max(float(getattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 8.0)), 0.1)
    product = _cached_product(number)
    results: list = []

    # Learn the product type first, unless both families need the same tools.
    postpaid_names = [c.name for c in calls_for(found.topics, POSTPAID)]
    lte_names = [c.name for c in calls_for(found.topics, LTE_PREPAID)]
    probed: dict = {}
    if product is None and postpaid_names != lte_names:
        probe = _run([ToolCall("show_service_detail", {}, "account")], access, number, deadline)[0]
        if probe.status in ("reauth", "unavailable"):
            return _outcome(probe.status, language, found, [probe], None, started)
        if probe.status == "ok":
            product = product_from(probe.output)
            if product:
                _remember_product(number, product)
        probed[probe.name] = probe
        results.append(probe)

    calls = [c for c in calls_for(found.topics, product) if c.name not in probed]
    results.extend(_run(calls, access, number, deadline))

    # Unknown product: the postpaid set was tried, try the LTE set once if every call said "not applicable".
    if product is None:
        fresh = [r for r in results if r.name not in probed]
        if fresh and all(r.status == "error" and r.detail == "not_applicable" for r in fresh):
            tried = {r.name for r in results}
            second = [c for c in calls_for(found.topics, LTE_PREPAID) if c.name not in tried]
            more = _run(second, access, number, deadline)
            results.extend(more)
            if any(r.status == "ok" for r in more):
                product = LTE_PREPAID
                _remember_product(number, product)

    answered = [r for r in results if r.status == "ok" and r.name in {c.name for c in calls_for(found.topics, product)}]
    if not answered:
        statuses = {r.status for r in results}
        if "reauth" in statuses:
            return _outcome("reauth", language, found, results, product, started)
        if statuses <= {"denied"}:
            return _outcome("none", language, found, results, product, started)
        return _outcome("unavailable", language, found, results, product, started)

    ordered = {c.name: i for i, c in enumerate(calls_for(found.topics, product))}
    answered.sort(key=lambda r: ordered.get(r.name, 99))
    blocks = [render.block(r.name, r.output) for r in answered]
    out = _outcome("data", language, found, results, product, started)
    out.tools = tuple(r.name for r in answered)
    out.context = render.assemble(blocks, int(getattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000)))
    return out


def fetch(text: str, language: Optional[str], access) -> CustomerOutcome:
    """Blocking, never raises. See the module docstring for the kinds."""
    try:
        return _fetch(text, language, access)
    except Exception as exc:  # noqa: BLE001 - an internal bug must not break the chat
        print(f"Customer data: internal error {type(exc).__name__}")
        return CustomerOutcome(
            kind="unavailable", canned=canned("unavailable", language), errors={"internal": "error"}
        )
