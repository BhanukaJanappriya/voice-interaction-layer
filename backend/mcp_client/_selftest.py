"""Self test for the MCP hub client, against local mock hubs only.

    python -m backend.mcp_client._selftest        (from the repository root)

The real hub is NOT contacted: it answers 401 without a key and no key
exists here. Everything below runs against mock_hub.MockHub, which speaks
real MCP through the SDK server side on both transports. A pass therefore
shows the client follows the protocol and handles the failure modes; it
shows nothing about the real hub's tool names or result shapes.
"""

import asyncio
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

_TMP = tempfile.mkdtemp(prefix="mcp_client_selftest_")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/selftest.db".replace("\\", "/")
os.environ["JWT_SECRET"] = "selftest-secret-selftest-secret-selftest-secret"
os.environ["FIELD_ENCRYPTION_KEY"] = "unused-here"
for _name in ("MCP_HUB_API_KEY", "MCP_HUB_TOOLS", "MCP_HUB_CUSTOMER_LOOKUP_TOOL"):
    os.environ[_name] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

config.MCP_HUB_IN_REPLIES = True  # the self test exercises the reply path against the mock hub
from mcp_client import tools as hub_tools  # noqa: E402
from mcp_client.client import HubClient, set_hub  # noqa: E402
from mcp_client.errors import HubAuthError, HubBreakerOpen, HubToolError, HubUnavailable  # noqa: E402
from mcp_client.mock_hub import DEFAULT_KEY, MockHub  # noqa: E402

_results = []


def check(name, condition, info=""):
    _results.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{('  ' + str(info)) if info and not condition else ''}")


def make_client(hub: MockHub, **overrides) -> HubClient:
    params = dict(
        url=hub.url, auth_header=hub.auth_header, api_key=hub.api_key, timeout=6.0, retries=2, breaker_seconds=300
    )
    params.update(overrides)
    return HubClient(**params)


def run(coro):
    return asyncio.run(coro)


def expect(_exc_type, coro):
    try:
        run(coro)
    except Exception as exc:  # noqa: BLE001  (a wrong type is returned too, so the check shows it)
        return exc
    return None


def set_config(**values):
    for key, value in values.items():
        setattr(config, key, value)


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


BASE_TOOLS = ["search_hub", "plan_notes"]


def main() -> int:
    logging.disable(logging.CRITICAL)  # the SDK and the mock log tracebacks for the failures we provoke on purpose

    print("transport detection")
    hub = MockHub("streamable", "/mcp")
    hub.start()
    client = make_client(hub)
    tool_list = run(client.list_tools())
    names = {t.name for t in tool_list}
    check("streamable at /mcp found from the bare URL", "search_hub" in names, names)
    check("detected transport is cached as streamable-http", client.status()["transport"] == "streamable-http")
    hub.reset_counters()
    text = run(client.call_tool("search_hub", {"query": "mobitel 1598"}))
    check("call_tool returns the text", "1598" in text, text)
    check(
        "cached transport: no probing of the bare URL again",
        all(path == "/mcp" for _m, path in hub.request_log),
        hub.request_log,
    )
    check("tool arguments arrive intact", hub.tool_calls == [("search_hub", {"query": "mobitel 1598"})], hub.tool_calls)
    one_string = {t.name for t in tool_list if t.required_string_argument()}
    check(
        "schema rule: exactly one required string",
        "search_hub" in one_string and "two_args" not in one_string and "count_items" not in one_string,
        one_string,
    )
    hub.stop()

    hub = MockHub("streamable", "/")
    hub.start()
    client = make_client(hub)
    check("streamable at the root found", "search_hub" in {t.name for t in run(client.list_tools())})
    check("root transport status", client.status()["transport"] == "streamable-http")
    hub.stop()

    hub = MockHub("streamable", "/mcp", json_response=True)
    hub.start()
    client = make_client(hub)
    check("streamable with plain JSON responses works", "search_hub" in {t.name for t in run(client.list_tools())})
    hub.stop()

    hub = MockHub("sse")
    hub.start()
    client = make_client(hub)
    check("SSE hub found (SSE is tried first)", "search_hub" in {t.name for t in run(client.list_tools())})
    check("SSE transport status", client.status()["transport"] == "sse", client.status())
    check("SSE call_tool", "1598" in run(client.call_tool("search_hub", {"query": "x"})))
    hub.stop()

    hub = MockHub("sse")
    hub.start()
    client = make_client(hub, url=hub.url + "/sse")
    check("a URL already ending in /sse is used as given", "search_hub" in {t.name for t in run(client.list_tools())})
    hub.stop()

    print("authentication")
    hub = MockHub("streamable", "/mcp")
    hub.start()
    client = make_client(hub, api_key="wrong-key-value")
    error = expect(HubAuthError, client.list_tools())
    check("401 raises HubAuthError", isinstance(error, HubAuthError), error)
    check("auth error is never retried (one network attempt)", client.attempts == 1, client.attempts)
    check("hub saw exactly one refused request", hub.auth_failures == 1, hub.auth_failures)
    status = client.status()
    check("status shows the failure, breaker open", status["breaker_open"] and status["reachable"] is False)
    check("status and error never contain the key", "wrong-key-value" not in json.dumps(status))
    before = hub.requests
    error = expect(HubBreakerOpen, client.list_tools())
    check("open breaker answers without any network traffic", isinstance(error, HubBreakerOpen) and hub.requests == before)
    hub.stop()

    hub = MockHub("streamable", "/mcp", auth_header="Authorization", api_key="Bearer abc123token")
    hub.start()
    client = make_client(hub, auth_header="Authorization", api_key="abc123token")
    check("Authorization header gets the Bearer prefix", "search_hub" in {t.name for t in run(client.list_tools())})
    hub.stop()

    hub = MockHub("sse", auth_header="X-Hub-Token")
    hub.start()
    client = make_client(hub, auth_header="X-Hub-Token")
    check("custom header name is honoured (SSE)", "search_hub" in {t.name for t in run(client.list_tools())})
    wrong = make_client(hub, auth_header="X-Hub-Token", api_key="nope")
    check("SSE 401 raises HubAuthError", isinstance(expect(HubAuthError, wrong.list_tools()), HubAuthError))
    hub.stop()

    print("timeout, retry, breaker")
    hub = MockHub("streamable", "/mcp")
    hub.start()
    hub.slow_seconds = 4.0
    client = make_client(hub, timeout=0.8, retries=0)
    started = time.monotonic()
    error = expect(HubUnavailable, client.call_tool("slow_search", {"query": "x"}))
    elapsed = time.monotonic() - started
    check("slow tool: timeout raises HubUnavailable", isinstance(error, HubUnavailable), error)
    check("timeout is honoured, not the tool's 4 s", elapsed < 3.0, f"{elapsed:.1f}s")
    check("timeout opens the breaker", client.status()["breaker_open"])
    hub.stop()

    hub = MockHub("streamable", "/mcp")
    hub.start()
    client = make_client(hub, retries=2)
    run(client.list_tools())  # detect first, so 503s hit the cached transport
    hub.fail_next = 2
    client.attempts = 0
    started = time.monotonic()
    text = run(client.call_tool("search_hub", {"query": "x"}))
    check("two 503s then success: retried and succeeded", "1598" in text and client.attempts == 3, client.attempts)
    check("retry backoff is short", time.monotonic() - started < 5)
    check("success leaves the breaker closed and reachable", client.status()["reachable"] and not client.status()["breaker_open"])

    hub.fail_next = 50
    client2 = make_client(hub, retries=1)
    client2._transport = client._transport
    error = expect(HubUnavailable, client2.call_tool("search_hub", {"query": "x"}))
    check("persistent 503: gives up after bounded retries", isinstance(error, HubUnavailable) and client2.attempts == 2, client2.attempts)
    check("and opens the breaker", client2.status()["breaker_open"])
    hub.fail_next = 0

    clock = FakeClock()
    client3 = make_client(hub, retries=0, breaker_seconds=300, clock=clock)
    hub.fail_next = 10
    expect(HubUnavailable, client3.list_tools())
    check("breaker open right after failure", client3.status()["breaker_open"])
    hub.fail_next = 0
    before = hub.requests
    check("breaker holds while the window runs", isinstance(expect(HubBreakerOpen, client3.list_tools()), HubBreakerOpen) and hub.requests == before)
    clock.now += 299
    check("still open at 299 s", client3.status()["breaker_open"])
    clock.now += 2
    check("breaker closes after the window and the hub is tried again", "search_hub" in {t.name for t in run(client3.list_tools())})
    check("breaker state cleared on success", not client3.status()["breaker_open"])
    hub.stop()

    client = make_client(hub, retries=1, timeout=2.0)
    started = time.monotonic()
    error = expect(HubUnavailable, client.list_tools())
    check("hub down (connection refused): HubUnavailable, quickly", isinstance(error, HubUnavailable) and time.monotonic() - started < 6, error)

    unconfigured = HubClient(url="http://x", auth_header="X-API-Key", api_key="", timeout=1, retries=0, breaker_seconds=1)
    check("no key: unconfigured and HubUnavailable without any call", not unconfigured.configured and isinstance(expect(HubUnavailable, unconfigured.list_tools()), HubUnavailable))

    print("tool errors")
    hub = MockHub("streamable", "/mcp")
    hub.start()
    client = make_client(hub)
    error = expect(HubToolError, client.call_tool("failing_tool", {"query": "x"}))
    check("isError result raises HubToolError", isinstance(error, HubToolError), error)
    check("a tool error does not open the breaker", not client.status()["breaker_open"] and client.status()["reachable"] is True)
    error = expect(HubToolError, client.call_tool("no_such_tool", {"query": "x"}))
    check("unknown tool raises HubToolError", isinstance(error, HubToolError), error)
    hub.stop()

    print("hub_context_for")
    hub = MockHub("streamable", "/mcp", tools=["search_hub", "plan_notes", "two_args", "count_items"])
    hub.start()
    set_config(MCP_HUB_ENABLED=True, MCP_HUB_API_KEY=DEFAULT_KEY, MCP_HUB_URL=hub.url, MCP_HUB_TIMEOUT_SECONDS=6.0, MCP_HUB_TOOLS=BASE_TOOLS, MCP_HUB_CUSTOMER_LOOKUP_TOOL="")
    client = make_client(hub)
    set_hub(client)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        notes = hub_tools.hub_context_for("what does mobitel 1598 cost")
    printed = out.getvalue()
    check("notes block with the heading", notes is not None and notes.startswith(hub_tools.HUB_HEADER), notes)
    called = {name for name, _ in hub.tool_calls}
    check("only the named tools were called", called == {"search_hub", "plan_notes"}, called)
    check("tools were called with the user text", all(list(args.values())[0] == "what does mobitel 1598 cost" for _n, args in hub.tool_calls))
    check("prints 'MCP hub latency: N ms'", "MCP hub latency:" in printed and " ms" in printed, printed)
    plan_block = notes.split("[plan_notes]")[1] if notes else ""
    check("each tool's text is capped near 700 chars", len(plan_block) <= 720 and plan_block.rstrip().endswith("..."), len(plan_block))
    check("key never printed", DEFAULT_KEY not in printed)
    hub.reset_counters()
    again = hub_tools.hub_context_for("what does mobitel 1598 cost")
    check("same question again is served from the short cache", again == notes and not hub.tool_calls)

    set_config(MCP_HUB_TOOLS=["plan_notes", "missing_tool"])
    hub.reset_counters()
    notes = hub_tools.hub_context_for("tell me about plans")
    check("MCP_HUB_TOOLS restricts the tools (unknown names ignored)", {n for n, _ in hub.tool_calls} == {"plan_notes"} and notes and "[plan_notes]" in notes, hub.tool_calls)
    set_config(MCP_HUB_TOOLS=BASE_TOOLS)

    hub.search_text = ""
    set_config(MCP_HUB_TOOLS=["search_hub"])
    check("empty tool output gives None", hub_tools.hub_context_for("anything else here") is None)
    hub.search_text = "ok"
    set_config(MCP_HUB_TOOLS=BASE_TOOLS)

    results = []
    with ThreadPoolExecutor(8) as pool:
        futures = [pool.submit(hub_tools.hub_context_for, f"question number {n}") for n in range(8)]
        results = [f.result(timeout=30) for f in futures]
    check("callable from 8 worker threads at once", all(r and "[search_hub]" in r for r in results))

    async def inside_running_loop():
        return hub_tools.hub_context_for("called inside a running loop")

    check("callable from a thread that has a running event loop", "[search_hub]" in (run(inside_running_loop()) or ""))

    set_config(MCP_HUB_ENABLED=False)
    set_hub(make_client(hub, enabled=False))
    check("None when MCP_HUB_ENABLED is off", hub_tools.hub_context_for("zzz off") is None)
    set_hub(make_client(hub, enabled=True))
    set_config(MCP_HUB_ENABLED=True, MCP_HUB_API_KEY="")
    set_hub(make_client(hub, api_key=""))
    check("None when no key is configured", hub_tools.hub_context_for("zzz nokey") is None)
    set_config(MCP_HUB_API_KEY=DEFAULT_KEY)

    wrong = make_client(hub, api_key="bad")
    set_hub(wrong)
    check("401 from the hub: None, no exception", hub_tools.hub_context_for("zzz wrongkey") is None)
    before = hub.requests
    check("then the breaker keeps later turns off the network", hub_tools.hub_context_for("zzz again") is None and hub.requests == before)
    hub.stop()
    dead = make_client(hub, retries=0, timeout=1.0)
    set_hub(dead)
    started = time.monotonic()
    check("hub down: None, no exception", hub_tools.hub_context_for("zzz dead") is None)
    check("hub down: the turn is not held up long", time.monotonic() - started < 5)
    check("status() reports the breaker", hub_tools.status()["breaker_open"] is True and "last_error" in hub_tools.status())

    print("compose_tool_callbacks")

    def retrieval(text):
        time.sleep(0.2)
        return "RETRIEVED " + text

    def hub_cb(text):
        return "HUB " + text

    def broken(text):
        raise RuntimeError("boom")

    def nothing(text):
        return None

    composed = hub_tools.compose_tool_callbacks(retrieval, hub_cb)
    check("outputs joined, retrieval first even when slower", composed("q") == "RETRIEVED q\n\nHUB q", composed("q"))
    check("None and failing callbacks are skipped", hub_tools.compose_tool_callbacks(nothing, broken, hub_cb)("q") == "HUB q")
    check("all empty gives None", hub_tools.compose_tool_callbacks(nothing, broken)("q") is None)
    check("no callbacks gives None", hub_tools.compose_tool_callbacks()("q") is None)
    check("None entries tolerated", hub_tools.compose_tool_callbacks(None, hub_cb)("q") == "HUB q")
    started = time.monotonic()

    def slow_a(text):
        time.sleep(0.4)
        return "A"

    def slow_b(text):
        time.sleep(0.4)
        return "B"

    hub_tools.compose_tool_callbacks(slow_a, slow_b)("q")
    check("callbacks run side by side", time.monotonic() - started < 0.7, f"{time.monotonic() - started:.2f}s")

    print("default allowlist and denylist")
    hub = MockHub("sse")
    hub.start()
    set_config(MCP_HUB_API_KEY=DEFAULT_KEY, MCP_HUB_URL=hub.url, MCP_HUB_CUSTOMER_LOOKUP_TOOL="", MCP_HUB_TOOLS=[])
    set_hub(make_client(hub))
    notes = hub_tools.hub_context_for("which plan is cheapest")
    called = [n for n, _ in hub.tool_calls]
    check("default: exactly search_knowledgebase_tool is called", called == ["search_knowledgebase_tool"], called)
    check("its answer reaches the notes", notes is not None and hub.search_text in notes, notes)
    check("SSE hub: only /sse and /messages/ were touched", {p for _m, p in hub.request_log} <= {"/sse", "/messages/"}, set(hub.request_log))
    hub.reset_counters()
    hub_tools._cache.clear()
    set_config(MCP_HUB_TOOLS=["purchase_extra_gb", "search_knowledgebase_tool", "validate_customer_and_send_otp", "show_gb_balance"])
    hub_tools.hub_context_for("denylist check")
    called = {n for n, _ in hub.tool_calls}
    check("purchase_, otp and other reserved tools refused even when listed", "purchase_extra_gb" not in called and "validate_customer_and_send_otp" not in called, called)
    for prefix_name in ("add_x", "subscribe_x", "log_x", "refresh_x"):
        tool = hub_tools.HubTool(prefix_name, "", {"required": ["q"], "properties": {"q": {"type": "string"}}})
        check(f"{prefix_name} denied", hub_tools.eligible_tools([tool], [prefix_name]) == [])
    transport_list = [t.name for t in run(make_client(hub).list_tools())]
    check("real hub tool names are visible to the client", "search_knowledgebase_tool" in transport_list and "show_gb_balance" in transport_list)
    hub.stop()
    set_config(MCP_HUB_TOOLS=BASE_TOOLS)

    print("customer lookup")
    hub = MockHub("streamable", "/mcp", tools=["find_customer", "search_hub"])
    hub.start()
    set_config(MCP_HUB_API_KEY=DEFAULT_KEY, MCP_HUB_URL=hub.url, MCP_HUB_CUSTOMER_LOOKUP_TOOL="find_customer", MCP_HUB_TOOLS=["search_hub", "find_customer"])
    set_hub(make_client(hub))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        found = hub_tools.lookup_customer_via_hub("+94112345678")
    check(
        "lookup returns only the minimum fields, mobile as E.164",
        found == {"display_name": "Nimal Perera", "mobile": "+94771234567", "email": "nimal@example.lk"},
        found,
    )
    check("lookup tool got the number", hub.tool_calls[-1] == ("find_customer", {"phone": "+94112345678"}), hub.tool_calls)
    check("lookup logs nothing personal", "Nimal" not in out.getvalue() and "0771234567" not in out.getvalue() and "+94112345678" not in out.getvalue())
    hub.reset_counters()
    ctx = hub_tools.hub_context_for("what is the price")
    check("the lookup tool is never used for chat context", all(n != "find_customer" for n, _ in hub.tool_calls), hub.tool_calls)
    hub.customer = None
    check("unknown customer gives None", hub_tools.lookup_customer_via_hub("+94112345678") is None)
    hub.customer = {"display_name": "Only Name"}
    check("customer without contact details still parsed", hub_tools.lookup_customer_via_hub("+94112345678") == {"display_name": "Only Name", "mobile": None, "email": None})
    set_config(MCP_HUB_CUSTOMER_LOOKUP_TOOL="")
    check("no lookup tool configured gives None", hub_tools.lookup_customer_via_hub("+94112345678") is None)
    set_config(MCP_HUB_CUSTOMER_LOOKUP_TOOL="not_in_hub")
    check("lookup tool missing from the hub gives None", hub_tools.lookup_customer_via_hub("+94112345678") is None)
    hub.stop()
    set_config(MCP_HUB_CUSTOMER_LOOKUP_TOOL="find_customer")
    set_hub(make_client(hub, retries=0, timeout=1.0))
    check("hub down gives None, no exception", hub_tools.lookup_customer_via_hub("+94112345678") is None)

    parse = hub_tools.parse_customer
    check("parse: structured content with a wrapper", parse({"customer": {"name": "A B", "msisdn": "94712345678", "email_address": "a@b.lk"}}, "") == {"display_name": "A B", "mobile": "+94712345678", "email": "a@b.lk"})
    check("parse: JSON text, list of records", parse(None, '[{"full_name": "C D", "mobile_number": "071 234 5678"}]') == {"display_name": "C D", "mobile": "+94712345678", "email": None})
    check("parse: found false", parse({"found": False}, "") is None)
    check("parse: free text is not guessed at", parse(None, "Customer: Nimal, 0771234567") is None)
    check("parse: landline in the mobile field is rejected", parse({"name": "E", "mobile": "0112345678"}, "")["mobile"] is None)
    check("parse: bad email dropped", parse({"name": "E", "email": "not an email"}, "")["email"] is None)

    set_hub(None)
    print("status")
    s = hub_tools.status()
    check("status has the documented keys", {"configured", "enabled", "transport", "reachable", "breaker_open", "tools", "last_error"} <= set(s), s)

    failed = [name for name, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
    if failed:
        print("FAILED:", *failed, sep="\n  ")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    os._exit(code)  # daemon threads (mock hubs, loop thread) must not hold the exit
