"""Keeps a model from adding contact details that did not come from the hub.

A model that is told to answer only from the hub still tends to end with
"call our hotline 1717 or visit www.example.lk" taken from its own memory.
Those are SLT facts too, and the official ones are 1212, the SMS number and
1222. filter_stream() reads the reply sentence by sentence and drops a
sentence that carries a web address, an e-mail address, a long telephone
number or a number named as a hotline unless that exact value appears in the
hub text given for the turn or is one of the official numbers. If anything was
dropped, the official dial options are added at the end, so the customer is
never left without a way forward.

Prices and other figures are not touched: a number is checked only when it is
written as a contact detail.
"""

import re
from typing import Iterable, Iterator, Optional

_URL = re.compile(r"(?i)\b(?:https?://\S+|www\.\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:lk|com|net|org|gov|info)\b\S*)")
_EMAIL = re.compile(r"(?i)\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LONG_PHONE = re.compile(r"(?<!\d)(?:\+94|0094|0)[\s-]?\d(?:[\s-]?\d){7,9}(?!\d)")
# A short code named as a number to call, in the three languages.
_CALL_WORD = (
    r"(?:hotline|hot line|dial|call|ring|contact|sms|text|helpline|number"
    r"|ඇමතුම|අමතන්න|අමතන්|අංකය|දුරකථන|ඩයල්"
    r"|அழை|அழைக்க|ஹாட்லைன்|எண்|தொடர்பு|டயல்)"
)
_SHORT_CODE = re.compile(rf"(?i){_CALL_WORD}\W{{0,24}}?(\d{{3,5}})(?!\d)")
_BOUNDARY = re.compile(r"(?<=[.!?।\n])\s+|\n+")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _allowed(values: Iterable[str], context: str, official: Iterable[str]) -> "tuple[str, set]":
    text = (context or "").lower()
    digits_in_context = _digits(context or "")
    keep = {_digits(v) for v in official if _digits(v)}
    return text + "\n" + digits_in_context, keep


def _offending(sentence: str, haystack: str, official_digits: set) -> bool:
    for pattern in (_URL, _EMAIL):
        for found in pattern.findall(sentence):
            token = found.rstrip(".,;:)").lower()
            if token and token not in haystack:
                return True
    for found in _LONG_PHONE.findall(sentence):
        digits = _digits(found)
        if digits not in official_digits and digits.lstrip("0") not in haystack.replace(" ", ""):
            return True
    for code in _SHORT_CODE.findall(sentence):
        if code not in official_digits and code not in haystack:
            return True
    return False


def clean_text(text: str, context: str, official: Iterable[str]) -> "tuple[str, bool]":
    """(the text without offending sentences, whether anything was dropped)."""
    haystack, official_digits = _allowed((), context, official)
    kept, dropped = [], False
    for part in re.split(r"(?<=[.!?।])\s+", text):
        if part and _offending(part, haystack, official_digits):
            dropped = True
            continue
        if part:
            kept.append(part)
    return " ".join(kept), dropped


def filter_stream(
    pieces: Iterator[str], context: str, official: Iterable[str], dial_line: Optional[str] = None
) -> Iterator[str]:
    """Yields the reply a sentence at a time with offending sentences removed."""
    haystack, official_digits = _allowed((), context, official)
    buffer = ""
    dropped = False
    emitted_any = False

    def flush(sentence: str) -> Optional[str]:
        nonlocal dropped
        if not sentence.strip():
            return sentence if sentence else None
        if _offending(sentence, haystack, official_digits):
            dropped = True
            return None
        return sentence

    for piece in pieces:
        buffer += piece
        parts = _BOUNDARY.split(buffer)
        # Keep the last part: its sentence may still be growing.
        buffer = parts.pop()
        # Re-attach the separators the split removed so spacing survives.
        for part in parts:
            out = flush(part + " ")
            if out is not None:
                emitted_any = emitted_any or bool(out.strip())
                yield out
    tail = flush(buffer) if buffer else None
    if tail is not None:
        emitted_any = emitted_any or bool(tail.strip())
        yield tail
    if dropped and dial_line:
        yield ("" if not emitted_any else " ") + dial_line
