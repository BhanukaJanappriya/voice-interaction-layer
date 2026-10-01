"""Knowledge base retrieval for the voice assistant.

get_retriever() returns the process wide KnowledgeRetriever. Its
context_for(text) is the Orchestrator's tool_callback: given the user's
message it returns a compact block of the best matching knowledge base
chunks, each tagged with its source URL and retrieval date, or None when
there is nothing useful to add. It never raises, because it sits in front
of every spoken reply and a retrieval problem must never cost the user an
answer; the reply then simply comes from the model without extra context.

How a query is answered (ADR 2 and ADR 3):
    1. The query is embedded with bge-m3 through the local Ollama (one
       reused HTTP session; about 30 to 90 ms warm).
    2. One matrix product against the L2 normalized chunk vectors gives
       the cosine similarity of every chunk (well under a millisecond for
       a few thousand chunks).
    3. A small lexical boost is added for package chunks whose package
       name or price number appears literally in the query ("MOBITEL
       1598", "the 799 plan"): dense embeddings blur numbers, and people
       ask for packages by their number.
    4. Chunks below config.RETRIEVAL_MIN_SCORE are dropped, as are chunks
       far below the best one (RELATIVE_MARGIN), so small talk ("hello",
       "what time is it") gets no context at all and a clear question
       gets only its relevant chunks.
Results are cached per normalized query (LRU), so when the Orchestrator
falls back from the cloud path to the local model within the same turn
the question is not embedded twice.

Run: python -m backend.knowledge.retriever "question" (or python
retriever.py "question" from inside backend/knowledge) prints the
results, the context block and the latency.
"""

import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import config
from knowledge.embeddings import EmbeddingUnavailableError, OllamaEmbedder, get_embedder
from knowledge.index import INDEX_DIR, IndexMissingError, load_index

# Chunks scoring more than this below the best chunk are not included.
RELATIVE_MARGIN = 0.12
# Added to a package chunk's score when the query names it literally.
LEXICAL_BOOST = 0.06
# Keyword help for ranking: each distinct question word (an email address,
# "APN", "roaming") found in a candidate chunk adds this much to its rank,
# up to TERM_BOOST_MAX words. Embeddings blur exact words, and the chunk
# holding the answer to "what email address can I write to" ranked 8th by
# meaning alone. Only chunks already within TERM_BOOST_WINDOW of the best
# embedding score are eligible, and the boost changes the order only: the
# score reported, and used to decide whether anything is relevant at all,
# stays the plain embedding score, so small talk cannot be lifted over the
# threshold by a shared word like "good" or "hello".
TERM_BOOST = 0.04
TERM_BOOST_MAX = 4
TERM_BOOST_WINDOW = 0.10
# Size limits for the context block, which goes into the model prompt:
# every extra character is read by the local model before it can start
# speaking (measured: 4 chunks of about 900 characters took 404 ms to the
# first token against 180 ms with none), so only the few best chunks go in,
# each cut down to the sentences that match the question, and page furniture that never helps an answer is removed.
MAX_CHUNKS = 3
MAX_CHUNK_CHARS = 700
MAX_CONTEXT_CHARS = 2200
CACHE_SIZE = 256
# How often to look for an index that was missing (built after startup).
RELOAD_INTERVAL_SECONDS = 60

CONTEXT_HEADER = "SLT-Mobitel notes (official sources; prices and packages can change after the date shown):"

# Table furniture copied from the websites' package tables along with the
# facts: buttons and a regulator reference number, never part of an answer.
_NOISE = re.compile(r"(?:,\s*)?(?:View Charges/ ?More Info|Self-care App|TRC Code - [A-Z0-9/]+)", re.IGNORECASE)


@dataclass
class RetrievedChunk:
    text: str
    score: float
    source_url: str
    retrieved_at: str
    title: str
    metadata: dict = field(default_factory=dict)


def _normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" ?!.,")


_NUMBER = re.compile(r"\b\d{2,5}\b")
_NAME_STOPWORDS = {"mobitel", "slt", "package", "plan", "plans", "packages", "the", "and", "rs", "new", "of"}


def _name_tokens(name: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if t not in _NAME_STOPWORDS and len(t) > 1}


class KnowledgeRetriever:
    def __init__(self, index_dir: Optional[Path] = None, embedder: Optional[OllamaEmbedder] = None):
        self.index_dir = Path(index_dir or INDEX_DIR)
        self._embedder = embedder
        self._lock = threading.RLock()
        self.vectors: Optional[np.ndarray] = None
        self.chunks: list = []
        self._lowered: list = []
        self.manifest: dict = {}
        self.loaded = False
        self.error: Optional[str] = None
        self.last_latency_ms: Optional[float] = None
        self._last_load_attempt = 0.0
        self._query_cache: "OrderedDict[str, np.ndarray]" = OrderedDict()
        self._context_cache: "OrderedDict[str, Optional[str]]" = OrderedDict()
        self._package_rows: list = []  # (chunk index, package dict, name tokens, price number)

    @property
    def embedder(self) -> OllamaEmbedder:
        if self._embedder is None:
            self._embedder = get_embedder()
        return self._embedder

    # Loading

    def load(self, force: bool = False) -> bool:
        """Loads the index from disk. Returns False (and records why in
        self.error) when it is missing or unreadable."""
        with self._lock:
            if self.loaded and not force:
                return True
            if not force and time.monotonic() - self._last_load_attempt < RELOAD_INTERVAL_SECONDS \
                    and self._last_load_attempt:
                return False
            self._last_load_attempt = time.monotonic()
            try:
                vectors, chunks, manifest = load_index(self.index_dir)
            except IndexMissingError as exc:
                self.error = str(exc)
                self.loaded = False
                return False
            self.vectors, self.chunks, self.manifest = vectors, chunks, manifest
            self._lowered = [f"{chunk.get('title') or ''} {chunk['text']}".lower() for chunk in chunks]
            self._package_rows = []
            for i, chunk in enumerate(chunks):
                pkg = (chunk.get("metadata") or {}).get("package")
                if pkg:
                    numbers = set(_NUMBER.findall(pkg.get("name", "")))
                    if pkg.get("price_lkr"):
                        numbers.add(str(int(pkg["price_lkr"])))
                    self._package_rows.append((i, pkg, _name_tokens(pkg.get("name", "")), numbers))
            self._query_cache.clear()
            self._context_cache.clear()
            self.loaded = True
            self.error = None
            return True

    def warm_up(self) -> None:
        """Loads the index and makes one embedding call, so bge-m3 is in
        memory before the first real question. Failures are only logged."""
        started = time.perf_counter()
        if not config.KNOWLEDGE_ENABLED:
            print("Knowledge base disabled (KNOWLEDGE_ENABLED=false).")
            return
        if not self.load(force=True):
            print(f"Knowledge base not loaded: {self.error}")
            return
        try:
            self.embedder.embed_query("warm up")
            self.error = None
        except EmbeddingUnavailableError as exc:
            self.error = str(exc)
            print(f"Knowledge base warm up: embedding unavailable ({exc})")
            return
        print(f"Knowledge base warm up: {len(self.chunks)} chunks, "
              f"{(time.perf_counter() - started) * 1000:.0f} ms")

    # Search

    def _query_vector(self, normalized: str) -> np.ndarray:
        cached = self._query_cache.get(normalized)
        if cached is not None:
            self._query_cache.move_to_end(normalized)
            return cached
        vector = self.embedder.embed_query(normalized)
        self._query_cache[normalized] = vector
        if len(self._query_cache) > CACHE_SIZE:
            self._query_cache.popitem(last=False)
        return vector

    def _lexical_boosts(self, normalized: str) -> dict:
        if not self._package_rows:
            return {}
        query_tokens = set(re.findall(r"[a-z0-9]+", normalized))
        query_numbers = set(_NUMBER.findall(normalized))
        boosts = {}
        for index, _pkg, name_tokens, numbers in self._package_rows:
            hit = bool(numbers & query_numbers) or (len(name_tokens) >= 2 and name_tokens <= query_tokens)
            if hit:
                boosts[index] = LEXICAL_BOOST
        return boosts

    def search(self, query: str, top_k: int = config.RETRIEVAL_TOP_K) -> list:
        """The top_k chunks for query, best first, with their cosine
        scores (plus the lexical boost). Returns an empty list when the
        knowledge base is disabled, the index is missing or the embedding
        model is unavailable (self.error says which); never raises."""
        started = time.perf_counter()
        normalized = _normalize_query(query)
        if not normalized or not config.KNOWLEDGE_ENABLED:
            return []
        if not self.load():
            return []
        try:
            vector = self._query_vector(normalized)
        except EmbeddingUnavailableError as exc:
            self.error = str(exc)
            print(f"Retrieval skipped: {exc}")
            return []
        with self._lock:
            vectors, chunks, lowered = self.vectors, self.chunks, self._lowered
        if vector.shape[0] != vectors.shape[1]:
            self.error = f"query has {vector.shape[0]} dimensions, index has {vectors.shape[1]} (rebuild the index)"
            return []
        scores = vectors @ vector
        for index, boost in self._lexical_boosts(normalized).items():
            scores[index] += boost
        ranking = scores.copy()
        terms = _query_terms(normalized)
        if terms:
            for index in np.nonzero(scores >= scores.max() - TERM_BOOST_WINDOW)[0]:
                matched = sum(1 for term in terms if term in lowered[index])
                ranking[index] += TERM_BOOST * min(matched, TERM_BOOST_MAX)
        k = max(1, min(top_k, len(chunks)))
        top = np.argpartition(-ranking, k - 1)[:k]
        top = top[np.argsort(-ranking[top])]
        results = []
        for index in top:
            chunk = chunks[int(index)]
            results.append(RetrievedChunk(
                text=chunk["text"],
                score=float(scores[index]),
                source_url=chunk["source_url"],
                retrieved_at=chunk.get("retrieved_at") or "",
                title=chunk.get("title") or "",
                metadata={**(chunk.get("metadata") or {}), "kind": chunk.get("kind"),
                          "chunk_id": chunk.get("chunk_id")},
            ))
        self.error = None
        self.last_latency_ms = (time.perf_counter() - started) * 1000
        print(f"Retrieval latency: {self.last_latency_ms:.0f} ms")
        return results

    # The Orchestrator hook

    def context_for(self, text: str) -> Optional[str]:
        """tool_callback for the Orchestrator: a compact context block of
        the relevant chunks, or None (index missing, knowledge disabled,
        Ollama down, or nothing relevant enough). Never raises."""
        try:
            normalized = _normalize_query(text)
            if len(normalized) < 2 or not config.KNOWLEDGE_ENABLED:
                return None
            if normalized in self._context_cache:
                self._context_cache.move_to_end(normalized)
                return self._context_cache[normalized]
            results = self.search(text, top_k=config.RETRIEVAL_TOP_K)
            if not results:
                # Not cached: an unavailable model or missing index may
                # recover, and the next turn should try again.
                return None
            context = format_context(select_relevant(results), text)
            self._context_cache[normalized] = context
            if len(self._context_cache) > CACHE_SIZE:
                self._context_cache.popitem(last=False)
            return context
        except Exception as exc:  # the hook must never break a reply
            self.error = f"context_for failed: {exc.__class__.__name__}: {exc}"
            print(f"Retrieval error (ignored): {self.error}")
            return None

    # Structured package lookup (for MCP)

    def get_package_details(self, name: str) -> list:
        """Structured metadata of the packages matching name, best match
        first (at most 10). Matches by package number or name words first;
        if nothing matches literally, falls back to semantic search over
        package chunks. Empty list when nothing matches or the index is
        unavailable."""
        normalized = _normalize_query(name)
        if not normalized or not self.load():
            return []
        query_tokens = _name_tokens(normalized)
        query_numbers = set(_NUMBER.findall(normalized))
        scored = []
        for index, pkg, name_tokens, numbers in self._package_rows:
            score = 0.0
            if query_numbers and numbers & query_numbers:
                score += 2.0
            if query_tokens and name_tokens:
                score += len(query_tokens & name_tokens) / len(query_tokens)
            if score > 0.5:
                scored.append((score, index, pkg))
        if not scored:
            for result in self.search(name, top_k=10):
                if result.metadata.get("kind") == "package" and result.score >= config.RETRIEVAL_MIN_SCORE:
                    scored.append((result.score, -1, result.metadata["package"]))
        scored.sort(key=lambda item: -item[0])
        details, seen = [], set()
        for _score, _index, pkg in scored:
            key = (pkg.get("name"), pkg.get("price"), pkg.get("data"))
            if key in seen:
                continue
            seen.add(key)
            details.append({k: pkg.get(k) for k in ("name", "price", "price_lkr", "data", "validity",
                                                    "conditions", "page_title", "section", "source_url",
                                                    "retrieved_at", "name_from")})
            if len(details) >= 10:
                break
        return details

    def status(self) -> dict:
        if not self.loaded and config.KNOWLEDGE_ENABLED:
            self.load()
        return {
            "enabled": config.KNOWLEDGE_ENABLED,
            "loaded": self.loaded,
            "chunks": len(self.chunks),
            "built_at": self.manifest.get("built_at"),
            "embedding_model": self.manifest.get("model") or config.EMBEDDING_MODEL,
            "min_score": config.RETRIEVAL_MIN_SCORE,
            "last_latency_ms": round(self.last_latency_ms, 1) if self.last_latency_ms is not None else None,
            "error": self.error,
        }


def select_relevant(results: list, min_score: Optional[float] = None) -> list:
    """Chunks worth putting in the prompt: above the absolute threshold
    and within RELATIVE_MARGIN of the best chunk."""
    threshold = config.RETRIEVAL_MIN_SCORE if min_score is None else min_score
    if not results:
        return []
    best = max(r.score for r in results)
    if best < threshold:
        return []
    floor = max(threshold, best - RELATIVE_MARGIN)
    return [r for r in results if r.score >= floor]


_WORD = re.compile(r"\w+", re.UNICODE)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?;])\s+|\n+")
_STOPWORDS = {
    "the", "and", "for", "are", "was", "how", "what", "when", "who", "does", "can", "you", "your",
    "with", "that", "this", "from", "have", "has", "will", "much", "many", "any", "get", "give",
    "tell", "about", "please", "mobitel", "slt", "which", "where", "there", "their", "them",
}


def _query_terms(query: Optional[str]) -> set:
    return {w.lower() for w in _WORD.findall(query or "") if len(w) > 2 and w.lower() not in _STOPWORDS}


def _excerpt(text: str, terms: set, limit: int) -> str:
    """At most limit characters of text: the run of consecutive sentences
    (or lines) that mentions the most words from the question, the first
    such run when several tie. Cutting a chunk at its start, as an earlier
    version did, dropped the answer whenever it sat later in the chunk (the
    tourist SIM plans, when Mobitel was founded), and the model then made
    something up. Short text is returned whole."""
    if len(text) <= limit:
        return text
    units = [u.strip() for u in _SENTENCE_BREAK.split(text) if u.strip()]
    hits = [sum(1 for t in terms if t in u.lower()) for u in units]
    best_score, best_run = -1, (0, 1)
    for start in range(len(units)):
        length, score, end = 0, 0, start
        while end < len(units) and (end == start or length + len(units[end]) + 1 <= limit):
            length += len(units[end]) + 1
            score += hits[end]
            end += 1
        if score > best_score:
            best_score, best_run = score, (start, end)
    excerpt = " ".join(units[best_run[0]:best_run[1]])
    if len(excerpt) > limit:
        excerpt = excerpt[:limit].rsplit(" ", 1)[0]
    prefix = "... " if best_run[0] > 0 else ""
    suffix = " ..." if best_run[1] < len(units) or len(excerpt) < len(" ".join(units[best_run[0]:best_run[1]])) else ""
    return prefix + excerpt + suffix


def format_context(results: list, query: Optional[str] = None) -> Optional[str]:
    """The block the model sees. Each excerpt names its source and the
    date it was retrieved, so the model (and the logs) can say where a
    price came from and how old it is. With the question given, a long
    chunk is cut down to its sentences that match it."""
    if not results:
        return None
    parts = [CONTEXT_HEADER]
    budget = MAX_CONTEXT_CHARS
    terms = _query_terms(query)
    for n, result in enumerate(results[:MAX_CHUNKS], 1):
        text = _excerpt(_NOISE.sub("", result.text).strip(), terms, MAX_CHUNK_CHARS)
        header = f"[{n}] {result.title} | {result.source_url} | {result.retrieved_at[:10]}"
        block = f"{header}\n{text}"
        if len(block) > budget and n > 1:
            break
        parts.append(block)
        budget -= len(block)
    return "\n\n".join(parts)


_retriever: Optional[KnowledgeRetriever] = None
_retriever_lock = threading.Lock()


def get_retriever() -> KnowledgeRetriever:
    """The process wide retriever (created on first use, loaded lazily)."""
    global _retriever
    with _retriever_lock:
        if _retriever is None:
            _retriever = KnowledgeRetriever()
        return _retriever


def main(argv=None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    query = " ".join(argv) or "What are the Mobitel anytime data rollover plans?"
    retriever = get_retriever()
    retriever.warm_up()
    results = retriever.search(query)
    print(f"\nQuery: {query}")
    for result in results:
        print(f"\n{result.score:.3f}  [{result.metadata.get('kind')}] {result.title}  {result.source_url}")
        print("   " + result.text[:300].replace("\n", "\n   "))
    started = time.perf_counter()
    retriever._context_cache.clear()
    context = retriever.context_for(query)
    print(f"\ncontext_for latency (query embedding cached): {(time.perf_counter() - started) * 1000:.1f} ms")
    print("\ncontext_for():")
    print(context if context else "None (nothing above RETRIEVAL_MIN_SCORE, or retrieval unavailable)")
    print(f"\nStatus: {retriever.status()}")


if __name__ == "__main__":
    main()
