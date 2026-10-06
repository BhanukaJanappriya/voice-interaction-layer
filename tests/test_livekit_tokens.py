"""LiveKit join tokens and transport config: claims, TTL, grants, 503 when
unconfigured, identity for guests and verified users, no secret in any
response. Also: /ws/voice keeps working with VOICE_TRANSPORT unset."""

import json
import logging

import pytest
from fastapi import FastAPI

import config
from conftest import BASE_URL, USER_PHONE_RAW, USER_PIN, make_client, require_module

LK_URL = "wss://test-project.livekit.example"
LK_KEY = "APItestkey123"
LK_SECRET = "lk-secret-0123456789-abcdefghijklmnopqrstuvwxyz-SECRET"


@pytest.fixture(scope="module")
def tokens_module():
    pytest.importorskip("livekit.api", reason="livekit-api is not installed")
    return require_module("livekit_transport.tokens", "backend/livekit_transport not delivered yet")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_URL", LK_URL)
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", LK_SECRET)
    monkeypatch.setattr(config, "LIVEKIT_TOKEN_TTL_SECONDS", 600)
    monkeypatch.setattr(config, "LIVEKIT_AGENT_NAME", "voice-assistant")


@pytest.fixture
def unconfigured(monkeypatch):
    for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        monkeypatch.setattr(config, name, "")


@pytest.fixture
def app(tokens_module):
    application = FastAPI()
    application.include_router(tokens_module.router)
    from auth.routes import router as auth_router

    application.include_router(auth_router)
    return application


@pytest.fixture
def lk_client(app):
    c = make_client(app)
    yield c
    c.close()


def decode(token, secret=LK_SECRET):
    import jwt

    return jwt.decode(token, secret, algorithms=["HS256"], options={"verify_aud": False})


# Config endpoint


def test_config_reports_transport_without_secrets(lk_client, configured, monkeypatch):
    monkeypatch.setattr(config, "VOICE_TRANSPORT", "livekit")
    response = lk_client.get("/api/livekit/config")
    assert response.status_code == 200
    body = response.json()
    assert body["transport"] == "livekit"
    assert body["livekit_configured"] is True
    assert body["avatar_renderer"] == config.AVATAR_RENDERER
    for secret in (LK_SECRET, LK_KEY):
        assert secret not in response.text


def test_config_unconfigured(lk_client, unconfigured, monkeypatch):
    monkeypatch.setattr(config, "VOICE_TRANSPORT", "websocket")
    body = lk_client.get("/api/livekit/config").json()
    assert body["transport"] == "websocket"
    assert body["livekit_configured"] is False


def test_new_transport_and_avatar_are_the_defaults_in_the_code():
    # Read the defaults from the source: a developer's own backend/.env is
    # loaded by config.py and would otherwise decide the answer.
    from conftest import BACKEND_DIR

    text = (BACKEND_DIR / "config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("VOICE_TRANSPORT", "livekit")' in text
    assert 'os.environ.get("AVATAR_RENDERER", "talkinghead")' in text


# Token endpoint


def test_unconfigured_is_503_and_names_the_variables(lk_client, unconfigured):
    response = lk_client.post("/api/livekit/token", json={})
    assert response.status_code == 503
    assert "LIVEKIT" in response.json()["detail"]


@pytest.mark.parametrize("missing", ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"])
def test_any_missing_setting_is_503(lk_client, configured, monkeypatch, missing):
    monkeypatch.setattr(config, missing, "")
    assert lk_client.post("/api/livekit/token", json={}).status_code == 503


def test_response_shape(lk_client, configured):
    response = lk_client.post("/api/livekit/token", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"url", "token", "room", "identity", "expires_at"}
    assert body["url"] == LK_URL
    assert isinstance(body["expires_at"], int)


def test_token_is_signed_with_the_secret_and_names_the_key(lk_client, configured):
    import jwt

    token = lk_client.post("/api/livekit/token", json={}).json()["token"]
    claims = decode(token)
    assert claims["iss"] == LK_KEY
    with pytest.raises(jwt.InvalidSignatureError):
        decode(token, secret="some-other-secret-0123456789-abcdefghijkl")


def test_ttl_matches_config_and_expires_at(lk_client, configured, monkeypatch):
    import time

    monkeypatch.setattr(config, "LIVEKIT_TOKEN_TTL_SECONDS", 120)
    before = int(time.time())
    body = lk_client.post("/api/livekit/token", json={}).json()
    claims = decode(body["token"])
    assert claims["exp"] - claims["nbf"] == 120
    assert abs(claims["exp"] - (before + 120)) <= 5
    assert abs(body["expires_at"] - claims["exp"]) <= 5


def test_default_ttl_is_ten_minutes(lk_client, configured):
    claims = decode(lk_client.post("/api/livekit/token", json={}).json()["token"])
    assert claims["exp"] - claims["nbf"] == 600


def test_grants_allow_only_this_room_and_the_microphone(lk_client, configured):
    body = lk_client.post("/api/livekit/token", json={}).json()
    grants = decode(body["token"])["video"]
    assert grants["roomJoin"] is True
    assert grants["room"] == body["room"]
    assert grants["canPublish"] is True and grants["canSubscribe"] is True
    assert grants.get("canPublishSources") == ["microphone"]
    for forbidden in ("roomAdmin", "roomCreate", "roomList", "roomRecord", "hidden", "recorder", "canUpdateOwnMetadata"):
        assert not grants.get(forbidden), f"token must not grant {forbidden}"
    assert not grants.get("ingressAdmin") and not grants.get("agent")


def test_token_dispatches_the_configured_agent(lk_client, configured, monkeypatch):
    monkeypatch.setattr(config, "LIVEKIT_AGENT_NAME", "my-agent")
    claims = decode(lk_client.post("/api/livekit/token", json={}).json()["token"])
    assert claims["roomConfig"]["agents"] == [{"agentName": "my-agent"}]


def test_every_call_gets_a_fresh_random_room(lk_client, configured):
    rooms = {lk_client.post("/api/livekit/token", json={}).json()["room"] for _ in range(5)}
    assert len(rooms) == 5
    assert all(len(r) >= 12 for r in rooms)


def test_guest_identity_is_random_and_prefixed(lk_client, configured):
    a = lk_client.post("/api/livekit/token", json={"user_name": "Kamal"}).json()
    b = lk_client.post("/api/livekit/token", json={"user_name": "Kamal"}).json()
    assert a["identity"].startswith("guest-") and b["identity"].startswith("guest-")
    assert a["identity"] != b["identity"]
    assert decode(a["token"])["sub"] == a["identity"]


def test_guest_attributes_carry_language_voice_and_name(lk_client, configured):
    body = lk_client.post(
        "/api/livekit/token", json={"language": "si", "voice_style": "male", "user_name": "  Kamal   Silva "}
    ).json()
    claims = decode(body["token"])
    attrs = claims["attributes"]
    assert attrs["language"] == "si" and attrs["voice_style"] == "male"
    assert attrs["user_name"] == "Kamal Silva"
    assert attrs["verified"] == "false"
    assert claims.get("name") == "Kamal Silva"


def test_bad_language_and_voice_fall_back_to_defaults(lk_client, configured):
    body = lk_client.post("/api/livekit/token", json={"language": "xx", "voice_style": "robot"}).json()
    attrs = decode(body["token"])["attributes"]
    assert attrs["language"] == "" and attrs["voice_style"] == "female_natural"


def test_empty_body_and_no_body_are_a_guest(lk_client, configured):
    assert lk_client.post("/api/livekit/token", json={}).status_code == 200
    assert lk_client.post("/api/livekit/token").status_code in (200, 422)


def test_verified_user_identity_and_name_come_from_the_account(lk_client, configured, make_user):
    user_id = make_user()
    login = lk_client.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN})
    assert login.status_code == 200
    body = lk_client.post("/api/livekit/token", json={"user_name": "Somebody Else"}).json()
    claims = decode(body["token"])
    assert body["identity"] == f"user-{user_id}" == claims["sub"]
    assert claims["attributes"]["verified"] == "true"
    assert claims["attributes"]["user_name"] == "Nimal Perera", "the account name wins over the body"


def test_verified_user_by_session_token_in_the_body(app, configured, make_user):
    user_id = make_user()
    c1 = make_client(app)
    token = c1.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN}).json()["token"]
    c1.close()
    c2 = make_client(app)  # no cookie
    body = c2.post("/api/livekit/token", json={"session_token": token}).json()
    c2.close()
    assert body["identity"] == f"user-{user_id}"


def test_invalid_session_token_is_just_a_guest(lk_client, configured):
    body = lk_client.post("/api/livekit/token", json={"session_token": "not.a.token"}).json()
    assert body["identity"].startswith("guest-")


def test_secret_never_appears_in_responses_or_logs(lk_client, configured, caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        response = lk_client.post("/api/livekit/token", json={"user_name": "Kamal"})
        config_response = lk_client.get("/api/livekit/config")
    out = capsys.readouterr()
    token = response.json()["token"]
    for text in (response.text, config_response.text, caplog.text, out.out, out.err):
        assert LK_SECRET not in text
    assert token not in caplog.text + out.out + out.err, "the token itself must not be logged"
    payload_part = token.split(".")[1]
    assert LK_SECRET not in payload_part


def test_token_is_not_cacheable_or_at_least_not_shared(lk_client, configured):
    response = lk_client.post("/api/livekit/token", json={})
    cache = response.headers.get("cache-control", "")
    assert "public" not in cache


# The existing path is untouched


def test_ws_voice_still_works_with_voice_transport_unset(server_module, fake_speech, monkeypatch):
    monkeypatch.setattr(config, "VOICE_TRANSPORT", "websocket")
    client = make_client(server_module.app)
    try:
        with client.websocket_connect("/ws/voice") as ws:
            ws.send_text(json.dumps({"type": "start_session", "language_hint": "en", "user_name": None, "greet": True}))
            seen = []
            for _ in range(50):
                message = ws.receive_json()
                seen.append(message)
                if message.get("type") == "agent_response":
                    break
            assert seen[-1]["type"] == "agent_response" and seen[-1]["mode"] == "Introduction"
    finally:
        client.close()


def test_server_app_still_serves_the_legacy_routes(server_module):
    from conftest import app_paths

    paths = app_paths(server_module.app)
    for path in ("/ws/voice", "/chat", "/health", "/auth/verify"):
        assert path in paths, path


def test_livekit_routes_are_mounted_on_the_real_app_once_integrated(server_module):
    from conftest import app_paths

    paths = app_paths(server_module.app)
    if "/api/livekit/token" not in paths:
        pytest.skip("the lead has not mounted the LiveKit router in server.py yet")
    assert "/api/livekit/config" in paths
