"""voicekit core: provider fallback order, sentence chunking, word timings,
cancel, input validation. Providers are fakes, nothing touches audio
hardware or the network."""

import threading
import types as pytypes

import pytest

from conftest import require_module
from helpers.voicekit_fakes import pcm


@pytest.fixture(scope="module")
def core():
    require_module("voicekit.types", "backend/voicekit not delivered yet")
    return require_module("voicekit.core", "voicekit.core not delivered yet")


@pytest.fixture(scope="module")
def vk():
    require_module("voicekit.types", "backend/voicekit not delivered yet")
    return pytypes.SimpleNamespace(
        types=require_module("voicekit.types"),
        settings=require_module("voicekit.settings"),
    )


@pytest.fixture(autouse=True)
def _clear_cooldown(core):
    core._cooldown.clear()
    yield
    core._cooldown.clear()


def make_provider(name, behaviour="ok", available=True, words=("Hello", "there."), log=None):
    """A fake provider module: NAME, available(settings), stream(...)."""
    from voicekit import types as t

    log = [] if log is None else log

    def stream(text, language, voice, settings, cancel):
        log.append({"text": text, "language": language, "voice": voice})
        if behaviour == "unavailable":
            raise t.ProviderUnavailable("no key")
        if behaviour == "boom":
            raise RuntimeError("provider bug")
        if behaviour == "silent":
            return
        start = 0.0
        for word in words:
            yield t.WordTiming(text=word, start_ms=start, duration_ms=100.0)
            start += 100.0
        yield t.AudioChunk(pcm=pcm(240), sample_rate=24000)
        if behaviour == "fail_after_audio":
            raise RuntimeError("dropped mid sentence")
        yield t.AudioChunk(pcm=pcm(240), sample_rate=24000)

    return pytypes.SimpleNamespace(NAME=name, available=lambda settings: available, stream=stream, log=log)


@pytest.fixture
def chain(core, monkeypatch):
    def _set(*providers):
        monkeypatch.setattr(core, "_provider_chain", lambda settings: list(providers))

    return _set


def run(core, text, **kwargs):
    return list(core.synthesize_stream(text, **kwargs))


def kinds(events):
    return [type(e).__name__ for e in events]


# Fallback order


def test_first_provider_wins_when_it_works(core, chain):
    a, b = make_provider("A"), make_provider("B")
    chain(a, b)
    events = run(core, "Hello there.")
    assert events[-1].provider == "A"
    assert a.log and not b.log


def test_provider_unavailable_falls_through_to_the_next(core, chain):
    a, b, c = make_provider("A", "unavailable"), make_provider("B"), make_provider("C")
    chain(a, b, c)
    events = run(core, "Hello there.")
    assert events[-1].provider == "B"
    assert len(a.log) == 1 and len(b.log) == 1 and not c.log


def test_unexpected_provider_exception_also_falls_through(core, chain):
    a, b = make_provider("A", "boom"), make_provider("B")
    chain(a, b)
    assert run(core, "Hello there.")[-1].provider == "B"


def test_provider_that_yields_no_audio_falls_through(core, chain):
    a, b = make_provider("A", "silent"), make_provider("B")
    chain(a, b)
    assert run(core, "Hello there.")[-1].provider == "B"


def test_all_providers_failing_ends_with_provider_none_and_no_exception(core, chain):
    chain(make_provider("A", "unavailable"), make_provider("B", "boom"))
    events = run(core, "Hello there.")
    assert isinstance(events[-1], pytypes.SimpleNamespace) is False
    assert events[-1].provider == "none"
    assert not any(type(e).__name__ == "AudioChunk" for e in events)


def test_provider_failing_after_audio_is_not_repeated_by_the_next_one(core, chain):
    a, b = make_provider("A", "fail_after_audio"), make_provider("B")
    chain(a, b)
    events = run(core, "Hello there.")
    assert not b.log, "audio was already heard, the next provider must not speak the sentence again"
    assert events[-1].provider == "A"
    assert kinds(events).count("AudioChunk") == 1


def test_failed_provider_is_skipped_for_the_cooldown_period(core, chain):
    a, b = make_provider("A", "unavailable"), make_provider("B")
    chain(a, b)
    run(core, "First sentence.")
    run(core, "Second sentence.")
    assert len(a.log) == 1, "A failed once and must be skipped afterwards (30 second cooldown)"
    assert len(b.log) == 2


def test_cooldown_is_per_language(core, chain):
    a, b = make_provider("A", "unavailable"), make_provider("B")
    chain(a, b)
    run(core, "Hello.", language="en")
    run(core, "Hello.", language="ta")
    assert len(a.log) == 2


def test_cooldown_expires(core, chain, monkeypatch):
    import time

    a, b = make_provider("A", "unavailable"), make_provider("B")
    chain(a, b)
    run(core, "One.")
    real = time.monotonic
    monkeypatch.setattr(core.time, "monotonic", lambda: real() + core.COOLDOWN_SECONDS + 1)
    run(core, "Two.")
    assert len(a.log) == 2


def test_unconfigured_provider_is_skipped_and_the_next_one_speaks(core, chain):
    a, b = make_provider("A", "unavailable", available=False), make_provider("B")
    chain(a, b)
    assert run(core, "One.")[-1].provider == "B"
    assert run(core, "Two.")[-1].provider == "B"
    assert not core._cooldown, "a provider that was never configured must not start a cooldown"


def test_real_chain_follows_provider_order_setting(core, vk):
    settings = vk.settings.VoicekitSettings(provider_order=("sapi", "edge"))
    assert [m.NAME for m in core._provider_chain(settings)] == ["sapi", "edge"]
    default = vk.settings.VoicekitSettings()
    names = [m.NAME for m in core._provider_chain(default)]
    assert names[1:] == ["edge", "local_model", "sapi"] or names[1:] == ["edge", "local", "sapi"], names
    assert names[0] in ("google", "google_cloud", "cloud")


def test_default_order_never_puts_local_model_ahead_of_cloud_or_edge(vk):
    order = vk.settings.VoicekitSettings().provider_order
    assert order.index("cloud") < order.index("edge") < order.index("local_model") < order.index("sapi")


def test_azure_selected_by_cloud_provider(core, vk):
    settings = vk.settings.VoicekitSettings(cloud_provider="azure", provider_order=("cloud",))
    assert core._provider_chain(settings)[0].NAME == "azure"


# Chunking


def test_sentence_chunker_matches_the_tts_module_rules(core):
    import tts

    pieces = ["Hello the", "re. How are", " you? I am fine", ", thanks. And a bit more with no end"]
    for cls_a, cls_b in ((core.SentenceChunker, tts.SentenceChunker),):
        a, b = cls_a(), cls_b()
        out_a, out_b = [], []
        for piece in pieces:
            out_a += a.feed(piece)
            out_b += b.feed(piece)
        out_a += a.flush()
        out_b += b.flush()
        assert out_a == out_b


def test_chunker_cuts_at_sentence_ends_and_waits_for_the_boundary(core):
    chunker = core.SentenceChunker()
    assert chunker.feed("Hello th") == []
    assert chunker.feed("ere. How are") == ["Hello there."]
    assert chunker.feed(" you? Fine") == ["How are you?"]
    assert chunker.flush() == ["Fine"]
    assert chunker.flush() == []


def test_chunker_handles_sinhala_danda(core):
    chunker = core.SentenceChunker()
    assert chunker.feed("ආයුබෝවන්। ස්තූතියි") == ["ආයුබෝවන්।"]


def test_chunker_cuts_only_the_first_long_clause(core):
    chunker = core.SentenceChunker()
    first = "This opening sentence is long enough, and then it keeps going without a stop"
    out = chunker.feed(first)
    assert out == ["This opening sentence is long enough,"] or len(out) == 1
    assert chunker.feed(" more, and more, and still more text goes on and on and on") == []


def test_stream_cuts_pieces_into_sentences_with_indices(core, chain):
    p = make_provider("A")
    chain(p)
    pieces = iter(["Hello the", "re. How are", " you? Fine."])
    events = run(core, pieces)
    starts = [e for e in events if type(e).__name__ == "SentenceStart"]
    assert [s.text for s in starts] == ["Hello there.", "How are you?", "Fine."]
    assert [s.sentence_index for s in starts] == [0, 1, 2]
    assert [entry["text"] for entry in p.log] == ["Hello there.", "How are you?", "Fine."]


def test_sentence_start_precedes_its_audio_and_done_is_last(core, chain):
    chain(make_provider("A"))
    events = run(core, "One. Two.")
    assert type(events[0]).__name__ == "SentenceStart"
    assert type(events[-1]).__name__ == "SynthesisDone"
    assert kinds(events).count("SynthesisDone") == 1
    for index in (0, 1):
        start = next(i for i, e in enumerate(events) if type(e).__name__ == "SentenceStart" and e.sentence_index == index)
        audio = next(i for i, e in enumerate(events) if type(e).__name__ == "AudioChunk" and e.sentence_index == index)
        assert start < audio


def test_markdown_symbols_are_not_spoken(core, chain):
    p = make_provider("A")
    chain(p)
    run(core, "**Hello** `there`.")
    assert p.log[0]["text"] == "Hello there."


def test_events_arrive_while_input_is_still_being_written(core, chain):
    chain(make_provider("A"))
    release = threading.Event()

    def pieces():
        yield "First sentence. "
        assert release.wait(5), "the consumer never saw the first sentence while the input was blocked"
        yield "Second."

    stream = core.synthesize_stream(pieces())
    seen = []
    for event in stream:
        seen.append(event)
        if type(event).__name__ == "AudioChunk":
            release.set()
    assert any(type(e).__name__ == "SentenceStart" and e.text == "Second." for e in seen)


# Word timings


def test_word_timings_pass_through_with_sentence_index(core, chain):
    chain(make_provider("A", words=("Hello", "there.")))
    events = run(core, "Hello there. Hello there.")
    words = [e for e in events if type(e).__name__ == "WordTiming"]
    assert [w.text for w in words] == ["Hello", "there.", "Hello", "there."]
    assert [w.sentence_index for w in words] == [0, 0, 1, 1]
    assert words[1].start_ms == pytest.approx(words[0].start_ms + words[0].duration_ms)
    assert all(w.start_ms >= 0 and w.duration_ms > 0 for w in words)


def test_audio_chunks_are_16_bit_pcm(core, chain):
    chain(make_provider("A"))
    for e in run(core, "Hello there."):
        if type(e).__name__ == "AudioChunk":
            assert len(e.pcm) % 2 == 0 and e.sample_rate > 0


def test_done_reports_provider_and_timings(core, chain, capsys):
    chain(make_provider("A"))
    done = run(core, "Hello there.", language="ta")[-1]
    assert done.provider == "A"
    assert 0 <= done.first_audio_ms <= done.total_ms
    assert "voicekit first audio (A, ta):" in capsys.readouterr().out


# Cancel


def test_cancel_stops_the_stream_and_sets_the_provider_event(core, chain):
    captured = {}

    def stream(text, language, voice, settings, cancel):
        from voicekit import types as t

        captured["cancel"] = cancel
        for _ in range(1000):
            if cancel.is_set():
                return
            yield t.AudioChunk(pcm=pcm(240), sample_rate=24000)
            cancel.wait(0.01)

    chain(pytypes.SimpleNamespace(NAME="slow", available=lambda s: True, stream=stream))
    stream_obj = core.synthesize_stream("A long sentence.")
    next(stream_obj)
    stream_obj.cancel()
    rest = list(stream_obj)
    assert len(rest) < 1000
    assert captured["cancel"].is_set(), "the provider is handed the event that cancel() sets"


# Validation


def test_invalid_requests_raise_at_once(core, vk):
    bad = [
        dict(text_or_iterable=""),
        dict(text_or_iterable="   "),
        dict(text_or_iterable="Hi", language="xx"),
        dict(text_or_iterable="Hi", voice="robot"),
        dict(text_or_iterable="x" * 50, settings=vk.settings.VoicekitSettings(max_text_chars=10)),
    ]
    for kwargs in bad:
        with pytest.raises(vk.types.InvalidRequest):
            core.synthesize_stream(**kwargs)


def test_oversize_streamed_input_raises_from_the_iterator(core, chain, vk):
    chain(make_provider("A"))
    settings = vk.settings.VoicekitSettings(max_text_chars=20)
    with pytest.raises(vk.types.InvalidRequest):
        list(core.synthesize_stream(iter(["Short one. ", "x" * 30]), settings=settings))


def test_settings_defaults_match_the_contract(vk):
    s = vk.settings.VoicekitSettings()
    assert (s.google_credentials_path, s.azure_key, s.azure_region) == ("", "", "")
    assert s.cloud_provider == "google" and s.local_voice_dir == "" and s.max_text_chars == 2000
    assert tuple(s.provider_order) == ("cloud", "edge", "local_model", "sapi")


# Voices


def test_list_voices_has_the_contract_fields(core):
    voices = core.list_voices()
    assert voices
    for v in voices:
        assert {"language", "voice", "provider", "name", "license", "gender"} <= set(v)
        assert v["voice"] in ("female_natural", "male") and v["gender"] in ("female", "male")


def test_voice_maps_agree_with_tts_py(core):
    import tts
    from voicekit import voices

    assert voices.EDGE_VOICES == dict(tts._NEURAL_VOICE_MAP)
    assert voices.GOOGLE_VOICES == dict(tts._GOOGLE_CHIRP_VOICE_MAP)


def test_every_language_and_style_has_an_edge_voice(core):
    have = {(v["language"], v["voice"]) for v in core.list_voices() if v["provider"] == "edge"}
    assert have == {(l, s) for l in ("en", "si", "ta") for s in ("female_natural", "male")}
