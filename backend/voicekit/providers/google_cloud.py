"""Google Cloud Text to Speech provider (paid API, Chirp3-HD voices).

Uses the streaming API, which delivers its first audio in about 0.3 s where
the plain request needs about 1.4 s (measured when backend/tts.py was
written). Chunks are 16 bit mono PCM at 24 kHz.

Word timings: Google only returns timepoints for SSML <mark> tags, and the
Chirp3-HD voices do not accept SSML marks. So this provider yields NO
WordTiming events; clients fall back to audio analysis for lip sync. This is
a Google limitation, not something to fix here.

Sinhala has no Google voice, so for si this raises ProviderUnavailable and
the core moves on to edge-tts without a network call.
"""

import threading
from typing import Iterator

from ..types import AudioChunk, ProviderUnavailable
from ..voices import google_voice

NAME = "google"
SAMPLE_RATE = 24000
_clients: dict = {}


def available(settings) -> bool:
    return bool(settings.google_credentials_path)


def _client(path: str):
    if path not in _clients:
        from google.cloud import texttospeech

        _clients[path] = texttospeech.TextToSpeechClient.from_service_account_file(path)
    return _clients[path]


def _strip_wav_header(data: bytes, first: bool) -> bytes:
    """Google's streaming LINEAR16 can start the first chunk with a RIFF
    header; playing it as samples would click, so drop it."""
    if first and data[:4] == b"RIFF":
        return data[44:]
    return data


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    entry = google_voice(language, voice)
    if entry is None:
        raise ProviderUnavailable(f"Google has no voice for language {language}")
    if not available(settings):
        raise ProviderUnavailable("Google credentials not configured")
    try:
        from google.cloud import texttospeech

        client = _client(settings.google_credentials_path)
        cfg = texttospeech.StreamingSynthesizeConfig(
            voice=texttospeech.VoiceSelectionParams(language_code=entry[0], name=entry[1]),
            streaming_audio_config=texttospeech.StreamingAudioConfig(
                audio_encoding=texttospeech.AudioEncoding.PCM, sample_rate_hertz=SAMPLE_RATE
            ),
        )
        requests = [
            texttospeech.StreamingSynthesizeRequest(streaming_config=cfg),
            texttospeech.StreamingSynthesizeRequest(input=texttospeech.StreamingSynthesisInput(text=text)),
        ]
        responses = client.streaming_synthesize(iter(requests))
    except Exception as exc:
        raise ProviderUnavailable(f"Google setup failed: {type(exc).__name__}") from exc

    leftover = b""
    first = True
    got_audio = False
    try:
        for response in responses:
            if cancel.is_set():
                return
            if not response.audio_content:
                continue
            data = leftover + _strip_wav_header(response.audio_content, first)
            first = False
            usable = len(data) - (len(data) % 2)  # whole 16 bit samples only
            leftover = data[usable:]
            if usable:
                got_audio = True
                yield AudioChunk(data[:usable], SAMPLE_RATE)
    except Exception as exc:
        if not got_audio:
            raise ProviderUnavailable(f"Google stream failed: {type(exc).__name__}") from exc
        return
    if not got_audio:
        raise ProviderUnavailable("Google returned no audio")
