"""Hub client behaviour that does not depend on the transport: the key header
on every request (SSE connect and every message POST, streamable POSTs),
retries with backoff and jitter, no retry on an auth failure, the breaker,
typed failure reasons, and the additive /health keys. Uses the local mock
hub only."""

import asyncio
import socket

import pytest

import config
from conftest import require_module

KEY = "modes-test-key-SECRET-abcdef123456"


@pytest.fixture(scope="module")
def mod():
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    return type(
        "Mods",
        (),
        dict(
            errors=require_module("mcp_client.errors"),
            client=require_module("mcp_client.client"),
            mock=require_module("mcp_client.mock_hub"),
            lookup=require_module("mcp_client.customer_lookup"),
        ),
    )


def make(mod, url, key=KEY, retries=2, breaker=300, timeout=2.0, **kw):
    return mod.client.HubClient(url, "X-API-Key", key, timeout, retries, breaker, **kw)


@pytest.fixture(params=["sse", "streamable"])
def hub(request, mod):
    server = mod.mock.MockHub(mode=request.param, path="/mcp", api_key=KEY)
    server.start()
    yield server
    server.stop()


@pytest.fixture(autouse=True)
def _fast_jitter(mod, monkeypatch):
    # Real waits are short (0.25 s base); keep tests quick but real.
    monkeypatch.setattr(mod.client, "_BACKOFF_BASE_SECONDS", 0.05)


def test_header_is_on_every_request_including_follow_ups(mod, hub):
    client = make(mod, hub.url)
    hub.reset_counters()
    out = asyncio.run(client.call_tool("search_knowledgebase_tool", {"query": "roaming"}, trusted=True))
    assert "MOBITEL" in out
    assert len(hub.request_log) >= 3, hub.request_log  # connect, initialize, call at least
    assert all(hub.request_key_ok), list(zip(hub.request_log, hub.request_key_ok))
    assert hub.auth_failures == 0
    assert client.status()["auth"] == "accepted"


def test_a_request_without_the_key_would_have_been_noticed(mod, hub):
    wrong = make(mod, hub.url, key="not-the-key-at-all")
    hub.reset_counters()
    with pytest.raises(mod.errors.HubAuthError):
        asyncio.run(wrong.list_tools())
    assert hub.auth_failures >= 1
    assert not all(hub.request_key_ok)


def test_auth_failure_is_not_retried_and_is_typed(mod, hub):
    wrong = make(mod, hub.url, key="not-the-key-at-all", retries=3)
    with pytest.raises(mod.errors.HubAuthError) as caught:
        asyncio.run(wrong.list_tools())
    assert caught.value.reason == "auth_rejected"
    assert caught.value.status == 401
    assert wrong.attempts == 1
    status = wrong.status()
    assert status["auth"] == "rejected"
    assert status["last_error_reason"] == "auth_rejected"
    assert status["reachable"] is False
    assert "not-the-key" not in str(status)


def test_503_is_retried_then_succeeds(mod, hub):
    client = make(mod, hub.url, retries=2)
    hub.fail_next = 2
    tools = asyncio.run(client.list_tools())
    assert any(t.name == "search_knowledgebase_tool" for t in tools)
    assert client.attempts >= 2
    assert client.status()["reachable"] is True


def test_retries_stop_at_the_limit_and_open_the_breaker(mod, hub):
    client = make(mod, hub.url, retries=1)
    hub.fail_next = 1000
    with pytest.raises(mod.errors.HubUnavailable) as caught:
        asyncio.run(client.list_tools())
    assert caught.value.reason in ("http_status", "no_transport")
    first = client.attempts
    assert first >= 2  # the first try plus the retry
    assert client.status()["breaker_open"] is True
    hub.reset_counters()
    with pytest.raises(mod.errors.HubBreakerOpen) as breaker:
        asyncio.run(client.list_tools())
    assert breaker.value.reason == "breaker_open"
    assert hub.requests == 0
    assert client.attempts == first


def test_zero_retries_means_one_attempt_round(mod, hub):
    client = make(mod, hub.url, retries=0)
    hub.fail_next = 1000
    with pytest.raises(mod.errors.HubUnavailable):
        asyncio.run(client.list_tools())
    assert client.attempts <= 3  # at most one pass over the transport candidates


def test_backoff_uses_jitter_within_bounds(mod, monkeypatch):
    waits = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(mod.client.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(mod.client, "_BACKOFF_BASE_SECONDS", 1.0)
    monkeypatch.setattr(mod.client, "_JITTER", lambda: 0.0)
    client = make(mod, "http://127.0.0.1:9", retries=2, timeout=0.5)

    async def always_down(self, op):
        raise mod.errors.HubUnavailable("down", reason="connect")

    monkeypatch.setattr(mod.client.HubClient, "_with_session", always_down)

    async def go():
        with pytest.raises(mod.errors.HubUnavailable):
            await client._call(lambda s: None, budget=60.0)

    asyncio.run(go())
    assert waits == [0.5, 1.0]  # half of 1 s and half of 2 s at jitter 0
    waits.clear()
    monkeypatch.setattr(mod.client, "_JITTER", lambda: 1.0)
    client2 = make(mod, "http://127.0.0.1:9", retries=2, timeout=0.5)
    asyncio.run(_run_down(mod, client2))
    assert waits == [1.0, 2.0]


async def _run_down(mod, client):
    with pytest.raises(mod.errors.HubUnavailable):
        await client._call(lambda s: None, budget=60.0)


def test_connection_refused_has_a_connect_reason(mod):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    client = make(mod, f"http://127.0.0.1:{port}", retries=0, timeout=6.0)  # Windows refuses in about 2 s
    with pytest.raises(mod.errors.HubUnavailable) as caught:
        asyncio.run(client.list_tools())
    assert caught.value.reason == "connect"
    status = client.status()
    assert status["last_error_reason"] == "connect"
    assert status["reachable"] is False
    assert status["checked_at"]


def test_unknown_host_has_a_dns_reason(mod):
    client = make(mod, "http://no-such-host.invalid", retries=0)
    with pytest.raises(mod.errors.HubUnavailable) as caught:
        asyncio.run(client.list_tools())
    # A resolver that is slow to refuse a .invalid name shows up as a timeout
    # instead of a clean lookup failure; both are the same class of problem.
    assert caught.value.reason in ("dns", "timeout")


def test_not_configured_reason(mod):
    client = make(mod, "http://127.0.0.1:1", key="")
    with pytest.raises(mod.errors.HubUnavailable) as caught:
        asyncio.run(client.list_tools())
    assert caught.value.reason == "not_configured"


def test_status_has_the_additive_keys(mod, hub):
    client = make(mod, hub.url)
    before = client.status()
    assert before["auth"] == "unknown" and before["checked_at"] is None
    for key in ("configured", "enabled", "transport", "reachable", "breaker_open", "tools", "last_error"):
        assert key in before  # the old keys are all still there
    asyncio.run(client.list_tools())
    after = client.status()
    assert after["auth"] == "accepted"
    assert after["checked_at"].endswith("+00:00")
    assert after["transport"] in ("sse", "streamable-http")
    assert after["reachable"] is True
    assert after["last_error"] is None and after["last_error_reason"] is None
    assert KEY not in str(after)


def test_a_tool_error_counts_as_the_hub_answering(mod):
    server = mod.mock.MockHub(mode="sse", api_key=KEY)
    server.start()
    try:
        client = make(mod, server.url)
        with pytest.raises(mod.errors.HubToolError) as caught:
            asyncio.run(client.call_tool("failing_tool", {"query": "x"}, trusted=True))
        assert caught.value.reason == "tool_error"
        status = client.status()
        assert status["reachable"] is True and status["auth"] == "accepted"
        assert status["breaker_open"] is False
    finally:
        server.stop()


def test_the_access_gate_still_blocks_a_customer_tool_for_a_guest(mod, hub, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_CONTROL_ENABLED", True)
    client = make(mod, hub.url)
    hub.reset_counters()
    with pytest.raises(Exception) as caught:
        asyncio.run(client.call_tool("show_gb_balance", {"telephone_no": "0112345678"}))
    assert type(caught.value).__name__ == "AccessDenied"
    assert hub.requests == 0  # refused before any network traffic


# The lookup class


def test_lookup_without_a_tool_is_unavailable_not_not_found(mod, monkeypatch):
    monkeypatch.setattr(config, "MCP_HUB_CUSTOMER_LOOKUP_TOOL", "")
    lookup = mod.lookup.HubCustomerLookup()
    with pytest.raises(mod.lookup.CustomerLookupUnavailable) as caught:
        lookup.find("+94112345678")
    assert caught.value.reason == "no_lookup_tool"
    assert lookup.available() is False


def test_selector_defaults_to_local_and_picks_the_hub(mod):
    pytest.importorskip("verification.lookup")
    from verification.lookup import CustomerDirectory

    assert isinstance(mod.lookup.select_customer_lookup("local"), CustomerDirectory)
    assert isinstance(mod.lookup.select_customer_lookup(None), CustomerDirectory)
    assert isinstance(mod.lookup.select_customer_lookup("mcp_hub"), mod.lookup.HubCustomerLookup)


def test_lookup_through_the_mock_hub_tool(mod, monkeypatch):
    pytest.importorskip("verification.lookup")
    tools = require_module("mcp_client.tools")
    server = mod.mock.MockHub(mode="sse", api_key=KEY)
    server.start()
    try:
        mod.client.set_hub(make(mod, server.url))
        tools._cache.clear()
        monkeypatch.setattr(config, "MCP_HUB_TIMEOUT_SECONDS", 3.0)
        lookup = mod.lookup.HubCustomerLookup(tool_name="find_customer")
        record = lookup.find("+94112345678")
        assert record is not None
        assert record.display_name == "Nimal Perera"
        assert record.mobile_e164 == "+94771234567"
        assert lookup.get(record.id) is record
        assert "ACC-0001" not in repr(record)  # internal fields are dropped
        server.customer = None
        assert lookup.find("+94112345678") is None  # found: false means not a customer
        missing = mod.lookup.HubCustomerLookup(tool_name="no_such_tool")
        with pytest.raises(mod.lookup.CustomerLookupUnavailable) as caught:
            missing.find("+94112345678")
        assert caught.value.reason == "tool_not_listed"
    finally:
        mod.client.set_hub(None)
        server.stop()
