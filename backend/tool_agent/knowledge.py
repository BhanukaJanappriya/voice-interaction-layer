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


# Added to a second, parallel search that only widens the context. Measured on the live hub:
# "What fibre packages do you have?" scored 0.45 on its own (two thin table chunks, and the
# model then said it had no details), 0.56 with this suffix; an off topic question rose from
# 0.36 to 0.44, so the plain question alone still decides whether the hub knows the topic.
RICH_SUFFIX = " SLT Mobitel details"


def _kept(result, threshold: float) -> list:
    kept = [c for c in _chunks_of(result) if c["score"] is None or c["score"] >= threshold]
    kept.sort(key=lambda c: -(c["score"] if c["score"] is not None else 1.0))
    return kept


# The hub's knowledge base is public and changes rarely, so the answer to a question that was just
# asked is kept for a few minutes: the second time costs no hub call (about 2 s on the live hub).
# Only a successful answer is kept, never a failure, and only for the guest tool.
CACHE_SECONDS = 600
CACHE_MAX = 200
_cache: "dict[str, tuple[float, Search]]" = {}


def _cache_key(query: str) -> str:
    return " ".join(query.lower().split())


def _cache_get(query: str) -> Optional[Search]:
    import time

    hit = _cache.get(_cache_key(query))
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return Search(hit[1].kind, list(hit[1].chunks), 0)
    return None


def _cache_put(query: str, found: Search) -> None:
    import time

    if len(_cache) >= CACHE_MAX:
        for key in sorted(_cache, key=lambda k: _cache[k][0])[: CACHE_MAX // 4]:
            _cache.pop(key, None)
    _cache[_cache_key(query)] = (time.monotonic(), found)


def clear_cache() -> None:
    _cache.clear()


def search(text: str, access, budget: Optional[float] = None, language: Optional[str] = None) -> Search:
    query = expand_query(text, language)[:QUERY_CHARS]
    cached = _cache_get(query)
    if cached is not None:
        return cached
    found = _search(text, access, budget, language)
    if found.kind in ("found", "none"):
        _cache_put(query, found)
    return found


def _search(text: str, access, budget: Optional[float] = None, language: Optional[str] = None) -> Search:
    from concurrent.futures import ThreadPoolExecutor

    query = expand_query(text, language)[:QUERY_CHARS]
    rich_query = (query + RICH_SUFFIX)[:QUERY_CHARS]
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        rich_future = pool.submit(mcp_gateway.call, TOOL, {"query": rich_query}, access, budget=budget)
        result = mcp_gateway.call(TOOL, {"query": query}, access, budget=budget)
        if result.kind == "reauth":
            return Search("reauth", ms=result.ms)
        if result.kind != "ok":
            return Search("unavailable", ms=result.ms)
        threshold = min_score()
        kept = _kept(result, threshold)
        ms = result.ms
        if kept:
            # The plain question found the topic: add what the richer question found, if it answered.
            try:
                rich = rich_future.result(timeout=max(1.0, float(budget or 0) or 4.0))
            except Exception:  # noqa: BLE001  the extra context is optional
                rich = None
            if rich is not None and rich.kind == "ok":
                seen = {c["content"][:120] for c in kept}
                for chunk in _kept(rich, threshold):
                    if chunk["content"][:120] not in seen:
                        kept.append(chunk)
                        seen.add(chunk["content"][:120])
                kept.sort(key=lambda c: -(c["score"] if c["score"] is not None else 1.0))
                ms = max(ms, rich.ms)
        kept = kept[:MAX_CHUNKS]
    finally:
        pool.shutdown(wait=False)  # never wait for the optional second search
    return Search("found" if kept else "none", kept, ms)


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

