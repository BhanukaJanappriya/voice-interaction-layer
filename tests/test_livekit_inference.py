"""LiveKit Inference as the cloud language model: it answers first, and when it
is not configured or fails the local Ollama model answers alone.

The gateway is never called here: the OpenAI client is replaced by a fake.
"""

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

import agent
import config  # type: ignore[import-not-found]

pytest.importorskip("livekit.api", reason="livekit-api is not installed")
inference = importlib.import_module("livekit_inference")

LOCAL_MODE = "local single agent (Llama via Ollama)"


def chunk(text=None, no_choices=False):
    if no_choices:
        return SimpleNamespace(choices=[])
    delta = SimpleNamespace(content=text)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class FakeClient:
    def __init__(self, events=None, error=None):
        self.events = events or []
        self.error = error
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return iter(self.events)


@pytest.fixture
def livekit_on(monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://test.livekit.example")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APItestkey123")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "lk-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setattr(config, "CLOUD_LLM_PROVIDER", "livekit")
    inference._reset_client()
    yield
    inference._reset_client()


def use_client(monkeypatch, client):
    monkeypatch.setattr(inference, "_get_client", lambda: client)


def test_streams_text_and_skips_chunks_without_text(livekit_on, monkeypatch):
    client = FakeClient([chunk("The hotline "), chunk(None), chunk(no_choices=True), chunk("is 1717.")])
    use_client(monkeypatch, client)
    reply = inference.LiveKitInferenceAgent(system_prompt="Be brief.").respond("hotline?")
    assert reply == "The hotline is 1717."
    call = client.calls[0]
    assert call["model"] == config.LIVEKIT_LLM_MODEL
    assert call["stream"] is True
    assert call["max_tokens"] == config.LIVEKIT_LLM_MAX_TOKENS
    assert call["messages"][0] == {"role": "system", "content": "Be brief."}
    assert call["messages"][-1] == {"role": "user", "content": "hotline?"}


def test_any_failure_is_a_cloud_unavailable_error_without_the_token(livekit_on, monkeypatch):
    class Boom(Exception):
        status_code = 402

    use_client(monkeypatch, FakeClient(error=Boom("secret-token-value should not be repeated")))
    with pytest.raises(agent.CloudUnavailableError) as caught:
        inference.LiveKitInferenceAgent().respond("hi")
    assert "credit" in str(caught.value).lower()
    assert "secret-token-value" not in str(caught.value)


def test_not_configured_is_cloud_unavailable(monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setattr(config, name, "")
    inference._reset_client()
    with pytest.raises(agent.CloudUnavailableError):
        inference.LiveKitInferenceAgent().respond("hi")


def test_token_carries_the_inference_grant_and_no_room(livekit_on):
    import jwt

    claims = jwt.decode(inference._make_token(), options={"verify_signature": False})
    assert claims["iss"] == "APItestkey123"
    assert claims["inference"] == {"perform": True}
    # Only the right to call the gateway: no room, so it cannot join anything.
    assert "video" not in claims


def test_provider_is_selected_by_the_factory_and_by_cloud_llm_configured(livekit_on):
    assert config.cloud_llm_configured() is True
    built = agent._build_cloud_agent(role_prompt="x")
    assert isinstance(built, inference.LiveKitInferenceAgent)


def test_cloud_llm_is_not_configured_without_livekit_keys(monkeypatch):
    monkeypatch.setattr(config, "CLOUD_LLM_PROVIDER", "livekit")
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setattr(config, name, "")
    assert config.cloud_llm_configured() is False


@pytest.fixture
def local_stub(monkeypatch):
    def _stream(self, text_input, user_name=None, language=None):
        yield "Local "
        yield "answer."

    monkeypatch.setattr(agent.LlamaOllamaAgent, "respond_stream", _stream)


def test_orchestrator_uses_livekit_first(livekit_on, local_stub, monkeypatch):
    use_client(monkeypatch, FakeClient([chunk("From "), chunk("LiveKit.")]))
    orchestrator = agent.Orchestrator()
    reply = "".join(orchestrator.respond_stream("hello", None, "en"))
    assert reply == "From LiveKit."
    assert orchestrator.last_mode == "cloud multi agent (livekit)"


def test_orchestrator_falls_back_to_local_ollama_only_when_livekit_fails(livekit_on, local_stub, monkeypatch):
    use_client(monkeypatch, FakeClient(error=RuntimeError("gateway down")))
    orchestrator = agent.Orchestrator()
    reply = "".join(orchestrator.respond_stream("hello", None, "en"))
    assert reply == "Local answer."
    assert orchestrator.last_mode == LOCAL_MODE
    assert "LiveKit Inference" in (orchestrator.last_cloud_error or "")
    # The breaker then skips LiveKit for a while instead of waiting on it again.
    again = "".join(orchestrator.respond_stream("hello again", None, "en"))
    assert again == "Local answer."


def test_orchestrator_uses_local_alone_when_livekit_is_not_configured(local_stub, monkeypatch):
    monkeypatch.setattr(config, "CLOUD_LLM_PROVIDER", "livekit")
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setattr(config, name, "")
    orchestrator = agent.Orchestrator()
    assert "".join(orchestrator.respond_stream("hello", None, "en")) == "Local answer."
    assert orchestrator.last_mode == LOCAL_MODE
