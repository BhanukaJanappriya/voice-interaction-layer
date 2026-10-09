"""Verification wizard (plan 3.3 R3): /verify/* routes and service rules.

The sender is a recorder (no console print, no SMTP, no gateway, no hub).
Time moves through the service's injectable clock, so the 120 second expiry
is checked on the server clock without waiting. A tiny FastAPI app mounts
the router, because the lead mounts it in server.py later.

Revision 0003 is not in db/migrations yet, so the autouse fixture creates
the same tables and columns on the temporary test database with
verification.models.ensure_schema (a no-op once the migration exists).
"""

import itertools
import logging
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import make_client, require_module

PHONE_RAW = "011 200 0001"
PHONE_E164 = "+94112000001"
MOBILE = "+94770000123"
EMAIL = "demo.customer@gmail.com"
NAME = "Test Customer"

_phones = itertools.count(2000100)


@pytest.fixture(scope="module")
def verification():
    require_module("verification.service", "backend/verification not delivered yet")
    from verification import lookup, models, routes, senders, service  # noqa: F401

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

    wipe()
    yield
    wipe()


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


@pytest.fixture
def recorder():
    from otp.sender import OtpSender

    class Recorder(OtpSender):
        channel = "sms"
        name = "recorder"

        def __init__(self):
            self.sent = []

        def send(self, destination, code, language):
            self.sent.append({"destination": destination, "code": code, "language": language})

        @property
        def last_code(self):
            return self.sent[-1]["code"]

    return Recorder()


@pytest.fixture
def directory(verification):
    from verification.lookup import CustomerDirectory

    d = CustomerDirectory()
    d.upsert(PHONE_E164, NAME, MOBILE, EMAIL, "en")
    return d


@pytest.fixture
def api(verification, directory, recorder, clock):
    from verification import routes

    verification.set_service(verification.VerificationService(sender_resolver=lambda channel: recorder, clock=clock))
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
    yield holder
    for c in clients:
        c.close()
    verification.set_service(None)


def lookup(client, name=NAME, phone=PHONE_RAW, language="en"):
    return client.post("/verify/lookup", json={"name": name, "phone": phone, "language": language})


def send(client, ticket, method="sms", language="en"):
    return client.post("/verify/otp/send", json={"ticket": ticket, "method": method, "language": language})


def verify(client, challenge_id, code):
    return client.post("/verify/otp/verify", json={"challenge_id": challenge_id, "code": code})


def start(api, recorder, method="sms", client=None):
    client = client or api.client
    ticket = lookup(client).json()["ticket"]
    response = send(client, ticket, method)
    assert response.status_code == 200, response.text
    return ticket, response.json()


def wrong(code):
    return "000000" if code != "000000" else "111111"


# Success path


def test_success_path_sets_cookie_with_otp_claim(api, recorder):
    from auth.tokens import AUDIENCE_USER, decode_token

    first = lookup(api.client)
    assert first.status_code == 200
    body = first.json()
    assert body["methods"] == [
        {"id": "sms", "masked": "07X XXX X123"},
        {"id": "email", "masked": "XXXXXXXXX@gmail.com"},
    ]
    assert first.headers["cache-control"] == "no-store"

    sent = send(api.client, body["ticket"], "sms")
    assert sent.status_code == 200
    data = sent.json()
    assert data["expires_in"] == 120 and data["resend_in"] == 30
    assert data["masked"] == "07X XXX X123"
    assert recorder.sent[-1]["destination"] == MOBILE

    done = verify(api.client, data["challenge_id"], recorder.last_code)
    assert done.status_code == 200, done.text
    assert done.json() == {"display_name": NAME, "language": "en"}

    cookie = api.client.cookies.get("user_session")
    assert cookie
    claims = decode_token(cookie, AUDIENCE_USER)
    assert claims["amr"] == ["otp"]

    session = api.client.get("/verify/session")
    assert session.json() == {"verified": True, "display_name": NAME}


def test_email_method_sends_to_the_email(api, recorder):
    from verification import senders

    ticket = lookup(api.client).json()["ticket"]
    response = send(api.client, ticket, "email")
    assert response.status_code == 200
    assert response.json()["masked"] == "XXXXXXXXX@gmail.com"
    assert recorder.sent[-1]["destination"] == EMAIL


def test_session_is_false_without_otp_claim(api, make_user):
    from auth.tokens import AUDIENCE_USER, issue_token

    user_id = make_user(phone_e164="+94112000009")
    token, _ = issue_token(str(user_id), AUDIENCE_USER, 5)
    api.client.cookies.set("user_session", token)
    assert api.client.get("/verify/session").json() == {"verified": False}
    assert api.new_client().get("/verify/session").json() == {"verified": False}


def test_logout_clears_cookie(api, recorder):
    ticket, sent = start(api, recorder)
    verify(api.client, sent["challenge_id"], recorder.last_code)
    assert api.client.get("/verify/session").json()["verified"] is True
    assert api.client.post("/verify/logout").json() == {"ok": True}
    assert api.client.get("/verify/session").json() == {"verified": False}


def test_verify_finds_existing_user_row(api, recorder, make_user):
    user_id = make_user(phone_e164=PHONE_E164, display_name="Existing Name")
    ticket, sent = start(api, recorder)
    done = verify(api.client, sent["challenge_id"], recorder.last_code)
    assert done.status_code == 200
    from auth.tokens import AUDIENCE_USER, decode_token

    assert decode_token(api.client.cookies.get("user_session"), AUDIENCE_USER)["sub"] == str(user_id)


# Generic failure


def test_wrong_name_and_unknown_number_are_identical(api):
    wrong_name = lookup(api.client, name="Somebody Else")
    unknown = lookup(api.client, phone="011 299 9999")
    assert wrong_name.status_code == unknown.status_code == 422
    assert wrong_name.json() == unknown.json() == {"code": "invalid_details"}
    assert dict(wrong_name.headers).keys() == dict(unknown.headers).keys()


def test_phone_format_only_for_impossible_syntax(api):
    response = lookup(api.client, phone="hello")
    assert response.status_code == 422
    assert response.json() == {"code": "phone_format"}
    assert lookup(api.client, phone="077 1234567").json() == {"code": "phone_format"}


def test_name_match_normalization(api):
    assert lookup(api.client, name="  test   CUSTOMER. ").status_code == 200


def test_name_match_not_required(api, monkeypatch):
    import config

    assert lookup(api.client, name="Nobody").status_code == 422
    monkeypatch.setattr(config, "NAME_MATCH_REQUIRED", False)
    assert lookup(api.client, name="Nobody").status_code == 200
    # An unknown number still fails.
    assert lookup(api.client, name="Nobody", phone="011 299 9999").status_code == 422


def test_unknown_number_does_same_work(api, verification, monkeypatch):
    """Both failure paths read the directory once and write one audit row."""
    from verification import audit

    calls = []
    real = audit.record
    monkeypatch.setattr(audit, "record", lambda *a, **k: (calls.append(a[:2]), real(*a, **k))[1])
    lookup(api.client, name="Somebody Else")
    lookup(api.client, phone="011 299 9999")
    assert calls == [("lookup", "invalid_details"), ("lookup", "invalid_details")]


# Methods and contacts


def test_methods_only_from_present_contacts(api, directory):
    directory.upsert("+94112000002", "Email Only", None, "only@gmail.com", "en")
    directory.upsert("+94112000003", "Mobile Only", "+94770000456", None, "en")
    directory.upsert("+94112000004", "Nothing Here", None, None, "en")
    only_email = lookup(api.client, "Email Only", "011 200 0002").json()
    only_mobile = lookup(api.client, "Mobile Only", "011 200 0003").json()
    nothing = lookup(api.client, "Nothing Here", "011 200 0004").json()
    assert [m["id"] for m in only_email["methods"]] == ["email"]
    assert [m["id"] for m in only_mobile["methods"]] == ["sms"]
    assert nothing["methods"] == []


def test_no_contact_is_409(api, directory, recorder):
    directory.upsert("+94112000004", "Nothing Here", None, None, "en")
    ticket = lookup(api.client, "Nothing Here", "011 200 0004").json()["ticket"]
    for method in ("sms", "email", "carrier_pigeon"):
        response = send(api.client, ticket, method)
        assert response.status_code == 409
        assert response.json() == {"code": "no_contact"}
    assert recorder.sent == []


def test_method_missing_for_customer_is_409(api, directory):
    directory.upsert("+94112000002", "Email Only", None, "only@gmail.com", "en")
    ticket = lookup(api.client, "Email Only", "011 200 0002").json()["ticket"]
    assert send(api.client, ticket, "sms").status_code == 409


def test_bad_ticket(api):
    response = send(api.client, "not-a-ticket")
    assert response.status_code == 401
    assert response.json() == {"code": "ticket_invalid"}


def test_user_session_token_is_not_a_ticket(api, make_user):
    from auth.tokens import AUDIENCE_USER, issue_token

    token, _ = issue_token("1", AUDIENCE_USER, 5, extra={"tel": PHONE_E164})
    assert send(api.client, token).status_code == 401


def test_ticket_is_not_a_session(api):
    ticket = lookup(api.client).json()["ticket"]
    api.client.cookies.set("user_session", ticket)
    assert api.client.get("/verify/session").json() == {"verified": False}


# Code rules


def test_code_is_six_digits_and_stored_only_as_hash(api, recorder, db_schema):
    ticket, sent = start(api, recorder)
    code = recorder.last_code
    assert len(code) == 6 and code.isdigit()
    from verification import models

    with db_schema.engine.begin() as conn:
        row = conn.execute(sa.select(models.challenges)).one()
    assert code not in "".join(str(v) for v in row)
    assert row.purpose == "verify"
    assert row.code_hash and len(row.code_hash) == 64
    assert row.user_id is None
    assert MOBILE not in "".join(str(v) for v in row) and PHONE_E164 not in "".join(str(v) for v in row)


def test_expires_at_120_seconds_on_the_server_clock(api, recorder, clock):
    ticket, sent = start(api, recorder)
    clock.advance(120)
    response = verify(api.client, sent["challenge_id"], recorder.last_code)
    assert response.status_code == 410
    assert response.json() == {"code": "expired"}


def test_still_valid_just_before_120_seconds(api, recorder, clock):
    ticket, sent = start(api, recorder)
    clock.advance(119)
    assert verify(api.client, sent["challenge_id"], recorder.last_code).status_code == 200


def test_single_use(api, recorder):
    ticket, sent = start(api, recorder)
    code = recorder.last_code
    assert verify(api.client, sent["challenge_id"], code).status_code == 200
    again = verify(api.new_client(), sent["challenge_id"], code)
    assert again.status_code == 401
    assert again.json()["code"] == "invalid"


def test_three_wrong_attempts_then_locked(api, recorder):
    ticket, sent = start(api, recorder)
    bad = wrong(recorder.last_code)
    first = verify(api.client, sent["challenge_id"], bad)
    assert first.status_code == 401 and first.json() == {"code": "invalid", "attempts_left": 2}
    second = verify(api.client, sent["challenge_id"], bad)
    assert second.status_code == 401 and second.json()["attempts_left"] == 1
    third = verify(api.client, sent["challenge_id"], bad)
    assert third.status_code == 423 and third.json()["code"] == "locked"
    # Even the right code is refused once locked.
    assert verify(api.client, sent["challenge_id"], recorder.last_code).status_code == 423


def test_two_wrong_then_right_still_works(api, recorder):
    ticket, sent = start(api, recorder)
    bad = wrong(recorder.last_code)
    verify(api.client, sent["challenge_id"], bad)
    verify(api.client, sent["challenge_id"], bad)
    assert verify(api.client, sent["challenge_id"], recorder.last_code).status_code == 200


def test_malformed_code_counts_as_an_attempt(api, recorder):
    ticket, sent = start(api, recorder)
    response = verify(api.client, sent["challenge_id"], "12ab")
    assert response.status_code == 401 and response.json()["attempts_left"] == 2


def test_unknown_challenge_is_invalid(api):
    response = verify(api.client, "f" * 32, "123456")
    assert response.status_code == 401
    assert response.json()["code"] == "invalid"


def test_resend_invalidates_the_previous_challenge(api, recorder, clock):
    ticket, first = start(api, recorder)
    old_code = recorder.last_code
    clock.advance(31)
    second = send(api.client, ticket, "sms").json()
    new_code = recorder.last_code
    assert second["challenge_id"] != first["challenge_id"]
    assert verify(api.client, first["challenge_id"], old_code).status_code == 401
    assert verify(api.client, second["challenge_id"], new_code).status_code == 200


def test_cooldown(api, recorder, clock):
    ticket, first = start(api, recorder)
    again = send(api.client, ticket, "sms")
    assert again.status_code == 429
    body = again.json()
    assert body["code"] == "cooldown" and 1 <= body["retry_after"] <= 30
    assert again.headers["retry-after"] == str(body["retry_after"])
    assert len(recorder.sent) == 1  # a refused request sends nothing and creates nothing
    clock.advance(29)
    assert send(api.client, ticket, "sms").status_code == 429
    clock.advance(1)
    assert send(api.client, ticket, "sms").status_code == 200


def test_rate_limit_per_number(api, recorder, clock, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR", 2)
    ticket = lookup(api.client).json()["ticket"]
    for _ in range(2):
        assert send(api.client, ticket, "sms").status_code == 200
        clock.advance(31)
    blocked = send(api.client, ticket, "sms")
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "rate_limited" and blocked.json()["retry_after"] > 0
    # Another client address is blocked too: the limit is per number.
    other = api.new_client()
    ticket2 = lookup(other).json()["ticket"]
    assert send(other, ticket2, "sms").json()["code"] == "rate_limited"
    clock.advance(3600)
    assert send(api.client, ticket, "sms").status_code == 200


def test_rate_limit_per_ip(api, directory, recorder, clock, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 2)
    for i in range(1, 4):
        directory.upsert(f"+9411200010{i}", f"Test Person {i}", MOBILE, None, "en")
    results = []
    for i in range(1, 4):
        # Tickets come from other addresses: lookups count against the limit too.
        ticket = lookup(api.new_client(), f"Test Person {i}", f"011 200 010{i}").json()["ticket"]
        results.append(send(api.client, ticket, "sms"))
    assert [r.status_code for r in results] == [200, 200, 429]
    assert results[2].json()["code"] == "rate_limited"
    # A different address is unaffected.
    other = api.new_client()
    ticket = lookup(other, "Test Person 3", "011 200 0103").json()["ticket"]
    assert send(other, ticket, "sms").status_code == 200


def test_lookup_rate_limit_per_ip(api, monkeypatch):
    import config

    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 3)
    codes = [lookup(api.client, name="Wrong Name").status_code for _ in range(4)]
    assert codes == [422, 422, 422, 429]
    last = lookup(api.client)
    assert last.status_code == 429 and last.json()["code"] == "rate_limited"
    assert "retry_after" in last.json()


def test_verify_rate_limit_per_ip(api, recorder):
    ticket, sent = start(api, recorder)
    bad = wrong(recorder.last_code)
    statuses = [verify(api.client, "e" * 32, bad).status_code for _ in range(12)]
    assert 429 in statuses


def test_delivery_failure_is_503_and_starts_no_cooldown(verification, directory, clock):
    from otp.sender import OtpSender, SenderDeliveryError
    from verification import routes

    class Broken(OtpSender):
        channel = "sms"
        name = "broken"

        def send(self, destination, code, language):
            raise SenderDeliveryError("down")

    verification.set_service(verification.VerificationService(sender_resolver=lambda c: Broken(), clock=clock))
    try:
        app = FastAPI()
        app.include_router(routes.router)
        client = make_client(app)
        ticket = lookup(client).json()["ticket"]
        first = send(client, ticket)
        assert first.status_code == 503 and first.json() == {"code": "unavailable"}
        assert send(client, ticket).status_code == 503  # not 429 cooldown
        client.close()
    finally:
        verification.set_service(None)


def test_no_sender_for_channel_is_503(verification, directory, clock):
    from verification import routes

    verification.set_service(verification.VerificationService(sender_resolver=lambda c: None, clock=clock))
    try:
        app = FastAPI()
        app.include_router(routes.router)
        client = make_client(app)
        assert send(client, lookup(client).json()["ticket"]).status_code == 503
        client.close()
    finally:
        verification.set_service(None)


# Privacy


def test_no_unmasked_contact_in_any_response(api, recorder, clock):
    texts = []
    for response in (
        lookup(api.client),
        lookup(api.client, name="Wrong"),
        lookup(api.client, phone="011 299 9999"),
    ):
        texts.append(response.text)
        texts.append(str(dict(response.headers)))
    ticket = lookup(api.client).json()["ticket"]
    for method in ("sms", "email", "pigeon"):
        response = send(api.client, ticket, method)
        texts += [response.text, str(dict(response.headers))]
    sent = send(api.client, ticket, "sms")
    texts.append(sent.text)
    done = verify(api.client, "0" * 32, "123456")
    texts.append(done.text)
    # A full successful run as well.
    clock.advance(31)
    api2 = api.new_client()
    t2 = lookup(api2).json()["ticket"]
    s2 = send(api2, t2, "email").json()
    good = verify(api2, s2["challenge_id"], recorder.last_code)
    texts += [good.text, str(dict(good.headers)), api2.get("/verify/session").text]
    joined = "\n".join(texts)
    for secret in (MOBILE, "770000123", "0770000123", EMAIL, "demo.customer", "demo"):
        assert secret not in joined, secret
    # The ticket itself carries no contact either.
    import base64
    import json

    payload = ticket.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert "770000123" not in json.dumps(claims) and "gmail" not in json.dumps(claims)


def test_no_code_in_logs_or_audit(api, recorder, capsys, caplog, db_schema):
    caplog.set_level(logging.DEBUG)
    ticket, sent = start(api, recorder)
    code = recorder.last_code
    verify(api.client, sent["challenge_id"], wrong(code))
    verify(api.client, sent["challenge_id"], code)
    out = capsys.readouterr()
    assert code not in out.out and code not in out.err
    assert code not in caplog.text
    assert "VERIFY" in out.out  # outcomes are logged, without secrets
    from verification import models

    with db_schema.engine.begin() as conn:
        rows = conn.execute(sa.select(models.VerificationAuditRow.__table__)).all()
    assert rows
    dump = "\n".join(str(tuple(r)) for r in rows)
    for secret in (code, PHONE_E164, "0112000001", "112000001", MOBILE, "770000123", EMAIL):
        assert secret not in dump
    events = {r.event for r in rows}
    assert {"lookup", "send", "verify"} <= events


def test_audit_outcomes_are_recorded(api, recorder, db_schema):
    lookup(api.client, name="Wrong")
    start(api, recorder)
    from verification import models

    with db_schema.engine.begin() as conn:
        rows = conn.execute(sa.select(models.VerificationAuditRow.__table__)).all()
    pairs = {(r.event, r.outcome) for r in rows}
    assert ("lookup", "invalid_details") in pairs
    assert ("lookup", "ok") in pairs
    assert ("send", "sent") in pairs
    assert all(r.phone_hash is None or len(r.phone_hash) == 64 for r in rows)


def test_console_sender_prints_only_masked_destination(verification, directory, clock, capsys, monkeypatch):
    """The dev console sender is the one place a code is printed; it still
    shows only the masked destination."""
    import config
    from verification import routes, senders

    monkeypatch.setattr(config, "VERIFY_OTP_SENDER", "console")
    verification.set_service(verification.VerificationService(clock=clock))
    try:
        app = FastAPI()
        app.include_router(routes.router)
        client = make_client(app)
        send(client, lookup(client).json()["ticket"], "sms")
        out = capsys.readouterr().out
        assert "OTP DEV ONLY" in out
        assert "07X XXX X123" in out
        assert "770000123" not in out
        client.close()
    finally:
        verification.set_service(None)


# Units


def test_masks():
    from verification.service import mask_email_local
    from otp.masking import mask_mobile

    assert mask_mobile("+94770000123") == "07X XXX X123"
    assert mask_email_local("a@gmail.com") == "XXXXXXXXX@gmail.com"
    assert mask_email_local("averyveryverylonglocalpart@gmail.com") == "XXXXXXXXX@gmail.com"


def test_namematch_unit():
    from verification.namematch import names_match, normalize_name

    assert normalize_name("  Nimal   D'Silva-Perera. ") == "nimal dsilvaperera"
    assert names_match("NIMAL DSILVAPERERA", normalize_name("Nimal D'Silva-Perera"))
    assert not names_match("", "")
    assert not names_match("a", "b")
    # Sinhala combining marks survive normalization.
    sinhala = "නිමල් පෙරේරා"
    assert names_match(sinhala + " ", normalize_name(sinhala))
    assert normalize_name(sinhala) == sinhala


def test_senders_pick_by_config(monkeypatch):
    import config
    from verification import senders

    monkeypatch.setattr(config, "VERIFY_OTP_SENDER", "sms,email")
    assert type(senders.sender_for("sms")).__name__ == "VerifySmsSender"
    assert type(senders.sender_for("email")).__name__ == "VerifyEmailSender"
    monkeypatch.setattr(config, "VERIFY_OTP_SENDER", "sms")
    assert senders.sender_for("email") is None
    monkeypatch.setattr(config, "VERIFY_OTP_SENDER", "console")
    assert type(senders.sender_for("email")).__name__ == "ConsoleOtpSender"
    monkeypatch.setattr(config, "VERIFY_OTP_SENDER", "typo")
    assert senders.sender_for("sms") is None


def test_message_body_says_two_minutes_and_contains_code(monkeypatch):
    from verification import senders

    body = senders.sms_body("123456", "en")
    assert "123456" in body and "2 minutes" in body
    assert "123456" in senders.sms_body("123456", "si")
    assert senders.email_subject("en")


def test_hub_lookup_stub_returns_none():
    from verification.lookup import HubCustomerLookup

    assert HubCustomerLookup().find("+94112000001") is None
    assert HubCustomerLookup().get(1) is None
