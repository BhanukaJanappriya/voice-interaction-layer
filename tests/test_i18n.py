"""Tests for the frontend and backend translation files and backend i18n.t."""

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import i18n  # noqa: E402

LANGS = ["en", "si", "ta"]
PLACEHOLDER = re.compile(r"\{(\w+)\}")

STEP1 = "title name_label name_placeholder phone_label phone_placeholder terms_prefix terms_link and privacy_link back next".split()
STEP2 = "title sms_option email_option send_otp change_number no_contact".split()
STEP3 = "title subtitle digit_aria countdown verify resend expired".split()
ERRORS = "generic phone_format rate_limited invalid expired locked attempts_left cooldown unavailable network".split()
CONTRACT = (
    [f"verify.step1.{k}" for k in STEP1]
    + [f"verify.step2.{k}" for k in STEP2]
    + [f"verify.step3.{k}" for k in STEP3]
    + [f"verify.errors.{k}" for k in ERRORS]
    + ["verify.success.greeting", "common.close", "access.customer_service_blocked",
       "llm.safety_blocked", "otp.sms_body", "otp.email_subject", "otp.email_body"]
)
BACKEND_REQUIRED = [
    "access.customer_service_blocked", "llm.safety_blocked", "otp.sms_body", "otp.email_subject",
    "otp.email_body", "verify.success.greeting",
] + [f"verify.errors.{k}" for k in ERRORS]


def load(side, lang):
    path = ROOT / side / "i18n" / f"{lang}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def entries(side, lang):
    return {k: v for k, v in load(side, lang).items() if k != "_meta"}


@pytest.mark.parametrize("side", ["frontend", "backend"])
def test_key_parity(side):
    base = set(entries(side, "en"))
    for lang in LANGS:
        assert set(entries(side, lang)) == base, lang


@pytest.mark.parametrize("side", ["frontend", "backend"])
def test_placeholder_parity_and_no_empty(side):
    base = entries(side, "en")
    for lang in LANGS:
        for key, value in entries(side, lang).items():
            assert isinstance(value, str) and value.strip(), f"{lang} {key} empty"
            assert set(PLACEHOLDER.findall(value)) == set(PLACEHOLDER.findall(base[key])), f"{lang} {key}"


@pytest.mark.parametrize("side", ["frontend", "backend"])
def test_no_forbidden_dashes(side):
    for lang in LANGS:
        for key, value in entries(side, lang).items():
            assert "--" not in value and "—" not in value and "–" not in value, f"{lang} {key}"


def test_meta_flags_machine_translations():
    for side in ("frontend", "backend"):
        for lang in ("si", "ta"):
            meta = load(side, lang)["_meta"]
            assert meta["machine_written"] is True and meta["needs_native_review"] is True


def test_frontend_contract_fully_covered():
    for lang in LANGS:
        missing = [k for k in CONTRACT if k not in entries("frontend", lang)]
        assert not missing, (lang, missing)


def test_backend_keys_covered_and_subset_of_frontend_contract():
    for lang in LANGS:
        have = entries("backend", lang)
        assert not [k for k in BACKEND_REQUIRED if k not in have]


def test_blocked_message_contents():
    for lang in LANGS:
        text = entries("backend", lang)["access.customer_service_blocked"]
        assert {"hotline", "sms", "prepaid"} <= set(PLACEHOLDER.findall(text))
    rendered = i18n.t("access.customer_service_blocked", "en", hotline="1212", sms="0113121212", prepaid="1222")
    assert "1212" in rendered and "0113121212" in rendered and "1222" in rendered
    assert "{" not in rendered


def test_otp_sms_has_code_and_minutes_and_is_short():
    for lang in LANGS:
        text = entries("backend", lang)["otp.sms_body"]
        assert {"code", "minutes"} == set(PLACEHOLDER.findall(text))
        assert "SLT-MOBITEL" in text
        assert len(text) < 160


def test_brand_stays_latin():
    for lang in LANGS:
        assert "SLT-MOBITEL" in entries("backend", lang)["otp.email_subject"]


def test_t_basic_and_language_selection():
    assert "123456" in i18n.t("otp.sms_body", "en", code="123456", minutes=2)
    si = i18n.t("verify.success.greeting", "si", name="Nimal")
    assert "Nimal" in si and si != i18n.t("verify.success.greeting", "en", name="Nimal")
    assert i18n.t("verify.success.greeting", "ta", name="Kumar") != i18n.t("verify.success.greeting", "en", name="Kumar")


def test_t_fallbacks():
    en = i18n.t("llm.safety_blocked", "en")
    assert i18n.t("llm.safety_blocked", "fr") == en
    assert i18n.t("llm.safety_blocked", None) == en
    assert i18n.t("llm.safety_blocked", "") == en
    assert i18n.t("llm.safety_blocked", "si-LK") == i18n.t("llm.safety_blocked", "si")
    assert i18n.t("no.such.key", "si") == "no.such.key"


def test_t_falls_back_to_en_for_key_missing_in_language(monkeypatch):
    real = i18n._load

    def fake(lang):
        return {} if lang == "si" else real(lang)

    monkeypatch.setattr(i18n, "_load", fake)
    assert i18n.t("llm.safety_blocked", "si") == real("en")["llm.safety_blocked"]


def test_safe_format_never_raises():
    assert "{code}" in i18n.t("otp.sms_body", "en", minutes=2)
    assert "{minutes}" in i18n.t("otp.sms_body", "en")
    assert "9" in i18n.t("otp.sms_body", "en", code=9, minutes=2, unused="x")
    assert "{name}" in i18n.t("verify.success.greeting", "en", name=None)


def test_available_languages_and_cache():
    assert i18n.available_languages() == ["en", "si", "ta"]
    assert i18n._load("en") is i18n._load("en")


def test_self_check_passes():
    from i18n import __main__ as check

    assert check.check() == []


def test_frontend_script_exposes_api():
    source = (ROOT / "frontend" / "i18n" / "i18n.js").read_text(encoding="utf-8")
    for needle in ("window.SltI18n", "setLanguage", "language:", "data-i18n-aria", "document.documentElement.lang"):
        assert needle in source
