"""Tools of the Gemini Live session: declarations, the executor and the instructions.

The hub is the local MockHub (customer_tools=True), as in test_mcp_gateway.py.
Nothing here uses the network beyond 127.0.0.1.
"""

import asyncio

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed")
pytest.importorskip("google.genai", reason="google-genai is not installed")

import config  # noqa: E402
from access import GUEST, AccessContext  # noqa: E402
from gemini_live import instructions, tokens, tools  # noqa: E402
from mcp_client import client as hub_client  # noqa: E402
from mcp_client.mock_hub import FIBRE_NUMBER, LTE_NUMBER, MockHub  # noqa: E402
from mcp_gateway import access_matrix, audit, gateway, pool as pool_module  # noqa: E402

KEY = "gemini-live-tools-test-key-0123456789"
FIBRE_PLUS = "+94112345678"
KB = "search_knowledgebase_tool"


def verified(plus=FIBRE_PLUS):
    return AccessContext.verified(1, "Nimal", plus)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 20))


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


def login(mock, number=FIBRE_NUMBER):
    start = gateway.begin_login(number, 2)
    assert start.ok, start.reason
    done = gateway.finish_login(number, start.hub_session_id, mock.otp_code)
    assert done.ok, done.reason
    gateway.service_type(verified("+94" + number[1:]))


def names(mock):
    return [name for name, _ in mock.tool_calls]


def declared(tool_list):
    return tools.declared_names(tool_list)


def all_property_names(tool_list):
    found = set()

    def walk(schema):
        if schema is None:
            return
        for key, child in (schema.properties or {}).items():
            found.add(key)
            walk(child)
        walk(schema.items)

    for t in tool_list:
        for d in t.function_declarations:
            walk(d.parameters)
    return found


# Declarations


def test_a_guest_sees_exactly_one_declaration(hub):
    built = tools.build_tools(GUEST)
    assert declared(built) == [KB]
    assert tools.build_tools(None) and declared(tools.build_tools(None)) == [KB]


def test_a_customer_sees_account_tools_without_telephone_or_token_parameters(hub):
    login(hub)
    built = tools.build_tools(verified())
    seen = declared(built)
    assert KB in seen and "show_gb_balance" in seen
    assert "purchase_extra_gb" not in seen  # state changing tools are off the list by default
    for forbidden in ("validate_customer_and_send_otp", "verify_otp_and_authenticate", "authenticate_with_token"):
        assert forbidden not in seen
    props = {p.lower() for p in all_property_names(built)}
    assert not any(bad in p for p in props for bad in ("phone", "token", "otp", "msisdn"))


def test_state_changing_tools_are_declared_only_when_asked(hub):
    login(hub)
    assert "purchase_extra_gb" in declared(tools.build_tools(verified(), include_state_changing=True))


def test_schema_conversion_drops_unsupported_keys_and_forbidden_parameters():
    schema = {
        "name": "t", "description": "d",
        "input_schema": {
            "type": "object", "title": "T", "additionalProperties": False, "$schema": "x",
            "properties": {
                "query": {"type": "string", "description": "q", "default": "a", "title": "Q"},
                "telephone_no": {"type": "string"},
                "access_token": {"type": "string"},
                "top_k": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": 3},
                "kind": {"type": "string", "enum": ["a", "b"]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["query", "telephone_no"],
        },
    }
    (decl,) = tools.declarations([schema])
    props = decl.parameters.properties
    assert set(props) == {"query", "top_k", "kind", "tags"}
    assert decl.parameters.required == ["query"]
    assert props["top_k"].nullable is True and props["top_k"].type.value == "INTEGER"
    assert props["kind"].enum == ["a", "b"]


def test_a_tool_without_parameters_has_no_parameter_schema():
    (decl,) = tools.declarations([{"name": "ping", "description": "x", "input_schema": {"type": "object", "properties": {}}}])
    assert decl.name == "ping" and decl.parameters is None


# Executor


def test_the_executor_runs_the_knowledge_search_for_a_guest(hub):
    executor = tools.ToolExecutor.for_tools(GUEST, tools.build_tools(GUEST), "en")
    out = run(executor.execute(KB, {"query": "mobitel 1598"}, GUEST))
    assert "result" in out and "chunks" in out["result"]
    assert names(hub) == [KB]


def test_a_forged_customer_tool_call_by_a_guest_is_refused_and_never_reaches_the_hub(hub):
    executor = tools.ToolExecutor.for_tools(GUEST, tools.build_tools(GUEST), "en")
    hub.reset_counters()
    for name in ("show_gb_balance", "purchase_extra_gb", "validate_customer_and_send_otp", "authenticate_with_token", "nonsense"):
        out = run(executor.execute(name, {"telephone_no": FIBRE_NUMBER}, GUEST))
        assert "error" in out and "result" not in out, name
    assert hub.tool_calls == []


def test_the_gateway_refuses_even_without_the_declared_name_filter(hub):
    executor = tools.ToolExecutor(GUEST)  # no allowed set: the gateway is the only guard
    hub.reset_counters()
    out = run(executor.execute("show_gb_balance", {}, GUEST))
    assert out == {"error": "not_allowed"}
    assert hub.tool_calls == []


def test_a_customer_call_uses_the_verified_number_whatever_the_model_sends(hub):
    login(hub)
    access = verified()
    executor = tools.ToolExecutor.for_tools(access, tools.build_tools(access), "en")
    hub.reset_counters()
    out = run(executor.execute("show_gb_balance", {"telephone_no": "0771111111", "access_token": "abc", "otp_value": "1"}, access))
    assert "result" in out and "remainingGB" in out["result"]
    assert hub.tool_calls == [("show_gb_balance", {"telephone_no": FIBRE_NUMBER})]


def test_a_lost_login_comes_back_as_reauth_with_a_note(hub):
    access = verified()  # never logged in
    executor = tools.ToolExecutor(access, "en")
    out = run(executor.execute("show_gb_balance", {}, access))
    assert out["error"] == "reauth" and "verify" in out["note"].lower()
    assert hub.tool_calls == []


def test_a_state_changing_tool_asks_for_confirmation_and_never_calls_the_hub(hub, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    import confirmations

    login(hub)
    access = verified()
    shown = []
    built = tools.build_tools(access, include_state_changing=True)
    executor = tools.ToolExecutor.for_tools(access, built, "en", on_confirmation=shown.append)
    hub.reset_counters()
    out = run(executor.execute("purchase_extra_gb", {"package_id": 7, "telephone_no": "0771111111"}, access))
    assert out["status"] == "needs_confirmation" and out["say"]
    assert "purchase_extra_gb" not in names(hub)
    view = confirmations.pending_for(access)
    assert view is not None and view["tool"] == "purchase_extra_gb"
    assert shown and shown[0]["id"] == view["id"]
    # the browser view and the spoken sentence hold no telephone number or token
    assert "0771111111" not in str(view) and "0771111111" not in out["say"]
    # only the confirmation step runs it, once
    result = confirmations.confirm(access, view["id"])
    assert result.kind in ("executed", "failed")
    assert names(hub).count("purchase_extra_gb") == 1


def test_a_state_changing_tool_is_refused_when_actions_are_switched_off(hub):
    login(hub)
    access = verified()
    built = tools.build_tools(access, include_state_changing=True)
    executor = tools.ToolExecutor.for_tools(access, built, "en")
    hub.reset_counters()
    out = run(executor.execute("purchase_extra_gb", {"package_id": 7}, access))
    assert out == {"error": "not_allowed"}
    assert hub.tool_calls == []


def test_a_guest_cannot_start_a_confirmation(hub, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    executor = tools.ToolExecutor(GUEST)
    out = run(executor.execute("purchase_extra_gb", {"package_id": 1}, GUEST))
    assert out == {"error": "not_allowed"}
    assert hub.tool_calls == []


def test_the_executor_never_raises():
    class Boom:
        def call(self, *a, **k):
            raise RuntimeError("hub exploded")

    executor = tools.ToolExecutor(GUEST, gateway=Boom(), matrix=access_matrix.load())
    assert run(executor.execute(KB, {"query": "x"}, GUEST)) == {"error": "tool_failed"}
    assert run(executor.execute("", None, GUEST)) == {"error": "unknown_tool"}


# Instructions


@pytest.mark.parametrize("lang,word", [("si", "Sinhala"), ("ta", "Tamil"), ("en", "English")])
def test_instruction_demands_the_language_and_carries_the_contact_rule(lang, word):
    from tool_agent import messages

    text = instructions.build_instruction(lang, GUEST)
    assert f"Always reply in {word}" in text
    assert messages.contact_rule() in text
    assert "ONLY from the results of your tools" in text
    assert "GUEST" in text and "VERIFIED" not in text
    assert "rupee" in text and "digit by digit" in text and "Sri Lankan" in text


def test_instruction_for_a_customer_never_holds_the_number():
    text = instructions.build_instruction("en", verified(), state_changing=True)
    assert "VERIFIED customer named Nimal" in text and "needs_confirmation" in text
    assert FIBRE_PLUS not in text and FIBRE_NUMBER not in text and "GUEST" not in text


def test_instruction_for_an_expired_login_asks_to_verify_again():
    from access.context import GUEST_EXPIRED

    assert "verify again" in instructions.build_instruction("si", GUEST_EXPIRED)


def test_instruction_unknown_language_falls_back_to_english_and_greeting_is_optional():
    assert "Always reply in English" in instructions.build_instruction("xx", None)
    assert "greeting the caller" in instructions.build_instruction("en", GUEST, greet=True)
    assert "greeting the caller" not in instructions.build_instruction("en", GUEST)


# Ephemeral token helper (not used by the default path)


def test_mint_ephemeral_token_asks_for_one_use_and_a_short_expiry():
    seen = {}

    class Tokens:
        def create(self, config=None):
            seen["config"] = config

            class T:
                name = "auth_tokens/abc"

            return T()

    class Client:
        auth_tokens = Tokens()

    from google.genai import types

    live = types.LiveConnectConfig(response_modalities=["AUDIO"])
    out = tokens.mint_ephemeral_token("k", "gemini-3.8-live", live, ttl_seconds=60, client=Client())
    cfg = seen["config"]
    assert out == "auth_tokens/abc" and cfg.uses == 1
    assert cfg.live_connect_constraints.model == "gemini-3.8-live"
    assert (cfg.expire_time - cfg.new_session_expire_time).total_seconds() > 0
    with pytest.raises(ValueError):
        tokens.mint_ephemeral_token("", "m")
