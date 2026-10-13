"""Hub mode of the verification wizard (plan 4.3, VERIFY_OTP_BACKEND=hub).

The hub is the local mock (mcp_client.mock_hub): it never sends a real SMS and
no network beyond 127.0.0.1 is used. Time moves through the service's
injectable clock, so the 120 second expiry is checked on the server clock
even though the mock hub would still accept the code.
"""

import itertools
import re
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import make_client, require_module

KEY = "verify-hub-test-key-SECRET-9f8e7d"
KNOWN_RAW = "011 234 5678"
KNOWN_E164 = "+94112345678"
KNOWN_NATIONAL = "0112345678"
UNKNOWN_RAW = "011 200 0009"
UNKNOWN_NATIONAL = "0112000009"
HUB_NAME = "Nimal Perera"


@pytest.fixture(scope="module")
def verification():
    require_module("verification.service", "backend/verification not delivered yet")
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    from verification import hub_mode, lookup, models, routes, senders, service  # noqa: F401

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
            conn.execute(sa.delete(models.CustomerDirectoryRow.__table__))
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
    server = mock.MockHub(mode="sse", path="/mcp", api_key=KEY)
    server.start()
    yield server
    server.stop()


def _install(url, retries=0, timeout=2.0):
    client = require_module("mcp_client.client")
    hub = client.HubClient(url, "X-API-Key", KEY, timeout, retries, 300)
    client.set_hub(hub)
    return hub


@pytest.fixture
def hub(mock_hub):
    mock_hub.sms_outbox.clear()
    mock_hub.tool_calls.clear()
    mock_hub.fail_next = 0
    mock_hub.otp_code = "123456"
    mock_hub.otp_session_key = "session_id"
    mock_hub.otp_masked_in_response = True
    mock_hub.otp_customers = {KNOWN_NATIONAL: {"name": HUB_NAME, "masked": "07X XXX X567"}}
    _install(mock_hub.url)
    yield mock_hub
    require_module("mcp_client.client").set_hub(None)


@pytest.fixture
def api(verification, hub, clock):
    from verification import routes

    service = verification.VerificationService(clock=clock)
    verification.set_service(service)
    app = FastAPI()
    app.include_router(routes.router)
    clients = []

    def new():
        c = make_client(app)
        clients.append(c)
        return c

    holder = type("Api", (), {})()
    holder.client = new()
    holder.new_client = new
    holder.service = service
    holder.app = app
    yield holder
    for c in clients:
        c.close()
    verification.set_service(None)


def lookup(client, name=HUB_NAME, phone=KNOWN_RAW, language="en"):
    return client.post("/verify/lookup", json={"name": name, "phone": phone, "language": language})


def send(client, ticket, method="sms", language="en"):
    return client.post("/verify/otp/send", json={"ticket": ticket, "method": method, "language": language})


def verify(client, challenge_id, code):
    return client.post("/verify/otp/verify", json={"challenge_id": challenge_id, "code": code})


def start(api, phone=KNOWN_RAW, name=HUB_NAME, client=None):
    client = client or api.client
    found = lookup(client, name=name, phone=phone)
    assert found.status_code == 200, found.text
    sent = send(client, found.json()["ticket"])
    assert sent.status_code == 200, sent.text
    return sent.json()


def verify_calls(hub):
    return [c for c in hub.tool_calls if c[0] == "verify_otp_and_authenticate"]


def wrong(code="123456"):
    return "000000" if code != "000000" else "111111"


# Lookup


def test_lookup_returns_the_generic_sms_method_and_a_ticket(api):
    response = lookup(api.client)
    assert response.status_code == 200
    body = response.json()
    assert body["methods"] == [{"id": "sms", "masked": None, "generic": True}]
    assert body["mode"] == "hub"
    assert body["ticket"]
    assert response.headers["cache-control"] == "no-store"


def test_lookup_does_not_read_the_directory_or_call_the_hub(api, hub, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the directory must not be used in hub mode")

    monkeypatch.setattr(api.service.directory, "find", boom, raising=False)
    assert lookup(api.client).status_code == 200
    assert hub.tool_calls == []  # nothing is sent before the person chooses to send a code


def test_name_is_optional_and_a_bad_number_is_phone_format(api):
    assert lookup(api.client, name="").status_code == 200
    assert lookup(api.client, phone="hello").json() == {"code": "phone_format"}
    assert lookup(api.client, phone="077 1234567").json() == {"code": "phone_format"}


def test_lookup_rate_limit_is_the_same_for_known_and_unknown(api, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 100)
    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR", 2)  # 6 lookups per number
    limits = []
    for raw in (KNOWN_RAW, UNKNOWN_RAW):
        client = api.new_client()
        count = 0
        while count < 12:
            response = lookup(client, phone=raw)
            if response.status_code == 429:
                break
            count += 1
        limits.append((count, response.status_code, response.json()["code"]))
    assert limits[0] == limits[1] == (6, 429, "rate_limited")


# Full flow


def test_full_flow_success_sets_the_cookie_with_otp_and_hub(api, hub):
    from auth.tokens import AUDIENCE_USER, decode_token

    sent = start(api)
    assert sent["expires_in"] == 120 and sent["resend_in"] == 30
    assert set(sent) == {"challenge_id", "expires_in", "resend_in", "masked"}
    assert sent["masked"] is None
    assert hub.sms_outbox[-1]["to"] == KNOWN_NATIONAL  # national form, as the backend sends it

    done = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert done.status_code == 200, done.text
    assert done.json() == {"display_name": HUB_NAME, "language": "en"}
    claims = decode_token(api.client.cookies.get("user_session"), AUDIENCE_USER)
    assert "otp" in claims["amr"] and "hub" in claims["amr"]
    assert api.client.get("/verify/session").json() == {"verified": True, "display_name": HUB_NAME}

    from access import AccessContext

    ctx = AccessContext.from_claims(claims["amr"], user_id=int(claims["sub"]), display_name=HUB_NAME, phone_e164=KNOWN_E164)
    assert ctx.level == "verified"


def test_the_users_row_is_created_by_phone_and_reused(api, hub, db_schema):
    from verification import models

    sent = start(api)
    verify(api.client, sent["challenge_id"], hub.otp_code)
    with db_schema.engine.begin() as conn:
        rows = conn.execute(sa.select(models.users.c.phone_e164, models.users.c.display_name)).all()
    assert [(r.phone_e164, r.display_name) for r in rows] == [(KNOWN_E164, HUB_NAME)]
    api.service.clock.advance(31)
    again = start(api, client=api.new_client())
    done = verify(api.client, again["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    with db_schema.engine.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(models.users)).scalar() == 1


def test_display_name_falls_back_to_the_typed_name_then_a_neutral_word(api, hub):
    hub.otp_customers = {KNOWN_NATIONAL: {"name": None, "masked": "07X XXX X567"}}
    sent = start(api, name="  Kamal   Silva  ")
    assert verify(api.client, sent["challenge_id"], hub.otp_code).json()["display_name"] == "Kamal Silva"

    api.service.clock.advance(31)
    other = api.new_client()
    hub.otp_customers = {"0112000001": {"name": None, "masked": "07X XXX X567"}}
    sent = start(api, phone="011 200 0001", name="", client=other)
    done = verify(other, sent["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    assert done.json()["display_name"] == "Customer"


def test_the_typed_name_is_cleaned_and_cut_to_80_characters(api, hub):
    hub.otp_customers = {KNOWN_NATIONAL: {"name": None, "masked": "07X XXX X567"}}
    sent = start(api, name="A\u0007" + "b" * 100)
    name = verify(api.client, sent["challenge_id"], hub.otp_code).json()["display_name"]
    assert len(name) == 80 and name.startswith("Ab") and "\u0007" not in name


# Our rules in front of the hub


def test_wrong_code_counts_an_attempt_and_is_401_with_attempts_left(api, hub):
    sent = start(api)
    first = verify(api.client, sent["challenge_id"], wrong())
    assert first.status_code == 401 and first.json() == {"code": "invalid", "attempts_left": 2}
    second = verify(api.client, sent["challenge_id"], wrong())
    assert second.json() == {"code": "invalid", "attempts_left": 1}
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200


def test_three_wrong_codes_lock_even_the_right_one(api, hub):
    sent = start(api)
    for left in (2, 1):
        assert verify(api.client, sent["challenge_id"], wrong()).json()["attempts_left"] == left
    third = verify(api.client, sent["challenge_id"], wrong())
    assert third.status_code == 423
    fourth = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert fourth.status_code == 423 and fourth.json()["code"] == "locked"
    asked = len(verify_calls(hub))
    verify(api.client, sent["challenge_id"], hub.otp_code)
    assert len(verify_calls(hub)) == asked  # a locked challenge never reaches the hub
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_expiry_at_120_seconds_on_the_server_clock_even_if_the_hub_would_accept(api, hub, clock):
    sent = start(api)
    clock.advance(119)
    ok_window = verify(api.client, sent["challenge_id"], wrong())
    assert ok_window.status_code == 401
    clock.advance(2)  # 121 s after the send
    late = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert late.status_code == 410 and late.json() == {"code": "expired"}
    assert verify_calls(hub) == [] or all(c[1]["otp_value"] != hub.otp_code for c in verify_calls(hub))
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_a_code_just_before_expiry_still_works(api, hub, clock):
    sent = start(api)
    clock.advance(119)
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200


def test_single_use(api, hub):
    sent = start(api)
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200
    again = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert again.status_code == 401 and again.json()["code"] == "invalid"
    calls = len(verify_calls(hub))
    verify(api.client, sent["challenge_id"], hub.otp_code)
    assert len(verify_calls(hub)) == calls  # a used challenge never reaches the hub


def test_unknown_challenge_and_malformed_code_do_not_reach_the_hub(api, hub):
    assert verify(api.client, "f" * 32, "123456").status_code == 401
    sent = start(api)
    response = verify(api.client, sent["challenge_id"], "12ab")
    assert response.status_code == 401 and response.json()["attempts_left"] == 2
    assert verify_calls(hub) == []


def test_resend_retires_the_old_session(api, hub, clock):
    first = start(api)
    first_session = hub.sms_outbox[-1]["session_id"]
    early = send(api.client, lookup(api.client).json()["ticket"])
    assert early.status_code == 429 and early.json()["code"] == "cooldown"
    clock.advance(31)
    second = send(api.client, lookup(api.client).json()["ticket"])
    assert second.status_code == 200
    assert hub.sms_outbox[-1]["session_id"] != first_session
    old = verify(api.client, first["challenge_id"], hub.otp_code)
    assert old.status_code == 401 and old.json()["code"] == "invalid"
    assert verify(api.client, second.json()["challenge_id"], hub.otp_code).status_code == 200


def test_cooldown_is_429_with_retry_after_and_creates_no_sms(api, hub, clock):
    start(api)
    texts = len(hub.sms_outbox)
    response = send(api.client, lookup(api.client).json()["ticket"])
    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "cooldown" and 1 <= body["retry_after"] <= 30
    assert response.headers["retry-after"] == str(body["retry_after"])
    assert len(hub.sms_outbox) == texts  # refused before the hub was asked


def test_hourly_limit_per_number(api, hub, clock):
    results = []
    for _ in range(8):
        clock.advance(31)
        client = api.new_client()
        found = lookup(client).json()["ticket"]
        results.append(send(client, found))
    codes = [r.status_code for r in results]
    assert codes[:6] == [200] * 6
    assert codes[6] == 429 and results[6].json()["code"] == "rate_limited"
    assert len(hub.sms_outbox) == 6


def test_hourly_limit_per_address(api, hub, clock, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 3)
    codes = []
    for index in range(5):
        clock.advance(31)
        raw = f"011 200 00{index:02d}"
        found = lookup(api.client, phone=raw)
        if found.status_code != 200:
            codes.append(found.status_code)
            continue
        codes.append(send(api.client, found.json()["ticket"]).status_code)
    assert codes[:3] == [200, 200, 200]
    assert set(codes[3:]) == {429}


# Decoy


def test_unknown_number_gets_an_identical_response_at_send(api, hub, clock):
    known = start(api)
    clock.advance(31)
    other = api.new_client()
    unknown = start(api, phone=UNKNOWN_RAW, client=other)
    assert set(known) == set(unknown)
    assert known["expires_in"] == unknown["expires_in"] and known["resend_in"] == unknown["resend_in"]
    assert known["masked"] == unknown["masked"] is None
    assert len(known["challenge_id"]) == len(unknown["challenge_id"])
    assert len(hub.sms_outbox) == 1  # only the real customer was texted
    assert [c[1]["telephone_no"] for c in hub.tool_calls if c[0] == "validate_customer_and_send_otp"] == [
        KNOWN_NATIONAL,
        UNKNOWN_NATIONAL,
    ]


def test_decoy_challenge_behaves_like_a_real_one_at_verify(api, hub, clock):
    real = start(api)
    other = api.new_client()
    decoy = start(api, phone=UNKNOWN_RAW, client=other)
    outcomes = []
    for client, sent in ((api.client, real), (other, decoy)):
        row = []
        for _ in range(3):
            response = verify(client, sent["challenge_id"], wrong())
            row.append((response.status_code, response.json()))
        final = verify(client, sent["challenge_id"], hub.otp_code)
        row.append((final.status_code, final.json().get("code")))
        outcomes.append(row)
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][-1] == (423, "locked")


def test_decoy_expires_and_is_never_a_session(api, hub, clock):
    decoy = start(api, phone=UNKNOWN_RAW)
    first = verify(api.client, decoy["challenge_id"], hub.otp_code)  # the code of ANOTHER customer's session
    assert first.status_code == 401 and first.json() == {"code": "invalid", "attempts_left": 2}
    clock.advance(121)
    late = verify(api.client, decoy["challenge_id"], hub.otp_code)
    assert late.status_code == 410
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_decoy_counts_against_the_same_limits(api, hub, clock):
    start(api, phone=UNKNOWN_RAW)
    response = send(api.client, lookup(api.client, phone=UNKNOWN_RAW).json()["ticket"])
    assert response.status_code == 429 and response.json()["code"] == "cooldown"


# Hub trouble


def test_hub_down_at_send_is_503_and_starts_no_cooldown(api, hub, clock, mock_hub):
    _install("http://127.0.0.1:9/mcp", timeout=1.0)  # nothing listens here
    found = lookup(api.client).json()["ticket"]
    response = send(api.client, found)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}
    _install(mock_hub.url)
    again = send(api.client, found)  # no cooldown was started by the failure
    assert again.status_code == 200


def test_hub_down_at_verify_is_503_and_only_one_attempt_is_used(api, hub, mock_hub):
    sent = start(api)
    _install("http://127.0.0.1:9/mcp", timeout=1.0)
    response = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}
    _install(mock_hub.url)
    again = verify(api.client, sent["challenge_id"], wrong())
    assert again.status_code == 401 and again.json()["attempts_left"] == 1  # one for the 503, one for this
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200


def test_hub_timeout_is_503_not_invalid(api, hub, mock_hub, monkeypatch):
    sent = start(api)
    from otp.hub_backend import HubOtpBackend

    def slow(self, phone, session, code):
        raise TimeoutError("budget")

    monkeypatch.setattr(HubOtpBackend, "verify", slow)
    response = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}


def test_unrecognised_request_answer_is_503(api, hub):
    hub.otp_session_key = "something_else"  # success without a session id the parser knows
    found = lookup(api.client).json()["ticket"]
    response = send(api.client, found)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}


def test_unrecognised_verify_answer_is_503_and_costs_one_attempt(api, hub, verification, clock, monkeypatch):
    from otp.hub_backend import HubOtpBackend, HubOtpUnknownResponse

    class Odd(HubOtpBackend):
        def verify(self, phone, session, code):
            raise HubOtpUnknownResponse("shape")

    api.service.hub_otp = Odd()
    sent = start(api)
    response = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}
    api.service.hub_otp = HubOtpBackend()
    left = verify(api.client, sent["challenge_id"], wrong())
    assert left.json() == {"code": "invalid", "attempts_left": 1}


def test_a_hub_that_rejects_the_code_is_invalid_not_unavailable(api, hub):
    sent = start(api)
    response = verify(api.client, sent["challenge_id"], wrong(hub.otp_code))
    assert response.status_code == 401 and response.json()["code"] == "invalid"
    assert len(verify_calls(hub)) == 1


# Secrets stay out of logs, rows and responses


def test_no_session_id_code_or_number_in_logs_rows_or_responses(api, hub, capsys, caplog, db_schema):
    from verification import models

    caplog.set_level("INFO")  # DEBUG would show the MCP SDK and the mock server wire logs, which are not ours
    found = lookup(api.client)
    sent = send(api.client, found.json()["ticket"])
    session_id = hub.sms_outbox[-1]["session_id"]
    done = verify(api.client, sent.json()["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    wrong_try = verify(api.client, sent.json()["challenge_id"], wrong())
    captured = capsys.readouterr()
    logs = captured.out + captured.err + caplog.text
    responses = " ".join(r.text for r in (found, sent, done, wrong_try))
    for secret in (session_id, hub.otp_code, KNOWN_NATIONAL, "112345678", "07X XXX X567"):
        assert secret not in logs, secret
    for secret in (session_id, KNOWN_NATIONAL, "112345678", hub.otp_code):
        assert secret not in sent.text and secret not in found.text, secret
    assert session_id not in responses
    with db_schema.engine.begin() as conn:
        dump = " ".join(
            str(v)
            for table in (models.challenges, models.VerificationAuditRow.__table__)
            for row in conn.execute(sa.select(table)).all()
            for v in row
        )
    for secret in (session_id, hub.otp_code, KNOWN_NATIONAL, "112345678"):
        assert secret not in dump, secret
    assert re.search(r"VERIFY (send|verify) [0-9a-f]{10}:", logs)


def test_the_hub_session_is_sealed_in_the_challenge_row(api, hub, db_schema):
    from verification import models

    start(api)
    with db_schema.engine.begin() as conn:
        row = conn.execute(sa.select(models.challenges)).one()
    assert row.hub_ref and hub.sms_outbox[-1]["session_id"] not in row.hub_ref
    assert row.purpose == "verify" and row.channel == "sms"
    assert row.user_id is None


# Other tickets and modes


def test_a_local_ticket_is_refused_in_hub_mode_and_a_hub_ticket_in_local_mode(api, hub, monkeypatch):
    import config

    ticket = lookup(api.client).json()["ticket"]
    monkeypatch.setattr(config, "VERIFY_OTP_BACKEND", "local")
    response = send(api.client, ticket)
    assert response.status_code == 401 and response.json() == {"code": "ticket_invalid"}
    monkeypatch.setattr(config, "VERIFY_OTP_BACKEND", "hub")
    from auth.tokens import issue_token
    from verification.service import AUDIENCE_TICKET

    local_style, _ = issue_token("1", AUDIENCE_TICKET, 5, extra={"tel": KNOWN_E164, "lang": "en"})
    refused = send(api.client, local_style)
    assert refused.status_code == 401 and refused.json() == {"code": "ticket_invalid"}


def test_only_the_sms_method_exists(api):
    ticket = lookup(api.client).json()["ticket"]
    response = send(api.client, ticket, "email")
    assert response.status_code == 409 and response.json() == {"code": "no_contact"}


def test_session_and_logout_are_unchanged(api, hub):
    sent = start(api)
    verify(api.client, sent["challenge_id"], hub.otp_code)
    assert api.client.get("/verify/session").json()["verified"] is True
    assert api.client.post("/verify/logout").json() == {"ok": True}
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_audit_rows_use_hashes_only(api, hub, db_schema):
    from verification import models

    sent = start(api)
    verify(api.client, sent["challenge_id"], hub.otp_code)
    with db_schema.engine.begin() as conn:
        rows = conn.execute(sa.select(models.VerificationAuditRow.__table__)).all()
    assert [(r.event, r.outcome) for r in rows] == [("lookup", "ok"), ("send", "sent"), ("verify", "ok")]
    assert all(r.phone_hash and len(r.phone_hash) == 64 for r in rows)


def test_local_mode_is_unchanged_by_the_new_code(verification, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_OTP_BACKEND", "local")
    from verification import hub_mode

    assert hub_mode.active() is False
