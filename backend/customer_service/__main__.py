"""Self check against the local mock hub: python -m customer_service (from backend/).

Starts mcp_client.mock_hub.MockHub with fake customers, never the real hub, and
prints per scenario the outcome kind, the tools that answered and the
milliseconds per call. It never prints customer data.
"""

import sys
import time

from access import GUEST, AccessContext
from mcp_client import client as hub_client
from mcp_client.mock_hub import DEFAULT_KEY, FIBRE_NUMBER, LTE_NUMBER, MockHub

from . import clear_cache, fetch


def _e164(national: str) -> str:
    return "+94" + national[1:]


def main() -> int:
    hub = MockHub(customer_tools=True)
    url = hub.start()
    hub.authenticated.update({FIBRE_NUMBER, LTE_NUMBER})
    hub_client.set_hub(hub_client.HubClient(url, "X-API-Key", DEFAULT_KEY, 3.0, 0, 30.0))
    fibre = AccessContext.verified(1, "Fake Customer", _e164(FIBRE_NUMBER))
    lte = AccessContext.verified(2, "Fake Customer", _e164(LTE_NUMBER))
    scenarios = [
        ("postpaid balance", fibre, "How much data do I have left?"),
        ("postpaid bill", fibre, "What is my bill this month?"),
        ("lte prepaid balance", lte, "What is my balance?"),
        ("lte prepaid reloads", lte, "What was my last payment"),
        ("change request", fibre, "I want to buy an extra data package"),
        ("guest", GUEST, "What is my balance?"),
        ("not an account question", fibre, "Tell me a joke"),
    ]
    failures = 0
    try:
        for label, ctx, text in scenarios:
            clear_cache()
            before = len(hub.tool_calls)
            started = time.perf_counter()
            out = fetch(text, "en", ctx)
            total = (time.perf_counter() - started) * 1000.0
            calls = len(hub.tool_calls) - before
            print(f"{label}: kind={out.kind} tools={list(out.tools)} changes={out.changes_requested} "
                  f"hub_calls={calls} total={total:.0f}ms per_call={out.timings_ms}")
            expected_none = ctx is GUEST or label.startswith("not an")
            if expected_none and (out.kind != "none" or calls):
                failures += 1
            if not expected_none and out.kind != "data":
                failures += 1
        hub.authenticated.clear()
        clear_cache()
        out = fetch("What is my balance?", "en", fibre)
        print(f"not signed in on the hub: kind={out.kind}")
        failures += out.kind != "reauth"
    finally:
        hub_client.set_hub(None)
        hub.stop()
    print("customer_service self check:", "OK" if not failures else f"{failures} FAILED")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
