"""frontend/i18n/format.js: number, currency, date and duration formatting.

The module is a plain script that also loads in Node, so the tests run it there
(skipped when Node is not installed). Expected values come from the browser's
own Intl data; the tests check the properties that matter (grouping style,
digits, no throwing), not every glyph.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FORMAT_JS = ROOT / "frontend" / "i18n" / "format.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run(script):
    code = "const F = require(%s);\nconsole.log(JSON.stringify((function(){ %s })()));" % (json.dumps(str(FORMAT_JS)), script)
    done = subprocess.run([NODE, "-e", code], capture_output=True, timeout=60, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr[-500:]
    return json.loads(done.stdout)


def test_number_grouping_follows_the_language():
    out = run("return {en: F.forLanguage('en').number(1234567.5), si: F.forLanguage('si').number(1234567.5), ta: F.forLanguage('ta').number(1234567.5)};")
    assert out["en"] == "1,234,567.5"
    assert out["si"] == "1,234,567.5"
    assert out["ta"] == "12,34,567.5"  # Sri Lankan Tamil uses lakh grouping in the ICU data


def test_currency_uses_lkr_and_the_local_symbol():
    out = run("return {en: F.forLanguage('en').currency(1250), si: F.forLanguage('si').currency(1250), ta: F.forLanguage('ta').currency(1250)};")
    for text in out.values():
        assert "1,250.00" in text
    assert "LKR" in out["en"] or "Rs" in out["en"]
    assert out["si"].startswith("රු")  # the Sinhala rupee sign
    assert out["ta"] != out["en"]


def test_percent_and_integer():
    out = run("const f = F.forLanguage('si'); return [f.percent(0.256), f.integer(7.9), f.number('12'), f.number(null)];")
    assert out[0] == "26%" and out[1] == "8" and out[2] == "12" and out[3] == ""


def test_date_and_time_are_localised():
    out = run(
        "const d = new Date(2026, 9, 15, 14, 5);"
        "return {si: F.forLanguage('si').date(d), ta: F.forLanguage('ta').date(d), en: F.forLanguage('en').date(d), "
        "tt: F.forLanguage('ta').time(d), dt: F.forLanguage('si').dateTime(d)};"
    )
    assert "ඔක්තෝබර්" in out["si"]  # Sinhala for October
    assert "அக்டோபர்" in out["ta"]  # Tamil for October
    assert "October" in out["en"] and "2026" in out["en"]
    assert "14:05" in out["tt"] or "2:05" in out["tt"]
    assert "2026" in out["dt"]


def test_duration_for_countdowns():
    out = run("const f = F.forLanguage('ta'); return [f.duration(105), f.duration(5), f.duration(0), f.duration(3725), f.duration(-4), f.duration('x'), f.duration(59.9)];")
    assert out == ["1:45", "0:05", "0:00", "1:02:05", "0:00", "", "0:59"]


def test_unknown_language_falls_back_to_english_and_never_throws():
    out = run(
        "const f = F.forLanguage('xx'); return [f.language(), f.number(1000), f.date('not a date'), f.time({}), "
        "f.currency('abc'), f.percent(undefined), f.duration(NaN), f.number(Infinity)];"
    )
    assert out[0] == "en" and out[1] == "1,000"
    assert out[2] == "" and out[4] == "abc" and out[5] == "" and out[6] == "" and out[7] == "Infinity"


def test_works_without_intl():
    out = run(
        "const saved = Intl; globalThis.Intl = undefined;"
        "try { F._reset(); const f = F.forLanguage('si'); return [f.number(1500.5), f.currency(3), f.date(new Date(2026, 0, 2)).length > 0, f.duration(65)]; }"
        "finally { globalThis.Intl = saved; F._reset(); }"
    )
    assert out[0] == "1500.5" and out[1] == "3" and out[2] is True and out[3] == "1:05"


def test_page_language_is_read_from_the_i18n_helper():
    out = run(
        "globalThis.SltI18n = {language: function(){ return 'ta'; }};"
        "const a = F.language(); globalThis.SltI18n = {language: function(){ throw new Error('x'); }};"
        "return [a, F.language(), F.number(5)];"
    )
    assert out == ["ta", "en", "5"]
