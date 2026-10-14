"""The MCP gateway against the mock hub (no network beyond localhost).

The mock behaves like the real hub on the point that matters: a login belongs
to ONE MCP connection. So these tests prove, with real connections, that a
customer's calls ride the connection that logged them in and that nothing
else is ever authenticated.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed")

import config  # noqa: E402
from access import GUEST, AccessContext  # noqa: E402
from mcp_client import client as hub_client  # noqa: E402
from mcp_client import tools as hub_tools  # noqa: E402
from mcp_client.errors import HubUnavailable  # noqa: E402
from mcp_client.mock_hub import FIBRE_NUMBER, LTE_NUMBER, MockHub  # noqa: E402
from mcp_gateway import access_matrix, audit, gateway, pool as pool_module  # noqa: E402
from mcp_gateway.session import CallOutput, SessionDead  # noqa: E402

KEY = "gateway-test-key-0123456789"
FIBRE_PLUS = "+94112345678"
LTE_PLUS = "+94712345670"


def verified(plus=FIBRE_PLUS):
    return AccessContext.verified(1, "Nimal", plus)


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
    mock.customer_payloads.clear()
    mock.otp_customers[FIBRE_NUMBER] = {"name": "Nimal Perera", "masked": "x"}
    mock.otp_customers[LTE_NUMBER] = {"name": "Kamal", "masked": "y"}
    mock.reset_counters()
    mock.sms_outbox.clear()
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    monkeypatch.setattr(config, "EXPOSE_LATENCY_TOOL", False)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000)
    hub_client.set_hub(hub_client.HubClient(mock.url, "X-API-Key", KEY, 3.0, 0, 30.0))
    pool = pool_module.SessionPool(sweep=False)
    pool_module.set_pool(pool)
    gateway._pending.clear()
    gateway._service_types.clear()
    audit.clear()
    access_matrix.reload()
    yield mock
    pool.close_all()
    for number in list(gateway._pending):
        gateway._close_pending(number)
    pool_module.set_pool(None)
    hub_client.set_hub(None)


def login(mock, number=FIBRE_NUMBER, media=2):
    start = gateway.begin_login(number, media)
    assert start.ok, start.reason
    done = gateway.finish_login(number, start.hub_session_id, mock.otp_code)
    assert done.ok, done.reason
    gateway.service_type(verified("+94" + number[1:]))  # learned once, as the first customer question would
    return start, done


def names(mock):
    return [name for name, _ in mock.tool_calls]


# Matrix


def test_matrix_tiers_and_service_types():
    m = access_matrix.reload()
    assert m.loaded
    assert m.tool_names_for("guest") == ["search_knowledgebase_tool"]
    assert "verify_otp_and_authenticate" in m.tool_names_for("authenticating")
    postpaid = m.tool_names_for("authenticated", "postpaid")
    lte = m.tool_names_for("authenticated", "lte_prepaid")
    unknown = m.tool_names_for("authenticated", "unknown")
    assert "show_gb_balance" in postpaid and "show_lte_wallet_detail" not in postpaid
    assert "show_lte_wallet_detail" in lte and "show_gb_balance" not in lte
    assert "show_service_detail" in unknown and "show_gb_balance" not in unknown
    assert "show_lte_wallet_detail" not in unknown
    assert m.never("authenticate_with_token") and not m.allowed("authenticate_with_token", "authenticated", "postpaid")
    assert m.is_state_changing("purchase_extra_gb") and not m.is_state_changing("show_gb_balance")
    assert "get_function_latencies" not in postpaid
    assert "get_function_latencies" in m.tool_names_for("authenticated", "postpaid", expose_latency=True)
    assert m.tool_names_for("diagnostic") == ["get_function_latencies"]


def test_matrix_fails_closed_on_a_broken_or_missing_file(tmp_path, capsys):
    broken = tmp_path / "broken.yaml"
    broken.write_text("tiers: [this is: not valid\n  - {", encoding="utf-8")
    for path in (str(broken), str(tmp_path / "missing.yaml")):
        m = access_matrix.load(path)
        assert not m.loaded
        assert m.tool_names_for("guest") == ["search_knowledgebase_tool"]
        assert m.tool_names_for("authenticating") == [] and m.tool_names_for("diagnostic") == []
        assert not m.allowed("show_gb_balance", "authenticated", "postpaid")
    out = capsys.readouterr().out
    assert out.count("could not be used") == 2 and "broken.yaml" not in out and "missing.yaml" not in out


def test_gateway_uses_the_failed_closed_matrix(hub, tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("tiers: nonsense", encoding="utf-8")
    monkeypatch.setattr(config, "TOOL_ACCESS_PATH", str(bad))
    access_matrix.reload()
    try:
        assert gateway.call("show_gb_balance", {}, verified()).kind == "refused"
        assert gateway.call("search_knowledgebase_tool", {"query": "fibre"}, GUEST).kind == "ok"
        assert gateway.status()["matrix_loaded"] is False
    finally:
        monkeypatch.undo()
        access_matrix.reload()


# Guests and refusals


@pytest.mark.parametrize("tool", [
    "show_gb_balance", "validate_customer_and_send_otp", "verify_otp_and_authenticate", "purchase_extra_gb",
    "authenticate_with_token", "get_function_latencies", "log_fault", "nonsense_tool",
])
@pytest.mark.parametrize("access", [None, GUEST])
def test_a_guest_can_call_nothing_but_the_knowledge_search(hub, tool, access):
    result = gateway.call(tool, {"telephone_no": FIBRE_NUMBER}, access)
    assert result.kind == "refused"
    assert hub.tool_calls == []


def test_guest_search_works_and_reports_json(hub):
    result = gateway.call("search_knowledgebase_tool", {"query": "mobitel 1598"}, None)
    assert result.kind == "ok" and result.data["count"] == 1 and result.data["chunks"][0]["score"]
    assert names(hub) == ["search_knowledgebase_tool"]


def test_the_never_tools_are_refused_even_for_a_customer(hub):
    login(hub)
    hub.reset_counters()
    for tool in ("authenticate_with_token", "refresh_customer_token"):
        assert gateway.call(tool, {}, verified()).reason == "never"
    assert gateway.call("validate_customer_and_send_otp", {"telephone_no": FIBRE_NUMBER}, verified()).kind == "refused"
    assert hub.tool_calls == []


# Customer calls ride the login connection


def test_a_customer_call_is_answered_on_the_login_connection(hub):
    login(hub)
    result = gateway.call("show_gb_balance", {}, verified())
    assert result.kind == "ok" and result.data["remainingGB"] == 42.5
    assert gateway.has_session(verified())
    assert len(hub.connection_auth) == 1  # the login connection, and no other


def test_the_number_is_forced_to_the_verified_one(hub):
    login(hub)
    hub.reset_counters()
    gateway.call("show_gb_balance", {"telephone_no": "0771111111"}, verified())
    gateway.call("show_gb_balance", {"telephone_no": "+94771111111"}, verified())
    assert [args["telephone_no"] for name, args in hub.tool_calls] == [FIBRE_NUMBER, FIBRE_NUMBER]


def test_token_style_arguments_from_the_model_are_dropped(hub):
    login(hub)
    hub.reset_counters()
    result = gateway.call(
        "show_gb_balance", {"access_token": "abc", "refresh_token": "d", "otp_value": "1", "telephone_no": "0771111111"}, verified()
    )
    assert result.kind == "ok"
    assert hub.tool_calls == [("show_gb_balance", {"telephone_no": FIBRE_NUMBER})]


def test_no_pooled_session_means_reauth_without_asking_the_hub(hub):
    result = gateway.call("show_gb_balance", {}, verified())
    assert (result.kind, result.reason) == ("reauth", "no_session")
    assert hub.tool_calls == []


def test_a_second_connection_is_never_authenticated(hub):
    login(hub)
    other = hub_tools.call_tool_sync("show_gb_balance", {"telephone_no": FIBRE_NUMBER})
    assert "No active session" in other.text
    still = gateway.call("show_gb_balance", {}, verified())
    assert still.kind == "ok"


def test_a_no_active_session_answer_is_reauth_and_the_session_is_dropped(hub):
    login(hub)
    for _, numbers in hub.connection_auth.values():
        numbers.clear()  # the hub forgot the login
    result = gateway.call("show_gb_balance", {}, verified())
    assert (result.kind, result.reason) == ("reauth", "login_lost")
    assert not gateway.has_session(verified())
    assert gateway.call("show_gb_balance", {}, verified()).reason == "no_session"


def test_other_hub_failures_are_errors_with_text(hub):
    login(hub)
    hub.customer_payloads["show_gb_balance"] = json.dumps({"success": False, "error": "Backend timeout"})
    result = gateway.call("show_gb_balance", {}, verified())
    assert result.kind == "error" and "Backend timeout" in result.text and gateway.has_session(verified())
    hub.customer_payloads["show_gb_balance"] = "Error executing tool show_gb_balance: boom"
    assert gateway.call("show_gb_balance", {}, verified()).kind == "error"


def test_service_type_filters_customer_tools(hub):
    login(hub)
    assert gateway.service_type(verified()) == "postpaid"
    assert gateway.call("show_lte_wallet_detail", {}, verified()).reason == "service_type"
    login(hub, LTE_NUMBER)
    lte = verified(LTE_PLUS)
    assert gateway.service_type(lte) == "lte_prepaid"
    assert gateway.call("show_lte_wallet_detail", {}, lte).kind == "ok"
    assert gateway.call("show_gb_balance", {}, lte).reason == "service_type"
    assert gateway.service_type(GUEST) == "unknown"


def test_service_type_is_cached_and_unknown_without_a_session(hub):
    assert gateway.service_type(verified()) == "unknown"
    login(hub)
    assert names(hub).count("show_service_detail") == 1
    gateway.service_type(verified())
    gateway.service_type(verified())
    assert names(hub).count("show_service_detail") == 1
    gateway._service_types.clear()
    assert gateway.service_type(verified()) == "postpaid"
    assert names(hub).count("show_service_detail") == 2


def test_the_answer_text_is_truncated(hub, monkeypatch):
    login(hub)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 30)
    result = gateway.call("show_gb_balance", {}, verified())
    assert len(result.text) == 30 and result.data["remainingGB"] == 42.5


# State changing tools


def test_state_changing_tools_are_refused_without_confirmation(hub, monkeypatch):
    login(hub)
    hub.reset_counters()
    assert gateway.call("purchase_extra_gb", {"package_id": 11}, verified()).reason == "state_changing"
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    assert gateway.call("purchase_extra_gb", {"package_id": 11}, verified()).reason == "state_changing"
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    assert gateway.call("purchase_extra_gb", {"package_id": 11}, verified(), confirmed=True).reason == "state_changing"
    assert hub.tool_calls == []


def test_a_confirmed_state_changing_tool_runs_once_when_enabled(hub, monkeypatch):
    login(hub)
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    hub.reset_counters()
    result = gateway.call("purchase_extra_gb", {"package_id": 11}, verified(), confirmed=True)
    assert result.kind == "ok" and names(hub) == ["purchase_extra_gb"]


def test_a_state_changing_call_is_never_retried(hub, monkeypatch):
    login(hub)
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    session = gateway._pool().get(FIBRE_NUMBER)
    attempts = []

    def broken(name, arguments, timeout=None):
        attempts.append(name)
        raise SessionDead("EndOfStream")

    monkeypatch.setattr(session, "call", broken)
    result = gateway.call("add_vas_data_bundle", {"package_id": 1}, verified(), confirmed=True)
    assert result.kind == "unavailable" and attempts == ["add_vas_data_bundle"]
    assert not gateway.has_session(verified())


def test_a_read_call_on_a_pooled_session_is_not_retried_either(hub, monkeypatch):
    login(hub)
    session = gateway._pool().get(FIBRE_NUMBER)
    attempts = []

    def broken(name, arguments, timeout=None):
        attempts.append(name)
        raise SessionDead("closed")

    monkeypatch.setattr(session, "call", broken)
    assert gateway.call("show_gb_balance", {}, verified()).kind == "unavailable"
    assert len(attempts) == 1
    assert gateway.call("show_gb_balance", {}, verified()).kind == "reauth"


def test_a_shared_read_gets_one_retry_on_a_transport_error(hub, monkeypatch):
    attempts = []

    def flaky(name, arguments, budget=None, access=None):
        attempts.append(name)
        if len(attempts) == 1:
            raise HubUnavailable("down", reason="connect")
        return hub_client.ToolOutput(text=json.dumps({"query": "q", "chunks": [], "count": 0}))

    monkeypatch.setattr(gateway, "call_tool_sync", flaky)
    assert gateway.call("search_knowledgebase_tool", {"query": "q"}, None).kind == "ok"
    assert len(attempts) == 2
    attempts.clear()
    monkeypatch.setattr(gateway, "call_tool_sync", lambda *a, **k: (attempts.append(1), (_ for _ in ()).throw(HubUnavailable("x")))[1])
    assert gateway.call("search_knowledgebase_tool", {"query": "q"}, None).kind == "unavailable"
    assert len(attempts) == 2


def test_a_timeout_on_a_shared_call_reports_timeout(hub, monkeypatch):
    def slow(*args, **kwargs):
        raise TimeoutError()

    monkeypatch.setattr(gateway, "call_tool_sync", slow)
    assert gateway.call("search_knowledgebase_tool", {"query": "q"}, None).kind == "timeout"
    assert gateway.status()["last_error"] == "timeout"


# Login


@pytest.mark.parametrize("media", [0, 4, -1, 99, "1", None, True])
def test_begin_login_refuses_other_channels_before_any_hub_call(hub, media):
    start = gateway.begin_login(FIBRE_NUMBER, media)
    assert not start.ok and start.reason == "refused"
    assert hub.tool_calls == []


@pytest.mark.parametrize("media,channel", [(1, "sms"), (2, "email"), (3, "voice")])
def test_begin_login_sends_over_the_chosen_channel_and_masks_contacts(hub, media, channel):
    start = gateway.begin_login(FIBRE_NUMBER, media)
    assert start.ok and start.hub_session_id
    assert start.masked_mobile == "XXXXXX4567" and start.masked_email == "XXXXXXXXXXth22@gmail.com"
    name, args = hub.tool_calls[0]
    assert name == "validate_customer_and_send_otp" and args["otp_media"] == media and args["channel"] == channel
    assert hub.sms_outbox[-1]["channel"] == channel
    assert gateway.status()["pending_logins"] == 1
    gateway.end_login(FIBRE_NUMBER)
    assert gateway.status()["pending_logins"] == 0


def test_an_unknown_number_is_rejected(hub):
    start = gateway.begin_login("0119999999", 1)
    assert (start.ok, start.reason) == (False, "rejected")
    assert gateway.status()["pending_logins"] == 0
    assert not gateway.begin_login("12", 1).ok


def test_a_wrong_code_keeps_the_login_pending_for_another_try(hub):
    start = gateway.begin_login(FIBRE_NUMBER, 2)
    wrong = gateway.finish_login(FIBRE_NUMBER, start.hub_session_id, "000000")
    assert (wrong.ok, wrong.reason) == (False, "invalid_code")
    assert gateway.status()["pending_logins"] == 1 and not gateway.has_session(verified())
    done = gateway.finish_login(FIBRE_NUMBER, start.hub_session_id, hub.otp_code)
    assert done.ok and done.subscriber_present
    assert gateway.status() == {"sessions": 1, "pending_logins": 0, "last_error": None, "matrix_loaded": True}
    assert gateway.call("show_gb_balance", {}, verified()).kind == "ok"


def test_finish_login_without_a_pending_login_or_with_the_wrong_id(hub):
    assert gateway.finish_login(FIBRE_NUMBER, "x", "123456").reason == "no_pending"
    start = gateway.begin_login(FIBRE_NUMBER, 2)
    assert gateway.finish_login(FIBRE_NUMBER, "other", hub.otp_code).reason == "mismatch"
    assert gateway.finish_login(FIBRE_NUMBER, start.hub_session_id, hub.otp_code).ok


def test_too_many_wrong_codes_close_the_login(hub):
    start = gateway.begin_login(FIBRE_NUMBER, 2)
    reasons = [gateway.finish_login(FIBRE_NUMBER, start.hub_session_id, "000000").reason for _ in range(gateway.MAX_CODE_TRIES)]
    assert reasons[-1] == "too_many_attempts"
    assert gateway.status()["pending_logins"] == 0


def test_a_pending_login_expires(hub, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(gateway, "_clock", lambda: now[0])
    start = gateway.begin_login(FIBRE_NUMBER, 2)
    session = gateway._pending[FIBRE_NUMBER].session
    now[0] += gateway.PENDING_TTL_SECONDS + 1
    assert gateway.finish_login(FIBRE_NUMBER, start.hub_session_id, hub.otp_code).reason == "no_pending"
    assert not session.alive and not gateway.has_session(verified())


def test_a_second_login_for_the_same_number_closes_the_first(hub):
    login(hub)
    first = gateway._pool().get(FIBRE_NUMBER)
    login(hub)
    second = gateway._pool().get(FIBRE_NUMBER)
    assert first is not second and not first.alive and second.alive
    assert gateway.call("show_gb_balance", {}, verified()).kind == "ok"


def test_drop_session_logs_the_customer_out(hub):
    login(hub)
    gateway.drop_session(verified())
    assert gateway.call("show_gb_balance", {}, verified()).kind == "reauth"


def test_a_hub_that_is_down_is_reported_as_unavailable(hub, monkeypatch):
    hub_client.set_hub(hub_client.HubClient("http://127.0.0.1:9", "X-API-Key", KEY, 1.0, 0, 30.0))
    start = gateway.begin_login(FIBRE_NUMBER, 2)
    assert (start.ok, start.reason) == (False, "unavailable")
    assert gateway.status()["last_error"] == "unavailable"


# Tool list for the model


def test_tool_schemas_hide_the_phone_argument_and_the_login_tools(hub):
    login(hub)
    schemas = gateway.tool_schemas(verified())
    by_name = {s["name"]: s for s in schemas}
    assert "show_gb_balance" in by_name and "search_knowledgebase_tool" in by_name
    for schema in schemas:
        assert set(schema) == {"name", "description", "input_schema"}
        assert "telephone_no" not in schema["input_schema"].get("properties", {})
        assert "telephone_no" not in schema["input_schema"].get("required", [])
    hidden = {"validate_customer_and_send_otp", "verify_otp_and_authenticate", "authenticate_with_token",
              "purchase_extra_gb", "add_vas_data_bundle", "subscribe_lte_prepaid_package", "log_fault",
              "show_lte_wallet_detail", "get_function_latencies"}
    assert not hidden & set(by_name)
    assert [s["name"] for s in gateway.tool_schemas(GUEST)] == ["search_knowledgebase_tool"]
    assert "show_lte_wallet_detail" in {s["name"] for s in gateway.tool_schemas(verified(), "lte_prepaid")}


def test_latencies_come_from_the_shared_path(hub):
    assert gateway.latencies() == hub.latencies


# Pool


class FakeSession:
    def __init__(self):
        self.alive = True
        self.closed = 0

    def open(self, timeout=None):
        pass

    def close(self):
        self.alive = False
        self.closed += 1


def fake_pool(now, **kwargs):
    return pool_module.SessionPool(clock=lambda: now[0], factory=FakeSession, sweep=False, **kwargs)


def test_idle_sessions_expire_on_access_and_on_sweep():
    now = [0.0]
    pool = fake_pool(now, idle_seconds=100, max_sessions=10)
    a, b = pool.open("0112345678"), pool.open("0112345679")
    now[0] = 60
    assert pool.get("0112345678") is a  # touching a resets its idle clock
    now[0] = 130
    assert pool.get("0112345679") is None and b.closed == 1
    assert pool.get("0112345678") is a
    now[0] = 300
    assert pool.sweep() == 1 and a.closed == 1 and pool.size() == 0


def test_the_least_recently_used_session_is_evicted_at_the_cap():
    now = [0.0]
    pool = fake_pool(now, idle_seconds=1000, max_sessions=2)
    first, second = pool.open("1"), pool.open("2")
    pool.get("1")
    third = pool.open("3")
    assert second.closed == 1 and first.alive and third.alive
    assert sorted(pool.keys()) == ["1", "3"]


def test_replacing_and_closing_sessions():
    now = [0.0]
    pool = fake_pool(now)
    old, new = pool.open("1"), pool.open("1")
    assert old.closed == 1 and pool.get("1") is new
    new.alive = False
    assert pool.get("1") is None  # a dead session is never handed out
    again = pool.open("1")
    pool.close("1")
    assert again.closed == 1 and pool.size() == 0
    pool.open("1"), pool.open("2")
    pool.close_all()
    assert pool.size() == 0


def test_discard_keeps_a_newer_login():
    pool = fake_pool([0.0])
    old = pool.open("1")
    new = pool.open("1")
    pool.discard("1", old)
    assert pool.get("1") is new


# Audit


def test_audit_lines_hold_no_number_argument_or_result(hub, capsys):
    login(hub)
    gateway.call("show_gb_balance", {"telephone_no": "0771111111", "note": "SECRETARG"}, verified())
    gateway.call("search_knowledgebase_tool", {"query": "SECRETQUERY"}, None)
    hub.customer_payloads["show_bill_history"] = json.dumps({"success": True, "secret": "SECRETRESULT"})
    gateway.call("show_bill_history", {}, verified())
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.startswith("GATEWAY ")]
    assert len(lines) >= 4
    for line in lines:
        assert line.count("=") == 4 and "session=" in line
    for banned in ("112345678", "771111111", "SECRETARG", "SECRETQUERY", "SECRETRESULT", hub.otp_code, "42.5"):
        assert banned not in out
    recent = audit.recent()
    assert {"tool", "outcome", "ms", "session"} <= set(recent[-1]) and len(audit.recent()) <= 200
    assert all(len(entry["session"]) in (1, 8) for entry in recent)


def test_an_unreadable_service_type_falls_back_to_the_postpaid_tools(hub):
    """The hub's show_service_detail can fail (it answered 405 for a real line). The
    customer is then treated as postpaid so balance and bill questions still answer."""
    login(hub)
    hub.customer_payloads["show_service_detail"] = "Error executing tool show_service_detail: Client error '405 Method Not Allowed'"
    gateway._service_types.clear()
    assert gateway.service_type(verified()) == "unknown"
    balance = gateway.call("show_gb_balance", {}, verified())
    assert balance.kind == "ok", balance
    assert gateway.call("show_bill_history", {}, verified()).kind == "ok"
    assert gateway.call("show_lte_wallet_detail", {}, verified()).reason == "service_type"
    schemas = {t["name"] for t in gateway.tool_schemas(verified())}
    assert "show_gb_balance" in schemas and "show_lte_wallet_detail" not in schemas


def test_a_failed_service_type_lookup_is_not_repeated_on_every_question(hub):
    login(hub)
    hub.customer_payloads["show_service_detail"] = json.dumps({"success": False, "error": "upstream"})
    gateway._service_types.clear()
    before = names(hub).count("show_service_detail")
    for _ in range(3):
        assert gateway.call("show_gb_balance", {}, verified()).kind == "ok"
    assert names(hub).count("show_service_detail") - before == 1


def test_a_stalled_first_try_of_a_public_tool_is_retried_once_and_the_breaker_cleared(hub, monkeypatch):
    """The live hub stalled on about one call in four; the second connection answers."""
    from mcp_client.tools import call_tool_sync as real

    calls = []

    def flaky(name, arguments, budget=None, access=None):
        calls.append(budget)
        if len(calls) == 1:
            raise TimeoutError("stalled")
        return real(name, arguments, budget=budget)

    reset = []

    class FakeHub:
        def reset_breaker(self):
            reset.append(True)

    monkeypatch.setattr(gateway, "call_tool_sync", flaky)
    monkeypatch.setattr(gateway, "get_hub", lambda: FakeHub())
    result = gateway.call("search_knowledgebase_tool", {"query": "fibre packages"}, None, budget=10)
    assert result.kind == "ok", result
    assert len(calls) == 2 and reset == [True]
    assert calls[0] < 10 and calls[1] > 0.3  # the first try did not use all of the time


def test_two_stalls_give_up_within_the_budget(hub, monkeypatch):
    calls = []

    def stalled(name, arguments, budget=None, access=None):
        calls.append(budget)
        raise TimeoutError("stalled")

    monkeypatch.setattr(gateway, "call_tool_sync", stalled)
    result = gateway.call("search_knowledgebase_tool", {"query": "x"}, None, budget=6)
    assert result.kind == "timeout" and len(calls) == 2
