"""Handover state and the reconnecting notice."""

import sys
import types

import pytest

from voice_router.handover import build_handover, make_notice
from voice_router.types import HandoverState


class FakeAccess:
    tier = "verified"


def test_handover_keeps_language_voice_and_access_identity():
    access = FakeAccess()
    state = build_handover(language="si", voice_style="male", access=access, turns=[("user", "hello"), ("assistant", "hi")])
    assert isinstance(state, HandoverState)
    assert state.language == "si"
    assert state.voice_style == "male"
    assert state.access is access
    assert "hello" in state.summary


def test_handover_defaults_and_summary_bound():
    state = build_handover(language=None, voice_style=None, turns=["x" * 5000])
    assert state.language == "en" and state.voice_style == "female_natural"
    assert len(state.summary) <= 600
    assert state.pending_confirmation is None


def test_handover_carries_pending_confirmation_without_token(monkeypatch):
    access = FakeAccess()
    seen = []
    fake = types.ModuleType("confirmations")

    def pending_for(a):
        seen.append(a)
        return {"id": "SECRET-TOKEN", "tool": "purchase_extra_gb", "title": "Buy 5 GB", "lines": [{"label": "Price", "value": "300"}], "expires_in": 42, "state": "pending"}

    fake.pending_for = pending_for
    monkeypatch.setitem(sys.modules, "confirmations", fake)
    state = build_handover(language="en", access=access)
    assert seen == [access]
    assert state.pending_confirmation["title"] == "Buy 5 GB"
    assert state.pending_confirmation["lines"] == [{"label": "Price", "value": "300"}]
    assert "SECRET-TOKEN" not in repr(state.pending_confirmation)
    assert "id" not in state.pending_confirmation


def test_no_pending_when_store_has_none_or_fails(monkeypatch):
    fake = types.ModuleType("confirmations")
    fake.pending_for = lambda a: None
    monkeypatch.setitem(sys.modules, "confirmations", fake)
    assert build_handover(access=FakeAccess()).pending_confirmation is None

    def boom(a):
        raise RuntimeError("x")

    fake.pending_for = boom
    assert build_handover(access=FakeAccess()).pending_confirmation is None
    assert build_handover(access=None).pending_confirmation is None


def test_explicit_pending_is_scrubbed_of_token():
    state = build_handover(pending_confirmation={"id": "T", "tool": "log_fault", "title": "Fault", "lines": [], "expires_in": 10})
    assert "id" not in state.pending_confirmation and state.pending_confirmation["tool"] == "log_fault"


def test_notice_fallback_is_english_when_key_missing():
    for lang in ("en", "si", "ta", None, "xx"):
        text = make_notice(lang)
        assert isinstance(text, str) and text


@pytest.mark.parametrize("lang, expected", [("en", "One moment"), ("si", "SI-TEXT"), ("ta", "TA-TEXT")])
def test_notice_uses_i18n_key(monkeypatch, lang, expected):
    fake = types.ModuleType("i18n")
    table = {"en": "One moment", "si": "SI-TEXT", "ta": "TA-TEXT"}

    def t(key, language=None, **kw):
        assert key == "voice.reconnecting"
        return table.get(language, key)

    fake.t = t
    monkeypatch.setitem(sys.modules, "i18n", fake)
    assert make_notice(lang) == expected


def test_notice_never_raises(monkeypatch):
    fake = types.ModuleType("i18n")

    def t(*a, **k):
        raise RuntimeError("boom")

    fake.t = t
    monkeypatch.setitem(sys.modules, "i18n", fake)
    assert make_notice("si") == "One moment, reconnecting."
    monkeypatch.setitem(sys.modules, "i18n", None)  # import fails
    assert make_notice("ta") == "One moment, reconnecting."


def test_notice_key_returned_as_is_means_missing(monkeypatch):
    fake = types.ModuleType("i18n")
    fake.t = lambda key, language=None, **k: key
    monkeypatch.setitem(sys.modules, "i18n", fake)
    assert make_notice("si") == "One moment, reconnecting."
