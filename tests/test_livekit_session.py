"""LiveKitVoiceSession against a fake room: frames in produce transcripts and
a spoken reply out, events keep the /ws/voice JSON shapes, and barge in
clears the audio queue and sends {"type": "interrupt"}.

Fakes: the room and its data channel, microphone frames, the audio source,
the speech detector, Whisper, the Orchestrator and voicekit. Real: the
session code and livekit.rtc.AudioFrame. Audio quality and network
behaviour need a LiveKit server and are not covered here.
"""

import asyncio
import json
import time
import types

import numpy as np
import pytest

from conftest import require_module

SR = 16000


@pytest.fixture(scope="module")
def lk():
    pytest.importorskip("livekit.rtc", reason="livekit is not installed")
    require_module("livekit_transport", "backend/livekit_transport not delivered yet")
    return require_module("livekit_transport.session", "livekit_transport.session not delivered yet")


# Fakes


class FakeLocalParticipant:
    def __init__(self):
        self.events = []

    async def publish_data(self, payload, *, reliable=True, destination_identities=(), topic=""):
        assert topic == "va-events", topic
        assert reliable is True
        self.events.append(json.loads(payload))


class FakeRoom:
    def __init__(self):
        self.handlers = {}
        self.local_participant = FakeLocalParticipant()
        self.remote_participants = {}
        self.name = "va-test"

    def on(self, event, callback=None):
        self.handlers.setdefault(event, []).append(callback)

    def off(self, event, callback):
        if callback in self.handlers.get(event, []):
            self.handlers[event].remove(callback)

    def emit(self, event, *args):
        for callback in list(self.handlers.get(event, [])):
            callback(*args)


class FakeAudioSource:
    def __init__(self):
        self.frames = 0
        self.rates = set()
        self.clears = 0
        self.closed = False
        self.samples = 0

    async def capture_frame(self, frame):
        self.frames += 1
        self.samples += frame.samples_per_channel
        self.rates.add(frame.sample_rate)
        await asyncio.sleep(frame.duration * 0.5)

    def clear_queue(self):
        self.clears += 1

    async def wait_for_playout(self):
        await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


class FakeMic:
    def __init__(self):
        self._queue = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def end(self):
        self._queue.put_nowait(None)

    async def feed(self, samples, rate=SR, channels=1):
        pcm = np.clip(samples * 32768.0, -32768, 32767).astype(np.int16)
        step = rate // 100
        for start in range(0, pcm.size - step + 1, step):
            piece = pcm[start : start + step]
            if channels > 1:
                piece = np.repeat(piece, channels)
            frame = types.SimpleNamespace(
                data=piece.tobytes(), sample_rate=rate, num_channels=channels, samples_per_channel=step
            )
            self._queue.put_nowait(types.SimpleNamespace(frame=frame))
            if (start // step) % 10 == 9:
                await asyncio.sleep(0.004)


def speech(seconds, rate=SR):
    t = np.arange(int(seconds * rate)) / rate
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(seconds, rate=SR):
    return np.zeros(int(seconds * rate), dtype=np.float32)


class FakeVad:
    def __init__(self):
        self.calibrated = 0

    def calibrate_noise_floor(self, audio):
        self.calibrated += 1

    def is_speech(self, block):
        return float(np.sqrt(np.mean(np.square(block)))) > 0.05


def fake_transcribe(script):
    """Reads chunks until one with speech, then a quiet one, then yields the
    next scripted transcript as the final result."""

    def transcribe(chunks, language_hint=None, vad=None):
        heard = False
        for chunk in chunks:
            loud = float(np.sqrt(np.mean(np.square(chunk)))) > 0.05
            if loud and not heard:
                heard = True
                yield types.SimpleNamespace(text="par", is_final=False, language=language_hint or "en")
            elif heard and not loud:
                text = script.pop(0) if script else ""
                yield types.SimpleNamespace(text=text, is_final=True, language=language_hint or "en")
                return

    return transcribe


def tone(rate, n=2400):
    return (np.sin(2 * np.pi * 300 * np.arange(n) / rate) * 8000).astype(np.int16).tobytes()


class FakeSynth:
    def __init__(self, delay=0.005, chunks_per_sentence=3):
        self.calls = []
        self.texts = []
        self.delay = delay
        self.n = chunks_per_sentence

    def __call__(self, text_or_iterable, language="en", voice="female_natural", settings=None):
        import re

        self.calls.append({"language": language, "voice": voice})
        pieces = [text_or_iterable] if isinstance(text_or_iterable, str) else text_or_iterable
        buffer = ""
        index = 0

        def sentences(text, final=False):
            nonlocal buffer
            buffer += text
            parts = re.split(r"(?<=[.!?])\s+", buffer)
            buffer = "" if final else (parts.pop() if parts else "")
            return [p for p in parts if p.strip()]

        def speak(sentence, i):
            self.texts.append(sentence)
            yield types.SimpleNamespace(text=sentence, sentence_index=i)
            rate = 22050 if i % 2 else 24000
            for _ in range(self.n):
                time.sleep(self.delay)
                yield types.SimpleNamespace(pcm=tone(rate), sample_rate=rate, sentence_index=i)

        for piece in pieces:
            for sentence in sentences(piece):
                yield from speak(sentence, index)
                index += 1
        for sentence in sentences("", final=True):
            yield from speak(sentence, index)
            index += 1
        yield types.SimpleNamespace(provider="fake", first_audio_ms=1.0, total_ms=2.0)


class FakeOrchestrator:
    last_mode = "fake agent"

    def __init__(self, reply="Hello there. Nice to meet you.", slow=False):
        self.reply = reply
        self.slow = slow
        self.questions = []
        self.yielded = 0
        self.closed = False

    def respond_stream(self, text, user_name=None, language=None, **kwargs):
        self.questions.append((text, user_name, language))
        try:
            if self.slow:
                for i in range(40):
                    time.sleep(0.1)
                    self.yielded += 1
                    yield f"This is sentence number {i} of a long reply. "
            else:
                for word in self.reply.split(" "):
                    self.yielded += 1
                    yield word + " "
        finally:
            self.closed = True


def make_bindings(lk, orchestrator, hallucination=lambda t: False):
    return lk.Bindings(
        orchestrator=orchestrator,
        greeting=lambda name, lang: (f"Hello {name or 'Guest'}, I am the assistant.", lang or "en"),
        looks_like_hallucination=hallucination,
        sounds_like_closing_signal=lambda t: "goodbye" in t.lower(),
        sounds_negative=lambda t: t.lower().strip() in ("no", "nope"),
        closing_question=lambda lang: "Anything else?",
        closing_goodbye=lambda lang: "Goodbye then.",
        extract_user_name=lambda t: "Kamal" if t.lower().startswith("my name is kamal") else None,
    )


class Harness:
    def __init__(self, lk, script, orchestrator=None, greet=False, settings=None, synth=None, **session_kwargs):
        self.lk = lk
        self.room = FakeRoom()
        self.mic = FakeMic()
        self.source = FakeAudioSource()
        self.vad = FakeVad()
        self.orchestrator = orchestrator or FakeOrchestrator()
        self.synth = synth or FakeSynth()
        self.settings = settings or lk.SessionSettings(greet=greet)
        self.session = lk.LiveKitVoiceSession(
            self.room,
            self.settings,
            make_bindings(lk, self.orchestrator),
            audio_stream_factory=lambda track: self.mic,
            audio_output_factory=lambda: self.source,
            vad_factory=lambda: self.vad,
            transcribe_fn=fake_transcribe(script),
            synth_fn=self.synth,
            chunk_seconds=0.3,
            **session_kwargs,
        )
        self.task = None

    @property
    def events(self):
        return self.room.local_participant.events

    def types(self):
        """Event names in order; a status event is named by its state."""
        return [e["state"] if e["type"] == "status" else e["type"] for e in self.events]

    def of(self, kind):
        return [e for e in self.events if e["type"] == kind]

    async def start(self, identity="user-1"):
        self.task = asyncio.ensure_future(self.session.run())
        await asyncio.sleep(0.05 if not self.settings.greet else 0.05)
        from livekit import rtc

        track = types.SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        participant = types.SimpleNamespace(identity=identity)
        self.room.emit("track_subscribed", track, None, participant)

    async def say(self, seconds=1.0):
        await self.mic.feed(speech(seconds))
        await self.mic.feed(silence(0.8))

    async def wait_for(self, predicate, timeout=10.0, what="condition"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            if self.task is not None and self.task.done() and self.task.exception():
                raise self.task.exception()
            await asyncio.sleep(0.02)
        raise AssertionError(f"timed out waiting for {what}; events so far: {self.types()}")

    async def finish(self):
        self.room.emit("participant_disconnected", types.SimpleNamespace(identity="user-1"))
        self.mic.end()
        try:
            await asyncio.wait_for(self.task, 0.5)
        except (asyncio.TimeoutError, Exception):
            self.task.cancel()


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 60))


# Frames in, transcripts and audio out


def test_utterance_produces_transcripts_reply_and_audio(lk):
    async def scenario():
        h = Harness(lk, ["how much is roaming"])
        await h.start()
        await h.mic.feed(silence(1.3))  # calibration
        await h.wait_for(lambda: "listening" in h.types(), what="listening")
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types(), what="speaking_done")
        await h.finish()
        return h

    h = run(scenario())
    kinds = h.types()
    assert kinds.index("calibrating") < kinds.index("listening") < kinds.index("final_transcript")
    assert {"calibrating", "listening", "thinking", "speaking"} <= {e["state"] for e in h.of("status")}
    final = h.of("final_transcript")[0]
    assert final == {"type": "final_transcript", "text": "how much is roaming", "language": "en"}
    assert h.of("partial_transcript"), "partials must reach the page too"
    assert h.vad.calibrated == 1
    assert h.orchestrator.questions[0][0] == "how much is roaming"
    response = h.of("agent_response")[0]
    assert response["text"].strip() == "Hello there. Nice to meet you."
    assert response["mode"] == "fake agent"
    chunks = [e["text"] for e in h.of("agent_response_chunk")]
    assert chunks and all(chunks[i + 1].startswith(chunks[i]) for i in range(len(chunks) - 1)), "chunks are cumulative"
    assert [e["text"] for e in h.of("speaking_sentence")] == ["Hello there.", "Nice to meet you."]
    assert kinds.index("final_transcript") < kinds.index("thinking") < kinds.index("speaking") < kinds.index("speaking_done")
    assert h.source.frames > 0, "synthesized audio must reach the published audio source"
    assert h.source.rates == {24000}, "everything is published at 24 kHz mono whatever the voice rate was"
    assert h.source.closed, "the audio source is released when the session ends"


def test_audio_out_duration_matches_the_synthesis(lk):
    async def scenario():
        h = Harness(lk, ["hello"], synth=FakeSynth(chunks_per_sentence=3))
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    expected_ms = 2 * 3 * 100  # two sentences, three 100 ms chunks (22.05 kHz chunks are resampled)
    got_ms = h.source.samples / 24
    assert expected_ms * 0.85 <= got_ms <= expected_ms * 1.15, got_ms


def test_stereo_48k_microphone_is_converted_to_16k_mono(lk):
    async def scenario():
        h = Harness(lk, ["hello"])
        await h.start()
        t = np.arange(int(1.3 * 48000)) / 48000
        await h.mic.feed(np.zeros(t.size, dtype=np.float32), rate=48000, channels=2)
        await h.wait_for(lambda: "listening" in h.types(), what="listening after a 48 kHz stereo calibration")
        await h.mic.feed(speech(1.0, 48000), rate=48000, channels=2)
        await h.mic.feed(silence(0.8, 48000), rate=48000, channels=2)
        await h.wait_for(lambda: "final_transcript" in h.types(), what="transcript from 48 kHz stereo input")
        await h.finish()
        return h

    h = run(scenario())
    assert h.of("final_transcript")[0]["text"] == "hello"


def test_agent_receives_the_name_and_language_from_the_token(lk):
    async def scenario():
        settings = lk.SessionSettings(language="si", voice_style="male", user_name="Nimal", verified=True, greet=False)
        h = Harness(lk, ["question"], settings=settings)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    assert h.orchestrator.questions[0] == ("question", "Nimal", "si")
    assert h.synth.calls[0] == {"language": "si", "voice": "male"}


def test_hallucinated_transcripts_are_not_sent_to_the_agent(lk):
    async def scenario():
        h = Harness(lk, ["Thanks for watching!", "real question"])
        h.session._b.looks_like_hallucination = lambda t: t.startswith("Thanks for watching")
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: len(h.of("final_transcript")) >= 1)
        await asyncio.sleep(0.4)
        assert h.orchestrator.questions == []
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    assert [q[0] for q in h.orchestrator.questions] == ["real question"]


def test_greeting_is_spoken_before_calibration(lk):
    async def scenario():
        h = Harness(lk, ["x"], greet=True)
        await h.start()
        await h.wait_for(lambda: "speaking_done" in h.types(), what="greeting spoken")
        first = list(h.events)
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.finish()
        return h, first

    h, first = run(scenario())
    greeting = next(e for e in first if e["type"] == "agent_response")
    assert greeting["mode"] == "Introduction" and "Guest" in greeting["text"]
    assert h.synth.texts[0].startswith("Hello Guest")
    names = h.types()
    assert names.index("speaking_done") < names.index("calibrating"), "the greeting plays before the microphone is calibrated"
    assert h.orchestrator.questions == [], "the introduction does not go through the agent"


def test_new_name_in_speech_is_announced_for_guests_only(lk):
    async def scenario(verified):
        settings = lk.SessionSettings(verified=verified, user_name="Nimal" if verified else "", greet=False)
        h = Harness(lk, ["my name is Kamal"], settings=settings)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    guest = run(scenario(False))
    assert guest.of("user_name") == [{"type": "user_name", "name": "Kamal"}]
    assert guest.orchestrator.questions[0][1] == "Kamal"
    verified = run(scenario(True))
    assert verified.of("user_name") == [] and verified.orchestrator.questions[0][1] == "Nimal"


# Control messages


def control(room, payload, identity="user-1", topic="va-control"):
    packet = types.SimpleNamespace(
        data=json.dumps(payload).encode(), topic=topic, participant=types.SimpleNamespace(identity=identity)
    )
    room.emit("data_received", packet)


def test_set_voice_control_changes_the_next_synthesis(lk):
    async def scenario():
        h = Harness(lk, ["one", "two"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        control(h.room, {"type": "set_voice", "voice_style": "male"})
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    assert h.synth.calls[0]["voice"] == "male"


def test_control_from_another_participant_or_topic_is_ignored(lk):
    async def scenario():
        h = Harness(lk, ["one"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        control(h.room, {"type": "set_voice", "voice_style": "male"}, identity="intruder")
        control(h.room, {"type": "set_voice", "voice_style": "male"}, topic="other-topic")
        control(h.room, {"type": "set_voice", "voice_style": "robot"})
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    assert h.synth.calls[0]["voice"] == "female_natural"


def test_mute_drops_microphone_audio(lk):
    async def scenario():
        h = Harness(lk, ["should not be heard"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        control(h.room, {"type": "mute", "muted": True})
        await h.say()
        await asyncio.sleep(0.5)
        muted_transcripts = list(h.of("final_transcript"))
        control(h.room, {"type": "mute", "muted": False})
        await h.say()
        await h.wait_for(lambda: "final_transcript" in h.types(), what="transcript after unmute")
        await h.finish()
        return h, muted_transcripts

    h, muted = run(scenario())
    assert muted == [], "nothing may be transcribed while muted"
    assert h.of("final_transcript")[0]["text"] == "should not be heard"


def test_malformed_control_packets_are_ignored(lk):
    async def scenario():
        h = Harness(lk, ["one"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        bad = types.SimpleNamespace(data=b"\xff\x00not json", topic="va-control", participant=types.SimpleNamespace(identity="user-1"))
        h.room.emit("data_received", bad)
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()

    run(scenario())


# Barge in


def test_barge_in_clears_the_queue_sends_interrupt_and_stops_the_agent(lk):
    async def scenario():
        orchestrator = FakeOrchestrator(slow=True)
        h = Harness(lk, ["tell me a long story", "stop"], orchestrator=orchestrator)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: h.source.frames > 0, what="assistant speaking")
        clears_before = h.source.clears
        yielded_before = orchestrator.yielded
        await h.mic.feed(speech(0.6))  # the user talks over the assistant
        await h.wait_for(lambda: "interrupt" in h.types(), what="interrupt event")
        interrupt_at = len(h.events)
        await asyncio.sleep(0.6)
        h.after = types.SimpleNamespace(
            clears_before=clears_before,
            clears_after=h.source.clears,
            yielded_at_interrupt=orchestrator.yielded,
            yielded_later=None,
            events_after=list(h.events[interrupt_at - 1 :]),
        )
        await asyncio.sleep(0.8)
        h.after.yielded_later = orchestrator.yielded
        await h.finish()
        return h

    h = run(scenario())
    assert {"type": "interrupt"} in h.events
    assert h.after.clears_after > h.after.clears_before, "barge in must clear the audio source queue"
    assert h.orchestrator.yielded < 40, "reply generation must stop, not run to the end"
    assert h.after.yielded_later - h.after.yielded_at_interrupt <= 2, "no more reply chunks after the interrupt"
    assert h.orchestrator.closed, "the agent generator is closed"
    assert len(h.of("speaking_done")) == 0, "an interrupted reply never reports speaking_done"


def test_barge_in_audio_is_not_lost_it_becomes_the_next_utterance(lk):
    async def scenario():
        orchestrator = FakeOrchestrator(slow=True)
        h = Harness(lk, ["tell me a long story", "interrupting question"], orchestrator=orchestrator)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: h.source.frames > 0)
        await h.mic.feed(speech(0.8))
        await h.mic.feed(silence(0.8))
        await h.wait_for(lambda: len(h.of("final_transcript")) >= 2, what="second transcript from the interrupting speech")
        await h.finish()
        return h

    h = run(scenario())
    assert [e["text"] for e in h.of("final_transcript")] == ["tell me a long story", "interrupting question"]
    kinds = h.types()
    assert kinds.index("interrupt") < len(kinds) - 1
    assert "thinking" in kinds[kinds.index("interrupt") :], "the interrupting utterance is answered"


def test_short_noise_does_not_cause_barge_in(lk):
    async def scenario():
        orchestrator = FakeOrchestrator(slow=True)
        h = Harness(lk, ["tell me a long story"], orchestrator=orchestrator)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: h.source.frames > 0)
        await h.mic.feed(speech(0.05))  # a click, shorter than the debounce
        await h.mic.feed(silence(0.5))
        await asyncio.sleep(0.3)
        interrupted = "interrupt" in h.types()
        await h.finish()
        return interrupted

    assert run(scenario()) is False


def test_muted_user_cannot_barge_in(lk):
    async def scenario():
        orchestrator = FakeOrchestrator(slow=True)
        h = Harness(lk, ["tell me a long story"], orchestrator=orchestrator)
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: h.source.frames > 0)
        control(h.room, {"type": "mute", "muted": True})
        await h.mic.feed(speech(0.8))
        await asyncio.sleep(0.3)
        interrupted = "interrupt" in h.types()
        await h.finish()
        return interrupted

    assert run(scenario()) is False


# Ending and failures


def test_user_leaving_ends_the_session(lk):
    async def scenario():
        h = Harness(lk, ["x"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        h.room.emit("participant_disconnected", types.SimpleNamespace(identity="user-1"))
        await asyncio.wait_for(h.task, 5)
        return h

    h = run(scenario())
    assert h.source.closed
    assert h.room.handlers.get("data_received") == [] or not h.room.handlers.get("data_received")


def test_another_participant_leaving_does_not_end_the_session(lk):
    async def scenario():
        h = Harness(lk, ["x"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        h.room.emit("participant_disconnected", types.SimpleNamespace(identity="someone-else"))
        await asyncio.sleep(0.3)
        alive = not h.task.done()
        await h.finish()
        return alive

    assert run(scenario()) is True


def test_only_the_first_microphone_is_used(lk):
    async def scenario():
        h = Harness(lk, ["x"])
        await h.start(identity="user-1")
        from livekit import rtc

        other = FakeMic()
        h.session._audio_stream_factory = lambda track: other
        h.room.emit(
            "track_subscribed",
            types.SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO),
            None,
            types.SimpleNamespace(identity="intruder"),
        )
        await asyncio.sleep(0.1)
        ingest_started_for_intruder = other._queue.qsize() == 0 and h.session._target_identity == "user-1"
        await h.finish()
        return ingest_started_for_intruder

    assert run(scenario()) is True


def test_no_microphone_audio_ends_with_an_error_event_and_session_error(lk):
    async def scenario():
        h = Harness(lk, ["x"], mic_wait_seconds=0.4)
        await h.start()
        with pytest.raises(lk.SessionError):
            await asyncio.wait_for(h.task, 5)
        return h

    h = run(scenario())
    assert h.of("error"), "the page must be told why the conversation ended"


def test_agent_exception_is_reported_and_the_session_survives(lk):
    class Boom(FakeOrchestrator):
        def respond_stream(self, text, user_name=None, language=None, **kwargs):
            raise RuntimeError("agent exploded")
            yield ""

    async def scenario():
        h = Harness(lk, ["first", "second"], orchestrator=Boom())
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: bool(h.of("error")), what="error event")
        alive = not h.task.done()
        await h.finish()
        return h, alive

    h, alive = run(scenario())
    assert "agent exploded" in h.of("error")[0]["message"]
    assert alive


def test_closing_phrase_asks_then_says_goodbye_and_ends(lk):
    async def scenario():
        h = Harness(lk, ["goodbye", "no"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types(), what="closing question spoken")
        await h.say()
        await h.wait_for(lambda: "end_chat" in h.types(), what="end_chat")
        await asyncio.wait_for(h.task, 5)
        return h

    h = run(scenario())
    texts = [e["text"] for e in h.of("agent_response")]
    assert texts == ["Anything else?", "Goodbye then."]
    assert h.orchestrator.questions == [], "closing lines are canned, not generated"


def test_every_event_has_the_ws_voice_shape(lk):
    async def scenario():
        h = Harness(lk, ["how much"])
        await h.start()
        await h.mic.feed(silence(1.3))
        await h.wait_for(lambda: "listening" in h.types())
        await h.say()
        await h.wait_for(lambda: "speaking_done" in h.types())
        await h.finish()
        return h

    h = run(scenario())
    required = {
        "status": {"type", "state"},
        "partial_transcript": {"type", "text", "language"},
        "final_transcript": {"type", "text", "language"},
        "agent_response_chunk": {"type", "text"},
        "agent_response": {"type", "text", "mode"},
        "speaking_sentence": {"type", "text"},
        "speaking_done": {"type"},
    }
    for event in h.events:
        assert isinstance(event["type"], str)
        if event["type"] in required:
            assert set(event) == required[event["type"]], event
        assert len(json.dumps(event)) < 15000, "reliable data packets are limited to about 15 KB"


def test_constants_match_the_contract(lk):
    assert lk.EVENTS_TOPIC == "va-events" and lk.CONTROL_TOPIC == "va-control"
    assert lk.AGENT_TRACK_NAME == "agent-voice" and lk.AGENT_SAMPLE_RATE == 24000


def test_ws_voice_server_events_use_the_same_type_names(lk, server_module):
    """The page has one message handler for both transports (ADR 2.4), so the
    type names the session sends must exist in server.py's /ws/voice code."""
    import inspect

    source = inspect.getsource(server_module)
    for kind in (
        "status",
        "partial_transcript",
        "final_transcript",
        "agent_response_chunk",
        "agent_response",
        "speaking_sentence",
        "speaking_done",
        "user_name",
        "end_chat",
        "error",
    ):
        assert f'"{kind}"' in source, f"/ws/voice does not send {kind!r}"
