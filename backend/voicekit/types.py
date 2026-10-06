"""Event types and errors shared by every voicekit module.

voicekit turns text into a stream of small events instead of one audio blob,
so a caller can start playing the first words while the rest is still being
synthesized, and can line a talking avatar up with the audio. Audio is always
16 bit little endian mono PCM, whatever the provider produced, because that is
the one format every sink (speakers, WebAudio, LiveKit, WebSocket) accepts
without further decoding.

This module imports nothing from the rest of the project, so the whole
package can be copied into another codebase.
"""

from dataclasses import dataclass
from typing import Union


@dataclass
class AudioChunk:
    """A piece of speech audio: 16 bit little endian mono PCM bytes."""

    pcm: bytes
    sample_rate: int
    sentence_index: int = 0

    @property
    def duration_ms(self) -> float:
        return len(self.pcm) / 2 / self.sample_rate * 1000.0


@dataclass
class WordTiming:
    """One spoken word. start_ms is measured from the start of THIS
    sentence's audio (not of the whole reply); the HTTP and WebSocket APIs
    convert it to a time from the start of the whole utterance. Only some
    providers report word times (edge-tts does, Google streaming does not)."""

    text: str
    start_ms: float
    duration_ms: float
    sentence_index: int = 0


@dataclass
class SentenceStart:
    """Marks the beginning of one sentence, before its audio."""

    text: str
    sentence_index: int = 0


@dataclass
class SynthesisDone:
    """Always the last event. provider is "none" when every provider failed,
    which is how a caller learns that nothing could be spoken."""

    provider: str
    first_audio_ms: float
    total_ms: float


SynthEvent = Union[AudioChunk, WordTiming, SentenceStart, SynthesisDone]


class VoicekitError(Exception):
    """Base class for every error voicekit raises on purpose."""


class ProviderUnavailable(VoicekitError):
    """One provider cannot speak this request (no key, no network, no model
    files, language not supported). Providers raise it; the core catches it
    and tries the next provider, so callers normally never see it."""


class InvalidRequest(VoicekitError):
    """The request itself is wrong (empty text, unknown language or voice,
    text over the length cap). The only error synthesize_stream raises."""
