"""SLT-Mobitel knowledge base: scraper, normalizer, embedding index and the
retriever the Orchestrator uses as its tool_callback.

Modules import each other and the rest of the backend by bare name
(import config, from knowledge.index import ...), the same way the other
backend modules do, because backend/__init__.py puts backend/ on sys.path.
Each module is also runnable on its own, from the repository root with
python -m backend.knowledge.<module>, or from inside backend/knowledge.
"""
