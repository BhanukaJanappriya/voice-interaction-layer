"""voicekit: reusable streaming text to speech with word timings.

Standalone on purpose: it imports only the standard library, numpy and third
party packages, never other modules of the project it was built in, so the
whole folder can be copied elsewhere. See docs/VOICEKIT.md.

Only the cheap, dependency free names are imported here; the HTTP router
(voicekit.api, needs FastAPI) and the sinks are imported where used.
"""

from .core import list_voices, synthesize_stream
from .settings import VoicekitSettings
from .types import (
    AudioChunk,
    InvalidRequest,
    ProviderUnavailable,
    SentenceStart,
    SynthesisDone,
    SynthEvent,
    VoicekitError,
    WordTiming,
)

__all__ = [
    "synthesize_stream",
    "list_voices",
    "VoicekitSettings",
    "AudioChunk",
    "WordTiming",
    "SentenceStart",
    "SynthesisDone",
    "SynthEvent",
    "VoicekitError",
    "ProviderUnavailable",
    "InvalidRequest",
]
