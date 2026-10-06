"""voicekit HTTP and WebSocket API: key auth, rate limit, size limit,
formats, and no secrets or text in logs. Synthesis is replaced by scripted
events, so no provider is needed."""

import base64
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from conftest import BASE_URL, require_module
from helpers.voicekit_fakes import install_fake_synth, pcm

KEY = "vk-test-key-SECRET-12345"
OTHER_KEY = "vk-other-key-SECRET-67890"
SECRET_TEXT = "my account number is 998877665544"


@pytest.fixture(scope="module")
def api_module():
    require_module("voicekit.types", "backend/voicekit not delivered yet")
    return require_module("voicekit.api", "voicekit.api not delivered yet")


@pytest.fixture(scope="module")
def settings_module():
    return require_module("voicekit.settings", "voicekit.settings not delivered yet")


def _make_app(api_module, settings_module, keys, rate=1000, max_chars=200):
    settings = settings_module.VoicekitSettings(max_text_chars=max_chars)
    app = FastAPI()
    app.include_router(api_module.create_router(settings, lambda: list(keys), rate))
    return app


@pytest.fixture
def make_app(api_module, settings_module, monkeypatch):
    install_fake_synth(monkeypatch)
    made = []

    def _make(keys=(KEY,), rate=1000, max_chars=200):
        client = TestClient(_make_app(api_module, settings_module, keys, rate, max_chars), base_url=BASE_URL)
        made.append(client)
        return client

    yield _make
    for c in made:
        c.close()


def _post(client, headers=None, **body):
    payload = {"text": "Hello there.", "language": "en", "voice": "female_natural", "format": "pcm"}
    payload.update(body)
    return client.post("/api/tts/stream", json=payload, headers=headers or {})


# Auth


def test_missing_key_is_401(make_app):
    assert _post(make_app()).status_code == 401


def test_wrong_key_is_401(make_app):
    assert _post(make_app(), headers={"X-API-Key": "nope"}).status_code == 401


def test_valid_key_is_200(make_app):
    response = _post(make_app(), headers={"X-API-Key": KEY})
    assert response.status_code == 200
    assert response.content == pcm()


def test_any_configured_key_works(make_app):
    client = make_app(keys=(KEY, OTHER_KEY))
    assert _post(client, headers={"X-API-Key": OTHER_KEY}).status_code == 200


def test_empty_key_list_refuses_everyone(make_app):
    client = make_app(keys=())
    assert _post(client).status_code == 401
    assert _post(client, headers={"X-API-Key": ""}).status_code == 401
    assert _post(client, headers={"X-API-Key": KEY}).status_code == 401


def test_key_list_is_read_per_request(api_module, settings_module, monkeypatch):
    install_fake_synth(monkeypatch)
    keys = []
    app = FastAPI()
    app.include_router(api_module.create_router(settings_module.VoicekitSettings(), lambda: keys, 1000))
    with TestClient(app, base_url=BASE_URL) as client:
        assert _post(client, headers={"X-API-Key": KEY}).status_code == 401
        keys.append(KEY)
        assert _post(client, headers={"X-API-Key": KEY}).status_code == 200


def test_key_is_compared_in_constant_time(api_module):
    import inspect

    source = inspect.getsource(api_module)
    assert "compare_digest" in source, "keys must be compared with hmac.compare_digest"


# Limits


def test_rate_limit_returns_429(make_app):
    client = make_app(rate=3)
    codes = [_post(client, headers={"X-API-Key": KEY}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_oversize_text_is_422(make_app):
    client = make_app(max_chars=50)
    assert _post(client, headers={"X-API-Key": KEY}, text="x" * 51).status_code == 422
    assert _post(client, headers={"X-API-Key": KEY}, text="x" * 50).status_code == 200


def test_bad_bodies_are_422(make_app):
    client = make_app()
    headers = {"X-API-Key": KEY}
    assert client.post("/api/tts/stream", json={}, headers=headers).status_code == 422
    assert _post(client, headers=headers, text="").status_code == 422
    assert _post(client, headers=headers, format="mp3").status_code == 422
    assert _post(client, headers=headers, voice="robot").status_code == 422


def test_auth_is_checked_before_body_validation(make_app):
    assert make_app().post("/api/tts/stream", json={}).status_code in (401, 422)


# Formats


def test_pcm_format_has_sample_rate_header(make_app):
    response = _post(make_app(), headers={"X-API-Key": KEY}, format="pcm")
    assert response.headers["X-Sample-Rate"] == "24000"
    assert len(response.content) % 2 == 0


def test_wav_format_has_a_riff_header(make_app):
    response = _post(make_app(), headers={"X-API-Key": KEY}, format="wav")
    assert response.status_code == 200
    assert response.content[:4] == b"RIFF" and response.content[8:12] == b"WAVE"
    assert "wav" in response.headers["content-type"]


def test_ndjson_format_lines(make_app):
    response = _post(make_app(), headers={"X-API-Key": KEY}, format="ndjson")
    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    kinds = [line["type"] for line in lines]
    assert kinds[-1] == "done"
    assert "audio" in kinds and "timing" in kinds
    audio = next(line for line in lines if line["type"] == "audio")
    assert base64.b64decode(next(v for k, v in audio.items() if k not in ("type", "sample_rate", "sentence_index")))


def test_ndjson_uses_only_the_documented_line_types(make_app):
    """docs/VOICEKIT.md: lines are sentence, timing, audio or done (sentence was
    added to the P2.4 contract by Agent C)."""
    response = _post(make_app(), headers={"X-API-Key": KEY}, format="ndjson")
    kinds = {json.loads(line)["type"] for line in response.text.splitlines() if line.strip()}
    assert kinds <= {"sentence", "timing", "audio", "done"}, kinds


# WebSocket


def test_ws_without_key_is_refused(make_app):
    from starlette.websockets import WebSocketDisconnect

    client = make_app()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/tts") as ws:
            ws.send_json({"type": "speak", "text": "Hi", "language": "en", "voice": "female_natural"})
            ws.receive_json()


def test_ws_wrong_key_is_refused(make_app):
    from starlette.websockets import WebSocketDisconnect

    client = make_app()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/tts?api_key=wrong") as ws:
            ws.send_json({"type": "speak", "text": "Hi", "language": "en", "voice": "female_natural"})
            ws.receive_json()


@pytest.mark.parametrize("how", ["header", "query"])
def test_ws_valid_key_streams_audio_timings_done(make_app, how):
    client = make_app()
    kwargs = {"headers": {"X-API-Key": KEY}} if how == "header" else {}
    url = "/ws/tts" if how == "header" else f"/ws/tts?api_key={KEY}"
    with client.websocket_connect(url, **kwargs) as ws:
        ws.send_json({"type": "speak", "text": "Hello there.", "language": "en", "voice": "female_natural"})
        got_start = got_audio = got_timings = False
        for _ in range(20):
            message = ws.receive()
            if message.get("bytes") is not None:
                got_audio = True
                continue
            data = json.loads(message["text"])
            if data["type"] == "audio_start":
                got_start = True
                assert data["sample_rate"] == 24000
            elif data["type"] == "timings":
                got_timings = True
                assert {"text", "start_ms", "duration_ms"} <= set(data["words"][0])
            elif data["type"] == "done":
                break
        assert got_start and got_audio and got_timings


def test_ws_oversize_text_is_rejected(make_app):
    client = make_app(max_chars=20)
    with client.websocket_connect("/ws/tts", headers={"X-API-Key": KEY}) as ws:
        ws.send_json({"type": "speak", "text": "x" * 21, "language": "en", "voice": "female_natural"})
        message = ws.receive_json()
        assert message["type"] in ("error", "done") and message["type"] != "audio_start"


def test_ws_cancel_is_accepted(make_app):
    client = make_app()
    with client.websocket_connect("/ws/tts", headers={"X-API-Key": KEY}) as ws:
        ws.send_json({"type": "cancel"})
        ws.send_json({"type": "speak", "text": "Hello there.", "language": "en", "voice": "female_natural"})
        for _ in range(20):
            message = ws.receive()
            if message.get("text") and json.loads(message["text"])["type"] == "done":
                break
        else:
            pytest.fail("no done message after a cancel followed by a speak")


# Logging


def test_no_key_or_text_in_logs(make_app, caplog, capsys):
    client = make_app()
    with caplog.at_level(logging.DEBUG):
        _post(client, headers={"X-API-Key": KEY}, text=SECRET_TEXT)
        _post(client, headers={"X-API-Key": "wrong-key-XYZ-111"}, text=SECRET_TEXT)
        _post(client, text=SECRET_TEXT)
        with client.websocket_connect(f"/ws/tts?api_key={KEY}") as ws:
            ws.send_json({"type": "speak", "text": SECRET_TEXT, "language": "en", "voice": "female_natural"})
            for _ in range(20):
                message = ws.receive()
                if message.get("text") and json.loads(message["text"])["type"] == "done":
                    break
    out = capsys.readouterr()
    everything = caplog.text + out.out + out.err
    for secret in (KEY, "wrong-key-XYZ-111", "998877665544"):
        assert secret not in everything, f"{secret!r} leaked into log or console output"
