"""Token mode sign in (HUB_AUTH_MODE=token) against the local mock hub.

The hub's REST endpoints /api/otp/send and /api/otp/verify issue tokens, which are seeded into the customer's
MCP connection with authenticate_with_token. The mock hub (customer_tools mode, a login belongs to one
connection) has those routes and that tool behind switches. No real hub, no real SMS: only 127.0.0.1.

The shapes of the real hub's success answers are NOT known; the mock's shapes are a guess and the parsing is
tested against several guesses.
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi import FastAPI

from conftest import make_client, require_module

KEY = "token-auth-test-key-SECRET-5c3a1e"
KNOWN_RAW = "011 234 5678"
KNOWN_E164 = "+94112345678"
KNOWN = "0112345678"
LTE = "0712345670"
LTE_E164 = "+94712345670"
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


class Clock:
    def __init__(self):
        self.now = datetime.now(timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Ticker:
    """A number that a test moves by hand (the fallback window or the unix time)."""

    def __init__(self, start):
        self.value = float(start)

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


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
def hub(mock_hub, monkeypatch):
    import config
    from mcp_gateway import access_matrix, audit, gateway, pool as pool_module, rest_auth

    mock_hub.sms_outbox.clear()
    mock_hub.tool_calls.clear()
    mock_hub.rest_calls.clear()
    mock_hub.issued_tokens.clear()
    mock_hub.fail_next = 0
    mock_hub.otp_code = "123456"
    mock_hub.authenticated.clear()
    mock_hub.connection_auth.clear()
    mock_hub.customer_tool_errors.clear()
    mock_hub.customer_payloads.clear()
    mock_hub.customer_names.clear()
    mock_hub.otp_customers = {
        KNOWN: {"name": HUB_NAME, "masked": "07X XXX X567"},
        LTE: {"name": "Kamal", "masked": "07X XXX X670"},
    }
    mock_hub.rest_otp = True
    mock_hub.rest_status = None
    mock_hub.rest_verify_status = None
    mock_hub.rest_not_json = False
    mock_hub.rest_token_shape = "flat"
    mock_hub.rest_expiry_style = "unix"
    mock_hub.rest_token_ttl = 3600.0
    mock_hub.seed_fail = False
    mock_hub.seed_text_error = False
    monkeypatch.setattr(config, "VERIFY_OTP_BACKEND", "hub")
    monkeypatch.setattr(config, "HUB_AUTH_MODE", "token")
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    monkeypatch.setattr(config, "EXPOSE_LATENCY_TOOL", False)
    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_PHONE_PER_HOUR", 6)
    monkeypatch.setattr(config, "VERIFY_RATE_LIMIT_PER_IP_PER_HOUR", 20)
    monkeypatch.setattr(rest_auth, "_clock", Ticker(1000.0))
    monkeypatch.setattr(rest_auth, "_wall", Ticker(__import__("time").time()))
    rest_auth.reset_fallback()
    client = require_module("mcp_client.client")
    client.set_hub(client.HubClient(mock_hub.url, "X-API-Key", KEY, 3.0, 0, 300))
    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    gateway._pending.clear()
    gateway._tokens.clear()
    gateway._service_types.clear()
    audit.clear()
    access_matrix.reload()
    yield mock_hub
    pool.close_all()
    for number in list(gateway._pending):
        gateway._close_pending(number)
    gateway._tokens.clear()
    pool_module.set_pool(None)
    client.set_hub(None)
    rest_auth.reset_fallback()


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
    yield holder
    for c in clients:
        c.close()
    verification.set_service(None)


def lookup(client, name=HUB_NAME, phone=KNOWN_RAW):
    return client.post("/verify/lookup", json={"name": name, "phone": phone, "language": "en"})


def send(client, ticket, method="sms"):
    return client.post("/verify/otp/send", json={"ticket": ticket, "method": method, "language": "en"})


def verify(client, challenge_id, code):
    return client.post("/verify/otp/verify", json={"challenge_id": challenge_id, "code": code})


def start(api, phone=KNOWN_RAW, name=HUB_NAME, client=None):
    client = client or api.client
    found = lookup(client, name=name, phone=phone)
    assert found.status_code == 200, found.text
    sent = send(client, found.json()["ticket"])
    assert sent.status_code == 200, sent.text
    return sent.json()


def access(plus=KNOWN_E164):
    from access import AccessContext

    return AccessContext.verified(1, "Nimal", plus)


def login(hub, number=KNOWN, media=1):
    """The gateway's own sign in, as the wizard drives it."""
    from mcp_gateway import gateway

    begun = gateway.begin_login(number, media)
    assert begun.ok, begun.reason
    done = gateway.finish_login(number, begun.hub_session_id, hub.otp_code)
    assert done.ok, done.reason
    return begun, done


def names(hub):
    return [n for n, _ in hub.tool_calls]


def count(hub, name):
    return names(hub).count(name)


# The wizard in token mode


def test_full_wizard_flow_ends_signed_in_through_the_rest_endpoints(api, hub):
    from auth.tokens import AUDIENCE_USER, decode_token
    from mcp_gateway import gateway

    sent = start(api)
    assert re.fullmatch(r"X{6}\d{4}", sent["masked"])  # the mobile mask of the REST send answer
    assert hub.sms_outbox[-1]["to"] == KNOWN
    done = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert done.status_code == 200, done.text
    assert done.json() == {"display_name": HUB_NAME, "language": "en"}
    claims = decode_token(api.client.cookies.get("user_session"), AUDIENCE_USER)
    assert "otp" in claims["amr"] and "hub" in claims["amr"]
    assert api.client.get("/verify/session").json() == {
        "verified": True, "display_name": HUB_NAME, "hub_session": True, "expired": False,
    }
    assert [c[0] for c in hub.rest_calls] == ["send", "verify"]
    assert "validate_customer_and_send_otp" not in names(hub) and "verify_otp_and_authenticate" not in names(hub)
    assert count(hub, "authenticate_with_token") == 1
    assert gateway.has_session(access()) and KNOWN in gateway._tokens
    assert gateway.call("show_gb_balance", {}, access()).ok


def test_the_email_mask_comes_from_the_same_rest_answer(api, hub):
    found = lookup(api.client)
    sent = api.client.post("/verify/otp/send", json={"ticket": found.json()["ticket"], "method": "email", "language": "en"})
    assert sent.status_code == 200
    assert sent.json()["masked"].endswith("@gmail.com") and "XXXX" in sent.json()["masked"]


def test_display_name_rules_are_unchanged(api, hub, monkeypatch):
    import config
    from mcp_gateway import profile

    monkeypatch.setattr(config, "HUB_NAME_LOOKUP_SECONDS", 6)
    profile.reset()
    hub.otp_customers = {KNOWN: {"name": None, "masked": "x"}}
    hub.customer_names[KNOWN] = "Kamala Silva"
    sent = start(api, name="Typed Name")
    done = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert done.json()["display_name"] == "Kamala Silva"  # the hub's name, read on the seeded connection
    assert any(c[0] == "show_service_detail" for c in hub.tool_calls)

    api.service.clock.advance(31)
    hub.customer_names.clear()
    profile.reset()
    other = api.new_client()
    hub.otp_customers = {"0112000001": {"name": None, "masked": "x"}, "0112000002": {"name": None, "masked": "x"}}
    sent = start(api, phone="011 200 0001", name="  Kamal   Silva  ", client=other)
    assert verify(other, sent["challenge_id"], hub.otp_code).json()["display_name"] == "Kamal Silva"

    api.service.clock.advance(31)
    third = api.new_client()
    sent = start(api, phone="011 200 0002", name="", client=third)
    assert verify(third, sent["challenge_id"], hub.otp_code).json()["display_name"] == "Customer"


def test_logout_drops_the_tokens_and_the_connection(api, hub):
    from mcp_gateway import gateway

    sent = start(api)
    verify(api.client, sent["challenge_id"], hub.otp_code)
    assert KNOWN in gateway._tokens
    assert api.client.post("/verify/logout").json() == {"ok": True}
    assert gateway._tokens == {}
    assert not gateway.has_session(access())  # nothing to seed from


# Every customer tool on the seeded connection


READ_ARGS = {"package_name": "Extra 10GB", "bill_date": "2026-09-01"}
TOOL_ARGS = {
    "search_knowledgebase_tool": {"query": "roaming"},
    "compare_package": {"current_package_name": "Fibre Unlimited 60", "current_price": 4900},
}


@pytest.mark.parametrize("number,plus,kind", [(KNOWN, KNOWN_E164, "postpaid"), (LTE, LTE_E164, "lte_prepaid")])
def test_every_read_tool_of_the_matrix_works_on_the_seeded_connection(hub, number, plus, kind):
    from mcp_gateway import access_matrix, gateway

    login(hub, number)
    who = access(plus)
    assert gateway.service_type(who) == kind
    matrix = access_matrix.load()
    wanted = [n for n in matrix.tool_names_for("authenticated", kind) if not matrix.is_state_changing(n)]
    assert len(wanted) >= 6
    for tool in wanted:
        result = gateway.call(tool, dict(TOOL_ARGS.get(tool, READ_ARGS)), who)
        assert result.ok, (tool, result.kind, result.reason)
    seen = {n for n, _ in hub.tool_calls}
    assert set(wanted) <= seen
    assert count(hub, "authenticate_with_token") == 1  # one seed, everything else on that connection
    forbidden = {"validate_customer_and_send_otp", "verify_otp_and_authenticate"}
    assert not forbidden & seen


def test_state_changing_tools_run_only_through_the_confirmation_and_only_once(hub, monkeypatch):
    import config
    from confirmations import api as confirm_api, store
    from mcp_gateway import gateway

    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    store.clear()
    login(hub, KNOWN)
    who = access()
    # Nothing runs without a confirmed action.
    assert gateway.call("purchase_extra_gb", {"package_id": 11}, who).reason == "state_changing"
    assert gateway.call("log_fault", {"service_id": "S1"}, who).reason == "state_changing"
    pending = confirm_api.propose(who, "purchase_extra_gb", {"package_id": 11}, [("Package", "Extra 10GB")], "en", None)
    assert pending is not None
    assert "purchase_extra_gb" not in names(hub) and "log_fault" not in names(hub)
    done = confirm_api.confirm(who, pending.token)
    assert done.kind == "executed", done.text
    assert count(hub, "purchase_extra_gb") == 1
    again = confirm_api.confirm(who, pending.token)
    assert again.kind == "invalid" and count(hub, "purchase_extra_gb") == 1

    fault = confirm_api.propose(who, "log_fault", {"service_id": "S1"}, [("Fault", "No internet")], "en", None)
    assert confirm_api.confirm(who, fault.token).kind == "executed"
    assert count(hub, "log_fault") == 1
    assert count(hub, "authenticate_with_token") == 1
    store.clear()


# Seeding again


def test_a_closed_connection_is_seeded_again_without_a_new_code(hub):
    from mcp_gateway import gateway, pool as pool_module

    login(hub)
    who = access()
    pool_module.get_pool().get(KNOWN).close()  # the connection is gone
    result = gateway.call("show_gb_balance", {}, who)
    assert result.ok, (result.kind, result.reason)
    assert count(hub, "authenticate_with_token") == 2
    assert len(hub.sms_outbox) == 1 and [c[0] for c in hub.rest_calls] == ["send", "verify"]  # no new code
    pool_module.get_pool().get(KNOWN).close()
    assert gateway.has_session(who)  # the session check seeds too
    assert count(hub, "authenticate_with_token") == 3


def test_a_read_is_retried_once_when_the_hub_lost_the_login(hub):
    from mcp_gateway import gateway

    login(hub)
    who = access()
    hub.connection_auth.clear()  # the hub forgot the login of the open connection
    result = gateway.call("show_fault_list", {}, who)
    assert result.ok, (result.kind, result.reason)
    assert count(hub, "show_fault_list") == 2 and count(hub, "authenticate_with_token") == 2
    assert gateway.call("show_fault_list", {}, who).ok and count(hub, "authenticate_with_token") == 2
    # a tool that needs the service type: the lookup is where the login loss is found and mended
    hub.connection_auth.clear()
    assert gateway.call("show_gb_balance", {}, who).ok
    assert count(hub, "authenticate_with_token") == 3


def test_a_read_that_fails_again_after_the_seed_is_not_retried_a_second_time(hub):
    from mcp_gateway import gateway

    login(hub)
    hub.connection_auth.clear()
    hub.seed_fail = False
    real_seed = hub._seed
    hub._seed = lambda ctx, *a, **k: json.dumps({"success": True, "message": "ok"})  # says yes, authenticates nothing
    try:
        result = gateway.call("show_fault_list", {}, access())
    finally:
        hub._seed = real_seed
    assert result.kind == "reauth"
    assert count(hub, "show_fault_list") == 2  # the call, then the one retry, no third
    assert KNOWN not in gateway._tokens  # tokens the hub does not honour are dropped


def test_a_state_changing_call_is_never_retried(hub, monkeypatch):
    import config
    from mcp_gateway import gateway

    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    login(hub)
    who = access()
    gateway.service_type(who)  # known already, so the purchase itself meets the lost login
    hub.connection_auth.clear()
    result = gateway.call("purchase_extra_gb", {"package_id": 11}, who, confirmed=True)
    assert result.kind == "reauth"
    assert count(hub, "purchase_extra_gb") == 1  # asked once, not repeated
    assert count(hub, "authenticate_with_token") == 2  # the connection was seeded for the next call
    assert gateway.has_session(who) and count(hub, "authenticate_with_token") == 2
    assert gateway.call("show_gb_balance", {}, who).ok
    assert count(hub, "purchase_extra_gb") == 1


def test_a_state_changing_call_is_not_run_on_a_connection_built_just_now(hub, monkeypatch):
    import config
    from mcp_gateway import gateway, pool as pool_module

    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    login(hub)
    pool_module.get_pool().get(KNOWN).close()
    result = gateway.call("log_fault", {"service_id": "S1"}, access(), confirmed=True)
    assert result.kind == "reauth" and result.reason == "reseeded"
    assert "log_fault" not in names(hub)
    assert gateway.call("show_gb_balance", {}, access()).ok  # ready for the next call


def test_expired_tokens_are_not_used_the_customer_signs_in_again(hub):
    from mcp_gateway import gateway, pool as pool_module, rest_auth

    login(hub)
    who = access()
    pool_module.get_pool().get(KNOWN).close()
    rest_auth._wall.advance(4000)  # past the one hour of the tokens
    seeds = count(hub, "authenticate_with_token")
    result = gateway.call("show_gb_balance", {}, who)
    assert result.kind == "reauth" and result.reason == "no_session"
    assert not gateway.has_session(who)
    assert count(hub, "authenticate_with_token") == seeds  # the hub was not asked with dead tokens
    assert gateway._tokens == {}


def test_a_hub_that_refuses_the_tokens_means_a_new_sign_in(hub):
    from mcp_gateway import gateway, pool as pool_module

    login(hub)
    pool_module.get_pool().get(KNOWN).close()
    hub.seed_fail = True
    result = gateway.call("show_gb_balance", {}, access())
    assert result.kind == "reauth"
    assert gateway._tokens == {}


def test_a_hub_that_cannot_be_reached_keeps_the_tokens(hub):
    from mcp_gateway import gateway, pool as pool_module

    login(hub)
    pool_module.get_pool().get(KNOWN).close()
    hub.fail_next = 50  # every request answers 503
    try:
        result = gateway.call("show_gb_balance", {}, access())
    finally:
        hub.fail_next = 0
    assert result.kind == "unavailable"
    assert KNOWN in gateway._tokens
    require_module("mcp_client.client").get_hub().reset_breaker()  # the client's own breaker, not ours
    assert gateway.call("show_gb_balance", {}, access()).ok  # back again, seeded then


# Idle sweeper and eviction


def test_an_idle_connection_is_swept_and_seeded_again_on_demand(hub):
    from mcp_gateway import gateway, pool as pool_module

    tick = Ticker(100.0)
    pool = pool_module.SessionPool(sweep=False, clock=tick, idle_seconds=60)
    pool_module.set_pool(pool)
    login(hub)
    tick.advance(120)
    assert pool.sweep() == 1 and pool.size() == 0
    assert KNOWN in gateway._tokens  # the tokens outlive the connection
    assert gateway.call("show_gb_balance", {}, access()).ok
    assert pool.size() == 1 and count(hub, "authenticate_with_token") == 2
    pool.close_all()


def test_the_sweep_drops_expired_tokens(hub):
    from mcp_gateway import gateway, pool as pool_module, rest_auth

    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    login(hub)
    assert KNOWN in gateway._tokens
    rest_auth._wall.advance(4000)
    pool.sweep()
    assert gateway._tokens == {}
    pool.close_all()


def test_the_least_recently_used_connection_is_evicted_and_comes_back_on_demand(hub):
    from mcp_gateway import gateway, pool as pool_module

    pool = pool_module.SessionPool(sweep=False, max_sessions=1)
    pool_module.set_pool(pool)
    login(hub, KNOWN)
    login(hub, LTE)
    assert pool.keys() == [LTE] and KNOWN in gateway._tokens and LTE in gateway._tokens
    assert gateway.call("show_gb_balance", {}, access()).ok  # evicts the LTE connection, seeds this one
    assert pool.keys() == [KNOWN]
    assert gateway.call("show_lte_wallet_detail", {}, access(LTE_E164)).ok
    assert pool.keys() == [LTE]
    pool.close_all()


# Fallback to the connection mode


def test_rest_404_falls_back_to_the_connection_mode_for_30_minutes_and_recovers(api, hub):
    from mcp_gateway import rest_auth

    hub.rest_status = 404
    sent = start(api)
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200
    assert "validate_customer_and_send_otp" in names(hub) and "verify_otp_and_authenticate" in names(hub)
    assert "authenticate_with_token" not in names(hub)
    assert rest_auth.fallback_active()
    rest_calls = len(hub.rest_calls)

    hub.rest_status = None  # the endpoint is back, but the window has not passed
    api.service.clock.advance(31)
    other = api.new_client()
    sent = start(api, client=other)
    assert verify(other, sent["challenge_id"], hub.otp_code).status_code == 200
    assert len(hub.rest_calls) == rest_calls  # not even asked
    assert count(hub, "validate_customer_and_send_otp") == 2

    rest_auth._clock.advance(1801)
    assert not rest_auth.fallback_active()
    api.service.clock.advance(31)
    third = api.new_client()
    sent = start(api, client=third)
    assert verify(third, sent["challenge_id"], hub.otp_code).status_code == 200
    assert [c[0] for c in hub.rest_calls[rest_calls:]] == ["send", "verify"]
    assert count(hub, "authenticate_with_token") == 1


@pytest.mark.parametrize("fault", ["server_error", "not_json"])
def test_other_transport_failures_of_send_also_fall_back(hub, fault):
    from mcp_gateway import gateway, rest_auth

    if fault == "server_error":
        hub.rest_status = 500
    else:
        hub.rest_not_json = True
    login(hub)  # the sign in still works, by the connection mode
    assert rest_auth.fallback_active()
    assert "authenticate_with_token" not in names(hub) and gateway.has_session(access())
    assert gateway._tokens == {}


def test_tokens_that_cannot_be_read_are_a_503_and_switch_to_the_connection_mode(api, hub):
    from mcp_gateway import gateway, rest_auth

    hub.rest_token_shape = "missing"
    sent = start(api)
    response = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert response.status_code == 503 and response.json() == {"code": "unavailable"}
    assert rest_auth.fallback_active() and not gateway.has_session(access())
    assert "authenticate_with_token" not in names(hub)
    # the next sign in works through the connection mode
    api.service.clock.advance(31)
    again = start(api, client=api.new_client())
    other = api.new_client()
    other.cookies.clear()
    assert again["challenge_id"]
    done = verify(api.client, again["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    assert "verify_otp_and_authenticate" in names(hub)


def test_a_missing_field_a_seed_refusal_and_dead_tokens_are_all_unavailable(hub):
    from mcp_gateway import gateway, rest_auth

    cases = [
        ("no_account", {}),
        ("flat", {"seed_fail": True}),
        ("flat", {"seed_text_error": True}),
        ("flat", {"rest_token_ttl": -10.0}),
    ]
    for shape, switches in cases:
        rest_auth.reset_fallback()
        hub.rest_token_shape = shape
        hub.seed_fail = hub.seed_text_error = False
        hub.rest_token_ttl = 3600.0
        for key, value in switches.items():
            setattr(hub, key, value)
        begun = gateway.begin_login(KNOWN, 1)
        assert begun.ok
        done = gateway.finish_login(KNOWN, begun.hub_session_id, hub.otp_code)
        assert (done.ok, done.reason) == (False, "unavailable"), (shape, switches)
        assert rest_auth.fallback_active() and not gateway.has_session(access()) and gateway._tokens == {}


def test_a_verify_that_fails_in_transport_is_unavailable_and_falls_back(hub):
    from mcp_gateway import gateway, rest_auth

    hub.rest_verify_status = 502
    begun = gateway.begin_login(KNOWN, 1)
    assert begun.ok
    done = gateway.finish_login(KNOWN, begun.hub_session_id, hub.otp_code)
    assert (done.ok, done.reason) == (False, "unavailable")
    assert rest_auth.fallback_active()


# Wrong and expired codes


def test_wrong_code_is_invalid_with_attempts_left_and_nothing_is_seeded(api, hub):
    sent = start(api)
    first = verify(api.client, sent["challenge_id"], "000000")
    assert first.status_code == 401 and first.json() == {"code": "invalid", "attempts_left": 2}
    assert "authenticate_with_token" not in names(hub)
    ok = verify(api.client, sent["challenge_id"], hub.otp_code)  # the right code still works afterwards
    assert ok.status_code == 200
    from mcp_gateway import rest_auth

    assert not rest_auth.fallback_active()  # a wrong code is not a hub fault


def test_expired_code_is_410_and_three_wrong_codes_lock(api, hub):
    sent = start(api)
    api.service.clock.advance(121)
    late = verify(api.client, sent["challenge_id"], hub.otp_code)
    assert late.status_code == 410 and late.json() == {"code": "expired"}
    api.service.clock.advance(31)
    again = start(api, client=api.new_client())
    codes = [verify(api.client, again["challenge_id"], "000000").status_code for _ in range(3)]
    assert codes == [401, 401, 429] or codes[-1] in (401, 423, 429), codes


def test_a_hub_that_says_the_code_expired_is_expired_not_invalid(hub):
    from mcp_gateway import gateway, rest_auth

    begun = gateway.begin_login(KNOWN, 1)
    assert rest_auth.classify_failure("OTP expired") == "expired"
    assert rest_auth.classify_failure("invalid or expired code") == "invalid_code"
    assert rest_auth.classify_failure("Too many attempts") == "locked"
    bad = gateway.finish_login(KNOWN, begun.hub_session_id, "000000")
    assert (bad.ok, bad.reason) == (False, "invalid_code")
    assert gateway.finish_login(KNOWN, begun.hub_session_id, hub.otp_code).ok  # still pending after a wrong code


def test_an_unknown_number_is_rejected_not_a_transport_failure(hub):
    from mcp_gateway import gateway, rest_auth

    begun = gateway.begin_login("0112000009", 1)
    assert (begun.ok, begun.reason) == (False, "rejected")
    assert not rest_auth.fallback_active()
    assert hub.sms_outbox == []


# Nothing secret leaves memory


def test_tokens_never_reach_logs_audit_rows_responses_or_the_cookie(api, hub, capsys, caplog, db_schema):
    from mcp_gateway import audit, gateway, pool as pool_module
    from verification import models

    caplog.set_level("INFO")
    found = lookup(api.client)
    sent = send(api.client, found.json()["ticket"])
    done = verify(api.client, sent.json()["challenge_id"], hub.otp_code)
    assert done.status_code == 200
    pool_module.get_pool().get(KNOWN).close()
    assert gateway.call("show_gb_balance", {}, access()).ok  # a reseed as well
    secrets = [hub.issued_tokens[KNOWN][k] for k in ("access_token", "refresh_token")]
    assert all(secrets)
    captured = capsys.readouterr()
    logs = captured.out + captured.err + caplog.text
    assert "GATEWAY tool=authenticate_with_token" in logs  # the audit line exists, with no values
    responses = " ".join(r.text for r in (found, sent, done, api.client.get("/verify/session")))
    cookie = " ".join(str(v) for v in api.client.cookies.values())
    audit_rows = json.dumps(audit.recent())
    with db_schema.engine.begin() as conn:
        dump = " ".join(
            str(v)
            for table in (models.challenges, models.VerificationAuditRow.__table__)
            for row in conn.execute(sa.select(table)).all()
            for v in row
        )
    session_id = hub.sms_outbox[-1]["session_id"]
    for blob in (logs, responses, cookie, audit_rows, dump):
        for secret in secrets + [session_id, hub.otp_code, KNOWN, "112345678"]:
            assert secret not in blob, secret
    assert "acc-" not in repr(gateway._tokens) and "ref-" not in repr(gateway._tokens)


# Reading the hub's answers (the real shapes are unknown)


@pytest.mark.parametrize("shape", ["flat", "camel", "pascal", "data", "result", "tokens", "token"])
@pytest.mark.parametrize("style", ["unix", "ms", "iso", "in"])
def test_every_guessed_shape_and_expiry_style_signs_in(hub, shape, style):
    from mcp_gateway import gateway, rest_auth

    hub.rest_token_shape = shape
    hub.rest_expiry_style = style
    login(hub)
    assert not rest_auth.fallback_active()
    assert count(hub, "authenticate_with_token") == 1 and gateway.call("show_gb_balance", {}, access()).ok
    expires = gateway._tokens[KNOWN].expires_at
    assert abs(expires - (rest_auth._wall() + 3600)) < 30


def test_parse_tokens_on_hand_made_answers(monkeypatch):
    from mcp_gateway import rest_auth

    now = 1_800_000_000.0
    monkeypatch.setattr(rest_auth, "_wall", lambda: now)
    flat = {"access_token": "a", "refresh_token": "r", "subscriber_id": 7, "account_number": "9", "expires_at": now + 60}
    parsed = rest_auth.parse_tokens({"success": True, **flat})
    assert parsed and parsed.subscriber_id == "7" and parsed.expires_at == now + 60
    deep = rest_auth.parse_tokens({"data": {"tokens": {"accessToken": "a", "refreshToken": "r", "expiresIn": 30},
                                           "subscriberId": "7", "accountNumber": "9"}})
    assert deep and deep.expires_at == now + 30
    assert rest_auth.parse_tokens({"success": True, "token": {"access": "a", "refresh": "r", "expiry": "2030-01-15T08:00:00Z"},
                                   "subscriber_id": "1", "account_number": "2"}).expires_at > now
    assert rest_auth.parse_tokens({**flat, "expires_at": (now + 60) * 1000}).expires_at == now + 60
    assert rest_auth.parse_tokens({**flat, "expires_at": str(now + 60)}).expires_at == now + 60
    # a JWT access token carries its own expiry
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"exp": now + 99}).encode()).decode().rstrip("=")
    no_expiry = {k: v for k, v in flat.items() if k != "expires_at"}
    jwt = rest_auth.parse_tokens({**no_expiry, "access_token": f"h.{payload}.s"})
    assert jwt and jwt.expires_at == now + 99
    for bad in (None, [], "text", 5, {}, {"success": True}, {**flat, "refresh_token": ""}, {**no_expiry},
                {**flat, "account_number": {"x": 1}}, {**flat, "expires_at": "tomorrow"}):
        assert rest_auth.parse_tokens(bad) is None, bad
    assert repr(parsed) == "Tokens(...)"


# The REST calls themselves


def test_send_and_verify_against_the_mock(hub):
    from mcp_gateway import rest_auth

    sent = rest_auth.send(KNOWN, 1)
    assert sent.ok and sent.session_id and sent.masked_mobile.startswith("XXXXXX") and "@" in sent.masked_email
    wrong = rest_auth.verify(KNOWN, sent.session_id, "000000")
    assert (wrong.ok, wrong.reason, wrong.transport) == (False, "invalid_code", False)
    good = rest_auth.verify(KNOWN, sent.session_id, hub.otp_code)
    assert good.ok and good.tokens and not good.tokens.expired()
    used = rest_auth.verify(KNOWN, sent.session_id, hub.otp_code)  # single use
    assert used.ok is False


def test_failures_are_told_apart(hub, monkeypatch):
    from mcp_client import client as hub_client
    from mcp_gateway import rest_auth

    assert rest_auth.send("0112000009", 1).reason == "rejected"
    hub.rest_status = 404
    assert rest_auth.send(KNOWN, 1).transport is True
    hub.rest_status = 503
    assert rest_auth.verify(KNOWN, "s", "123456").transport is True
    hub.rest_status = None
    hub.rest_not_json = True
    assert rest_auth.send(KNOWN, 1).transport is True
    hub.rest_not_json = False
    hub_client.set_hub(hub_client.HubClient(hub.url, "X-API-Key", "the-wrong-key", 3.0, 0, 300))
    assert rest_auth.send(KNOWN, 1).transport is True  # 401
    hub_client.set_hub(hub_client.HubClient("http://127.0.0.1:9", "X-API-Key", KEY, 1.0, 0, 300))
    assert rest_auth.send(KNOWN, 1).transport is True  # nothing listens
    hub_client.set_hub(None)
    assert rest_auth.send(KNOWN, 1).transport is True  # no hub configured


def test_the_hub_gets_the_api_key_and_the_body_asked_for(hub):
    from mcp_gateway import rest_auth

    hub.reset_counters()
    rest_auth.send(KNOWN, 2)
    assert hub.request_log[-1] == ("POST", "/api/otp/send") and hub.request_key_ok[-1] is True
    assert hub.rest_calls[-1] == ("send", KNOWN)


def test_the_mock_rest_routes_check_the_key_and_the_required_fields(hub):
    import requests

    url = hub.url + "/api/otp/send"
    assert requests.post(url, json={"telephone_no": KNOWN}, timeout=5).status_code == 401
    headers = {"X-API-Key": KEY}
    missing = requests.post(url, json={}, headers=headers, timeout=5)
    assert missing.status_code == 400 and missing.json() == {"success": False, "error": "telephone_no is required"}
    missing = requests.post(hub.url + "/api/otp/verify", json={"telephone_no": KNOWN}, headers=headers, timeout=5)
    assert missing.status_code == 400 and "required" in missing.json()["error"]
    hub.rest_otp = False
    assert requests.post(url, json={"telephone_no": KNOWN}, headers=headers, timeout=5).status_code == 404


def test_the_mock_seed_tool_rejects_wrong_and_expired_tokens(hub):
    from mcp_gateway import gateway, rest_auth

    sent = rest_auth.send(KNOWN, 1)
    tokens = rest_auth.verify(KNOWN, sent.session_id, hub.otp_code).tokens
    good, verdict = gateway._seed(KNOWN, tokens)
    assert verdict == "ok" and good is not None
    good.close()
    bad = rest_auth.Tokens("wrong", tokens.refresh_token, tokens.subscriber_id, tokens.account_number, tokens.expires_at)
    assert gateway._seed(KNOWN, bad) == (None, "rejected")
    old = rest_auth.Tokens(tokens.access_token, tokens.refresh_token, tokens.subscriber_id, tokens.account_number, 1.0)
    assert gateway._seed(KNOWN, old) == (None, "rejected")
    assert gateway._seed("0112000001", tokens) == (None, "rejected")  # tokens of another number


def test_connection_mode_is_untouched(api, hub, monkeypatch):
    import config

    monkeypatch.setattr(config, "HUB_AUTH_MODE", "connection")
    sent = start(api)
    assert verify(api.client, sent["challenge_id"], hub.otp_code).status_code == 200
    assert hub.rest_calls == [] and "authenticate_with_token" not in names(hub)
    assert "validate_customer_and_send_otp" in names(hub)
    from mcp_gateway import gateway

    assert gateway._tokens == {}
