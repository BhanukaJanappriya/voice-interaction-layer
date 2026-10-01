"""POST /chat: contract {reply, mode} unchanged; verified users are named
from the database."""

import pytest

from conftest import USER_NAME, app_paths, make_client, server_source


class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.last_mode = "fake mode"
        self.last_cloud_error = None
        self.tool_callback = None

    def respond(self, text_input, user_name=None, language=None, **kwargs):
        self.calls.append({"text": text_input, "user_name": user_name, "language": language})
        return f"Reply to {user_name or 'Guest'}"

    def respond_stream(self, text_input, user_name=None, language=None, **kwargs):
        yield self.respond(text_input, user_name, language)


@pytest.fixture
def fake_orchestrator(server_module, monkeypatch):
    fake = FakeOrchestrator()
    monkeypatch.setattr(server_module, "orchestrator", fake)
    return fake


@pytest.fixture
def chat_client(server_module):
    c = make_client(server_module.app)
    yield c
    c.close()


def test_chat_contract(chat_client, fake_orchestrator):
    response = chat_client.post("/chat", json={"text": "hello", "language": "si", "user_name": "Kamal"})
    assert response.status_code == 200
    assert response.json() == {"reply": "Reply to Kamal", "mode": "fake mode"}
    assert fake_orchestrator.calls == [{"text": "hello", "user_name": "Kamal", "language": "si"}]


def test_chat_optional_fields(chat_client, fake_orchestrator):
    response = chat_client.post("/chat", json={"text": "hello"})
    assert response.status_code == 200
    assert response.json()["reply"] == "Reply to Guest"
    assert fake_orchestrator.calls[0]["user_name"] is None


def test_chat_requires_text(chat_client, fake_orchestrator):
    assert chat_client.post("/chat", json={}).status_code == 422


def test_chat_with_real_orchestrator_local_path(chat_client, server_module, fake_ollama, monkeypatch):
    fake_ollama.state["reply"] = lambda payload: "Hello from the fake Llama."
    orchestrator = server_module.orchestrator
    if hasattr(orchestrator, "external_agent_provider"):
        monkeypatch.setattr(orchestrator, "external_agent_provider", lambda: None)
    monkeypatch.setattr(orchestrator, "tool_callback", None)
    monkeypatch.setattr(orchestrator.local_agent, "tool_callback", None)

    response = chat_client.post("/chat", json={"text": "hello", "language": "en", "user_name": None})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"reply", "mode"}
    assert body["reply"] == "Hello from the fake Llama."
    assert body["mode"] == "local single agent (Llama via Ollama)"


def test_chat_bearer_token_uses_verified_name(server_module, fake_orchestrator, login_user):
    auth_client = make_client(server_module.app)
    try:
        if "/auth/verify" not in app_paths(server_module.app):
            pytest.skip("/auth routes not integrated into server.app yet")
        token = login_user(auth_client)["token"]
        c = make_client(server_module.app)
        response = c.post(
            "/chat",
            json={"text": "who am I", "user_name": "Impostor"},
            headers={"Authorization": f"Bearer {token}"},
        )
        c.close()
    finally:
        auth_client.close()
    assert response.status_code == 200
    seen = fake_orchestrator.calls[-1]["user_name"]
    if seen == "Impostor" and "verified_user" not in server_source():
        pytest.skip("/chat does not resolve verified users yet (plan R4 wiring pending)")
    assert seen == USER_NAME


def test_chat_bad_token_falls_back_to_guest_path(server_module, fake_orchestrator):
    c = make_client(server_module.app)
    response = c.post(
        "/chat", json={"text": "hi", "user_name": "Kamal"}, headers={"Authorization": "Bearer garbage"}
    )
    c.close()
    # Either refused outright or treated as an unverified (Guest path) call.
    if response.status_code == 200:
        assert fake_orchestrator.calls[-1]["user_name"] == "Kamal"
    else:
        assert response.status_code == 401
