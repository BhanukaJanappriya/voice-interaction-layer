"""End to end: the LLM fallback chain (plan part 3, R1) through POST /chat
and GET /health.

The fakes sit at the level the tiers wrap, not inside the chain: the
GeminiAgent and LiveKitInferenceAgent classes get a scripted
respond_stream, and the local tier talks to the fake Ollama of conftest.
So the same fakes work before and after the lead wires llm_chain into
agent.Orchestrator, and the tests describe behaviour, not internals.

Nothing here uses the network, Ollama or a real key.

Detection: every chain test needs the llm_chain package AND the wiring
(agent.py mentions llm_chain). Until both exist the tests skip with the
reason; once they exist the tests run and are strict.

Other test modules reuse the `llm_env` fixture from here to prove that a
blocked guest never reaches any model.
"""

import inspect
import json
import time

import pytest

import agent
import config
from conftest import make_client, optional_import, require_module, server_source

GEMINI_MODE = "cloud (gemini)"
LOCAL_MODE = "local single agent (Llama via Ollama)"

GEMINI_REPLY = "Gemini says hello."
LIVEKIT_REPLY = "LiveKit says hello."
LOCAL_REPLY = "Local says hello."


def livekit_mode():
    return f"livekit inference ({config.LIVEKIT_LLM_MODEL})"


class FakeApiError(Exception):
    """What a provider SDK raises: an HTTP style status on the exception."""

    def __init__(self, status, message="fake provider error"):
        super().__init__(f"{status}: {message}")
        self.status_code = status
        self.code = status


class LlmControl:
    """Scripts and records the three tiers.

    behaviour[name] is one of
      ("ok",)                    streams the tier's reply
      ("fail", status)           raises before any text (status None: a
                                 network style failure with no code)
      ("partial", status)        one piece of text, then raises
      ("safety",)                raises llm_chain.errors.SafetyBlocked
    """

    def __init__(self, fake_ollama):
        self.behaviour = {"gemini": ("ok",), "livekit": ("ok",)}
        self.replies = {"gemini": GEMINI_REPLY, "livekit": LIVEKIT_REPLY}
        self.calls = {"gemini": [], "livekit": []}
        self.fake_ollama = fake_ollama
        self.local_reply = LOCAL_REPLY
        self.orchestrator = None
        self._make_orchestrator = None
        self.server_module = None

    @property
    def local_calls(self):
        return self.fake_ollama.payloads

    def total_calls(self):
        return len(self.calls["gemini"]) + len(self.calls["livekit"]) + len(self.local_calls)

    def assert_no_model_called(self):
        assert self.calls["gemini"] == [], "the Gemini tier was called"
        assert self.calls["livekit"] == [], "the LiveKit tier was called"
        assert self.local_calls == [], "the local model was called"

    def rebuild(self):
        """A fresh Orchestrator (fresh breakers) installed as the server's."""
        self.orchestrator = self._make_orchestrator()
        return self.orchestrator

    def client(self):
        return make_client(self.server_module.app)


def _cloud_error(name, status):
    error = agent.CloudUnavailableError(f"{name} call failed ({status})")
    if status is None:
        return error
    error.__cause__ = FakeApiError(status)
    return error


def _make_stream(control, name):
    def respond_stream(self, text_input, *args, **kwargs):
        control.calls[name].append(text_input)
        kind = control.behaviour[name]
        if kind[0] == "safety":
            errors = require_module("llm_chain.errors")
            try:
                raise errors.SafetyBlocked("blocked by the provider's safety filter")
            except TypeError:
                raise errors.SafetyBlocked()
        if kind[0] == "fail":
            raise _cloud_error(name, kind[1])
        if kind[0] == "partial":
            yield "Partial answer "
            raise _cloud_error(name, kind[1])
        yield control.replies[name]

    return respond_stream


@pytest.fixture
def llm_env(server_module, fake_ollama, monkeypatch):
    """Gemini and LiveKit configured, scripted, recording; local through
    the fake Ollama; a fresh Orchestrator installed as server.orchestrator."""
    livekit_inference = optional_import("livekit_inference")
    if livekit_inference is None:
        pytest.skip("livekit_inference could not be imported")

    control = LlmControl(fake_ollama)
    control.server_module = server_module
    fake_ollama.state["reply"] = lambda payload: control.local_reply

    for cls, name in ((agent.GeminiAgent, "gemini"), (livekit_inference.LiveKitInferenceAgent, "livekit")):
        stream = _make_stream(control, name)
        monkeypatch.setattr(cls, "respond_stream", stream)
        monkeypatch.setattr(cls, "respond", lambda self, text, _s=stream: "".join(_s(self, text)).strip())

    monkeypatch.setattr(config, "GEMINI_API_KEY", "fake-gemini-key-for-tests")
    monkeypatch.setattr(config, "CLOUD_LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://fake.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "fake-livekit-key")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "fake-livekit-secret-0123456789abcdef")
    monkeypatch.setattr(config, "LLM_CHAIN", ["gemini", "livekit", "local"], raising=False)
    monkeypatch.setattr(config, "LLM_BREAKER_SECONDS", 300, raising=False)
    monkeypatch.setattr(config, "CLOUD_REASONING_STAGE", False)

    def _make():
        orchestrator = agent.Orchestrator()
        monkeypatch.setattr(orchestrator.local_agent, "tool_callback", None)
        monkeypatch.setattr(server_module, "orchestrator", orchestrator)
        return orchestrator

    control._make_orchestrator = _make
    control.rebuild()
    return control


@pytest.fixture
def chain_env(llm_env):
    """llm_env, but only once llm_chain exists and agent.py uses it."""
    require_module("llm_chain", "backend/llm_chain not delivered yet")
    require_module("llm_chain.chain", "llm_chain.chain not delivered yet")
    if "llm_chain" not in inspect.getsource(agent):
        pytest.skip("agent.Orchestrator is not wired to llm_chain yet (lead integration pending)")
    return llm_env


def chat(control, text="Tell me about SLT fibre broadband packages", language="en"):
    client = control.client()
    try:
        response = client.post("/chat", json={"text": text, "language": language})
    finally:
        client.close()
    assert response.status_code == 200, response.text
    return response.json()


def health(control):
    client = control.client()
    try:
        response = client.get("/health")
    finally:
        client.close()
    assert response.status_code == 200, response.text
    report = response.json()
    if "llm_chain" not in report and "llm_chain" not in server_source():
        pytest.skip("/health does not report llm_chain yet (server.py wiring pending)")
    return report


def translate(key, language):
    """The backend string for a key, or None when i18n is not delivered."""
    module = optional_import("i18n")
    if module is None or not hasattr(module, "t"):
        return None
    value = module.t(key, language)
    return None if (not value or value == key) else value


# The healthy path


def test_all_tiers_up_uses_gemini_and_keeps_the_chat_contract(chain_env):
    body = chat(chain_env)
    assert set(body) == {"reply", "mode"}
    assert body["reply"] == GEMINI_REPLY
    assert body["mode"] == GEMINI_MODE
    assert len(chain_env.calls["gemini"]) == 1
    assert chain_env.calls["livekit"] == []
    assert chain_env.local_calls == []


# Each tier failing in turn


FAILURES = [
    (401, "auth"),  # expired or revoked key
    (403, "auth"),
    (402, "quota"),  # credits used up
    (429, "quota"),
    (404, "not_found"),
    (500, "outage"),
    (503, "outage"),
    (None, "network"),
]


@pytest.mark.parametrize("status,reason", FAILURES)
def test_gemini_failure_falls_to_livekit(chain_env, status, reason):
    chain_env.behaviour["gemini"] = ("fail", status)
    body = chat(chain_env)
    assert body["reply"] == LIVEKIT_REPLY
    assert body["mode"] == livekit_mode()
    assert len(chain_env.calls["gemini"]) == 1
    assert chain_env.local_calls == []

    tier = health(chain_env)["llm_chain"]["tiers"]["gemini"]
    assert tier["state"] == "tripped", tier
    assert tier["retry_in"] > 0, "the breaker window should be reported"
    assert tier["last_error"], "/health should say why the tier is tripped"
    if status is not None:
        assert str(status) in tier["last_error"], tier
    assert health(chain_env)["llm_chain"]["tiers"]["livekit"]["state"] == "ready"


def test_expired_key_401_is_not_retried_on_the_next_turn(chain_env):
    chain_env.behaviour["gemini"] = ("fail", 401)
    chat(chain_env)
    second = chat(chain_env)
    assert second["mode"] == livekit_mode()
    assert len(chain_env.calls["gemini"]) == 1, "an open breaker must stop calls to a dead key"
    assert len(chain_env.calls["livekit"]) == 2


@pytest.mark.parametrize("status", [402, 429])
def test_quota_errors_fall_through_both_cloud_tiers_to_local(chain_env, status):
    chain_env.behaviour["gemini"] = ("fail", status)
    chain_env.behaviour["livekit"] = ("fail", status)
    body = chat(chain_env)
    assert body["reply"] == LOCAL_REPLY
    assert body["mode"] == LOCAL_MODE
    assert len(chain_env.calls["gemini"]) == 1
    assert len(chain_env.calls["livekit"]) == 1
    assert len(chain_env.local_calls) == 1


def test_gemini_without_a_key_is_skipped_not_called(chain_env, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    chain_env.rebuild()
    body = chat(chain_env)
    assert body["mode"] == livekit_mode()
    assert body["reply"] == LIVEKIT_REPLY
    assert chain_env.calls["gemini"] == []


def test_livekit_unconfigured_and_gemini_down_ends_on_local(chain_env, monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "")
    chain_env.behaviour["gemini"] = ("fail", 503)
    chain_env.rebuild()
    body = chat(chain_env)
    assert body["mode"] == LOCAL_MODE
    assert body["reply"] == LOCAL_REPLY
    assert chain_env.calls["livekit"] == []


def test_a_broken_livekit_tier_does_not_disturb_a_healthy_gemini(chain_env):
    chain_env.behaviour["livekit"] = ("fail", 500)
    body = chat(chain_env)
    assert body["mode"] == GEMINI_MODE
    assert chain_env.calls["livekit"] == []


def test_local_tier_ignores_its_breaker_so_an_answer_always_exists(chain_env):
    chain_env.behaviour["gemini"] = ("fail", 429)
    chain_env.behaviour["livekit"] = ("fail", 402)
    modes = [chat(chain_env)["mode"] for _ in range(3)]
    assert modes == [LOCAL_MODE] * 3
    assert len(chain_env.local_calls) == 3
    assert len(chain_env.calls["gemini"]) == 1, "gemini's breaker stays open"
    assert len(chain_env.calls["livekit"]) == 1, "livekit's breaker stays open"


def test_breaker_window_ends_and_the_tier_is_tried_again(chain_env, monkeypatch):
    if not hasattr(config, "LLM_BREAKER_SECONDS"):
        pytest.skip("config.LLM_BREAKER_SECONDS not delivered yet")
    monkeypatch.setattr(config, "LLM_BREAKER_SECONDS", 1)
    chain_env.rebuild()
    chain_env.behaviour["gemini"] = ("fail", 503)
    assert chat(chain_env)["mode"] == livekit_mode()
    chain_env.behaviour["gemini"] = ("ok",)
    time.sleep(1.4)
    assert chat(chain_env)["mode"] == GEMINI_MODE
    assert len(chain_env.calls["gemini"]) == 2


# Failure after text started, and safety


def test_failure_after_text_started_apologises_and_does_not_switch_tier(chain_env):
    chain_env.behaviour["gemini"] = ("partial", 503)
    body = chat(chain_env)
    assert body["reply"].startswith("Partial answer")
    assert len(body["reply"]) > len("Partial answer"), "a short apology should follow the partial text"
    assert LIVEKIT_REPLY not in body["reply"] and LOCAL_REPLY not in body["reply"]
    assert chain_env.calls["livekit"] == []
    assert chain_env.local_calls == []


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_safety_block_ends_the_turn_and_never_falls_back(chain_env, language):
    chain_env.behaviour["gemini"] = ("safety",)
    body = chat(chain_env, language=language)
    assert body["reply"].strip(), "the user gets a notice, not silence"
    for text in (GEMINI_REPLY, LIVEKIT_REPLY, LOCAL_REPLY):
        assert text not in body["reply"]
    assert len(chain_env.calls["gemini"]) == 1
    assert chain_env.calls["livekit"] == [], "a refusal must never be retried on another provider"
    assert chain_env.local_calls == []
    expected = translate("llm.safety_blocked", language)
    if expected is not None:
        assert expected in body["reply"]


def test_safety_block_does_not_open_a_breaker_that_hides_the_next_turn(chain_env):
    chain_env.behaviour["gemini"] = ("safety",)
    chat(chain_env)
    chain_env.behaviour["gemini"] = ("ok",)
    body = chat(chain_env)
    assert body["reply"] == GEMINI_REPLY, "a safety refusal is about one message, not the provider's health"


# /health


def test_health_reports_the_chain_and_keeps_the_old_keys(chain_env):
    report = health(chain_env)
    for key in (
        "status",
        "cloud_llm_configured",
        "cloud_llm_provider",
        "cloud_llm_model",
        "cloud_llm_last_error",
        "cloud_speech_configured",
        "supported_languages",
    ):
        assert key in report, f"/health lost the existing key {key}"
    assert "llm_chain" in report
    chain = report["llm_chain"]
    assert isinstance(chain, (dict, list)) and chain, "llm_chain should describe the tiers"
    assert list(chain["order"]) == ["gemini", "livekit", "local"]
    assert set(chain["tiers"]) == {"gemini", "livekit", "local"}
    for tier in chain["tiers"].values():
        assert {"label", "configured", "state", "last_error", "retry_in"} <= set(tier)
    assert chain["tiers"]["gemini"]["label"] == GEMINI_MODE
    assert chain["tiers"]["livekit"]["label"] == livekit_mode()
    assert chain["tiers"]["local"]["label"] == LOCAL_MODE
    secrets = [config.JWT_SECRET, config.GEMINI_API_KEY, config.LIVEKIT_API_SECRET]
    assert not any(secret and secret in json.dumps(report) for secret in secrets)


def test_health_shows_a_tripped_tier_then_a_healthy_one(chain_env):
    chain_env.behaviour["gemini"] = ("fail", 402)
    chat(chain_env)
    report = health(chain_env)["llm_chain"]
    assert report["tiers"]["gemini"]["state"] == "tripped"
    assert report["last_mode"] == livekit_mode()
    chain_env.rebuild()
    fresh = health(chain_env)["llm_chain"]
    assert fresh["tiers"]["gemini"]["state"] == "ready"
    assert fresh["tiers"]["gemini"]["last_error"] is None


def test_health_without_any_traffic_does_not_call_a_model(chain_env):
    health(chain_env)
    chain_env.assert_no_model_called()
