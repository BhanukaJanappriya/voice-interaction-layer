"""Orchestrator: external agent first, fallback, and where retrieved
context ends up (local system prompt, cloud composer prompt)."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Ensure the project root is also available to the language server and test runner
# when this file is opened directly from VS Code.
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import pytest

import agent
import config  # type: ignore[import-not-found]
from conftest import orchestrator_integrated

LOCAL_MODE = "local single agent (Llama via Ollama)"
FACT = "QA-FACT: the Roaming Saver pack costs Rs 999 for 7 days."


def _require_provider_param():
    if not orchestrator_integrated():
        pytest.skip("Orchestrator(external_agent_provider=...) not integrated yet (plan R2 wiring)")


class FakeExternal(agent.ChatAgent):
    def __init__(self, pieces=("External ", "reply."), fail_before=False, fail_after=False, name="Partner Bot"):
        self.name = name
        self.pieces = pieces
        self.fail_before = fail_before
        self.fail_after = fail_after
        self.calls = []

    def respond(self, text_input, user_name=None, language=None):
        return "".join(self.respond_stream(text_input, user_name, language))

    def respond_stream(self, text_input, user_name=None, language=None):
        self.calls.append((text_input, user_name, language))
        error = getattr(agent, "ExternalAgentError", RuntimeError)
        if self.fail_before:
            raise error("partner down")
        yield from self.pieces
        if self.fail_after:
            raise error("partner dropped")


@pytest.fixture
def local_stub(monkeypatch):
    calls = []

    def _stream(self, text_input, user_name=None, language=None):
        calls.append((text_input, user_name, language))
        yield "Local "
        yield "answer."

    monkeypatch.setattr(agent.LlamaOllamaAgent, "respond_stream", _stream)
    return calls


def test_external_agent_used_and_mode_named(local_stub):
    _require_provider_param()
    external = FakeExternal()
    orchestrator = agent.Orchestrator(external_agent_provider=lambda: external)
    reply = "".join(orchestrator.respond_stream("hi", "Nimal", "en"))
    assert reply == "External reply."
    assert orchestrator.last_mode == "external agent (Partner Bot)"
    assert external.calls and external.calls[0][0] == "hi"
    assert local_stub == []


def test_respond_uses_external_agent_too(local_stub):
    _require_provider_param()
    orchestrator = agent.Orchestrator(external_agent_provider=lambda: FakeExternal())
    assert orchestrator.respond("hi") == "External reply."
    assert orchestrator.last_mode == "external agent (Partner Bot)"


def test_provider_returning_none_uses_normal_path(local_stub):
    _require_provider_param()
    orchestrator = agent.Orchestrator(external_agent_provider=lambda: None)
    assert "".join(orchestrator.respond_stream("hi")) == "Local answer."
    assert orchestrator.last_mode == LOCAL_MODE


def test_external_failure_before_text_falls_back_to_local(local_stub):
    _require_provider_param()
    if not hasattr(agent, "ExternalAgentError"):
        pytest.skip("agent.ExternalAgentError not delivered yet")
    orchestrator = agent.Orchestrator(external_agent_provider=lambda: FakeExternal(fail_before=True))
    reply = "".join(orchestrator.respond_stream("hi", "Nimal", "en"))
    assert reply == "Local answer."
    assert orchestrator.last_mode == LOCAL_MODE
    assert local_stub[0] == ("hi", "Nimal", "en")


def test_external_failure_after_text_apologizes_without_switching(local_stub):
    _require_provider_param()
    if not hasattr(agent, "ExternalAgentError"):
        pytest.skip("agent.ExternalAgentError not delivered yet")
    orchestrator = agent.Orchestrator(external_agent_provider=lambda: FakeExternal(fail_after=True))
    reply = "".join(orchestrator.respond_stream("hi"))
    assert reply.startswith("External reply.")
    assert len(reply) > len("External reply."), "an apology should follow the partial reply"
    assert "Local answer." not in reply
    assert local_stub == []


def test_provider_itself_raising_does_not_break_the_turn(local_stub):
    _require_provider_param()

    def _provider():
        raise RuntimeError("database unavailable")

    orchestrator = agent.Orchestrator(external_agent_provider=_provider)
    try:
        reply = "".join(orchestrator.respond_stream("hi"))
    except RuntimeError:
        pytest.fail("a failing external agent provider should fall back, not break the turn")
    assert reply == "Local answer."


def test_tool_context_reaches_local_system_prompt(fake_ollama):
    orchestrator = agent.Orchestrator(tool_callback=lambda text: FACT)
    "".join(orchestrator.respond_stream("how much is roaming?", "Nimal", "en"))
    assert orchestrator.last_mode == LOCAL_MODE
    payload = fake_ollama.payloads[-1]
    if FACT not in payload.get("system", "") and not orchestrator_integrated():
        pytest.skip("retrieved context still goes into the local user prompt (plan R1 wiring pending)")
    assert FACT in payload["system"]
    assert FACT not in payload["prompt"], "context should move to the system prompt, not be duplicated"
    assert "how much is roaming?" in payload["prompt"]


def test_tool_context_reaches_local_system_prompt_non_streaming(fake_ollama):
    orchestrator = agent.Orchestrator(tool_callback=lambda text: FACT)
    orchestrator.local_agent.respond("how much is roaming?", "Nimal", "en")
    payload = fake_ollama.payloads[-1]
    if FACT not in payload.get("system", "") and not orchestrator_integrated():
        pytest.skip("retrieved context still goes into the local user prompt (plan R1 wiring pending)")
    assert FACT in payload["system"]


def test_no_context_keeps_prompt_plain(fake_ollama):
    orchestrator = agent.Orchestrator(tool_callback=lambda text: None)
    "".join(orchestrator.respond_stream("hello"))
    payload = fake_ollama.payloads[-1]
    assert payload["prompt"] == "hello"


def test_failing_tool_callback_is_ignored(fake_ollama):
    def _boom(text):
        raise RuntimeError("index broken")

    orchestrator = agent.Orchestrator(tool_callback=_boom)
    assert "".join(orchestrator.respond_stream("hello")) == "Local reply."


class FakeCloud(agent.ChatAgent):
    created = []

    def __init__(self, role_prompt):
        self.role_prompt = role_prompt
        self.inputs = []
        FakeCloud.created.append(self)

    def respond(self, text_input):
        return "".join(self.respond_stream(text_input))

    def respond_stream(self, text_input):
        self.inputs.append(text_input)
        yield "Cloud answer."


@pytest.fixture
def fake_cloud(monkeypatch):
    FakeCloud.created = []
    monkeypatch.setattr(config, "cloud_llm_configured", lambda: True)
    monkeypatch.setattr(agent, "_build_cloud_agent", lambda role_prompt=None: FakeCloud(role_prompt))
    return FakeCloud


def test_tool_context_reaches_cloud_composer_prompt(fake_cloud, fake_ollama):
    orchestrator = agent.Orchestrator(tool_callback=lambda text: FACT)
    reply = "".join(orchestrator.respond_stream("how much is roaming?", "Nimal", "en"))
    assert reply == "Cloud answer."
    assert orchestrator.last_mode == f"cloud multi agent ({config.CLOUD_LLM_PROVIDER})"
    composer = fake_cloud.created[-1]
    seen = composer.inputs[-1] + "\n" + (composer.role_prompt or "")
    assert FACT in seen
    assert "how much is roaming?" in composer.inputs[-1]
    assert fake_ollama.payloads == []


def test_cloud_failure_falls_back_to_local_with_context(monkeypatch, fake_ollama):
    class Failing(FakeCloud):
        def respond_stream(self, text_input):
            raise agent.CloudUnavailableError("down")
            yield  # pragma: no cover

    monkeypatch.setattr(config, "cloud_llm_configured", lambda: True)
    monkeypatch.setattr(agent, "_build_cloud_agent", lambda role_prompt=None: Failing(role_prompt))
    orchestrator = agent.Orchestrator(tool_callback=lambda text: FACT)
    reply = "".join(orchestrator.respond_stream("how much is roaming?"))
    assert reply == "Local reply."
    assert orchestrator.last_mode == LOCAL_MODE
    payload = fake_ollama.payloads[-1]
    assert FACT in payload.get("system", "") + payload.get("prompt", "")
