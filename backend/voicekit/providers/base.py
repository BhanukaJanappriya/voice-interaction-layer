"""The small contract every provider follows.

A provider is a function from one sentence to an iterator of AudioChunk and
WordTiming events (sentence_index left at 0; the core fills it in). It raises
ProviderUnavailable, or any other exception, when it cannot speak; the core
treats both the same way and falls through to the next provider. A provider
must check cancel (a threading.Event) between chunks so barge in is quick.
"""

import threading
from typing import Callable, Iterator

from ..types import AudioChunk, WordTiming

ProviderFn = Callable[[str, str, str, "object", threading.Event], Iterator[object]]

# Markdown the LLM sometimes adds means nothing when spoken, and some voices
# read the symbols aloud.
import re

_UNSPOKEN_MARKUP = re.compile(r"[*#_`]+")


def clean_for_speech(text: str) -> str:
    return _UNSPOKEN_MARKUP.sub("", text).strip()


def pcm16_from_float(samples) -> bytes:
    """Float samples in -1..1 to 16 bit little endian mono bytes."""
    import numpy as np

    arr = np.asarray(samples, dtype="float32")
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return (np.clip(arr, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()


def chunked(pcm: bytes, sample_rate: int, chunk_ms: int = 100) -> Iterator[AudioChunk]:
    """Cuts finished PCM into small chunks (for providers that only return a
    whole sentence), so sinks see the same shape of data from every provider."""
    step = int(sample_rate * chunk_ms / 1000) * 2
    for i in range(0, len(pcm), step):
        yield AudioChunk(pcm[i : i + step], sample_rate)


__all__ = ["ProviderFn", "clean_for_speech", "pcm16_from_float", "chunked", "WordTiming"]
