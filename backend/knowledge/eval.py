"""Evaluates the knowledge base: retrieval hit rate, answer quality with
and without retrieval, and retrieval latency. Writes docs/RAG_EVAL.md.

Method:
    Questions come from eval_questions.jsonl; each has expected facts
    (short strings such as a price "159", a code "#999#" or a year
    "1993", with alternatives) and expected source URL substrings, all
    taken from content the crawler and the public sources actually hold.

    Retrieval hit@k: a question is a hit when one of the top k chunks
    comes from an expected source or contains every expected fact.
    Context hit: the same test on what context_for() actually passes to
    the model (after RETRIEVAL_MIN_SCORE and the relative margin).

    Answer quality: the local model (config.LOCAL_LLM_MODEL through
    Ollama /api/generate, stream false, temperature 0 so runs are
    repeatable) answers every question twice:
        baseline    today's production system prompt (voice persona and
                    reply language), no retrieval, no grounding rules;
        retrieval   the same prompt plus knowledge/prompts.py's grounding
                    rules and the context_for() block.
    Score = share of expected facts present in the answer (after light
    normalization: case, spaces and thousands separators ignored).

    "Negative" questions (small talk, another operator) are expected to
    get no context at all (context_for returns None).

    Latency: every question is searched with the query cache cleared (so
    each measurement includes the bge-m3 embedding call), several passes,
    in a process that has opted out of Windows power throttling (EcoQoS),
    like the server does. Cold numbers: index load plus first query in a
    fresh retriever, and optionally (--cold-model) the first query after
    Ollama has unloaded bge-m3.

Run: python -m backend.knowledge.eval [--no-answers] [--cold-model] [--passes N]
"""

import argparse
import ctypes
import json
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

import config
from knowledge.index import MANIFEST_PATH
from knowledge.prompts import with_context
from knowledge.retriever import KnowledgeRetriever, format_context, select_relevant

QUESTIONS_PATH = Path(__file__).resolve().parent / "eval_questions.jsonl"
REPORT_PATH = config.PROJECT_ROOT / "docs" / "RAG_EVAL.md"
RESULTS_PATH = config.KNOWLEDGE_DIR / "eval_results.json"

try:  # the exact production wording, when agent.py imports cleanly
    from agent import _VOICE_ASSISTANT_SYSTEM_PROMPT as VOICE_PROMPT
    from agent import _personalize as personalize
except Exception:  # agent.py mid edit elsewhere: verbatim copy of it
    VOICE_PROMPT = (
        "You are a concise voice assistant. Answer only what the user actually "
        "asked, nothing more and nothing less. Keep replies short and natural, "
        "since they will be spoken aloud rather than read as text. Do not add "
        "disclaimers, caveats, or commentary about being an AI. The user's "
        "message is a live speech to text transcript, so it may be missing "
        "punctuation or have imperfect grammar or phrasing; never comment on, "
        "correct, or apologize for that, just answer what was actually meant. "
        "Usually one to three sentences is enough."
    )
    _LANG = {
        "si": "Always reply in Sinhala (සිංහල), written in Sinhala script, even if the user's message "
              "contains English words. Keep product names like SLT Mobitel as they are.",
        "ta": "Always reply in Tamil (தமிழ்), written in Tamil script, even if the user's message "
              "contains English words. Keep product names like SLT Mobitel as they are.",
        "en": "Always reply in English.",
    }

    def personalize(prompt, user_name, language=None):
        prompt = f"{prompt} The user has not shared their name. If you address them directly, call them Guest."
        if language in _LANG:
            prompt += " " + _LANG[language]
        return prompt


def opt_out_of_power_throttling() -> None:
    """Same call as server.py's _opt_out_of_windows_power_throttling:
    processes started from a background shell on the development machine
    run on efficiency cores at reduced clock, which would inflate the
    latency numbers compared with the real server."""
    if sys.platform != "win32":
        return

    class _State(ctypes.Structure):
        _fields_ = [("Version", ctypes.c_ulong), ("ControlMask", ctypes.c_ulong), ("StateMask", ctypes.c_ulong)]

    state = _State(1, 0x1, 0)
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
        kernel32.SetProcessInformation(kernel32.GetCurrentProcess(), 4, ctypes.byref(state), ctypes.sizeof(state))
    except Exception as exc:
        print(f"Power throttling opt out failed: {exc}")


def _norm(text: str) -> str:
    text = text.lower().replace("‍", "")
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)
    return re.sub(r"[\s\-()]+", "", text)


def fact_present(fact, text: str) -> bool:
    """fact is a string or a list of alternative strings."""
    alternatives = fact if isinstance(fact, list) else [fact]
    haystack = _norm(text)
    return any(_norm(alt) in haystack for alt in alternatives)


def facts_score(facts: list, text: str) -> float:
    if not facts:
        return 1.0
    return sum(1 for f in facts if fact_present(f, text)) / len(facts)


def load_questions() -> list:
    with QUESTIONS_PATH.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _source_hit(expected_sources: list, url: str) -> bool:
    return any(s in url for s in expected_sources or [])


def generate(system: str, prompt: str, session: requests.Session) -> tuple:
    started = time.perf_counter()
    response = session.post(
        f"{config.LOCAL_LLM_HOST.rstrip('/')}/api/generate",
        json={"model": config.LOCAL_LLM_MODEL, "system": system, "prompt": prompt, "stream": False,
              "keep_alive": "30m", "options": {"temperature": 0, "num_predict": 220}},
        timeout=180,
    )
    response.raise_for_status()
    return response.json().get("response", "").strip(), (time.perf_counter() - started) * 1000


def percentile(values: list, pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    index = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
    return ordered[index]


def measure_cold(cold_model: bool, index_dir=None) -> dict:
    result = {}
    if cold_model:
        # Ask Ollama to unload bge-m3 now (keep_alive 0), then time the
        # first query, which includes loading the model again.
        try:
            requests.post(f"{config.EMBEDDING_HOST.rstrip('/')}/api/embed",
                          json={"model": config.EMBEDDING_MODEL, "input": "x", "keep_alive": 0}, timeout=60)
            time.sleep(2)
        except requests.RequestException:
            pass
    fresh = KnowledgeRetriever(index_dir=index_dir)
    started = time.perf_counter()
    fresh.load(force=True)
    load_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    fresh.search("How do I activate roaming?")
    first_ms = (time.perf_counter() - started) * 1000
    result["index_load_ms"] = round(load_ms, 1)
    result["first_query_ms" + ("_model_unloaded" if cold_model else "")] = round(first_ms, 1)
    return result


def run(answers: bool = True, cold_model: bool = False, passes: int = 3, index_dir=None,
        label: str = "default") -> dict:
    opt_out_of_power_throttling()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    questions = load_questions()
    top_k = config.RETRIEVAL_TOP_K

    cold = measure_cold(cold_model, index_dir)
    retriever = KnowledgeRetriever(index_dir=index_dir)
    retriever.warm_up()
    if not retriever.loaded:
        raise SystemExit(f"Index not available: {retriever.error}")

    # Latency: every search pays the embedding (query cache cleared).
    latencies = []
    for _ in range(passes):
        for q in questions:
            retriever._query_cache.clear()
            started = time.perf_counter()
            retriever.search(q["question"], top_k=top_k)
            latencies.append((time.perf_counter() - started) * 1000)
    # context_for when only the final context is uncached: the query
    # embedding is still in the cache from the passes above, which is the
    # cloud to local fallback case within one turn.
    retriever._context_cache.clear()
    started = time.perf_counter()
    retriever.context_for(questions[0]["question"])
    cached_embedding_ms = (time.perf_counter() - started) * 1000

    session = requests.Session()
    rows = []
    for q in questions:
        retriever._context_cache.clear()
        results = retriever.search(q["question"], top_k=top_k)
        selected = select_relevant(results)
        context = format_context(selected, q["question"])
        facts = q.get("expected_facts", [])
        sources = q.get("expected_sources", [])
        rank = None
        for i, r in enumerate(results, 1):
            if _source_hit(sources, r.source_url) or (facts and facts_score(facts, r.text) == 1.0):
                rank = i
                break
        top_facts = facts_score(facts, "\n".join(r.text for r in results)) if facts else None
        context_text = context or ""
        row = {
            "id": q["id"],
            "lang": q["lang"],
            "category": q["category"],
            "question": q["question"],
            "top_score": round(results[0].score, 3) if results else None,
            "top_source": results[0].source_url if results else None,
            "hit_rank": rank,
            "hit": rank is not None,
            "facts_in_top_k": top_facts,
            "context_returned": context is not None,
            "context_chunks": len(selected),
            "context_facts": facts_score(facts, context_text) if facts else None,
            "context_source_hit": any(_source_hit(sources, r.source_url) for r in selected),
        }
        if answers and q["category"] != "negative":
            base_system = personalize(VOICE_PROMPT, None, q["lang"])
            try:
                baseline, base_ms = generate(base_system, q["question"], session)
                grounded, grounded_ms = generate(with_context(base_system, context), q["question"], session)
            except requests.RequestException as exc:
                raise SystemExit(f"Local model unavailable: {exc}")
            row.update({
                "baseline_answer": baseline,
                "baseline_score": facts_score(facts, baseline),
                "baseline_ms": round(base_ms),
                "rag_answer": grounded,
                "rag_score": facts_score(facts, grounded),
                "rag_ms": round(grounded_ms),
            })
        rows.append(row)
        print(f"{q['id']}: hit={row['hit']} rank={rank} top={row['top_score']} context={row['context_returned']}"
              + (f" base={row.get('baseline_score'):.2f} rag={row.get('rag_score'):.2f}" if 'rag_score' in row else ""))

    manifest_path = Path(index_dir) / "manifest.json" if index_dir else MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    summary = summarize(rows, latencies, cold, cached_embedding_ms, top_k, manifest)
    summary["label"] = label
    summary["crawled_hosts"] = manifest.get("crawled_hosts")
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.with_name(f"eval_results_{label}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, ensure_ascii=False),
                            encoding="utf-8")
    write_report(summary, rows, label)
    print(json.dumps(summary, indent=1))
    return summary


def _mean(values):
    values = [v for v in values if v is not None]
    return round(statistics.mean(values), 3) if values else None


def summarize(rows, latencies, cold, cached_ms, top_k, manifest) -> dict:
    positives = [r for r in rows if r["category"] != "negative"]
    negatives = [r for r in rows if r["category"] == "negative"]
    by_lang = {}
    for lang in ("en", "si", "ta"):
        subset = [r for r in positives if r["lang"] == lang]
        if subset:
            by_lang[lang] = {
                "questions": len(subset),
                "hit_rate": _mean([1.0 if r["hit"] else 0.0 for r in subset]),
                "context_fact_rate": _mean([r["context_facts"] for r in subset]),
                "baseline_score": _mean([r.get("baseline_score") for r in subset]),
                "rag_score": _mean([r.get("rag_score") for r in subset]),
            }
    return {
        "run_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "index_built_at": manifest.get("built_at"),
        "index_counts": manifest.get("counts"),
        "embedding_model": manifest.get("model"),
        "llm": config.LOCAL_LLM_MODEL,
        "top_k": top_k,
        "min_score": config.RETRIEVAL_MIN_SCORE,
        "questions": len(rows),
        "positive_questions": len(positives),
        "hit_rate": _mean([1.0 if r["hit"] else 0.0 for r in positives]),
        "hit_at_1": _mean([1.0 if r["hit_rank"] == 1 else 0.0 for r in positives]),
        "context_returned_rate": _mean([1.0 if r["context_returned"] else 0.0 for r in positives]),
        "context_fact_rate": _mean([r["context_facts"] for r in positives]),
        "negatives_without_context": sum(1 for r in negatives if not r["context_returned"]),
        "negatives": len(negatives),
        "baseline_score": _mean([r.get("baseline_score") for r in positives]),
        "rag_score": _mean([r.get("rag_score") for r in positives]),
        "baseline_all_facts": _mean([1.0 if r.get("baseline_score") == 1.0 else 0.0 for r in positives
                                     if "baseline_score" in r]),
        "rag_all_facts": _mean([1.0 if r.get("rag_score") == 1.0 else 0.0 for r in positives if "rag_score" in r]),
        "by_language": by_lang,
        "latency_ms": {
            "samples": len(latencies),
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "max": round(max(latencies), 1) if latencies else None,
            "context_for_with_cached_embedding": round(cached_ms, 2) if cached_ms is not None else None,
            **cold,
        },
        "answer_ms": {
            "baseline_p50": percentile([r["baseline_ms"] for r in positives if "baseline_ms" in r], 50),
            "rag_p50": percentile([r["rag_ms"] for r in positives if "rag_ms" in r], 50),
        },
    }


def _cell(text: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", text or "").replace("|", "/")
    return text if len(text) <= limit else text[:limit - 3] + "..."


def write_report(summary: dict, rows: list, label: str = "default") -> None:
    """The per question table and the numbers. The prose sections of
    RAG_EVAL.md (method notes, interpretation) live between the markers
    and are preserved; only the generated block is replaced."""
    generated = [
        "<!-- generated by python -m backend.knowledge.eval; do not edit by hand -->",
        f"Run {summary['run_at']}, index built {summary['index_built_at']}, embedding model "
        f"{summary['embedding_model']}, answer model {summary['llm']}, top_k {summary['top_k']}, "
        f"RETRIEVAL_MIN_SCORE {summary['min_score']}, crawled hosts {summary.get('crawled_hosts')}.",
        "",
        "| Measure | Value |",
        "| --- | --- |",
        f"| Questions (with expected facts / negative) | {summary['positive_questions']} / {summary['negatives']} |",
        f"| Retrieval hit@{summary['top_k']} | {summary['hit_rate']:.0%} |",
        f"| Retrieval hit@1 | {summary['hit_at_1']:.0%} |",
        f"| Context returned by context_for | {summary['context_returned_rate']:.0%} |",
        f"| Expected facts present in the context | {summary['context_fact_rate']:.0%} |",
        f"| Negative questions with no context (correct) | {summary['negatives_without_context']} of {summary['negatives']} |",
    ]
    if summary.get("rag_score") is not None:
        generated += [
            f"| Answer fact score, no retrieval baseline | {summary['baseline_score']:.0%} |",
            f"| Answer fact score, with retrieval | {summary['rag_score']:.0%} |",
            f"| Answers with every expected fact, baseline / retrieval | "
            f"{summary['baseline_all_facts']:.0%} / {summary['rag_all_facts']:.0%} |",
            f"| Answer time p50, baseline / retrieval (full reply, not streamed) | "
            f"{summary['answer_ms']['baseline_p50']:.0f} ms / {summary['answer_ms']['rag_p50']:.0f} ms |",
        ]
    lat = summary["latency_ms"]
    generated += [
        f"| Retrieval latency warm, p50 / p95 ({lat['samples']} searches, embedding included) | "
        f"{lat['p50']:.0f} ms / {lat['p95']:.0f} ms |",
        f"| context_for with the query embedding cached | {lat['context_for_with_cached_embedding']} ms |",
        f"| Cold: index load | {lat.get('index_load_ms')} ms |",
    ]
    if "first_query_ms" in lat:
        generated.append(f"| Cold: first query in a fresh retriever (model already loaded) | {lat['first_query_ms']:.0f} ms |")
    if "first_query_ms_model_unloaded" in lat:
        generated.append(f"| Cold: first query after Ollama unloaded bge-m3 | {lat['first_query_ms_model_unloaded']:.0f} ms |")
    generated += ["", "By language:", "", "| Language | Questions | hit@k | Facts in context | Baseline | Retrieval |",
                  "| --- | --- | --- | --- | --- | --- |"]
    for lang, s in summary["by_language"].items():
        base = f"{s['baseline_score']:.0%}" if s["baseline_score"] is not None else "n/a"
        rag = f"{s['rag_score']:.0%}" if s["rag_score"] is not None else "n/a"
        generated.append(f"| {lang} | {s['questions']} | {s['hit_rate']:.0%} | {s['context_fact_rate']:.0%} | {base} | {rag} |")
    generated += ["", "Per question:", "",
                  "| id | lang | category | question | top score | hit rank | context | facts in context | "
                  "baseline | retrieval | retrieval answer (truncated) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        def pct(v):
            return f"{v:.0%}" if isinstance(v, (int, float)) else "n/a"
        generated.append(
            f"| {r['id']} | {r['lang']} | {r['category']} | {_cell(r['question'], 80)} | {r['top_score']} | "
            f"{r['hit_rank'] or '-'} | {'yes' if r['context_returned'] else 'none'} | {pct(r['context_facts'])} | "
            f"{pct(r.get('baseline_score'))} | {pct(r.get('rag_score'))} | {_cell(r.get('rag_answer', ''))} |")
    block = "\n".join(generated)

    start_marker, end_marker = f"<!-- EVAL RESULTS START {label} -->", f"<!-- EVAL RESULTS END {label} -->"
    if REPORT_PATH.exists():
        existing = REPORT_PATH.read_text(encoding="utf-8")
        if start_marker in existing and end_marker in existing:
            before = existing.split(start_marker)[0]
            after = existing.split(end_marker)[1]
            REPORT_PATH.write_text(f"{before}{start_marker}\n{block}\n{end_marker}{after}", encoding="utf-8")
            return
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = REPORT_PATH.read_text(encoding="utf-8") if REPORT_PATH.exists() else "# RAG evaluation\n"
    REPORT_PATH.write_text(f"{existing.rstrip()}\n\n{start_marker}\n{block}\n{end_marker}\n", encoding="utf-8")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval and grounded answers.")
    parser.add_argument("--no-answers", action="store_true", help="retrieval and latency only (no LLM calls)")
    parser.add_argument("--cold-model", action="store_true", help="also time a query after unloading bge-m3")
    parser.add_argument("--passes", type=int, default=3, help="latency passes over the question set")
    parser.add_argument("--index-dir", default=None, help="evaluate another index directory")
    parser.add_argument("--label", default="default", help="name of this run's block in RAG_EVAL.md")
    args = parser.parse_args(argv)
    run(answers=not args.no_answers, cold_model=args.cold_model, passes=args.passes,
        index_dir=args.index_dir, label=args.label)


if __name__ == "__main__":
    main()
