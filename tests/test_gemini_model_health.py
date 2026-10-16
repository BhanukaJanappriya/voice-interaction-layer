"""The Gemini text model is a current alias, and /health says why Gemini is not answering."""

import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_the_model_default_is_a_current_alias_not_a_retired_pinned_name():
    out = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, 'backend'); import config; print(config.GEMINI_MODEL)"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items() if k != "GEMINI_MODEL"},
    )
    model = out.stdout.strip().splitlines()[-1]
    assert model.endswith("-latest") or model == "gemini-flash-latest"
    assert "GEMINI_MODEL=gemini-flash-latest" in open(os.path.join(ROOT, ".env.example"), encoding="utf-8").read()


def test_health_reports_the_error_of_the_gemini_tier_in_the_chain(monkeypatch):
    import config
    import server

    class FakeChain:
        def status(self):
            return {"tiers": {"gemini": {"last_error": "out of credit or rate limited"}, "livekit": {"last_error": None}}}

    monkeypatch.setattr(config, "LLM_CHAIN", ["gemini", "livekit", "local"])
    monkeypatch.setattr(type(server.orchestrator), "chain", property(lambda self: FakeChain()))
    assert server._chain_error("gemini") == "out of credit or rate limited"
    assert server._chain_error("livekit") is None
    assert server._chain_error("gpt") is None


def test_the_health_error_helper_never_raises(monkeypatch):
    import config
    import server

    monkeypatch.setattr(config, "LLM_CHAIN", ["gemini"])
    monkeypatch.setattr(type(server.orchestrator), "chain", property(lambda self: object()))
    assert server._chain_error("gemini") is None
