"""Deterministic heuristic for text no keyword rule matched.

It scores cues that suggest the person is asking about THEIR OWN account
(first person words, time words like "this month", charge words, a phone
number) in English, Sinhala, Tamil and the Latin letter forms. No model
call, no network, microseconds. The score is a capped sum; at or above the
policy threshold the text is treated as needing customer data.
"""

import re
from dataclasses import dataclass

from .policy import normalize

CUES = [
    # (name, weight, pattern) applied to normalized text
    ("first_person", 0.25, r"(?<![a-z])(my|mine|me|i|im|ive|our|we)(?![a-z])|මගේ|මම|අපේ|මට|எனது|என்|நான்|எனக்கு|எங்கள|(?<![a-z])(mage|mama|mata|ape|enakku|ennoda|enathu|naan|en)(?![a-z])"),
    ("time_anchor", 0.2, r"this month|last month|yesterday|today|this week|last week|so far|\bstill\b|\bagain\b|මේ මාසේ|පසුගිය මාසේ|ඊයේ|අද |இந்த மாதம்|கடந்த மாதம்|நேற்று|இன்று|me masaye|giya masaye|\biye\b|\bada\b|indha madham|ketha madham|netru|inniku"),
    ("charge_words", 0.35, r"charg(ed|es|ing)|deduct|debit|extra money|money (gone|missing|taken)|cut from|penalty|late fee|reconnection fee|ගාස්තු කපා|මුදල් කැපුණ|கட்டணம் வசூல|பணம் பிடித்|salli kapuna|salli kapila|panam pidichu"),
    ("status_words", 0.3, r"(is|was|has|have|did|does|are) (it|my|the)? ?(been )?(working|connected|active|activated|suspended|barred|blocked|expired|deactivated)|\bexpire|suspended|barred|වැඩ කරනවාද|සක්‍රීයද|இயங்குகிறதா|செயலில்|wada karanawada|activeda"),
    ("lookup_verbs", 0.15, r"\b(check|show|tell me|look up|find out|let me know|give me|view)\b|බලන්න|කියන්න|පෙන්වන්න|பார்க்க|சொல்ல|காட்டு|balanna|kiyanna|penwanna|paarunga|sollunga|kaattunga"),
    ("question_amount", 0.2, r"how much|how many|when (will|does|is|did)|what is the (amount|total)|කීයද|කීයක්ද|කවදාද|எவ்வளவு|எப்போது|kiyada|kiyak|kawadada|evvalavu|evlo|eppo"),
    ("own_possession", 0.2, r"do i have|have i (got|used|paid)|did i|am i|will i|can i get my|do we have|ඇතිද මට|මට තියෙන|எனக்கு இருக்கு|enakku irukka|mata thiyenawada"),
    ("own_number", 0.45, r"(?<!\d)(\+?94|0)?\d{2}[ -]?\d{3}[ -]?\d{4}(?!\d)|(?<!\d)\d{9,10}(?!\d)"),
    ("account_nouns", 0.1, r"\b(account|line|connection|number|package|plan|service|subscription|contract)\b|ගිණුම|ලයින්|සම්බන්ධතා|පැකේජ|கணக்கு|இணைப்பு|லைன்|பேக்கேஜ்|package eka|plan eka"),
]
_COMPILED = [(n, w, re.compile(p)) for n, w, p in CUES]


class ClassifierError(Exception):
    """The classifier could not decide; guests are then blocked."""


@dataclass(frozen=True)
class Verdict:
    needs_customer_data: bool
    score: float
    cues: tuple


def classify(text: str, threshold: float) -> Verdict:
    """Never raises for ordinary strings; a non string raises ClassifierError."""
    if not isinstance(text, str):
        raise ClassifierError("text must be a string")
    norm = normalize(text)
    score = 0.0
    hit = []
    for name, weight, pattern in _COMPILED:
        if pattern.search(norm):
            score += weight
            hit.append(name)
    score = min(score, 1.0)
    return Verdict(score >= threshold, round(score, 3), tuple(hit))
