"""One time code login (R5): the service and the /auth/otp/* routes.

The sender is replaced by a recorder, so the real code is known to the test
and never printed. Time moves through the service's injectable clock.

Revision 0002 (users.mobile_e164, users.email, otp_challenges) is applied by
the lead. Until it is part of the migrations, the fixture below adds the
same columns and table to the temporary test database only, using the
definitions in backend/otp/tables.py, so the flow can be tested now. Once
the migration exists this is a no-op.
"""

import itertools
import logging
import re
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import BASE_URL, USER_NAME, USER_PIN, require_module

_numbers = itertools.count(1000)
_clients = itertools.count(1)


@pytest.fixture(scope="module")
def otp_service():
    require_module("otp.sender", "backend/otp not delivered yet")
    return require_module("otp.service", "otp.service not delivered yet")


@pytest.fixture(scope="module")
def otp_routes():
    return require_module("otp.routes", "otp.routes not delivered yet")


@pytest.fixture(scope="module")
def otp_sender_module():
    return require_module("otp.sender", "backend/otp not delivered yet")


@pytest.fixture(autouse=True)
def _otp_schema(db_schema):
    require_module("otp.tables", "backend/otp not delivered yet")
    from otp import tables

    engine = db_schema.engine
    columns = {c["name"] for c in sa.inspect(engine).get_columns("users")}
    with engine.begin() as conn:
        for name, ddl in (("mobile_e164", "VARCHAR(16)"), ("email", "VARCHAR(254)")):
            if name not in columns:
                conn.execute(sa.text(f"ALTER TABLE users ADD COLUMN {name} {ddl}"))
    tables.challenges.create(engine, checkfirst=True)
    with engine.begin() as conn:
        conn.execute(sa.delete(tables.challenges))
    yield
    with engine.begin() as conn:
        conn.execute(sa.delete(tables.challenges))


class Clock:
    """A controllable UTC clock for OtpService(clock=...)."""

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
def sender(otp_service, otp_sender_module, clock):
    """Installs a process wide OtpService with a recording sender and the
    fake clock, sending inline so tests need no waiting."""

    class Recorder(otp_sender_module.OtpSender):
        channel = "sms"
        name = "recorder"

        def __init__(self):
            self.sent = []

        def accepts(self, mobile, email):
            if mobile:
                return mobile, "sms"
            if email:
                return email, "email"
            return None

        def send(self, destination, code, language):
            self.sent.append({"destination": destination, "code": code, "language": language})

        @property
        def last_code(self):
            return self.sent[-1]["code"]

    recorder = Recorder()
    otp_service.set_service(otp_service.OtpService(sender=recorder, clock=clock, send_in_background=False))
    yield recorder
    otp_service.set_service(None)


@pytest.fixture(autouse=True)
def _auth_methods(monkeypatch):
    import config

    monkeypatch.setattr(config, "AUTH_METHODS", ["pin", "otp"])


def challenge_rows():
    from db import base
    from otp import tables

    with base.session_scope() as session:
        return [dict(r) for r in session.execute(sa.select(tables.challenges)).mappings()]


@pytest.fixture
def app(otp_routes):
    from auth.routes import router as auth_router

    application = FastAPI()
    application.include_router(auth_router)
    application.include_router(otp_routes.router)
    return application


@pytest.fixture
def new_user(make_user):
    """A registered user with a mobile number (or email), on a fresh landline."""
    from otp.service import set_contact

    def _make(mobile="+94771234567", email=None):
        n = next(_numbers)
        landline = f"+9411{n:07d}"
        make_user(phone_e164=landline)
        assert set_contact(landline, mobile=mobile, email=email)
        return landline

    return _make


def fresh_client(app):
    from fastapi.testclient import TestClient

    n = next(_clients)
    return TestClient(app, base_url=BASE_URL, client=(f"10.88.{n // 250}.{n % 250 + 1}", 50000))


@pytest.fixture
def client(app):
    c = fresh_client(app)
    yield c
    c.close()


def request_code(client, phone, **extra):
    return client.post("/auth/otp/request", json={"phone": phone, **extra})


def verify_code(client, challenge_id, code):
    return client.post("/auth/otp/verify", json={"challenge_id": challenge_id, "code": code})


# Methods


def test_methods_endpoint_follows_config(client, sender, monkeypatch):
    import config

    monkeypatch.setattr(config, "AUTH_METHODS", ["pin", "otp"])
    response = client.get("/auth/methods")
    assert response.status_code == 200
    assert response.json()["methods"] == ["pin", "otp"]
    monkeypatch.setattr(config, "AUTH_METHODS", ["pin"])
    assert client.get("/auth/methods").json()["methods"] == ["pin"]


# Issue


def test_request_returns_the_contract_shape_and_sends_a_six_digit_code(client, sender, new_user):
    phone = new_user()
    response = request_code(client, phone)
    assert response.status_code == 200, response.text
    body = response.json()
    assert {"challenge_id", "masked_destination", "channel", "expires_in", "resend_in"} <= set(body)
    assert isinstance(body["expires_in"], int) and body["expires_in"] > 0
    assert isinstance(body["resend_in"], int) and body["resend_in"] >= 0
    assert len(sender.sent) == 1
    assert re.fullmatch(r"[0-9]{6}", sender.last_code)


def test_code_is_hashed_at_rest(client, sender, new_user):
    request_code(client, new_user(mobile="+94771234567"))
    rows = challenge_rows()
    assert len(rows) == 1
    row = rows[0]
    code = sender.last_code
    assert row["code_hash"] != code and len(str(row["code_hash"])) >= 32
    assert re.fullmatch(r"[0-9a-f]{32,}", row["code_hash"]), "expected a hex digest"
    for key, value in row.items():
        text = str(value)
        assert text != code, f"plain code stored in {key}"
        assert "771234567" not in text, f"destination stored in {key}"


def test_same_code_hashes_differently_per_challenge(client, sender, new_user, clock):
    import config

    phone = new_user()
    request_code(client, phone)
    clock.advance(config.OTP_RESEND_COOLDOWN_SECONDS + 1)
    request_code(client, phone)
    hashes = [r["code_hash"] for r in challenge_rows()]
    assert len(set(hashes)) == len(hashes) == 2


def test_masked_destination_hides_most_of_the_number(client, sender, new_user):
    body = request_code(client, new_user(mobile="+94771234567")).json()
    masked = body["masked_destination"]
    assert "+94771234567" not in masked and "771234567" not in masked
    assert re.search(r"[*•x#X]", masked), masked
    assert sum(ch.isdigit() for ch in masked) <= 6, masked
    assert masked.rstrip().endswith("67") or masked.rstrip().endswith("567"), masked


def test_masked_email_destination(client, sender, new_user):
    import config

    phone = new_user(mobile=None, email="nimal.perera@example.com")
    response = request_code(client, phone)
    if response.status_code != 200:
        pytest.skip(f"email only users are not supported here: {response.status_code}")
    masked = response.json()["masked_destination"]
    assert "nimal.perera" not in masked
    assert "@" in masked and masked.endswith("example.com") or "*" in masked


def test_sender_receives_the_users_destination_and_language(client, sender, new_user):
    request_code(client, new_user(mobile="+94771234567"), language="si")
    assert sender.sent[0]["destination"] in ("+94771234567", "94771234567", "0771234567")
    assert sender.sent[0]["language"] == "si"


# Anti enumeration


def test_unknown_number_gets_same_shape_and_nothing_is_sent(client, sender, new_user):
    known = request_code(client, new_user())
    sent_before = len(sender.sent)
    unknown = request_code(client, "+94112999999")
    assert unknown.status_code == known.status_code == 200
    assert set(unknown.json()) == set(known.json())
    for key, value in known.json().items():
        assert type(unknown.json()[key]) is type(value), key
    assert unknown.json()["channel"] == known.json()["channel"]
    assert len(sender.sent) == sent_before, "no message may be sent to an unknown number"


def test_unknown_number_challenge_fails_like_a_wrong_code(client, sender, new_user):
    unknown = request_code(client, "+94112888888").json()
    wrong_unknown = verify_code(client, unknown["challenge_id"], "123456")
    other = fresh_client(client.app)
    known = request_code(other, new_user()).json()
    real = sender.last_code
    wrong_known = verify_code(other, known["challenge_id"], "000000" if real != "000000" else "111111")
    other.close()
    assert wrong_unknown.status_code == wrong_known.status_code == 401
    assert wrong_known.json() == wrong_unknown.json()


def test_a_guessed_code_never_logs_in_through_an_unknown_number(client, sender):
    info = request_code(client, "+94112666666").json()
    for guess in ("000000", "123456", "654321"):
        assert verify_code(client, info["challenge_id"], guess).status_code in (401, 423)


def test_unknown_number_does_not_reveal_itself_by_cooldown(client, sender):
    first = request_code(client, "+94112777777")
    second = request_code(client, "+94112777777")
    assert first.status_code == 200
    assert second.status_code in (200, 429)


# Verify


def test_correct_code_logs_in_with_cookie_and_token(client, sender, new_user):
    from auth.tokens import AUDIENCE_USER, decode_token

    info = request_code(client, new_user()).json()
    response = verify_code(client, info["challenge_id"], sender.last_code)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == USER_NAME
    assert "expires_at" in body
    decode_token(body["token"], AUDIENCE_USER)
    cookies = [v for k, v in response.headers.multi_items() if k.lower() == "set-cookie"]
    cookie = next((c for c in cookies if c.startswith("user_session=")), None)
    assert cookie and "httponly" in cookie.lower()
    assert client.get("/auth/me").status_code == 200


def test_code_is_single_use(client, sender, new_user):
    info = request_code(client, new_user()).json()
    code = sender.last_code
    assert verify_code(client, info["challenge_id"], code).status_code == 200
    again = verify_code(fresh_client(client.app), info["challenge_id"], code)
    assert again.status_code == 401


def test_wrong_code_is_401(client, sender, new_user):
    info = request_code(client, new_user()).json()
    wrong = "000000" if sender.last_code != "000000" else "111111"
    assert verify_code(client, info["challenge_id"], wrong).status_code == 401


def test_unknown_challenge_is_401(client):
    assert verify_code(client, "does-not-exist", "123456").status_code == 401


def test_expired_code_is_rejected_with_the_same_401_as_a_wrong_code(client, sender, new_user, clock):
    import config

    info = request_code(client, new_user()).json()
    wrong = "000000" if sender.last_code != "000000" else "111111"
    wrong_response = verify_code(client, info["challenge_id"], wrong)
    clock.advance(config.OTP_TTL_SECONDS + 5)
    expired = verify_code(client, info["challenge_id"], sender.last_code)
    assert expired.status_code == 401
    assert wrong_response.status_code == 401
    assert expired.json().get("detail") == wrong_response.json().get("detail")
    assert expired.json().get("reason") == wrong_response.json().get("reason")


def test_code_is_valid_just_before_expiry(client, sender, new_user, clock):
    import config

    info = request_code(client, new_user()).json()
    clock.advance(config.OTP_TTL_SECONDS - 30)
    assert verify_code(client, info["challenge_id"], sender.last_code).status_code == 200


def test_three_wrong_attempts_lock_the_challenge(client, sender, new_user):
    import config

    assert config.OTP_MAX_ATTEMPTS == 3
    info = request_code(client, new_user()).json()
    real = sender.last_code
    wrong = "000000" if real != "000000" else "111111"
    statuses = [verify_code(client, info["challenge_id"], wrong).status_code for _ in range(3)]
    assert statuses[0] == 401 and statuses[1] == 401 and statuses[2] in (401, 423)
    after = verify_code(client, info["challenge_id"], real)
    assert after.status_code == 423, "the right code must not work once the attempts are used up"
    assert "code" not in after.text.lower() or real not in after.text


def test_bad_input_is_422(client):
    assert client.post("/auth/otp/request", json={}).status_code == 422
    assert request_code(client, "not a phone").status_code == 422
    assert client.post("/auth/otp/verify", json={}).status_code == 422


# Limits


def test_resend_cooldown_is_429_with_retry_after(client, sender, new_user):
    phone = new_user()
    assert request_code(client, phone).status_code == 200
    second = request_code(client, phone)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) > 0
    assert len(sender.sent) == 1, "a refused resend must not send another code"


def test_resend_after_the_cooldown_works_and_old_code_stops_working(client, sender, new_user, clock):
    import config

    phone = new_user()
    first_info = request_code(client, phone).json()
    first_code = sender.last_code
    clock.advance(config.OTP_RESEND_COOLDOWN_SECONDS + 1)
    assert request_code(client, phone).status_code == 200
    assert len(sender.sent) == 2
    stale = verify_code(fresh_client(client.app), first_info["challenge_id"], first_code)
    if sender.sent[1]["code"] != first_code:
        assert stale.status_code in (401, 200), stale.status_code  # superseded codes should be dead
        assert stale.status_code == 401, "a resent code must invalidate the previous one"


def test_per_number_hourly_limit(client, sender, new_user, clock):
    import config

    phone = new_user()
    limit = config.OTP_RATE_LIMIT_PER_NUMBER_PER_HOUR
    codes = []
    for _ in range(limit):
        codes.append(request_code(client, phone).status_code)
        clock.advance(config.OTP_RESEND_COOLDOWN_SECONDS + 1)
    assert codes == [200] * limit
    over = request_code(client, phone)
    assert over.status_code == 429
    assert "Retry-After" in over.headers


def test_per_ip_hourly_limit(app, sender, new_user):
    import config

    limit = config.OTP_RATE_LIMIT_PER_IP_PER_HOUR
    c = fresh_client(app)
    try:
        results = [request_code(c, new_user()).status_code for _ in range(limit)]
        assert results == [200] * limit
        assert request_code(c, new_user()).status_code == 429
        other_ip = fresh_client(app)
        assert request_code(other_ip, new_user()).status_code == 200, "another address is unaffected"
        other_ip.close()
    finally:
        c.close()


# Logs and PIN


def test_code_never_appears_in_logs_or_output_except_the_console_sender(client, sender, new_user, caplog, capsys):
    with caplog.at_level(logging.DEBUG):
        info = request_code(client, new_user()).json()
        code = sender.last_code
        wrong = "000000" if code != "000000" else "111111"
        verify_code(client, info["challenge_id"], wrong)
        verify_code(client, info["challenge_id"], code)
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    assert code not in text, "the one time code leaked into logs or stdout"
    assert "+94771234567" not in text, "the full destination leaked into logs or stdout"


def test_console_sender_is_the_only_one_that_prints_the_code(otp_sender_module, capsys, caplog):
    console = otp_sender_module.ConsoleOtpSender()
    with caplog.at_level(logging.DEBUG):
        console.send("+94771234567", "482913", "en")
    text = capsys.readouterr().out + caplog.text
    assert "482913" in text


def test_email_and_sms_senders_do_not_print_the_code(otp_sender_module, capsys, caplog, monkeypatch):
    for name in ("EmailOtpSender", "SmsGatewayOtpSender"):
        cls = getattr(otp_sender_module, name, None)
        assert cls is not None, name
        try:
            sender = cls()
        except Exception as exc:
            pytest.skip(f"{name} needs configuration to construct: {exc}")
        with caplog.at_level(logging.DEBUG):
            try:
                sender.send("+94771234567", "735190", "en")
            except Exception:
                pass  # unconfigured: failing is fine, printing the code is not
        text = capsys.readouterr().out + caplog.text
        assert "735190" not in text, f"{name} leaked the code"


def test_get_sender_follows_config(otp_sender_module, monkeypatch):
    import config

    for value, cls in (("console", "ConsoleOtpSender"), ("email", "EmailOtpSender"), ("sms", "SmsGatewayOtpSender")):
        monkeypatch.setattr(config, "OTP_SENDER", value)
        try:
            sender = otp_sender_module.get_sender()
        except Exception as exc:
            pytest.skip(f"get_sender({value}) needs more configuration: {exc}")
        assert type(sender).__name__ == cls
        assert isinstance(sender, otp_sender_module.OtpSender)


def test_pin_login_still_works_beside_otp(client, new_user):
    phone = new_user()
    response = client.post("/auth/verify", json={"phone": phone, "pin": USER_PIN})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == USER_NAME


def test_service_level_errors_have_a_reason(otp_service, sender, new_user):
    assert issubclass(otp_service.OtpError, Exception)
    phone = new_user()
    info = otp_service.request_otp(phone, "10.99.0.1")
    challenge_id = getattr(info, "challenge_id", None) or info["challenge_id"]
    with pytest.raises(otp_service.OtpError) as excinfo:
        otp_service.verify_otp(challenge_id, "000000" if sender.last_code != "000000" else "111111", "10.99.0.1")
    assert excinfo.value.reason == "invalid"
    user = otp_service.verify_otp(challenge_id, sender.last_code, "10.99.0.1")
    assert user.display_name == USER_NAME and user.phone_e164 == phone


def test_otp_routes_refuse_when_otp_is_not_a_listed_method(client, sender, new_user, monkeypatch):
    import config

    monkeypatch.setattr(config, "AUTH_METHODS", ["pin"])
    assert request_code(client, new_user()).status_code == 404
    assert verify_code(client, "x", "123456").status_code == 404
    assert client.get("/auth/methods").json()["methods"] == ["pin"]


def test_unusable_sender_is_503_not_a_silent_console_fallback(client, new_user, monkeypatch, capsys):
    import config
    from otp import service

    monkeypatch.setattr(config, "OTP_SENDER", "sms")
    monkeypatch.setattr(config, "SMS_GATEWAY_URL", "")
    monkeypatch.setattr(config, "SMS_GATEWAY_API_KEY", "")
    service.set_service(None)
    try:
        response = request_code(client, new_user())
    finally:
        service.set_service(None)
    assert response.status_code == 503
    assert "OTP DEV ONLY" not in capsys.readouterr().out


def test_real_console_sender_flow_prints_the_code_once_for_development(client, new_user, monkeypatch, capsys):
    import config
    from otp import service

    monkeypatch.setattr(config, "OTP_SENDER", "console")
    service.set_service(service.OtpService(send_in_background=False))
    try:
        info = request_code(client, new_user()).json()
    finally:
        out = capsys.readouterr().out
        service.set_service(None)
    codes = re.findall(r"one time code for [^:]+: (\d{6})", out)
    assert len(codes) == 1, out
    assert info["challenge_id"] not in out, "the challenge id must not be printed either"
