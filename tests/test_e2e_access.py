"""End to end: access control (plan part 3, R2).

A guest asking a customer service question is answered by the policy guard
with a canned, translated reply that names the hotline 1212, and NO model
is called. The fake tiers of test_e2e_chain record every call, so "no LLM
call was made" is asserted, not assumed. Covered over POST /chat and over
/ws/voice, in English, Sinhala and Tamil. A verified session (user_session
cookie whose token carries amr ["otp"]) is answered normally. The MCP tool
gate refuses customer tools without a verified session, refuses another
number, and lets public tools through.

Detection: the access package must exist and agent.Orchestrator.respond_stream
must accept the plan's `access` keyword. Cookie based verified sessions also
need server.py to build the context (source mentions access). Until then
the tests skip with the reason, then run by themselves.
"""

import asyncio
import enum
import inspect
import json
import re

import numpy as np
import pytest

import agent
import config
from conftest import USER_PHONE_E164, make_client, optional_import, require_module, server_source
from test_e2e_chain import (  # noqa: F401  (llm_env is a fixture used by the tests below)
    GEMINI_REPLY,
    LIVEKIT_REPLY,
    LOCAL_REPLY,
    llm_env,
)

ONE_SECOND = np.zeros(16000, dtype=np.float32).tobytes()
HALF_SECOND = np.zeros(8000, dtype=np.float32).tobytes()

BLOCKED = {
    "en": [
        "What is my current bill balance and when was my last payment?",
        "Show my data usage and my package balance",
    ],
    "si": [
        "මගේ වත්මන් බිල්පත් ශේෂය කීයද, අවසන් ගෙවීම කවදාද?",
        "මගේ දත්ත භාවිතය සහ පැකේජ ශේෂය පෙන්වන්න",
    ],
    "ta": [
        "எனது தற்போதைய பில் நிலுவை எவ்வளவு, கடைசி கட்டணம் எப்போது?",
        "என் டேட்டா பயன்பாடு மற்றும் தொகுப்பு இருப்பைக் காட்டு",
    ],
}
BLOCKED_CASES = [(language, text) for language, texts in BLOCKED.items() for text in texts]

PUBLIC = {
    "en": "Tell me about SLT fibre broadband packages",
    "si": "SLT ෆයිබර් බ්‍රෝඩ්බෑන්ඩ් පැකේජ ගැන කියන්න",
    "ta": "SLT ஃபைபர் பிராட்பேண்ட் தொகுப்புகள் பற்றி சொல்லுங்கள்",
}

SCRIPT = {"si": re.compile("[඀-෿]"), "ta": re.compile("[஀-௿]")}


def assert_canned_blocked_reply(reply, language):
    assert "1212" in reply, f"the canned reply must name the hotline 1212: {reply!r}"
    assert reply not in (GEMINI_REPLY, LIVEKIT_REPLY, LOCAL_REPLY)
    if language in SCRIPT:
        assert SCRIPT[language].search(reply), f"the {language} reply should be written in its own script: {reply!r}"


# Detection


@pytest.fixture
def access_wired():
    require_module("access.context", "backend/access not delivered yet")
    require_module("access.guard", "access.guard not delivered yet")
    require_module("access.tool_gate", "access.tool_gate not delivered yet")
    if "access" not in inspect.signature(agent.Orchestrator.respond_stream).parameters:
        pytest.skip("Orchestrator.respond_stream(access=...) not wired yet (lead integration pending)")


@pytest.fixture
def access_env(access_wired, llm_env):
    return llm_env


@pytest.fixture
def server_builds_context(access_wired):
    if "access" not in server_source():
        pytest.skip("server.py does not build an AccessContext from the session yet (lead integration pending)")


def session_token(user_id, amr=None):
    from auth.tokens import AUDIENCE_USER, issue_token

    extra = {"amr": amr} if amr is not None else None
    token, _ = issue_token(str(user_id), AUDIENCE_USER, 60, extra=extra)
    return token


# Guest, over POST /chat


@pytest.mark.parametrize("language,text", BLOCKED_CASES)
def test_guest_blocked_over_chat_without_any_model_call(access_env, language, text):
    client = access_env.client()
    response = client.post("/chat", json={"text": text, "language": language})
    client.close()
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"reply", "mode"}
    assert_canned_blocked_reply(body["reply"], language)
    access_env.assert_no_model_called()


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_guest_public_question_still_reaches_the_model(access_env, language):
    client = access_env.client()
    response = client.post("/chat", json={"text": PUBLIC[language], "language": language})
    client.close()
    assert response.status_code == 200, response.text
    assert response.json()["reply"] == GEMINI_REPLY
    assert len(access_env.calls["gemini"]) == 1


def test_claiming_a_name_does_not_make_a_guest_verified(access_env):
    client = access_env.client()
    response = client.post(
        "/chat", json={"text": BLOCKED["en"][0], "language": "en", "user_name": "Nimal Perera"}
    )
    client.close()
    assert_canned_blocked_reply(response.json()["reply"], "en")
    access_env.assert_no_model_called()


def test_a_garbage_session_cookie_is_a_guest(access_env):
    client = access_env.client()
    client.cookies.set("user_session", "not.a.token")
    response = client.post("/chat", json={"text": BLOCKED["en"][0], "language": "en"})
    client.close()
    assert_canned_blocked_reply(response.json()["reply"], "en")
    access_env.assert_no_model_called()


# Guest, over /ws/voice


def _receive_until(ws, predicate, limit=200):
    seen = []
    for _ in range(limit):
        message = ws.receive_json()
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(f"no matching message in {seen!r}")


def _is_state(state):
    return lambda m: m.get("type") == "status" and m.get("state") == state


def _voice_turn(client, fake_speech, text, language, headers=None):
    """One spoken question; returns (agent_response message, all messages)."""
    fake_speech.transcripts = [text]
    kwargs = {"headers": headers} if headers else {}
    with client.websocket_connect("/ws/voice", **kwargs) as ws:
        ws.send_text(
            json.dumps({"type": "start_session", "language_hint": language, "user_name": None, "greet": False})
        )
        _receive_until(ws, _is_state("calibrating"))
        ws.send_bytes(ONE_SECOND)
        _receive_until(ws, _is_state("listening"))
        ws.send_bytes(HALF_SECOND)
        final, _ = _receive_until(ws, lambda m: m.get("type") == "final_transcript")
        assert final["text"] == text
        response, seen = _receive_until(ws, lambda m: m.get("type") in ("agent_response", "error"))
        assert response["type"] == "agent_response", response
        _receive_until(ws, lambda m: m.get("type") == "speaking_done")
        ws.send_text(json.dumps({"type": "end_session"}))
    return response, seen


@pytest.mark.parametrize("language,text", BLOCKED_CASES)
def test_guest_blocked_over_voice_without_any_model_call(access_env, fake_speech, language, text):
    client = access_env.client()
    try:
        response, _ = _voice_turn(client, fake_speech, text, language)
    finally:
        client.close()
    assert_canned_blocked_reply(response["text"], language)
    spoken = " ".join(fake_speech.spoken)
    assert "1212" in spoken, "the canned reply is spoken, not only shown"
    access_env.assert_no_model_called()


def test_guest_public_question_over_voice_reaches_the_model(access_env, fake_speech):
    client = access_env.client()
    try:
        response, _ = _voice_turn(client, fake_speech, PUBLIC["en"], "en")
    finally:
        client.close()
    assert GEMINI_REPLY in response["text"]
    assert len(access_env.calls["gemini"]) == 1


# Verified session


@pytest.fixture
def verified_cookie(make_user):
    user_id = make_user()
    return session_token(user_id, amr=["otp"])


@pytest.mark.parametrize("language,text", BLOCKED_CASES)
def test_verified_session_is_answered_normally_over_chat(
    access_env, server_builds_context, verified_cookie, language, text
):
    client = access_env.client()
    client.cookies.set("user_session", verified_cookie)
    response = client.post("/chat", json={"text": text, "language": language})
    client.close()
    assert response.status_code == 200, response.text
    assert response.json()["reply"] == GEMINI_REPLY
    assert len(access_env.calls["gemini"]) == 1


def test_verified_session_is_answered_normally_over_voice(
    access_env, server_builds_context, fake_speech, verified_cookie
):
    client = access_env.client()
    try:
        response, _ = _voice_turn(
            client, fake_speech, BLOCKED["en"][0], "en", headers={"cookie": f"user_session={verified_cookie}"}
        )
    finally:
        client.close()
    assert GEMINI_REPLY in response["text"]
    assert len(access_env.calls["gemini"]) == 1


def test_pin_only_session_is_still_a_guest_for_customer_data(access_env, server_builds_context, make_user):
    """ADR 3.4: an old PIN login is valid for the greeting, not for data."""
    pin_only = session_token(make_user(), amr=None)
    client = access_env.client()
    client.cookies.set("user_session", pin_only)
    response = client.post("/chat", json={"text": BLOCKED["en"][0], "language": "en"})
    client.close()
    assert_canned_blocked_reply(response.json()["reply"], "en")
    access_env.assert_no_model_called()


def test_a_session_whose_amr_lacks_otp_is_a_guest(access_env, server_builds_context, make_user):
    weak = session_token(make_user(), amr=["pwd"])
    client = access_env.client()
    client.cookies.set("user_session", weak)
    response = client.post("/chat", json={"text": BLOCKED["en"][0], "language": "en"})
    client.close()
    assert_canned_blocked_reply(response.json()["reply"], "en")
    access_env.assert_no_model_called()


# The MCP tool gate


def _contexts():
    context = require_module("access.context")
    guest = context.GUEST
    level = guest.level
    if isinstance(level, enum.Enum):
        verified_level = next(
            (member for member in type(level) if member.name.lower() == "verified"), None
        )
        assert verified_level is not None, "access.context level enum has no VERIFIED member"
    else:
        verified_level = "verified"

    def verified(phone=USER_PHONE_E164):
        return context.AccessContext(
            level=verified_level, user_id=1, display_name="Nimal Perera", phone_e164=phone, language="en"
        )

    return guest, verified


def _denied_exception():
    gate = require_module("access.tool_gate")
    denied = getattr(gate, "AccessDenied", None)
    if denied is None:
        denied = getattr(optional_import("access"), "AccessDenied", None)
    assert denied is not None, "AccessDenied is not exported by access.tool_gate or access"
    return denied


CUSTOMER_TOOLS = [
    "show_gb_balance",
    "show_bill_history",
    "show_last_payment",
    "show_service_detail",
    "show_fault_list",
    "log_fault",
    "show_daily_usage",
    "show_reload_history",
    "purchase_extra_gb",
    "add_vas_data_bundle",
    "subscribe_lte_prepaid_package",
]
PUBLIC_TOOLS = ["search_knowledgebase_tool", "compare_package"]


@pytest.mark.parametrize("tool", CUSTOMER_TOOLS)
def test_gate_refuses_a_customer_tool_without_a_verified_session(access_wired, tool):
    gate = require_module("access.tool_gate")
    guest, _ = _contexts()
    with pytest.raises(_denied_exception()):
        gate.authorize_tool(tool, {"telephone_no": USER_PHONE_E164}, guest)


@pytest.mark.parametrize("tool", CUSTOMER_TOOLS)
def test_gate_refuses_another_customers_number(access_wired, tool):
    gate = require_module("access.tool_gate")
    _, verified = _contexts()
    with pytest.raises(_denied_exception()):
        gate.authorize_tool(tool, {"telephone_no": "+94117654321"}, verified())


def test_gate_refuses_a_customer_tool_with_no_number_at_all(access_wired):
    gate = require_module("access.tool_gate")
    _, verified = _contexts()
    with pytest.raises(_denied_exception()):
        gate.authorize_tool("show_gb_balance", {}, verified())


def test_gate_allows_the_verified_users_own_number(access_wired):
    gate = require_module("access.tool_gate")
    _, verified = _contexts()
    gate.authorize_tool("show_gb_balance", {"telephone_no": USER_PHONE_E164}, verified())


@pytest.mark.parametrize("tool", PUBLIC_TOOLS)
def test_gate_allows_public_tools_for_a_guest(access_wired, tool):
    gate = require_module("access.tool_gate")
    guest, _ = _contexts()
    gate.authorize_tool(tool, {"query": "roaming"}, guest)


def test_bind_arguments_forces_the_number_from_the_session(access_wired):
    gate = require_module("access.tool_gate")
    _, verified = _contexts()
    ctx = verified()
    forged = {"telephone_no": "+94117654321"}
    try:
        bound = gate.bind_arguments("show_gb_balance", forged, ctx)
    except TypeError:
        try:
            bound = gate.bind_arguments(forged, ctx)
        except TypeError:
            pytest.skip("bind_arguments has a signature this test does not know")
    assert bound["telephone_no"] == ctx.phone_e164


# The same gate in front of the real hub client, against the local mock hub


@pytest.fixture(scope="module")
def mock_hub():
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    mock = require_module("mcp_client.mock_hub", "mcp_client.mock_hub not delivered yet")
    hub = mock.MockHub(mode="streamable", api_key="e2e-hub-key-SECRET-0123456789")
    hub.start()
    yield hub
    hub.stop()


@pytest.fixture
def hub_client(mock_hub, access_wired):
    from mcp_client import client as hub_module

    if "access" not in inspect.signature(hub_module.HubClient.call_tool).parameters:
        pytest.skip("HubClient.call_tool(access=...) not wired yet (lead integration pending)")
    mock_hub.reset_counters()
    hub = hub_module.HubClient(mock_hub.url, "X-API-Key", mock_hub.api_key, 2.0, 0, 300.0)
    hub_module.set_hub(hub)
    yield hub
    hub_module.set_hub(None)


def test_hub_client_refuses_a_customer_tool_for_a_guest_before_any_network_call(hub_client, mock_hub):
    guest, _ = _contexts()
    with pytest.raises(_denied_exception()):
        asyncio.run(hub_client.call_tool("show_gb_balance", {"telephone_no": USER_PHONE_E164}, access=guest))
    assert hub_client.attempts == 0
    assert mock_hub.tool_calls == []


def test_hub_client_refuses_another_number_for_a_verified_user(hub_client, mock_hub):
    _, verified = _contexts()
    with pytest.raises(_denied_exception()):
        asyncio.run(
            hub_client.call_tool("show_gb_balance", {"telephone_no": "+94117654321"}, access=verified())
        )
    assert hub_client.attempts == 0
    assert mock_hub.tool_calls == []


def test_hub_client_never_lets_a_guest_run_a_mutating_tool(hub_client, mock_hub):
    guest, _ = _contexts()
    with pytest.raises(_denied_exception()):
        asyncio.run(hub_client.call_tool("purchase_extra_gb", {"telephone_no": USER_PHONE_E164}, access=guest))
    assert mock_hub.tool_calls == []


def test_hub_client_lets_a_public_tool_through_for_a_guest(hub_client, mock_hub):
    guest, _ = _contexts()
    text = asyncio.run(hub_client.call_tool("search_knowledgebase_tool", {"query": "roaming"}, access=guest))
    assert text
    assert [name for name, _ in mock_hub.tool_calls] == ["search_knowledgebase_tool"]
