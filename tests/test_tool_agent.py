"""backend/tool_agent against the local mock hub (customer_tools=True, real per
connection logins through the gateway). Nothing here contacts the real hub."""

import json
import sys
import types

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed")

import config  # noqa: E402
from access import GUEST, AccessContext  # noqa: E402
from mcp_client import client as hub_client  # noqa: E402
from mcp_client.mock_hub import FIBRE_NUMBER, LTE_NUMBER, MockHub  # noqa: E402
from mcp_gateway import access_matrix, audit, gateway, pool as pool_module  # noqa: E402
from mcp_gateway.gateway import GatewayResult  # noqa: E402
from mcp_gateway.session import SessionDead  # noqa: E402

import customer_service  # noqa: E402
import tool_agent  # noqa: E402
from tool_agent import customer as customer_module  # noqa: E402
from tool_agent import knowledge, voice  # noqa: E402
from tool_agent import stats as stats_function  # noqa: E402,F401

KEY = "tool-agent-test-key-0123456789"
FIBRE_PLUS = "+94112345678"
LTE_PLUS = "+94712345670"
KB = "search_knowledgebase_tool"
SENSITIVE = ["4250.75", "Fibre Unlimited", "112345678", "712345670", "Nimal", "F-1001", "LTE Prepaid Super", "350.0"]
DIAL = ("1212", "0113121212", "1222")


def fibre():
    return AccessContext.verified(1, "Nimal", FIBRE_PLUS)


def lte():
    return AccessContext.verified(2, "Kamala", LTE_PLUS)


@pytest.fixture(scope="module")
def mock():
    server = MockHub(mode="sse", api_key=KEY, customer_tools=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def hub(mock, monkeypatch):
    mock.authenticated.clear()
    mock.connection_auth.clear()
    mock.customer_tool_errors.clear()
    mock.customer_tool_delays.clear()
    mock.customer_payloads.clear()
    mock.search_text = "The MOBITEL 1598 package costs Rs. 1598 per month."
    mock.otp_customers[FIBRE_NUMBER] = {"name": "Nimal Perera", "masked": "x"}
    mock.otp_customers[LTE_NUMBER] = {"name": "Kamal", "masked": "y"}
    mock.reset_counters()
    monkeypatch.setattr(config, "ANSWER_SOURCE", "mcp_hub")
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    monkeypatch.setattr(config, "EXPOSE_LATENCY_TOOL", False)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 5.0)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000)
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False)
    monkeypatch.setattr(config, "KB_MIN_SCORE", 0.25, raising=False)
    hub_client.set_hub(hub_client.HubClient(mock.url, "X-API-Key", KEY, 3.0, 0, 30.0))
    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    gateway._pending.clear()
    gateway._service_types.clear()
    audit.clear()
    access_matrix.reload()
    customer_service.clear_cache()
    tool_agent.reset_stats()
    monkeypatch.delitem(sys.modules, "confirmations", raising=False)
    yield mock
    pool.close_all()
    pool_module.set_pool(None)
    hub_client.set_hub(None)


def login(mock, number=FIBRE_NUMBER):
    start = gateway.begin_login(number, 2)
    assert start.ok, start.reason
    assert gateway.finish_login(number, start.hub_session_id, mock.otp_code).ok
    gateway.service_type(AccessContext.verified(1, "x", "+94" + number[1:]))  # learned as at sign in
    mock.reset_counters()


@pytest.fixture
def signed_in(hub):
    login(hub)
    return hub


def names(mock):
    return [name for name, _ in mock.tool_calls]


def kill_connections():
    pool = pool_module.get_pool()

    def dead(*args, **kwargs):
        raise SessionDead("connection lost")

    for key in pool.keys():
        session = pool.get(key)
        if session is not None:
            session.call = dead


# Switch


def test_other_answer_source_is_a_pass_and_changes_nothing(hub, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_SOURCE", "local_rag")
    plan = tool_agent.prepare("What is the MOBITEL 1598 package?", "en", GUEST)
    assert plan.kind == "pass" and plan.context == "" and plan.clause == "" and plan.canned == ""
    assert hub.tool_calls == []


def test_blank_text_is_a_pass(hub):
    assert tool_agent.prepare("   ", "en", GUEST).kind == "pass"
    assert hub.tool_calls == []


# Guests


def test_guest_answer_is_built_from_the_chunk_text(hub):
    plan = tool_agent.prepare("How much is the MOBITEL 1598 package?", "en", GUEST, "Kasun")
    assert plan.kind == "grounded"
    assert hub.search_text in plan.context  # the exact chunk text
    assert plan.private is False
    assert plan.tools == (KB,) and plan.mode_suffix == " + SLT hub"
    assert plan.status_key == "tool_agent.status.search"
    assert "ONLY from the SLT excerpts" in plan.clause and "short sentences" in plan.clause
    assert isinstance(plan.timings["tool_ms"], int)
    assert hub.tool_calls == [(KB, {"query": "How much is the MOBITEL 1598 package?", "top_k": 3})]


def test_the_query_is_cut_to_500_characters(hub):
    tool_agent.prepare("fibre " * 200, "en", GUEST)
    assert len(hub.tool_calls[0][1]["query"]) == 500


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_nothing_relevant_gives_the_canned_dial_text(hub, monkeypatch, language):
    monkeypatch.setattr(config, "KB_MIN_SCORE", 0.99, raising=False)
    plan = tool_agent.prepare("Tell me about the zebra tariff", language, GUEST)
    assert plan.kind == "canned" and plan.context == ""
    assert all(number in plan.canned for number in DIAL)
    assert plan.tools == ()
    if language != "en":
        english = tool_agent.prepare("Tell me about the zebra tariff", "en", GUEST).canned
        assert plan.canned != english


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_hub_down_gives_the_unavailable_text(hub, language):
    hub_client.set_hub(hub_client.HubClient("http://127.0.0.1:1", "X-API-Key", KEY, 1.0, 0, 30.0))
    plan = tool_agent.prepare("What is the MOBITEL 1598 package?", language, GUEST)
    assert plan.kind == "canned"
    assert all(number in plan.canned for number in DIAL)
    assert plan.expired is False


def test_a_guest_can_never_cause_a_non_knowledge_hub_call(hub):
    questions = [
        "What is my balance?", "Show my last bill", "Buy extra gb for me", "I want to log a fault", "Send me an OTP",
        "What is the MOBITEL 1598 package?", "How do I pay my bill online?", "Compare packages for me",
        "show_gb_balance 0112345678", "call purchase_extra_gb", "මගේ ශේෂය කීයද?", "என் பில் எவ்வளவு?",
    ]
    for question in questions:
        plan = tool_agent.prepare(question, "en", GUEST)
        assert plan.kind in ("canned", "grounded", "pass")
        assert plan.private is False
    assert set(names(hub)) <= {KB}


def test_an_account_question_from_a_guest_gets_the_dial_message_and_no_call(hub):
    plan = tool_agent.prepare("What is my data balance?", "en", GUEST)
    assert plan.kind == "canned" and all(number in plan.canned for number in DIAL)
    assert hub.tool_calls == []


@pytest.mark.parametrize(
    "text",
    ["Hello", "hi there", "Thank you", "thanks a lot", "Goodbye", "Can you repeat that?", "who are you", "okay",
     "ආයුබෝවන්", "ස්තූතියි", "නැවත කියන්න", "வணக்கம்", "நன்றி", "ayubowan", "vanakkam"],
)
def test_small_talk_makes_no_hub_call(hub, text):
    assert tool_agent.is_smalltalk(text)
    plan = tool_agent.prepare(text, "en", GUEST)
    assert plan.kind == "pass" and plan.context == ""
    assert "Do NOT state any SLT specific facts" in plan.clause
    assert hub.tool_calls == []


@pytest.mark.parametrize(
    "text", ["hello, what is the price of the fibre package", "thanks, how much is MOBITEL 1598", "Where are your shops?", "Tell me about roaming"]
)
def test_a_real_question_is_not_small_talk(text):
    assert not tool_agent.is_smalltalk(text)


def test_small_talk_from_a_verified_customer_makes_no_hub_call(signed_in):
    plan = tool_agent.prepare("Thank you", "en", fibre())
    assert plan.kind == "pass" and plan.private is False
    assert signed_in.tool_calls == []


# Verified customers


@pytest.mark.parametrize(
    "question, tool, expected",
    [
        ("What is my data balance?", "show_gb_balance", ["42.5", "17.5", "60", "2026-10-31"]),
        ("How much is my bill?", "show_bill_history", ["4250.75", "2026-10-25", "4100.0"]),
        ("What was my last payment?", "show_last_payment", ["4100.0", "2026-09-27", "Online"]),
        ("Show my service details", "show_service_detail", ["Fibre Unlimited 60", "Active", "100 Mbps"]),
        ("I have a fault", "show_fault_list", ["F-1001", "No internet", "2026-09-02"]),
        ("Show the available extra GB packages", "show_extra_gb_packages", ["Extra 10GB", "490.0", "Extra 25GB", "990.0"]),
        ("How much data have I used", "show_gb_balance", ["17.5"]),
    ],
)
def test_account_answers_carry_the_mocked_values_exactly(signed_in, question, tool, expected):
    plan = tool_agent.prepare(question, "en", fibre())
    assert plan.kind == "grounded", plan
    assert tool in plan.tools and tool in plan.context
    for value in expected:
        assert value in plan.context, value
    assert plan.private is True
    assert plan.mode_suffix == " + SLT account data"
    assert plan.status_key and plan.status_key.startswith("tool_agent.status.")
    assert "Never read out" in plan.clause and "Reply in English" in plan.clause
    assert set(names(signed_in)) <= set(customer_service.ALLOWED_TOOLS)
    assert plan.timings["tool_ms"] >= 0


def test_an_lte_prepaid_customer_gets_the_wallet(hub):
    login(hub, LTE_NUMBER)
    plan = tool_agent.prepare("What is my balance?", "en", lte())
    assert plan.kind == "grounded" and "350.0" in plan.context and "Daily 5GB" in plan.context


def test_private_flag_follows_the_cloud_switch(signed_in, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", True)
    assert tool_agent.prepare("What is my data balance?", "en", fibre()).private is False


def test_a_general_question_from_a_customer_uses_the_knowledge_base(signed_in):
    plan = tool_agent.prepare("How much is the MOBITEL 1598 package?", "en", fibre())
    assert plan.kind == "grounded" and signed_in.search_text in plan.context
    assert plan.private is False and plan.mode_suffix == " + SLT hub"
    assert names(signed_in) == [KB]


def test_at_most_four_tool_calls_per_turn(signed_in):
    text = "My balance, my bill, my last payment, my service details, my fault list and my extra packages"
    plan = tool_agent.prepare(text, "en", fibre())
    assert plan.kind == "grounded"
    assert len(plan.tools) <= 4
    assert len(signed_in.tool_calls) <= 4


def test_a_slow_tool_is_cut_off_by_the_budget(signed_in, monkeypatch):
    import time

    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 0.8)
    signed_in.customer_tool_delays["show_fault_list"] = 3.0
    started = time.monotonic()
    plan = tool_agent.prepare("I have a fault", "en", fibre())
    assert time.monotonic() - started < 2.5
    assert plan.kind == "canned" and plan.expired is False


def test_a_lost_login_gives_the_expired_text_and_no_hub_call(signed_in):
    gateway.drop_session(fibre())
    plan = tool_agent.prepare("What is my data balance?", "si", fibre())
    assert plan.kind == "canned" and plan.expired is True and plan.private is False
    assert plan.canned and plan.canned != tool_agent.prepare("What is my data balance?", "en", fibre()).canned
    assert signed_in.tool_calls == []


def test_a_hub_that_forgot_the_login_gives_the_expired_text(signed_in):
    signed_in.connection_auth.clear()  # the hub side lost the login: "No active session"
    plan = tool_agent.prepare("I have a fault", "en", fibre())
    assert plan.kind == "canned" and plan.expired is True
    assert not gateway.has_session(fibre())


def test_a_dead_connection_gives_the_unavailable_text(signed_in):
    kill_connections()
    plan = tool_agent.prepare("I have a fault", "en", fibre())
    assert plan.kind == "canned" and plan.expired is False
    assert all(number in plan.canned for number in DIAL)


def test_secret_looking_keys_never_reach_the_context(signed_in):
    signed_in.customer_payloads["show_gb_balance"] = json.dumps({
        "status": "success", "remainingGB": 42.5, "access_token": "SECRET-TOKEN-aaa", "password": "SECRET-PW-bbb",
        "otp": "SECRET-OTP-ccc", "nested": {"refresh_token": "SECRET-REFRESH-ddd"},
    })
    plan = tool_agent.prepare("What is my data balance?", "en", fibre())
    assert plan.kind == "grounded" and "42.5" in plan.context
    for secret in ("SECRET-TOKEN-aaa", "SECRET-PW-bbb", "SECRET-OTP-ccc", "SECRET-REFRESH-ddd"):
        assert secret not in plan.context


def test_the_context_is_cut_to_the_configured_size(signed_in, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 300)
    signed_in.customer_payloads["show_gb_balance"] = json.dumps({"status": "success", "note": "x" * 5000})
    plan = tool_agent.prepare("What is my data balance?", "en", fibre())
    assert plan.kind == "grounded" and len(plan.context) <= 300


def test_no_customer_data_in_stdout_or_logs(signed_in, capsys, caplog):
    import logging

    caplog.set_level(logging.INFO)
    for question in ("What is my data balance?", "How much is my bill?", "I have a fault", "What is the MOBITEL 1598 package?"):
        tool_agent.prepare(question, "en", fibre())
    gateway.drop_session(fibre())
    tool_agent.prepare("What is my data balance?", "en", fibre())
    joined = capsys.readouterr().out + caplog.text
    assert "Tool agent:" in joined
    for secret in SENSITIVE + ["MOBITEL 1598", "How much is my", "What is my data"]:
        assert secret not in joined, secret


# Daily usage and compare_package


def test_daily_usage_only_when_a_day_is_named(signed_in):
    tool_agent.prepare("How much data have I used", "en", fibre())
    assert "show_daily_usage" not in names(signed_in)
    signed_in.reset_counters()
    plan = tool_agent.prepare("What was my data usage on the 5th", "en", fibre())
    assert "show_daily_usage" in names(signed_in)
    assert dict(signed_in.tool_calls).get("show_daily_usage", {}).get("telephone_no") == FIBRE_NUMBER
    assert plan.kind == "grounded" and "show_daily_usage" in plan.tools


@pytest.mark.parametrize(
    "text, day",
    [("my data usage on the 5th", 5), ("data usage day 12", 12), ("data usage on 28", 28), ("5 වෙනිදා දත්ත භාවිතය", 5),
     ("my data usage on the 45th", None), ("what is my bill on the 5th", None), ("data usage", None)],
)
def test_day_number(text, day):
    assert customer_module.day_number(text) == day


def _with_compare(monkeypatch, record):
    real = customer_module.mcp_gateway.call

    def call(name, arguments, access, **kwargs):
        if name == "compare_package":
            record.append(dict(arguments))
            return GatewayResult(kind="ok", text=json.dumps({"success": True, "result": "Fibre Plus 90 costs 2500"}), tool=name)
        return real(name, arguments, access, **kwargs)

    monkeypatch.setattr(customer_module.mcp_gateway, "call", call)


def test_compare_package_uses_the_name_and_price_from_the_account_data(signed_in, monkeypatch):
    signed_in.customer_payloads["show_service_detail"] = json.dumps(
        {"status": "success", "serviceType": "Fibre", "accountType": "Postpaid", "package": "Fibre Unlimited 60", "monthlyFee": "Rs. 1,250.00"}
    )
    sent = []
    _with_compare(monkeypatch, sent)
    plan = tool_agent.prepare("Compare my package with a better one", "en", fibre())
    assert sent == [{"current_package_name": "Fibre Unlimited 60", "current_price": 1250.0}]
    assert plan.kind == "grounded" and "Fibre Plus 90 costs 2500" in plan.context and "compare_package" in plan.tools
    assert len(signed_in.tool_calls) <= 4


def test_compare_package_is_skipped_and_explained_when_the_price_is_missing(signed_in, monkeypatch):
    sent = []
    _with_compare(monkeypatch, sent)
    plan = tool_agent.prepare("Is there a better package than mine", "en", fibre())
    assert sent == []
    assert plan.kind == "grounded" and "Fibre Unlimited 60" in plan.context
    assert "monthly price" in plan.clause


def test_package_facts_parsing():
    blocks = [("show_service_detail", json.dumps({"data": {"packageName": "Gold", "price": 990}}))]
    assert customer_module.package_facts(blocks) == ("Gold", 990.0)
    assert customer_module.package_facts([("x", "plain text")]) == (None, None)


# Confirmations (a fake module, Agent D builds the real one)


def fake_confirmations(monkeypatch, intercept=None, start=None):
    module = types.ModuleType("confirmations")
    calls = []

    def make(name, result):
        def func(access, text, language):
            calls.append(name)
            return result

        return func

    module.intercept = make("intercept", intercept)
    module.start = make("start", start)
    monkeypatch.setitem(sys.modules, "confirmations", module)
    return calls


def result(text, status_key=None):
    return types.SimpleNamespace(text=text, status_key=status_key)


def test_intercept_answers_first_and_no_tool_is_called(signed_in, monkeypatch):
    calls = fake_confirmations(monkeypatch, intercept=result("Done, I added it.", "confirm.done"))
    plan = tool_agent.prepare("yes", "en", fibre())
    assert plan.kind == "canned" and plan.canned == "Done, I added it." and plan.status_key == "confirm.done"
    assert calls == ["intercept"] and signed_in.tool_calls == []


def test_start_answers_when_intercept_does_not(signed_in, monkeypatch):
    calls = fake_confirmations(monkeypatch, start=result("Please confirm: buy 10GB for Rs. 490?"))
    plan = tool_agent.prepare("buy extra 10GB", "en", fibre())
    assert plan.kind == "canned" and "Please confirm" in plan.canned
    assert calls == ["intercept", "start"] and signed_in.tool_calls == []


def test_confirmations_that_decline_fall_through(signed_in, monkeypatch):
    calls = fake_confirmations(monkeypatch)
    plan = tool_agent.prepare("What is my data balance?", "en", fibre())
    assert plan.kind == "grounded" and calls == ["intercept", "start"]


def test_a_broken_confirmations_module_does_not_break_the_chat(signed_in, monkeypatch):
    module = types.ModuleType("confirmations")

    def boom(*args):
        raise RuntimeError("bug")

    module.intercept = boom
    module.start = boom
    monkeypatch.setitem(sys.modules, "confirmations", module)
    assert tool_agent.prepare("What is my data balance?", "en", fibre()).kind == "grounded"


def test_confirmations_are_not_asked_when_the_switch_is_off(hub, monkeypatch):
    monkeypatch.setattr(config, "ANSWER_SOURCE", "local_rag")
    calls = fake_confirmations(monkeypatch, intercept=result("x"))
    assert tool_agent.prepare("yes", "en", fibre()).kind == "pass" and calls == []


# Robustness and numbers


def test_an_internal_error_never_raises(hub, monkeypatch):
    def boom(*args, **kwargs):
        raise ZeroDivisionError

    monkeypatch.setattr(knowledge, "search", boom)
    plan = tool_agent.prepare("What is the MOBITEL 1598 package?", "en", GUEST)
    assert plan.kind == "canned" and all(number in plan.canned for number in DIAL)


def test_stats_are_recorded_per_turn_and_per_tool(signed_in):
    assert tool_agent.stats()["turns"] == 0
    tool_agent.prepare("What is the MOBITEL 1598 package?", "en", GUEST)
    tool_agent.prepare("What is my data balance?", "en", fibre())
    snapshot = tool_agent.stats()
    assert snapshot["turns"] == 2
    assert snapshot["avg_tool_ms"] >= 0 and snapshot["p95_tool_ms"] >= 0
    assert set(snapshot["by_tool"]) >= {KB, "show_gb_balance"}
    assert snapshot["by_tool"]["show_gb_balance"]["calls"] == 1
    assert json.dumps(snapshot)  # plain data, safe for /health


def test_stats_are_thread_safe():
    import threading

    from tool_agent import stats as stats_module

    tool_agent.reset_stats()
    sys.modules["tool_agent.stats"].record  # the module, not the function
    threads = [threading.Thread(target=lambda: [sys.modules["tool_agent.stats"].record(5, {"x": 5}) for _ in range(100)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert tool_agent.stats()["turns"] == 800 and tool_agent.stats()["by_tool"]["x"]["calls"] == 800


# Speech


@pytest.mark.parametrize(
    "language, text, expected",
    [
        ("en", "You owe Rs. 1,250 today.", "You owe 1250 rupees today."),
        ("en", "The bill is Rs 1250.50.", "The bill is 1250 rupees and 50 cents."),
        ("en", "Pay LKR 1,250", "Pay 1250 rupees"),
        ("en", "It is 4,100.00 LKR", "It is 1250 rupees".replace("1250", "4100")),
        ("en", "You have 42.5 GB left, 80% used.", "You have 42.5 gigabytes left, 80 percent used."),
        ("en", "Total 1,234,567 units", "Total 1234567 units"),
        ("si", "ඔබේ බිල Rs. 1,250 යි", "ඔබේ බිල රුපියල් 1250 යි"),
        ("si", "ශේෂය 42.5 GB, 80%", "ශේෂය 42.5 ගිගාබයිට්, සියයට 80"),
        ("si", "Rs 1250.50", "රුපියල් 1250 යි ශත 50"),
        ("ta", "உங்கள் பில் Rs. 1,250", "உங்கள் பில் ரூபாய் 1250"),
        ("ta", "மீதம் 42.5 GB, 80%", "மீதம் 42.5 ஜிகாபைட், 80 சதவீதம்"),
        ("ta", "Rs 1250.50", "ரூபாய் 1250 மற்றும் 50 சதம்"),
        ("en", "**Bold** and `code`", "Bold and code"),
    ],
)
def test_for_speech(language, text, expected):
    assert voice.for_speech(text, language) == expected


def test_for_speech_leaves_other_text_and_never_raises():
    assert voice.for_speech("Call 0113121212 or 1212", "en") == "Call 0113121212 or 1212"
    assert voice.for_speech("", "en") == ""
    assert voice.for_speech(None, "en") == ""  # type: ignore[arg-type]


def test_shorten_for_voice():
    assert voice.shorten_for_voice(["a", "b"]) == "a and b"
    assert voice.shorten_for_voice(["a"]) == "a"
    assert voice.shorten_for_voice([]) == ""
    out = voice.shorten_for_voice(["one", "two", "three", "four", "five"], limit=3)
    assert out.startswith("one, two and three.") and "2 more" in out and "four" not in out
    si = voice.shorten_for_voice(["a", "b", "c", "d"], 3, "si")
    assert "a, b, c" in si and "1" in si and "more" not in si


# Function declarations


def test_schemas_for_a_guest_is_the_knowledge_search_only(hub):
    schemas = tool_agent.schemas_for(GUEST)
    assert [s["name"] for s in schemas] == [KB]
    assert set(schemas[0]) == {"name", "description", "parameters"}
    assert schemas[0]["parameters"]["type"] == "object" and "query" in schemas[0]["parameters"]["properties"]
    assert "title" not in json.dumps(schemas[0]["parameters"]["properties"]["query"])


def test_schemas_for_a_customer_hide_the_number_and_state_changing_tools(signed_in):
    schemas = tool_agent.schemas_for(fibre())
    listed = {s["name"] for s in schemas}
    assert {"show_gb_balance", "show_bill_history", KB} <= listed
    assert not listed & {"purchase_extra_gb", "add_vas_data_bundle", "subscribe_lte_prepaid_package", "log_fault",
                         "validate_customer_and_send_otp", "verify_otp_and_authenticate", "authenticate_with_token",
                         "refresh_customer_token", "get_function_latencies", "show_lte_wallet_detail"}
    for schema in schemas:
        assert "telephone_no" not in json.dumps(schema["parameters"])
        assert "telephone_no" not in schema["parameters"].get("required", [])
        assert schema["parameters"]["type"] == "object"


def test_schemas_for_is_empty_when_the_hub_is_down(hub):
    hub_client.set_hub(hub_client.HubClient("http://127.0.0.1:1", "X-API-Key", KEY, 1.0, 0, 30.0))
    assert tool_agent.schemas_for(GUEST) == []
