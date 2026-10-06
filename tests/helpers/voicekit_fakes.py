"""Fakes shared by the voicekit tests: a synthesize_stream replacement that
yields scripted events, installed everywhere the real one was imported."""

import importlib
import struct

SAMPLE_RATE = 24000


def pcm(n_samples=480, value=1000):
    return struct.pack("<%dh" % n_samples, *([value] * n_samples))


def scripted_events(text="Hello there.", provider="fake", with_timings=True):
    """One sentence: SentenceStart, two timings, one audio chunk, done."""
    from voicekit import types as t

    events = [t.SentenceStart(text=text, sentence_index=0)]
    if with_timings:
        events.append(t.WordTiming(text="Hello", start_ms=0.0, duration_ms=200.0, sentence_index=0))
        events.append(t.WordTiming(text="there.", start_ms=200.0, duration_ms=250.0, sentence_index=0))
    events.append(t.AudioChunk(pcm=pcm(), sample_rate=SAMPLE_RATE, sentence_index=0))
    events.append(t.SynthesisDone(provider=provider, first_audio_ms=12.0, total_ms=30.0))
    return events


def install_fake_synth(monkeypatch, events_factory=None, calls=None):
    """Replaces synthesize_stream in voicekit.core and in every voicekit
    module that imported the name. Returns the list of recorded calls."""
    calls = [] if calls is None else calls
    factory = events_factory or (lambda text: scripted_events(text if isinstance(text, str) else "Hello there."))

    class FakeStream:
        """Like voicekit.core.SynthStream: an iterator with cancel()."""

        def __init__(self, text_or_iterable):
            if not isinstance(text_or_iterable, str):
                text_or_iterable = "".join(text_or_iterable)  # an LLM reply being written
            self.cancelled = False
            self._events = iter(factory(text_or_iterable))

        def __iter__(self):
            return self

        def __next__(self):
            if self.cancelled:
                raise StopIteration
            return next(self._events)

        def cancel(self):
            self.cancelled = True

    def fake(text_or_iterable, language="en", voice="female_natural", settings=None):
        from voicekit import types as t

        if language not in ("en", "si", "ta") or voice not in ("female_natural", "male"):
            raise t.InvalidRequest("unknown language or voice")
        calls.append({"text": text_or_iterable, "language": language, "voice": voice})
        return FakeStream(text_or_iterable)

    for name in ("voicekit.core", "voicekit.adapter", "voicekit.api", "voicekit"):
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        if hasattr(module, "synthesize_stream"):
            monkeypatch.setattr(module, "synthesize_stream", fake)
    return calls
