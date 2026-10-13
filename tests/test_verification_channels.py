"""Hub mode channels (SMS and email), the hub login lifecycle and the session
downgrade, against the mock hub in customer_tools mode (a login belongs to one
connection, exactly like the real hub) through mcp_gateway.

No network beyond 127.0.0.1. Time moves through the service's injectable clock.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, Request

from conftest import make_client, require_module

KEY = "verify-channels-test-key-SECRET-4c2d"
KNOWN_RAW = "011 234 5678"
KNOWN_E164 = "+94112345678"
KNOWN_NATIONAL = "0112345678"
UNKNOWN_RAW = "011 200 0009"
NAME = "Nimal Perera"
MASK_SMS = "XXXXXX1111"
MASK_EMAIL = "XXXXXXXXXXab12@gmail.com"
SMS_SHAPE = r"X{6}\d{4}"
EMAIL_SHAPE = r"X{10}[a-z]{4}@gmail\.com"


@pytest.fixture(scope="module")
def verification():
    require_module("verification.service", "backend/verification not delivered yet")
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    from verification import hub_mode, routes, service  # noqa: F401

    return service


@pytest.fixture(autouse=True)
def _schema(verification, db_schema):
    from verification import models

    models.ensure_schema(db_schema.engine)
    yield


@pytest.fixture(autouse=True)
def _wipe(verification, db_schema, _schema):
    from verification import models

    def wipe():
        with db_schema.engine.begin() as conn:
            conn.execute(sa.delete(models.challenges))
            conn.execute(sa.delete(models.VerificationAuditRow.__table__))
            conn.execute(sa.delete(models.users))

    wipe()
    yield
    wipe()


@pytest.fixture(autouse=True)
def hub_mode_on(monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_OTP_BACKEND", "hub")
    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR", 6)
    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 20)
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture(scope="module")
def mock_hub(verification):
    mock = require_module("mcp_client.mock_hub")
    server = mock.MockHub(mode="sse", path="/mcp", api_key=KEY, customer_tools=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def hub(mock_hub):
    from mcp_client import client
    from mcp_gateway import access_matrix, gateway, pool as pool_module

    mock_hub.sms_outbox.clear()
    mock_hub.tool_calls.clear()
    mock_hub.authenticated.clear()
    mock_hub.connection_auth.clear()
    mock_hub.customer_tool_errors.clear()
    mock_hub.customer_payloads.clear()
    mock_hub.fail_next = 0
    mock_hub.otp_code = "123456"
    mock_hub.otp_customers = {
        KNOWN_NATIONAL: {"name": NAME, "masked_mobile": MASK_SMS, "masked_email": MASK_EMAIL}
    }
    client.set_hub(client.HubClient(mock_hub.url, "X-API-Key", KEY, 3.0, 0, 300))
    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    gateway._pending.clear()
    gateway._service_types.clear()
    access_matrix.reload()
    yield mock_hub
    pool.close_all()
    for number in list(gateway._pending):
        gateway._close_pending(number)
    pool_module.set_pool(None)
    client.set_hub(None)


@pytest.fixture
def api(verification, hub, clock):
    from auth.access_context import access_from_request
    from verification import routes

    service = verification.VerificationService(clock=clock)
    verification.set_service(service)
    app = FastAPI()
    app.include_router(routes.router)

    @app.get("/who")
    def who(request: Request):
        access = access_from_request(request)
        return {"level": access.level, "expired": access.session_expired}

    clients = []

    def new():
        c = make_client(app)
        clients.append(c)
        return c

    holder = type("Api", (), {})()
    holder.client = new()
    holder.new_client = new
    holder.service = service
    yield holder
    for c in clients:
        c.close()
    verification.set_service(None)


def lookup(client, phone=KNOWN_RAW, name=NAME):
    return client.post("/verify/lookup", json={"name": name, "phone": phone, "language": "en"})


def send(client, ticket, method="sms"):
    return client.post("/verify/otp/send", json={"ticket": ticket, "method": method, "language": "en"})


def verify(client, challenge_id, code):
    return client.post("/verify/otp/verify", json={"challenge_id": challenge_id, "code": code})


def start(api, method="sms", phone=KNOWN_RAW, client=None):
    client = client or api.client
    found = lookup(client, phone=phone)
    assert found.status_code == 200, found.text
    sent = send(client, found.json()["ticket"], method)
    assert sent.status_code == 200, sent.text
    return sent.json()


def login(api, hub, method="sms"):
    sent = start(api, method)
    done = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert done.status_code == 200, done.text
    return sent, done


def validate_calls(hub):
    return [c[1] for c in hub.tool_calls if c[0] == "validate_customer_and_send_otp"]


def verify_calls(hub):
    return [c for c in hub.tool_calls if c[0] == "verify_otp_and_authenticate"]


def wrong():
    return "000000"


def verified_access():
    from access import AccessContext

    return AccessContext.verified(1, NAME, KNOWN_E164)


# Channels and masks


def test_lookup_offers_both_channels_without_a_mask_and_sends_nothing(api, hub):
    body = lookup(api.client).json()
    assert body["methods"] == [
        {"id": "sms", "masked": None, "generic": True},
        {"id": "email", "masked": None, "generic": True},
    ]
    assert hub.tool_calls == [] and hub.sms_outbox == []


@pytest.mark.parametrize("method,media,channel", [("sms", 1, "sms"), ("email", 2, "email")])
def test_each_channel_asks_the_hub_with_its_own_otp_media(api, hub, method, media, channel):
    start(api, method)
    calls = validate_calls(hub)
    assert len(calls) == 1 and calls[0]["otp_media"] == media and calls[0]["telephone_no"] == KNOWN_NATIONAL
    assert hub.sms_outbox[-1]["channel"] == channel


def test_the_mask_is_the_hubs_mask_for_the_chosen_channel_and_never_unmasked(api, hub, clock):
    sms = start(api, "sms")
    clock.advance(31)
    email = start(api, "email", client=api.new_client())
    assert sms["masked"] == MASK_SMS
    assert email["masked"] == MASK_EMAIL
    for body in (sms, email):
        assert KNOWN_NATIONAL not in str(body) and "112345678" not in str(body) and "nimal" not in str(body).lower()


def test_a_hub_mask_that_is_not_masked_is_dropped(api, hub):
    hub.otp_customers = {KNOWN_NATIONAL: {"name": NAME, "masked_mobile": "0771234567", "masked_email": "a@b.lk"}}
    assert start(api, "sms")["masked"] is None


def test_decoy_looks_identical_for_both_channels(api, hub, clock):
    for method, shape in (("sms", SMS_SHAPE), ("email", EMAIL_SHAPE)):
        clock.advance(31)
        real = start(api, method, client=api.new_client())
        clock.advance(31)
        decoy = start(api, method, phone=UNKNOWN_RAW, client=api.new_client())
        assert set(real) == set(decoy)
        assert real["expires_in"] == decoy["expires_in"] and real["resend_in"] == decoy["resend_in"]
        assert len(real["challenge_id"]) == len(decoy["challenge_id"])
        assert re.fullmatch(shape, decoy["masked"]), decoy["masked"]
    assert {m["to"] for m in hub.sms_outbox} == {KNOWN_NATIONAL}  # nothing was sent for the unknown number


def test_a_decoy_never_logs_in_and_answers_like_a_real_challenge(api, hub, clock):
    decoy = start(api, "sms", phone=UNKNOWN_RAW)
    real_client = api.new_client()
    real = start(api, "sms", client=real_client)
    rows = []
    for client, sent in ((api.client, decoy), (real_client, real)):
        row = []
        for _ in range(3):
            r = verify(client, sent["challenge_id"], wrong())
            row.append((r.status_code, r.json()))
        final = verify(client, sent["challenge_id"], hub.otp_code)
        row.append((final.status_code, final.json()["code"]))
        rows.append(row)
    assert rows[0] == rows[1] and rows[0][-1] == (423, "locked")
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_an_unknown_method_is_no_contact(api):
    ticket = lookup(api.client).json()["ticket"]
    response = send(api.client, ticket, "voice")
    assert response.status_code == 409 and response.json() == {"code": "no_contact"}


# Our rules in front of the hub


def test_wrong_code_then_right_code(api, hub):
    sent = start(api, "email")
    first = verify(api.client, sent["challenge_id"], wrong())
    assert first.status_code == 401 and first.json() == {"code": "invalid", "attempts_left": 2}
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200


def test_expired_code_is_410_on_our_clock(api, hub, clock):
    sent = start(api, "sms")
    clock.advance(121)
    late = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert late.status_code == 410 and late.json() == {"code": "expired"}
    assert verify_calls(hub) == []


def test_three_wrong_codes_lock_the_challenge_before_the_hub_is_asked_again(api, hub):
    sent = start(api, "sms")
    for left in (2, 1):
        assert verify(api.client, sent["challenge_id"], wrong()).json()["attempts_left"] == left
    assert verify(api.client, sent["challenge_id"], wrong()).status_code == 423
    asked = len(verify_calls(hub))
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 423
    assert len(verify_calls(hub)) == asked


def test_single_use(api, hub):
    sent = start(api, "email")
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200
    again = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert again.status_code == 401 and again.json()["code"] == "invalid"


def test_resend_waits_30_seconds_and_a_new_send_retires_the_old_one(api, hub, clock):
    first = start(api, "sms")
    early = send(api.client, lookup(api.client).json()["ticket"], "email")
    assert early.status_code == 429 and early.json()["code"] == "cooldown"
    assert len(validate_calls(hub)) == 1  # refused before the hub was asked
    clock.advance(31)
    second = send(api.client, lookup(api.client).json()["ticket"], "email")
    assert second.status_code == 200
    assert [c["otp_media"] for c in validate_calls(hub)] == [1, 2]
    old = verify(api.client, first["challenge_id"], hub.otp_code)
    assert old.status_code == 401 and old.json()["code"] == "invalid"
    assert verify(api.client, second.json()["challenge_id"], hub.otp_code).status_code == 200


def test_hourly_limit_per_number(api, hub, clock):
    results = []
    for index in range(8):
        clock.advance(31)
        client = api.new_client()
        ticket = lookup(client).json()["ticket"]
        results.append(send(client, ticket, "sms" if index % 2 else "email"))
    assert [r.status_code for r in results[:6]] == [200] * 6
    assert results[6].status_code == 429 and results[6].json()["code"] == "rate_limited"
    assert len(validate_calls(hub)) == 6


def test_hourly_limit_per_address(api, hub, clock, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 2)
    statuses = []
    for index in range(4):
        clock.advance(31)
        found = lookup(api.client, phone=f"011 200 00{index:02d}")
        if found.status_code != 200:
            statuses.append(found.status_code)  # the lookup limit is per address too
            continue
        statuses.append(send(api.client, found.json()["ticket"], "email").status_code)
    assert statuses[:2] == [200, 200] and set(statuses[2:]) == {429}


# Session lifecycle


def test_success_cookie_has_otp_and_hub_and_the_hub_login_is_kept(api, hub):
    import mcp_gateway
    from auth.tokens import AUDIENCE_USER, decode_token

    login(api, hub, "email")
    claims = decode_token(api.client.cookies.get("user_session"), AUDIENCE_USER)
    assert claims["amr"] == ["otp", "hub"]
    for leaked in ("subscriber_id", "account_number", "tel", "phone"):
        assert leaked not in claims
    assert mcp_gateway.has_session(verified_access())
    assert api.client.get("/who").json() == {"level": "verified", "expired": False}


def test_a_customer_tool_works_after_verify_and_not_after_logout(api, hub):
    import mcp_gateway

    login(api, hub)
    assert mcp_gateway.call("show_gb_balance", {}, verified_access()).ok
    assert api.client.post("/verify/logout").json() == {"ok": True}
    assert mcp_gateway.has_session(verified_access()) is False
    assert mcp_gateway.call("show_gb_balance", {}, verified_access()).kind == "reauth"


def test_a_token_whose_hub_session_is_gone_is_a_guest_with_expired(api, hub):
    import mcp_gateway

    login(api, hub)
    assert api.client.cookies.get("user_session")
    mcp_gateway.drop_session(verified_access())  # expired, evicted or server restart
    assert api.client.get("/who").json() == {"level": "guest", "expired": True}
    assert api.client.get("/verify/session").json() == {"verified": False, "hub_session": False, "expired": True}


def test_a_local_token_without_hub_is_not_downgraded(api, hub):
    from auth.tokens import AUDIENCE_USER, issue_token
    from verification.lookup import CustomerRecord
    from verification.service import VerificationService

    record = CustomerRecord(
        id=0, phone_e164=KNOWN_E164, display_name=NAME, name_normalized="", mobile_e164=None, email=None,
        preferred_language="en",
    )
    user_id, _ = VerificationService._find_or_create_user(record, "en", datetime.now(timezone.utc))
    token, _ = issue_token(str(user_id), AUDIENCE_USER, 5, extra={"amr": ["otp"]})
    client = api.new_client()
    client.cookies.set("user_session", token)
    assert client.get("/who").json() == {"level": "verified", "expired": False}


def test_logout_without_a_session_is_fine(api):
    assert api.client.post("/verify/logout").json() == {"ok": True}


def test_expired_reply_has_the_official_numbers_in_every_language():
    from access import expired_reply

    for language in ("en", "si", "ta", "xx"):
        text = expired_reply(language)
        assert "1212" in text and "0113121212" in text and "1222" in text


# Nothing secret leaves


def test_no_code_number_or_token_in_output_or_browser_responses(api, hub, capsys, caplog):
    caplog.set_level("INFO")
    found = lookup(api.client)
    ticket = found.json()["ticket"]
    sent = send(api.client, ticket, "email")
    session_id = hub.sms_outbox[-1]["session_id"]
    bad = verify(api.client, sent.json()["challenge_id"], wrong())
    done = verify(api.client, sent.json()["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    session_cookie = api.client.cookies.get("user_session")
    me = api.client.get("/verify/session")
    out = capsys.readouterr()
    logs = out.out + out.err + caplog.text
    for secret in (hub.otp_code, KNOWN_NATIONAL, "112345678", session_id, session_cookie, MASK_EMAIL, ticket):
        assert secret not in logs, secret
    for r in (found, sent, bad, done, me):
        assert session_cookie not in r.text
        assert session_id not in r.text and hub.otp_code not in r.text
        for name, value in r.headers.items():
            if name.lower() != "set-cookie":
                assert session_cookie not in value
    cookies = done.headers.get_list("set-cookie")
    assert len(cookies) == 1 and cookies[0].startswith("user_session=") and "httponly" in cookies[0].lower()
    assert [r for r in (found, sent, bad, me) if r.headers.get_list("set-cookie")] == []
    for secret in ("subscriber", "account_number", "SUB-0001", "ACC-0001"):
        assert secret not in done.text
