"""The LLM fallback chain (backend/llm_chain), with fakes only.

No network, no Ollama and no cloud SDK: the real tier classes are driven
through injected fake agents, so the status code and exception handling is the
code that runs in production.
"""

import pytest

import conftest  # noqa: F401  (pins the environment and puts backend/ on sys.path)

import agent
import config
from llm_chain import APOLOGY, Breaker, LlmChain, SafetyBlocked, TierUnavailable, Turn, reason_from_exception
from llm_chain.tiers import GeminiTier, LiveKitTier, LocalTier

SAFETY_TEXT = "safety notice"


class HttpError(Exception):
    """Stands in for an SDK error carrying an HTTP status."""

    def __init__(self, status_code=None, code=None, message="secret-key-abc user said hello"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SlowError(Exception):
    pass


class FakeAgent:
    """respond_stream yields scripted pieces; a piece that is an exception is
    raised at that point, the way the real agents raise from inside the
    stream. A cloud failure is wrapped as CloudUnavailableError from the
    original, exactly like GeminiAgent and LiveKitInferenceAgent do."""

    def __init__(self, script, wrap=True, calls=None):
        self.script = list(script)
        self.wrap = wrap
        self.calls = calls

    def respond_stream(self, *args):
        if self.calls is not None:
            self.calls.append(args)
        for item in self.script:
            if isinstance(item, BaseException):
                if self.wrap and not isinstance(item, SafetyBlocked):
                    raise agent.CloudUnavailableError("wrapped call failed") from item
                raise item
            yield item


class Scenario:
    """Holds each tier's script and counts calls, then builds a chain."""

    def __init__(self, monkeypatch, gemini=("G1", "G2"), livekit=("L1", "L2"), local=("local reply",)):
        monkeypatch.setattr(config, "GEMINI_API_KEY", "test-gemini-key")
        monkeypatch.setattr(config, "LIVEKIT_URL", "wss://example.invalid")
        monkeypatch.setattr(config, "LIVEKIT_API_KEY", "k")
        monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "s")
        self.scripts = {"gemini": list(gemini), "livekit": list(livekit), "local": list(local)}
        self.calls = {"gemini": [], "livekit": [], "local": []}
        self.now = [1000.0]
        self.chain = LlmChain(
            order=["gemini", "livekit", "local"],
            tiers={
                "gemini": GeminiTier(lambda prompt: FakeAgent(self.scripts["gemini"], calls=self.calls["gemini"])),
                "livekit": LiveKitTier(lambda prompt: FakeAgent(self.scripts["livekit"], calls=self.calls["livekit"])),
                "local": LocalTier(lambda turn: FakeAgent(self.scripts["local"], wrap=False, calls=self.calls["local"])),
            },
            breaker_seconds=300,
            clock=lambda: self.now[0],
            safety_message=lambda language: SAFETY_TEXT,
        )

    def run(self, text="hello"):
        return "".join(self.chain.stream(Turn(text=text)))

    def hits(self):
        return {name: len(calls) for name, calls in self.calls.items()}


@pytest.fixture
def scenario(monkeypatch):
    def make(**kwargs):
        return Scenario(monkeypatch, **kwargs)

    return make


# Order and happy path


def test_first_tier_answers_and_the_others_are_not_called(scenario):
    s = scenario()
    assert s.run() == "G1G2"
    assert s.chain.last_mode == "cloud (gemini)"
    assert s.hits() == {"gemini": 1, "livekit": 0, "local": 0}


def test_order_comes_from_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_CHAIN", ["local", "gemini", "bogus", "local"])
    chain = LlmChain()
    assert chain.order == ["local", "gemini"]
    monkeypatch.setattr(config, "LLM_CHAIN", ["livekit", "local"])
    assert LlmChain().order == ["livekit", "local"]


def test_default_order_is_gemini_livekit_local(monkeypatch):
    # conftest pins LLM_CHAIN empty for the older tests; the shipped default is
    # what config.py parses when the variable is not set at all.
    import importlib

    monkeypatch.delenv("LLM_CHAIN", raising=False)
    fresh = importlib.reload(config)
    try:
        assert fresh.LLM_CHAIN == ["gemini", "livekit", "local"]
    finally:
        monkeypatch.setenv("LLM_CHAIN", "")
        importlib.reload(config)


def test_custom_order_is_followed(monkeypatch):
    s = Scenario(monkeypatch)
    s.chain.order = ["livekit", "gemini", "local"]
    assert s.run() == "L1L2"
    assert s.chain.last_mode == f"livekit inference ({config.LIVEKIT_LLM_MODEL})"


def test_mode_strings(scenario):
    s = scenario()
    assert s.chain.tiers["gemini"].label() == "cloud (gemini)"
    assert s.chain.tiers["livekit"].label() == f"livekit inference ({config.LIVEKIT_LLM_MODEL})"
    assert s.chain.tiers["local"].label() == "local single agent (Llama via Ollama)"


# Each tier failing in turn


def test_gemini_failure_moves_the_whole_turn_to_livekit(scenario):
    s = scenario(gemini=[HttpError(500)])
    assert s.run() == "L1L2"
    assert s.chain.last_mode.startswith("livekit inference")
    assert s.hits() == {"gemini": 1, "livekit": 1, "local": 0}


def test_gemini_and_livekit_failing_reach_local(scenario):
    s = scenario(gemini=[HttpError(503)], livekit=[HttpError(500)])
    assert s.run() == "local reply"
    assert s.chain.last_mode == "local single agent (Llama via Ollama)"


def test_livekit_failing_alone_is_not_reached_when_gemini_works(scenario):
    s = scenario(livekit=[HttpError(500)])
    assert s.run() == "G1G2"
    assert s.hits()["livekit"] == 0


def test_every_tier_failing_gives_a_spoken_notice_not_an_exception(scenario):
    s = scenario(gemini=[HttpError(500)], livekit=[HttpError(500)], local=[ConnectionError()])
    reply = s.run()
    assert "Sorry" in reply
    assert s.chain.last_mode == "unavailable"


def test_the_retrieved_context_and_language_reach_the_local_tier(monkeypatch):
    seen = {}

    def factory(turn):
        seen["turn"] = turn
        return FakeAgent(["ok"], wrap=False)

    tier = LocalTier(factory)
    chain = LlmChain(order=["local"], tiers={"local": tier})
    list(chain.stream(Turn(text="q", user_name="Nimal", language="si", tool_context="ctx")))
    assert (seen["turn"].user_name, seen["turn"].language, seen["turn"].tool_context) == ("Nimal", "si", "ctx")


# Why a tier is unavailable


@pytest.mark.parametrize(
    "error, reason",
    [
        (HttpError(401), "auth"),
        (HttpError(403), "auth"),
        (HttpError(code=401), "auth"),
        (HttpError(402), "quota"),
        (HttpError(429), "quota"),
        (HttpError(code=429), "quota"),
        (HttpError(404), "not_found"),
        (HttpError(500), "outage"),
        (HttpError(502), "outage"),
        (HttpError(503), "outage"),
        (TimeoutError(), "timeout"),
        (SlowError(), "other"),
        (ConnectionError(), "network"),
        (OSError("x"), "other"),
    ],
)
def test_reason_classification(error, reason):
    assert reason_from_exception(error) == reason
    assert reason_from_exception(agent.CloudUnavailableError("wrapped")) == "other"
    wrapped = agent.CloudUnavailableError("wrapped")
    wrapped.__cause__ = error
    assert reason_from_exception(wrapped) == reason


def test_named_sdk_timeout_and_connection_types_are_recognised():
    class APITimeoutError(Exception):
        pass

    class APIConnectionError(Exception):
        pass

    assert reason_from_exception(APITimeoutError()) == "timeout"
    assert reason_from_exception(APIConnectionError()) == "network"


def test_a_status_code_wins_over_the_exception_type():
    class ConnectTimeout(Exception):
        status_code = 429

    assert reason_from_exception(ConnectTimeout()) == "quota"


def test_tier_unavailable_is_a_cloud_unavailable_error():
    assert issubclass(TierUnavailable, agent.CloudUnavailableError)
    assert TierUnavailable("quota").reason == "quota"
    assert TierUnavailable("nonsense").reason == "other"


@pytest.mark.parametrize(
    "error, reason",
    [
        (HttpError(401), "auth"),
        (HttpError(402), "quota"),
        (HttpError(429), "quota"),
        (HttpError(404), "not_found"),
        (HttpError(503), "outage"),
        (TimeoutError(), "timeout"),
        (ConnectionError(), "network"),
    ],
)
def test_each_failure_kind_falls_back_and_is_reported(scenario, error, reason):
    s = scenario(gemini=[error])
    assert s.run() == "L1L2"
    assert s.chain.breakers["gemini"].reason == reason
    gemini = s.chain.status()["tiers"]["gemini"]
    assert gemini["state"] == "tripped"
    assert gemini["last_error"]


def test_expired_key_on_livekit_falls_to_local(scenario):
    s = scenario(gemini=[HttpError(429)], livekit=[HttpError(401)])
    assert s.run() == "local reply"
    assert s.chain.breakers["livekit"].reason == "auth"


def test_missing_key_skips_the_tier_without_calling_it(monkeypatch):
    s = Scenario(monkeypatch)
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert s.run() == "L1L2"
    assert s.hits()["gemini"] == 0
    info = s.chain.status()["tiers"]["gemini"]
    assert (info["configured"], info["state"]) == (False, "not_configured")
    assert info["last_error"]


def test_livekit_needs_all_three_settings(monkeypatch):
    s = Scenario(monkeypatch, gemini=[HttpError(500)])
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", "")
    assert s.run() == "local reply"
    assert s.hits()["livekit"] == 0


def test_an_empty_reply_from_a_cloud_tier_counts_as_a_failure(scenario):
    s = scenario(gemini=["", ""])
    assert s.run() == "L1L2"
    assert s.chain.breakers["gemini"].reason == "other"


# Messages never carry secrets or user text


def test_error_text_and_logs_do_not_leak_key_or_user_text(scenario, capsys):
    s = scenario(gemini=[HttpError(401, message="Bearer secret-key-abc says: my phone is 0771234567")])
    s.run(text="my phone is 0771234567")
    out = capsys.readouterr().out
    shown = repr(s.chain.status())
    for text in (out, shown):
        assert "secret-key-abc" not in text
        assert "0771234567" not in text
        assert "test-gemini-key" not in text
    try:
        raise agent.CloudUnavailableError("x") from HttpError(401, message="secret-key-abc")
    except agent.CloudUnavailableError as exc:
        from llm_chain.errors import unavailable_from_exception

        assert "secret-key-abc" not in str(unavailable_from_exception(exc))


# Safety blocks are answers


def test_a_safety_block_does_not_fall_back(scenario):
    s = scenario(gemini=[SafetyBlocked()])
    assert s.run() == SAFETY_TEXT
    assert s.hits() == {"gemini": 1, "livekit": 0, "local": 0}
    assert s.chain.breakers["gemini"].open() is False
    assert s.chain.status()["tiers"]["gemini"]["state"] == "ready"


def test_a_livekit_content_filter_does_not_fall_back_to_local(scenario):
    s = scenario(gemini=[HttpError(500)], livekit=[SafetyBlocked()])
    assert s.run() == SAFETY_TEXT
    assert s.hits()["local"] == 0
    assert s.chain.breakers["livekit"].open() is False


def test_a_block_after_some_text_appends_the_notice_without_falling_back(scenario):
    s = scenario(gemini=["Part one. ", SafetyBlocked()])
    assert s.run() == "Part one.  " + SAFETY_TEXT
    assert s.hits()["livekit"] == 0


def test_safety_notice_is_localized_when_i18n_has_the_key(monkeypatch):
    s = Scenario(monkeypatch, gemini=[SafetyBlocked()])
    s.chain._safety_message = __import__("llm_chain.chain", fromlist=["x"])._default_safety_message
    reply = "".join(s.chain.stream(Turn(text="q", language="si")))
    assert reply and reply != "G1G2"
    assert s.hits()["livekit"] == 0


# Breaker


def test_breaker_skips_a_failed_tier_then_recovers(scenario):
    s = scenario(gemini=[HttpError(503)])
    assert s.run() == "L1L2"
    assert s.hits()["gemini"] == 1

    s.run()
    s.run()
    assert s.hits()["gemini"] == 1  # skipped while tripped
    status = s.chain.status()["tiers"]["gemini"]
    assert status["state"] == "tripped" and 0 < status["retry_in"] <= 300

    s.now[0] += 301
    s.scripts["gemini"][:] = ["back"]
    assert s.run() == "back"
    assert s.hits()["gemini"] == 2
    assert s.chain.status()["tiers"]["gemini"]["state"] == "ready"
    assert s.chain.last_mode == "cloud (gemini)"


def test_breaker_retrips_when_the_probe_turn_fails_again(scenario):
    s = scenario(gemini=[HttpError(503)])
    s.run()
    s.now[0] += 301
    s.run()
    assert s.hits()["gemini"] == 2
    s.run()
    assert s.hits()["gemini"] == 2


def test_each_tier_has_its_own_breaker(scenario):
    s = scenario(gemini=[HttpError(500)], livekit=[HttpError(500)])
    s.run()
    s.scripts["gemini"][:] = ["ok"]
    s.now[0] += 301
    assert s.run() == "ok"
    assert len({id(b) for b in s.chain.breakers.values()}) == 3


def test_local_tier_ignores_its_breaker(scenario):
    s = scenario(gemini=[HttpError(500)], livekit=[HttpError(500)], local=[ConnectionError()])
    s.run()
    s.scripts["local"][:] = ["local is back"]
    assert s.run() == "local is back"
    assert s.chain.status()["tiers"]["local"]["state"] == "ready"
    assert s.chain.status()["tiers"]["local"]["retry_in"] == 0


def test_breaker_unit():
    now = [0.0]
    breaker = Breaker(300, clock=lambda: now[0])
    assert not breaker.open() and breaker.seconds_left() == 0
    breaker.trip("quota")
    assert breaker.open() and breaker.seconds_left() == 300 and breaker.reason == "quota"
    now[0] = 299
    assert breaker.open()
    now[0] = 300
    assert not breaker.open()
    breaker.trip("auth")
    breaker.reset()
    assert not breaker.open() and breaker.reason is None


def test_breaker_length_comes_from_config(monkeypatch):
    monkeypatch.setattr(config, "LLM_BREAKER_SECONDS", 42)
    assert LlmChain(order=["gemini"]).breakers["gemini"].seconds == 42


# All or nothing, and failure after speech started


def test_a_late_failure_adds_the_apology_and_does_not_restart_elsewhere(scenario):
    s = scenario(gemini=["Hello there. ", HttpError(500)])
    reply = s.run()
    assert reply == "Hello there. " + APOLOGY
    assert reply.endswith(" Sorry, I lost my connection there. Could you ask that again?")
    assert s.hits() == {"gemini": 1, "livekit": 0, "local": 0}


def test_a_late_failure_does_not_trip_the_breaker(scenario):
    s = scenario(gemini=["Hello there. ", HttpError(500)])
    s.run()
    assert s.chain.breakers["gemini"].open() is False
    assert s.chain.status()["tiers"]["gemini"]["last_error"]


def test_text_from_two_tiers_is_never_mixed(scenario):
    s = scenario(gemini=[HttpError(500)], livekit=["L1"])
    assert s.run() == "L1"


# Streaming


def test_streaming_yields_pieces_in_order(scenario):
    s = scenario(gemini=["A", "B", "C"])
    pieces = list(s.chain.respond_stream(Turn(text="q")))
    assert pieces == ["A", "B", "C"]


def test_streaming_falls_through_before_the_first_piece(scenario):
    s = scenario(gemini=[HttpError(429)], livekit=["x", "y"])
    assert list(s.chain.stream(Turn(text="q"))) == ["x", "y"]


def test_respond_returns_the_joined_stripped_text(scenario):
    s = scenario(gemini=["  Hi ", "there.  "])
    assert s.chain.respond(Turn(text="q")) == "Hi there."


def test_closing_the_stream_early_closes_the_tier_stream(scenario):
    s = scenario(gemini=["one", "two", "three"])
    stream = s.chain.stream(Turn(text="q"))
    assert next(stream) == "one"
    stream.close()  # must not raise, and must not start another tier
    assert s.hits()["livekit"] == 0


def test_cloud_tiers_get_the_user_text_and_context(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "k")
    seen = {}

    def factory(prompt):
        seen["prompt"] = prompt
        return FakeAgent(["ok"], calls=seen.setdefault("calls", []))

    chain = LlmChain(order=["gemini"], tiers={"gemini": GeminiTier(factory)})
    list(chain.stream(Turn(text="my question", tool_context="doc text", user_name="Nimal", language="en")))
    message = seen["calls"][0][0]
    assert "my question" in message and "doc text" in message
    assert "Nimal" in seen["prompt"]

    seen.clear()
    list(chain.stream(Turn(text="q", system_prompt="CUSTOM")))
    assert seen["prompt"] == "CUSTOM"


# Status and probing


def test_status_shape(scenario):
    s = scenario(gemini=[HttpError(402)])
    s.run()
    status = s.chain.status()
    assert status["order"] == ["gemini", "livekit", "local"]
    assert status["last_mode"].startswith("livekit inference")
    assert set(status["tiers"]) == {"gemini", "livekit", "local"}
    for info in status["tiers"].values():
        assert {"configured", "state", "last_error", "retry_in"} <= set(info)
        assert info["state"] in {"ready", "tripped", "not_configured"}
        assert isinstance(info["retry_in"], int)
    assert status["tiers"]["gemini"]["state"] == "tripped"
    assert status["tiers"]["gemini"]["retry_in"] > 0


def test_status_before_any_turn(scenario):
    status = scenario().chain.status()
    assert status["last_mode"] == "unknown"
    assert all(info["last_error"] is None and info["retry_in"] == 0 for info in status["tiers"].values())


class ProbeTier(GeminiTier):
    def __init__(self, error=None):
        super().__init__(lambda prompt: FakeAgent(["ok"]))
        self.error = error

    def probe(self):
        if self.error is not None:
            raise self.error


def test_probe_all_trips_a_dead_tier_at_startup(monkeypatch):
    s = Scenario(monkeypatch)
    s.chain.tiers["gemini"] = ProbeTier(TierUnavailable("quota"))
    status = s.chain.probe_all()
    assert status["tiers"]["gemini"]["state"] == "tripped"
    assert s.run() == "L1L2"
    assert s.hits()["gemini"] == 0


def test_probe_all_clears_a_recovered_tier(monkeypatch):
    s = Scenario(monkeypatch, gemini=[HttpError(500)])
    s.run()
    s.chain.tiers["gemini"] = ProbeTier(None)
    status = s.chain.probe_all()
    assert status["tiers"]["gemini"]["state"] == "ready"
    assert status["tiers"]["gemini"]["last_error"] is None


def test_probe_all_with_real_tiers_uses_the_agents_and_never_the_network(monkeypatch):
    s = Scenario(monkeypatch, gemini=[HttpError(401)])
    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    # The local probe would call Ollama; replace its HTTP call.
    class Resp:
        def raise_for_status(self):
            raise HttpError(500)

    monkeypatch.setattr(agent.requests, "get", lambda *a, **k: Resp())
    status = s.chain.probe_all()
    assert status["tiers"]["gemini"]["state"] == "tripped"
    assert status["tiers"]["livekit"]["state"] == "not_configured"
    assert status["tiers"]["local"]["last_error"]
    assert status["tiers"]["local"]["state"] == "ready"  # local never trips


def test_private_turn_never_reaches_a_cloud_tier(monkeypatch):
    """A turn that carries customer data is answered by the local tier only,
    and skipping the cloud tiers is not a failure (no breaker, no error)."""
    s = Scenario(monkeypatch)
    out = "".join(s.chain.stream(Turn(text="What is my balance?", private=True)))
    assert out == "local reply"
    assert s.hits() == {"gemini": 0, "livekit": 0, "local": 1}
    assert s.chain.last_mode == "local single agent (Llama via Ollama)"
    assert not any(b.open() for b in s.chain.breakers.values())
    # a normal turn afterwards still starts with the first tier
    assert s.run() == "G1G2"
