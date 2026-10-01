"""Text embeddings from the local Ollama server (config.EMBEDDING_MODEL,
bge-m3 by default).

Why this shape:
    * One reused requests.Session. Measured on the development machine, a
      warm bge-m3 query embedding takes about 30 to 90 ms over a kept alive
      connection; a fresh requests.post per call pays the TCP setup again
      and is noticeably slower. Retrieval runs before the first spoken
      word of every turn, so this matters.
    * config.EMBEDDING_HOST defaults to 127.0.0.1, never "localhost": on
      Windows "localhost" is tried as IPv6 first, Ollama listens on IPv4
      only, and every request then waited about 2 s for that to time out.
    * keep_alive "30m", the same as the chat model, so bge-m3 is not
      unloaded between turns of a demo with pauses in it.
    * Inputs are sent in batches (one /api/embed call embeds a list).
    * Vectors are returned L2 normalized as float32, so cosine similarity
      is a plain dot product.
    * Every failure (Ollama not running, model not pulled, timeout, bad
      response) raises EmbeddingUnavailableError, never a raw requests
      error, so callers can degrade to "no retrieval" in one except.

Run: python -m backend.knowledge.embeddings "some text"
"""

import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import requests

import config

KEEP_ALIVE = "30m"
DEFAULT_BATCH_SIZE = 16


class EmbeddingUnavailableError(RuntimeError):
    """The embedding model could not produce vectors (server down, model
    missing, timeout, malformed response)."""


class OllamaEmbedder:
    def __init__(self, model: Optional[str] = None, host: Optional[str] = None, timeout_seconds: float = 60.0):
        self.model = model or config.EMBEDDING_MODEL
        self.host = (host or config.EMBEDDING_HOST).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        # requests.Session is not documented as thread safe; the server may
        # retrieve from more than one worker thread, so calls are serialized.
        # Each call is tens of milliseconds, so this costs nothing in practice.
        self._lock = threading.Lock()
        self.dimensions: Optional[int] = None
        self.last_latency_ms: Optional[float] = None

    def _post(self, inputs: list, timeout: float) -> list:
        url = f"{self.host}/api/embed"
        payload = {"model": self.model, "input": inputs, "keep_alive": KEEP_ALIVE}
        try:
            with self._lock:
                response = self.session.post(url, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            raise EmbeddingUnavailableError(f"could not reach Ollama at {self.host}: {exc.__class__.__name__}") from exc
        if response.status_code == 404:
            raise EmbeddingUnavailableError(f"embedding model '{self.model}' not found on Ollama (ollama pull {self.model})")
        if response.status_code != 200:
            raise EmbeddingUnavailableError(f"Ollama /api/embed returned HTTP {response.status_code}: {response.text[:200]}")
        try:
            vectors = response.json()["embeddings"]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingUnavailableError("unreadable /api/embed response") from exc
        if len(vectors) != len(inputs):
            raise EmbeddingUnavailableError(f"asked for {len(inputs)} embeddings, got {len(vectors)}")
        return vectors

    def embed(self, texts: Sequence[str], batch_size: int = DEFAULT_BATCH_SIZE,
              timeout: Optional[float] = None, progress: bool = False) -> np.ndarray:
        """Returns a (len(texts), dims) float32 array of unit vectors."""
        texts = [t if t.strip() else " " for t in texts]
        if not texts:
            return np.zeros((0, self.dimensions or 0), dtype=np.float32)
        started = time.perf_counter()
        chunks = []
        for i in range(0, len(texts), batch_size):
            chunks.extend(self._post(texts[i:i + batch_size], timeout or self.timeout_seconds))
            if progress:
                print(f"  embedded {min(i + batch_size, len(texts))}/{len(texts)}", flush=True)
        matrix = np.asarray(chunks, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms
        self.dimensions = matrix.shape[1]
        self.last_latency_ms = (time.perf_counter() - started) * 1000
        return matrix

    def embed_query(self, text: str, timeout: Optional[float] = None) -> np.ndarray:
        """One query vector. A short timeout by default: this sits in
        front of every spoken reply, and a slow embedding is better
        skipped than waited for."""
        return self.embed([text], timeout=timeout or 10.0)[0]


_default: Optional[OllamaEmbedder] = None
_default_lock = threading.Lock()


def get_embedder() -> OllamaEmbedder:
    global _default
    with _default_lock:
        if _default is None:
            _default = OllamaEmbedder()
        return _default


if __name__ == "__main__":
    sample = " ".join(sys.argv[1:]) or "What are the Mobitel anytime data rollover plans?"
    embedder = get_embedder()
    try:
        embedder.embed_query(sample)  # first call may load the model
        cold_ms = embedder.last_latency_ms
        vector = embedder.embed_query(sample)
        print(f"Model {embedder.model} at {embedder.host}: {vector.shape[0]} dimensions, norm {np.linalg.norm(vector):.3f}")
        print(f"Embedding latency: first call {cold_ms:.0f} ms, warm {embedder.last_latency_ms:.0f} ms")
    except EmbeddingUnavailableError as exc:
        print(f"Embedding unavailable: {exc}")
        sys.exit(1)
