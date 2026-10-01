"""End to end over /ws/voice with the real server code and fake speech
(scripted transcripts in, recorded sentences out)."""

import json

import numpy as np
import pytest

from conftest import USER_NAME, app_paths, make_client, orchestrator_integrated, server_source

FACT = "QA-FACT: the Roaming Saver pack costs Rs 999 for 7 days."
ONE_SECOND = np.zeros(16000, dtype=np.float32).tobytes()
HALF_SECOND = np.zeros(8000, dtype=np.float32).tobytes()
MAX_MESSAGES = 200


def _receive_until(ws, predicate):
    """Collects messages until one matches; fails instead of looping
    forever if the server keeps talking without ever matching."""
    seen = []
    for _ in range(MAX_MESSAGES):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(f"no matching message in {seen!r}")


def _is_state(state):
    return lambda m: m.get("type") == "status" and m.get("state") == state


def _start(ws, **fields):
    payload = {"type": "start_session", "language_hint": "en", "user_name": None, "greet": True}
    payload.update(fields)
    ws.send_text(json.dumps(payload))


def _greeting(ws):
    message, _ = _receive_until(ws, lambda m: m.get("type") in ("agent_response", "error"))
    assert message["type"] == "agent_response", message
    assert message["mode"] == "Introduction"
    _receive_until(ws, _is_state("calibrating"))
    return message["text"]


@pytest.fixture
def ws_client(server_module, fake_speech):
    c = make_client(server_module.app)
    yield c
    c.close()


def test_guest_flow_unchanged(ws_client, fake_speech):
    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, user_name=None, greet=True)
        text = _greeting(ws)
    assert "Guest" in text
    assert text in fake_speech.spoken, "the introduction is spoken through TTS"


def test_client_name_used_without_verified_session(ws_client):
    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, user_name="Kamal")
        text = _greeting(ws)
    assert "Kamal" in text


def test_greeting_in_selected_language(ws_client):
    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, language_hint="si")
        text = _greeting(ws)
    assert "අමුත්තා" in text


def test_invalid_start_message_reports_error(ws_client):
    with ws_client.websocket_connect("/ws/voice") as ws:
        ws.send_text("not json")
        message = ws.receive_json()
    assert message["type"] == "error"


def _verified_login(server_module, login_user):
    if "/auth/verify" not in app_paths(server_module.app):
        pytest.skip("/auth routes not integrated into server.app yet")
    c = make_client(server_module.app)
    body = login_user(c)
    cookie = c.cookies.get("user_session")
    c.close()
    return body["token"], cookie


def _skip_if_not_wired(text):
    if "Impostor" in text and "session_token" not in server_source():
        pytest.skip("start_session does not accept session_token yet (plan R4 wiring pending)")


def test_verified_user_greeting_uses_database_name(ws_client, server_module, login_user):
    token, _ = _verified_login(server_module, login_user)
    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, user_name="Impostor", session_token=token)
        text = _greeting(ws)
    _skip_if_not_wired(text)
    assert USER_NAME in text
    assert "Impostor" not in text


def test_verified_user_via_cookie_on_handshake(ws_client, server_module, login_user):
    _, cookie = _verified_login(server_module, login_user)
    assert cookie, "login should set the user_session cookie"
    with ws_client.websocket_connect("/ws/voice", headers={"cookie": f"user_session={cookie}"}) as ws:
        _start(ws, user_name="Impostor")
        text = _greeting(ws)
    _skip_if_not_wired(text)
    assert USER_NAME in text


def test_bad_session_token_falls_back_to_guest_path(ws_client, server_module):
    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, user_name=None, session_token="garbage")
        message, _ = _receive_until(ws, lambda m: m.get("type") in ("agent_response", "error"))
    # Either refused with an error or greeted as Guest; never as someone else.
    if message["type"] == "agent_response":
        assert "Guest" in message["text"]


def test_retrieval_grounded_answer(ws_client, server_module, fake_speech, fake_ollama, monkeypatch):
    orchestrator = server_module.orchestrator
    fake_callback = lambda text: FACT  # noqa: E731
    monkeypatch.setattr(orchestrator, "tool_callback", fake_callback)
    monkeypatch.setattr(orchestrator.local_agent, "tool_callback", fake_callback)
    if hasattr(orchestrator, "external_agent_provider"):
        monkeypatch.setattr(orchestrator, "external_agent_provider", lambda: None)
    fake_ollama.state["reply"] = lambda payload: (
        "Grounded: the fact was in my system prompt."
        if FACT in payload.get("system", "")
        else ("Context only in the user prompt." if FACT in payload.get("prompt", "") else "No context.")
    )
    fake_speech.transcripts = ["how much does the roaming pack cost"]

    with ws_client.websocket_connect("/ws/voice") as ws:
        _start(ws, greet=False)
        _receive_until(ws, _is_state("calibrating"))
        ws.send_bytes(ONE_SECOND)
        _receive_until(ws, _is_state("listening"))
        ws.send_bytes(HALF_SECOND)
        final, _ = _receive_until(ws, lambda m: m.get("type") == "final_transcript")
        assert final["text"] == "how much does the roaming pack cost"
        response, seen = _receive_until(ws, lambda m: m.get("type") in ("agent_response", "error"))
        assert response["type"] == "agent_response", response
        _receive_until(ws, lambda m: m.get("type") == "speaking_done")
        _receive_until(ws, _is_state("listening"))
        ws.send_text(json.dumps({"type": "end_session"}))

    assert any(m.get("type") == "agent_response_chunk" for m in seen)
    assert response["mode"] == "local single agent (Llama via Ollama)"
    assert fake_ollama.payloads, "the local model was called"
    if response["text"].startswith("Context only") and not orchestrator_integrated():
        pytest.skip("retrieved context still goes into the local user prompt (plan R1 wiring pending)")
    assert response["text"] == "Grounded: the fact was in my system prompt."
    # The voice path may speak the first clause early (SentenceChunker's
    # first clause cut), so compare the spoken text as a whole.
    assert " ".join(fake_speech.spoken) == "Grounded: the fact was in my system prompt."


def test_retrieval_uses_real_retriever_wiring(server_module):
    """After integration the server's Orchestrator is built with the
    retriever's context_for as its tool_callback."""
    if optional_retriever() is None:
        pytest.skip("knowledge.retriever not delivered yet")
    callback = server_module.orchestrator.tool_callback
    if callback is None:
        if not orchestrator_integrated():
            pytest.skip("Orchestrator not wired to the retriever yet (plan R1 wiring pending)")
        pytest.fail("server.orchestrator has no tool_callback after integration")
    assert callable(callback)
    # Missing index in the test data directory: the wired callback must
    # quietly return None, never raise into the turn.
    assert callback("hello") is None


def optional_retriever():
    from conftest import optional_import

    return optional_import("knowledge.retriever")
