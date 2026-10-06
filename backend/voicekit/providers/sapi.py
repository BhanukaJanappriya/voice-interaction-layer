"""SAPI5 provider (the Windows speech engine) through pyttsx3, offline.

backend/tts.py plays through the engine directly, which cannot be streamed to
a browser or a LiveKit track. This provider renders the sentence to a
temporary WAV with save_to_file, reads it back as 16 bit mono PCM and yields
it in small chunks. So the audio is NOT streamed while it is being made: the
first chunk appears after the whole sentence is rendered (short, and local).

Limits: English only (the two stock voices David and Zira; no Sinhala or
Tamil voice is installed), no word timings, and one render at a time because
SAPI5 engines are not safe to share between threads.
"""

import os
import tempfile
import threading
from typing import Iterator

from ..types import ProviderUnavailable
from .base import chunked, pcm16_from_float

NAME = "sapi"
_lock = threading.Lock()


def available(settings) -> bool:
    try:
        import pyttsx3  # noqa: F401
    except Exception:
        return False
    return True


def _pick_voice_id(engine, voice: str):
    want_female = voice == "female_natural"
    for v in engine.getProperty("voices"):
        name = (v.name or "").lower()
        gender = str(getattr(v, "gender", "") or "").lower()
        if want_female and ("female" in gender or "zira" in name):
            return v.id
        if not want_female and (("male" in gender and "female" not in gender) or "david" in name):
            return v.id
    return None


def _render(text: str, voice: str) -> str:
    import pyttsx3

    fd, path = tempfile.mkstemp(suffix=".wav", prefix="voicekit_")
    os.close(fd)
    try:
        engine = pyttsx3.init()
        voice_id = _pick_voice_id(engine, voice)
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.save_to_file(text, path)
        engine.runAndWait()
        engine.stop()
    except Exception:
        os.unlink(path)
        raise
    return path


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    if language != "en":
        raise ProviderUnavailable("SAPI5 has only English voices here")
    if not available(settings):
        raise ProviderUnavailable("pyttsx3 is not installed")
    path = None
    try:
        with _lock:
            path = _render(text, voice)
        import soundfile as sf

        samples, rate = sf.read(path, dtype="float32")
    except Exception as exc:
        raise ProviderUnavailable(f"SAPI5 render failed: {type(exc).__name__}") from exc
    finally:
        if path and os.path.exists(path):
            os.unlink(path)
    pcm = pcm16_from_float(samples)
    if not pcm:
        raise ProviderUnavailable("SAPI5 produced no audio")
    for chunk in chunked(pcm, rate):
        if cancel.is_set():
            return
        yield chunk
