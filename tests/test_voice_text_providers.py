import base64
import io
import os
import sys
import threading
import types
import urllib.error
import wave

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from voicekit.providers import gemini_tts, livekit_inference_tts as lk, piper_si  # noqa: E402
from voicekit.types import AudioChunk, ProviderUnavailable  # noqa: E402

LK_ENV = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


def drain(it):
    return list(it)


def test_livekit_unavailable_without_credentials(monkeypatch):
    for n in LK_ENV:
        monkeypatch.delenv(n, raising=False)
    s = types.SimpleNamespace()
    assert not lk.available(s)
    with pytest.raises(ProviderUnavailable) as e:
        drain(lk.stream("hi", "en", "male", s, threading.Event()))
    assert "LIVEKIT_URL" in str(e.value)


def test_livekit_split_entry():
    assert lk.split_entry("inworld/inworld-tts-2:Ashley") == ("inworld/inworld-tts-2", "Ashley")
    assert lk.split_entry("inworld/inworld-tts-2") == ("inworld/inworld-tts-2", None)


def test_livekit_streams_pcm_with_a_fake_model(monkeypatch):
    for n in LK_ENV:
        monkeypatch.setenv(n, "x")
    calls = []

    class Frame:
        data = b"\x01\x00" * 480
        sample_rate = 24000
        num_channels = 1

    class Ev:
        frame = Frame()

    class Stream:
        def __aiter__(self):
            self.n = 0
            return self

        async def __anext__(self):
            self.n += 1
            if self.n > 2:
                raise StopAsyncIteration
            return Ev()

        async def aclose(self):
            pass

    class Fake:
        def synthesize(self, text):
            return Stream()

    def make(model, voice, language, settings):
        calls.append((model, voice, language))
        return Fake()

    monkeypatch.setattr(lk, "_make_tts", make)
    chunks = drain(lk.stream("hello", "si", "female_natural", types.SimpleNamespace(), threading.Event()))
    assert len(chunks) == 2 and all(isinstance(c, AudioChunk) and c.sample_rate == 24000 for c in chunks)
    assert calls[0][0].startswith("inworld/") and calls[0][2] == "si"


def test_livekit_failed_entries_end_unavailable(monkeypatch):
    for n in LK_ENV:
        monkeypatch.setenv(n, "x")

    def make(*a):
        raise RuntimeError("boom")

    monkeypatch.setattr(lk, "_make_tts", make)
    with pytest.raises(ProviderUnavailable):
        drain(lk.stream("hello", "en", "male", types.SimpleNamespace(), threading.Event()))


def test_gemini_tts_off_by_default(monkeypatch):
    monkeypatch.delenv("GEMINI_TTS_ENABLED", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert not gemini_tts.available(types.SimpleNamespace())
    with pytest.raises(ProviderUnavailable):
        drain(gemini_tts.stream("hi", "en", "male", types.SimpleNamespace(), threading.Event()))


def test_gemini_tts_mock_pcm_and_wav(monkeypatch):
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    pcm = b"\x10\x00" * 4800
    seen = {}

    def post(url, body, key):
        seen["url"], seen["body"] = url, body
        inline = {"mimeType": "audio/L16;codec=pcm;rate=24000", "data": base64.b64encode(pcm).decode()}
        return {"candidates": [{"content": {"parts": [{"inlineData": inline}]}}]}

    monkeypatch.setattr(gemini_tts, "_post", post)
    chunks = drain(gemini_tts.stream("hi", "ta", "male", types.SimpleNamespace(), threading.Event()))
    assert b"".join(c.pcm for c in chunks) == pcm and chunks[0].sample_rate == 24000
    assert "gemini-3.8-flash-tts" in seen["url"]
    cfg = seen["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert cfg["voiceName"] == "Puck"

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    out, rate = gemini_tts.decode_audio({"mimeType": "audio/wav", "data": base64.b64encode(buf.getvalue()).decode()})
    assert out == pcm and rate == 16000


def test_gemini_tts_http_error_is_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_TTS_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "k")

    def post(*a):
        raise urllib.error.HTTPError("u", 429, "credits", {}, None)

    monkeypatch.setattr(gemini_tts, "_post", post)
    with pytest.raises(ProviderUnavailable) as e:
        drain(gemini_tts.stream("hi", "en", "male", types.SimpleNamespace(), threading.Event()))
    assert "429" in str(e.value)


def test_piper_si_unavailable_without_model(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPER_SI_MODEL_DIR", str(tmp_path))
    s = types.SimpleNamespace()
    assert not piper_si.available(s)
    for lang, voice in (("si", "female_natural"), ("si", "male"), ("en", "female_natural")):
        with pytest.raises(ProviderUnavailable):
            drain(piper_si.stream("x", lang, voice, s, threading.Event()))
    assert piper_si.licence(s) == "no model installed"
