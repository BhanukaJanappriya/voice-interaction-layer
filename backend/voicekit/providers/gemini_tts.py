"""Gemini text to speech through generateContent (model gemini-3.8-flash-tts). OFF BY DEFAULT.

Optional last resort voice for the local tier or a second opinion on pronunciation. It is
disabled unless GEMINI_TTS_ENABLED=1 (or settings.gemini_tts_enabled) AND GEMINI_API_KEY is set.
The key in backend/.env had no credit on 2026-10-15, so this module was only tested against a
mock; a real call returns 402 or RESOURCE_EXHAUSTED until the key is funded, which is mapped
to ProviderUnavailable.

The API returns base64 audio in candidates[0].content.parts[0].inlineData: raw 16 bit PCM at
24 kHz (mimeType audio/L16;rate=24000) per the Gemini docs, or a WAV container; both are handled.
The whole sentence is returned at once and cut into 100 ms chunks. No WordTiming events.
"""

import base64
import io
import json
import os
import threading
import urllib.error
import urllib.request
import wave
from typing import Iterator, Optional

from ..types import ProviderUnavailable
from ..voice_config import lookup
from .base import chunked

NAME = "gemini_tts"
DEFAULT_MODEL = "gemini-3.8-flash-tts"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
TIMEOUT_S = 20.0


def _flag(settings) -> bool:
    if getattr(settings, "gemini_tts_enabled", None) is not None:
        return bool(settings.gemini_tts_enabled)
    return os.environ.get("GEMINI_TTS_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def _key(settings) -> str:
    return (getattr(settings, "gemini_api_key", "") or os.environ.get("GEMINI_API_KEY", "") or "").strip()


def available(settings=None) -> bool:
    return _flag(settings) and bool(_key(settings))


def _post(url: str, body: dict, key: str) -> dict:
    """Separate so tests can replace the network call."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def decode_audio(inline: dict) -> tuple:
    """(pcm16_mono_bytes, sample_rate) from an inlineData dict."""
    raw = base64.b64decode(inline.get("data", ""))
    mime = str(inline.get("mimeType", ""))
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw), "rb") as w:
            frames = w.readframes(w.getnframes())
            rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if width != 2:
            raise ValueError("unsupported sample width")
        if channels > 1:
            import numpy as np

            frames = np.frombuffer(frames, dtype="<i2").reshape(-1, channels).mean(axis=1).astype("<i2").tobytes()
        return frames, rate
    rate = 24000
    if "rate=" in mime:
        try:
            rate = int(mime.split("rate=")[1].split(";")[0])
        except ValueError:
            pass
    return raw[: len(raw) - len(raw) % 2], rate


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    if not available(settings):
        raise ProviderUnavailable("Gemini TTS is off (set GEMINI_TTS_ENABLED=1 and GEMINI_API_KEY)")
    names = lookup("gemini_live", language, voice)
    voice_name: Optional[str] = names[0] if names else "Kore"
    model = getattr(settings, "gemini_tts_model", "") or os.environ.get("GEMINI_TTS_MODEL", "") or DEFAULT_MODEL
    body = {
        "contents": [{"parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}},
        },
    }
    try:
        data = _post(ENDPOINT.format(model=model), body, _key(settings))
        part = data["candidates"][0]["content"]["parts"][0]["inlineData"]
        pcm, rate = decode_audio(part)
    except urllib.error.HTTPError as exc:
        raise ProviderUnavailable(f"Gemini TTS HTTP {exc.code}") from exc
    except Exception as exc:
        raise ProviderUnavailable(f"Gemini TTS failed: {type(exc).__name__}") from exc
    if not pcm:
        raise ProviderUnavailable("Gemini TTS returned no audio")
    for chunk in chunked(pcm, rate):
        if cancel.is_set():
            return
        yield chunk
