"""backend/customer_service: intent, the read only allow list, the fetch flow
against the local mock hub (no network, no real hub), failure classification,
rendering and privacy. Nothing here contacts the real MCP hub."""

import json
import time

import pytest

import config
from conftest import require_module

FIBRE = "0112345678"
LTE = "0712345670"
FIBRE_E164 = "+94112345678"
LTE_E164 = "+94712345670"
KEY = "customer-service-test-key"

REFUSED = [
    "purchase_extra_gb", "add_vas_data_bundle", "log_fault", "subscribe_lte_prepaid_package",
    "authenticate_with_token", "refresh_customer_token", "validate_customer_and_send_otp",
    "verify_otp_and_authenticate", "show_daily_usage", "show_lte_prepaid_package_list",
]
# Strings that only exist in customer data and must never reach stdout or logs.
SENSITIVE = ["4250.75", "Fibre Unlimited", "112345678", "712345670", "Nimal", "F-1001", "LTE Prepaid Super", "350.0"]


@pytest.fixture(scope="module")
def mods():
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    cs = require_module("customer_service")
    return type("Mods", (), dict(
        cs=cs,
        service=require_module("customer_service.service"),
        render=require_module("customer_service.render"),
        tools=require_module("customer_service.tools"),
        messages=require_module("customer_service.messages"),
        client=require_module("mcp_client.client"),
        errors=require_module("mcp_client.errors"),
        hub_tools=require_module("mcp_client.tools"),
        mock=require_module("mcp_client.mock_hub"),
        access=require_module("access"),
        gateway=require_module("mcp_gateway.gateway"),
        pool=require_module("mcp_gateway.pool"),
        session_dead=require_module("mcp_gateway.session").SessionDead,
    ))


@pytest.fixture(scope="module")
def mock_hub(mods):
    server = mods.mock.MockHub(mode="sse", api_key=KEY, customer_tools=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def hub(mods, mock_hub, monkeypatch):
    mock_hub.authenticated.clear()
    mock_hub.connection_auth.clear()
    mock_hub.customer_tool_errors.clear()
    mock_hub.customer_tool_delays.clear()
    mock_hub.customer_payloads.clear()
    mock_hub.otp_customers[FIBRE] = {"name": "Nimal Perera", "masked": "x"}
    mock_hub.otp_customers[LTE] = {"name": "Kamala", "masked": "y"}
    mods.service.clear_cache()
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 5.0)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000)
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    mods.client.set_hub(mods.client.HubClient(mock_hub.url, "X-API-Key", KEY, 3.0, 0, 30.0))
    pool = mods.pool.SessionPool(sweep=False)
    mods.pool.set_pool(pool)
    mods.gateway._service_types.clear()
    # Real per connection logins: the gateway keeps each customer's connection.
    for number in (FIBRE, LTE):
        start = mods.gateway.begin_login(number, 2)
        assert start.ok, start.reason
        assert mods.gateway.finish_login(number, start.hub_session_id, mock_hub.otp_code).ok
    mock_hub.reset_counters()
    yield mock_hub
    pool.close_all()
    mods.pool.set_pool(None)
    mods.client.set_hub(None)


@pytest.fixture
def fibre(mods):
    return mods.access.AccessContext.verified(1, "Nimal Perera", FIBRE_E164)


@pytest.fixture
def lte(mods):
    return mods.access.AccessContext.verified(2, "Kamala Silva", LTE_E164)


def kill_connections(mods):
    """The hub connections of every pooled customer die (hub restart, network)."""
    pool = mods.pool.get_pool()

    def dead(*args, **kwargs):
        raise mods.session_dead("connection lost")

    for key in pool.keys():
        session = pool.get(key)
        if session is not None:
            session.call = dead


def called(hub):
    return [name for name, _ in hub.tool_calls]


# Intent


@pytest.mark.parametrize(
    "text, topic",
    [
        ("What is my data balance?", "balance"),
        ("How much is my bill?", "bills_payments"),
        ("What was my last payment", "bills_payments"),
        ("My internet is not working", "connection_status"),
        ("I have a fault", "faults"),
        ("What is my current package", "account"),
        ("මගේ ශේෂය කීයද", "balance"),
        ("මගේ බිල්පත කීයද", "bills_payments"),
        ("මගේ අන්තර්ජාලය වැඩ නැහැ", "connection_status"),
        ("දෝෂයක් තියෙනවා", "faults"),
        ("මගේ ගිණුම් විස්තර", "account"),
        ("mage balance eka kiyada", "balance"),
        ("mage bill eka kiyada", "bills_payments"),
        ("mage internet wada na", "connection_status"),
        ("mage fault eka", "faults"),
        ("என் பேலன்ஸ் எவ்வளவு", "balance"),
        ("என் பில் எவ்வளவு", "bills_payments"),
        ("இணைப்பு நிலை", "connection_status"),
        ("என் புகார்", "faults"),
        ("எனது கணக்கு விவரங்கள்", "account"),
    ],
)
def test_intent_topics(mods, text, topic):
    found = mods.cs.detect(text, "en", None)
    assert topic in found.topics, (text, found)


@pytest.mark.parametrize("text", ["tell me a joke", "How do I pay online", "what is the address of the Kandy branch", ""])
def test_intent_ignores_general_questions(mods, text):
    assert mods.cs.detect(text, "en", None).topics == ()


@pytest.mark.parametrize(
    "text",
    [
        "I want to buy an extra data package", "please activate roaming", "cancel my package", "upgrade my plan",
        "subscribe me to a bundle", "add 5GB to my line",
        "මට අමතර පැකේජයක් මිලදී ගන්න ඕනේ", "මගේ පැකේජය වෙනස් කරන්න", "මේක අවලංගු කරන්න",
        "inna package eka cancel karanna", "roaming activate karanna", "mage package eka wenas karanna",
        "பேக்கேஜ் வாங்க வேண்டும்", "எனது பேக்கேஜை மாற்ற வேண்டும்", "இதை ரத்து செய்ய வேண்டும்",
    ],
)
def test_change_requests_are_detected_in_every_language(mods, text):
    found = mods.cs.detect(text, "en", None)
    assert found.changes, text
    assert found.topics, text  # a change request still fetches read only data


def test_a_plain_question_is_not_a_change(mods):
    assert not mods.cs.detect("What is my data balance?", "en", None).changes
    assert not mods.cs.detect("what is the address of my branch", "en", None).changes


def test_plan_gives_calls_without_a_number(mods, fibre):
    calls = mods.cs.plan("What is my bill?", "en", fibre)
    assert [c.name for c in calls] == ["show_bill_history", "show_last_payment"]
    assert all("telephone_no" not in c.arguments for c in calls)


# Allow list


@pytest.mark.parametrize("name", REFUSED)
def test_refused_tools_raise(mods, name):
    assert name not in mods.tools.ALLOWED_TOOLS
    with pytest.raises(mods.tools.ToolRefused):
        mods.tools.require_allowed(name)


def test_allowed_tools_are_all_read_only_and_known(mods):
    for name in mods.tools.ALLOWED_TOOLS:
        mods.tools.require_allowed(name)
        assert name.startswith("show_")
    assert not set(mods.tools.ALLOWED_TOOLS) & set(REFUSED)


def test_every_planned_call_is_on_the_allow_list(mods):
    for topic in mods.tools.TOPICS:
        for product in mods.tools.PRODUCTS + (None,):
            for call in mods.tools.calls_for([topic], product):
                assert call.name in mods.tools.ALLOWED_TOOLS


def test_a_planned_refused_tool_is_never_sent(mods, hub, fibre, monkeypatch):
    bad = [mods.tools.ToolCall("purchase_extra_gb", {"package_id": 1}, "balance"),
           mods.tools.ToolCall("log_fault", {}, "balance")]
    monkeypatch.setattr(mods.service, "calls_for", lambda topics, product=None: list(bad))
    out = mods.cs.fetch("What is my balance?", "en", fibre)
    assert out.kind == "none"
    assert out.errors == {"purchase_extra_gb": "denied", "log_fault": "denied"}
    assert "purchase_extra_gb" not in called(hub) and "log_fault" not in called(hub)


def test_only_allowed_tools_reach_the_hub_across_scenarios(mods, hub, fibre, lte):
    for ctx, text in (
        (fibre, "What is my balance and my bill?"), (fibre, "I have a fault, my package"),
        (lte, "What is my balance and my last payment?"), (fibre, "I want to buy an extra data package"),
    ):
        mods.service.clear_cache()
        assert mods.cs.fetch(text, "en", ctx).kind == "data"
    assert set(called(hub)) <= set(mods.tools.ALLOWED_TOOLS), called(hub)
    assert not set(called(hub)) & set(REFUSED)


# Who may get data


def test_guest_and_pin_only_get_none_and_cause_no_hub_call(mods, hub):
    pin_only = mods.access.AccessContext.from_claims(["pin"], user_id=3, display_name="Nimal Perera", phone_e164=FIBRE_E164)
    assert not pin_only.is_verified
    for ctx in (mods.access.GUEST, pin_only, None):
        out = mods.cs.fetch("What is my data balance?", "en", ctx)
        assert out.kind == "none" and not out.context and not out.canned
    assert hub.requests == 0 and hub.tool_calls == []


def test_a_general_question_from_a_verified_user_calls_nothing(mods, hub, fibre):
    out = mods.cs.fetch("tell me a joke", "en", fibre)
    assert out.kind == "none" and hub.requests == 0


def test_disabled_by_flag(mods, hub, fibre, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", False)
    out = mods.cs.fetch("What is my data balance?", "en", fibre)
    assert out.kind == "none" and hub.requests == 0
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", True)
    assert mods.cs.fetch("What is my data balance?", "en", fibre).kind == "data"


# Data for the two product types


def test_postpaid_fibre_balance(mods, hub, fibre):
    out = mods.cs.fetch("What is my data balance?", "en", fibre)
    assert out.kind == "data" and out.product == "postpaid"
    assert out.tools == ("show_gb_balance",)
    assert "[show_gb_balance]" in out.context and "42.5" in out.context
    assert called(hub) == ["show_service_detail", "show_gb_balance"]  # the probe, then the tool
    assert set(out.timings_ms) >= {"show_gb_balance"}


def test_postpaid_bill_uses_bill_tools(mods, hub, fibre):
    out = mods.cs.fetch("How much is my bill?", "en", fibre)
    assert out.kind == "data"
    assert set(out.tools) == {"show_bill_history", "show_last_payment"}
    assert "4250.75" in out.context and "billAmount" in out.context


def test_lte_prepaid_balance_and_reload_history(mods, hub, lte):
    out = mods.cs.fetch("What is my data balance?", "en", lte)
    assert out.kind == "data" and out.product == "lte_prepaid"
    assert set(out.tools) == {"show_lte_wallet_detail", "show_lte_purchased_packages"}
    out = mods.cs.fetch("What was my last payment", "en", lte)
    assert out.tools == ("show_reload_history",)
    sent = dict(hub.tool_calls)["show_reload_history"]
    assert len(sent["from_date"]) == 10 and sent["from_date"] < sent["to_date"]  # ISO dates, 90 days


def test_service_detail_answers_the_account_topic_without_a_second_call(mods, hub, fibre):
    out = mods.cs.fetch("What is my current package", "en", fibre)
    assert out.tools == ("show_service_detail",)
    # The gateway reads the service type once (cached), then the topic call itself.
    assert called(hub).count("show_service_detail") == 2


def test_faults_need_no_probe(mods, hub, fibre):
    out = mods.cs.fetch("I have a fault", "en", fibre)
    assert out.tools == ("show_fault_list",)
    assert called(hub) == ["show_fault_list"]


def test_package_information_uses_empty_package_name(mods, hub, fibre):
    out = mods.cs.fetch("I want to change my package", "en", fibre)
    assert out.kind == "data" and out.changes_requested
    sent = [args for name, args in hub.tool_calls if name == "show_extra_gb_packages"]
    assert sent and sent[0]["package_name"] == ""
    assert "purchase_extra_gb" not in called(hub) and "add_vas_data_bundle" not in called(hub)


def test_product_type_is_cached_for_ten_minutes(mods, hub, fibre, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(mods.service, "_now", lambda: clock[0])
    monkeypatch.setattr(mods.gateway, "_clock", lambda: clock[0])
    mods.cs.fetch("What is my data balance?", "en", fibre)
    mods.cs.fetch("What is my data balance?", "en", fibre)
    assert called(hub).count("show_service_detail") == 1
    clock[0] += 601
    mods.cs.fetch("What is my data balance?", "en", fibre)
    assert called(hub).count("show_service_detail") == 2


def test_unknown_service_type_falls_back_to_the_postpaid_tools(mods, hub, lte):
    # The gateway treats a service type it cannot read as postpaid (owner decision),
    # so the postpaid tools are tried and the LTE prepaid ones are never called.
    hub.customer_payloads["show_service_detail"] = "Service is fine"  # no product words
    mods.cs.fetch("What is my data balance?", "en", lte)
    assert not {"show_lte_wallet_detail", "show_lte_purchased_packages"} & set(called(hub))


def test_product_detection_from_text(mods):
    f = mods.service.product_from
    assert f("Service: LTE, account type Prepaid") == "lte_prepaid"
    assert f("Fibre postpaid") == "postpaid"
    assert f(json.dumps({"serviceType": "Copper"})) == "postpaid"
    assert f("nothing useful") is None


# The number sent


def test_the_verified_number_is_sent_in_national_form(mods, hub, fibre):
    mods.cs.fetch("How much is my bill?", "en", fibre)
    assert {args["telephone_no"] for _, args in hub.tool_calls} == {FIBRE}


def test_gate_rewrites_plus_form_and_refuses_another_number(mods, hub, fibre):
    # The gate (not this package) owns the number: the verified one is sent in
    # national form, any other number is refused before a network call.
    mods.hub_tools.call_tool_sync("show_gb_balance", {"telephone_no": FIBRE_E164}, 3, access=fibre)
    assert hub.tool_calls[-1] == ("show_gb_balance", {"telephone_no": FIBRE})
    hub.reset_counters()
    for other in ("0000000000", "0112999999", "PLACEHOLDER"):
        with pytest.raises(mods.access.AccessDenied):
            mods.hub_tools.call_tool_sync("show_gb_balance", {"telephone_no": other}, 3, access=fibre)
    assert hub.tool_calls == []


def test_a_model_chosen_number_in_the_call_is_replaced_by_the_verified_one(mods, hub, fibre, monkeypatch):
    call = mods.tools.ToolCall("show_bill_history", {"telephone_no": "0119999999"}, "bills_payments")
    monkeypatch.setattr(mods.service, "calls_for", lambda topics, product=None: [call])
    out = mods.cs.fetch("How much is my bill?", "en", fibre)
    assert out.kind == "data"
    assert dict(hub.tool_calls)["show_bill_history"]["telephone_no"] == FIBRE


# Failures


def test_not_authenticated_on_the_hub_is_reauth(mods, hub, fibre):
    mods.gateway.drop_session(fibre)  # the pooled login is gone
    out = mods.cs.fetch("What is my data balance?", "si", fibre)
    assert out.kind == "reauth" and out.context == ""
    assert out.canned == mods.cs.canned("reauth_needed", "si")
    assert hub.tool_calls == []  # no login, so the hub is not asked


def test_a_login_on_another_connection_does_not_help(mods, hub, fibre):
    """Like the real hub: verify on one connection does not sign in another, and the
    gateway only ever uses the customer's own pooled connection."""
    mods.gateway.drop_session(fibre)
    assert mods.cs.fetch("What is my bill?", "en", fibre).kind == "reauth"
    sent = json.loads(mods.hub_tools.call_tool_sync("validate_customer_and_send_otp", {"telephone_no": FIBRE}).text)
    mods.hub_tools.call_tool_sync(
        "verify_otp_and_authenticate", {"telephone_no": FIBRE, "session_id": sent["session_id"], "otp_value": hub.otp_code}
    )
    mods.service.clear_cache()
    assert mods.cs.fetch("What is my bill?", "en", fibre).kind == "reauth"


@pytest.mark.parametrize(
    "message",
    ["Customer not authenticated", "you are not logged in", "token invalid", "Session expired", "Unauthorized", "HTTP 401"],
)
def test_reauth_wording(mods, message):
    status, _ = mods.service.classify_failure(mods.errors.HubToolError("show_gb_balance: " + message))
    assert status == "reauth"


def test_failure_classes(mods):
    c = mods.service.classify_failure
    # The hub refusing our own api key is a setup problem, not "verify again".
    assert c(mods.errors.HubAuthError("rejected"))[0] == "unavailable"
    assert c(mods.errors.HubUnavailable("down"))[0] == "unavailable"
    assert c(mods.errors.HubBreakerOpen(4.0))[0] == "unavailable"
    assert c(TimeoutError())[0] == "unavailable"
    assert c(mods.errors.HubToolError("show_gb_balance: bad request"))[0] == "error"
    assert c(mods.access.AccessDenied("x", "y"))[0] == "denied"


def test_an_error_inside_a_successful_body_is_classified(mods, hub, fibre):
    hub.customer_payloads["show_gb_balance"] = json.dumps({"status": "error", "message": "Not authenticated"})
    assert mods.cs.fetch("What is my data balance?", "en", fibre).kind == "reauth"
    hub.customer_payloads["show_gb_balance"] = json.dumps({"status": "error", "message": "boom"})
    out = mods.cs.fetch("What is my data balance?", "en", fibre)
    assert out.kind == "unavailable" and out.errors == {"show_gb_balance": "error"}


def test_hub_down_is_unavailable(mods, hub, fibre):
    kill_connections(mods)
    out = mods.cs.fetch("I have a fault", "en", fibre)
    assert out.kind == "unavailable"
    assert out.canned == mods.cs.canned("unavailable", "en")
    assert "1212" in out.canned and "0113121212" in out.canned and "1222" in out.canned


def test_a_lost_connection_is_unavailable_then_needs_a_new_login(mods, hub, fibre):
    kill_connections(mods)
    assert mods.cs.fetch("I have a fault", "en", fibre).kind == "unavailable"
    hub.reset_counters()
    assert mods.cs.fetch("What is my bill?", "en", fibre).kind == "reauth"
    assert hub.requests == 0


def test_one_failing_tool_among_several_keeps_the_rest(mods, hub, fibre):
    hub.customer_tool_errors["show_last_payment"] = "payment service failed"
    out = mods.cs.fetch("How much is my bill?", "en", fibre)
    assert out.kind == "data"
    assert out.tools == ("show_bill_history",)
    assert out.errors == {"show_last_payment": "error"}
    assert "[show_bill_history]" in out.context and "show_last_payment" not in out.context


def test_every_tool_failing_is_unavailable(mods, hub, fibre):
    hub.customer_tool_errors["show_bill_history"] = "failed"
    hub.customer_tool_errors["show_last_payment"] = "failed"
    assert mods.cs.fetch("How much is my bill?", "en", fibre).kind == "unavailable"


def test_total_budget_is_respected(mods, hub, fibre, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 0.8)
    hub.customer_tool_delays["show_fault_list"] = 3.0
    started = time.monotonic()
    out = mods.cs.fetch("I have a fault", "en", fibre)
    assert time.monotonic() - started < 2.5
    assert out.kind == "unavailable" and out.errors == {"show_fault_list": "timeout"}


def test_budget_exceeded_by_one_tool_keeps_the_others(mods, hub, fibre, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 1.5)
    hub.customer_tool_delays["show_last_payment"] = 4.0
    started = time.monotonic()
    out = mods.cs.fetch("How much is my bill?", "en", fibre)
    assert time.monotonic() - started < 3.5
    assert out.kind == "data" and out.tools == ("show_bill_history",)
    assert out.errors["show_last_payment"] == "timeout"


def test_internal_error_never_raises(mods, hub, fibre, monkeypatch):
    monkeypatch.setattr(mods.service, "_run", lambda *a, **k: 1 / 0)
    out = mods.cs.fetch("What is my data balance?", "en", fibre)
    assert out.kind == "unavailable" and out.canned


# Changes


@pytest.mark.parametrize(
    "text", ["I want to buy an extra data package", "මට අමතර පැකේජයක් මිලදී ගන්න ඕනේ", "பேக்கேஜ் வாங்க வேண்டும்", "roaming activate karanna"]
)
def test_changes_are_flagged_and_still_fetch_read_only_data(mods, hub, fibre, text):
    hub.reset_counters()
    out = mods.cs.fetch(text, "en", fibre)
    assert out.kind == "data" and out.changes_requested
    assert set(called(hub)) <= set(mods.tools.ALLOWED_TOOLS)
    assert "changes cannot be made here" in mods.cs.system_clause("en", out.changes_requested)
    assert "1212" in mods.cs.system_clause("en", True)
    assert "changes cannot be made here" not in mods.cs.system_clause("en")


# Rendering


def test_secret_keys_are_dropped_at_any_depth(mods):
    payload = {
        "billAmount": 10, "token": "t1", "Access_Token": "t2", "refresh-token": "t3",
        "nested": {"password": "p", "Secret": "s", "otp": "123456", "ok": 1, "deeper": [{"authorization": "Bearer x", "keep": 2}]},
        "id_token": "t4",
    }
    text = mods.render.body(payload)
    for leaked in ("t1", "t2", "t3", "t4", "123456", "Bearer", '"p"', '"s"'):
        assert leaked not in text, leaked
    assert "billAmount" in text and '"keep":2' in text and '"ok":1' in text


def test_secret_in_plain_text_is_masked(mods):
    text = mods.render.body("Balance 10 GB. access_token: abc123 password=hunter2")
    assert "abc123" not in text and "hunter2" not in text and "10 GB" in text


def test_secrets_from_the_hub_never_reach_the_context(mods, hub, fibre):
    hub.customer_payloads["show_gb_balance"] = json.dumps(
        {"status": "success", "remainingGB": 7, "access_token": "SECRET-TOKEN-VALUE", "data2": {"refresh_token": "SECRET-REFRESH"}}
    )
    out = mods.cs.fetch("What is my data balance?", "en", fibre)
    assert out.kind == "data" and "remainingGB" in out.context
    assert "SECRET" not in out.context


def test_output_shapes(mods):
    ToolOutput = mods.client.ToolOutput
    expected = {"remainingGB": 5}
    shapes = [
        ToolOutput(text=json.dumps(expected)),
        ToolOutput(text=json.dumps({"data": expected})),
        ToolOutput(text=json.dumps({"result": expected, "status": "success"})),
        ToolOutput(text="", structured=expected),
        ToolOutput(text="", structured={"result": expected}),
        ToolOutput(text=json.dumps({"data": json.dumps(expected)})),
    ]
    for shape in shapes:
        assert mods.render.extract(shape) == expected, shape
    assert mods.render.extract(ToolOutput(text="You have 5 GB left")) == "You have 5 GB left"
    assert mods.render.extract(ToolOutput(text="")) == ""
    assert mods.render.body("") == mods.render.EMPTY


def test_truncation_keeps_every_tool_represented(mods):
    blocks = [(f"show_tool_{i}", "x" * (4000 if i == 0 else 300)) for i in range(5)]
    text = mods.render.assemble(blocks, 1200)
    assert len(text) <= 1200
    for i in range(5):
        assert f"[show_tool_{i}]" in text
    assert text.count("(trimmed)") >= 1
    assert mods.render.assemble(blocks[1:], 100000).count("(trimmed)") == 0  # everything fits, nothing cut
    assert mods.render.assemble([], 1000) == ""


def test_truncation_through_fetch(mods, hub, fibre, monkeypatch):
    hub.customer_payloads["show_bill_history"] = json.dumps({"rows": ["a" * 50] * 200})
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 500)
    out = mods.cs.fetch("How much is my bill?", "en", fibre)
    assert out.kind == "data" and len(out.context) <= 500
    assert "[show_bill_history]" in out.context and "[show_last_payment]" in out.context
    assert "paymentAmount" in out.context  # the short block survives whole


# Messages


@pytest.mark.parametrize("lang", ["en", "si", "ta"])
def test_canned_texts_in_three_languages(mods, lang):
    for reason in ("unavailable", "changes_not_supported"):
        text = mods.cs.canned(reason, lang)
        assert "1212" in text and "0113121212" in text and "1222" in text and "{" not in text
    assert mods.cs.canned("reauth_needed", lang) and "{" not in mods.cs.canned("reauth_needed", lang)
    assert mods.cs.canned("unavailable", "si") != mods.cs.canned("unavailable", "en")
    assert mods.cs.canned("unavailable", "ta") != mods.cs.canned("unavailable", "en")


def test_canned_falls_back_to_built_in_english(mods, monkeypatch):
    import i18n

    monkeypatch.setattr(i18n, "t", lambda key, lang=None, **v: key)
    assert "verify again" in mods.cs.canned("reauth_needed", "si")
    assert "1212" in mods.cs.canned("unavailable", "ta")
    assert mods.cs.canned("nonsense", "en") == mods.cs.canned("unavailable", "en")


def test_system_clause_languages(mods):
    assert "Sinhala" in mods.cs.system_clause("si")
    assert "Tamil" in mods.cs.system_clause("ta")
    assert "English" in mods.cs.system_clause("xx")


# Privacy


def test_no_customer_data_in_stdout_or_logs(mods, hub, fibre, lte, capsys, caplog):
    import logging

    # INFO and up: the MCP SDK itself logs request arguments at DEBUG, which
    # is the SDK's behaviour and outside this package (see the report).
    caplog.set_level(logging.INFO)
    captured_text = []
    for ctx, text in (
        (fibre, "What is my data balance?"), (fibre, "How much is my bill?"), (lte, "What is my balance?"),
        (fibre, "I have a fault"), (fibre, "I want to buy an extra data package"),
    ):
        mods.service.clear_cache()
        out = mods.cs.fetch(text, "en", ctx)
        assert out.kind == "data"
    mods.gateway.drop_session(fibre)
    assert mods.cs.fetch("What is my data balance?", "en", fibre).kind == "reauth"
    captured_text.append(capsys.readouterr().out)
    captured_text.append(caplog.text)
    joined = "\n".join(captured_text)
    assert "Customer data:" in joined  # the one summary line per fetch is there
    for secret in SENSITIVE:
        assert secret not in joined, secret
    assert not [r for r in caplog.records if r.name.startswith("customer_service")]
