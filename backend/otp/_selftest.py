"""Self test for one time code sign in, on a throwaway SQLite database.

    python -m backend.otp._selftest        (from the repository root)

The real data/app.db is never touched, and neither is the real MCP hub:
the hub backend is exercised against mcp_client.mock_hub only, because the
real hub's validate_customer_and_send_otp sends an SMS to a real customer.
Time is moved with an injectable clock, so expiry and the hourly limits
are tested without waiting.
"""

import contextlib
import io
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="otp_selftest_")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/selftest.db".replace("\\", "/")
os.environ["JWT_SECRET"] = "selftest-secret-selftest-secret-selftest-secret"
os.environ["FIELD_ENCRYPTION_KEY"] = "eXh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="
for _name in ("MCP_HUB_API_KEY", "MCP_HUB_TOOLS", "MCP_HUB_CUSTOMER_LOOKUP_TOOL", "SMTP_HOST", "SMS_GATEWAY_URL"):
    os.environ[_name] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlalchemy as sa  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
from auth import users as auth_users  # noqa: E402
from auth.dependencies import verified_user_from_token  # noqa: E402
from auth.routes import router as auth_router  # noqa: E402
from auth.users import AuthError  # noqa: E402
from db import migrate  # noqa: E402
from db.base import engine, session_scope  # noqa: E402
from otp import hashing, masking, sender as senders, service  # noqa: E402
from otp.contacts import set_contact  # noqa: E402
from otp.routes import router as otp_router  # noqa: E402
from otp.tables import challenges  # noqa: E402

_results = []


def check(name, condition, info=""):
    _results.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{('  ' + str(info)) if info and not condition else ''}")


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Capture(senders.OtpSender):
    channel = "sms"
    name = "capture"

    def __init__(self):
        self.sent = []

    def accepts(self, mobile, email):
        return (mobile, "sms") if mobile else ((email, "email") if email else None)

    def send(self, destination, code, language):
        self.sent.append({"to": destination, "code": code, "language": language})


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def apply_migrations():
    from alembic import command

    migrate.upgrade_to_head()
    cfg = migrate._alembic_config()
    real = migrate._MIGRATIONS_DIR / "versions"
    locations = [str(real)]
    if not any("0002" in f.name for f in real.glob("*.py")):
        locations.append(str(Path(__file__).resolve().parent / "proposed" / "versions"))
    cfg.set_main_option("version_locations", os.pathsep.join(locations).replace("%", "%%"))
    cfg.set_main_option("version_path_separator", "os")
    command.upgrade(cfg, "head")
    return cfg


_phone_counter = [100]


def new_user(mobile="0771234567", email=None, name="Test User"):
    _phone_counter[0] += 1
    phone = f"011 200 {_phone_counter[0]:04d}"
    auth_users.create_user(phone, name, "1234")
    set_contact(phone, mobile=mobile, email=email)
    return phone


def wrong_for(code):
    return "000000" if code != "000000" else "111111"


def main() -> int:
    logging.disable(logging.CRITICAL)
    print("migration 0002")
    cfg = apply_migrations()
    inspector = sa.inspect(engine)
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    check("users.mobile_e164 and users.email exist", {"mobile_e164", "email"} <= user_cols, user_cols)
    cols = {c["name"] for c in inspector.get_columns("otp_challenges")}
    check(
        "otp_challenges has the contract columns",
        {"id", "user_id", "code_hash", "destination_hash", "channel", "attempts", "expires_at", "consumed_at", "created_at", "client_ip_hash"} <= cols,
        cols,
    )
    from alembic import command

    command.downgrade(cfg, "0001")
    check("downgrade removes them", "otp_challenges" not in sa.inspect(engine).get_table_names() and "email" not in {c["name"] for c in sa.inspect(engine).get_columns("users")})
    command.upgrade(cfg, "head")
    check("upgrade again", "otp_challenges" in sa.inspect(engine).get_table_names())

    config.OTP_BACKEND = "local"
    config.AUTH_METHODS = ["pin", "otp"]
    config.OTP_RESEND_COOLDOWN_SECONDS = 60
    config.OTP_RATE_LIMIT_PER_NUMBER_PER_HOUR = 5
    config.OTP_RATE_LIMIT_PER_IP_PER_HOUR = 20
    clock = Clock()
    cap = Capture()
    svc = service.OtpService(sender=cap, clock=clock, send_in_background=False)
    service.set_service(svc)

    print("masking")
    check("mobile mask", masking.mask_mobile("+94771234567") == "07X XXX X567", masking.mask_mobile("+94771234567"))
    check("email mask", masking.mask_email("nimal@example.lk") == "n***@e***.lk", masking.mask_email("nimal@example.lk"))
    check("email mask with a long domain", masking.mask_email("a.b@mail.example.com") == "a***@m***.com")
    check("decoys are stable and well shaped", masking.decoy_mobile("ab" * 32) == masking.decoy_mobile("ab" * 32) and re.fullmatch(r"07X XXX X\d{3}", masking.decoy_mobile("cd" * 32)) and re.fullmatch(r"[a-z]\*\*\*@[a-z]\*\*\*\.[a-z]+", masking.decoy_email("ef" * 32)))

    print("request, hashing, verify")
    phone = new_user()
    out, logs = io.StringIO(), LogCapture()
    logging.disable(logging.NOTSET)
    logging.getLogger().addHandler(logs)
    logging.getLogger().setLevel(logging.DEBUG)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        info = svc.request_otp(phone, "10.0.0.1", "si")
    code = cap.sent[-1]["code"]
    check("info shape", set(vars(info)) == {"challenge_id", "masked_destination", "channel", "expires_in", "resend_in"} and info.channel == "sms" and info.expires_in == 300 and info.resend_in == 60, info)
    check("masked destination", info.masked_destination == "07X XXX X567", info.masked_destination)
    check("six digits from the sender, to the registered mobile, in the requested language", re.fullmatch(r"\d{6}", code) and cap.sent[-1]["to"] == "+94771234567" and cap.sent[-1]["language"] == "si")
    with session_scope() as session:
        row = session.execute(sa.select(challenges).where(challenges.c.id == info.challenge_id)).mappings().one()
    values = " ".join(str(v) for v in row.values())
    check("code hashed at rest (HMAC, not the code)", row["code_hash"] == hashing.hash_code(info.challenge_id, code) and code not in values and len(row["code_hash"]) == 64)
    check("no phone, mobile or address stored in the challenge", "771234567" not in values and phone.replace(" ", "") not in values and "10.0.0.1" not in values)
    check("challenge id is part of the hash", hashing.hash_code("a" * 32, code) != hashing.hash_code("b" * 32, code))
    with contextlib.redirect_stdout(out):
        user = svc.verify_otp(info.challenge_id, code, "10.0.0.1")
    check("correct code returns the VerifiedUser", user.display_name == "Test User" and user.phone_e164.startswith("+94112"))
    try:
        svc.verify_otp(info.challenge_id, code, "10.0.0.1")
        check("single use", False)
    except service.OtpError as e:
        check("single use (second try is invalid)", e.reason == "invalid", e.reason)
    captured = out.getvalue() + " ".join(logs.lines)
    check("no code, number or address in stdout, stderr or logging", code not in captured and "771234567" not in captured and "11200" not in captured.replace(" ", ""), captured[:200])
    logging.getLogger().removeHandler(logs)
    logging.disable(logging.CRITICAL)

    print("attempts, expiry, cooldown")
    phone = new_user()
    clock.advance(10)
    info = svc.request_otp(phone, "10.0.0.2")
    code = cap.sent[-1]["code"]
    reasons = []
    for _ in range(3):
        try:
            svc.verify_otp(info.challenge_id, wrong_for(code), "10.0.0.2")
        except service.OtpError as e:
            reasons.append(e.reason)
    check("three wrong codes: invalid, invalid, locked", reasons == ["invalid", "invalid", "locked"], reasons)
    try:
        svc.verify_otp(info.challenge_id, code, "10.0.0.2")
        check("a locked challenge refuses even the right code", False)
    except service.OtpError as e:
        check("a locked challenge refuses even the right code", e.reason == "locked", e.reason)

    clock.advance(61)
    info = svc.request_otp(phone, "10.0.0.2")
    code = cap.sent[-1]["code"]
    for _ in range(2):
        try:
            svc.verify_otp(info.challenge_id, wrong_for(code), "10.0.0.2")
        except service.OtpError:
            pass
    check("right code on the third attempt still works", svc.verify_otp(info.challenge_id, code, "10.0.0.2").display_name == "Test User")

    clock.advance(61)
    info = svc.request_otp(phone, "10.0.0.2")
    code = cap.sent[-1]["code"]
    clock.advance(299)
    check("valid at 299 s", svc.verify_otp(info.challenge_id, code, "10.0.0.2") is not None)
    clock.advance(61)
    info = svc.request_otp(phone, "10.0.0.2")
    code = cap.sent[-1]["code"]
    clock.advance(301)
    try:
        svc.verify_otp(info.challenge_id, code, "10.0.0.2")
        check("expired at 301 s", False)
    except service.OtpError as e:
        check("expired at 301 s", e.reason in ("expired", "invalid"), e.reason)

    phone = new_user()
    clock.advance(4000)
    first_info = svc.request_otp(phone, "10.0.0.2")
    first_code = cap.sent[-1]["code"]
    try:
        svc.request_otp(phone, "10.0.0.2")
        check("cooldown", False)
    except service.OtpError as e:
        check("immediate resend is refused with a Retry-After", e.reason == "cooldown" and 0 < e.retry_after <= 60, (e.reason, e.retry_after))
    sent_before = len(cap.sent)
    check("a refused resend sends nothing", len(cap.sent) == sent_before)
    clock.advance(61)
    svc.request_otp(phone, "10.0.0.2")
    try:
        svc.verify_otp(first_info.challenge_id, first_code, "10.0.0.2")
        check("resending retires the older code", False)
    except service.OtpError:
        check("resending retires the older code", True)

    print("limits")
    config.OTP_RESEND_COOLDOWN_SECONDS = 0
    phone = new_user()
    clock.advance(4000)
    outcome = []
    for _ in range(7):
        try:
            svc.request_otp(phone, f"10.9.0.{len(outcome)}")
            outcome.append("ok")
        except service.OtpError as e:
            outcome.append((e.reason, e.retry_after))
    check("per number: 5 per hour then rate_limited", outcome[:5] == ["ok"] * 5 and outcome[5][0] == "rate_limited" and 0 < outcome[5][1] <= 3600, outcome)
    clock.advance(3601)
    check("per number: allowed again after the hour", svc.request_otp(phone, "10.9.1.1") is not None)
    config.OTP_RATE_LIMIT_PER_IP_PER_HOUR = 3
    clock.advance(3601)
    phones = [new_user() for _ in range(5)]
    outcome = []
    for p in phones[:4]:
        try:
            svc.request_otp(p, "10.7.7.7")
            outcome.append("ok")
        except service.OtpError as e:
            outcome.append(e.reason)
    check("per IP: 3 per hour then rate_limited", outcome == ["ok", "ok", "ok", "rate_limited"], outcome)
    check("per IP: another address is unaffected", svc.request_otp(phones[4], "10.7.7.8") is not None)
    config.OTP_RATE_LIMIT_PER_IP_PER_HOUR = 20
    config.OTP_RESEND_COOLDOWN_SECONDS = 60

    print("console sender prints the code, nothing else does")
    clock.advance(4000)
    phone = new_user()
    console_out = io.StringIO()
    console_service = service.OtpService(sender=senders.ConsoleOtpSender(), clock=clock, send_in_background=False)
    with contextlib.redirect_stdout(console_out):
        info = console_service.request_otp(phone, "10.5.5.5")
    text = console_out.getvalue()
    printed = re.findall(r"one time code for [^:]+: (\d{6})", text)
    check("console sender prints exactly one marked code line", len(printed) == 1 and "DEV ONLY" in text, text)
    check("console output holds only a masked destination", "771234567" not in text)
    with session_scope() as session:
        stored = session.scalar(sa.select(challenges.c.code_hash).where(challenges.c.id == info.challenge_id))
    check("and that code matches the stored hash", hashing.verify_code(info.challenge_id, printed[0], stored))

    print("anti enumeration")
    clock.advance(4000)
    real_phone = new_user()
    no_contact = new_user(mobile=None)
    unknown = "011 299 9999"
    infos = {}
    cap.sent.clear()
    for label, p in (("real", real_phone), ("no_contact", no_contact), ("unknown", unknown)):
        infos[label] = svc.request_otp(p, "10.6.6.6")
    check("only the real customer was sent a message", len(cap.sent) == 1)
    shapes = {k: (tuple(sorted(vars(v))), v.channel, v.expires_in, v.resend_in, len(v.challenge_id), re.sub(r"\d", "9", v.masked_destination)) for k, v in infos.items()}
    check("same response shape for real, contactless and unknown", len(set(shapes.values())) == 1, shapes)
    clock.advance(61)
    again = svc.request_otp(unknown, "10.6.6.6")
    check("a decoy destination is stable for the same number", again.masked_destination == infos["unknown"].masked_destination)
    for label in ("unknown", "no_contact"):
        reasons = []
        for _ in range(4):
            try:
                svc.verify_otp(infos[label].challenge_id if label == "no_contact" else again.challenge_id, "123456", "10.6.6.6")
            except service.OtpError as e:
                reasons.append(e.reason)
        check(f"{label}: wrong codes behave like a real challenge (invalid, invalid, locked, locked)", reasons == ["invalid", "invalid", "locked", "locked"], reasons)
    try:
        svc.request_otp(unknown, "10.6.6.6")
        check("decoy cooldown like a real one", False)
    except service.OtpError as e:
        check("decoy cooldown like a real one", e.reason == "cooldown")
    check("unknown challenge id answers invalid", _reason(lambda: svc.verify_otp("0" * 32, "123456", "10.6.6.6")) == "invalid")
    config.OTP_RESEND_COOLDOWN_SECONDS = 0
    config.OTP_RATE_LIMIT_PER_NUMBER_PER_HOUR = 1000
    config.OTP_RATE_LIMIT_PER_IP_PER_HOUR = 1000
    real_times, decoy_times = [], []
    for i in range(12):
        t = time.perf_counter()
        svc.request_otp(real_phone, "10.8.8.8")
        real_times.append(time.perf_counter() - t)
        t = time.perf_counter()
        svc.request_otp(f"011 288 {i:04d}", "10.8.8.8")
        decoy_times.append(time.perf_counter() - t)
    real_mean, decoy_mean = sum(real_times) / 12, sum(decoy_times) / 12
    check("similar timing (means within 30 ms)", abs(real_mean - decoy_mean) < 0.03, f"{real_mean * 1000:.1f} vs {decoy_mean * 1000:.1f} ms")
    config.OTP_RESEND_COOLDOWN_SECONDS = 60
    config.OTP_RATE_LIMIT_PER_NUMBER_PER_HOUR = 5
    config.OTP_RATE_LIMIT_PER_IP_PER_HOUR = 20

    print("senders")
    config.SMTP_HOST = ""
    check("email sender without SMTP settings is unusable, not a fallback", not senders.EmailOtpSender().is_usable())
    check("sms sender without a gateway is unusable", not senders.SmsGatewayOtpSender().is_usable())
    config.OTP_SENDER = "bogus"
    check("unknown OTP_SENDER is unusable (never the console)", not senders.get_sender().is_usable())
    config.OTP_SENDER = "console"
    check("console is chosen only by name", isinstance(senders.get_sender(), senders.ConsoleOtpSender))
    for lang in ("en", "si", "ta"):
        check(f"message in {lang} carries the code", "482913" in senders.compose_message("482913", lang))

    class FakeSMTP:
        calls = []

        def __init__(self, host, port, timeout=None):
            FakeSMTP.calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            FakeSMTP.calls.append(("starttls",))

        def login(self, u, p):
            FakeSMTP.calls.append(("login", u))

        def send_message(self, m):
            FakeSMTP.calls.append(("send", m["To"], m["Subject"], m.get_content()))

    real_smtp = senders.smtplib.SMTP
    senders.smtplib.SMTP = FakeSMTP
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            senders.EmailOtpSender("smtp.example.lk", 587, "user", "pw", "noreply@example.lk").send("a@b.lk", "246810", "en")
    finally:
        senders.smtplib.SMTP = real_smtp
    names = [c[0] for c in FakeSMTP.calls]
    check("SMTP: STARTTLS before login, then send", names == ["connect", "starttls", "login", "send"], names)
    check("SMTP: message carries the code; nothing printed", "246810" in FakeSMTP.calls[-1][3] and out.getvalue() == "")

    import http.server
    import json
    import threading

    seen = {}

    class Gateway(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen["body"], seen["auth"] = body, self.headers.get("Authorization")
            if body["message"].find("500000") >= 0:
                self.send_response(500)
            elif body["message"].find("999999") >= 0:
                time.sleep(3)
                self.send_response(200)
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/sms"
    sms = senders.SmsGatewayOtpSender(url, "gw-secret-key", "Authorization", "SLTMobitel", allow_private=True, timeout=(1.0, 1.0))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        sms.send("+94771234567", "135790", "en")
    check("SMS: JSON body {to, message, sender_id}", set(seen["body"]) == {"to", "message", "sender_id"} and seen["body"]["to"] == "+94771234567" and "135790" in seen["body"]["message"] and seen["body"]["sender_id"] == "SLTMobitel", seen)
    check("SMS: key in the configured header (Bearer added)", seen["auth"] == "Bearer gw-secret-key")
    check("SMS: nothing printed", out.getvalue() == "")
    for bad_code, label in (("500000", "HTTP 500"), ("999999", "timeout")):
        try:
            started = time.monotonic()
            sms.send("+94771234567", bad_code, "en")
            check(f"SMS {label} raises SenderDeliveryError", False)
        except senders.SenderDeliveryError as e:
            check(f"SMS {label} raises SenderDeliveryError without secrets", "gw-secret-key" not in str(e) and bad_code not in str(e) and time.monotonic() - started < 2.5, str(e))
    national = senders.SmsGatewayOtpSender(url, "k", "X-Key", "S", to_format="national", allow_private=True)
    check("SMS: to_format national", national.build_payload("+94771234567", "1", "en")["to"] == "0771234567")
    server.shutdown()

    print("routes")
    config.OTP_SENDER = "console"
    config.OTP_RESEND_COOLDOWN_SECONDS = 60
    clock.advance(4000)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(otp_router)
    client = TestClient(app, client=("10.4.4.4", 50000))
    for configured, expected in ((["pin", "otp"], ["pin", "otp"]), (["otp", "bogus", "pin", "otp"], ["otp", "pin"]), ([], ["pin"]), (["pin"], ["pin"])):
        config.AUTH_METHODS = configured
        check(f"GET /auth/methods {configured}", client.get("/auth/methods").json()["methods"] == expected)
    config.AUTH_METHODS = ["pin"]
    check("request refused (404) when otp is not listed", client.post("/auth/otp/request", json={"phone": "011 234 5678"}).status_code == 404)
    config.AUTH_METHODS = ["pin", "otp"]
    service.set_service(service.OtpService(sender=cap, clock=clock, send_in_background=False))
    phone = new_user(name="Route User")
    r = client.post("/auth/otp/request", json={"phone": phone})
    check("POST /auth/otp/request 200 contract shape", r.status_code == 200 and set(r.json()) == {"challenge_id", "masked_destination", "channel", "expires_in", "resend_in"}, r.text)
    cid, code = r.json()["challenge_id"], cap.sent[-1]["code"]
    r2 = client.post("/auth/otp/request", json={"phone": phone})
    check("cooldown is 429 with Retry-After and reason", r2.status_code == 429 and int(r2.headers["retry-after"]) > 0 and r2.json()["reason"] == "cooldown", r2.text)
    check("mobile number as landline is 422 phone_format", client.post("/auth/otp/request", json={"phone": "0771234567"}).json().get("reason") == "phone_format")
    check("missing fields are 422", client.post("/auth/otp/request", json={}).status_code == 422 and client.post("/auth/otp/verify", json={"challenge_id": "x"}).status_code == 422)
    bad = client.post("/auth/otp/verify", json={"challenge_id": cid, "code": wrong_for(code)})
    check("wrong code: 401 invalid", bad.status_code == 401 and bad.json()["reason"] == "invalid", bad.text)
    ok = client.post("/auth/otp/verify", json={"challenge_id": cid, "code": code})
    cookie = ok.headers.get("set-cookie", "")
    check("right code: 200 with display_name, token, expires_at", ok.status_code == 200 and set(ok.json()) == {"display_name", "token", "expires_at"} and ok.json()["display_name"] == "Route User", ok.text)
    check("same cookie as /auth/verify (httpOnly, SameSite=Lax)", "user_session=" in cookie and "HttpOnly" in cookie and "samesite=lax" in cookie.lower(), cookie)
    check("token resolves to the user", verified_user_from_token(ok.json()["token"]).display_name == "Route User")
    check("/auth/me works with the cookie", client.get("/auth/me").json() == {"display_name": "Route User"})
    clock.advance(61)
    r = client.post("/auth/otp/request", json={"phone": phone})
    cid, code = r.json()["challenge_id"], cap.sent[-1]["code"]
    statuses = [client.post("/auth/otp/verify", json={"challenge_id": cid, "code": wrong_for(code)}).status_code for _ in range(3)]
    check("three wrong codes over HTTP: 401, 401, 423 with Retry-After", statuses == [401, 401, 423], statuses)
    pin = client.post("/auth/verify", json={"phone": phone, "pin": "1234"})
    check("phone plus PIN sign in is untouched", pin.status_code == 200 and pin.json()["display_name"] == "Route User", pin.text)
    config.OTP_SENDER = "sms"
    config.SMS_GATEWAY_URL = ""
    config.SMS_GATEWAY_API_KEY = ""
    service.set_service(service.OtpService(clock=clock, send_in_background=False))
    clock.advance(61)
    check("sender not usable: methods still lists otp, otp_available false", "otp" in client.get("/auth/methods").json()["methods"] and client.get("/auth/methods").json()["otp_available"] is False)
    r = client.post("/auth/otp/request", json={"phone": phone})
    check("sender not usable: request is 503 unavailable", r.status_code == 503 and r.json()["reason"] == "unavailable", r.text)
    config.OTP_SENDER = "console"

    print("hub backend (mock hub only)")
    from mcp_client import client as hub_client
    from mcp_client.mock_hub import DEFAULT_KEY, MockHub
    from otp.hub_backend import HubOtpBackend

    hub = MockHub("sse")
    hub.start()
    check("the test hub is local, never the real one", hub.url.startswith("http://127.0.0.1"))
    config.MCP_HUB_ENABLED, config.MCP_HUB_API_KEY, config.MCP_HUB_URL = True, DEFAULT_KEY, hub.url
    hub_client.set_hub(hub_client.HubClient(hub.url, "X-API-Key", DEFAULT_KEY, 4.0, 0, 300))
    config.OTP_BACKEND = "hub"
    config.OTP_RESEND_COOLDOWN_SECONDS = 60
    hub_svc = service.OtpService(sender=Capture(), clock=clock, send_in_background=False, hub_otp=HubOtpBackend())
    service.set_service(hub_svc)
    clock.advance(4000)
    info = hub_svc.request_otp("011 234 5678", "10.3.3.3")
    check("hub knows the customer: masked destination comes from the hub", info.masked_destination == "07X XXX X567" and info.channel == "sms", info)
    check("the hub sent the SMS, we sent none", len(hub.sms_outbox) == 1 and not hub_svc.sender.sent)
    check("hub tool got telephone_no in national form and otp_media 2", hub.tool_calls[0] == ("validate_customer_and_send_otp", {"telephone_no": "0112345678", "otp_media": 2}), hub.tool_calls)
    with session_scope() as session:
        row = session.execute(sa.select(challenges).where(challenges.c.id == info.challenge_id)).mappings().one()
    stored = " ".join(str(v) for v in row.values())
    check("hub session id is sealed at rest, no phone stored", hub.sms_outbox[0]["session_id"] not in stored and "0112345678" not in stored and row["hub_ref"])
    try:
        hub_svc.request_otp("011 234 5678", "10.3.3.3")
        check("cooldown is enforced in front of the hub", False)
    except service.OtpError as e:
        check("cooldown is enforced in front of the hub", e.reason == "cooldown" and len(hub.tool_calls) == 1)
    wrong = hub_svc.verify_otp
    check("wrong code: invalid", _reason(lambda: wrong(info.challenge_id, wrong_for(hub.otp_code), "10.3.3.3")) == "invalid")
    user = hub_svc.verify_otp(info.challenge_id, hub.otp_code, "10.3.3.3")
    check("right code: a local account is provisioned and returned", user.display_name == "Nimal Perera" and user.phone_e164 == "+94112345678", user)
    check("single use", _reason(lambda: hub_svc.verify_otp(info.challenge_id, hub.otp_code, "10.3.3.3")) == "invalid")
    try:
        auth_users.verify_user("011 234 5678", "1234", "10.3.3.3")
        check("the provisioned account has no usable PIN", False)
    except AuthError:
        check("the provisioned account has no usable PIN", True)
    clock.advance(61)
    unknown_info = hub_svc.request_otp("011 211 1111", "10.3.3.3")
    check("unknown customer: same shape, nothing sent", unknown_info.channel == "sms" and re.sub(r"\d", "9", unknown_info.masked_destination) == re.sub(r"\d", "9", info.masked_destination) and len(hub.sms_outbox) == 1, unknown_info)
    reasons = [_reason(lambda: hub_svc.verify_otp(unknown_info.challenge_id, "123456", "10.3.3.3")) for _ in range(3)]
    check("unknown customer verify: invalid, invalid, locked", reasons == ["invalid", "invalid", "locked"], reasons)
    clock.advance(61)
    hub.otp_masked_in_response = False
    info = hub_svc.request_otp("011 234 5678", "10.3.3.4")
    decoy = hub_svc.request_otp("011 233 3333", "10.3.3.4")
    check("no mask from the hub: generic text for everyone", info.masked_destination == decoy.masked_destination == "your registered mobile number", (info.masked_destination, decoy.masked_destination))
    hub.otp_masked_in_response = True
    clock.advance(61)
    hub.otp_session_key = "weird_key"
    check("unrecognised response shape is a typed failure, 503 (never a guess)", _reason(lambda: hub_svc.request_otp("011 234 5678", "10.3.3.5")) == "unavailable")
    hub.otp_session_key = "sessionId"
    info = hub_svc.request_otp("011 234 5678", "10.3.3.5")
    check("sessionId spelling accepted", info.masked_destination == "07X XXX X567")
    check("right code through the sessionId spelling", hub_svc.verify_otp(info.challenge_id, hub.otp_code, "10.3.3.5").display_name == "Nimal Perera")
    hub.stop()
    hub_client.set_hub(hub_client.HubClient(hub.url, "X-API-Key", DEFAULT_KEY, 1.0, 0, 300))
    clock.advance(61)
    check("OTP_BACKEND=hub and the hub is down: 503", _reason(lambda: hub_svc.request_otp("011 234 5678", "10.3.3.6")) == "unavailable")
    hub_client.set_hub(hub_client.HubClient(hub.url, "X-API-Key", DEFAULT_KEY, 1.0, 0, 300))
    config.OTP_BACKEND = "auto"
    config.OTP_SENDER = "console"
    local_cap = Capture()
    auto_svc = service.OtpService(sender=local_cap, clock=clock, send_in_background=False, hub_otp=HubOtpBackend())
    phone = new_user()
    clock.advance(61)
    info = auto_svc.request_otp(phone, "10.3.3.7")
    check("auto: hub down falls back to the local backend", len(local_cap.sent) == 1 and info.masked_destination == "07X XXX X567")
    check("auto: and its code works", auto_svc.verify_otp(info.challenge_id, local_cap.sent[-1]["code"], "10.3.3.7") is not None)
    config.OTP_BACKEND = "local"
    local_only = service.OtpService(sender=Capture(), clock=clock, send_in_background=False, hub_otp=HubOtpBackend())
    check("local: hub backend never chosen", local_only._choose_backend(local_only.sender) == "local")
    service.set_service(None)

    failed = [name for name, ok in _results if not ok]
    print(f"{len(_results) - len(failed)}/{len(_results)} passed")
    if failed:
        print("FAILED:", *failed, sep=chr(10) + "  ")
    return 1 if failed else 0


def _reason(fn):
    try:
        fn()
    except service.OtpError as exc:
        return exc.reason
    return None


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    engine.dispose()
    os._exit(code)  # daemon threads (mock hub, hub loop) must not hold the exit
