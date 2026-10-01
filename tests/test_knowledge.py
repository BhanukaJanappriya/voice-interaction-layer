"""Knowledge base: chunking, retriever threshold behavior, failure
tolerance of context_for, status() shape. The index used here is a tiny
hand made one in a temporary directory, and embeddings come from a fake
Ollama, so nothing needs the network or a model. Real retrieval against
the built index is marked live."""

import json
import math
from pathlib import Path

import numpy as np
import pytest
import requests

import config
from conftest import REPO_ROOT, optional_import

ROAMING_TEXT = (
    "Roaming Saver: stay connected abroad with the SLT Mobitel Roaming Saver pack, "
    "Rs 999 for 7 days with 2 GB of data in 40 countries."
)
BILLING_TEXT = "You can pay your SLT Mobitel bill online through the MySLT portal or at any SLT Mobitel outlet."
ROAMING_URL = "https://www.sltmobitel.lk/roaming"
BILLING_URL = "https://www.sltmobitel.lk/bill-payment"


@pytest.fixture(scope="module")
def retriever_module():
    module = optional_import("knowledge.retriever")
    if module is None:
        pytest.skip("knowledge.retriever not delivered yet (workstream A)")
    return module


def _unit(vector):
    array = np.asarray(vector, dtype=np.float32)
    return array / np.linalg.norm(array)


# Three directions in a tiny embedding space: roaming, billing, small talk.
_VECTORS = {"roaming": _unit([1, 0, 0, 0]), "billing": _unit([0, 1, 0, 0]), "smalltalk": _unit([0, 0, 1, 0])}


def _topic(text):
    lowered = text.lower()
    if "roam" in lowered or "abroad" in lowered:
        return "roaming"
    if "bill" in lowered or "pay" in lowered:
        return "billing"
    return "smalltalk"


@pytest.fixture
def fake_embeddings(monkeypatch):
    """Answers Ollama /api/embed (and the older /api/embeddings) through
    any requests call, with a vector chosen by topic keyword."""
    state = {"down": False, "calls": 0}
    real_request = requests.Session.request

    class _Response:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200
            self.ok = True
            self.text = json.dumps(payload)
            self.content = self.text.encode("utf-8")
            self.headers = {"content-type": "application/json"}

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    def _request(self, method, url, *args, **kwargs):
        url = str(url)
        if "/api/embed" not in url:
            return real_request(self, method, url, *args, **kwargs)
        state["calls"] += 1
        if state["down"]:
            raise requests.exceptions.ConnectionError("Ollama is down (test)")
        body = kwargs.get("json") or {}
        inputs = body.get("input", body.get("prompt", ""))
        texts = inputs if isinstance(inputs, list) else [inputs]
        vectors = [_VECTORS[_topic(text)].tolist() for text in texts]
        if "/api/embeddings" in url:
            return _Response({"embedding": vectors[0]})
        return _Response({"model": config.EMBEDDING_MODEL, "embeddings": vectors})

    monkeypatch.setattr(requests.Session, "request", _request)
    monkeypatch.setattr(requests, "post", lambda url, *a, **k: _request(None, "POST", url, *a, **k))
    return state


def _write_index(directory: Path):
    index_dir = directory / "index"
    index_dir.mkdir(parents=True, exist_ok=True)
    chunks = [
        {
            "id": "roaming-0",
            "text": ROAMING_TEXT,
            "title": "Roaming",
            "source_url": ROAMING_URL,
            "retrieved_at": "2026-09-30",
            "metadata": {"package": {"name": "Roaming Saver", "price": "Rs 999", "validity": "7 days"}},
        },
        {
            "id": "billing-0",
            "text": BILLING_TEXT,
            "title": "Bill payment",
            "source_url": BILLING_URL,
            "retrieved_at": "2026-09-30",
            "metadata": {},
        },
    ]
    vectors = np.stack([_VECTORS["roaming"], _VECTORS["billing"]]).astype(np.float32)
    np.save(index_dir / "vectors.npy", vectors)
    with open(index_dir / "chunks.jsonl", "w", encoding="utf-8") as handle:
        for chunk in chunks:
            handle.write(json.dumps(chunk) + "\n")
    manifest = {
        "model": config.EMBEDDING_MODEL,
        "embedding_model": config.EMBEDDING_MODEL,
        "dimensions": 4,
        "built_at": "2026-09-30T00:00:00+00:00",
        "counts": {"chunks": 2, "documents": 2},
        "chunks": 2,
        "sources": [ROAMING_URL, BILLING_URL],
    }
    (index_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return index_dir


def _fresh_retriever(retriever_module, monkeypatch, knowledge_dir):
    """A retriever of its own over knowledge_dir/index (the module level
    INDEX_DIR was fixed at import, so the directory is passed in)."""
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", Path(knowledge_dir))
    return retriever_module.KnowledgeRetriever(index_dir=Path(knowledge_dir) / "index")


@pytest.fixture
def empty_retriever(retriever_module, monkeypatch, tmp_path):
    return _fresh_retriever(retriever_module, monkeypatch, tmp_path / "knowledge-empty")


@pytest.fixture
def small_retriever(retriever_module, monkeypatch, tmp_path, fake_embeddings):
    knowledge_dir = tmp_path / "knowledge"
    _write_index(knowledge_dir)
    retriever = _fresh_retriever(retriever_module, monkeypatch, knowledge_dir)
    retriever.warm_up()
    if not retriever.status().get("loaded"):
        pytest.skip(f"hand made test index not accepted by this retriever: {retriever.status()}")
    return retriever


# Singleton and status


def test_get_retriever_is_a_singleton(retriever_module):
    assert retriever_module.get_retriever() is retriever_module.get_retriever()


STATUS_KEYS = {"loaded", "chunks", "built_at", "embedding_model", "last_latency_ms"}


def test_status_shape_without_index(empty_retriever):
    status = empty_retriever.status()
    assert STATUS_KEYS <= set(status), status
    assert status["loaded"] is False
    assert json.dumps(status)  # plain JSON for /health


# Never raises


def test_context_for_none_when_index_missing(empty_retriever, fake_embeddings):
    assert empty_retriever.context_for("what roaming packs do you have?") is None


def test_warm_up_without_index_does_not_raise(empty_retriever, fake_embeddings):
    empty_retriever.warm_up()


def test_context_for_none_when_ollama_down(small_retriever, fake_embeddings):
    fake_embeddings["down"] = True
    assert small_retriever.context_for("a completely new roaming question 7731") is None


def test_context_for_none_when_embedding_raises_anything(small_retriever, monkeypatch):
    def _boom(self, *args, **kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(requests.Session, "request", _boom)
    monkeypatch.setattr(requests, "post", lambda *a, **k: _boom(None))
    assert small_retriever.context_for("another roaming question 8812") is None


def test_context_for_handles_odd_input(small_retriever):
    for text in ("", "   ", "x" * 20000, "ආයුබෝවන්"):
        small_retriever.context_for(text)


# Threshold behavior


def test_relevant_question_gets_tagged_context(small_retriever):
    context = small_retriever.context_for("how much is roaming abroad?")
    assert context is not None
    assert "Roaming Saver" in context
    assert ROAMING_URL in context
    assert "2026-09-30" in context


def test_small_talk_returns_none(small_retriever):
    assert small_retriever.context_for("hello, how are you today?") is None


def test_search_ranks_and_scores(small_retriever):
    results = small_retriever.search("roaming abroad", top_k=2)
    assert results, "the matching chunk should be returned"
    top = results[0]
    assert top.source_url == ROAMING_URL
    assert top.retrieved_at
    assert math.isclose(float(top.score), 1.0, abs_tol=1e-3)
    assert all(results[i].score >= results[i + 1].score for i in range(len(results) - 1))


def test_status_after_loading(small_retriever):
    status = small_retriever.status()
    assert status["loaded"] is True
    assert status["chunks"] == 2
    assert status["embedding_model"] == config.EMBEDDING_MODEL


def test_repeated_question_is_cached(small_retriever, fake_embeddings):
    small_retriever.context_for("roaming price please")
    calls = fake_embeddings["calls"]
    small_retriever.context_for("roaming price please")
    assert fake_embeddings["calls"] == calls, "the same question should not be embedded twice"


def test_package_details(small_retriever):
    if not hasattr(small_retriever, "get_package_details"):
        pytest.skip("get_package_details not delivered yet")
    matches = small_retriever.get_package_details("roaming saver")
    assert isinstance(matches, list)
    assert small_retriever.get_package_details("no such package zzz") == []


# Chunking


@pytest.fixture(scope="module")
def index_module():
    module = optional_import("knowledge.index")
    if module is None:
        pytest.skip("knowledge.index not delivered yet")
    return module


def test_chunking_splits_long_text_with_overlap(index_module):
    units = [("Fibre plans", f"Line {i}. " + "SLT Mobitel offers fibre and mobile plans. " * 3) for i in range(40)]
    chunks = index_module.chunk_text(units, max_tokens=200, overlap_tokens=60)
    assert len(chunks) > 1
    texts = [" ".join(lines) for _heading, lines in chunks]
    assert all(text.strip() for text in texts)
    for i in range(40):
        assert any(f"Line {i}." in text for text in texts), f"line {i} lost"
    assert max(index_module.estimate_tokens(text) for text in texts) <= 200 + 60
    # Consecutive chunks under one heading share their boundary lines.
    first_lines, second_lines = chunks[0][1], chunks[1][1]
    assert first_lines[-1] in second_lines
    assert all(heading == "Fibre plans" for heading, _ in chunks)


def test_chunking_short_text_is_one_chunk(index_module):
    chunks = index_module.chunk_text([("FAQ", "A short FAQ answer.")])
    assert chunks == [("FAQ", ["A short FAQ answer."])]


def test_chunking_marks_heading_changes(index_module):
    chunks = index_module.chunk_text([("Roaming", "Roaming line."), ("Billing", "Billing line.")])
    assert len(chunks) == 1
    heading, lines = chunks[0]
    assert heading == "Roaming"
    assert "Billing:" in lines


def test_chunking_overlong_line_is_split(index_module):
    long_line = "word " * 2000
    chunks = index_module.chunk_text([("", long_line)], max_tokens=200, overlap_tokens=0)
    assert len(chunks) > 1
    total_words = sum(len(" ".join(lines).split()) for _h, lines in chunks)
    assert total_words == 2000


def test_chunk_documents_keeps_sources_and_faqs(index_module):
    doc = {
        "kind": "page",
        "lang": "en",
        "host": "www.sltmobitel.lk",
        "title": "Roaming",
        "source_url": ROAMING_URL,
        "retrieved_at": "2026-09-30",
        "sections": [{"heading": "Roaming Saver", "lines": [ROAMING_TEXT]}],
        "packages": [],
    }
    faq = {"question": "How do I pay my bill?", "answer": BILLING_TEXT, "source_url": BILLING_URL,
           "retrieved_at": "2026-09-30"}
    chunks = index_module.chunk_documents([doc, doc], [faq])
    kinds = sorted(chunk["kind"] for chunk in chunks)
    assert kinds == ["faq", "text"], "duplicate text is indexed once, and each FAQ is its own chunk"
    for chunk in chunks:
        assert chunk["source_url"] and chunk["retrieved_at"] == "2026-09-30"
    faq_chunk = next(c for c in chunks if c["kind"] == "faq")
    assert "How do I pay my bill?" in faq_chunk["text"] and BILLING_TEXT in faq_chunk["text"]


def test_load_index_rejects_inconsistent_index(index_module, tmp_path):
    index_dir = _write_index(tmp_path / "knowledge")
    np.save(index_dir / "vectors.npy", np.zeros((5, 4), dtype=np.float32))
    with pytest.raises(index_module.IndexMissingError):
        index_module.load_index(index_dir)


def test_select_relevant_threshold(retriever_module):
    chunk = retriever_module.RetrievedChunk
    fields = {"text": "t", "source_url": "u", "retrieved_at": "2026-09-30", "title": "x", "metadata": {}}
    results = [chunk(score=0.80, **fields), chunk(score=0.75, **fields), chunk(score=0.60, **fields)]
    kept = retriever_module.select_relevant(results, min_score=0.53)
    assert [r.score for r in kept] == [0.80, 0.75], "within the relative margin of the best only"
    assert retriever_module.select_relevant([chunk(score=0.40, **fields)], min_score=0.53) == []
    assert retriever_module.format_context([]) is None


# Live: the real built index and a running Ollama


@pytest.mark.live
def test_live_roaming_retrieval(retriever_module, monkeypatch):
    real_dir = REPO_ROOT / "data" / "knowledge"
    if not (real_dir / "index" / "manifest.json").exists():
        pytest.skip("no built index in data/knowledge (run python -m backend.knowledge.build)")
    retriever = _fresh_retriever(retriever_module, monkeypatch, real_dir)
    retriever.warm_up()
    if not retriever.status().get("loaded"):
        pytest.skip(f"index did not load: {retriever.status()}")
    context = retriever.context_for("What roaming packages does SLT Mobitel have for travelling abroad?")
    assert context, "a roaming question should retrieve context"
    assert "sltmobitel.lk" in context.lower() or "http" in context
    assert "roam" in context.lower()
    assert retriever.status()["last_latency_ms"] is not None


# Scraper URL policy (no network)


@pytest.fixture(scope="module")
def scraper():
    module = optional_import("knowledge.scraper")
    if module is None:
        pytest.skip("knowledge.scraper not delivered yet")
    return module


def test_scraper_stays_on_site(scraper):
    assert scraper.canonical_url("https://www.sltmobitel.lk/roaming#top") == "https://www.sltmobitel.lk/roaming"
    assert scraper.canonical_url("http://sltmobitel.lk/roaming?utm_source=x") == "https://www.sltmobitel.lk/roaming"
    assert scraper.canonical_url("https://evil.example.com/roaming") is None
    assert scraper.canonical_url("mailto:someone@sltmobitel.lk") is None
    assert scraper.canonical_url("/roaming", base="https://www.sltmobitel.lk/") == "https://www.sltmobitel.lk/roaming"


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/cart",
        "/my-account/settings",
        "/checkout",
        "/selfcare/usage",
        "/payment",
        "/myslt",
        "/brochure.pdf",
        "/images/banner.jpg",
    ],
)
def test_scraper_skips_private_and_binary_pages(scraper, path):
    assert scraper.is_crawlable(f"https://www.sltmobitel.lk{path}") is False


def test_scraper_allows_ordinary_pages(scraper):
    assert scraper.is_crawlable("https://www.sltmobitel.lk/roaming") is True


def test_scraper_delay_is_polite():
    assert config.SCRAPER_DELAY_SECONDS >= 1.0


@pytest.mark.live
@pytest.mark.slow
def test_live_grounded_local_answer(retriever_module, monkeypatch):
    """Real index, real bge-m3 and real llama3.2 through Ollama: the
    retrieved context reaches the local system prompt and the model
    answers without an error."""
    import agent

    real_dir = REPO_ROOT / "data" / "knowledge"
    if not (real_dir / "index" / "manifest.json").exists():
        pytest.skip("no built index in data/knowledge")
    retriever = _fresh_retriever(retriever_module, monkeypatch, real_dir)
    retriever.warm_up()
    if not retriever.status().get("loaded") or retriever.status().get("error"):
        pytest.skip(f"retriever not usable: {retriever.status()}")
    seen = []
    real_post = agent.requests.post

    def _spy(url, json=None, **kwargs):
        seen.append(json or {})
        return real_post(url, json=json, **kwargs)

    monkeypatch.setattr(agent.requests, "post", _spy)
    orchestrator = agent.Orchestrator(tool_callback=retriever.context_for)
    reply = orchestrator.respond("What roaming packages does SLT Mobitel offer?", None, "en")
    if reply.startswith("Error:"):
        pytest.skip(f"local model unavailable: {reply}")
    assert orchestrator.last_mode == "local single agent (Llama via Ollama)"
    assert any("sltmobitel.lk" in payload.get("system", "") for payload in seen)
    assert reply.strip()
