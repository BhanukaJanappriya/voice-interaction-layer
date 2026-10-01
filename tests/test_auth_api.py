"""/auth endpoints: user verification (phone + PIN), sessions, lockout,
rate limiting, admin login, and admin endpoints refusing anonymous calls."""

import pytest

import config
from conftest import (
    ADMIN_PASSWORD,
    ADMIN_USERNAME,
    USER_NAME,
    USER_PHONE_E164,
    USER_PHONE_RAW,
    USER_PIN,
    require_path,
)


@pytest.fixture(autouse=True)
def _require_auth(api_app):
    require_path(api_app, "/auth/verify")


def _set_cookie_headers(response):
    return [value for key, value in response.headers.multi_items() if key.lower() == "set-cookie"]


def _cookie_header_for(response, name):
    for header in _set_cookie_headers(response):
        if header.split("=", 1)[0].strip() == name:
            return header
    return None


# User verification


def test_verify_success_sets_http_only_cookie_and_returns_token(client, make_user):
    make_user()
    response = client.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display_name"] == USER_NAME
    assert isinstance(body["token"], str) and body["token"].count(".") == 2
    assert "expires_at" in body
    cookie = _cookie_header_for(response, "user_session")
    assert cookie is not None, _set_cookie_headers(response)
    lowered = cookie.lower()
    assert "httponly" in lowered
    assert "samesite=lax" in lowered
    assert "pin" not in body and "pin_hash" not in body


def test_verify_token_is_a_user_audience_token(client, make_user):
    from auth.tokens import AUDIENCE_ADMIN, AUDIENCE_USER, TokenError, decode_token

    make_user()
    token = client.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN}).json()["token"]
    decode_token(token, AUDIENCE_USER)
    with pytest.raises(TokenError):
        decode_token(token, AUDIENCE_ADMIN)


@pytest.mark.parametrize("phone", [USER_PHONE_RAW, "0112345678", "+94 11 234 5678", USER_PHONE_E164])
def test_verify_accepts_any_phone_format(client, make_user, phone):
    make_user()
    response = client.post("/auth/verify", json={"phone": phone, "pin": USER_PIN})
    assert response.status_code == 200, response.text


def test_wrong_pin_and_unknown_number_look_identical(new_client, make_user):
    make_user()
    wrong_pin = new_client().post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": "9999"})
    unknown = new_client().post("/auth/verify", json={"phone": "011 999 8888", "pin": USER_PIN})
    assert wrong_pin.status_code == 401
    assert unknown.status_code == 401
    assert wrong_pin.json() == unknown.json()
    assert _cookie_header_for(wrong_pin, "user_session") is None


def test_invalid_phone_format_does_not_log_in(client, make_user):
    make_user()
    response = client.post("/auth/verify", json={"phone": "077 123 4567", "pin": USER_PIN})
    assert response.status_code in (400, 401, 422)


def test_lockout_after_max_attempts(new_client, make_user):
    make_user()
    attempts = config.LOGIN_MAX_ATTEMPTS
    c = new_client()
    statuses = [
        c.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": "0000"}).status_code for _ in range(attempts)
    ]
    assert all(status in (401, 423, 429) for status in statuses), statuses
    # Even the right PIN is refused now, from any address.
    locked = new_client().post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN})
    assert locked.status_code in (423, 429), locked.text
    assert "retry-after" in {key.lower() for key in locked.headers.keys()}
    assert int(locked.headers["retry-after"]) > 0


def test_lockout_is_recorded_and_success_resets_counter(new_client, make_user, db_schema):
    from db.models import User

    make_user()
    c = new_client()
    for _ in range(2):
        assert c.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": "0000"}).status_code == 401
    with db_schema.session_scope() as session:
        assert session.query(User).filter_by(phone_e164=USER_PHONE_E164).one().failed_attempts == 2
    assert c.post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN}).status_code == 200
    with db_schema.session_scope() as session:
        user = session.query(User).filter_by(phone_e164=USER_PHONE_E164).one()
        assert user.failed_attempts == 0
        assert user.last_login_at is not None


def test_per_address_rate_limit(new_client, make_user):
    make_user()
    c = new_client()
    limit = config.LOGIN_RATE_LIMIT_PER_MINUTE
    responses = []
    for i in range(limit + 2):
        # Different unknown numbers, so no single account locks.
        responses.append(c.post("/auth/verify", json={"phone": f"011 300 {1000 + i}", "pin": "1111"}))
    limited = [r for r in responses if r.status_code == 429]
    assert limited, [r.status_code for r in responses]
    assert "retry-after" in {key.lower() for key in limited[0].headers.keys()}
    # Another address is not affected.
    other = new_client().post("/auth/verify", json={"phone": USER_PHONE_RAW, "pin": USER_PIN})
    assert other.status_code == 200, other.text


# Sessions


def test_me_via_cookie(client, login_user):
    login_user(client)
    response = client.get("/auth/me")
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == USER_NAME


def test_me_via_bearer(client, new_client, login_user):
    token = login_user(client)["token"]
    fresh = new_client()
    response = fresh.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    assert response.json()["display_name"] == USER_NAME


def test_me_without_session_is_401(client):
    assert client.get("/auth/me").status_code == 401


def test_me_rejects_admin_and_garbage_tokens(client):
    from auth.tokens import AUDIENCE_ADMIN, issue_token

    admin_token, _ = issue_token("1", AUDIENCE_ADMIN, 5)
    for token in (admin_token, "garbage"):
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_me_rejects_expired_token(client, make_user):
    from auth.tokens import AUDIENCE_USER, issue_token

    user_id = make_user()
    token, _ = issue_token(str(user_id), AUDIENCE_USER, -1)
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_clears_cookie(client, login_user):
    login_user(client)
    assert client.get("/auth/me").status_code == 200
    response = client.post("/auth/logout")
    assert response.status_code in (200, 204)
    assert client.get("/auth/me").status_code == 401


# Admin sessions


def test_admin_login_me_logout(new_client, make_admin):
    make_admin()
    c = new_client()
    response = c.post("/auth/admin/login", json={"username": ADMIN_USERNAME, "password": ADMIN_PASSWORD})
    assert response.status_code == 200, response.text
    cookie = _cookie_header_for(response, "admin_session")
    assert cookie is not None
    assert "httponly" in cookie.lower()
    assert "samesite=strict" in cookie.lower()
    me = c.get("/auth/admin/me")
    assert me.status_code == 200
    assert me.json()["username"] == ADMIN_USERNAME
    assert "password_hash" not in me.text
    assert c.post("/auth/admin/logout").status_code in (200, 204)
    assert c.get("/auth/admin/me").status_code == 401


def test_admin_wrong_password_and_unknown_admin_identical(new_client, make_admin):
    make_admin()
    wrong = new_client().post("/auth/admin/login", json={"username": ADMIN_USERNAME, "password": "nope"})
    unknown = new_client().post("/auth/admin/login", json={"username": "ghost", "password": ADMIN_PASSWORD})
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_user_session_is_not_an_admin_session(client, login_user):
    token = login_user(client)["token"]
    assert client.get("/auth/admin/me").status_code == 401
    assert client.get("/auth/admin/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_user_cookie_value_replayed_as_admin_cookie_refused(new_client, login_user, client):
    token = login_user(client)["token"]
    c = new_client()
    c.cookies.set("admin_session", token)
    assert c.get("/auth/admin/me").status_code == 401


ADMIN_ENDPOINTS = [
    ("GET", "/admin/api/agents"),
    ("POST", "/admin/api/agents"),
    ("PUT", "/admin/api/agents/1"),
    ("DELETE", "/admin/api/agents/1"),
    ("POST", "/admin/api/agents/1/test"),
    ("POST", "/admin/api/agents/1/activate"),
    ("POST", "/admin/api/agents/deactivate"),
    ("GET", "/admin/api/mcp-clients"),
    ("POST", "/admin/api/mcp-clients"),
    ("DELETE", "/admin/api/mcp-clients/1"),
    ("GET", "/admin/api/audit"),
]


@pytest.mark.parametrize("method, path", ADMIN_ENDPOINTS)
def test_admin_endpoints_401_without_session(client, method, path):
    require_path(client.app, "/admin/api/agents")
    response = client.request(method, path, json={})
    assert response.status_code == 401, (method, path, response.status_code, response.text)


@pytest.mark.parametrize("method, path", ADMIN_ENDPOINTS[:1] + ADMIN_ENDPOINTS[7:8])
def test_admin_endpoints_refuse_user_session(client, login_user, method, path):
    require_path(client.app, "/admin/api/agents")
    token = login_user(client)["token"]
    response = client.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code in (401, 403)


def test_seed_first_admin_from_env():
    admins = pytest.importorskip("auth.admins", reason="auth.admins not delivered yet")
    from db.base import session_scope
    from db.models import Admin

    admins.seed_first_admin_from_env()
    with session_scope() as session:
        rows = session.query(Admin).all()
        assert [row.username for row in rows] == [config.ADMIN_USERNAME]
        assert rows[0].password_hash != config.ADMIN_PASSWORD
    # Only when no admin exists yet.
    admins.seed_first_admin_from_env()
    with session_scope() as session:
        assert session.query(Admin).count() == 1
