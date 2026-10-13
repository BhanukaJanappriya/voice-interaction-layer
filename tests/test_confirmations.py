"""The confirmation step against the mock hub (customer_tools=True).

Proved here: nothing that changes an account runs without a confirm; with the
feature flag off nothing can even be proposed; a confirm runs the hub tool
exactly once and is never retried, also after a transport failure; the hub's
own failure text is passed on; the summary comes from the hub's data and a
missing price is not invented; spoken yes, no and ambiguous answers in English,
Sinhala and Tamil; fault slot filling with a read back; the HTTP routes; and
that no token or number reaches stdout.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed")

import config  # noqa: E402
from access import GUEST, AccessContext  # noqa: E402
from confirmations import api, fault, intents, routes, store, texts  # noqa: E402
from mcp_client import client as hub_client  # noqa: E402
from mcp_client.mock_hub import FIBRE_NUMBER, LTE_NUMBER, MockHub  # noqa: E402
from mcp_gateway import access_matrix, audit, gateway, pool as pool_module  # noqa: E402
from mcp_gateway.session import SessionDead  # noqa: E402

KEY = "confirm-test-key-0123456789"
FIBRE_PLUS = "+94112345678"
LTE_PLUS = "+94712345670"


def verified(plus=FIBRE_PLUS, user_id=1):
    return AccessContext.verified(user_id, "Nimal", plus)


@pytest.fixture(scope="module")
def mock():
    server = MockHub(mode="sse", api_key=KEY, customer_tools=True)
    server.start()
    yield server
    server.stop()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def hub(mock, monkeypatch):
    mock.authenticated.clear()
    mock.connection_auth.clear()
    mock.customer_tool_errors.clear()
    mock.customer_payloads.clear()
    mock.otp_customers[FIBRE_NUMBER] = {"name": "Nimal Perera", "masked": "x"}
    mock.otp_customers[LTE_NUMBER] = {"name": "Kamal", "masked": "y"}
    mock.reset_counters()
    mock.sms_outbox.clear()
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    monkeypatch.setattr(config, "EXPOSE_LATENCY_TOOL", False)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000)
    monkeypatch.setattr(config, "CONFIRMATION_TTL_SECONDS", 60)
    hub_client.set_hub(hub_client.HubClient(mock.url, "X-API-Key", KEY, 3.0, 0, 30.0))
    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    gateway._pending.clear()
    gateway._service_types.clear()
    audit.clear()
    access_matrix.reload()
    clock = Clock()
    store.set_clock(clock)
    store.clear()
    fault.clear()
    routes.clear_limits()
    mock.clock = clock
    yield mock
    store.set_clock(None)
    store.clear()
    fault.clear()
    pool.close_all()
    for number in list(gateway._pending):
        gateway._close_pending(number)
    pool_module.set_pool(None)
    hub_client.set_hub(None)


def login(mock, number=FIBRE_NUMBER):
    start = gateway.begin_login(number, 2)
    assert start.ok, start.reason
    done = gateway.finish_login(number, start.hub_session_id, mock.otp_code)
    assert done.ok, done.reason
    gateway.service_type(verified("+94" + number[1:]))


def names(mock):
    return [name for name, _ in mock.tool_calls]


def calls(mock, name):
    return [args for n, args in mock.tool_calls if n == name]


@pytest.fixture
def customer(hub):
    login(hub)
    return verified()


# Proposing and confirming


def test_nothing_executes_until_confirmed(hub, customer):
    result = api.start(customer, "I want to buy extra 10GB", "en")
    assert result is not None and result.pending is not None
    assert "Extra 10GB" in result.text and "490.0" in result.text and "678" in result.text
    assert "purchase_extra_gb" not in names(hub)
    assert api.pending_for(customer)["tool"] == "purchase_extra_gb"


def test_confirm_runs_exactly_once(hub, customer):
    token = api.start(customer, "buy extra 10GB please", "en").pending.token
    first = api.confirm(customer, token)
    assert first.kind == "executed" and "Extra GB purchase submitted" in first.text
    second = api.confirm(customer, token)
    assert second.kind == "invalid"
    assert len(calls(hub, "purchase_extra_gb")) == 1
    assert calls(hub, "purchase_extra_gb")[0]["package_id"] == 11
    assert calls(hub, "purchase_extra_gb")[0]["telephone_no"] == FIBRE_NUMBER
    assert api.pending_for(customer) is None


def test_the_browser_view_holds_no_arguments(hub, customer):
    api.start(customer, "buy extra 10GB", "en")
    view = api.pending_for(customer)
    assert set(view) == {"id", "tool", "title", "lines", "expires_in", "state"}
    blob = json.dumps(view)
    assert "package_id" not in blob and FIBRE_NUMBER not in blob and "112345678" not in blob
    assert view["state"] == "pending" and 0 < view["expires_in"] <= 60


def test_a_guest_gets_nothing(hub):
    assert api.start(GUEST, "buy extra 10GB", "en") is None
    assert api.intercept(GUEST, "yes", "en") is None
    assert api.propose(GUEST, "purchase_extra_gb", {"package_id": 1}, [], "en", "x") is None
    assert api.pending_for(GUEST) is None


def test_feature_off_builds_nothing(hub, customer, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    result = api.start(customer, "buy extra 10GB", "en")
    assert result is not None and result.pending is None
    assert "1212" in result.text and "1222" in result.text
    assert api.pending_for(customer) is None
    assert api.propose(customer, "purchase_extra_gb", {"package_id": 11}, [], "en", "t") is None
    for text in ("log a fault", "ඔව්"):
        api.start(customer, text, "en")
    assert not {"purchase_extra_gb", "log_fault", "show_extra_gb_packages"} & set(names(hub))


def test_turning_the_flag_off_after_a_proposal_executes_nothing(hub, customer, monkeypatch):
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    result = api.confirm(customer, token)
    assert result.kind == "disabled" and "not switched on" in result.text
    assert "purchase_extra_gb" not in names(hub)
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    assert api.confirm(customer, token).kind == "invalid"


def test_a_token_of_another_customer_is_invalid(hub, customer):
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    other = verified(LTE_PLUS, user_id=2)
    assert api.confirm(other, token).kind == "invalid"
    assert api.cancel(other, token).kind == "invalid"
    assert "purchase_extra_gb" not in names(hub)
    assert api.pending_for(customer) is not None  # still waiting for its owner
    assert api.confirm(customer, "not-the-token").kind == "invalid"
    assert api.pending_for(customer) is not None


def test_a_new_proposal_replaces_the_old_one(hub, customer):
    first = api.start(customer, "buy extra 10GB", "en").pending.token
    second = api.start(customer, "buy extra 25GB", "en").pending.token
    assert first != second
    assert api.confirm(customer, first).kind == "invalid"
    assert api.confirm(customer, second).kind == "executed"
    assert calls(hub, "purchase_extra_gb")[0]["package_id"] == 12


def test_expiry_after_sixty_seconds(hub, customer):
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    hub.clock.now += 59
    assert api.pending_for(customer)["expires_in"] == 1
    hub.clock.now += 2
    assert api.pending_for(customer) is None
    assert api.confirm(customer, token).kind == "expired"
    assert "purchase_extra_gb" not in names(hub)


def test_a_late_spoken_yes_is_reported_as_expired(hub, customer):
    api.start(customer, "buy extra 10GB", "en")
    hub.clock.now += 61
    reply = api.intercept(customer, "yes", "en")
    assert reply.kind == "expired" and "timed out" in reply.text
    assert "purchase_extra_gb" not in names(hub)
    assert api.intercept(customer, "hello", "en") is None


@pytest.mark.parametrize(
    "language,yes",
    [("en", "Yes, please!"), ("en", "go ahead"), ("en", "OK"), ("si", "ඔව්"), ("si", "ඔව් කරන්න"), ("si", "ow"), ("ta", "ஆம்"), ("ta", "சரி"), ("ta", "seri")],
)
def test_a_clear_spoken_yes_confirms(hub, customer, language, yes):
    api.start(customer, "buy extra 10GB", "en")
    reply = api.intercept(customer, yes, language)
    assert reply.kind == "executed" and "Extra GB purchase submitted" in reply.text
    assert len(calls(hub, "purchase_extra_gb")) == 1


@pytest.mark.parametrize(
    "language,answer",
    [
        ("en", "no"), ("en", "cancel"), ("en", "never mind"), ("si", "නැහැ"), ("si", "එපා"), ("ta", "வேண்டாம்"), ("ta", "இல்லை"),
        ("en", "yes but how much is it?"), ("en", "yes and also check my bill"), ("en", "what is my balance"),
        ("en", "maybe"), ("en", "yes no"), ("en", "don't"), ("si", "ඔව් නැහැ"), ("ta", "ஆம் ஆனால் எவ்வளவு?"),
    ],
)
def test_no_and_anything_unclear_cancels(hub, customer, language, answer):
    api.start(customer, "buy extra 10GB", "en")
    reply = api.intercept(customer, answer, language)
    assert reply.kind == "cancelled" and reply.text == texts.tr("confirm.cancelled", language)
    assert "purchase_extra_gb" not in names(hub)
    assert api.pending_for(customer) is None


def test_without_a_pending_action_intercept_does_nothing(hub, customer):
    assert api.intercept(customer, "yes", "en") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("yes", "yes"), ("Yeah.", "yes"), ("sure, go ahead", "yes"), ("proceed please", "yes"), ("confirm", "yes"), ("හරි", "yes"),
        ("ow", "yes"), ("owu", "yes"), ("ஆம்", "yes"), ("ஓம்", "yes"), ("aam", "yes"), ("தயவுசெய்து சரி", "yes"),
        ("no", "no"), ("stop", "no"), ("நிறுத்து", "other"), ("வேண்டாம்", "no"), ("අවලංගු", "no"), ("නෑ", "no"),
        ("yes?", "other"), ("yes I want a refund", "other"), ("ok but cheaper", "other"), ("do it not", "other"), ("", "other"),
    ],
)
def test_reading_answers(text, expected):
    assert intents.classify_reply(text) == expected


# Transport failure and the hub's own words


def test_a_transport_failure_during_confirm_is_not_retried(hub, customer, monkeypatch):
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    session = gateway._pool().get(FIBRE_NUMBER)
    seen = []
    real = session.call

    def flaky(name, arguments, timeout=None):
        if name == "purchase_extra_gb":
            seen.append(name)
            raise TimeoutError()
        return real(name, arguments, timeout)

    monkeypatch.setattr(session, "call", flaky)
    result = api.confirm(customer, token)
    assert result.kind == "failed" and "did not try again" in result.text
    assert api.confirm(customer, token).kind == "invalid"
    assert seen == ["purchase_extra_gb"]
    assert "purchase_extra_gb" not in names(hub)


def test_a_dead_connection_is_not_retried_either(hub, customer, monkeypatch):
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    session = gateway._pool().get(FIBRE_NUMBER)
    count = []

    def dead(name, arguments, timeout=None):
        count.append(name)
        raise SessionDead()

    monkeypatch.setattr(session, "call", dead)
    assert api.confirm(customer, token).kind == "failed"
    assert count == ["purchase_extra_gb"]


def test_the_hubs_failure_text_is_reported_exactly(hub, customer):
    raw = json.dumps({"success": False, "error": "Insufficient credit for this package"})
    hub.customer_payloads["purchase_extra_gb"] = raw
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    result = api.confirm(customer, token)
    assert result.kind == "failed"
    assert result.text.endswith(raw) and result.text.startswith("The request was not completed.")
    assert len(calls(hub, "purchase_extra_gb")) == 1


def test_the_hubs_success_text_is_reported_exactly(hub, customer):
    raw = json.dumps({"success": True, "message": "Package 11 activated until 2026-11-12"})
    hub.customer_payloads["purchase_extra_gb"] = raw
    token = api.start(customer, "buy extra 10GB", "en").pending.token
    assert api.confirm(customer, token).text == raw


# The summary comes from the hub


def test_a_missing_price_is_not_invented(hub, customer):
    hub.customer_payloads["show_extra_gb_packages"] = json.dumps(
        {"success": True, "packages": [{"packageId": 21, "name": "Extra 5GB", "validity": "7 days"}]}
    )
    result = api.start(customer, "buy extra 5GB", "en")
    lines = {line["label"]: line["value"] for line in api.pending_for(customer)["lines"]}
    assert lines["Package"] == "Extra 5GB" and lines["Price"] == "not provided" and lines["Validity"] == "7 days"
    assert lines["Connection"] == "ending 678"
    assert "Price: not provided" in result.text


def test_several_matches_ask_and_create_nothing(hub, customer):
    hub.customer_payloads["show_extra_gb_packages"] = json.dumps(
        {"packages": [{"packageId": 1, "name": "Night 10GB A"}, {"packageId": 2, "name": "Night 10GB B"}, {"packageId": 3, "name": "Day 5GB"}]}
    )
    result = api.start(customer, "buy extra 10gb", "en")
    assert result.pending is None and "Night 10GB A" in result.text and "Night 10GB B" in result.text and "Day 5GB" not in result.text
    assert api.pending_for(customer) is None


def test_no_match_says_so(hub, customer):
    result = api.start(customer, "buy extra 50GB", "en")
    assert result.pending is None and "could not find a package" in result.text
    assert api.pending_for(customer) is None


def test_an_unreadable_list_creates_nothing(hub, customer):
    hub.customer_payloads["show_extra_gb_packages"] = "not json at all"
    result = api.start(customer, "buy extra 10GB", "en")
    assert result.pending is None and "could not read" in result.text


def test_a_vas_bundle(hub, customer):
    result = api.start(customer, "I want to add a data bundle, Extra 25GB", "en")
    assert result.pending.tool == "add_vas_data_bundle"
    assert api.confirm(customer, result.pending.token).kind == "executed"
    assert calls(hub, "add_vas_data_bundle")[0]["package_id"] == 12


def test_an_lte_prepaid_package(hub):
    login(hub, LTE_NUMBER)
    lte = verified(LTE_PLUS, user_id=2)
    hub.customer_payloads["show_lte_prepaid_package_list"] = json.dumps(
        {"status": "success", "packages": [
            {"OFFERING_ID": "OFF1", "OFFERING_NAME": "Daily 5GB", "DATA_VOLUME_GB": 5, "PRICE_LKR_WITH_TAX": 150, "VALIDITY": "1 day"},
            {"OFFERING_ID": "OFF2", "OFFERING_NAME": "Weekly 20GB", "DATA_VOLUME_GB": 20, "PRICE_LKR_WITH_TAX": 600, "VALIDITY": "7 days"}]}
    )
    result = api.start(lte, "I want to buy the weekly package", "en")
    assert result.pending.tool == "subscribe_lte_prepaid_package"
    lines = {line["label"]: line["value"] for line in api.pending_for(lte)["lines"]}
    assert lines["Package"] == "Weekly 20GB" and lines["Price"] == "600" and lines["Validity"] == "7 days"
    assert api.confirm(lte, result.pending.token).kind == "executed"
    assert len(calls(hub, "subscribe_lte_prepaid_package")) == 1


def test_the_reply_is_in_the_customers_language(hub, customer):
    for language, word in (("si", "තහවුරු"), ("ta", "உறுதிப்படுத்து")):
        result = api.start(customer, "buy extra 10GB", language)
        assert word in result.text


def test_sinhala_and_tamil_requests_are_understood(hub, customer):
    for text in ("අමතර 10GB එකක් ගන්න ඕන", "எக்ஸ்ட்ரா 10GB வாங்க வேண்டும்", "extra 10GB ekak ganna one"):
        api.cancel(customer, api.pending_for(customer)["id"]) if api.pending_for(customer) else None
        result = api.start(customer, text, "en")
        assert result is not None and result.pending is not None, text


# Fault slot filling

FAULT_DETAIL = {
    "status": "success", "serviceType": "Fibre", "accountType": "Postpaid", "serviceStatus": "Active",
    "listofBBSService": ["BB123456"], "listofVoiceService": ["0112345678"], "listofPEOService": [],
}


def test_a_fault_end_to_end_with_a_read_back(hub, customer):
    hub.customer_payloads["show_service_detail"] = json.dumps(FAULT_DETAIL)
    gateway._service_types.clear()
    first = api.start(customer, "I want to log a fault with my internet", "en")
    assert "contact number" in first.text and first.pending is None
    second = api.intercept(customer, "0771234567", "en")
    assert "what is the problem" in second.text
    third = api.intercept(customer, "The internet keeps dropping", "en")
    assert third.kind == "proposed" and third.confirmation["tool"] == "log_fault"
    assert "Broadband" in third.text and "ending 456" in third.text and "The internet keeps dropping" in third.text
    assert "log_fault" not in names(hub)
    lines = {line["label"]: line["value"] for line in third.confirmation["lines"]}
    assert lines["Contact number"].endswith("567") and "077123" not in lines["Contact number"]
    done = api.intercept(customer, "yes", "en")
    assert done.kind == "executed" and "F-2002" in done.text
    made = calls(hub, "log_fault")
    assert len(made) == 1 and made[0]["service_id"] == "BB123456"


def test_a_fault_asks_for_the_service_and_the_connection(hub, customer):
    detail = dict(FAULT_DETAIL, listofBBSService=[{"serviceId": "BB111222"}, {"serviceId": "BB333444"}])
    hub.customer_payloads["show_service_detail"] = json.dumps(detail)
    gateway._service_types.clear()
    assert "broadband, voice" in api.start(customer, "report a fault", "en").text
    asked = api.intercept(customer, "broadband", "en")
    assert "1)" in asked.text and "2)" in asked.text and "222" in asked.text and "444" in asked.text
    assert "contact number" in api.intercept(customer, "2", "en").text
    api.intercept(customer, "skip", "en")
    proposed = api.intercept(customer, "skip", "en")
    assert proposed.kind == "proposed" and "not provided" in proposed.text and "444" in proposed.text
    assert api.intercept(customer, "yes", "en").kind == "executed"
    assert calls(hub, "log_fault")[0]["service_id"] == "BB333444"


def test_a_fault_can_be_cancelled_and_times_out(hub, customer):
    hub.customer_payloads["show_service_detail"] = json.dumps(FAULT_DETAIL)
    gateway._service_types.clear()
    api.start(customer, "log a fault, broadband", "en")
    assert "stopped" in api.intercept(customer, "cancel", "en").text
    assert api.intercept(customer, "hello", "en") is None
    api.start(customer, "log a fault, broadband", "en")
    hub.clock.now += 601
    assert api.intercept(customer, "0771234567", "en") is None
    assert "log_fault" not in names(hub)


def test_a_fault_with_no_service_found(hub, customer):
    result = api.start(customer, "log a fault with my internet", "en")
    assert "could not find a service" in result.text and "1212" in result.text
    assert api.intercept(customer, "anything", "en") is None


def test_a_sinhala_and_a_tamil_fault_request(hub, customer):
    hub.customer_payloads["show_service_detail"] = json.dumps(FAULT_DETAIL)
    gateway._service_types.clear()
    for text, word in (("දෝෂයක් වාර්තා කරන්න", "සේවාව"), ("பழுது பதிவு செய்ய வேண்டும்", "சேவை")):
        reply = api.start(customer, text, "en")
        assert reply is not None and reply.pending is None
        api.intercept(customer, "cancel", "en")


# Logging


def test_no_tokens_or_numbers_reach_the_console(hub, customer, capsys):
    result = api.start(customer, "buy extra 10GB", "en")
    token = result.pending.token
    api.confirm(customer, token)
    out = capsys.readouterr()
    text = out.out + out.err
    assert token not in text and FIBRE_NUMBER not in text and "112345678" not in text


# Store


def test_the_store_is_capped(hub):
    for number in range(store.MAX_PENDING + 50):
        store.put(f"07{number:08d}", "log_fault", {}, "t", [], "en")
    assert len(store._actions) <= store.MAX_PENDING


def test_a_token_is_single_use_under_concurrency(hub):
    import threading

    action = store.put("0112345678", "log_fault", {}, "t", [], "en")
    wins = []

    def take():
        status, _ = store.take("0112345678", action.token)
        if status == "ok":
            wins.append(1)

    threads = [threading.Thread(target=take) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(wins) == 1


# Texts


def test_every_text_exists_in_all_languages_with_the_same_placeholders():
    import re

    for key, english in texts.EN.items():
        for table in (texts.SI, texts.TA):
            assert key in table, key
            assert set(re.findall(r"\{(\w+)\}", table[key])) == set(re.findall(r"\{(\w+)\}", english)), key
    for table in (texts.EN, texts.SI, texts.TA):
        for value in table.values():
            assert "--" not in value and "—" not in value and "–" not in value


# Routes


@pytest.fixture
def web(hub, make_user):
    from fastapi import FastAPI

    from auth.dependencies import USER_COOKIE
    from auth.tokens import AUDIENCE_USER, issue_token
    from conftest import make_client

    app = FastAPI()
    app.include_router(routes.router)

    def cookie_for(phone):
        user_id = make_user(phone_e164=phone)
        token, _ = issue_token(str(user_id), AUDIENCE_USER, 60, extra={"amr": ["otp"]})
        return user_id, token

    def client(token=None):
        c = make_client(app)
        if token:
            c.cookies.set(USER_COOKIE, token)
        return c

    login(hub)
    owner_id, owner_token = cookie_for(FIBRE_PLUS)
    other_id, other_token = cookie_for(LTE_PLUS)
    return type("Web", (), {"client": staticmethod(client), "owner": (owner_id, owner_token), "other": (other_id, other_token)})


def propose_for(web):
    access = AccessContext.verified(web.owner[0], "Nimal", FIBRE_PLUS)
    return api.start(access, "buy extra 10GB", "en").pending.token


def test_route_accept_executes_once(web, hub):
    token = propose_for(web)
    c = web.client(web.owner[1])
    pending = c.get("/confirm/pending")
    assert pending.status_code == 200 and pending.json()["confirmation"]["id"] == token
    assert pending.headers["cache-control"] == "no-store"
    first = c.post(f"/confirm/{token}/accept")
    assert first.status_code == 200 and first.headers["cache-control"] == "no-store"
    body = first.json()
    assert body["kind"] == "executed" and body["state"] == "executed" and "Extra GB purchase submitted" in body["text"]
    assert c.post(f"/confirm/{token}/accept").status_code == 404
    assert len(calls(hub, "purchase_extra_gb")) == 1
    assert c.get("/confirm/pending").json() == {"confirmation": None}


def test_route_cancel(web, hub):
    token = propose_for(web)
    c = web.client(web.owner[1])
    body = c.post(f"/confirm/{token}/cancel").json()
    assert body["kind"] == "cancelled" and body["state"] == "cancelled"
    assert c.post(f"/confirm/{token}/accept").status_code == 404
    assert "purchase_extra_gb" not in names(hub)


def test_route_invalid_and_foreign_tokens_look_the_same(web, hub):
    token = propose_for(web)
    other = web.client(web.other[1])
    foreign = other.post(f"/confirm/{token}/accept")
    junk = other.post("/confirm/not-a-token/accept")
    nobody = web.client().post(f"/confirm/{token}/accept")
    assert foreign.status_code == junk.status_code == nobody.status_code == 404
    assert foreign.json() == junk.json() == nobody.json()
    assert other.post(f"/confirm/{token}/cancel").status_code == 404
    assert other.get("/confirm/pending").json() == {"confirmation": None}
    assert "purchase_extra_gb" not in names(hub)
    assert api.pending_for(AccessContext.verified(web.owner[0], "N", FIBRE_PLUS)) is not None


def test_route_expired_token_is_reported(web, hub):
    token = propose_for(web)
    hub.clock.now += 61
    body = web.client(web.owner[1]).post(f"/confirm/{token}/accept").json()
    assert body["kind"] == "expired" and body["state"] == "expired"
    assert "purchase_extra_gb" not in names(hub)


def test_routes_are_rate_limited(web):
    c = web.client(web.owner[1])
    codes = [c.get("/confirm/pending").status_code for _ in range(routes.RATE_LIMIT + 3)]
    assert codes[: routes.RATE_LIMIT] == [200] * routes.RATE_LIMIT and codes[-1] == 429
