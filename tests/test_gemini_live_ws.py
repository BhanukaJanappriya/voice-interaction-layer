"""The /ws/live proxy handler with a fake websocket and the fake Live server."""

import asyncio
import json

import pytest

pytest.importorskip("google.genai", reason="google-genai is not installed")

from access import GUEST  # noqa: E402
from gemini_live import fake_server as fake  # noqa: E402
from gemini_live import ws  # noqa: E402


SAMPLE1 = bytes([1, 0])
SILENCE = bytes([0, 0])


class FakeSocket:
    def __init__(self):
        self.inbox = asyncio.Queue()
        self.sent = []

    async def receive(self):
        return await self.inbox.get()

    async def send_text(self, text):
        self.sent.append(("text", json.loads(text)))

    async def send_bytes(self, data):
        self.sent.append(("bytes", data))

    def json_types(self):
        return [m["type"] for kind, m in self.sent if kind == "text"]


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def test_a_turn_reaches_the_page_in_the_voice_event_shapes():
    def script(s):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(
                    fake.msg_input_text("hello", True), fake.msg_audio(b"\x01\x00" * 4),
                    fake.msg_output_text("Hi", False), fake.msg_output_text(" there", True),
                )
            if n == 2:
                sess.feed(fake.msg_interrupted(), fake.msg_turn_complete())
        s.on_audio = on_audio

    server = fake.FakeLiveServer([script])
    socket = FakeSocket()

    async def go():
        task = asyncio.create_task(ws.handle(
            socket, GUEST, "en", api_key="k", state_changing=False, client_factory=server.factory(), setup_timeout_seconds=2,
        ))
        await asyncio.sleep(0.2)
        await socket.inbox.put({"type": "websocket.receive", "bytes": b"\x00\x00" * 160})
        await asyncio.sleep(0.3)
        await socket.inbox.put({"type": "websocket.receive", "bytes": SILENCE * 160})
        await asyncio.sleep(0.3)
        await socket.inbox.put({"type": "websocket.receive", "text": json.dumps({"type": "end_session"})})
        return await task

    result = run(go())
    assert result.ok and result.reason_class == "user_closed" and result.turns == 1
    types_seen = socket.json_types()
    assert types_seen[:2] == ["live_ready", "status"]
    for expected in ("final_transcript", "agent_response_chunk", "agent_response", "interrupt", "speaking_done"):
        assert expected in types_seen
    assert [d for k, d in socket.sent if k == "bytes"] == [b"\x01\x00" * 4]
    final = next(m for k, m in socket.sent if k == "text" and m["type"] == "agent_response")
    assert final["text"] == "Hi there" and final["mode"] == "gemini live"
    # only the knowledge tool was declared for a guest
    names = [d.name for t in (server.sessions[0].config.tools or []) for d in t.function_declarations]
    assert names == ["search_knowledgebase_tool"] or names == []


def test_a_connect_failure_returns_the_reason_for_the_router_and_tells_the_page():
    server = fake.FakeLiveServer()
    server.connect_errors = [fake.closed_error(1011, "Your prepayment credits are depleted")]
    socket = FakeSocket()
    result = run(ws.handle(socket, GUEST, "si", api_key="k", client_factory=server.factory(), setup_timeout_seconds=1))
    assert not result.ok and result.reason_class == "billing_or_quota"
    assert socket.sent == [("text", {"type": "live_failed", "reason": "billing_or_quota"})]


def test_the_page_closing_the_socket_ends_the_proxy_quietly():
    server = fake.FakeLiveServer([lambda s: None])
    socket = FakeSocket()

    async def go():
        task = asyncio.create_task(ws.handle(socket, GUEST, "ta", api_key="k", client_factory=server.factory()))
        await asyncio.sleep(0.2)
        await socket.inbox.put({"type": "websocket.disconnect"})
        return await task

    result = run(go())
    assert result.ok and result.reason_class == "user_closed"
