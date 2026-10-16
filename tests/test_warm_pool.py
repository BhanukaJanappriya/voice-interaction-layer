"""The warm shared hub connections: reuse, replacement of a dead or idle session, fallback to the
short lived connection, and that only public read only tools use them. Fake sessions, no network."""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config  # noqa: E402
from mcp_client.errors import HubToolError, HubUnavailable  # noqa: E402
from mcp_gateway import gateway, warm  # noqa: E402
from mcp_gateway.session import CallOutput, SessionDead  # noqa: E402


class FakeSession:
    instances = []

    def __init__(self):
        self.alive = False
        self.calls = []
        self.closed = False
        self.fail_open = None
        self.fail_call = None
        FakeSession.instances.append(self)

    def open(self, timeout=None):
        if FakeSession.open_error is not None:
            raise FakeSession.open_error
        self.alive = True

    def call(self, name, arguments, timeout=None):
        if self.fail_call is not None:
            self.alive = False
            raise self.fail_call
        self.calls.append((name, arguments))
        return CallOutput(text='{"chunks": []}')

    def close(self):
        self.closed = True
        self.alive = False


FakeSession.open_error = None


@pytest.fixture(autouse=True)
def fresh():
    FakeSession.instances = []
    FakeSession.open_error = None
    yield
    warm.set_pool(None)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def pool(**kw):
    return warm.WarmPool(size=kw.pop("size", 2), idle_seconds=kw.pop("idle_seconds", 300), factory=FakeSession, clock=kw.pop("clock", Clock()))


def test_the_second_call_reuses_the_open_connection():
    p = pool()
    p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    p.call("search_knowledgebase_tool", {"query": "b"}, 5)
    assert len(FakeSession.instances) == 1
    assert [c[1]["query"] for c in FakeSession.instances[0].calls] == ["a", "b"]
    assert p.status()["open"] == 1 and p.status()["opened"] == 1


def test_two_calls_at_once_use_two_connections():
    p = pool(size=2)
    inside, release = threading.Event(), threading.Event()
    original = FakeSession.call

    def slow(self, name, arguments, timeout=None):
        inside.set()
        release.wait(2)
        return original(self, name, arguments, timeout)

    FakeSession.call = slow
    try:
        t = threading.Thread(target=lambda: p.call("search_knowledgebase_tool", {"query": "a"}, 5))
        t.start()
        inside.wait(2)
        FakeSession.call = original
        p.call("search_knowledgebase_tool", {"query": "b"}, 5)
        release.set()
        t.join(2)
    finally:
        FakeSession.call = original
    assert len(FakeSession.instances) == 2


def test_a_dead_connection_is_dropped_and_the_caller_told_to_fall_back():
    p = pool()
    p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    FakeSession.instances[0].fail_call = SessionDead("gone")
    with pytest.raises(warm.WarmUnavailable):
        p.call("search_knowledgebase_tool", {"query": "b"}, 5)
    assert FakeSession.instances[0].closed and p.status()["open"] == 0 and p.status()["dropped"] == 1
    p.call("search_knowledgebase_tool", {"query": "c"}, 5)  # a fresh one opens next time
    assert len(FakeSession.instances) == 2


def test_a_timeout_drops_the_connection_too():
    p = pool()
    p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    FakeSession.instances[0].fail_call = TimeoutError()
    with pytest.raises(warm.WarmUnavailable):
        p.call("search_knowledgebase_tool", {"query": "b"}, 5)
    assert FakeSession.instances[0].closed


def test_a_tool_error_keeps_the_connection():
    p = pool()
    p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    FakeSession.instances[0].fail_call = HubToolError("bad arguments")
    FakeSession.instances[0].alive = True
    with pytest.raises(HubToolError):
        p.call("search_knowledgebase_tool", {"query": "b"}, 5)
    assert not FakeSession.instances[0].closed


def test_an_idle_connection_is_replaced():
    clock = Clock()
    p = pool(clock=clock, idle_seconds=300)
    p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    clock.now += 301
    p.call("search_knowledgebase_tool", {"query": "b"}, 5)
    assert len(FakeSession.instances) == 2 and FakeSession.instances[0].closed


def test_a_hub_that_cannot_be_opened_means_fallback():
    FakeSession.open_error = HubUnavailable("down", reason="timeout")
    p = pool()
    with pytest.raises(warm.WarmUnavailable):
        p.call("search_knowledgebase_tool", {"query": "a"}, 5)
    assert p.status()["open"] == 0


def test_prewarm_opens_sessions_and_never_raises():
    p = pool(size=3)
    assert p.prewarm(count=2) == 2 and p.status()["open"] == 2
    FakeSession.open_error = HubUnavailable("down", reason="timeout")
    q = pool(size=3)
    assert q.prewarm(count=2) == 0


def test_the_gateway_uses_the_warm_session_for_a_public_tool_and_not_for_a_changing_one(monkeypatch):
    p = pool()
    warm.set_pool(p)
    monkeypatch.setattr(config, "HUB_WARM_SHARED", True)
    slow_path = []
    monkeypatch.setattr(gateway, "call_tool_sync", lambda *a, **k: slow_path.append(a) or CallOutput(text="slow"))
    result = gateway._shared("search_knowledgebase_tool", {"query": "x"}, 8.0, False, 0.0, None)
    assert result.ok and not slow_path and len(FakeSession.instances) == 1
    gateway._shared("purchase_extra_gb", {"a": 1}, 8.0, True, 0.0, None)
    assert slow_path, "a state changing call never uses the shared warm session"
    assert FakeSession.instances[0].calls[-1][0] == "search_knowledgebase_tool"


def test_the_gateway_falls_back_to_the_short_lived_connection(monkeypatch):
    p = pool()
    warm.set_pool(p)
    monkeypatch.setattr(config, "HUB_WARM_SHARED", True)
    FakeSession.open_error = HubUnavailable("down", reason="timeout")
    monkeypatch.setattr(gateway, "call_tool_sync", lambda *a, **k: CallOutput(text="from the short lived connection"))
    result = gateway._shared("search_knowledgebase_tool", {"query": "x"}, 8.0, False, 0.0, None)
    assert result.ok and "short lived" in result.text


def test_the_switch_turns_it_off(monkeypatch):
    p = pool()
    warm.set_pool(p)
    monkeypatch.setattr(config, "HUB_WARM_SHARED", False)
    monkeypatch.setattr(gateway, "call_tool_sync", lambda *a, **k: CallOutput(text="slow"))
    gateway._shared("search_knowledgebase_tool", {"query": "x"}, 8.0, False, 0.0, None)
    assert FakeSession.instances == []
