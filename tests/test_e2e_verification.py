"""End to end: the verification wizard over HTTP (plan part 3, R3).

Black box against /verify/lookup, /verify/otp/send, /verify/otp/verify,
/verify/session and /verify/logout, using the contract of section 3.3:

  * wrong name and unknown number give the IDENTICAL response (ADR 3.2)
  * the code lives 120 s, works once, allows 3 attempts, a resend kills the
    previous challenge, a second send within 30 s is refused
  * hourly rate limits per address and per number, equal for known and
    unknown numbers
  * masks look like "07X XXX X123" and "XXXXXXXXX@gmail.com", and no
    unmasked contact ever leaves the server
  * the OTP code never reaches a log line or the audit table
  * after the wizard, the question a guest was refused is answered

How the test controls things without knowing internals:
  * the OTP code is captured by replacing ConsoleOtpSender.send (the plan
    reuses otp.sender); the recorded destination also proves WHERE it went
  * time passes by moving the stored challenge timestamps back (the plan
    reuses the otp_challenges table), which is what real time would do
  * the customer is inserted straight into the customer_directory table
    using its documented columns

Detection: skips until the /verify routes exist (in server.app, or as a
router in a verification module) and until customer_directory exists.
"""

import itertools
import logging
import re
import threading
import time
import unicodedata
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import app_paths, make_client, optional_import, require_module
from test_e2e_access import BLOCKED, assert_canned_blocked_reply, access_wired  # noqa: F401
from test_e2e_chain import GEMINI_REPLY, llm_env  # noqa: F401

COOLDOWN = 30
TTL = 120

_seq = itertools.count(1)


# The app under test


@pytest.fixture
def verify_app(server_module):
    app = server_module.app
    if "/verify/lookup" in app_paths(app):
        return app
    router = None
    for name in ("verification.routes", "verification.router", "verification.api"):
        module = optional_import(name)
        if module is not None and getattr(module, "router", None) is not None:
            router = module.router
            break
    if router is None:
        pytest.skip("backend/verification is not delivered yet (no /verify router)")
    fallback = FastAPI()
    fallback.include_router(router)
    if "/verify/lookup" not in app_paths(fallback):
        pytest.skip("the verification router does not serve /verify/lookup")
    return fallback


# Database preparation


def _create_tables_from(engine, module):
    for value in list(vars(module).values()):
        if isinstance(value, sa.Table):
            value.create(engine, checkfirst=True)
        elif isinstance(value, sa.MetaData) and value.tables:
            value.create_all(engine)


@pytest.fixture
def wizard_db(db_schema, verify_app):
    engine = db_schema.engine
    for name in (
        "verification.tables",
        "verification.models",
        "verification.directory",
        "verification.audit",
        "verification.store",
    ):
        module = optional_import(name)
        if module is not None:
            _create_tables_from(engine, module)
    db_schema.Base.metadata.create_all(engine)

    otp_tables = optional_import("otp.tables")
    if otp_tables is not None:
        otp_tables.challenges.create(engine, checkfirst=True)
    inspector = sa.inspect(engine)
    if not inspector.has_table("customer_directory"):
        pytest.skip("customer_directory table does not exist (migration 20261009_0003 not applied)")
    if inspector.has_table("otp_challenges"):
        have = {c["name"] for c in inspector.get_columns("otp_challenges")}
        with engine.begin() as conn:
            for column in ("purpose", "wizard_ref"):
                if column not in have:
                    conn.execute(sa.text(f"ALTER TABLE otp_challenges ADD COLUMN {column} VARCHAR(64)"))

    def _wipe():
        insp = sa.inspect(engine)
        with engine.begin() as conn:
            for table in ("otp_challenges", "verification_audit", "customer_directory"):
                if insp.has_table(table):
                    conn.execute(sa.text(f"DELETE FROM {table}"))

    _wipe()
    yield engine
    _wipe()


def normalize_name(name):
    text = unicodedata.normalize("NFC", name).casefold()
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    return " ".join(text.split())


def _filler(column):
    kind = column.type
    if isinstance(kind, sa.Boolean):
        return True
    if isinstance(kind, sa.Integer):
        return 0
    if isinstance(kind, sa.DateTime):
        return datetime.utcnow()
    return ""


class Customer:
    def __init__(self, name, phone_raw, phone_e164, mobile, email, language):
        self.name = name
        self.phone_raw = phone_raw
        self.phone_e164 = phone_e164
        self.mobile = mobile
        self.email = email
        self.language = language

    @property
    def mobile_mask(self):
        return f"07X XXX X{self.mobile[-3:]}"


@pytest.fixture
def make_customer(wizard_db):
    table = sa.Table("customer_directory", sa.MetaData(), autoload_with=wizard_db)

    def _make(name="Nimal Perera", mobile=True, email=True, language="en", active=True):
        n = next(_seq)
        subscriber = f"{3000000 + n:07d}"
        customer = Customer(
            name=name,
            phone_raw=f"011 {subscriber[:3]} {subscriber[3:]}",
            phone_e164=f"+9411{subscriber}",
            mobile=f"+9477{2000000 + n:07d}" if mobile else None,
            email=f"nimalp{n:04d}@gmail.com" if email else None,
            language=language,
        )
        values = {
            "phone_e164": customer.phone_e164,
            "name_normalized": normalize_name(name),
            "display_name": name,
            "mobile_e164": customer.mobile,
            "email": customer.email,
            "preferred_language": language,
            "active": active,
        }
        row = {key: value for key, value in values.items() if key in table.c}
        for column in table.c:
            if column.name in row or column.primary_key and column.autoincrement is not False:
                continue
            if not column.nullable and column.default is None and column.server_default is None:
                row[column.name] = _filler(column)
        with wizard_db.begin() as conn:
            conn.execute(sa.insert(table).values(**row))
        return customer

    return _make


# Capturing the code


class Outbox:
    def __init__(self):
        self.sent = []
        self.lock = threading.Lock()

    def record(self, destination, code, language):
        with self.lock:
            self.sent.append({"destination": destination, "code": code, "language": language})

    def wait_for(self, count, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.sent) >= count:
                    return self.sent[count - 1]
            time.sleep(0.02)
        raise AssertionError(f"expected {count} code(s) to be sent, saw {len(self.sent)}")

    @property
    def last(self):
        return self.sent[-1]


@pytest.fixture
def outbox(monkeypatch):
    sender = require_module("otp.sender", "backend/otp not delivered yet")
    box = Outbox()
    monkeypatch.setattr(
        sender.ConsoleOtpSender, "send", lambda self, destination, code, language: box.record(destination, code, language)
    )
    return box


@pytest.fixture
def client(verify_app, wizard_db):
    c = make_client(verify_app)
    yield c
    c.close()


# Helpers


def do_lookup(c, name, phone, language="en"):
    return c.post("/verify/lookup", json={"name": name, "phone": phone, "language": language})


def do_send(c, ticket, method="sms", language="en"):
    return c.post("/verify/otp/send", json={"ticket": ticket, "method": method, "language": language})


def do_verify(c, challenge_id, code):
    return c.post("/verify/otp/verify", json={"challenge_id": challenge_id, "code": code})


def method_of(lookup_body, method_id):
    for method in lookup_body["methods"]:
        if method["id"] == method_id:
            return method
    raise AssertionError(f"no {method_id} method in {lookup_body!r}")


def start_challenge(c, outbox, customer, method="sms", language="en", expected_sends=None):
    """Lookup and send; returns (challenge_id, code, send response body)."""
    before = len(outbox.sent)
    lookup = do_lookup(c, customer.name, customer.phone_raw, language)
    assert lookup.status_code == 200, lookup.text
    sent = do_send(c, lookup.json()["ticket"], method, language)
    assert sent.status_code == 200, sent.text
    entry = outbox.wait_for(before + 1)
    return sent.json()["challenge_id"], entry["code"], sent.json()


def wrong_code(code):
    return f"{(int(code) + 1) % 1000000:06d}"


def age_challenges(engine, seconds):
    """What `seconds` of real time would do to every stored challenge."""
    table = sa.Table("otp_challenges", sa.MetaData(), autoload_with=engine)
    names = [n for n in ("created_at", "expires_at", "resend_at", "last_sent_at", "sent_at", "cooldown_until") if n in table.c]
    with engine.begin() as conn:
        rows = conn.execute(sa.select(table)).mappings().all()
        for row in rows:
            changes = {n: row[n] - timedelta(seconds=seconds) for n in names if row[n] is not None}
            if changes:
                conn.execute(sa.update(table).where(table.c.id == row["id"]).values(**changes))


def challenge_rows(engine):
    table = sa.Table("otp_challenges", sa.MetaData(), autoload_with=engine)
    with engine.begin() as conn:
        return [dict(r) for r in conn.execute(sa.select(table)).mappings()]


def standalone(text, token):
    """True when `token` appears as its own number, not inside a hash,
    an id or a fractional second."""
    return re.search(rf"(?<![0-9A-Za-z.]){re.escape(token)}(?![0-9A-Za-z])", text) is not None


# Wrong name and unknown number are the same answer


def test_wrong_name_and_unknown_number_give_the_identical_response(client, make_customer):
    customer = make_customer()
    wrong_name = do_lookup(client, "Kamal Silva", customer.phone_raw)
    unknown = do_lookup(client, customer.name, "011 999 8877")
    assert wrong_name.status_code == unknown.status_code == 422
    assert wrong_name.json() == unknown.json()
    assert wrong_name.json()["code"] == "invalid_details"
    assert "ticket" not in wrong_name.json() and "methods" not in wrong_name.json()
    assert wrong_name.headers.get("cache-control", "").lower().find("no-store") >= 0
    assert unknown.headers.get("cache-control", "").lower().find("no-store") >= 0


def test_a_deactivated_customer_looks_exactly_like_an_unknown_number(client, make_customer):
    customer = make_customer(active=False)
    inactive = do_lookup(client, customer.name, customer.phone_raw)
    unknown = do_lookup(client, customer.name, "011 998 8776")
    assert inactive.status_code == unknown.status_code == 422
    assert inactive.json() == unknown.json()


def test_only_impossible_syntax_is_told_apart(client, make_customer):
    make_customer()
    response = do_lookup(client, "Nimal Perera", "abc")
    assert response.status_code == 422
    assert response.json()["code"] == "phone_format"


# Lookup and masking


@pytest.mark.parametrize(
    "language,name",
    [("en", "Nimal Perera"), ("si", "නිමල් පෙරේරා"), ("ta", "நிமல் பெரேரா")],
)
def test_lookup_returns_masked_methods_and_an_opaque_ticket(client, make_customer, language, name):
    customer = make_customer(name=name, language=language)
    response = do_lookup(client, name, customer.phone_raw, language)
    assert response.status_code == 200, response.text
    body = response.json()
    assert response.headers.get("cache-control", "").lower().find("no-store") >= 0
    sms = method_of(body, "sms")
    email = method_of(body, "email")
    assert re.fullmatch(r"07X XXX X\d{3}", sms["masked"]), sms
    assert sms["masked"] == customer.mobile_mask
    assert re.fullmatch(r"X+@gmail\.com", email["masked"]), email
    ticket = body["ticket"]
    assert isinstance(ticket, str) and len(ticket) > 20
    for secret in (customer.phone_e164, customer.phone_e164[3:], name, customer.mobile, customer.mobile[3:], customer.email):
        assert secret not in ticket, "the ticket must be opaque"
    text = response.text
    for secret in (customer.mobile, customer.mobile[3:], "0" + customer.mobile[3:], customer.email, customer.email.split("@")[0]):
        assert secret not in text, f"unmasked contact {secret!r} leaked in the lookup response"


def test_name_matching_ignores_case_spacing_and_punctuation(client, make_customer):
    customer = make_customer(name="Nimal Perera")
    for typed in ("nimal perera", "  NIMAL   PERERA ", "Nimal, Perera."):
        response = do_lookup(client, typed, customer.phone_raw)
        assert response.status_code == 200, (typed, response.text)


def test_phone_is_accepted_in_common_shapes(client, make_customer):
    customer = make_customer()
    digits = customer.phone_e164[3:]
    for typed in (f"0{digits}", f"+94{digits}", f"0094{digits}", customer.phone_raw):
        response = do_lookup(client, customer.name, typed)
        assert response.status_code == 200, (typed, response.text)


def test_a_customer_with_only_a_mobile_is_offered_only_sms(client, make_customer):
    customer = make_customer(email=False)
    body = do_lookup(client, customer.name, customer.phone_raw).json()
    assert [m["id"] for m in body["methods"]] == ["sms"]


# Sending


def test_send_returns_the_contract_and_delivers_to_the_real_contact(client, make_customer, outbox):
    customer = make_customer()
    lookup = do_lookup(client, customer.name, customer.phone_raw, "si").json()
    response = do_send(client, lookup["ticket"], "sms", "si")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expires_in"] == TTL
    assert body["resend_in"] == COOLDOWN
    assert body["masked"] == method_of(lookup, "sms")["masked"]
    assert body["challenge_id"]
    entry = outbox.wait_for(1)
    assert entry["destination"] == customer.mobile
    assert entry["language"] == "si"
    assert re.fullmatch(r"\d{6}", entry["code"])
    assert entry["code"] not in response.text
    assert customer.mobile not in response.text and customer.email not in response.text


def test_send_by_email_goes_to_the_email_address(client, make_customer, outbox):
    customer = make_customer()
    lookup = do_lookup(client, customer.name, customer.phone_raw).json()
    response = do_send(client, lookup["ticket"], "email")
    assert response.status_code == 200, response.text
    assert response.json()["masked"] == method_of(lookup, "email")["masked"]
    assert outbox.wait_for(1)["destination"] == customer.email


def test_choosing_a_method_the_customer_does_not_have_is_no_contact(client, make_customer, outbox):
    customer = make_customer(email=False)
    ticket = do_lookup(client, customer.name, customer.phone_raw).json()["ticket"]
    response = do_send(client, ticket, "email")
    assert response.status_code == 409
    assert response.json()["code"] == "no_contact"
    assert outbox.sent == []


def test_a_forged_or_empty_ticket_is_refused(client, make_customer, outbox):
    make_customer()
    for ticket in ("garbage", "", "a.b.c"):
        response = do_send(client, ticket, "sms")
        assert 400 <= response.status_code < 500, (ticket, response.status_code)
    assert outbox.sent == []


def test_the_code_lifetime_stored_is_exactly_120_seconds(client, make_customer, outbox, wizard_db):
    customer = make_customer()
    start_challenge(client, outbox, customer)
    rows = challenge_rows(wizard_db)
    assert len(rows) == 1
    assert rows[0]["expires_at"] - rows[0]["created_at"] == timedelta(seconds=TTL)


# Verify: success


def test_correct_code_signs_in_with_an_otp_session(client, make_customer, outbox):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    response = do_verify(client, challenge_id, code)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == customer.name
    assert code not in response.text
    assert customer.mobile not in response.text and customer.email not in response.text

    set_cookie = response.headers.get("set-cookie", "")
    assert "user_session=" in set_cookie and "httponly" in set_cookie.lower()

    from auth.tokens import AUDIENCE_USER, decode_token

    claims = decode_token(client.cookies.get("user_session"), AUDIENCE_USER)
    assert "otp" in claims.get("amr", []), claims

    session = client.get("/verify/session").json()
    assert session["verified"] is True
    assert session.get("display_name") == customer.name


def test_logout_ends_the_verified_session(client, make_customer, outbox):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    assert do_verify(client, challenge_id, code).status_code == 200
    assert client.post("/verify/logout").status_code in (200, 204)
    assert client.get("/verify/session").json()["verified"] is False


def test_no_session_means_not_verified(client):
    response = client.get("/verify/session")
    assert response.status_code == 200
    assert response.json()["verified"] is False


# Verify: expiry, single use, attempts


def test_code_still_works_just_inside_120_seconds(client, make_customer, outbox, wizard_db):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    age_challenges(wizard_db, TTL - 1)
    assert do_verify(client, challenge_id, code).status_code == 200


def test_code_is_expired_after_120_seconds(client, make_customer, outbox, wizard_db):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    age_challenges(wizard_db, TTL + 1)
    response = do_verify(client, challenge_id, code)
    assert response.status_code == 410, response.text
    assert response.json()["code"] == "expired"
    assert client.get("/verify/session").json()["verified"] is False


def test_a_code_works_only_once(client, make_customer, outbox, new_client_for):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    assert do_verify(client, challenge_id, code).status_code == 200
    other = new_client_for()
    replay = do_verify(other, challenge_id, code)
    assert replay.status_code in (401, 410, 423), replay.text
    assert other.get("/verify/session").json()["verified"] is False


def test_three_wrong_attempts_lock_the_challenge_even_for_the_right_code(client, make_customer, outbox):
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    wrong = wrong_code(code)

    first = do_verify(client, challenge_id, wrong)
    assert first.status_code == 401 and first.json()["code"] == "invalid"
    assert first.json()["attempts_left"] == 2
    second = do_verify(client, challenge_id, wrong)
    assert second.status_code == 401 and second.json()["attempts_left"] == 1
    third = do_verify(client, challenge_id, wrong)
    assert third.status_code in (401, 423), third.text
    if third.status_code == 401:
        assert third.json()["attempts_left"] == 0

    final = do_verify(client, challenge_id, code)
    assert final.status_code == 423, "the third failure must lock the challenge for good"
    assert final.json()["code"] == "locked"
    assert client.get("/verify/session").json()["verified"] is False


def test_an_unknown_challenge_is_refused_not_a_server_error(client, make_customer):
    make_customer()
    response = do_verify(client, "0" * 32, "123456")
    assert 400 <= response.status_code < 500


def test_a_malformed_code_is_refused_not_a_server_error(client, make_customer, outbox):
    customer = make_customer()
    challenge_id, _, _ = start_challenge(client, outbox, customer)
    for bad in ("", "12345", "abcdef", "1234567"):
        response = do_verify(client, challenge_id, bad)
        assert 400 <= response.status_code < 500, (bad, response.status_code)


# Resend and cooldown


def test_resend_within_30_seconds_is_a_cooldown(client, make_customer, outbox):
    customer = make_customer()
    ticket = do_lookup(client, customer.name, customer.phone_raw).json()["ticket"]
    assert do_send(client, ticket, "sms").status_code == 200
    again = do_send(client, ticket, "sms")
    assert again.status_code == 429, again.text
    body = again.json()
    assert body["code"] == "cooldown"
    assert 0 < body["retry_after"] <= COOLDOWN
    assert len(outbox.sent) == 1, "a refused resend must not send a second code"


def test_resend_after_the_cooldown_invalidates_the_previous_code(client, make_customer, outbox, wizard_db):
    customer = make_customer()
    ticket = do_lookup(client, customer.name, customer.phone_raw).json()["ticket"]
    first = do_send(client, ticket, "sms").json()
    first_code = outbox.wait_for(1)["code"]
    age_challenges(wizard_db, COOLDOWN + 1)
    second_response = do_send(client, ticket, "sms")
    assert second_response.status_code == 200, second_response.text
    second = second_response.json()
    second_code = outbox.wait_for(2)["code"]
    assert second["challenge_id"] != first["challenge_id"]

    if first_code != second_code:  # one in a million chance of equal codes
        stale = do_verify(client, first["challenge_id"], first_code)
        assert stale.status_code in (401, 410, 423), "the previous challenge must be dead"
        assert client.get("/verify/session").json()["verified"] is False
    fresh = do_verify(client, second["challenge_id"], second_code)
    assert fresh.status_code == 200, fresh.text


# Rate limits


def _first_rate_limited(client, name, phone, limit=25):
    for attempt in range(1, limit + 1):
        response = do_lookup(client, name, phone)
        if response.status_code == 429:
            return attempt, response
    return None, None


def test_per_number_rate_limit_is_the_same_for_known_and_unknown_numbers(
    make_customer, verify_app, wizard_db
):
    customer = make_customer()
    known_client = make_client(verify_app)
    unknown_client = make_client(verify_app)
    try:
        known_at, known_response = _first_rate_limited(known_client, customer.name, customer.phone_raw)
        unknown_at, unknown_response = _first_rate_limited(unknown_client, customer.name, "011 997 7665")
    finally:
        known_client.close()
        unknown_client.close()
    assert known_at is not None, "no 429 within 25 lookups of the same number"
    assert known_at == unknown_at, "a known and an unknown number must hit the limit at the same attempt"
    assert known_at <= 21
    for response in (known_response, unknown_response):
        body = response.json()
        assert body["code"] == "rate_limited"
        assert isinstance(body["retry_after"], int) and body["retry_after"] > 0
    assert known_response.json() == unknown_response.json() or set(known_response.json()) == set(
        unknown_response.json()
    )


def test_per_address_rate_limit_and_a_new_address_is_unaffected(make_customer, verify_app, wizard_db):
    customer = make_customer()
    flooding = make_client(verify_app)
    other = make_client(verify_app)
    try:
        limited_at = None
        for attempt in range(1, 31):
            phone = f"011 {400 + attempt:03d} 5566"
            response = do_lookup(flooding, "Someone Else", phone)
            if response.status_code == 429:
                limited_at = attempt
                body = response.json()
                assert body["code"] == "rate_limited" and body["retry_after"] > 0
                break
        assert limited_at is not None, "no 429 within 30 lookups from one address"
        assert do_lookup(other, customer.name, customer.phone_raw).status_code == 200
    finally:
        flooding.close()
        other.close()


# Secrets stay out of logs and storage


def test_code_and_contacts_never_reach_logs_or_the_audit_table(
    client, make_customer, outbox, wizard_db, caplog
):
    caplog.set_level(logging.DEBUG)
    customer = make_customer()
    challenge_id, code, _ = start_challenge(client, outbox, customer)
    do_verify(client, challenge_id, wrong_code(code))
    do_verify(client, challenge_id, code)
    do_lookup(client, "Kamal Silva", customer.phone_raw)

    logged = caplog.text
    assert not standalone(logged, code), "the one time code appears in a log line"
    for secret in (customer.mobile, customer.mobile[3:], customer.email, customer.phone_e164):
        assert secret not in logged, f"{secret!r} appears in a log line"

    if sa.inspect(wizard_db).has_table("verification_audit"):
        table = sa.Table("verification_audit", sa.MetaData(), autoload_with=wizard_db)
        with wizard_db.begin() as conn:
            stored = str([dict(r) for r in conn.execute(sa.select(table)).mappings()])
        assert not standalone(stored, code)
        for secret in (customer.mobile, customer.email, customer.phone_e164, customer.phone_e164[3:], "127.0.0.1"):
            assert secret not in stored, f"{secret!r} stored in the audit table"

    stored_challenges = str(challenge_rows(wizard_db))
    assert not standalone(stored_challenges, code), "the plain code is stored on the challenge row"
    assert customer.mobile not in stored_challenges and customer.email not in stored_challenges


# The point of the wizard


@pytest.fixture
def new_client_for(verify_app):
    made = []

    def _new():
        c = make_client(verify_app)
        made.append(c)
        return c

    yield _new
    for c in made:
        c.close()


def test_verified_session_unlocks_the_question_that_was_blocked_for_a_guest(
    server_module, make_customer, outbox, access_wired, llm_env
):
    app = server_module.app
    paths = app_paths(app)
    if "/verify/lookup" not in paths:
        pytest.skip("/verify routes are not integrated into server.app yet (lead integration pending)")
    from conftest import server_source

    if "access" not in server_source():
        pytest.skip("server.py does not build an AccessContext from the session yet (lead integration pending)")

    customer = make_customer()
    question = BLOCKED["en"][0]
    c = make_client(app)
    try:
        before = c.post("/chat", json={"text": question, "language": "en"})
        assert_canned_blocked_reply(before.json()["reply"], "en")
        llm_env.assert_no_model_called()

        challenge_id, code, _ = start_challenge(c, outbox, customer)
        assert do_verify(c, challenge_id, code).status_code == 200

        after = c.post("/chat", json={"text": question, "language": "en"})
        assert after.status_code == 200, after.text
        assert after.json()["reply"] == GEMINI_REPLY
        assert len(llm_env.calls["gemini"]) == 1

        assert c.post("/verify/logout").status_code in (200, 204)
        again = c.post("/chat", json={"text": question, "language": "en"})
        assert_canned_blocked_reply(again.json()["reply"], "en")
        assert len(llm_env.calls["gemini"]) == 1, "after logout the guest is blocked again"
    finally:
        c.close()
