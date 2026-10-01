"""Chunks the normalized documents, embeds them and stores the vector index.

Chunking (chunk_documents):
    * Page and public source text is packed into chunks of about 300 to
      500 tokens (MAX_TOKENS 450) with about 60 tokens of overlap, keeping
      whole lines (a table row or paragraph is never cut in half unless it
      alone is longer than a chunk). Each chunk carries its document title
      and the nearest heading, because a line such as "Validity: 30 Days"
      means nothing without the package name above it.
    * Every detected package becomes its own small chunk (name, price,
      data, validity, conditions), so a question about one package
      retrieves exactly that package instead of a long page.
    * Every curated FAQ entry (faq.jsonl) becomes its own chunk.
    * Identical chunk texts (the same table on two pages) are kept once.
Token counts are estimated (about 1.3 tokens per English word, fewer
characters per token for Sinhala and Tamil script); the exact count does
not matter, only that chunks stay well inside bge-m3's window and small
enough to keep the prompt short.

Storage, under KNOWLEDGE_DIR/index/ (ADR 3, a numpy matrix, no vector DB):
    vectors.npy     float32, one L2 normalized row per chunk
    chunks.jsonl    one chunk per line, same order as the rows
    manifest.json   model, dimensions, built_at, counts, sources

Run: python -m backend.knowledge.index (uses docs.jsonl from normalize.py)
"""

import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

import config
from knowledge.embeddings import get_embedder
from knowledge.normalize import DOCS_PATH, load_faq
from knowledge.scraper import ALLOWED_HOSTS, LEGACY_HOSTS

INDEX_DIR = config.KNOWLEDGE_DIR / "index"
VECTORS_PATH = INDEX_DIR / "vectors.npy"
CHUNKS_PATH = INDEX_DIR / "chunks.jsonl"
MANIFEST_PATH = INDEX_DIR / "manifest.json"

MAX_TOKENS = 160
OVERLAP_TOKENS = 30


class IndexMissingError(RuntimeError):
    """No usable index on disk (never built, or files missing/corrupt)."""


def estimate_tokens(text: str) -> int:
    non_latin = sum(1 for ch in text if ord(ch) > 0x2FF)
    if non_latin > len(text) * 0.3:
        return int(len(text) / 2.5) + 1
    return int(max(len(text.split()) * 1.3, len(text) / 4)) + 1


def _split_long_line(line: str, max_tokens: int) -> list:
    if estimate_tokens(line) <= max_tokens:
        return [line]
    sentences = re.split(r"(?<=[.!?;])\s+", line)
    pieces, current = [], ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    # A single sentence longer than the limit: cut by words.
    result = []
    for piece in pieces:
        words = piece.split()
        while estimate_tokens(" ".join(words)) > max_tokens and len(words) > 1:
            cut = max(1, int(len(words) * max_tokens / estimate_tokens(" ".join(words))) - 1)
            result.append(" ".join(words[:cut]))
            words = words[cut:]
        result.append(" ".join(words))
    return result


def chunk_text(units: list, max_tokens: int = MAX_TOKENS, overlap_tokens: int = OVERLAP_TOKENS) -> list:
    """Packs (heading, line) units into chunks. Returns a list of
    (heading, [lines]) tuples: heading is the heading in force at the
    start of the chunk. Consecutive chunks share up to overlap_tokens of
    trailing lines from the same heading."""
    expanded = []
    for heading, line in units:
        for piece in _split_long_line(line, max_tokens):
            expanded.append((heading, piece))

    chunks = []
    current, current_tokens = [], 0
    for heading, line in expanded:
        tokens = estimate_tokens(line)
        if current and current_tokens + tokens > max_tokens:
            chunks.append(current)
            overlap, overlap_size = [], 0
            for prev_heading, prev_line in reversed(current):
                size = estimate_tokens(prev_line)
                if prev_heading != heading or overlap_size + size > overlap_tokens:
                    break
                overlap.insert(0, (prev_heading, prev_line))
                overlap_size += size
            current, current_tokens = overlap, overlap_size
        current.append((heading, line))
        current_tokens += tokens
    if current:
        chunks.append(current)

    result = []
    for chunk in chunks:
        heading = chunk[0][0]
        lines = []
        last_heading = heading
        for unit_heading, line in chunk:
            if unit_heading != last_heading and unit_heading:
                lines.append(f"{unit_heading}:")
                last_heading = unit_heading
            lines.append(line)
        result.append((heading, lines))
    return result


def _chunk_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _package_text(pkg: dict) -> str:
    parts = [f"Package: {pkg['name']}", f"Price: {pkg['price']}"]
    if pkg.get("data"):
        parts.append(f"Data: {pkg['data']}")
    if pkg.get("validity"):
        parts.append(f"Validity: {pkg['validity']}")
    for label, value in list((pkg.get("conditions") or {}).items())[:8]:
        parts.append(f"{label}: {value}")
    context = pkg.get("page_title") or ""
    if pkg.get("section") and pkg["section"] != context:
        context = f"{context} / {pkg['section']}" if context else pkg["section"]
    text = "; ".join(parts)
    return f"{text} (listed under {context})" if context else text


def chunk_documents(docs: list, faqs: Optional[list] = None) -> list:
    """All chunks for the index, as dicts with text, title, source_url,
    retrieved_at, kind (text, package or faq) and metadata."""
    chunks = []
    seen = set()

    def add(kind: str, text: str, title: str, source_url: str, retrieved_at: str, metadata: dict):
        key = re.sub(r"\s+", " ", text.lower()).strip()
        if not key or key in seen:
            return
        seen.add(key)
        chunks.append({
            "chunk_id": _chunk_id(source_url + key),
            "kind": kind,
            "title": title,
            "text": text,
            "source_url": source_url,
            "retrieved_at": retrieved_at,
            "metadata": metadata,
        })

    for doc in docs:
        units = []
        for section in doc["sections"]:
            for line in section["lines"]:
                units.append((section.get("heading", ""), line))
        base_meta = {"doc_kind": doc["kind"], "lang": doc["lang"], "host": doc.get("host"),
                     "page_date": doc.get("page_date")}
        for heading, lines in chunk_text(units):
            body = "\n".join(lines)
            text = f"{heading}\n{body}" if heading and not body.startswith(heading) else body
            add("text", text, doc["title"], doc["source_url"], doc["retrieved_at"], {**base_meta, "heading": heading})
        for pkg in doc.get("packages", []):
            add("package", _package_text(pkg), doc["title"], pkg["source_url"], pkg["retrieved_at"],
                {**base_meta, "package": pkg})

    for faq in faqs or []:
        question = faq["question"]
        text = f"Q: {question}\nA: {faq['answer']}"
        add("faq", text, faq.get("context") or "FAQ", faq["source_url"], faq["retrieved_at"],
            {"doc_kind": "faq", "lang": "en", "question": question})
    return chunks


def _embedding_input(chunk: dict) -> str:
    """What is embedded: the title in front of the chunk, so the page or
    package name contributes to the match even when the chunk body only
    says "Validity: 30 Days"."""
    return f"{chunk['title']}\n{chunk['text']}"


def build_index(docs: Optional[list] = None, hosts: Optional[list] = None) -> dict:
    started = time.perf_counter()
    if docs is None:
        if not DOCS_PATH.exists():
            raise FileNotFoundError(f"{DOCS_PATH} not found; run normalize first")
        with DOCS_PATH.open(encoding="utf-8") as handle:
            docs = [json.loads(line) for line in handle if line.strip()]
    if hosts is None:
        hosts = sorted(set(ALLOWED_HOSTS) | {d.get("host") for d in docs if d.get("kind") == "page"})
    # Curated FAQ entries taken from a legacy host are only indexed when
    # that host is part of this build (--include-legacy); otherwise they
    # would bring an excluded site back in through the side door.
    excluded = set(LEGACY_HOSTS) - set(hosts)
    faqs = [f for f in load_faq() if urlparse(f["source_url"]).hostname not in excluded]
    chunks = chunk_documents(docs, faqs)
    print(f"Index: {len(docs)} documents, {len(faqs)} curated FAQ entries -> {len(chunks)} chunks")

    embedder = get_embedder()
    embed_started = time.perf_counter()
    vectors = embedder.embed([_embedding_input(c) for c in chunks], batch_size=16, timeout=180, progress=True)
    embed_seconds = time.perf_counter() - embed_started

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECTORS_PATH, vectors.astype(np.float32))
    with CHUNKS_PATH.open("w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    kinds = {}
    for chunk in chunks:
        kinds[chunk["kind"]] = kinds.get(chunk["kind"], 0) + 1
    sources = {}
    for chunk in chunks:
        sources.setdefault(chunk["source_url"], chunk["retrieved_at"])
    manifest = {
        "model": embedder.model,
        "dimensions": int(vectors.shape[1]),
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "counts": {
            "documents": len(docs),
            "chunks": len(chunks),
            "chunks_by_kind": kinds,
            "packages": sum(len(d.get("packages", [])) for d in docs),
            "faq_entries": len(faqs),
            "sources": len(sources),
        },
        "crawled_hosts": hosts,
        "chunking": {"max_tokens": MAX_TOKENS, "overlap_tokens": OVERLAP_TOKENS},
        "embedding_seconds": round(embed_seconds, 1),
        "sources": [{"source_url": url, "retrieved_at": at} for url, at in sorted(sources.items())],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    size_kb = (VECTORS_PATH.stat().st_size + CHUNKS_PATH.stat().st_size) / 1024
    print(f"Index written to {INDEX_DIR}: {vectors.shape[0]} x {vectors.shape[1]}, {size_kb:.0f} KB")
    print(f"Index latency: {time.perf_counter() - started:.1f}s (embedding {embed_seconds:.1f}s)")
    return manifest


def load_index(index_dir: Optional[Path] = None) -> tuple:
    """(vectors, chunks, manifest). Raises IndexMissingError when the
    index is absent or inconsistent."""
    index_dir = Path(index_dir or INDEX_DIR)
    vectors_path = index_dir / "vectors.npy"
    chunks_path = index_dir / "chunks.jsonl"
    manifest_path = index_dir / "manifest.json"
    if not (vectors_path.exists() and chunks_path.exists() and manifest_path.exists()):
        raise IndexMissingError(f"no knowledge index in {index_dir} (run python -m backend.knowledge.build)")
    try:
        vectors = np.load(vectors_path).astype(np.float32, copy=False)
        with chunks_path.open(encoding="utf-8") as handle:
            chunks = [json.loads(line) for line in handle if line.strip()]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise IndexMissingError(f"knowledge index in {index_dir} is unreadable: {exc}") from exc
    if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
        raise IndexMissingError(f"knowledge index in {index_dir} is inconsistent: "
                                f"{vectors.shape} vectors for {len(chunks)} chunks")
    return vectors, chunks, manifest


if __name__ == "__main__":
    build_index()
