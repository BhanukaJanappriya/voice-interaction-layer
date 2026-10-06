"""LiveKit is tried first and the page falls back when its agent is not running.

GET /api/livekit/config reports agent_ready, and POST /api/livekit/token refuses
(503) while the worker is starting, retrying or stopped, so the page uses
/ws/voice and the local pipeline instead of joining a silent room.
"""

import pytest
from fastapi import FastAPI

import config
from conftest import make_client, require_module

LK_URL = "wss://test-project.livekit.example"


@pytest.fixture(scope="module")
def tokens_module():
    pytest.importorskip("livekit.api", reason="livekit-api is not installed")
    return require_module("livekit_transport.tokens", "backend/livekit_transport not delivered yet")


@pytest.fixture
def client(tokens_module, monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", LK_URL)
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", "APItestkey123")
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "lk-secret-0123456789-abcdefghijklmnopqrstuvwxyz")
    monkeypatch.setattr(config, "VOICE_TRANSPORT", "livekit")
    app = FastAPI()
    app.include_router(tokens_module.router)
    c = make_client(app)
    yield c
    c.close()
    tokens_module.set_agent_status_provider(None)


def test_agent_ready_when_this_process_runs_no_worker(client, tokens_module):
    tokens_module.set_agent_status_provider(None)
    assert client.get("/api/livekit/config").json()["agent_ready"] is True


def test_agent_ready_when_the_worker_is_running(client, tokens_module):
    tokens_module.set_agent_status_provider(lambda: {"state": "running"})
    assert client.get("/api/livekit/config").json()["agent_ready"] is True
    assert client.post("/api/livekit/token", json={}).status_code == 200


@pytest.mark.parametrize("state", ["starting", "retrying", "stopped", "not_configured"])
def test_not_ready_and_no_token_while_the_worker_is_not_running(client, tokens_module, state):
    tokens_module.set_agent_status_provider(lambda: {"state": state})
    assert client.get("/api/livekit/config").json()["agent_ready"] is False
    response = client.post("/api/livekit/token", json={})
    assert response.status_code == 503
    assert "agent" in response.json()["detail"].lower()


def test_a_failing_status_provider_means_not_ready(client, tokens_module):
    def broken():
        raise RuntimeError("boom")

    tokens_module.set_agent_status_provider(broken)
    assert client.get("/api/livekit/config").json()["agent_ready"] is False
