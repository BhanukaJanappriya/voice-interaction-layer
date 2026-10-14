"""prepare(): decide how one user message is answered when ANSWER_SOURCE is
mcp_hub, and fetch what the answer must be built from.

Order of the decisions:
  1. confirmations.intercept / confirmations.start (when that module exists):
     a pending "yes or no" or a new state changing request is answered by it;
  2. a guest or unverified caller: small talk passes through with a clause that
     forbids SLT facts from the model's own knowledge; an account question is
     refused with the dial message (no tool call); everything else about SLT
     is a knowledge search;
  3. a verified customer: small talk as above; account topics through
     customer_service (balance, usage, bills, last payment, service detail,
     faults, packages, plus daily usage and compare_package when asked);
     everything else is a knowledge search.

Why tool calling is deterministic and not left to the model, per tier:
  * native function calling is NOT used for customer tools, because customer
    data must stay on the local Llama (owner decision, config
    CUSTOMER_DATA_TO_CLOUD_LLM is false by default) and the local model is
    asked to answer, not to drive tools;
  * a guest has exactly one tool, the knowledge search, so a model that is
    asked to call a tool could only call that one, or a forbidden one. A
    deterministic pre-fetch removes that risk and one model round trip.
The gateway still enforces the tool allowlist by tier and service type on
every call. schemas_for() exists for a future cloud function calling path.

Nothing here logs arguments, results, numbers or customer text: only the kind,
tool names and milliseconds.
"""

import sys
import time
from typing import Optional

import config

from . import customer, knowledge, stats
from .messages import STATUS_BY_TOOL, dial_text, guest_grounded_clause, no_facts_clause
from .plan import CANNED, GROUNDED, PASS, TurnPlan
from .smalltalk import is_smalltalk


def _confirmations():
    """The confirmations module, or None while it does not exist (it is built
    by another workstream, so it is imported only when needed)."""
    try:
        import confirmations  # noqa: PLC0415

        return confirmations
    except ImportError:
        return None


def _canned(text: str, status_key: Optional[str] = None, expired: bool = False) -> TurnPlan:
    return TurnPlan(kind=CANNED, canned=text, status_key=status_key, expired=expired)


def _intercepted(access, text: str, language) -> Optional[TurnPlan]:
    module = _confirmations()
    if module is None:
        return None
    for name in ("intercept", "start"):
        func = getattr(module, name, None)
        if not callable(func):
            continue
        try:
            result = func(access, text, language)
        except Exception as exc:  # noqa: BLE001 - a broken confirmation step must not break the chat
            print(f"Tool agent: confirmations.{name} failed ({type(exc).__name__})")
            continue
        if result is not None and getattr(result, "text", None):
            return _canned(result.text, getattr(result, "status_key", None))
    return None


def _knowledge(text: str, language, access, started: float, note: str = "", on_status=None) -> TurnPlan:
    if on_status is not None:
        try:
            on_status(STATUS_BY_TOOL[knowledge.TOOL])
        except Exception:  # noqa: BLE001
            pass
    # Room for two tries: a stalled first connection is retried (mcp_gateway).
    budget = max(float(getattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 8.0)), float(config.MCP_HUB_TIMEOUT_SECONDS))
    found = knowledge.search(text, access, budget=budget, language=language)
    tool_ms = int((time.perf_counter() - started) * 1000)
    stats.record(tool_ms, {knowledge.TOOL: found.ms})
    timings = {"tool_ms": tool_ms}
    if found.kind == "unavailable":
        plan = _canned(dial_text("tool_agent.hub_unavailable", language))
    elif found.kind == "reauth":
        from .messages import text as translate

        plan = _canned(translate("tool_agent.session_expired", language), expired=True)
    elif found.kind == "none":
        plan = _canned(dial_text("tool_agent.not_found", language))
    else:
        plan = TurnPlan(
            kind=GROUNDED,
            context=knowledge.context_of(found.chunks),
            clause=guest_grounded_clause(language) + (" " + note if note else ""),
            private=False,
            mode_suffix=" + SLT hub",
            status_key=STATUS_BY_TOOL[knowledge.TOOL],
            tools=(knowledge.TOOL,),
        )
    plan.timings = timings
    return plan


def _prepare(text: str, language, access, user_name, on_status=None) -> TurnPlan:
    started = time.perf_counter()
    if not isinstance(text, str) or not text.strip():
        return TurnPlan()
    done = _intercepted(access, text, language)
    if done is not None:
        return done

    verified = access is not None and bool(getattr(access, "is_verified", False))

    if is_smalltalk(text):
        return TurnPlan(kind=PASS, clause=no_facts_clause(language))

    if not verified:
        if getattr(config, "ACCESS_CONTROL_ENABLED", True):
            from access import GUEST, blocked_reply, evaluate

            if not evaluate(text, language, access or GUEST).allowed:
                return _canned(blocked_reply(language))
        return _knowledge(text, language, access, started, on_status=on_status)

    deadline = time.monotonic() + max(float(getattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 8.0)), 0.1)
    result = customer.run(text, language, access, deadline, on_status) if getattr(config, "CUSTOMER_TOOLS_ENABLED", True) else None
    if result is not None and result.plan is not None:
        plan = result.plan
        tool_ms = int((time.perf_counter() - started) * 1000)
        plan.timings = {"tool_ms": tool_ms}
        stats.record(tool_ms, result.timings)
        return plan
    note = ""
    if result is not None and result.missing:
        note = f"Not available for this question: {result.missing}. Say so."
    return _knowledge(text, language, access, started, note, on_status)


def prepare(text: str, language: Optional[str], access, user_name: Optional[str] = None, on_status=None) -> TurnPlan:
    """Blocking, never raises; run it on a worker thread. With ANSWER_SOURCE
    other than mcp_hub it returns kind "pass" and touches nothing."""
    if getattr(config, "ANSWER_SOURCE", "") != "mcp_hub":
        return TurnPlan()
    try:
        plan = _prepare(text, language, access, user_name, on_status)
    except Exception as exc:  # noqa: BLE001
        print(f"Tool agent: internal error {type(exc).__name__}", file=sys.stdout)
        return _canned(dial_text("tool_agent.hub_unavailable", language))
    print(f"Tool agent: kind={plan.kind} tools={list(plan.tools)} tool_ms={plan.timings.get('tool_ms', 0)}")
    return plan
