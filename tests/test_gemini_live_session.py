"""GeminiLiveSession against the scripted fake Live server (no network, no key).

Covers: setup config, event order, transcripts (cumulative), barge in (queued
audio dropped), tool call round trip, resumption on GoAway with a 1 s audio
buffer, a dropped connection, failures and what is NOT a failure.
"""

import asyncio

import pytest

pytest.importorskip("google.genai", reason="google-genai is not installed")

from google.genai import types  # noqa: E402

from gemini_live import errors  # noqa: E402
from gemini_live import fake_server as fake  # noqa: E402
from gemini_live.config import LiveSessionConfig  # noqa: E402
from gemini_live.session import (  # noqa: E402
    AudioOut, Failed, GeminiLiveSession, GoAway, InputText, Interrupted, OutputText, Resumed, ToolActivity, TurnComplete,
    parse_seconds,
)

PCM = b"\x01\x00" * 480


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


def make_cfg(**kw):
    base = dict(api_key="test-key-not-real", model="gemini-test-live", language="si", voice_name="Kore",
                system_instruction="Reply in Sinhala.", setup_timeout_seconds=2.0)
    base.update(kw)
    return LiveSessionConfig(**base)


async def collect(session, until, limit=60):
    """Events up to and including the first one `until(event)` accepts."""
    got = []
    async for event in session.events:
        got.append(event)
        if until(event) or len(got) >= limit:
            break
    return got


def kinds(events):
    return [type(e).__name__ for e in events]


async def started(server, tool_executor=None, **cfg):
    session = GeminiLiveSession(tool_executor, client_factory=server.factory())
    assert await session.open(make_cfg(**cfg)) is True
    return session


def test_setup_config_carries_the_documented_options():
    server = fake.FakeLiveServer([lambda s: None])

    async def go():
        tools = [types.Tool(function_declarations=[types.FunctionDeclaration(name="search_knowledgebase_tool", description="x")])]
        session = await started(server, tools=tools, resumption_handle="old-handle")
        config = server.sessions[0].config
        await session.close()
        return config

    config = run(go())
    assert server.sessions[0].model == "gemini-test-live"
    assert config.response_modalities == ["AUDIO"]
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert config.input_audio_transcription is not None and config.output_audio_transcription is not None
    assert config.context_window_compression.sliding_window is not None
    assert config.session_resumption.handle == "old-handle"
    assert config.system_instruction == "Reply in Sinhala."
    assert [d.name for d in config.tools[0].function_declarations] == ["search_knowledgebase_tool"]
    # no language code anywhere: native audio takes none
    assert not getattr(config.speech_config, "language_code", None)
    assert config.realtime_input_config.automatic_activity_detection.disabled is False


def test_audio_goes_out_as_16k_pcm_and_a_turn_comes_back_in_order():
    def script(s):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(
                    fake.msg_input_text("hel", False), fake.msg_input_text("lo", True),
                    fake.msg_audio(b"\x02\x00" * 10), fake.msg_output_text("Hi ", False), fake.msg_output_text("there", True),
                    fake.msg_audio(b"\x03\x00" * 10), fake.msg_turn_complete(),
                )
        s.on_audio = on_audio

    server = fake.FakeLiveServer([script])

    async def go():
        session = await started(server)
        await session.send_audio(PCM)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    events = run(go())
    sent = server.sessions[0].audio_in
    assert sent == [PCM]
    assert kinds(events) == ["InputText", "InputText", "AudioOut", "OutputText", "OutputText", "AudioOut", "TurnComplete"]
    # transcripts are cumulative, like partial_transcript
    assert [(e.text, e.final) for e in events if isinstance(e, InputText)] == [("hel", False), ("hello", True)]
    assert [(e.text, e.final) for e in events if isinstance(e, OutputText)] == [("Hi ", False), ("Hi there", True)]
    assert [len(e.pcm24k) for e in events if isinstance(e, AudioOut)] == [20, 20]


def test_unfinished_input_text_is_finalised_when_the_model_starts_to_answer():
    server = fake.FakeLiveServer([lambda s: s.feed(fake.msg_input_text("what is", False), fake.msg_audio(b"\x00\x00"), fake.msg_turn_complete())])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    events = run(go())
    assert kinds(events) == ["InputText", "InputText", "AudioOut", "TurnComplete"]
    assert (events[1].text, events[1].final) == ("what is", True)


def test_barge_in_drops_queued_audio_and_reports_interrupted():
    # The consumer is slow: three audio chunks are queued when the interruption arrives.
    server = fake.FakeLiveServer([lambda s: s.feed(
        fake.msg_audio(b"\x01\x00"), fake.msg_audio(b"\x02\x00"), fake.msg_audio(b"\x03\x00"),
        fake.msg_interrupted(), fake.msg_audio(b"\x09\x00"), fake.msg_turn_complete(),
    )])

    async def go():
        session = await started(server)
        await asyncio.sleep(0.2)  # let the receive loop queue everything before the consumer reads
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    events = run(go())
    assert kinds(events) == ["Interrupted", "AudioOut", "TurnComplete"]
    assert events[1].pcm24k == b"\x09\x00"


def test_a_tool_call_runs_the_executor_and_answers_on_the_connection():
    calls = []

    async def executor(name, args):
        calls.append((name, args))
        return {"result": "Fibre 60 costs five thousand"}

    def script(s):
        s.feed(fake.msg_tool_call(("c1", "search_knowledgebase_tool", {"query": "fibre"})))

        def on_response(sess, items):
            sess.feed(fake.msg_output_text("It costs five thousand.", True), fake.msg_turn_complete())
        s.on_tool_response = on_response

    server = fake.FakeLiveServer([script])

    async def go():
        session = await started(server, tool_executor=executor)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    events = run(go())
    assert kinds(events) == ["ToolActivity", "OutputText", "TurnComplete"]
    assert events[0].name == "search_knowledgebase_tool"
    assert calls == [("search_knowledgebase_tool", {"query": "fibre"})]
    response = server.sessions[0].tool_responses[0][0]
    assert response.id == "c1" and response.name == "search_knowledgebase_tool"
    assert response.response == {"result": "Fibre 60 costs five thousand"}


def test_a_raising_executor_or_no_executor_still_answers_the_model():
    async def broken(name, args):
        raise RuntimeError("boom")

    for executor, expected in ((broken, {"error": "tool_failed"}), (None, {"error": "no_tools"})):
        def script(s):
            s.feed(fake.msg_tool_call(("c1", "x", {})))
            s.on_tool_response = lambda sess, items: sess.feed(fake.msg_turn_complete())

        server = fake.FakeLiveServer([script])

        async def go():
            session = await started(server, tool_executor=executor)
            await collect(session, lambda e: isinstance(e, TurnComplete))
            await session.close()

        run(go())
        assert server.sessions[0].tool_responses[0][0].response == expected


def test_go_away_reconnects_with_the_handle_at_the_gap_and_keeps_at_most_one_second_of_audio():
    first = lambda s: s.feed(  # noqa: E731
        fake.msg_resumption("h-1"), fake.msg_audio(b"\x01\x00"), fake.msg_go_away(30), fake.msg_audio(b"\x02\x00"),
        fake.msg_turn_complete(),
    )
    second = lambda s: s.feed(fake.msg_output_text("back again", True), fake.msg_turn_complete())  # noqa: E731
    server = fake.FakeLiveServer([first, second])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, OutputText) and e.final)
        handle = session.resumption_handle
        reconnects = session.reconnects
        await session.close()
        return events, handle, reconnects

    events, handle, reconnects = run(go())
    assert kinds(events) == ["AudioOut", "GoAway", "AudioOut", "TurnComplete", "Resumed", "OutputText"]
    assert next(e for e in events if isinstance(e, GoAway)).seconds == 30.0
    assert len(server.sessions) == 2 and reconnects == 1 and handle == "h-1"
    assert server.sessions[1].config.session_resumption.handle == "h-1"
    assert events[kinds(events).index("Resumed")].handle == "h-1"


def test_go_away_between_turns_reconnects_at_once():
    first = lambda s: s.feed(fake.msg_resumption("h-2"), fake.msg_go_away(20))  # noqa: E731
    second = lambda s: s.feed(fake.msg_turn_complete())  # noqa: E731
    server = fake.FakeLiveServer([first, second])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    assert kinds(run(go())) == ["GoAway", "Resumed", "TurnComplete"]
    assert len(server.sessions) == 2


def test_audio_sent_while_reconnecting_is_buffered_to_one_second_and_flushed():
    server = fake.FakeLiveServer([lambda s: s.feed(fake.msg_resumption("h-3")), lambda s: None])

    async def go():
        session = await started(server)
        await asyncio.sleep(0.05)
        server.connect_delay = 0.3  # the second connect is slow, so the gap can be observed
        server.sessions[0].close_remote(1001, "going away")
        await asyncio.sleep(0.1)
        assert not session.connected
        chunk = b"\x07\x00" * 16000  # 1 s of audio per chunk
        for _ in range(3):
            await session.send_audio(chunk)
        await asyncio.sleep(0.6)
        assert session.connected
        await session.close()

    run(go())
    assert len(server.sessions) == 2
    flushed = b"".join(server.sessions[1].audio_in)
    assert len(flushed) == 32000  # three seconds were sent into the gap, only the newest second is kept
    assert flushed == b"\x07\x00" * 16000


def test_a_dropped_connection_without_a_handle_is_a_failure_outage():
    server = fake.FakeLiveServer([lambda s: s.close_remote(1011, "internal error")])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, Failed))
        await session.close()
        return events, session.closed_reason

    events, reason = run(go())
    assert [(type(e).__name__, e.reason_class) for e in events] == [("Failed", "outage")]
    assert reason == "outage"


def test_a_dropped_connection_with_a_handle_resumes():
    server = fake.FakeLiveServer([
        lambda s: (s.feed(fake.msg_resumption("h-4")), s.close_remote(1006, "abnormal")),
        lambda s: s.feed(fake.msg_turn_complete()),
    ])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    assert kinds(run(go())) == ["Resumed", "TurnComplete"]


def test_billing_close_text_is_a_billing_failure_with_no_audio():
    server = fake.FakeLiveServer([lambda s: s.close_remote(1011, "Your prepayment credits are depleted")])

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, Failed))
        await session.close()
        return events

    events = run(go())
    assert events[-1].reason_class == errors.BILLING_OR_QUOTA
    assert not any(isinstance(e, AudioOut) for e in events)


def test_connect_errors_are_classified_and_open_returns_false():
    for exc, expected in (
        (fake.closed_error(1011, "Your prepayment credits are depleted"), "billing_or_quota"),
        (PermissionError("API key not valid"), "invalid_key"),
        (asyncio.TimeoutError(), "timeout"),
    ):
        server = fake.FakeLiveServer()
        server.connect_errors = [exc]

        async def go():
            session = GeminiLiveSession(None, client_factory=server.factory())
            ok = await session.open(make_cfg())
            events = await collect(session, lambda e: isinstance(e, Failed))
            return ok, events

        ok, events = run(go())
        assert ok is False and events[-1].reason_class == expected, expected


def test_setup_timeout_is_a_timeout_failure():
    server = fake.FakeLiveServer()
    server.connect_delay = 5.0

    async def go():
        session = GeminiLiveSession(None, client_factory=server.factory())
        ok = await session.open(make_cfg(setup_timeout_seconds=0.1))
        events = await collect(session, lambda e: isinstance(e, Failed))
        return ok, events

    ok, events = run(go())
    assert ok is False and events[-1].reason_class == "timeout"


def test_a_missing_key_fails_before_any_connection():
    async def go():
        session = GeminiLiveSession(None)
        ok = await session.open(make_cfg(api_key=""))
        events = await collect(session, lambda e: isinstance(e, Failed))
        return ok, events

    ok, events = run(go())
    assert ok is False and events[-1].reason_class == "missing_key"


def test_repeated_reconnect_failures_end_as_repeated_failure():
    server = fake.FakeLiveServer([lambda s: (s.feed(fake.msg_resumption("h-5")), s.close_remote(1006, "x"))])
    server.connect_errors = [None, fake.closed_error(1013, "try later"), fake.closed_error(1013, "try later"),
                             fake.closed_error(1013, "try later"), fake.closed_error(1013, "try later")]

    async def go():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, Failed))
        await session.close()
        return events

    assert run(go())[-1].reason_class == "repeated_failure"


def test_safety_block_refusal_and_normal_close_are_not_failures():
    # a safety rejected turn is a normal TurnComplete
    server = fake.FakeLiveServer([lambda s: s.feed(fake.msg_turn_complete("GENERATED_AUDIO_SAFETY"))])

    async def blocked():
        session = await started(server)
        events = await collect(session, lambda e: isinstance(e, TurnComplete))
        await session.close()
        return events

    events = run(blocked())
    assert kinds(events) == ["TurnComplete"]

    # a server side close with a safety text ends the stream without Failed
    server = fake.FakeLiveServer([lambda s: s.close_remote(1008, "blocked by safety policy")])

    async def closed_by_safety():
        session = await started(server)
        events = [e async for e in session.events]
        return events, session.closed_reason

    events, reason = run(closed_by_safety())
    assert events == [] and reason == "safety_block"

    # a normal close (1000) is the end of the conversation
    server = fake.FakeLiveServer([lambda s: s.close_remote(1000, "")])

    async def normal():
        session = await started(server)
        events = [e async for e in session.events]
        return events, session.closed_reason

    events, reason = run(normal())
    assert events == [] and reason == "user_closed"


def test_close_ends_the_stream_and_is_user_closed():
    server = fake.FakeLiveServer([lambda s: None])

    async def go():
        session = await started(server)
        await session.close()
        events = [e async for e in session.events]
        await session.send_audio(PCM)  # after close: ignored, no error
        return events, session.closed_reason

    events, reason = run(go())
    assert events == [] and reason == "user_closed"


def test_parse_seconds():
    assert parse_seconds("57s") == 57.0 and parse_seconds("0.5s") == 0.5 and parse_seconds(None) == 0.0
    assert parse_seconds("garbage") == 0.0
