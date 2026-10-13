"""End to end: account answers for verified customers through the MCP hub
(plan part 4).

A verified user (user_session cookie whose token carries amr ["otp"], with a
phone number on the user row) asking for the balance, the last bill, the last
payment, the service details or the fault list gets an answer built from the
hub's read only customer tools. The hub here is the local mock hub with
customer_tools=True, the models are the fakes of test_e2e_chain (Gemini and
LiveKit scripted, the local model through the fake Ollama). Nothing uses the
network, Ollama or the real hub.

What is proved, over POST /chat and (where noted) /ws/voice, in English,
Sinhala and Tamil:
  * the matching read only tools are called with the verified number in the
    national form and with no other number, even if the text names one;
  * the local model receives the data in its prompt, Gemini and LiveKit are
    never invoked (unless CUSTOMER_DATA_TO_CLOUD_LLM is true);
  * the reply mode ends with " + SLT account data";
  * guests and PIN only sessions get the canned dial message, no hub call;
  * a number that is not authenticated on the hub gets the canned re-verify
    text, a hub that is down or too slow gets the canned unavailable text,
    and in both cases no model is called;
  * CUSTOMER_TOOLS_ENABLED=false makes no hub call, a general question makes
    no hub call, a request to change something never reaches a tool that
    changes an account (checked in EVERY test at teardown);
  * customer data and secret looking keys do not leak into logs, stdout or
    the model prompt; /health keeps its old keys.

Detection: backend/customer_service must be importable, the mock hub must
accept customer_tools, and the Orchestrator source must mention
customer_service. Until all three hold the tests skip with the reason, then
run by themselves. See the notes at the bottom of the fixtures for the
interfaces that were guessed.
"""

import inspect
import json
import re
import socket

import pytest

import agent
import config
from conftest import BACKEND_DIR, USER_PHONE_E164, make_client, optional_import, require_module, server_source
from test_e2e_access import _voice_turn, assert_canned_blocked_reply, session_token
from test_e2e_chain import (  # noqa: F401  (llm_env is a fixture used below)
    GEMINI_MODE,
    GEMINI_REPLY,
    LIVEKIT_REPLY,
    LOCAL_REPLY,
    llm_env,
)

NATIONAL = "0" + USER_PHONE_E164[3:]  # +94112345678 -> 0112345678
OTHER_NATIONAL = "0777654321"
SUFFIX = " + SLT account data"
HUB_KEY = "e2e-customer-hub-key-SECRET-0123456789"

# Tools that change an account or the login state. The assistant must never
# call any of them (plan 4.2). Checked at the end of every test that uses the hub.
FORBIDDEN_TOOLS = {
    "purchase_extra_gb",
    "add_vas_data_bundle",
    "log_fault",
    "subscribe_lte_prepaid_package",
    "authenticate_with_token",
    "refresh_customer_token",
    "validate_customer_and_send_otp",
    "verify_otp_and_authenticate",
}

_POLICY = json.loads((BACKEND_DIR / "access" / "policy.json").read_text(encoding="utf-8"))
CUSTOMER_TOOL_NAMES = set(_POLICY["customer_tools"])

# topic -> {language: question}, and the tool that must be called for it.
# Every question matches a keyword rule of backend/access/policy.json.
QUESTIONS = {
    "balance": {
        "tool": "show_gb_balance",
        "en": "What is my balance?",
        "si": "මගේ ශේෂය කීයද?",
        "ta": "என் இருப்பு எவ்வளவு?",
    },
    "bill": {
        "tool": "show_bill_history",
        "en": "Show my last bill",
        "si": "මගේ අවසාන බිල්පත පෙන්වන්න",
        "ta": "என் கடைசி பில் எவ்வளவு?",
    },
    "payment": {
        "tool": "show_last_payment",
        "en": "When was my last payment?",
        "si": "මගේ අවසාන ගෙවීම කවදාද?",
        "ta": "என் கடைசி கட்டணம் எப்போது?",
    },
    "service": {
        "tool": "show_service_detail",
        "en": "Show my service details",
        "si": "මගේ සේවාව පිළිබඳ විස්තර පෙන්වන්න",
        "ta": "என் கணக்கு விவரங்களைக் காட்டு",
    },
    "faults": {
        "tool": "show_fault_list",
        "en": "Show my fault list",
        "si": "මගේ දෝෂ ලැයිස්තුව පෙන්වන්න",
        "ta": "என் புகார் பட்டியலைக் காட்டு",
    },
}
TOPIC_CASES = [(topic, language) for topic in QUESTIONS for language in ("en", "si", "ta")]

CHANGE_REQUESTS = {
    "en": ["I want to buy extra GB for my number", "Please activate a data bundle on my line"],
    "si": ["extra gb ganna one"],
    "ta": ["கூடுதல் ஜிபி வாங்க வேண்டும்"],
}
CHANGE_CASES = [(language, text) for language, texts in CHANGE_REQUESTS.items() for text in texts]

NAMING_ANOTHER_NUMBER = {
    "en": "Also check the number 0777654321 and +94 77 765 4321",
    "si": "සහ 0777654321 අංකයත් බලන්න",
    "ta": "மேலும் 0777654321 எண்ணையும் பார்",
}

GENERAL = "Tell me about SLT fibre broadband packages"


# Detection and wiring


@pytest.fixture
def customer_wired():
    """The three conditions of the module docstring. Skips with the reason."""
    require_module("customer_service", "backend/customer_service not delivered yet (Agent S)")
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    mock = require_module("mcp_client.mock_hub", "mcp_client.mock_hub not delivered yet")
    if "customer_tools" not in inspect.signature(mock.MockHub.__init__).parameters:
        pytest.skip("MockHub(customer_tools=True) not delivered yet (Agent S)")
    if "customer_service" not in inspect.getsource(agent.Orchestrator):
        pytest.skip("agent.Orchestrator does not use customer_service yet (lead integration pending)")
    require_module("access.context", "backend/access not delivered yet")
    if "access" not in inspect.signature(agent.Orchestrator.respond_stream).parameters:
        pytest.skip("Orchestrator.respond_stream(access=...) not wired yet (lead integration pending)")
    if "access" not in server_source():
        pytest.skip("server.py does not build an AccessContext from the session yet (lead integration pending)")


@pytest.fixture(scope="module")
def _mock_hub_module():
    require_module("customer_service", "backend/customer_service not delivered yet (Agent S)")
    pytest.importorskip("mcp", reason="the MCP SDK is not installed")
    mock = require_module("mcp_client.mock_hub", "mcp_client.mock_hub not delivered yet")
    if "customer_tools" not in inspect.signature(mock.MockHub.__init__).parameters:
        pytest.skip("MockHub(customer_tools=True) not delivered yet (Agent S)")
    hub = mock.MockHub(mode="streamable", api_key=HUB_KEY, customer_tools=True)
    hub.start()
    yield hub
    hub.stop()


@pytest.fixture
def env(customer_wired, llm_env, _mock_hub_module, monkeypatch):
    """The fake tiers of the chain tests plus a started mock hub that knows
    the verified number, installed as the process hub, with a spy that records
    every tool call the code tried (name, arguments) before the gate."""
    from mcp_client import client as hub_module

    hub = _mock_hub_module
    hub.reset_counters()
    hub.authenticated.clear()
    hub.authenticated.add(NATIONAL)

    monkeypatch.setattr(config, "MCP_HUB_URL", hub.url)
    monkeypatch.setattr(config, "MCP_HUB_API_KEY", HUB_KEY)
    monkeypatch.setattr(config, "MCP_HUB_ENABLED", True)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False, raising=False)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 5.0, raising=False)
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_MAX_CHARS", 6000, raising=False)

    attempted = []
    original = hub_module.HubClient.call_tool_output

    async def spy(self, name, arguments, *args, **kwargs):
        attempted.append((name, dict(arguments)))
        return await original(self, name, arguments, *args, **kwargs)

    monkeypatch.setattr(hub_module.HubClient, "call_tool_output", spy)

    client = hub_module.HubClient(hub.url, "X-API-Key", HUB_KEY, 3.0, 0, 300.0)
    hub_module.set_hub(client)
    _clear_customer_caches()

    llm_env.hub = hub
    llm_env.hub_client = client
    llm_env.attempted = attempted
    llm_env.hub_module = hub_module
    yield llm_env

    hub_module.set_hub(None)
    _clear_customer_caches()
    tried = {name for name, _ in attempted} | {name for name, _ in hub.tool_calls}
    assert not (tried & FORBIDDEN_TOOLS), f"the assistant called a forbidden tool: {sorted(tried & FORBIDDEN_TOOLS)}"


def _clear_customer_caches():
    module = optional_import("customer_service")
    if module is None:
        return
    for owner in (module, optional_import("customer_service.service")):
        if owner is None:
            continue
        for name in ("clear_cache", "reset_cache", "clear_caches", "reset", "_reset"):
            func = getattr(owner, name, None)
            if callable(func):
                try:
                    func()
                except TypeError:
                    pass


@pytest.fixture
def verified_cookie(make_user):
    return session_token(make_user(), amr=["otp"])


# Helpers


def post_chat(env, text, language="en", cookie=None):
    client = env.client()
    try:
        if cookie is not None:
            client.cookies.set("user_session", cookie)
        response = client.post("/chat", json={"text": text, "language": language})
    finally:
        client.close()
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"reply", "mode"}
    return body


def customer_attempts(env):
    return [(name, args) for name, args in env.attempted if name in CUSTOMER_TOOL_NAMES]


def hub_customer_calls(env):
    return [(name, args) for name, args in env.hub.tool_calls if name in CUSTOMER_TOOL_NAMES]


def assert_no_customer_hub_traffic(env):
    assert customer_attempts(env) == [], f"unexpected customer tool attempts: {customer_attempts(env)}"
    assert hub_customer_calls(env) == [], f"the mock hub received customer calls: {hub_customer_calls(env)}"


def local_prompt_text(env):
    """Everything the local model was sent: prompt and system field."""
    parts = []
    for payload in env.local_calls:
        parts.append(str(payload.get("prompt", "")))
        parts.append(str(payload.get("system", "")))
        for message in payload.get("messages", []) or []:
            parts.append(str(message.get("content", "")))
    return "\n".join(parts)


def leaves(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item)
    elif isinstance(value, bool) or value is None:
        return
    else:
        yield str(value)


def distinctive_values(text):
    """Values from a raw tool result that identify customer data: numbers and
    long words, not keys and not common words like "success"."""
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    found = list(leaves(parsed)) if parsed is not None else re.findall(r"[A-Za-z0-9.,:/-]{3,}", text)
    strong = [v for v in found if len(v) >= 3 and (any(c.isdigit() for c in v) or len(v) >= 8)]
    return strong or [v for v in found if len(v) >= 3]


def raw_results(env, tool_names):
    """What the mock hub itself returns for the tools (own number, trusted
    path), then the counters are cleared again so the test counts only the
    assistant's calls."""
    from mcp_client import tools as hub_tools

    texts = []
    for name in tool_names:
        try:
            output = hub_tools.call_tool_sync(name, {"telephone_no": NATIONAL})
        except Exception as exc:  # the mock may not offer every tool
            pytest.fail(f"the mock hub could not serve {name} with customer_tools=True: {exc!r}")
        texts.append(output.text or json.dumps(output.structured))
    env.attempted.clear()
    env.hub.reset_counters()
    return texts


def expect_translation(reply, key, language):
    """The canned text for a key, if i18n has it; the reply must contain it.
    Without i18n text the reply must at least be a non empty non model reply."""
    i18n = optional_import("i18n")
    # The dial numbers are filled into the text, so compare the filled text.
    value = (
        i18n.t(key, language, hotline="1212", sms="0113121212", prepaid="1222")
        if i18n is not None and hasattr(i18n, "t")
        else None
    )
    assert reply.strip()
    assert reply not in (GEMINI_REPLY, LIVEKIT_REPLY, LOCAL_REPLY)
    if value and value != key:
        assert value in reply, f"expected the {key} text in the {language} reply: {reply!r}"
    else:
        pytest.skip(f"i18n key {key} is not delivered yet; the reply was not compared")


# The verified path


@pytest.mark.parametrize("topic,language", TOPIC_CASES)
def test_verified_question_calls_the_matching_tool_and_gives_the_local_model_the_data(
    env, verified_cookie, topic, language
):
    tool = QUESTIONS[topic]["tool"]
    expected_values = distinctive_values(raw_results(env, [tool])[0])

    body = post_chat(env, QUESTIONS[topic][language], language, verified_cookie)

    attempted_names = {name for name, _ in customer_attempts(env)}
    hub_names = {name for name, _ in hub_customer_calls(env)}
    assert tool in attempted_names, f"{tool} was not requested; got {sorted(attempted_names)}"
    assert tool in hub_names, f"the mock hub never received {tool}; got {sorted(hub_names)}"
    for name, args in hub_customer_calls(env):
        assert args.get("telephone_no") == NATIONAL, f"{name} got {args}"

    assert len(env.local_calls) == 1, "the local model answers a turn with customer data"
    assert env.calls["gemini"] == [] and env.calls["livekit"] == [], "customer data must stay off the cloud tiers"
    prompt = local_prompt_text(env)
    assert any(value in prompt for value in expected_values), (
        f"none of {expected_values[:6]} reached the local prompt"
    )
    assert body["reply"] == LOCAL_REPLY
    assert body["mode"].endswith(SUFFIX), body["mode"]


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_a_number_named_in_the_text_is_never_sent_to_the_hub(env, verified_cookie, language):
    text = QUESTIONS["balance"][language] + " " + NAMING_ANOTHER_NUMBER[language]
    body = post_chat(env, text, language, verified_cookie)
    assert body["mode"].endswith(SUFFIX), body
    assert customer_attempts(env), "the question should still be answered for the verified number"
    blob = json.dumps(env.attempted) + json.dumps(env.hub.tool_calls)
    assert "777654321" not in blob and "77 765 4321" not in blob, blob
    for name, args in customer_attempts(env):
        assert args.get("telephone_no") == NATIONAL, f"{name} got {args}"
    for name, args in hub_customer_calls(env):
        assert args.get("telephone_no") == NATIONAL, f"{name} got {args}"


def test_tool_arguments_use_the_national_form_not_e164(env, verified_cookie):
    post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    calls = hub_customer_calls(env)
    assert calls
    for _, args in calls:
        assert args["telephone_no"] == NATIONAL
        assert not str(args["telephone_no"]).startswith("+")


def test_cloud_flag_lets_the_first_tier_see_the_data(env, verified_cookie, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", True)
    env.rebuild()
    body = post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    assert len(env.calls["gemini"]) == 1, "with the flag on the first tier of the chain answers"
    assert env.local_calls == []
    assert body["reply"] == GEMINI_REPLY
    assert body["mode"] == GEMINI_MODE + SUFFIX
    assert hub_customer_calls(env), "the hub was still asked"


def test_the_flag_off_is_the_default_and_survives_a_cloud_failure(env, verified_cookie):
    """If the local model were down the user gets the local tier's own text,
    never a hosted model's answer. Here the cloud tiers would have failed
    anyway, so the point is that they are not even tried."""
    env.behaviour["gemini"] = ("fail", 503)
    post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    assert env.calls["gemini"] == [] and env.calls["livekit"] == []


# Guests and PIN only sessions


@pytest.mark.parametrize("topic,language", TOPIC_CASES)
def test_guest_gets_the_dial_message_and_the_hub_gets_nothing(env, topic, language):
    body = post_chat(env, QUESTIONS[topic][language], language)
    assert_canned_blocked_reply(body["reply"], language)
    env.assert_no_model_called()
    assert env.hub.tool_calls == [] and env.attempted == []


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_pin_only_session_gets_the_dial_message_and_the_hub_gets_nothing(env, make_user, language):
    pin_only = session_token(make_user(), amr=None)
    body = post_chat(env, QUESTIONS["bill"][language], language, pin_only)
    assert_canned_blocked_reply(body["reply"], language)
    env.assert_no_model_called()
    assert env.hub.tool_calls == [] and env.attempted == []


def test_a_session_without_otp_in_amr_gets_nothing_from_the_hub(env, make_user):
    weak = session_token(make_user(), amr=["pwd"])
    body = post_chat(env, QUESTIONS["balance"]["en"], "en", weak)
    assert_canned_blocked_reply(body["reply"], "en")
    env.assert_no_model_called()
    assert env.hub.tool_calls == [] and env.attempted == []


# The hub says the customer is not authenticated


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_unauthenticated_on_the_hub_gets_the_reverify_text_and_no_model(env, verified_cookie, language):
    env.hub.authenticated.discard(NATIONAL)
    body = post_chat(env, QUESTIONS["balance"][language], language, verified_cookie)
    env.assert_no_model_called()
    assert hub_customer_calls(env), "the hub had to be asked to learn that"
    for _, args in hub_customer_calls(env):
        assert args.get("telephone_no") == NATIONAL
    expect_translation(body["reply"], "customer.reauth_needed", language)


# The hub is down or too slow


@pytest.fixture
def dead_hub(env, monkeypatch):
    """Replaces the process hub with one that cannot answer: a port with no
    listener (connection refused) or a listener that never speaks (timeout)."""
    made = []

    def _use(kind):
        if kind == "refused":
            probe = socket.socket()
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
        else:
            listener = socket.socket()
            listener.bind(("127.0.0.1", 0))
            listener.listen(8)
            made.append(listener)
            port = listener.getsockname()[1]
        client = env.hub_module.HubClient(f"http://127.0.0.1:{port}", "X-API-Key", HUB_KEY, 0.6, 0, 300.0)
        env.hub_module.set_hub(client)
        monkeypatch.setattr(config, "CUSTOMER_TOOLS_BUDGET_SECONDS", 1.5)
        _clear_customer_caches()

    yield _use
    for listener in made:
        listener.close()


@pytest.mark.parametrize("kind", ["refused", "timeout"])
@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_hub_down_or_timing_out_gets_the_unavailable_text_and_no_model(
    env, dead_hub, verified_cookie, kind, language
):
    dead_hub(kind)
    body = post_chat(env, QUESTIONS["balance"][language], language, verified_cookie)
    env.assert_no_model_called()
    assert env.hub.tool_calls == [], "the real mock was replaced by a dead hub"
    expect_translation(body["reply"], "customer.unavailable", language)


# Switches and scope


def test_customer_tools_disabled_makes_no_hub_call_and_uses_the_normal_flow(env, verified_cookie, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_TOOLS_ENABLED", False)
    body = post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    assert_no_customer_hub_traffic(env)
    assert body["reply"] == GEMINI_REPLY, "the same as a verified user without the feature"
    assert body["mode"] == GEMINI_MODE
    assert env.local_calls == []


def test_a_general_question_from_a_verified_user_makes_no_customer_call(env, verified_cookie):
    body = post_chat(env, GENERAL, "en", verified_cookie)
    assert_no_customer_hub_traffic(env)
    assert body["reply"] == GEMINI_REPLY
    assert not body["mode"].endswith(SUFFIX)
    assert len(env.calls["gemini"]) == 1


@pytest.mark.parametrize("language,text", CHANGE_CASES)
def test_a_request_to_change_something_never_calls_a_changing_tool(env, verified_cookie, language, text):
    body = post_chat(env, text, language, verified_cookie)
    assert body["reply"].strip()
    # The teardown of `env` asserts that none of FORBIDDEN_TOOLS was attempted
    # or received; here the mock is also asked directly.
    names = [name for name, _ in env.hub.tool_calls]
    for forbidden in ("purchase_extra_gb", "add_vas_data_bundle", "log_fault", "subscribe_lte_prepaid_package"):
        assert forbidden not in names


def test_asking_to_log_a_fault_reads_the_fault_list_at_most(env, verified_cookie):
    post_chat(env, "Please log a fault for my line, it is not working", "en", verified_cookie)
    names = {name for name, _ in env.attempted}
    assert "log_fault" not in names
    read_only = {"show_fault_list", "show_service_detail", "show_lte_subscriber_status"}
    assert {n for n in names if n in CUSTOMER_TOOL_NAMES} <= read_only, names


# Privacy


def test_secret_looking_keys_from_the_hub_never_reach_the_model(env, verified_cookie, monkeypatch):
    secrets = {
        "access_token": "SECRET-ACCESS-TOKEN-aaa111",
        "refresh_token": "SECRET-REFRESH-TOKEN-bbb222",
        "password": "SECRET-PASSWORD-ccc333",
        "otp": "SECRET-OTP-ddd444",
    }
    original = env.hub_module.HubClient.call_tool_output  # the spy of `env`
    ToolOutput = env.hub_module.ToolOutput

    async def leaky(self, name, arguments, *args, **kwargs):
        out = await original(self, name, arguments, *args, **kwargs)
        try:
            body = json.loads(out.text)
        except ValueError:
            body = {"data": out.text}
        if not isinstance(body, dict):
            body = {"data": body}
        body = {**body, **secrets, "customer": {"refresh_token": secrets["refresh_token"]}}
        return ToolOutput(text=json.dumps(body), structured=body)

    monkeypatch.setattr(env.hub_module.HubClient, "call_tool_output", leaky)
    body = post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    assert body["mode"].endswith(SUFFIX), "the data itself should still reach the model"
    prompt = local_prompt_text(env)
    for key, value in secrets.items():
        assert value not in prompt, f"the value of {key} reached the model prompt"
    for cloud in ("gemini", "livekit"):
        assert not any(value in json.dumps(env.calls[cloud]) for value in secrets.values())
    assert not any(value in body["reply"] for value in secrets.values())


@pytest.mark.parametrize("topic", ["balance", "bill", "payment", "service", "faults"])
def test_customer_data_is_not_written_to_stdout_or_logs(env, verified_cookie, capfd, caplog, topic):
    import logging

    tool = QUESTIONS[topic]["tool"]
    values = distinctive_values(raw_results(env, [tool])[0])
    capfd.readouterr()
    caplog.set_level(logging.DEBUG)
    post_chat(env, QUESTIONS[topic]["en"], "en", verified_cookie)
    out = capfd.readouterr()
    haystack = out.out + out.err + "\n".join(record.getMessage() for record in caplog.records)
    for value in values:
        assert value not in haystack, f"customer data {value!r} appeared in stdout, stderr or logs"


def test_the_verified_number_is_not_written_to_stdout_or_logs(env, verified_cookie, capfd, caplog):
    import logging

    capfd.readouterr()
    caplog.set_level(logging.DEBUG)
    post_chat(env, QUESTIONS["balance"]["en"], "en", verified_cookie)
    out = capfd.readouterr()
    haystack = out.out + out.err + "\n".join(record.getMessage() for record in caplog.records)
    assert NATIONAL not in haystack and USER_PHONE_E164 not in haystack and NATIONAL[1:] not in haystack


# /health


def test_health_keeps_the_old_keys_and_leaks_nothing(env):
    client = env.client()
    try:
        response = client.get("/health")
    finally:
        client.close()
    assert response.status_code == 200, response.text
    report = response.json()
    for key in (
        "status",
        "cloud_llm_configured",
        "cloud_llm_provider",
        "cloud_llm_model",
        "cloud_llm_last_error",
        "cloud_speech_configured",
        "supported_languages",
    ):
        assert key in report, f"/health lost the existing key {key}"
    text = json.dumps(report)
    assert HUB_KEY not in text and NATIONAL not in text


# /ws/voice


def test_verified_spoken_question_gets_the_local_models_answer_with_the_data(
    env, fake_speech, verified_cookie
):
    tool = QUESTIONS["balance"]["tool"]
    expected_values = distinctive_values(raw_results(env, [tool])[0])
    client = env.client()
    try:
        response, _ = _voice_turn(
            client,
            fake_speech,
            QUESTIONS["balance"]["en"],
            "en",
            headers={"cookie": f"user_session={verified_cookie}"},
        )
    finally:
        client.close()
    assert LOCAL_REPLY in response["text"]
    assert LOCAL_REPLY in " ".join(fake_speech.spoken)
    assert env.calls["gemini"] == [] and env.calls["livekit"] == []
    assert len(env.local_calls) == 1
    assert any(value in local_prompt_text(env) for value in expected_values)
    for _, args in hub_customer_calls(env):
        assert args.get("telephone_no") == NATIONAL


@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_guest_spoken_question_gets_the_dial_message_and_the_hub_gets_nothing(env, fake_speech, language):
    client = env.client()
    try:
        response, _ = _voice_turn(client, fake_speech, QUESTIONS["bill"][language], language)
    finally:
        client.close()
    assert_canned_blocked_reply(response["text"], language)
    assert "1212" in " ".join(fake_speech.spoken)
    env.assert_no_model_called()
    assert env.hub.tool_calls == [] and env.attempted == []


def test_spoken_question_with_the_hub_down_is_answered_with_the_canned_text(
    env, fake_speech, verified_cookie, dead_hub
):
    dead_hub("refused")
    client = env.client()
    try:
        response, _ = _voice_turn(
            client,
            fake_speech,
            QUESTIONS["balance"]["en"],
            "en",
            headers={"cookie": f"user_session={verified_cookie}"},
        )
    finally:
        client.close()
    env.assert_no_model_called()
    assert response["text"].strip()
    assert response["text"] not in (GEMINI_REPLY, LIVEKIT_REPLY, LOCAL_REPLY)
    assert fake_speech.spoken, "the canned text is spoken"
