"""Voice friendly wording, for the spoken path only (the transcript keeps the
original text).

    for_speech("You owe Rs. 1,250.50 and have 42.5 GB left (80%)", "en")
    -> "You owe 1250 rupees and 50 cents and have 42.5 gigabytes left (80 percent)"
    shorten_for_voice(items, limit=3)   first few items plus an offer to read more
"""

import re
from typing import Optional

from .messages import short, text

_NUM = r"\d[\d,]*(?:\.\d+)?"
_MONEY_PREFIX = re.compile(r"(?<![A-Za-z])(?:Rs|LKR|රු|₨)\.?\s*(" + _NUM + r")", re.IGNORECASE)
_MONEY_SUFFIX = re.compile(r"(" + _NUM + r")\s*(?:LKR|Rs)\b\.?", re.IGNORECASE)
_THOUSANDS = re.compile(r"(?<![\d,.])\d{1,3}(?:,\d{3})+(?![\d,])")
_PERCENT = re.compile(r"(" + _NUM + r")\s*%")
_UNITS = re.compile(r"(?<![A-Za-z])(GB|MB|TB)(?![A-Za-z])")
_MARKUP = re.compile(r"[*_`#]+")
_SPACES = re.compile(r"[ \t]{2,}")

RUPEES = {"en": ("{n} rupees", " and {c} cents"), "si": ("රුපියල් {n}", " යි ශත {c}"), "ta": ("ரூபாய் {n}", " மற்றும் {c} சதம்")}
PERCENT = {"en": "{n} percent", "si": "සියයට {n}", "ta": "{n} சதவீதம்"}
UNITS = {
    "en": {"GB": "gigabytes", "MB": "megabytes", "TB": "terabytes"},
    "si": {"GB": "ගිගාබයිට්", "MB": "මෙගාබයිට්", "TB": "ටෙරාබයිට්"},
    "ta": {"GB": "ஜிகாபைட்", "MB": "மெகாபைட்", "TB": "டெராபைட்"},
}


def _money_words(raw: str, lang: str) -> str:
    cleaned = raw.replace(",", "")
    whole, _, fraction = cleaned.partition(".")
    whole = whole or "0"
    cents = (fraction + "00")[:2] if fraction else ""
    head, tail = RUPEES[lang]
    words = head.format(n=whole)
    if cents and cents != "00":
        words += tail.format(c=int(cents))
    return words


def for_speech(text_in: str, language: Optional[str] = None) -> str:
    """The text rewritten so a speech engine reads it well: amounts in words,
    no thousands commas, GB and % spelled out, markdown marks removed.
    Never raises; anything unexpected returns the input."""
    if not isinstance(text_in, str) or not text_in:
        return text_in if isinstance(text_in, str) else ""
    lang = short(language)
    try:
        out = _MONEY_PREFIX.sub(lambda m: _money_words(m.group(1), lang), text_in)
        out = _MONEY_SUFFIX.sub(lambda m: _money_words(m.group(1), lang), out)
        out = _PERCENT.sub(lambda m: PERCENT[lang].format(n=m.group(1).replace(",", "")), out)
        out = _THOUSANDS.sub(lambda m: m.group(0).replace(",", ""), out)
        out = _UNITS.sub(lambda m: UNITS[lang][m.group(1)], out)
        out = _MARKUP.sub("", out)
        return _SPACES.sub(" ", out).strip()
    except Exception:  # noqa: BLE001
        return text_in


def shorten_for_voice(items, limit: int = 3, language: Optional[str] = None) -> str:
    """A spoken summary of a list: the first `limit` items, then an offer to
    read the rest. A short list is just joined."""
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    limit = max(int(limit), 1)
    if len(values) <= limit:
        return _join(values, language)
    rest = len(values) - limit
    return _join(values[:limit], language) + ". " + text("tool_agent.more_offer", language, count=rest)


def _join(values: list, language: Optional[str]) -> str:
    if len(values) <= 1:
        return "".join(values)
    if short(language) == "en":
        return ", ".join(values[:-1]) + " and " + values[-1]
    return ", ".join(values)
