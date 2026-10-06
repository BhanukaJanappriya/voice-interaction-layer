"""voicekit sinks and the adapter, driven by scripted SynthEvents (no audio
hardware, no provider, no network)."""

import pytest

from conftest import require_module
from helpers.voicekit_fakes import install_fake_synth, pcm, scripted_events


@pytest.fixture(scope="module")
def vk_types():
    return require_module("voicekit.types", "backend/voicekit not delivered yet")


@pytest.fixture(scope="module")
def sinks(vk_types):
    return require_module("voicekit.sinks", "voicekit.sinks not delivered yet")


@pytest.fixture(scope="module")
def adapter(vk_types):
    return require_module("voicekit.adapter", "voicekit.adapter not delivered yet")


def test_event_dataclasses_have_the_contract_fields(vk_types):
    chunk = vk_types.AudioChunk(pcm=b"\x00\x00", sample_rate=24000, sentence_index=2)
    assert (chunk.pcm, chunk.sample_rate, chunk.sentence_index) == (b"\x00\x00", 24000, 2)
    word = vk_types.WordTiming(text="hi", start_ms=1.0, duration_ms=2.0, sentence_index=0)
    assert (word.text, word.start_ms, word.duration_ms) == ("hi", 1.0, 2.0)
    start = vk_types.SentenceStart(text="Hi.", sentence_index=0)
    assert start.text == "Hi."
    done = vk_types.SynthesisDone(provider="edge", first_audio_ms=5.0, total_ms=9.0)
    assert done.provider == "edge"


def test_error_hierarchy(vk_types):
    assert issubclass(vk_types.ProviderUnavailable, vk_types.VoicekitError)
    assert issubclass(vk_types.InvalidRequest, vk_types.VoicekitError)
    assert issubclass(vk_types.VoicekitError, Exception)


def test_callback_sink_receives_every_event_in_order(sinks):
    seen = []
    events = scripted_events()
    sinks.CallbackSink(seen.append).play(iter(events))
    assert seen == events


def test_callback_sink_cancel_stops_delivery(sinks):
    seen = []
    holder = {}

    def on_event(event):
        seen.append(event)
        holder["sink"].cancel()

    holder["sink"] = sinks.CallbackSink(on_event)
    holder["sink"].play(iter(scripted_events()))
    assert len(seen) == 1, "after cancel() no further events may be delivered"


def test_sinks_are_sink_subclasses(sinks):
    for name in ("SpeakerSink", "CallbackSink", "HttpChunkSink", "WebSocketSink", "LiveKitSink"):
        cls = getattr(sinks, name, None)
        assert cls is not None, f"voicekit.sinks.{name} missing"
        assert issubclass(cls, sinks.Sink), name


def test_websocket_sink_sends_binary_audio_and_json_markers(sinks):
    frames, messages = [], []
    sink = sinks.WebSocketSink(frames.append, messages.append)
    sink.play(iter(scripted_events()))
    assert frames and all(isinstance(f, (bytes, bytearray)) for f in frames)
    assert b"".join(frames) == pcm()
    kinds = [m.get("type") for m in messages]
    assert "audio_start" in kinds and "done" in kinds
    start = next(m for m in messages if m["type"] == "audio_start")
    assert start["sample_rate"] == 24000
    words = [w for m in messages if m["type"] == "timings" for w in m["words"]]
    assert [w["text"] for w in words] == ["Hello", "there."]
    assert {"text", "start_ms", "duration_ms"} <= set(words[0])
    assert kinds.index("audio_start") < kinds.index("done")


def test_websocket_sink_cancel_stops_audio(sinks):
    from voicekit import types as t

    frames = []
    holder = {}

    def send_bytes(data):
        frames.append(data)
        holder["sink"].cancel()

    many = [t.AudioChunk(pcm=pcm(), sample_rate=24000, sentence_index=0) for _ in range(5)]
    holder["sink"] = sinks.WebSocketSink(send_bytes, lambda m: None)
    holder["sink"].play(iter(many))
    assert len(frames) == 1


class _FakeAudioSource:
    def __init__(self):
        self.frames = []
        self.cleared = 0

    def capture_frame(self, frame):
        self.frames.append(frame)

    def clear_queue(self):
        self.cleared += 1


def test_livekit_sink_pushes_frames_and_cancel_clears_the_queue(sinks):
    source = _FakeAudioSource()
    sink = sinks.LiveKitSink(source)
    try:
        sink.play(iter(scripted_events()))
    except (TypeError, ImportError, ModuleNotFoundError) as exc:  # frames need livekit.rtc
        pytest.skip(f"LiveKitSink needs livekit.rtc to build frames: {exc}")
    assert source.frames, "audio should reach capture_frame"
    sink.cancel()
    assert source.cleared >= 1, "cancel() must call clear_queue so queued speech stops at once"


def test_http_chunk_sink_yields_raw_bytes(sinks):
    sink = sinks.HttpChunkSink()
    gen = next((getattr(sink, a) for a in ("iter_bytes", "stream", "generate", "chunks") if hasattr(sink, a)), None)
    if gen is None:
        pytest.skip("HttpChunkSink generator method name is not in the contract")
    out = list(gen(iter(scripted_events())))
    assert out and all(isinstance(b, (bytes, bytearray)) for b in out)


# Adapter


class EchoLike:
    """A ChatAgent with respond() only, like agent.EchoAgent."""

    def respond(self, text):
        return "Echo: " + text


class StreamingAgent:
    def respond_stream(self, text, user_name=None, language=None):
        yield "Hello "
        yield "there. "
        yield "Bye."


def test_adapter_wraps_a_respond_only_agent(adapter, sinks, monkeypatch):
    calls = install_fake_synth(monkeypatch)
    seen = []
    speak = adapter.speak_agent(EchoLike(), sink=sinks.CallbackSink(seen.append), language="en", voice="male")
    assert speak("ping") == "Echo: ping"
    assert seen, "the reply must be spoken into the sink"
    assert calls and calls[0]["voice"] == "male" and calls[0]["language"] == "en"


def test_adapter_works_with_the_real_echo_agent(adapter, sinks, monkeypatch):
    import agent

    install_fake_synth(monkeypatch)
    speak = adapter.speak_agent(agent.EchoAgent(), sink=sinks.CallbackSink(lambda e: None))
    assert isinstance(speak("hello"), str)


def test_adapter_wraps_a_plain_callable_returning_str(adapter, sinks, monkeypatch):
    install_fake_synth(monkeypatch)
    seen = []
    speak = adapter.speak_agent(lambda text: "Plain " + text, sink=sinks.CallbackSink(seen.append))
    assert speak("x") == "Plain x"
    assert seen


def test_adapter_wraps_a_callable_returning_an_iterable(adapter, sinks, monkeypatch):
    install_fake_synth(monkeypatch)
    seen = []
    speak = adapter.speak_agent(lambda text: iter(["One. ", "Two."]), sink=sinks.CallbackSink(seen.append))
    assert speak("x") == "One. Two."
    assert seen


def test_adapter_uses_respond_stream_when_available(adapter, sinks, monkeypatch):
    install_fake_synth(monkeypatch)
    speak = adapter.speak_agent(StreamingAgent(), sink=sinks.CallbackSink(lambda e: None))
    assert speak("x") == "Hello there. Bye."
