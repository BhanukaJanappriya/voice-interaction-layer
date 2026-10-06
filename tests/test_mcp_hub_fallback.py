"""MCP hub as an MCP client (R4): when the hub is down, rejects the key or is
too slow, the Orchestrator must still answer through the retrieval path;
the breaker must open and stop further calls; the key must never leak.

Uses the local mock hub that ships with the client (mcp_client.mock_hub),
a real MCP server over HTTP on 127.0.0.1, plus a closed port for "down".
"""

import asyncio
import logging
import socket
import time

import pytest

import agent
import config
from conftest import make_client, require_module

KEY = "hub-test-key-SECRET-abcdef123456"
WRONG_KEY = "wrong-hub-key-SECRET-999888"
FACT = "QA-FACT: the Roaming Saver pack costs Rs 999 for 7 days."
LOCAL_MODE = "local single agent (Llama via Ollama)"


@pytest.fixture(scope="module")
def mod():
    require_module("mcp_client.errors", "backend/mcp_client not delivered yet")
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    return type(
        "Mods",
        (),
        dict(
            errors=require_module("mcp_client.errors"),
            client=require_module("mcp_client.client", "mcp_client.client not delivered yet"),
            tools=require_module("mcp_client.tools", "mcp_client.tools not delivered yet"),
            mock=require_module("mcp_client.mock_hub", "mcp_client.mock_hub not delivered yet"),
        ),
    )


@pytest.fixture(scope="module")
def mock_hub(mod):
    hub = mod.mock.MockHub(mode="streamable", api_key=KEY)
    hub.start()
    yield hub
    hub.stop()


@pytest.fixture(autouse=True)
def _isolate(mod, monkeypatch):
    """No process wide client and no cached notes leak between tests."""
    mod.client.set_hub(None)
    mod.tools._cache.clear()
    monkeypatch.setattr(config, "MCP_HUB_TOOLS", ["search_hub"])
    monkeypatch.setattr(config, "MCP_HUB_CUSTOMER_LOOKUP_TOOL", "")
    monkeypatch.setattr(config, "MCP_HUB_TIMEOUT_SECONDS", 1.0)
    yield
    mod.client.set_hub(None)
    mod.tools._cache.clear()


@pytest.fixture
def mock_hub_reset(mock_hub):
    mock_hub.fail_next = 0
    mock_hub.slow_seconds = 3.0
    mock_hub.reset_counters()
    return mock_hub


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def install(mod):
    """Installs a process wide hub client and returns it."""

    def _install(url, key=KEY, timeout=1.0, retries=0, breaker=300.0, clock=None, enabled=True):
        kwargs = {"enabled": enabled}
        if clock is not None:
            kwargs["clock"] = clock
        hub = mod.client.HubClient(url, "X-API-Key", key, timeout, retries, breaker, **kwargs)
        mod.client.set_hub(hub)
        return hub

    return _install


def closed_port_url():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}"


def local_answer(fake_ollama):
    return fake_ollama.state.__setitem__("reply", lambda payload: "Local reply.")


def ask(callback, fake_ollama, text="how much is roaming?"):
    orchestrator = agent.Orchestrator(tool_callback=callback)
    reply = "".join(orchestrator.respond_stream(text, "Nimal", "en"))
    return orchestrator, reply


# Typed errors


def test_error_hierarchy(mod):
    e = mod.errors
    for name in ("HubUnavailable", "HubAuthError", "HubToolError", "HubBreakerOpen"):
        assert issubclass(getattr(e, name), e.HubError), name
    assert issubclass(e.HubError, Exception)


# Hub failures never reach the user


def test_hub_down_returns_none_quickly_and_opens_the_breaker(mod, install):
    hub = install(closed_port_url())
    started = time.monotonic()
    assert mod.tools.hub_context_for("how much is roaming?") is None
    assert time.monotonic() - started < 10
    status = hub.status()
    assert status["reachable"] is False and status["breaker_open"] is True
    assert status["last_error"]


def test_hub_401_returns_none_and_is_not_hammered(mod, install, mock_hub_reset):
    hub = install(mock_hub_reset.url, key=WRONG_KEY, retries=2)
    assert mod.tools.hub_context_for("how much is roaming?") is None
    assert hub.status()["breaker_open"] is True
    seen = mock_hub_reset.requests
    assert seen <= 3, f"an auth failure must not be retried ({seen} requests)"
    for _ in range(3):
        assert mod.tools.hub_context_for("another roaming question?") is None
    assert mock_hub_reset.requests == seen, "the open breaker must skip the network"


def test_hub_401_error_text_has_no_key(mod, install, mock_hub_reset):
    hub = install(mock_hub_reset.url, key=WRONG_KEY)
    mod.tools.hub_context_for("hello there")
    assert WRONG_KEY not in str(hub.status())


def test_hub_timeout_returns_none_and_trips_the_breaker(mod, install, mock_hub_reset, monkeypatch):
    monkeypatch.setattr(config, "MCP_HUB_TOOLS", ["slow_search"])
    monkeypatch.setattr(config, "MCP_HUB_TIMEOUT_SECONDS", 0.6)
    mock_hub_reset.slow_seconds = 5.0
    hub = install(mock_hub_reset.url, timeout=0.6)
    started = time.monotonic()
    assert mod.tools.hub_context_for("slow question please") is None
    assert time.monotonic() - started < 5.0, "the hub call must be bounded by the timeout"
    assert hub.status()["breaker_open"] is True


def test_hub_503_opens_breaker_then_skips_the_network(mod, install, mock_hub_reset):
    mock_hub_reset.fail_next = 1000
    hub = install(mock_hub_reset.url)
    assert mod.tools.hub_context_for("roaming question one") is None
    assert hub.status()["breaker_open"] is True
    seen = mock_hub_reset.requests
    assert seen >= 1
    assert mod.tools.hub_context_for("roaming question two") is None
    assert mock_hub_reset.requests == seen


def test_breaker_closes_after_the_window_and_the_hub_is_tried_again(mod, install, mock_hub_reset):
    clock = Clock()
    mock_hub_reset.fail_next = 1
    hub = install(mock_hub_reset.url, breaker=60.0, clock=clock)
    assert mod.tools.hub_context_for("first roaming question") is None
    assert hub.status()["breaker_open"] is True
    clock.now += 61
    assert hub.status()["breaker_open"] is False
    notes = mod.tools.hub_context_for("second roaming question")
    assert notes and mock_hub_reset.search_text in notes
    status = hub.status()
    assert status["reachable"] is True and status["breaker_open"] is False


def test_open_breaker_raises_typed_error_without_a_network_call(mod, install, mock_hub_reset):
    mock_hub_reset.fail_next = 1000
    hub = install(mock_hub_reset.url)
    mod.tools.hub_context_for("trip it")
    seen = mock_hub_reset.requests
    with pytest.raises(mod.errors.HubBreakerOpen):
        asyncio.run(hub.list_tools())
    assert mock_hub_reset.requests == seen


def test_unconfigured_hub_makes_no_network_call(mod, install, mock_hub_reset):
    hub = install(mock_hub_reset.url, key="")
    assert mod.tools.hub_context_for("hello") is None
    disabled = install(mock_hub_reset.url, enabled=False)
    assert mod.tools.hub_context_for("hello again") is None
    assert mock_hub_reset.requests == 0
    assert hub.status()["configured"] is False and disabled.status()["enabled"] is False


def test_empty_text_is_not_sent_to_the_hub(mod, install, mock_hub_reset):
    install(mock_hub_reset.url)
    assert mod.tools.hub_context_for("") is None
    assert mod.tools.hub_context_for("   ") is None
    assert mock_hub_reset.requests == 0


def test_hub_context_for_never_raises(mod, monkeypatch):
    def explode():
        raise RuntimeError("get_hub exploded")

    monkeypatch.setattr(mod.tools, "get_hub", explode)
    assert mod.tools.hub_context_for("anything") is None


# The Orchestrator still answers


@pytest.mark.parametrize("scenario", ["down", "401", "503", "timeout"])
def test_orchestrator_answers_through_retrieval_when_the_hub_fails(
    scenario, mod, install, mock_hub_reset, fake_ollama, monkeypatch
):
    if scenario == "down":
        install(closed_port_url())
    elif scenario == "401":
        install(mock_hub_reset.url, key=WRONG_KEY)
    elif scenario == "503":
        mock_hub_reset.fail_next = 1000
        install(mock_hub_reset.url)
    else:
        monkeypatch.setattr(config, "MCP_HUB_TOOLS", ["slow_search"])
        monkeypatch.setattr(config, "MCP_HUB_TIMEOUT_SECONDS", 0.6)
        mock_hub_reset.slow_seconds = 5.0
        install(mock_hub_reset.url, timeout=0.6)

    callback = mod.tools.compose_tool_callbacks(lambda text: FACT, mod.tools.hub_context_for)
    orchestrator, reply = ask(callback, fake_ollama)
    assert reply == "Local reply."
    assert orchestrator.last_mode == LOCAL_MODE
    system = fake_ollama.payloads[-1]["system"]
    assert FACT in system, "retrieval output must survive a failing hub"
    assert mod.tools.HUB_HEADER not in system


def test_hub_notes_are_added_to_retrieval_not_instead_of_it(mod, install, mock_hub_reset, fake_ollama):
    install(mock_hub_reset.url)
    callback = mod.tools.compose_tool_callbacks(lambda text: FACT, mod.tools.hub_context_for)
    ask(callback, fake_ollama)
    system = fake_ollama.payloads[-1]["system"]
    assert FACT in system
    assert mock_hub_reset.search_text in system
    assert system.index(FACT) < system.index(mock_hub_reset.search_text), "retrieval comes first"


def test_the_hub_alone_can_ground_the_answer_when_retrieval_has_nothing(mod, install, mock_hub_reset, fake_ollama):
    install(mock_hub_reset.url)
    callback = mod.tools.compose_tool_callbacks(lambda text: None, mod.tools.hub_context_for)
    ask(callback, fake_ollama)
    assert mock_hub_reset.search_text in fake_ollama.payloads[-1]["system"]


# compose_tool_callbacks


def test_compose_joins_non_empty_outputs_in_order(mod):
    composed = mod.tools.compose_tool_callbacks(lambda t: "first", lambda t: None, lambda t: "  ", lambda t: "third")
    out = composed("q")
    assert out.index("first") < out.index("third")
    assert "None" not in out


def test_compose_passes_the_text_to_every_callback(mod):
    seen = []
    composed = mod.tools.compose_tool_callbacks(lambda t: seen.append(("a", t)), lambda t: seen.append(("b", t)))
    composed("hello")
    assert sorted(seen) == [("a", "hello"), ("b", "hello")]


def test_compose_keeps_retrieval_when_a_callback_raises(mod):
    def boom(text):
        raise RuntimeError("hub exploded")

    composed = mod.tools.compose_tool_callbacks(lambda t: FACT, boom)
    assert composed("q") == FACT
    assert mod.tools.compose_tool_callbacks(boom, lambda t: FACT)("q") == FACT


def test_compose_returns_none_when_everything_is_empty_or_missing(mod):
    assert mod.tools.compose_tool_callbacks()("q") is None
    assert mod.tools.compose_tool_callbacks(None, lambda t: None, lambda t: "")("q") is None


def test_compose_single_callback_and_none_entries(mod):
    assert mod.tools.compose_tool_callbacks(None, lambda t: FACT)("q") == FACT


def test_compose_does_not_wait_for_a_slow_callback_longer_than_it_takes(mod):
    def slow(text):
        time.sleep(0.4)
        return "slow"

    def also_slow(text):
        time.sleep(0.4)
        return "also"

    started = time.monotonic()
    out = mod.tools.compose_tool_callbacks(slow, also_slow)("q")
    assert time.monotonic() - started < 0.75, "callbacks should run side by side"
    assert "slow" in out and "also" in out


# Secrets and /health


def test_key_never_appears_in_output_or_logs(mod, install, mock_hub_reset, capsys, caplog):
    with caplog.at_level(logging.DEBUG):
        hub = install(mock_hub_reset.url, key=WRONG_KEY)
        mod.tools.hub_context_for("my secret question")
        install(closed_port_url(), key=KEY)
        mod.tools.hub_context_for("another secret question")
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    assert WRONG_KEY not in text and KEY not in text
    assert "my secret question" not in text and "another secret question" not in text, "user text must not be logged"


def test_status_has_the_contract_keys_and_no_secret(mod, install):
    hub = install(closed_port_url(), key=KEY)
    mod.tools.hub_context_for("trip the breaker")
    info = hub.status()
    assert {"configured", "enabled", "transport", "reachable", "breaker_open", "tools", "last_error"} <= set(info)
    assert KEY not in str(info)
    assert len(info["last_error"]) <= 200
    assert KEY not in str(mod.tools.status())


def test_health_reports_mcp_hub_without_the_key(server_module, mod, install, monkeypatch):
    install(closed_port_url(), key=KEY)
    monkeypatch.setattr(config, "MCP_HUB_API_KEY", KEY)
    client = make_client(server_module.app)
    try:
        response = client.get("/health")
    finally:
        client.close()
    assert response.status_code == 200
    body = response.json()
    if "mcp_hub" not in body:
        pytest.skip("/health does not report mcp_hub yet (the lead wires server.py)")
    assert KEY not in response.text
    assert isinstance(body["mcp_hub"], dict)
    assert "configured" in body["mcp_hub"]
    assert "mcp" in body, "the existing mcp key must stay"


def test_health_still_ok_when_the_hub_is_down(server_module, mod, install):
    install(closed_port_url(), key=KEY)
    mod.tools.hub_context_for("trip it")
    client = make_client(server_module.app)
    try:
        response = client.get("/health")
    finally:
        client.close()
    assert response.status_code == 200 and response.json()["status"] == "ok"
