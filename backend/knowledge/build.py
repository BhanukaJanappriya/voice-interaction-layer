"""One command to (re)build the knowledge base: scrape, normalize, index.

    python -m backend.knowledge.build                  full run (incremental scrape)
    python -m backend.knowledge.build --skip-scrape    rebuild from the page cache only
    python -m backend.knowledge.build --max-pages 50   smaller crawl while developing
    python -m backend.knowledge.build --no-include-legacy   www.sltmobitel.lk only (no package prices)

Both official hosts, www.sltmobitel.lk and www.mobitel.lk, are used by
default; see scraper.py for why the second one is needed.

The scrape is incremental (see scraper.py), so a re-run mostly re-checks
pages; the index is always rebuilt from scratch, which takes well under a
minute at this size. The curated FAQ (faq.jsonl) and the public sources
(sources/public_sources.jsonl) are committed files and are read, never
overwritten. A running server picks up the new index on its next restart
(or through KnowledgeRetriever.load(force=True)).
"""

import argparse
import sys
import time
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge import normalize, scraper
from knowledge.embeddings import EmbeddingUnavailableError
from knowledge.index import build_index


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the SLT-Mobitel knowledge base.")
    parser.add_argument("--skip-scrape", action="store_true", help="use the cached pages as they are")
    parser.add_argument("--max-pages", type=int, default=None, help="page cap for the crawl")
    parser.add_argument("--include-legacy", action=argparse.BooleanOptionalAction, default=True,
                        help="also use the second official host www.mobitel.lk (default on; --no-include-legacy keeps to www.sltmobitel.lk only)")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    if not args.skip_scrape:
        print("== Scrape")
        scraper.Crawler(max_pages=args.max_pages, include_legacy=args.include_legacy).run()
    print("== Normalize")
    docs = normalize.run(include_legacy=args.include_legacy)
    print("== Index")
    try:
        manifest = build_index(docs, hosts=list(scraper.active_hosts(args.include_legacy)))
    except EmbeddingUnavailableError as exc:
        print(f"Index not built: {exc}")
        print("Start Ollama and pull the model (ollama pull bge-m3), then rerun with --skip-scrape.")
        return 1
    counts = manifest["counts"]
    print(f"Knowledge base built: {counts['documents']} documents, {counts['chunks']} chunks "
          f"({counts['chunks_by_kind']}), {counts['packages']} packages, {counts['faq_entries']} FAQ entries")
    print(f"Build latency: {time.perf_counter() - started:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
