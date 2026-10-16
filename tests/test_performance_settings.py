"""The latency and accuracy settings: shorter end of speech wait, the larger speech model for the
languages that need it, and the short lived cache of knowledge base answers."""

import os
import re
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config  # noqa: E402
import stt  # noqa: E402
from tool_agent import knowledge  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_the_page_and_the_server_use_the_same_chunk_length():
    app = open(os.path.join(ROOT, "frontend", "app.js"), encoding="utf-8").read()
    page = float(re.search(r"const CHUNK_SECONDS = ([0-9.]+);", app).group(1))
    assert page == stt.CHUNK_SECONDS == 0.25


def test_an_utterance_ends_after_three_quarters_of_a_second_of_silence():
    assert stt.SILENCE_CHUNKS_TO_FINALIZE * stt.CHUNK_SECONDS == pytest.approx(config.STT_SILENCE_SECONDS, abs=0.13)
    assert stt.SILENCE_CHUNKS_TO_FINALIZE == 3
    assert stt.REFRESH_CHUNKS == 6


class _Vad:
    def __init__(self, pattern):
        self.pattern = list(pattern)

    def is_speech(self, chunk):
        return self.pattern.pop(0)


def test_the_final_transcript_arrives_after_the_silence_chunks(monkeypatch):
    monkeypatch.setattr(stt, "_emit", lambda audio, hint, final=False: (("done" if final else "partial"), "en"))
    chunk = np.zeros(4000, dtype=np.float32)
    pattern = [True, True, False, False, False, True, True]  # 3 quiet chunks end the first utterance
    events = list(stt.transcribe_stream(iter([chunk] * len(pattern)), vad=_Vad(pattern)))
    finals = [e for e in events if e.is_final]
    assert len(finals) == 2  # the first at the third quiet chunk, the rest flushed at the end


def test_two_quiet_chunks_do_not_end_the_utterance(monkeypatch):
    monkeypatch.setattr(stt, "_emit", lambda audio, hint, final=False: ("x", "en"))
    chunk = np.zeros(4000, dtype=np.float32)
    pattern = [True, False, False, True, True]
    events = list(stt.transcribe_stream(iter([chunk] * len(pattern)), vad=_Vad(pattern)))
    assert sum(1 for e in events if e.is_final) == 1, "only the flush at the end of the stream"


def test_tamil_finals_use_the_accurate_model_directly(monkeypatch):
    used = []

    def fake_run(model, audio, hint):
        used.append(model)
        return "text", "ta", -0.1

    monkeypatch.setattr(stt, "_run_whisper", fake_run)
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(config, "LOCAL_WHISPER_MODEL_SIZE", "base")
    monkeypatch.setattr(config, "STT_ACCURATE_MODEL", "small")
    audio = np.zeros(16000, dtype=np.float32)
    stt._transcribe_local(audio, "ta", True)
    assert used == ["small"], "one pass, not base and then small"
    used.clear()
    stt._transcribe_local(audio, "ta", False)
    assert used == ["base"], "partials stay on the fast model"
    used.clear()
    stt._transcribe_local(audio, "en", True)
    assert used == ["base"], "English keeps the fast model"


def test_a_failing_accurate_model_falls_back_to_the_main_one(monkeypatch):
    used = []

    def fake_run(model, audio, hint):
        used.append(model)
        if model == "small":
            raise RuntimeError("out of memory")
        return "text", "ta", -0.1

    monkeypatch.setattr(stt, "_run_whisper", fake_run)
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(config, "LOCAL_WHISPER_MODEL_SIZE", "base")
    monkeypatch.setattr(config, "STT_ACCURATE_MODEL", "small")
    text, _ = stt._transcribe_local(np.zeros(16000, dtype=np.float32), "ta", True)
    assert text == "text" and used == ["small", "base"]


class _Result:
    def __init__(self, chunks, kind="ok"):
        self.kind = kind
        self.ms = 5
        self.data = {"chunks": chunks}


def test_a_repeated_question_does_not_ask_the_hub_again(monkeypatch):
    calls = []

    def fake_call(name, arguments, access, budget=None, confirmed=False):
        calls.append(arguments["query"])
        return _Result([{"content": "fibre " * 10, "score": 0.6}])

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    knowledge.clear_cache()
    first = knowledge.search("What fibre packages do you have?", None, budget=4, language="en")
    made = len(calls)
    second = knowledge.search("  what fibre PACKAGES do you have? ", None, budget=4, language="en")
    assert made >= 1 and len(calls) == made, "the second question came from the cache"
    assert second.kind == first.kind == "found" and second.chunks == first.chunks and second.ms == 0


def test_a_failed_search_is_never_cached(monkeypatch):
    results = iter([_Result([], kind="unavailable"), _Result([{"content": "ok " * 10, "score": 0.7}])])

    def fake_call(name, arguments, access, budget=None, confirmed=False):
        return next(results) if not arguments["query"].endswith(knowledge.RICH_SUFFIX) else _Result([], kind="unavailable")

    monkeypatch.setattr(knowledge.mcp_gateway, "call", fake_call)
    knowledge.clear_cache()
    assert knowledge.search("fibre", None, budget=4, language="en").kind == "unavailable"
    assert knowledge.search("fibre", None, budget=4, language="en").kind == "found"


def test_the_cache_forgets_old_entries(monkeypatch):
    import time as _time

    knowledge.clear_cache()
    knowledge._cache_put("a question", knowledge.Search("found", [{"content": "x", "score": 0.5}], 3))
    assert knowledge._cache_get("a question") is not None
    real = _time.monotonic
    monkeypatch.setattr(_time, "monotonic", lambda: real() + knowledge.CACHE_SECONDS + 1)
    assert knowledge._cache_get("a question") is None
