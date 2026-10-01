"""Local speech to text: when the larger model is asked to recheck a
transcript, that the noise filter is off by default, and that partial
transcripts never pay for a second pass. Fake models stand in for Whisper,
so no model files or audio are needed."""

from types import SimpleNamespace

import numpy as np
import pytest

import config
import stt


class FakeModel:
    def __init__(self, text, logprob):
        self.text, self.logprob, self.calls = text, logprob, 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        segments = [SimpleNamespace(text=f" {self.text}", avg_logprob=self.logprob)] if self.text else []
        return segments, SimpleNamespace(language="en")


@pytest.fixture
def models(monkeypatch):
    main, accurate = FakeModel("what is the price", -0.8), FakeModel("what is the price of the combo", -0.1)
    by_size = {"base": main, "small": accurate}
    monkeypatch.setattr(stt, "_get_local_model", lambda size: by_size[size])
    monkeypatch.setattr(config, "LOCAL_WHISPER_MODEL_SIZE", "base")
    monkeypatch.setattr(config, "STT_ACCURATE_MODEL", "small")
    monkeypatch.setattr(config, "STT_CONFIDENCE_FLOOR", -0.35)
    return SimpleNamespace(main=main, accurate=accurate, by_size=by_size)


AUDIO = np.zeros(16000, dtype=np.float32)


def test_low_confidence_final_is_rechecked_by_the_larger_model(models):
    text, lang = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "what is the price of the combo" and lang == "en"
    assert models.main.calls == 1 and models.accurate.calls == 1


def test_confident_final_is_not_rechecked(models):
    models.main.logprob = -0.1
    text, _ = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "what is the price" and models.accurate.calls == 0


def test_partial_transcripts_never_use_the_larger_model(models):
    text, _ = stt._transcribe_local(AUDIO, "en", final=False)
    assert text == "what is the price" and models.accurate.calls == 0


def test_recheck_is_off_when_no_larger_model_is_configured(models, monkeypatch):
    monkeypatch.setattr(config, "STT_ACCURATE_MODEL", "")
    text, _ = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "what is the price" and models.accurate.calls == 0


def test_failed_recheck_keeps_the_first_transcript(models):
    def broken(audio, **kwargs):
        raise RuntimeError("model file missing")

    models.accurate.transcribe = broken
    text, _ = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "what is the price"


def test_empty_recheck_keeps_the_first_transcript(models):
    models.accurate.text = ""
    text, _ = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "what is the price"


def test_nothing_heard_is_not_rechecked(models):
    models.main.text = ""
    text, _ = stt._transcribe_local(AUDIO, "en", final=True)
    assert text == "" and models.accurate.calls == 0


def test_whisper_gets_hotwords_and_beam_search(models, monkeypatch):
    monkeypatch.setattr(config, "STT_HOTWORDS", "SLT Mobitel")
    monkeypatch.setattr(config, "STT_BEAM_SIZE", 5)
    stt._transcribe_local(AUDIO, "en", final=False)
    assert models.main.kwargs["hotwords"] == "SLT Mobitel"
    assert models.main.kwargs["beam_size"] == 5
    assert models.main.kwargs["language"] == "en"


def test_noise_filter_is_off_by_default_and_skipped(models, monkeypatch):
    assert config.STT_DENOISE is False

    def must_not_run(audio, *args, **kwargs):
        raise AssertionError("denoise_audio ran although STT_DENOISE is off")

    monkeypatch.setattr(stt, "denoise_audio", must_not_run)
    text, _ = stt._emit(AUDIO, "en", final=False)
    assert text == "what is the price"


def test_noise_filter_runs_when_switched_on(models, monkeypatch):
    monkeypatch.setattr(config, "STT_DENOISE", True)
    seen = []
    monkeypatch.setattr(stt, "denoise_audio", lambda audio, *a, **k: seen.append(True) or audio)
    stt._emit(AUDIO, "en", final=False)
    assert seen == [True]


def test_final_event_of_a_stream_asks_for_the_recheck(models, monkeypatch):
    class AlwaysSpeech:
        def is_speech(self, chunk):
            return True

    chunks = [np.ones(8000, dtype=np.float32)] * 3
    events = list(stt.transcribe_stream(iter(chunks), "en", AlwaysSpeech()))
    assert events and events[-1].is_final
    assert events[-1].text == "what is the price of the combo"
    assert not any(e.is_final for e in events[:-1])
    assert all(e.text == "what is the price" for e in events[:-1])


def test_whisper_gets_the_speech_filter(models, monkeypatch):
    monkeypatch.setattr(config, "STT_VAD_FILTER", True)
    stt._transcribe_local(AUDIO, "en", final=False)
    assert models.main.kwargs["vad_filter"] is True


def test_defaults_are_the_measured_ones():
    assert config.STT_DENOISE is False
    assert config.STT_VAD_FILTER is True
    assert config.STT_CONFIDENCE_FLOOR == -0.40
    assert config.STT_BEAM_SIZE == 5


def test_failed_cloud_speech_is_skipped_for_a_while(monkeypatch):
    calls = []

    class BrokenClient:
        def __init__(self):
            calls.append(1)
            raise RuntimeError("403 Cloud Speech-to-Text API has not been used in project 1 before")

    import sys
    import types

    fake_speech = types.SimpleNamespace(SpeechClient=BrokenClient)
    fake_cloud = types.SimpleNamespace(speech=fake_speech)
    monkeypatch.setitem(sys.modules, "google.cloud", fake_cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.speech", fake_speech)
    monkeypatch.setattr(stt, "_cloud_retry_at", 0.0)

    assert stt._transcribe_cloud_google(AUDIO, "si") is None
    assert stt._transcribe_cloud_google(AUDIO, "si") is None
    assert stt._transcribe_cloud_google(AUDIO, "ta") is None
    assert len(calls) == 1  # the second and third calls never reached the cloud
