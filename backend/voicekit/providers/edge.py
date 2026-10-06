"""edge-tts provider: Microsoft Edge neural voices, no key, needs internet.

edge-tts is async and streams MP3 over a websocket. It also sends
WordBoundary events with the time and length of every word, which is what a
talking avatar needs. This provider runs the async stream on its own thread
and event loop and hands events to the (synchronous) caller through a queue,
so audio chunks are delivered while the sentence is still being synthesized.

Limits: it is an unofficial client of a Microsoft endpoint that has no SLA;
the MP3 is decoded here (see audio.Mp3StreamDecoder) and the first chunk
arrives after the first few KB of MP3, not after the whole sentence.
"""

import asyncio
import queue
import threading
from typing import Iterator

from ..audio import Mp3StreamDecoder
from ..types import AudioChunk, ProviderUnavailable, WordTiming
from ..voices import edge_voice

NAME = "edge"
_DONE = object()
# edge-tts reports offsets and durations in 100 nanosecond ticks.
_TICKS_PER_MS = 10_000.0


def available(settings) -> bool:
    try:
        import edge_tts  # noqa: F401
    except Exception:
        return False
    return True


def _pump(text: str, voice_name: str, out: "queue.Queue", cancel: threading.Event) -> None:
    import edge_tts

    async def run():
        # edge-tts 7 defaults to sentence boundaries; words are opt in.
        communicate = edge_tts.Communicate(text, voice_name, boundary="WordBoundary")
        chunks = communicate.stream()
        try:
            async for chunk in chunks:
                if cancel.is_set():
                    return
                out.put(chunk)
        finally:
            await chunks.aclose()  # releases the HTTP session on early exit

    try:
        asyncio.run(run())
    except Exception as exc:
        out.put(exc)
    finally:
        out.put(_DONE)


def stream(text: str, language: str, voice: str, settings, cancel: threading.Event) -> Iterator[object]:
    if not available(settings):
        raise ProviderUnavailable("edge-tts is not installed")
    q: "queue.Queue" = queue.Queue()
    threading.Thread(
        target=_pump, args=(text, edge_voice(language, voice), q, cancel), daemon=True, name="voicekit-edge"
    ).start()
    decoder = Mp3StreamDecoder()
    got_audio = False
    while True:
        item = q.get()
        if item is _DONE:
            break
        if isinstance(item, Exception):
            if got_audio:
                break  # keep what was already played; the core moves on
            raise ProviderUnavailable(f"edge-tts failed: {type(item).__name__}")
        kind = item.get("type")
        if kind == "WordBoundary":
            yield WordTiming(
                text=item["text"],
                start_ms=item["offset"] / _TICKS_PER_MS,
                duration_ms=item["duration"] / _TICKS_PER_MS,
            )
        elif kind == "audio":
            out = decoder.feed(item["data"])
            if out:
                got_audio = True
                yield AudioChunk(out[0], out[1])
    tail = decoder.feed(b"", final=True) if decoder.has_data else None
    if tail:
        got_audio = True
        yield AudioChunk(tail[0], tail[1])
    if not got_audio:
        raise ProviderUnavailable("edge-tts returned no audio")
