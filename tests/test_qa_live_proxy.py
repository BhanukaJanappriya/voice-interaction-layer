"""QA: the /ws/live Gemini Live proxy through the real FastAPI app.

A scripted fake Live server (backend/gemini_live/fake_server.py) stands in for
Google. It is injected by replacing gemini_live.ws.GeminiLiveSession with a
subclass whose client factory is the fake, so the real server route, the real
access context, the real gateway and the local mock hub are all in play. Nothing
touches the network.

Covered:
  a. a guest session declares one tool; forged customer tool calls are refused
     and never reach the hub;
  b. a verified customer sees customer tools; a forged purchase is refused while
     changes are off, and with changes on it needs a confirmation and the hub is
     only called after /confirm accept;
  c. no key, a billing failure (at connect and mid session), a safety block;
  d. voice name and language in the system instruction, for en, si, ta and both genders;
  e. the handover context in the instruction, trimmed to 800 characters;
  f. the API key never reaches the page.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP SDK is not installed")
pytest.importorskip("google.genai", reason="google-genai is not installed")

import config  # noqa: E402
from gemini_live import fake_server as fake  # noqa: E402
from gemini_live import ws as gemini_live_ws  # noqa: E402
from test_e2e_hub_only import (  # noqa: E402,F401  (the fixtures are used by name)
    KB,
    PAYLOADS,
    env,
    hub_names,
    llm_env,
    mock_hub,
    use_payloads,
    verified_client,
)

GEMINI_KEY = "qa-live-proxy-key-SECRET-31d0c8e2"
AUDIO = b"\x00\x00" * 160
CUSTOMER_TOOLS = {"show_service_detail", "show_gb_balance", "show_bill_history", "show_fault_list"}
STATE_TOOLS = {"purchase_extra_gb", "add_vas_data_bundle", "subscribe_lte_prepaid_package", "log_fault"}


# Fixtures and helpers


@pytest.fixture
def live(env, monkeypatch):
    """env (mock hub, gateway, confirmations) plus Gemini Live pointed at a fake."""
    import voice_router
    from voice_router import VoiceRouter

    monkeypatch.setattr(config, "VOICE_ROUTER_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "gemini-3.8-live")
    # Customer tools are only declared to Gemini when the owner allows customer data in the cloud;
    # the tests of the default (false) set it themselves.
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", True)
    router = VoiceRouter(breaker_seconds=60)
    voice_router.reset_router()
    monkeypatch.setattr(voice_router, "_router", router)

    class Live:
        pass

    state = Live()
    state.env = env
    state.router = router
    state.server = None

    def inject(scripts=None):
        server = fake.FakeLiveServer(scripts or [])
        real = gemini_live_ws.GeminiLiveSession

        class Injected(real):
            def __init__(self, tool_executor=None, client_factory=None, **kwargs):
                super().__init__(tool_executor, client_factory=server.factory(), **kwargs)

        monkeypatch.setattr(gemini_live_ws, "GeminiLiveSession", Injected)
        state.server = server
        return server

    state.inject = inject
    yield state
    voice_router.reset_router()


def _decode(message):
    if message["type"] == "websocket.close":
        return None
    if message.get("text") is not None:
        return json.loads(message["text"])
    return ("bytes", message.get("bytes"))


def drive(client, start, *, wait_for=("agent_response", "live_failed"), send_audio=True):
    """Runs one /ws/live conversation; returns every JSON message and every binary frame."""
    payload = {"type": "start_session", "language_hint": "en", "voice_style": "female_natural", "greet": False}
    payload.update(start or {})
    # TestClient does not send its cookie jar on a websocket handshake, so the verified
    # token goes in the start message, the way the page sends it.
    from auth.dependencies import USER_COOKIE

    token = client.cookies.get(USER_COOKIE)
    if token and "session_token" not in payload:
        payload["session_token"] = token
    texts, frames = [], []

    def take(ws):
        message = _decode(ws.receive())
        if message is None:
            return False
        if isinstance(message, tuple):
            frames.append(message[1])
        else:
            texts.append(message)
        return message

    with client.websocket_connect("/ws/live") as ws:
        ws.send_text(json.dumps(payload))
        for _ in range(60):
            message = take(ws)
            if message is False:
                return texts, frames
            if isinstance(message, dict) and message.get("type") in ("live_ready", "live_failed"):
                break
        if texts and texts[-1].get("type") == "live_ready" and send_audio:
            ws.send_bytes(AUDIO)
            for _ in range(200):
                message = take(ws)
                if message is False:
                    return texts, frames
                if isinstance(message, dict) and message.get("type") in wait_for:
                    break
        ws.send_text(json.dumps({"type": "end_session"}))
        for _ in range(100):
            if take(ws) is False:
                break
    return texts, frames


def tool_script(calls, results):
    """Feeds the given (id, name, args) tool calls after the first audio chunk and
    records the function responses; once all are answered the model finishes its turn."""

    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(fake.msg_tool_call(*calls))

        def on_tool(sess, items):
            results.extend(items)
            if len(results) >= len(calls):
                sess.feed(fake.msg_output_text("done", True), fake.msg_turn_complete())

        session.on_audio, session.on_tool_response = on_audio, on_tool

    return script


def declared(server, index=0):
    config_ = server.sessions[index].config
    return [d.name for t in (config_.tools or []) for d in (t.function_declarations or [])]


def instruction_of(server, index=0):
    value = server.sessions[index].config.system_instruction
    if isinstance(value, str):
        return value
    return " ".join(getattr(p, "text", "") or "" for p in getattr(value, "parts", []) or [])


def voice_of(server, index=0):
    return server.sessions[index].config.speech_config.voice_config.prebuilt_voice_config.voice_name


def by_name(results):
    return {r.name: r.response for r in results}


def idle_script(session):
    session.on_audio = lambda sess, n: None


# a. guest


FORGED_BY_GUEST = [
    "show_service_detail",
    "show_gb_balance",
    "show_bill_history",
    "show_fault_list",
    "purchase_extra_gb",
    "log_fault",
    "validate_customer_and_send_otp",
    "verify_otp_and_authenticate",
    "get_function_latencies",
    "authenticate_with_token",
]


def test_guest_declares_exactly_the_knowledge_tool(live):
    server = live.inject([idle_script])
    texts, _ = drive(live.env.new_client(), {}, send_audio=False)
    assert texts[0]["type"] == "live_ready"
    assert declared(server) == [KB]


def test_guest_forged_customer_calls_are_refused_and_never_reach_the_hub(live):
    results = []
    calls = [(f"c{i}", name, {"telephone_no": "0112345678"}) for i, name in enumerate(FORGED_BY_GUEST)]
    server = live.inject([tool_script(calls, results)])
    texts, _ = drive(live.env.new_client(), {})
    assert [t["type"] for t in texts if t["type"] == "agent_response"], texts
    answers = by_name(results)
    assert set(answers) == set(FORGED_BY_GUEST)
    for name, response in answers.items():
        assert "error" in response, (name, response)
        assert "result" not in response, (name, response)
    assert live.env.hub.tool_calls == [], live.env.hub.tool_calls
    assert declared(server) == [KB]


def test_guest_may_still_use_the_knowledge_tool(live):
    results = []
    server = live.inject([tool_script([("k1", KB, {"query": "MOBITEL 1598"})], results)])
    drive(live.env.new_client(), {})
    answer = by_name(results)[KB]
    assert "1598" in json.dumps(answer), answer
    assert hub_names(live.env) == [KB]
    assert declared(server) == [KB]


def test_a_guest_cannot_escape_through_a_session_token_for_nobody(live):
    results = []
    calls = [("c1", "show_service_detail", {})]
    live.inject([tool_script(calls, results)])
    drive(live.env.new_client(), {"session_token": "not-a-real-token"})
    assert "error" in by_name(results)["show_service_detail"]
    assert live.env.hub.tool_calls == []


# b. verified customer


def _customer(env):
    use_payloads(env)
    return verified_client(env)


def test_verified_customer_sees_customer_tools_but_not_state_changing_ones_while_changes_are_off(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    client = _customer(live.env)
    server = live.inject([idle_script])
    drive(client, {}, send_audio=False)
    names = set(declared(server))
    assert KB in names
    assert "show_service_detail" in names and "show_gb_balance" in names
    assert not (names & STATE_TOOLS), names & STATE_TOOLS


def test_forged_purchase_is_refused_while_changes_are_off(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    client = _customer(live.env)
    results = []
    calls = [("p1", "purchase_extra_gb", {"package_id": 11}), ("p2", "log_fault", {"description": "x"})]
    live.inject([tool_script(calls, results)])
    texts, _ = drive(client, {})
    answers = by_name(results)
    assert answers["purchase_extra_gb"] == {"error": "unknown_tool"}
    assert answers["log_fault"] == {"error": "unknown_tool"}
    assert not (set(hub_names(live.env)) & STATE_TOOLS)
    assert not [t for t in texts if t["type"] == "confirmation"]
    assert client.get("/confirm/pending").json() == {"confirmation": None}


def test_a_customer_read_tool_runs_against_the_hub(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    client = _customer(live.env)
    results = []
    live.inject([tool_script([("r1", "show_gb_balance", {})], results)])
    drive(client, {})
    assert "Fibre Unlimited 60" in json.dumps(by_name(results)["show_gb_balance"])
    assert "show_gb_balance" in hub_names(live.env)


def test_with_changes_on_a_purchase_needs_confirmation_and_the_hub_waits_for_accept(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    client = _customer(live.env)
    results = []
    server = live.inject([tool_script([("p1", "purchase_extra_gb", {"package_id": 11})], results)])
    texts, _ = drive(client, {})
    assert "purchase_extra_gb" in declared(server)
    answer = by_name(results)["purchase_extra_gb"]
    assert answer["status"] == "needs_confirmation" and answer["say"]
    assert "purchase_extra_gb" not in hub_names(live.env), "the hub must not be called before accept"
    shown = [t for t in texts if t["type"] == "confirmation"]
    assert shown and shown[0]["confirmation"]["tool"] == "purchase_extra_gb"
    token = client.get("/confirm/pending").json()["confirmation"]["id"]
    assert token == shown[0]["confirmation"]["id"]
    # the page view never carries the arguments or the number
    assert "package_id" not in json.dumps(shown[0]) and "112345678" not in json.dumps(shown[0])
    response = client.post(f"/confirm/{token}/accept")
    assert response.status_code == 200 and response.json()["kind"] == "executed", response.text
    bought = [args for name, args in live.env.hub.tool_calls if name == "purchase_extra_gb"]
    assert len(bought) == 1
    assert client.post(f"/confirm/{token}/accept").status_code == 404


def test_cancelling_a_live_proposal_leaves_the_hub_untouched(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", True)
    client = _customer(live.env)
    results = []
    live.inject([tool_script([("p1", "purchase_extra_gb", {"package_id": 11})], results)])
    drive(client, {})
    token = client.get("/confirm/pending").json()["confirmation"]["id"]
    assert client.post(f"/confirm/{token}/cancel").json()["kind"] == "cancelled"
    assert "purchase_extra_gb" not in hub_names(live.env)


def test_a_model_supplied_phone_number_never_reaches_the_hub(live, monkeypatch):
    monkeypatch.setattr(config, "STATE_CHANGING_TOOLS_ENABLED", False)
    client = _customer(live.env)
    results = []
    live.inject([tool_script([("r1", "show_gb_balance", {"telephone_no": "0771111111", "msisdn": "0771111111"})], results)])
    drive(client, {})
    sent = [args for name, args in live.env.hub.tool_calls if name == "show_gb_balance"]
    assert sent, live.env.hub.tool_calls
    assert "0771111111" not in json.dumps(sent)


def test_by_default_a_verified_customer_is_not_sent_to_gemini(live, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False)
    client = _customer(live.env)
    server = live.inject([idle_script])
    before = live.router.status()
    texts, _ = drive(client, {}, send_audio=False)
    assert texts == [{"type": "live_failed", "reason": "customer_data_local_only"}]
    assert server.connect_attempts == 0
    assert live.env.hub.tool_calls == [] or not (set(hub_names(live.env)) & CUSTOMER_TOOLS)
    after = live.router.status()
    assert after["tiers"]["gemini_live"] == before["tiers"]["gemini_live"], "the router must not be told"
    assert live.router.pick().value == "gemini_live"


def test_the_tier_endpoint_skips_gemini_for_a_signed_in_customer_only(live, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False)
    client = _customer(live.env)
    assert client.get("/api/voice/tier").json()["tier"] != "gemini_live"
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] == "gemini_live"  # a guest


def test_with_customer_data_allowed_in_the_cloud_the_customer_session_runs(live, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", True)
    client = _customer(live.env)
    server = live.inject([idle_script])
    texts, _ = drive(client, {}, send_audio=False)
    assert texts[0]["type"] == "live_ready"
    assert set(declared(server)) & CUSTOMER_TOOLS
    assert client.get("/api/voice/tier").json()["tier"] == "gemini_live"


def test_a_guest_is_unaffected_by_the_customer_data_rule(live, monkeypatch):
    monkeypatch.setattr(config, "CUSTOMER_DATA_TO_CLOUD_LLM", False)
    server = live.inject([idle_script])
    texts, _ = drive(live.env.new_client(), {}, send_audio=False)
    assert texts[0]["type"] == "live_ready"
    assert declared(server) == [KB]


# c. failures and the router


def test_no_key_is_live_failed_missing_key_and_the_router_hears_it(live, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    server = live.inject([idle_script])
    texts, _ = drive(live.env.new_client(), {}, send_audio=False)
    assert texts == [{"type": "live_failed", "reason": "missing_key"}]
    assert server.connect_attempts == 0
    assert live.router.status()["tiers"]["gemini_live"]["last_error"] == "missing_key"
    tier = live.env.new_client().get("/api/voice/tier").json()["tier"]
    assert tier != "gemini_live"


def test_a_billing_failure_at_connect_trips_the_tier(live):
    server = live.inject([])
    server.connect_errors = [fake.closed_error(1011, "Your prepayment credits are depleted")]
    texts, _ = drive(live.env.new_client(), {}, send_audio=False)
    assert texts == [{"type": "live_failed", "reason": "billing_or_quota"}]
    status = live.router.status()["tiers"]["gemini_live"]
    assert status["state"] == "tripped" and status["last_error"] == "billing_or_quota"
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] != "gemini_live"


def test_a_billing_failure_mid_session_is_reported_to_the_page_and_the_router(live):
    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.close_remote(1011, "Your prepayment credits are depleted")

        session.on_audio = on_audio

    live.inject([script])
    texts, _ = drive(live.env.new_client(), {}, wait_for=("live_failed",))
    assert {"type": "live_failed", "reason": "billing_or_quota"} in texts
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] != "gemini_live"


@pytest.mark.parametrize(
    "error,reason",
    [
        (fake.closed_error(1008, "API key not valid"), "invalid_key"),
        (fake.closed_error(1011, "internal error"), "outage"),
        (TimeoutError("connect timed out"), "timeout"),
    ],
    ids=["invalid_key", "outage", "timeout"],
)
def test_other_genuine_connect_failures_trip_the_tier(live, error, reason):
    server = live.inject([])
    server.connect_errors = [error]
    texts, _ = drive(live.env.new_client(), {}, send_audio=False)
    assert texts[-1] == {"type": "live_failed", "reason": reason}
    assert live.router.pick().value != "gemini_live"


def test_a_safety_block_turn_does_not_trip_the_tier(live):
    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(
                    fake.msg_input_text("something the filter dislikes", True),
                    fake.msg_turn_complete("GENERATED_CONTENT_SAFETY"),
                )

        session.on_audio = on_audio

    live.inject([script])
    texts, _ = drive(live.env.new_client(), {}, wait_for=("speaking_done",))
    assert not [t for t in texts if t["type"] == "live_failed"]
    assert live.router.pick().value == "gemini_live"
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] == "gemini_live"


def test_a_safety_close_does_not_trip_the_tier(live):
    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.close_remote(1011, "response blocked by safety")

        session.on_audio = on_audio

    live.inject([script])
    texts, _ = drive(live.env.new_client(), {}, wait_for=("live_failed",))
    assert not [t for t in texts if t["type"] == "live_failed"]
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] == "gemini_live"


def test_a_page_that_hangs_up_does_not_trip_the_tier(live):
    live.inject([idle_script])
    drive(live.env.new_client(), {}, send_audio=False)
    assert live.env.new_client().get("/api/voice/tier").json()["tier"] == "gemini_live"


def test_a_conversation_that_worked_closes_an_earlier_trip(live):
    from voice_router import Tier

    live.router.report(Tier.GEMINI_LIVE, 500)
    assert live.router.pick().value != "gemini_live"

    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(fake.msg_input_text("hello", True), fake.msg_output_text("hi", True), fake.msg_turn_complete())

        session.on_audio = on_audio

    live.inject([script])
    drive(live.env.new_client(), {})
    assert live.router.pick().value == "gemini_live"


# d. voice name and language


LANG_WORD = {"en": "English", "si": "Sinhala", "ta": "Tamil"}
OTHER_WORDS = {"en": ("Sinhala", "Tamil"), "si": ("Tamil",), "ta": ("Sinhala",)}


@pytest.mark.parametrize("style", ["female_natural", "male"])
@pytest.mark.parametrize("language", ["en", "si", "ta"])
def test_voice_name_and_language_follow_the_start_message(live, language, style):
    from voicekit import voice_config

    server = live.inject([idle_script])
    drive(live.env.new_client(), {"language_hint": language, "voice_style": style}, send_audio=False)
    expected = voice_config.lookup("gemini_live", language, style)
    assert expected, "voices.yaml has no gemini_live voice"
    assert voice_of(server) == expected[0]
    gender = "male" if style == "male" else "female"
    assert voice_config.gender_of("gemini_live", language, voice_of(server)) == gender
    text = instruction_of(server)
    assert f"Always reply in {LANG_WORD[language]}" in text
    for other in OTHER_WORDS[language]:
        assert f"Always reply in {other}" not in text
    assert server.sessions[0].model == config.GEMINI_LIVE_MODEL


def test_an_unknown_voice_style_and_language_fall_back_safely(live):
    server = live.inject([idle_script])
    drive(live.env.new_client(), {"language_hint": "fr", "voice_style": "robot"}, send_audio=False)
    assert voice_of(server) in ("Kore", "Aoede")
    assert "Always reply in English" in instruction_of(server)


# e. handover context


def test_handover_context_is_in_the_instruction(live):
    server = live.inject([idle_script])
    drive(live.env.new_client(), {"context": "user: what is the Fibre price | assistant: it is 4500 rupees"}, send_audio=False)
    text = instruction_of(server)
    assert "what is the Fibre price" in text and "Do not greet again" in text


def test_handover_context_is_trimmed_to_800_characters(live):
    server = live.inject([idle_script])
    context = "BEGIN-" + "x" * 900 + "-FINISH"
    drive(live.env.new_client(), {"context": context}, send_audio=False)
    text = instruction_of(server)
    assert "BEGIN-" in text and "-FINISH" not in text
    carried = text.split("What was said so far:\n", 1)[1]
    assert len(carried) == 800


def test_no_context_means_no_handover_note(live):
    server = live.inject([idle_script])
    drive(live.env.new_client(), {}, send_audio=False)
    assert "What was said so far" not in instruction_of(server)


def test_a_non_text_context_does_not_break_the_session(live):
    server = live.inject([idle_script])
    texts, _ = drive(live.env.new_client(), {"context": {"a": 1}}, send_audio=False)
    assert texts[0]["type"] == "live_ready"
    assert "Do not greet again" in instruction_of(server)  # str() of the value, still bounded


# f. the key stays on the server


def _all_text(texts, frames):
    return json.dumps(texts, ensure_ascii=False) + " ".join(repr(f) for f in frames)


def test_the_api_key_never_reaches_the_page_in_a_normal_session(live):
    def script(session):
        def on_audio(sess, n):
            if n == 1:
                sess.feed(
                    fake.msg_input_text("hello", True), fake.msg_audio(b"\x01\x00" * 8),
                    fake.msg_output_text("Hi", True), fake.msg_turn_complete(),
                )

        session.on_audio = on_audio

    live.inject([script])
    texts, frames = drive(live.env.new_client(), {}, wait_for=("speaking_done",))
    assert any(t["type"] == "agent_response" for t in texts)
    assert GEMINI_KEY not in _all_text(texts, frames)


@pytest.mark.parametrize(
    "error",
    [
        fake.closed_error(1011, "Your prepayment credits are depleted"),
        fake.closed_error(1008, "API key not valid. key=" + GEMINI_KEY),
        RuntimeError("boom " + GEMINI_KEY),
    ],
    ids=["billing", "key_in_provider_text", "key_in_exception"],
)
def test_the_api_key_never_reaches_the_page_when_a_connect_fails(live, error):
    server = live.inject([])
    server.connect_errors = [error]
    texts, frames = drive(live.env.new_client(), {}, send_audio=False)
    assert texts[-1]["type"] == "live_failed"
    assert GEMINI_KEY not in _all_text(texts, frames)
    assert set(texts[-1]) == {"type", "reason"}


def test_the_api_key_is_not_in_the_health_or_tier_output_after_a_failure(live):
    server = live.inject([])
    server.connect_errors = [fake.closed_error(1008, "API key not valid. key=" + GEMINI_KEY)]
    drive(live.env.new_client(), {}, send_audio=False)
    client = live.env.new_client()
    assert GEMINI_KEY not in client.get("/api/voice/tier").text
    assert GEMINI_KEY not in client.get("/health").text
