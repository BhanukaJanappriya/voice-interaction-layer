"""The SLT knowledge search through the gateway.

search(text, access) asks search_knowledgebase_tool (the only tool a guest
has) and keeps the excerpts whose score is at least KB_MIN_SCORE. The reply of
the hub is {"query", "chunks": [{"content", "score"}], "count"}.
"""

from dataclasses import dataclass, field
from typing import Optional

import config
import mcp_gateway

TOOL = "search_knowledgebase_tool"
QUERY_CHARS = 500
MAX_CHUNKS = 5


@dataclass
class Search:
    kind: str  # found | none | unavailable | reauth
    chunks: list = field(default_factory=list)  # [{"content": str, "score": float|None}]
    ms: int = 0


def _score(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _chunks_of(result) -> list:
    data = result.data
    found = []
    if isinstance(data, dict) and isinstance(data.get("chunks"), list):
        for item in data["chunks"]:
            if isinstance(item, dict):
                content = str(item.get("content") or item.get("text") or "").strip()
                score = _score(item.get("score"))
            else:
                content, score = str(item or "").strip(), None
            if content:
                found.append({"content": content, "score": score})
    elif data is None and (result.text or "").strip():
        # A hub that answers with plain text: one excerpt without a score.
        found.append({"content": result.text.strip(), "score": None})
    return found


def min_score() -> float:
    try:
        return float(getattr(config, "KB_MIN_SCORE", 0.40))
    except (TypeError, ValueError):
        return 0.40


# The hub's knowledge base is mostly English, so a Sinhala or Tamil question finds
# its answer better when the matching English topic words are added to the query.
# Measured on the live hub: a Sinhala fibre question scored 0.46 against loosely
# related text, the same topic in English 0.59 to 0.68.
_TOPIC_TERMS = (
    (r"ෆයිබර්|ෆයිබර|ஃபைபர்|ஃபைபர|பைபர்", "fibre FTTH broadband packages"),
    (r"බ්‍රෝඩ්බෑන්ඩ්|අන්තර්ජාල|ඉන්ටර්නෙට්|ப்ராட்பேண்ட்|இணைய", "broadband internet packages"),
    (r"එල්ටීඊ|4ජී|எல்டிஇ|4ஜி", "LTE 4G packages"),
    (r"පියෝ|පීඊඕ|ටීවී|பியோ|டிவி|தொலைக்காட்சி", "PEO TV packages channels"),
    (r"දුරකථන|ෆෝන්|தொலைபேசி|ஃபோன்|போன்", "voice telephone plans"),
    (r"ඩේටා|දත්ත|டேட்டா|தரவு", "data add-on package"),
    (r"රවුටර්|වයිෆයි|ரூட்டர்|வைஃபை", "router wifi"),
    (r"පැකේජ්|පැකේජ|පැකෙජ|பேக்கேஜ்|தொகுப்பு", "package price monthly rental"),
    (r"මිල|ගාස්තු|விலை|கட்டணம்", "price charges"),
    (r"නව සම්බන්ධතා|අලුත් සම්බන්ධතා|புதிய இணைப்பு", "new connection"),
    (r"ස්ථාන මාරු|இடமாற்ற", "relocation transfer"),
    (r"රෝමිං|ரோமிங்", "roaming"),
    (r"ජාත්‍යන්තර|சர்வதேச", "international calls IDD"),
    (r"වේගය|வேகம்", "speed Mbps"),
    (r"ආවරණ|கவரேஜ்", "coverage"),
    (r"රිචාජ්|ප්‍රීපේඩ්|ரீசார்ஜ்|ப்ரீபெய்ட்", "prepaid reload"),
)


def expand_query(text: str, language: Optional[str]) -> str:
    """The question, plus English topic words for Sinhala and Tamil."""
    import re

    text = (text or "").strip()
    if (language or "en")[:2] == "en":
        return text
    terms = []
    for pattern, english in _TOPIC_TERMS:
        if re.search(pattern, text):
            for word in english.split():
                if word not in terms:
                    terms.append(word)
    return f"{text} {' '.join(terms)}".strip() if terms else text


def search(text: str, access, budget: Optional[float] = None, language: Optional[str] = None) -> Search:
    query = expand_query(text, language)[:QUERY_CHARS]
    result = mcp_gateway.call(TOOL, {"query": query}, access, budget=budget)
    if result.kind == "reauth":
        return Search("reauth", ms=result.ms)
    if result.kind != "ok":
        return Search("unavailable", ms=result.ms)
    threshold = min_score()
    kept = [c for c in _chunks_of(result) if c["score"] is None or c["score"] >= threshold]
    kept.sort(key=lambda c: -(c["score"] if c["score"] is not None else 1.0))
    kept = kept[:MAX_CHUNKS]
    return Search("found" if kept else "none", kept, result.ms)


def context_of(chunks: list, max_chars: Optional[int] = None) -> str:
    """The excerpts as numbered text blocks, cut to max_chars."""
    limit = int(max_chars or getattr(config, "KB_MAX_CHARS", 4000))
    parts = []
    used = 0
    for number, chunk in enumerate(chunks, 1):
        block = f"[Excerpt {number}]\n{chunk['content']}"
        if used + len(block) > limit:
            block = block[: max(limit - used, 0)]
        if block:
            parts.append(block)
            used += len(block) + 2
        if used >= limit:
            break
    return "SLT INFORMATION (excerpts from the SLT knowledge base):\n" + "\n\n".join(parts)

