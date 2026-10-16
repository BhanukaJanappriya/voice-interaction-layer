"""Sinhala speech to text goes to the Sinhala model when it is on disk, only for final transcripts,
and falls back to the default model on any problem. The model itself is faked."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import config  # noqa: E402
import stt  # noqa: E402

AUDIO = np.zeros(16000, dtype=np.float32)


@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STT_SINHALA_MODEL_DIR", tmp_path)
    return tmp_path


def make_ready(directory):
    (directory / "model.bin").write_bytes(b"x")


def test_not_ready_without_the_model_file(model_dir):
    assert stt.sinhala_model_ready() is False
    make_ready(model_dir)
    assert stt.sinhala_model_ready() is True


def test_sinhala_finals_use_the_sinhala_model_and_partials_are_skipped(model_dir, monkeypatch):
    make_ready(model_dir)
    seen = []
    monkeypatch.setattr(stt, "_run_sinhala", lambda audio: seen.append("si") or ("මගේ බිල කීයද", -0.2))
    monkeypatch.setattr(stt, "_run_whisper", lambda *a, **k: seen.append("whisper") or ("garbage", "si", -0.5))
    assert stt._transcribe_local(AUDIO, "si", True) == ("මගේ බිල කීයද", "si")
    assert stt._transcribe_local(AUDIO, "si", False) == ("", "si")
    assert seen == ["si"], "the stock model is not asked at all, and a partial costs nothing"


def test_other_languages_never_touch_the_sinhala_model(model_dir, monkeypatch):
    make_ready(model_dir)
    monkeypatch.setattr(stt, "_run_sinhala", lambda audio: pytest.fail("the Sinhala model was used"))
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(stt, "_run_whisper", lambda model, audio, hint: ("hello", "en", -0.1))
    assert stt._transcribe_local(AUDIO, "en", True) == ("hello", "en")
    assert stt._transcribe_local(AUDIO, "ta", False)[0] == "hello"


def test_a_failing_sinhala_model_falls_back_to_the_default(model_dir, monkeypatch):
    make_ready(model_dir)

    def boom(audio):
        raise RuntimeError("cannot load")

    monkeypatch.setattr(stt, "_run_sinhala", boom)
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(stt, "_run_whisper", lambda model, audio, hint: ("fallback text", "si", -0.1))
    assert stt._transcribe_local(AUDIO, "si", True) == ("fallback text", "si")


def test_an_empty_sinhala_result_falls_back_too(model_dir, monkeypatch):
    make_ready(model_dir)
    monkeypatch.setattr(stt, "_run_sinhala", lambda audio: ("", -9.0))
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(stt, "_run_whisper", lambda model, audio, hint: ("fallback text", "si", -0.1))
    assert stt._transcribe_local(AUDIO, "si", True)[0] == "fallback text"


def test_without_the_model_sinhala_behaves_as_before(model_dir, monkeypatch):
    monkeypatch.setattr(stt, "_get_local_model", lambda size: size)
    monkeypatch.setattr(stt, "_run_whisper", lambda model, audio, hint: ("old path", "si", -0.1))
    assert stt._transcribe_local(AUDIO, "si", False)[0] == "old path"


def test_the_download_is_background_only_and_can_be_switched_off(model_dir, monkeypatch):
    started = []
    monkeypatch.setattr(stt.threading, "Thread", lambda target, daemon: type("T", (), {"start": lambda self: started.append(1)})())
    monkeypatch.setattr(config, "STT_SINHALA_AUTO_DOWNLOAD", False)
    stt._fetch_sinhala_model()
    assert started == []
    monkeypatch.setattr(config, "STT_SINHALA_AUTO_DOWNLOAD", True)
    monkeypatch.setattr(stt, "_sinhala_fetching", False)
    stt._fetch_sinhala_model()
    assert started == [1]
    monkeypatch.setattr(stt, "_sinhala_fetching", False)


def test_the_decoding_settings_avoid_the_slow_retries(model_dir, monkeypatch):
    """Default settings took 16 to 25 s on a 12 s sentence; these must stay greedy with no fallback."""
    captured = {}

    class FakeModel:
        def transcribe(self, audio, **kw):
            captured.update(kw)
            return iter([]), None

    monkeypatch.setattr(stt, "_get_sinhala_model", lambda: FakeModel())
    stt._run_sinhala(AUDIO)
    assert captured["beam_size"] == 1 and captured["temperature"] == 0.0
    assert captured["condition_on_previous_text"] is False and captured["vad_filter"] is True
    assert captured["language"] == "si"
