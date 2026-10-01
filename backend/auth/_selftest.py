"""Self test for the database and auth layer, against a throwaway SQLite
database in a temporary folder (the real data/app.db is never touched).

    python backend/auth/_selftest.py        (from the repository root)

Covers: migrations, phone normalization, user and admin sign in through
the real routes on a throwaway FastAPI app (cookies, Bearer, lockout,
rate limit, admin session and require_admin), token audience separation,
and user resolution on a WebSocket handshake. The project's real test
suite lives in tests/; this is the workstream's own quick check.
"""

import os
import sys
import tempfile
import time

# Everything below must be set before config is imported anywhere.
_TMP = tempfile.mkdtemp(prefix="auth_selftest_")
os.environ["DATA_DIR"] = _TMP
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/selftest.db".replace("\\", "/")
os.environ["JWT_SECRET"] = "selftest-secret-selftest-secret-selftest-secret"
os.environ["FIELD_ENCRYPTION_KEY"] = "unused-here"
os.environ["LOGIN_MAX_ATTEMPTS"] = "5"
os.environ["LOGIN_LOCKOUT_MINUTES"] = "15"
os.environ["LOGIN_RATE_LIMIT_PER_MINUTE"] = "8"
os.environ["ADMIN_USERNAME"] = "root-admin"
os.environ["ADMIN_PASSWORD"] = "correct horse battery"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import Depends, FastAPI, WebSocket  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from auth import admins, users  # noqa: E402
from auth.dependencies import require_admin, verified_user_from_request, verified_user_from_token  # noqa: E402
from auth.phone import InvalidPhoneNumber, normalize_sl_landline  # noqa: E402
from auth.routes import router  # noqa: E402
from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_MCP, AUDIENCE_USER, issue_token  # noqa: E402
from db.base import session_scope  # noqa: E402
from db.migrate import database_status, upgrade_to_head  # noqa: E402
from db.models import User  # noqa: E402

_results = []


def check(name, condition, info=""):
    _results.append((name, bool(condition)))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{('  ' + str(info)) if info and not condition else ''}")


def build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/admin/api/ping")
    def ping(admin: dict = Depends(require_admin)):
        return {"admin": admin}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        message = await websocket.receive_json()
        user = verified_user_from_request(websocket, message.get("session_token"))
        await websocket.send_json({"name": user.display_name if user else None})
        await websocket.close()

    return app


def main() -> int:
    print(f"Temporary database: {os.environ['DATABASE_URL']}")

    print("\nMigrations")
    upgrade_to_head()
    upgrade_to_head()  # second run must be a no op
    status = database_status()
    check("upgrade_to_head twice, schema at head", status["ok"] and status["revision"] == status["head"], status)

    print("\nPhone numbers")
    check("0XX XXXXXXX", normalize_sl_landline("011 2345678") == "+94112345678")
    check("+94 with (0)", normalize_sl_landline("+94 (0)81 222 3344") == "+94812223344")
    check("0094 and dashes", normalize_sl_landline("0094-91-222-3344") == "+94912223344")
    for bad in ["0771234567", "0123456789", "011234567", "+4420794600", "abc"]:
        try:
            normalize_sl_landline(bad)
            check(f"refuses {bad}", False)
        except InvalidPhoneNumber:
            check(f"refuses {bad}", True)

    print("\nAccounts")
    nimal = users.create_user("011 234 5678", "Nimal Perera", "1234", "en")
    users.create_user("081 222 3344", "Kumari Jayasinghe", "2468", "si")
    try:
        users.create_user("+94112345678", "Dup", "1111")
        check("duplicate number refused", False)
    except users.UserAlreadyExists:
        check("duplicate number refused", True)
    for bad_pin in ["123", "1234567", "12a4", "١٢٣٤"]:
        try:
            users.create_user("091 222 3344", "X", bad_pin)
            check(f"bad PIN {bad_pin!a} refused", False)
        except ValueError:
            check(f"bad PIN {bad_pin!a} refused", True)
    with session_scope() as session:
        stored = session.get(User, nimal.id)
        check("PIN stored as bcrypt hash", stored.pin_hash.startswith("$2") and "1234" not in stored.pin_hash)
    check("seed_first_admin_from_env creates the first admin", admins.seed_first_admin_from_env() is True)
    check("seed_first_admin_from_env is a no op afterwards", admins.seed_first_admin_from_env() is False)
    try:
        admins.create_admin("short", "123456789")
        check("admin password under 10 refused", False)
    except ValueError:
        check("admin password under 10 refused", True)

    client = TestClient(build_app())

    print("\nUser verification routes")
    r = client.post("/auth/verify", json={"phone": "011 234 5678", "pin": "1234"})
    body = r.json()
    cookie_header = r.headers.get("set-cookie", "")
    check("verify ok 200", r.status_code == 200 and body.get("display_name") == "Nimal Perera", r.text)
    check("token and expires_at returned", body.get("token") and body.get("expires_at", 0) > time.time())
    check(
        "cookie httpOnly, SameSite=Lax, path=/",
        "user_session=" in cookie_header and "HttpOnly" in cookie_header
        and "samesite=lax" in cookie_header.lower() and "Path=/" in cookie_header,
        cookie_header,
    )
    check("last_login_at set", users.list_users()[0]["last_login_at"] is not None)
    token = body["token"]

    r = client.get("/auth/me")
    check("/auth/me with cookie", r.status_code == 200 and r.json() == {"display_name": "Nimal Perera"}, r.text)
    bare = TestClient(build_app())
    check("/auth/me without session is 401", bare.get("/auth/me").status_code == 401)
    r = bare.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    check("/auth/me with Bearer", r.status_code == 200 and r.json()["display_name"] == "Nimal Perera", r.text)
    r = bare.get("/auth/me", headers={"Authorization": "Bearer not-a-token"})
    check("/auth/me with garbage Bearer is 401", r.status_code == 401)

    r = client.post("/auth/logout")
    check("logout clears cookie", r.status_code == 200 and client.get("/auth/me").status_code == 401)

    users.login_rate_limiter.reset()
    r = client.post("/auth/verify", json={"phone": "011 234 5678", "pin": "9999"})
    check("wrong PIN is 401 generic", r.status_code == 401 and r.json()["reason"] == "invalid")
    r2 = client.post("/auth/verify", json={"phone": "011 999 9999", "pin": "9999"})
    check("unknown number gives the same answer", r2.status_code == 401 and r2.json() == r.json(), r2.text)
    r = client.post("/auth/verify", json={"phone": "077 123 4567", "pin": "1234"})
    check("mobile number is 422 with a clear message", r.status_code == 422 and "mobile" in r.json()["detail"], r.text)

    # Timing: unknown number vs wrong PIN should both pay for one bcrypt.
    users.login_rate_limiter.reset()
    started = time.perf_counter()
    try:
        users.verify_user("021 999 9999", "0000", "timing")
    except users.AuthError:
        pass
    unknown_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    try:
        users.verify_user("081 222 3344", "0000", "timing")
    except users.AuthError:
        pass
    wrong_ms = (time.perf_counter() - started) * 1000
    check(
        "unknown number takes about as long as a wrong PIN",
        unknown_ms > 0.5 * wrong_ms,
        f"unknown {unknown_ms:.0f} ms, wrong PIN {wrong_ms:.0f} ms",
    )
    print(f"        (unknown number {unknown_ms:.0f} ms, wrong PIN {wrong_ms:.0f} ms)")
    users.unlock_user("081 222 3344")

    print("\nLockout")
    users.login_rate_limiter.reset()
    users.unlock_user("011 234 5678")
    codes = [client.post("/auth/verify", json={"phone": "0112345678", "pin": "0000"}).status_code for _ in range(5)]
    check("4 x 401 then 423 on the 5th failure", codes == [401, 401, 401, 401, 423], codes)
    r = client.post("/auth/verify", json={"phone": "0112345678", "pin": "1234"})
    check(
        "correct PIN still refused while locked, with Retry-After",
        r.status_code == 423 and int(r.headers.get("retry-after", 0)) > 800,
        (r.status_code, r.headers.get("retry-after")),
    )
    users.unlock_user("011 234 5678")
    r = client.post("/auth/verify", json={"phone": "0112345678", "pin": "1234"})
    check("after unlock the correct PIN works", r.status_code == 200, r.text)
    check("success reset the failure count", users.list_users()[0]["failed_attempts"] == 0)

    print("\nParallel burst against one account")
    from concurrent.futures import ThreadPoolExecutor

    users.unlock_user("011 234 5678")

    def attempt(i):
        pin = "1234" if i == 11 else "0000"  # the right PIN arrives mid burst
        try:
            users.verify_user("0112345678", pin, f"burst-{i}")
            return "ok"
        except users.AuthError as error:
            return error.reason

    with ThreadPoolExecutor(12) as pool:
        outcomes = list(pool.map(attempt, range(12)))
    check(
        "at most LOGIN_MAX_ATTEMPTS-1 wrong PINs answered 'invalid', the rest 'locked'",
        outcomes.count("invalid") <= 4 and outcomes.count("locked") >= 7,
        outcomes,
    )
    print(f"        ({outcomes.count('invalid')} invalid, {outcomes.count('locked')} locked, {outcomes.count('ok')} ok)")
    users.unlock_user("011 234 5678")

    print("\nRate limit per address")
    users.login_rate_limiter.reset()
    codes = [client.post("/auth/verify", json={"phone": f"011 200 000{i}", "pin": "0000"}).status_code for i in range(9)]
    check("8 attempts allowed, 9th is 429", codes[:8] == [401] * 8 and codes[8] == 429, codes)
    r = client.post("/auth/verify", json={"phone": "011 234 5678", "pin": "1234"})
    check("429 even with the right PIN, Retry-After set", r.status_code == 429 and r.headers.get("retry-after"), r.text)
    check("another address is not limited", users.login_rate_limiter.hit("10.0.0.2") is None)
    users.login_rate_limiter.reset()

    print("\nTokens")
    check("verified_user_from_token(valid)", verified_user_from_token(token) == nimal)
    admin_token, _ = issue_token("1", AUDIENCE_ADMIN, 5)
    mcp_token, _ = issue_token(str(nimal.id), AUDIENCE_MCP, 5)
    check("admin token is not a user token", verified_user_from_token(admin_token) is None)
    check("MCP token is not a user token", verified_user_from_token(mcp_token) is None)
    expired, _ = issue_token(str(nimal.id), AUDIENCE_USER, -1)
    check("expired token refused", verified_user_from_token(expired) is None)
    ghost, _ = issue_token("99999", AUDIENCE_USER, 5)
    check("token for a deleted/unknown user refused", verified_user_from_token(ghost) is None)

    print("\nWebSocket handshake")
    ws_client = TestClient(build_app())
    with ws_client.websocket_connect("/ws") as ws:
        ws.send_json({})
        check("no session on WebSocket is guest", ws.receive_json() == {"name": None})
    with ws_client.websocket_connect("/ws") as ws:
        ws.send_json({"session_token": token})
        check("explicit session_token on WebSocket", ws.receive_json() == {"name": "Nimal Perera"})
    with ws_client.websocket_connect("/ws", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_json({"session_token": "garbage"})
        check("bad explicit token falls through to Bearer", ws.receive_json() == {"name": "Nimal Perera"})
    ws_client.post("/auth/verify", json={"phone": "081 222 3344", "pin": "2468"})
    with ws_client.websocket_connect("/ws") as ws:
        ws.send_json({})
        check("user_session cookie on WebSocket handshake", ws.receive_json() == {"name": "Kumari Jayasinghe"})

    print("\nAdmin")
    admin_client = TestClient(build_app())
    check("admin API without session is 401", admin_client.get("/admin/api/ping").status_code == 401)
    check("/auth/admin/me without session is 401", admin_client.get("/auth/admin/me").status_code == 401)
    r = admin_client.post("/auth/admin/login", json={"username": "root-admin", "password": "wrong password!"})
    check("admin wrong password is 401", r.status_code == 401)
    r = admin_client.post("/auth/admin/login", json={"username": "nobody", "password": "wrong password!"})
    check("admin unknown username is 401 too", r.status_code == 401)
    r = admin_client.post("/auth/admin/login", json={"username": "root-admin", "password": "correct horse battery"})
    set_cookie = r.headers.get("set-cookie", "")
    check("admin login 200", r.status_code == 200 and r.json()["username"] == "root-admin", r.text)
    check(
        "admin cookie httpOnly, SameSite=Strict, no token in body",
        "admin_session=" in set_cookie and "HttpOnly" in set_cookie
        and "samesite=strict" in set_cookie.lower() and "token" not in r.json(),
        set_cookie,
    )
    r = admin_client.get("/admin/api/ping")
    check("require_admin passes with cookie", r.status_code == 200 and r.json()["admin"]["username"] == "root-admin", r.text)
    check("/auth/admin/me", admin_client.get("/auth/admin/me").json() == {"username": "root-admin"})
    user_as_admin = TestClient(build_app(), cookies={"admin_session": token})
    check("user token in admin cookie is 401", user_as_admin.get("/admin/api/ping").status_code == 401)
    admin_client.post("/auth/admin/logout")
    check("admin logout", admin_client.get("/admin/api/ping").status_code == 401)
    admins.admin_rate_limiter.reset()
    codes = [
        admin_client.post("/auth/admin/login", json={"username": "root-admin", "password": "nope nope nope"}).status_code
        for _ in range(5)
    ]
    check("admin lockout after 5 failures", codes[-1] == 423, codes)

    failed = [name for name, ok in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    code = main()
    from db.base import engine

    engine.dispose()
    sys.exit(code)
