"""Sinks: where synthesized speech goes.

Every sink takes the SynthEvent iterator from synthesize_stream() and has
play(events) (blocks until the audio is delivered or cancelled) and cancel()
(safe to call from another thread, used for barge in: it stops delivery and
tells the synthesizer to stop, so no more network or CPU is spent).

    SpeakerSink      the local sound card through sounddevice
    CallbackSink     calls your function for every event
    HttpChunkSink    turns events into bytes for a chunked HTTP body
    WebSocketSink    sends JSON control messages and binary PCM frames
    LiveKitSink      feeds a LiveKit rtc.AudioSource (duck typed)

Word times in the wire formats (HttpChunkSink ndjson, WebSocketSink) are in
ms from the start of the whole utterance, so a client needs no per sentence
bookkeeping; the in process WordTiming keeps its per sentence meaning.
"""

import asyncio
import base64
import inspect
import json
import threading
from abc import ABC, abstractmethod
from typing import Callable, Iterable, Iterator, Optional

from .audio import resample_pcm16, wav_header
from .types import AudioChunk, SentenceStart, SynthesisDone, SynthEvent, WordTiming


class Sink(ABC):
    def __init__(self):
        self._cancelled = threading.Event()
        self._events = None

    @abstractmethod
    def play(self, events: Iterable[SynthEvent]) -> None: ...

    def cancel(self) -> None:
        self._cancelled.set()
        stop = getattr(self._events, "cancel", None)
        if stop is not None:
            stop()

    def _begin(self, events) -> Iterator[SynthEvent]:
        """Registers events for cancel() and yields until cancelled."""
        self._cancelled.clear()
        self._events = events
        for event in events:
            if self._cancelled.is_set():
                break
            yield event


class UtteranceClock:
    """Tracks how much audio has been emitted, to turn per sentence word
    times into times from the start of the utterance."""

    def __init__(self):
        self._sentence_offset_ms = {}
        self._emitted_ms = 0.0

    def see(self, event) -> None:
        if isinstance(event, SentenceStart):
            self._sentence_offset_ms[event.sentence_index] = self._emitted_ms
        elif isinstance(event, AudioChunk):
            self._emitted_ms += event.duration_ms

    def absolute_ms(self, timing: WordTiming) -> float:
        return self._sentence_offset_ms.get(timing.sentence_index, 0.0) + timing.start_ms


def _word_dict(timing: WordTiming, clock: UtteranceClock) -> dict:
    return {
        "text": timing.text,
        "start_ms": round(clock.absolute_ms(timing), 1),
        "duration_ms": round(timing.duration_ms, 1),
        "sentence_index": timing.sentence_index,
    }


class SpeakerSink(Sink):
    """Plays on the local sound card. Needs sounddevice and a device; the
    stream is reopened when a fallback provider changes the sample rate."""

    def __init__(self, device=None):
        super().__init__()
        self._device = device
        self._stream = None

    def play(self, events) -> None:
        import sounddevice as sd

        rate = 0
        try:
            for event in self._begin(events):
                if not isinstance(event, AudioChunk):
                    continue
                if event.sample_rate != rate:
                    self._close(wait=True)
                    rate = event.sample_rate
                    self._stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16", device=self._device)
                    self._stream.start()
                self._stream.write(event.pcm)
        finally:
            self._close(wait=not self._cancelled.is_set())

    def _close(self, wait: bool) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop() if wait else stream.abort()  # stop() plays out what is buffered
            stream.close()
        except Exception:
            pass

    def cancel(self) -> None:
        super().cancel()
        stream = self._stream
        if stream is not None:
            try:
                stream.abort()  # drop what is buffered right now, do not wait for write()
            except Exception:
                pass


class CallbackSink(Sink):
    """Calls fn(event) for every event, on the thread that called play()."""

    def __init__(self, fn: Callable[[SynthEvent], None]):
        super().__init__()
        self._fn = fn

    def play(self, events) -> None:
        for event in self._begin(events):
            self._fn(event)


def events_to_ndjson(events, clock: Optional[UtteranceClock] = None) -> Iterator[bytes]:
    clock = clock or UtteranceClock()
    for event in events:
        clock.see(event)
        if isinstance(event, AudioChunk):
            line = {"type": "audio", "pcm": base64.b64encode(event.pcm).decode("ascii"),
                    "sample_rate": event.sample_rate, "sentence_index": event.sentence_index}
        elif isinstance(event, WordTiming):
            line = {"type": "timing", **_word_dict(event, clock)}
        elif isinstance(event, SentenceStart):
            line = {"type": "sentence", "text": event.text, "sentence_index": event.sentence_index}
        else:
            line = {"type": "done", "provider": event.provider,
                    "first_audio_ms": round(event.first_audio_ms, 1), "total_ms": round(event.total_ms, 1)}
        yield (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")


class HttpChunkSink(Sink):
    """Produces the bytes of a chunked HTTP body: .stream(events) is a
    generator for a streaming response. format is "pcm" (raw bytes), "wav"
    (streaming WAV, unknown length) or "ndjson" (JSON lines, audio as base64).
    pcm and wav carry only audio, resampled to the first chunk's rate."""

    def __init__(self, format: str = "ndjson"):
        super().__init__()
        if format not in ("pcm", "wav", "ndjson"):
            raise ValueError(f"unknown format {format!r}")
        self.format = format
        self.sample_rate = 0

    def stream(self, events) -> Iterator[bytes]:
        source = self._begin(events)
        if self.format == "ndjson":
            yield from events_to_ndjson(source)
            return
        for event in source:
            if not isinstance(event, AudioChunk):
                continue
            if not self.sample_rate:
                self.sample_rate = event.sample_rate
                if self.format == "wav":
                    yield wav_header(self.sample_rate)
            yield resample_pcm16(event.pcm, event.sample_rate, self.sample_rate)

    def play(self, events) -> None:
        for _ in self.stream(events):
            pass


class WsTranslator:
    """Turns SynthEvents into /ws/tts messages one event at a time, keeping
    the state it needs: an audio_start when the sample rate first appears or
    changes, pending word timings sent as one timings message just before the
    audio that follows them, binary PCM frames, then done."""

    def __init__(self):
        self._clock = UtteranceClock()
        self._rate = 0
        self._pending: list = []

    def _flush(self) -> list:
        if not self._pending:
            return []
        words, self._pending = self._pending, []
        return [("json", {"type": "timings", "words": words})]

    def feed(self, event) -> list:
        """Returns a list of ("json", dict) or ("bytes", pcm)."""
        self._clock.see(event)
        if isinstance(event, WordTiming):
            self._pending.append(_word_dict(event, self._clock))
            return []
        if isinstance(event, AudioChunk):
            out = []
            if event.sample_rate != self._rate:
                self._rate = event.sample_rate
                out.append(("json", {"type": "audio_start", "sample_rate": self._rate}))
            return out + self._flush() + [("bytes", event.pcm)]
        if isinstance(event, SynthesisDone):
            return self._flush() + [("json", {"type": "done", "provider": event.provider,
                                              "first_audio_ms": round(event.first_audio_ms, 1),
                                              "total_ms": round(event.total_ms, 1)})]
        return []


def events_to_ws_messages(events) -> Iterator[tuple]:
    """Generator form of WsTranslator, for synchronous senders."""
    translator = WsTranslator()
    for event in events:
        yield from translator.feed(event)


class WebSocketSink(Sink):
    """send_bytes(bytes) and send_json(dict) are plain (blocking) callables,
    for example thin wrappers over a sync websocket. Async servers should use
    events_to_ws_messages() directly, as api.py does."""

    def __init__(self, send_bytes: Callable[[bytes], None], send_json: Callable[[dict], None]):
        super().__init__()
        self._send_bytes = send_bytes
        self._send_json = send_json

    def play(self, events) -> None:
        for kind, payload in events_to_ws_messages(self._begin(events)):
            (self._send_json if kind == "json" else self._send_bytes)(payload)


class LiveKitSink(Sink):
    """Feeds a LiveKit rtc.AudioSource, or anything with capture_frame(frame)
    and clear_queue(). capture_frame is a coroutine in the real SDK, so pass
    the event loop that owns the source (loop=); play() is meant to run on a
    worker thread, not on that loop. Chunks are resampled to the source rate
    (sample_rate=, 24000 by default, the agent-voice track rate) and cut into
    10 ms frames. cancel() calls clear_queue() so audio already handed to
    LiveKit stops too: this is what makes barge in immediate."""

    def __init__(self, audio_source, sample_rate: int = 24000, loop: Optional[asyncio.AbstractEventLoop] = None,
                 frame_factory: Optional[Callable[[bytes, int], object]] = None):
        super().__init__()
        self._source = audio_source
        self._rate = sample_rate
        self._loop = loop
        self._frame_factory = frame_factory or self._livekit_frame

    @staticmethod
    def _livekit_frame(pcm: bytes, rate: int):
        from livekit import rtc  # only needed with a real LiveKit source

        return rtc.AudioFrame(data=pcm, sample_rate=rate, num_channels=1, samples_per_channel=len(pcm) // 2)

    def _capture(self, frame) -> None:
        result = self._source.capture_frame(frame)
        if inspect.isawaitable(result):
            if self._loop is None:
                if inspect.iscoroutine(result):
                    result.close()
                raise RuntimeError("LiveKitSink needs loop= when capture_frame is a coroutine")
            asyncio.run_coroutine_threadsafe(result, self._loop).result()

    def play(self, events) -> None:
        frame_bytes = self._rate // 100 * 2  # 10 ms of 16 bit mono
        leftover = b""
        for event in self._begin(events):
            if not isinstance(event, AudioChunk):
                continue
            data = leftover + resample_pcm16(event.pcm, event.sample_rate, self._rate)
            usable = len(data) - len(data) % frame_bytes
            leftover = data[usable:]
            for i in range(0, usable, frame_bytes):
                if self._cancelled.is_set():
                    return
                self._capture(self._frame_factory(data[i : i + frame_bytes], self._rate))
        if leftover and not self._cancelled.is_set():
            self._capture(self._frame_factory(leftover.ljust(frame_bytes, b"\x00"), self._rate))

    def cancel(self) -> None:
        super().cancel()
        clear = getattr(self._source, "clear_queue", None)
        if clear is not None:
            clear()
