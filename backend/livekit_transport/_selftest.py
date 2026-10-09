"""Standalone checks for the LiveKit transport, run without a LiveKit server.

    python -m backend.livekit_transport.session --selftest
    python -m backend.livekit_transport._selftest [--worker]

What is real and what is a fake, so the output is not over read:
  real   the session code, vad.VoiceActivityDetector (calibration), the real
         stt.transcribe_stream chunking and finalisation logic, the real
         livekit.rtc AudioFrame and AudioResampler (native library), the
         LiveKit access token signing and the FastAPI router.
  fake   the room and its data channel, the microphone frames, the audio
         source, the speech detector's decision (an amplitude rule, since
         synthetic tones are not speech to WebRTC VAD), Whisper (stt._emit
         returns scripted text), the Orchestrator and voicekit (scripted).
So it proves the control flow, event shapes, bridging, barge in and token
contents. It does not prove audio quality or network behaviour, which need a
LiveKit server and a browser ("not live tested").
"""

import asyncio
import io
import json
import sys
import time
import types
from contextlib import redirect_stdout

import numpy as np

failures: list = []


def check(label: str, condition: bool, detail: str = ""):
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' (' + detail + ')') if detail and not condition else ''}")
    if not condition:
        failures.append(label)


# ----------------------------------------------------------------- fakes


class FakeLocalParticipant:
    def __init__(self):
        self.events: list = []  # (time, payload dict)

    async def publish_data(self, payload, *, reliable=True, destination_identities=(), topic=""):
        assert topic == "va-events", topic
        assert reliable is True
        self.events.append((time.time(), json.loads(payload)))


class FakeRoom:
    def __init__(self):
        self.handlers: dict = {}
        self.local_participant = FakeLocalParticipant()
        self.remote_participants: dict = {}
        self.name = "va-selftest"

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
        self.samples = 0
        self.rates: set = set()
        self.clears = 0
        self.closed = False

    async def capture_frame(self, frame):
        self.frames += 1
        self.samples += frame.samples_per_channel
        self.rates.add(frame.sample_rate)
        await asyncio.sleep(frame.duration * 0.5)  # a source plays slower than the synthesizer produces

    def clear_queue(self):
        self.clears += 1

    async def wait_for_playout(self):
        await asyncio.sleep(0)

    async def aclose(self):
        self.closed = True


class FakeMic:
    """An async iterator of fake frames fed by the scenario."""

    def __init__(self):
        self._queue: "asyncio.Queue" = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    async def feed(self, samples: np.ndarray, rate: int = 16000, channels: int = 1):
        """Sends float samples as 10 ms int16 frames, quickly (about 20x
        real time) but yielding to the loop so the session keeps up."""
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


def _speech(seconds: float, rate: int = 16000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.25 * np.sin(2 * np.pi * 220 * t) + 0.05 * np.random.randn(t.size)).astype(np.float32)


def _silence(seconds: float, rate: int = 16000) -> np.ndarray:
    return (0.001 * np.random.randn(int(seconds * rate))).astype(np.float32)


class _AudioChunk:
    def __init__(self, pcm, sample_rate, sentence_index):
        self.pcm, self.sample_rate, self.sentence_index = pcm, sample_rate, sentence_index


class _SentenceStart:
    def __init__(self, text, sentence_index):
        self.text, self.sentence_index = text, sentence_index


class _Done:
    def __init__(self):
        self.provider, self.first_audio_ms, self.total_ms = "fake", 1.0, 2.0


def fake_synth(text_or_iterable, language="en", voice="female_natural", settings=None):
    """Same contract as voicekit.core.synthesize_stream: str or iterable of
    str in, SynthEvent iterator out. Sentences are cut at ". "; every second
    sentence is produced at 22050 Hz to exercise the resampling."""
    import re

    pieces = [text_or_iterable] if isinstance(text_or_iterable, str) else text_or_iterable
    buffer, index = "", 0

    def sentences_from(text, final=False):
        nonlocal buffer
        buffer += text
        parts = re.split(r"(?<=[.!?])\s+", buffer)
        if not final:
            buffer = parts.pop() if parts else ""
        else:
            buffer = ""
        return [p for p in parts if p.strip()]

    def speak(sentence, i):
        yield _SentenceStart(sentence, i)
        rate = 22050 if i % 2 else 24000
        for _ in range(3):
            tone = (np.sin(2 * np.pi * 300 * np.arange(rate // 10) / rate) * 8000).astype(np.int16)
            time.sleep(0.01)
            yield _AudioChunk(tone.tobytes(), rate, i)

    for piece in pieces:
        for sentence in sentences_from(piece):
            yield from speak(sentence, index)
            index += 1
    for sentence in sentences_from("", final=True):
        yield from speak(sentence, index)
        index += 1
    yield _Done()


class FakeOrchestrator:
    last_mode = "fake agent"

    def __init__(self):
        self.questions: list = []

    def respond_stream(self, text, user_name=None, language=None, **kwargs):
        self.questions.append((text, user_name, language))
        if "long story" in text:
            for i in range(12):
                time.sleep(0.12)
                yield f"This is sentence number {i} of a long reply. "
        else:
            for piece in ("Fibre plans start at ten dollars. ", "They include unlimited data. ", "Anything else?"):
                time.sleep(0.05)
                yield piece


def _stub_bindings(orchestrator):
    from .session import Bindings

    return Bindings(
        orchestrator=orchestrator,
        greeting=lambda name, lang: (f"Welcome to the assistant, {name or 'Guest'}.", lang or "en"),
        looks_like_hallucination=lambda t: t.strip().lower() in {"you", "thank you"},
        sounds_like_closing_signal=lambda t: "thanks" in t.lower(),
        sounds_negative=lambda t: t.lower().startswith("no"),
        closing_question=lambda lang: "Is there anything else I can help you with?",
        closing_goodbye=lambda lang: "Goodbye and have a great day.",
        extract_user_name=lambda t: None,
    )


# --------------------------------------------------------------- session


async def _wait(predicate, timeout=15.0, label=""):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    print(f"  (timed out waiting for {label})")
    return False


async def _session_scenario():
    import stt
    from vad import VoiceActivityDetector

    from .session import LiveKitVoiceSession, SessionSettings

    class AmplitudeVad(VoiceActivityDetector):
        """Real calibration, but speech is "loud enough": synthetic tones are
        not speech to WebRTC VAD."""

        def is_speech(self, chunk):
            return chunk.size > 0 and float(np.sqrt(np.mean(np.square(chunk)))) >= self.noise_floor_rms * 2

    finals = iter(["what are the broadband packages", "tell me a long story about fibre", "thanks", "no thanks"])

    def fake_emit(audio, language_hint, final=False):
        return (next(finals) if final else "partial words", "en")

    real_emit = stt._emit
    stt._emit = fake_emit
    try:
        room, mic, source, orchestrator = FakeRoom(), FakeMic(), FakeAudioSource(), FakeOrchestrator()
        session = LiveKitVoiceSession(
            room,
            SessionSettings(language="en", voice_style="female_natural", user_name="Nimal", verified=False,
                            participant_identity="guest-test"),
            _stub_bindings(orchestrator),
            audio_stream_factory=lambda track: mic,
            audio_output_factory=lambda: source,
            vad_factory=AmplitudeVad,
            transcribe_fn=stt.transcribe_stream,
            synth_fn=fake_synth,
            chunk_seconds=stt.CHUNK_SECONDS,
        )
        participant = types.SimpleNamespace(identity="guest-test", track_publications={})
        events = lambda kind: [e for _, e in room.local_participant.events if e.get("type") == kind]
        statuses = lambda state: [e for e in events("status") if e["state"] == state]

        task = asyncio.ensure_future(session.run())
        await _wait(lambda: room.handlers.get("track_subscribed"), label="handlers attached")
        room.emit("track_subscribed", types.SimpleNamespace(kind=__import__("livekit.rtc", fromlist=["x"]).TrackKind.KIND_AUDIO), None, participant)

        print("greeting and calibration")
        check("greeting spoken before calibration", await _wait(lambda: len(events("speaking_done")) >= 1, label="greeting"))
        check("greeting text is the introduction", events("agent_response")[0]["mode"] == "Introduction")
        check("status calibrating sent", await _wait(lambda: statuses("calibrating"), label="calibrating"))
        # 48 kHz stereo, to run the resampling and downmix path.
        await mic.feed(_silence(1.3, 48000), rate=48000, channels=2)
        check("status listening after calibration", await _wait(lambda: statuses("listening"), label="listening"))

        print("turn 1: a normal question")
        await mic.feed(_speech(1.6))
        await mic.feed(_silence(1.5))
        check("final transcript sent", await _wait(lambda: events("final_transcript"), label="final"))
        check("partial transcript sent", len(events("partial_transcript")) >= 1)
        check("agent reply streamed in chunks", await _wait(lambda: events("agent_response_chunk"), label="chunks"))
        check("speaking status sent", await _wait(lambda: statuses("speaking"), label="speaking"))
        check("speaking_sentence sent", await _wait(lambda: events("speaking_sentence"), label="sentence"))
        check("agent_response sent with the mode", await _wait(lambda: len(events("agent_response")) >= 2, label="agent_response") and events("agent_response")[1]["mode"] == "fake agent")
        check("speaking_done sent", await _wait(lambda: len(events("speaking_done")) >= 2, label="speaking_done"))
        check("audio frames published at 24000 Hz only", source.frames > 0 and source.rates == {24000}, str(source.rates))
        check("orchestrator got text, name, language", orchestrator.questions[0] == ("what are the broadband packages", "Nimal", "en"))
        check("turn latency recorded", await _wait(lambda: session.last_turn_latency is not None, label="latency"))
        if session.last_turn_latency:
            print(f"  turn 1 first audio {session.last_turn_latency['first_audio_s']:.3f}s after the final transcript (fakes)")

        print("turn 2: barge in while the assistant is speaking")
        await _wait(lambda: len(statuses("listening")) >= 2, label="listening again")
        await asyncio.sleep(0.1)
        await mic.feed(_speech(1.2))
        await mic.feed(_silence(1.5))
        check("second final transcript", await _wait(lambda: len(events("final_transcript")) >= 2, label="final 2"))
        check("assistant started speaking the long reply", await _wait(lambda: len(statuses("speaking")) >= 2, label="speaking 2"))
        await _wait(lambda: len(events("speaking_sentence")) >= 3, label="3 sentences")
        done_before = len(events("speaking_done"))
        clears_before = source.clears
        frames_before = source.frames
        await mic.feed(_speech(0.6))
        check("interrupt event sent", await _wait(lambda: events("interrupt"), label="interrupt"))
        check("audio queue cleared", source.clears == clears_before + 1)
        await asyncio.sleep(0.4)
        check("no more audio after the interrupt", source.frames - frames_before <= 6, str(source.frames - frames_before))
        check("interrupted turn did not send speaking_done", len(events("speaking_done")) == done_before)
        check("status listening after the interrupt", len(statuses("listening")) >= 3)

        print("turn 3 and 4: closing detection")
        await mic.feed(_speech(0.8))
        await mic.feed(_silence(1.5))
        check("interrupting speech became the next transcript", await _wait(lambda: len(events("final_transcript")) >= 3, label="final 3") and events("final_transcript")[2]["text"] == "thanks")
        check("closing question spoken", await _wait(lambda: [e for e in events("agent_response") if e["mode"] == "closing"], label="closing question"))
        await _wait(lambda: len(events("speaking_done")) > done_before, label="closing question done")
        await _wait(lambda: len(statuses("listening")) >= 4, label="listening 4")
        await asyncio.sleep(0.1)
        await mic.feed(_speech(1.0))
        await mic.feed(_silence(1.5))
        check("goodbye then end_chat", await _wait(lambda: events("end_chat"), label="end_chat"))
        check("session returned", await _wait(task.done, timeout=5, label="session end"))
        if task.done() and task.exception():
            check("session finished without error", False, repr(task.exception()))
        check("audio source released", source.closed)
        check("handlers removed", not any(room.handlers.values()))
        types_seen = {e["type"] for _, e in room.local_participant.events}
        expected = {"status", "partial_transcript", "final_transcript", "agent_response_chunk", "agent_response",
                    "speaking_sentence", "speaking_done", "interrupt", "end_chat"}
        check("event types match /ws/voice", expected <= types_seen, str(expected - types_seen))

        print("control messages")
        session2 = LiveKitVoiceSession(FakeRoom(), SessionSettings(participant_identity="guest-test"), _stub_bindings(orchestrator))
        sender = types.SimpleNamespace(identity="guest-test")
        other = types.SimpleNamespace(identity="someone-else")
        send = lambda body, who=sender, topic="va-control": session2._on_data_received(
            types.SimpleNamespace(data=json.dumps(body).encode(), topic=topic, participant=who))
        send({"type": "set_voice", "voice_style": "male"})
        check("set_voice male accepted", session2._voice_style == "male")
        send({"type": "set_voice", "voice_style": "robot"})
        check("set_voice invalid value ignored", session2._voice_style == "male")
        send({"type": "set_voice", "voice_style": "female_natural"}, who=other)
        check("control from another participant ignored", session2._voice_style == "male")
        send({"type": "mute", "muted": True})
        check("mute true", session2._muted is True)
        send({"type": "mute", "muted": False})
        check("mute false", session2._muted is False)
    finally:
        stt._emit = real_emit


# ---------------------------------------------------------------- tokens


def _token_checks():
    import jwt
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import config
    from auth.users import VerifiedUser

    from . import tokens

    app = FastAPI()
    app.include_router(tokens.router)
    client = TestClient(app)
    secret = "test-secret-" + "x" * 32
    saved = (config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
    saved_verifier = tokens.verified_user_from_request

    print("token endpoint")
    config.LIVEKIT_URL = config.LIVEKIT_API_KEY = config.LIVEKIT_API_SECRET = ""
    response = client.post("/api/livekit/token", json={})
    check("503 when not configured", response.status_code == 503 and "not configured" in response.json()["detail"])
    check("config says not configured", client.get("/api/livekit/config").json()["livekit_configured"] is False)

    config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET = "wss://lk.example.test", "devkey", secret
    tokens.verified_user_from_request = lambda request, explicit=None: None
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            response = client.post(
                "/api/livekit/token",
                json={"language": "si", "voice_style": "male", "user_name": "  Nimal   Perera ", "session_token": "ignored"},
            )
        data = response.json()
        check("200 for a guest", response.status_code == 200)
        claims = jwt.decode(data["token"], secret, algorithms=["HS256"], options={"verify_aud": False})
        check("identity is guest-<random>", claims["sub"] == data["identity"] and data["identity"].startswith("guest-"))
        check("issuer is the api key", claims["iss"] == "devkey")
        check("TTL matches config", claims["exp"] - claims["nbf"] == config.LIVEKIT_TOKEN_TTL_SECONDS, f"{claims['exp'] - claims['nbf']}")
        check("expires_at about now + TTL", abs(data["expires_at"] - (time.time() + config.LIVEKIT_TOKEN_TTL_SECONDS)) < 5)
        video = claims["video"]
        check("room grant matches response room", video["room"] == data["room"] and data["room"].startswith("va-"))
        check("grants: join, publish, subscribe", video["roomJoin"] and video["canPublish"] and video["canSubscribe"])
        check("no admin or create grants", not any(video.get(k) for k in ("roomCreate", "roomAdmin", "roomList", "roomRecord", "ingressAdmin")))
        check("publish limited to the microphone", video.get("canPublishSources") == ["microphone"])
        attrs = claims["attributes"]
        check("attributes: language, voice, name, guest", attrs == {"language": "si", "voice_style": "male", "user_name": "Nimal Perera", "verified": "false"}, str(attrs))
        agents = claims["roomConfig"]["agents"]
        check("agent dispatch to the configured agent", agents[0]["agentName"] == config.LIVEKIT_AGENT_NAME)
        check("url returned, secret not", data["url"] == "wss://lk.example.test" and secret not in response.text)
        check("secret not inside the token", secret.encode() not in __import__("base64").urlsafe_b64decode(data["token"].split(".")[1] + "=="))
        check("nothing logged contains the secret or token", secret not in captured.getvalue() and data["token"] not in captured.getvalue())

        second = client.post("/api/livekit/token", json={}).json()
        check("room and identity differ per call", second["room"] != data["room"] and second["identity"] != data["identity"])
        invalid = client.post("/api/livekit/token", json={"voice_style": "robot", "language": "fr"}).json()
        invalid_attrs = jwt.decode(invalid["token"], secret, algorithms=["HS256"], options={"verify_aud": False})["attributes"]
        check("invalid voice and language fall back to defaults", invalid_attrs["voice_style"] == "female_natural" and invalid_attrs["language"] == "")

        tokens.verified_user_from_request = lambda request, explicit=None: VerifiedUser(id=7, display_name="Kamala", phone_e164="+94112000000")
        verified = client.post("/api/livekit/token", json={"user_name": "Evil Name", "voice_style": "female_natural"}).json()
        vclaims = jwt.decode(verified["token"], secret, algorithms=["HS256"], options={"verify_aud": False})
        check("verified identity is user-<id>", vclaims["sub"] == "user-7")
        check("verified name comes from the account, not the body", vclaims["attributes"]["user_name"] == "Kamala" and vclaims["name"] == "Kamala")
        check("verified flag set", vclaims["attributes"]["verified"] == "true")

        cfg = client.get("/api/livekit/config").json()
        check("config shape", set(cfg) == {"transport", "livekit_configured", "avatar_renderer"} and cfg["livekit_configured"] is True)
        check("config exposes no secret", secret not in json.dumps(cfg))
    finally:
        config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET = saved
        tokens.verified_user_from_request = saved_verifier


# ---------------------------------------------------------------- worker


def _worker_checks():
    import config

    from . import worker

    print("worker degradation")
    saved = (config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
    try:
        config.LIVEKIT_URL = config.LIVEKIT_API_KEY = config.LIVEKIT_API_SECRET = ""
        handle = worker.run_in_thread()
        handle.thread.join(30)
        check("not configured: thread ends, state not_configured", handle.state == "not_configured" and not handle.thread.is_alive(), f"{handle.state} alive={handle.thread.is_alive()}")

        config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET = "ws://127.0.0.1:9", "devkey", "s" * 40
        options = worker.build_worker_options()
        from livekit import agents

        check("options: thread executor and agent name", options.job_executor_type == agents.JobExecutorType.THREAD and options.agent_name == config.LIVEKIT_AGENT_NAME)
        handle = worker.run_in_thread()
        reached = False
        deadline = time.time() + 60
        while time.time() < deadline:
            if handle.state == "retrying":
                reached = True
                break
            time.sleep(0.5)
        check("unreachable server: worker reports retrying, host keeps running", reached and handle.thread.is_alive(), handle.state)
        handle.stop(15)
        check("worker stops cleanly", not handle.thread.is_alive() and handle.state == "stopped", handle.state)
    finally:
        config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET = saved


def main() -> int:
    only_worker = "--worker" in sys.argv
    _token_checks()
    print("session against a fake room")
    asyncio.run(asyncio.wait_for(_session_scenario(), timeout=90))
    if only_worker:
        _worker_checks()
    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED: {failures}")
        return 1
    print("All checks passed (fake room; not live tested against a LiveKit server).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
