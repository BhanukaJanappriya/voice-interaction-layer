import json
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from voice_text import normalize  # noqa: E402
from voice_text.normalizer import en_int, en_ordinal, en_year  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")

# (input, exact expected) English
EN_EXACT = [
    ("I have 5 apples", "I have five apples"),
    ("Total 1,234,567 items", "Total one million two hundred thirty four thousand five hundred sixty seven items"),
    ("It is 3.5 now", "It is three point five now"),
    ("Pay Rs. 4,590.50 today", "Pay four thousand five hundred ninety rupees and fifty cents today"),
    ("Pay Rs 1500 today", "Pay one thousand five hundred rupees today"),
    ("Pay LKR 250", "Pay two hundred fifty rupees"),
    ("Pay Rs. 1,500/= now", "Pay one thousand five hundred rupees now"),
    ("Pay Rs. 1", "Pay one rupee"),
    ("Pay 500 LKR", "Pay five hundred rupees"),
    ("Due 25 October 2026.", "Due twenty fifth of October twenty twenty six."),
    ("Due 25/10/2026.", "Due twenty fifth of October twenty twenty six."),
    ("Due 2026-10-25.", "Due twenty fifth of October twenty twenty six."),
    ("October 25, 2026", "twenty fifth of October twenty twenty six"),
    ("on 1st of March", "on first of March"),
    ("open from 8.30 a.m.", "open from eight thirty A M."),
    ("open at 5.05 p.m. daily", "open at five oh five P M daily"),
    ("at 9 am", "at nine am"),
    ("closes 17:45", "closes seventeen forty five"),
    ("opens at 8.00 am", "opens at eight o'clock A M"),
    ("Call 0112123456 now", "Call zero one one two one two three four five six now"),
    ("Call 011 212 3456 now", "Call zero one one, two one two, three four five six now"),
    ("Call +94 11 212 3456 now", "Call plus nine four, one one, two one two, three four five six now"),
    ("Call 0771234567.", "Call zero seven seven one two three four five six seven."),
    ("Dial 1212 or 1222", "Dial one two one two or one two two two"),
    ("25 GB data", "twenty five gigabytes data"),
    ("1 GB data", "one gigabyte data"),
    ("100 Mbps speed", "one hundred megabits per second speed"),
    ("2 Gbps", "two gigabits per second"),
    ("512 kbps", "five hundred twelve kilobits per second"),
    ("1.5 GB", "one point five gigabytes"),
    ("50MB left", "fifty megabytes left"),
    ("50% off", "fifty percent off"),
    ("SLT is on LTE and 4G", "S L T is on L T E and four G"),
    ("PEOTV VAS SMS OTP FTTH IDD", "P E O T V V A S S M S O T P F T T H I D D"),
    ("Your OTP 123456 is valid", "Your O T P one two three four five six is valid"),
    ("in 2026 we grew", "in twenty twenty six we grew"),
    ("in 2005", "in two thousand five"),
    ("account 12345678", "account one two three four five six seven eight"),
    ("version 3.5.1", "version 3.5.1"),
    ("Hello world.", "Hello world."),
    ("", ""),
]

# (lang, input, substrings that must be present, substrings that must be absent)
IND = [
    ("si", "ඔබගේ Fibre බිල රු. 4,590.50 කි", ["ෆයිබර්", "රුපියල් 4590", "සත 50"], ["Fibre", "රු.", ","]),
    ("si", "රු. 1,500/= ක්", ["රුපියල් 1500"], ["/=", "රු."]),
    ("si", "Rs 250", ["රුපියල් 250"], ["Rs"]),
    ("si", "LKR 99.00", ["රුපියල් 99"], ["LKR", "සත"]),
    ("si", "0112 123 456 අමතන්න", ["බිංදුව එක එක දෙක, එක දෙක තුන, හතර පහ හය"], ["0112"]),
    ("si", "+94 11 212 3456", ["ප්ලස් නවය හතර, එක එක, දෙක එක දෙක"], ["+94", "3456"]),
    ("si", "1212 අමතන්න", ["එක දෙක එක දෙක"], ["1212"]),
    ("si", "25 GB සහ 100 Mbps", ["25 ගිගාබයිට්", "100 මෙගාබිට් පර් සෙකන්ඩ්"], ["GB", "Mbps"]),
    ("si", "50% ක්", ["සියයට 50"], ["%"]),
    ("si", "SLT PEOTV LTE", ["එස් එල් ටී", "පී ඊ ඕ ටී වී", "එල් ටී ඊ"], ["SLT", "PEOTV", "LTE"]),
    ("si", "Wi-Fi router එක", ["වයිෆයි", "රවුටර්"], ["router", "Wi-Fi"]),
    ("si", "broadband package reload", ["බ්‍රෝඩ්බෑන්ඩ්", "පැකේජ්", "රීලෝඩ්"], ["broadband"]),
    ("si", "data bundle", ["ඩේටා බණ්ඩල්"], ["data"]),
    ("si", "උදේ 8.30 සිට", ["8:30"], ["8.30"]),
    ("si", "8.30 a.m.", ["8:30 පෙරවරු"], ["a.m"]),
    ("si", "25 October 2026", ["2026 ඔක්තෝබර් 25"], ["October"]),
    ("si", "25/10/2026", ["2026 ඔක්තෝබර් 25"], ["/"]),
    ("si", "ඔක්තෝබර් 25 වන දිනට", ["ඔක්තෝබර් 25 වන"], []),
    ("si", "3.5 GB", ["3 දශම පහ ගිගාබයිට්"], ["3.5"]),
    ("si", "OTP 123456", ["ඕ ටී පී එක දෙක තුන හතර පහ හය"], ["123456"]),
    ("si", "කොළඹ, මහනුවර සහ ගාල්ල", ["කොළඹ, මහනුවර සහ ගාල්ල"], []),
    ("ta", "உங்கள் Fibre கட்டணம் ரூ. 4,590.50", ["ஃபைபர்", "4590 ரூபாய்", "50 சதம்"], ["Fibre", "ரூ."]),
    ("ta", "ரூ. 250", ["250 ரூபாய்"], ["ரூ."]),
    ("ta", "Rs. 99.5", ["99 ரூபாய் 50 சதம்"], ["Rs"]),
    ("ta", "0112 123 456 அழைக்கவும்", ["பூஜ்ஜியம் ஒன்று ஒன்று இரண்டு"], ["0112"]),
    ("ta", "+94 11 212 3456", ["பிளஸ் ஒன்பது நான்கு"], ["+94"]),
    ("ta", "1222 ஐ அழைக்கவும்", ["ஒன்று இரண்டு இரண்டு இரண்டு"], ["1222"]),
    ("ta", "25 GB மற்றும் 100 Mbps", ["25 ஜிகாபைட்", "100 மெகாபிட் பெர் செகண்ட்"], ["GB", "Mbps"]),
    ("ta", "50% தள்ளுபடி", ["50 சதவீதம்"], ["%"]),
    ("ta", "SLT PEOTV IDD", ["எஸ் எல் டி", "பி ஈ ஓ டி வி", "ஐ டி டி"], ["SLT", "IDD"]),
    ("ta", "WiFi router package", ["வைஃபை", "ரவுட்டர்", "பேக்கேஜ்"], ["router", "WiFi"]),
    ("ta", "broadband reload bill", ["பிராட்பேண்ட்", "ரீலோட்", "பில்"], ["bill"]),
    ("ta", "காலை 8.30 முதல்", ["8:30"], ["8.30"]),
    ("ta", "5.15 p.m.", ["5:15 பிற்பகல்"], ["p.m"]),
    ("ta", "25 October 2026", ["2026 அக்டோபர் 25"], ["October"]),
    ("ta", "2026-10-25", ["2026 அக்டோபர் 25"], ["-10-"]),
    ("ta", "1.5 GB", ["1 புள்ளி ஐந்து ஜிகாபைட்"], ["1.5"]),
    ("ta", "கொழும்பு, கண்டி, காலி", ["கொழும்பு, கண்டி, காலி"], []),
]


@pytest.mark.parametrize("text,expected", EN_EXACT)
def test_english_exact(text, expected):
    assert normalize(text, "en") == expected


@pytest.mark.parametrize("lang,text,present,absent", IND)
def test_si_ta_contains(lang, text, present, absent):
    out = normalize(text, lang)
    for p in present:
        assert p in out, out
    for a in absent:
        assert a not in out, out


def test_number_helpers():
    assert en_int(0) == "zero"
    assert en_int(13) == "thirteen"
    assert en_int(100) == "one hundred"
    assert en_int(101) == "one hundred one"
    assert en_int(1000) == "one thousand"
    assert en_int(1_000_001) == "one million one"
    assert en_ordinal(1) == "first" and en_ordinal(20) == "twentieth" and en_ordinal(25) == "twenty fifth"
    assert en_ordinal(12) == "twelfth" and en_ordinal(31) == "thirty first"
    assert en_year(2026) == "twenty twenty six" and en_year(1999) == "nineteen ninety nine"
    assert en_year(2000) == "two thousand" and en_year(1905) == "nineteen oh five"


def test_sentence_boundary_period_kept_after_pm():
    assert normalize("Open until 5.30 p.m. Call us.", "en") == "Open until five thirty P M. Call us."
    assert normalize("From 8.30 a.m. to 5.30 p.m.", "en") == "From eight thirty A M to five thirty P M."


def test_unknown_language_falls_back_to_english():
    assert normalize("5", "xx") == "five"
    assert normalize("5", None) == "five"
    assert normalize(None, "en") == ""


def test_untouched_when_nothing_to_do():
    for lang in ("en", "si", "ta"):
        assert normalize("Hello, how are you? Fine!", lang) == "Hello, how are you? Fine!"


def _eval_sentences():
    data = json.load(open(os.path.join(ROOT, "tools", "voice_eval", "sentences.json"), encoding="utf-8"))
    return [(lang, s) for lang, items in data.items() for s in items]


@pytest.mark.parametrize("lang,sentence", _eval_sentences())
def test_eval_sentences(lang, sentence):
    out = normalize(sentence, lang)
    assert normalize(out, lang) == out
    assert len(out) <= 8 * len(sentence) + 64
    # phone numbers and short codes carry no digits after reading
    assert "123 456" not in out and "1212" not in out
    if lang == "en":
        assert not re.search(r"[0-9]", out)
    else:
        assert "Fibre" not in out and "PEOTV" not in out and "Mbps" not in out


# === properties

SAMPLES = [t for tests in (EN_EXACT,) for t, _ in tests] + [t for _, t, _, _ in IND] + [s for _, s in _eval_sentences()]


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_idempotent_on_all_samples(lang):
    for text in SAMPLES:
        once = normalize(text, lang)
        assert normalize(once, lang) == once, (lang, text, once)


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_random_unicode_never_raises_and_idempotent(lang):
    rnd = random.Random(1234)
    alphabets = [
        "0123456789 .,:/+-%=",
        "abcXYZ GBMbpsRsLKR.am pm",
        "".join(chr(c) for c in range(0x0D80, 0x0DFF)),
        "".join(chr(c) for c in range(0x0B80, 0x0BFF)),
        "".join(chr(c) for c in range(0x20, 0x2FF)) + "‍\u0000�\U0001F600",
    ]
    for _ in range(400):
        alphabet = rnd.choice(alphabets) + "0123456789 ."
        text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 60)))
        out = normalize(text, lang)
        assert isinstance(out, str)
        assert normalize(out, lang) == out, (lang, repr(text), repr(out))
        assert len(out) <= 8 * len(text) + 64


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_phone_numbers_leave_no_digits(lang):
    rnd = random.Random(99)
    for _ in range(200):
        digits = "".join(rnd.choice("0123456789") for _ in range(9))
        style = rnd.choice(["0{}", "0{}", "+94{}", "+94 {}", "0094{}"])
        number = style.format(digits)
        if rnd.random() < 0.5 and not number.startswith("0094"):
            number = re.sub(r"(?<=\d)(?=(?:\d{3})+$)", " ", number) if not number.startswith("+") else number
        text = f"Call {number} now"
        out = normalize(text, lang)
        if re.fullmatch(r"(\+94|0094)\s?[0-9 ]{9,}|0[0-9 ]{9,}", number):
            digits_only = re.sub(r"\D", "", number)
            if len(digits_only) in (10, 11, 13):
                assert not re.search(r"[0-9]", out), (number, out)


def test_never_grows_unreasonably():
    text = "0112123456 " * 200
    out = normalize(text, "en")
    assert len(out) <= 8 * len(text) + 64
    text = "1" * 5000
    assert len(normalize(text, "en")) <= 8 * len(text) + 64


def test_lexicon_loads_and_has_all_languages():
    from voice_text.normalizer import _lexicon

    lex = _lexicon()
    for lang in ("en", "si", "ta"):
        assert len(lex[lang]["digits"]) == 10
        assert len(lex[lang]["months"]) == 12
