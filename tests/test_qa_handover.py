"""QA: the hand over between voice tiers.

1. /ws/voice with handover=true says the localized "voice.reconnecting" notice
   instead of the introduction, in en, si and ta, keeping language and voice style.
2. The LiveKit token endpoint accepts handover and signs attribute handover=true;
   the worker turns the attributes into SessionSettings.handover.
3. A pending confirmation belongs to the customer, not to the voice tier, so it
   survives a tier change: build_handover summarises it without the token and the
   same token still accepts afterwards, also after a /ws/voice handover session.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from conftest import BACKEND_DIR, app_paths, make_client

LANGS = ["en", "si", "ta"]


def _i18n(language):
    data = json.loads((Path(BACKEND_DIR) / "i18n" / f"{language}.json").read_text(encoding="utf-8"))
    return data["voice.reconnecting"]


def _receive_until(ws, predicate, limit=100):
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(f"no matching message in {seen!r}")


@pytest.fixture
def spoken(server_module, monkeypatch):
    """Records every (text, language, voice_style) the server asks the speaker to say."""
    calls = []

    def speak_text(text, language="en", voice_style="female_natural", on_sentence_start=None):
        calls.append((text, language, voice_style))
        if on_sentence_start is not None:
            on_sentence_start(text)

    monkeypatch.setattr(server_module, "speak_text", speak_text)
    return calls


def _start(client, **fields):
    payload = {"type": "start_session", "language_hint": "en", "user_name": None, "greet": True}
    payload.update(fields)
    ws_cm = client.websocket_connect("/ws/voice")
    ws = ws_cm.__enter__()
    ws.send_text(json.dumps(payload))
    return ws_cm, ws


def _introduction(client, **fields):
    ws_cm, ws = _start(client, **fields)
    try:
        response, _ = _receive_until(ws, lambda m: m.get("type") == "agent_response")
        _receive_until(ws, lambda m: m.get("type") == "speaking_done")
    finally:
        ws_cm.__exit__(None, None, None)
    return response


# 1. /ws/voice


@pytest.fixture
def client(server_module):
    c = make_client(server_module.app)
    yield c
    c.close()


@pytest.mark.parametrize("language", LANGS)
def test_handover_speaks_the_localized_notice_not_the_introduction(client, spoken, language):
    notice = _i18n(language)
    response = _introduction(client, language_hint=language, handover=True, voice_style="male")
    assert response["text"] == notice
    assert response["mode"] == "Introduction"
    assert spoken == [(notice, language, "male")]


@pytest.mark.parametrize("language", LANGS)
def test_without_handover_the_introduction_is_unchanged(client, spoken, language):
    response = _introduction(client, language_hint=language)
    assert response["text"] != _i18n(language)
    assert "SLT" in response["text"] or "Mobitel" in response["text"] or len(response["text"]) > len(_i18n(language))
    assert spoken and spoken[0][0] == response["text"]


@pytest.mark.parametrize("style", ["female_natural", "male"])
def test_handover_keeps_the_voice_style(client, spoken, style):
    _introduction(client, language_hint="si", handover=True, voice_style=style)
    assert spoken[0][1:] == ("si", style)


def test_handover_with_greet_false_still_says_the_notice(client, spoken):
    response = _introduction(client, language_hint="ta", handover=True, greet=False)
    assert response["text"] == _i18n("ta")


@pytest.mark.parametrize("language", [None, "fr", ""])
def test_handover_with_an_unknown_language_falls_back_to_english(client, spoken, language):
    response = _introduction(client, language_hint=language, handover=True)
    assert response["text"] == _i18n("en")
    assert spoken[0][1] == "en"


@pytest.mark.parametrize("value", ["true", 1, "yes"])
def test_only_a_real_true_counts_as_handover(client, spoken, value):
    response = _introduction(client, language_hint="en", handover=value)
    assert response["text"] != _i18n("en")


def test_the_notice_is_one_sentence_and_not_the_name_greeting(client, spoken):
    response = _introduction(client, language_hint="en", handover=True, user_name="Kamal")
    assert "Kamal" not in response["text"]


# 2. LiveKit


LK_URL = "wss://qa.livekit.example"
LK_KEY = "APIqakey123"
LK_SECRET = "qa-lk-secret-0123456789-abcdefghijklmnopqrstuvwxyz-SECRET"


@pytest.fixture
def lk(server_module, monkeypatch):
    pytest.importorskip("livekit.api", reason="livekit-api is not installed")
    if "/api/livekit/token" not in app_paths(server_module.app):
        pytest.skip("the LiveKit router is not mounted")
    monkeypatch.setattr(config, "LIVEKIT_URL", LK_URL)
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", LK_KEY)
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", LK_SECRET)
    c = make_client(server_module.app)
    yield c
    c.close()


def _claims(token):
    import jwt

    return jwt.decode(token, options={"verify_signature": False, "verify_aud": False})


def test_token_with_handover_signs_the_attribute(lk):
    body = lk.post("/api/livekit/token", json={"language": "si", "voice_style": "male", "handover": True}).json()
    attributes = _claims(body["token"])["attributes"]
    assert attributes["handover"] == "true"
    assert attributes["language"] == "si" and attributes["voice_style"] == "male"
    assert LK_SECRET not in json.dumps(body)


def test_token_without_handover_has_no_attribute(lk):
    body = lk.post("/api/livekit/token", json={"language": "en"}).json()
    assert "handover" not in _claims(body["token"])["attributes"]
    body = lk.post("/api/livekit/token", json={"language": "en", "handover": False}).json()
    assert "handover" not in _claims(body["token"])["attributes"]


def test_a_non_boolean_handover_is_rejected(lk):
    assert lk.post("/api/livekit/token", json={"handover": "maybe"}).status_code == 422


@pytest.mark.parametrize("language", LANGS)
def test_worker_settings_carry_handover_from_the_token(lk, language):
    pytest.importorskip("livekit.agents", reason="livekit-agents is not installed")
    from livekit_transport import worker

    body = lk.post("/api/livekit/token", json={"language": language, "voice_style": "male", "handover": True}).json()
    participant = SimpleNamespace(attributes=_claims(body["token"])["attributes"], identity="guest-x")
    settings = worker._settings_from_participant(participant)
    assert settings.handover is True
    assert settings.language == language and settings.voice_style == "male"
    assert settings.greet is True, "greet must stay true so the notice is spoken"


def test_worker_settings_default_to_no_handover():
    pytest.importorskip("livekit.agents", reason="livekit-agents is not installed")
    from livekit_transport import worker

    settings = worker._settings_from_participant(SimpleNamespace(attributes={"language": "en"}, identity="g"))
    assert settings.handover is False
    assert worker.SessionSettings().handover is False


# 3. The pending confirmation survives a tier change

pytest.importorskip("mcp", reason="the MCP SDK is not installed")
from test_e2e_hub_only import (  # noqa: E402,F401  (the fixtures are used by name)
    KNOWN_E164,
    NAME,
    env,
    hub_names,
    llm_env,
    mock_hub,
    post_chat,
    verified_client,
)


def _pending_customer(env, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    web = verified_client(env)
    body = post_chat(web, "buy extra 10GB")
    assert "confirmation" in body, body
    return web, body["confirmation"]


def _access():
    from access import AccessContext

    return AccessContext.verified(1, NAME, KNOWN_E164)


def test_build_handover_summarises_the_pending_action_without_its_token(env, monkeypatch):
    from voice_router import build_handover

    web, shown = _pending_customer(env, monkeypatch)
    state = build_handover(language="si", voice_style="male", access=_access(), turns=[("user", "buy extra 10GB")])
    pending = state.pending_confirmation
    assert pending is not None
    assert pending["tool"] == "purchase_extra_gb"
    assert pending["title"] == shown["title"]
    assert pending["lines"] == shown["lines"]
    assert shown["id"] not in json.dumps(pending)
    assert "id" not in pending
    assert state.language == "si" and state.voice_style == "male"
    assert state.access is not None


def test_the_same_token_still_accepts_after_the_handover_is_built(env, monkeypatch):
    from voice_router import build_handover

    web, shown = _pending_customer(env, monkeypatch)
    build_handover(language="en", access=_access())
    assert web.get("/confirm/pending").json()["confirmation"]["id"] == shown["id"]
    assert "purchase_extra_gb" not in hub_names(env)
    response = web.post(f"/confirm/{shown['id']}/accept")
    assert response.status_code == 200 and response.json()["kind"] == "executed", response.text
    assert len([1 for name, _ in env.hub.tool_calls if name == "purchase_extra_gb"]) == 1


def test_a_handover_voice_session_does_not_touch_the_pending_action(env, fake_speech, monkeypatch):
    web, shown = _pending_customer(env, monkeypatch)
    token = web.cookies.get("user_session")
    assert token
    ws_cm = web.websocket_connect("/ws/voice")
    ws = ws_cm.__enter__()
    try:
        ws.send_text(json.dumps({
            "type": "start_session", "language_hint": "en", "greet": True, "handover": True,
            "session_token": token, "voice_style": "female_natural",
        }))
        response, _ = _receive_until(ws, lambda m: m.get("type") == "agent_response")
        assert response["text"] == _i18n("en")
        _receive_until(ws, lambda m: m.get("type") == "speaking_done")
    finally:
        ws_cm.__exit__(None, None, None)
    assert web.get("/confirm/pending").json()["confirmation"]["id"] == shown["id"]
    assert "purchase_extra_gb" not in hub_names(env)
    assert web.post(f"/confirm/{shown['id']}/accept").json()["kind"] == "executed"


def test_the_pending_action_is_per_customer_so_another_session_cannot_take_it(env, monkeypatch):
    web, shown = _pending_customer(env, monkeypatch)
    stranger = env.new_client()
    assert stranger.post(f"/confirm/{shown['id']}/accept").status_code == 404
    assert web.get("/confirm/pending").json()["confirmation"]["id"] == shown["id"]


def test_a_guest_handover_has_no_pending_action(env):
    from access import GUEST
    from voice_router import build_handover

    state = build_handover(language="en", access=GUEST)
    assert state.pending_confirmation is None


def test_an_expired_pending_action_is_not_carried(env, monkeypatch):
    from voice_router import build_handover

    web, shown = _pending_customer(env, monkeypatch)
    env.confirm_clock.now += 61
    assert build_handover(language="en", access=_access()).pending_confirmation is None
