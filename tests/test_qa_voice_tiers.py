"""QA: which voice tier a new session gets, through the real FastAPI app.

GET /api/voice/tier is driven with TestClient. The process wide router is
replaced by one with an injected clock, so the breaker window can be crossed
without sleeping. Reasons are reported the way the server and the Live proxy
report them (router.report with a status code, a close text, an exception or a
reason class). Nothing here touches the network.
"""

import json

import pytest

import config
from conftest import make_client

GEMINI_KEY = "qa-gemini-key-SECRET-7731aa90"
LIVEKIT_KEY = "qa-livekit-key-SECRET-5512"
LIVEKIT_SECRET = "qa-livekit-secret-SECRET-0123456789abcdef"
RAW_BODY = "RAW-PROVIDER-BODY-do-not-leak-4455"


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def tiers(server_module, monkeypatch):
    """The app, a router with a fake clock, config for both cloud tiers."""
    import voice_router
    from voice_router import VoiceRouter

    clock = Clock()
    monkeypatch.setattr(config, "VOICE_ROUTER_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "GEMINI_API_KEY", GEMINI_KEY)
    monkeypatch.setattr(config, "GEMINI_LIVE_MODEL", "gemini-3.8-live")
    monkeypatch.setattr(config, "LIVEKIT_URL", "wss://qa.livekit.invalid")
    monkeypatch.setattr(config, "LIVEKIT_API_KEY", LIVEKIT_KEY)
    monkeypatch.setattr(config, "LIVEKIT_API_SECRET", LIVEKIT_SECRET)
    # LiveKit counts as usable (transport chosen, worker running) unless a test says otherwise.
    monkeypatch.setattr(server_module, "_livekit_usable", lambda: True)
    router = VoiceRouter(breaker_seconds=60, clock=clock)
    voice_router.reset_router()
    monkeypatch.setattr(voice_router, "_router", router)
    client = make_client(server_module.app)

    class Env:
        pass

    env = Env()
    env.client, env.router, env.clock = client, router, clock
    env.server = server_module
    env.monkeypatch = monkeypatch
    env.tier = lambda: client.get("/api/voice/tier").json()["tier"]
    yield env
    client.close()
    voice_router.reset_router()


class _Closed(Exception):
    """Looks like a websockets close with a reason text."""


FAILURES = [
    ("401", 401),
    ("402", 402),
    ("403", 403),
    ("404", 404),
    ("429", 429),
    ("500", 500),
    ("503", 503),
    ("1011_prepayment_depleted", "Your prepayment credits are depleted"),
    ("1011_prepayment_exception", _Closed("received 1011 (internal error) Your prepayment credits are depleted")),
    ("timeout", TimeoutError("connect timed out")),
    ("missing_key_reason", "missing_key"),
]


def test_no_gemini_key_goes_straight_to_livekit(tiers, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    assert tiers.tier() == "livekit"


def test_no_gemini_key_and_no_livekit_is_local(tiers, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    assert tiers.tier() == "local"


def test_key_set_and_router_fresh_is_gemini_live(tiers):
    body = tiers.client.get("/api/voice/tier").json()
    assert body["tier"] == "gemini_live"
    assert body["model"] == "gemini-3.8-live"
    assert body["enabled"] is True


def test_gemini_live_disabled_by_flag_falls_to_livekit(tiers, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_LIVE_ENABLED", False)
    assert tiers.tier() == "livekit"


@pytest.mark.parametrize("label,failure", FAILURES, ids=[f[0] for f in FAILURES])
def test_every_genuine_failure_moves_to_livekit(tiers, label, failure):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, failure)
    assert tiers.tier() == "livekit"


@pytest.mark.parametrize("label,failure", FAILURES, ids=[f[0] for f in FAILURES])
def test_every_genuine_failure_moves_to_local_without_livekit(tiers, monkeypatch, label, failure):
    from voice_router import Tier

    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    tiers.router.report(Tier.GEMINI_LIVE, failure)
    assert tiers.tier() == "local"


def test_both_cloud_tiers_down_is_local(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 402)
    tiers.router.report(Tier.LIVEKIT, 503)
    assert tiers.tier() == "local"


def test_local_never_trips_even_when_reported_failing(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 402)
    tiers.router.report(Tier.LIVEKIT, 503)
    for _ in range(5):
        tiers.router.report(Tier.LOCAL, 500)
    assert tiers.tier() == "local"


@pytest.mark.parametrize(
    "outcome", ["safety_block", "refusal", "user_closed", "dont_know", "normal_close", "barge_in", 1000],
)
def test_valid_outcomes_do_not_change_the_tier(tiers, outcome):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, outcome)
    assert tiers.tier() == "gemini_live"
    assert tiers.router.status()["tiers"]["gemini_live"]["state"] == "ready"


def test_a_safety_close_text_does_not_change_the_tier(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, "response blocked by safety filter")
    tiers.router.report(Tier.GEMINI_LIVE, _Closed("prohibited_content"))
    assert tiers.tier() == "gemini_live"


def test_recovery_upward_after_the_breaker_window(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "livekit"
    tiers.clock.now += 59
    assert tiers.tier() == "livekit"
    tiers.clock.now += 2
    assert tiers.tier() == "gemini_live"


def test_a_second_failure_after_the_window_trips_again(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 500)
    tiers.clock.now += 61
    assert tiers.tier() == "gemini_live"
    tiers.router.report(Tier.GEMINI_LIVE, 500)
    assert tiers.tier() == "livekit"


def test_a_success_closes_the_breaker_at_once(tiers):
    from voice_router import Outcome, Tier

    tiers.router.report(Tier.GEMINI_LIVE, 500)
    assert tiers.tier() == "livekit"
    tiers.router.report(Tier.GEMINI_LIVE, Outcome.success())
    assert tiers.tier() == "gemini_live"


def test_both_down_then_window_ends_goes_back_to_the_top(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 402)
    tiers.router.report(Tier.LIVEKIT, 503)
    assert tiers.tier() == "local"
    tiers.clock.now += 61
    assert tiers.tier() == "gemini_live"
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "livekit"


def test_router_disabled_never_offers_gemini_live(tiers, monkeypatch):
    monkeypatch.setattr(config, "VOICE_ROUTER_ENABLED", False)
    body = tiers.client.get("/api/voice/tier").json()
    assert body == {"enabled": False, "tier": "livekit"}


def test_tier_response_has_no_secrets(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, _Closed(RAW_BODY + " prepayment credits are depleted"))
    text = tiers.client.get("/api/voice/tier").text
    for secret in (GEMINI_KEY, LIVEKIT_KEY, LIVEKIT_SECRET, RAW_BODY):
        assert secret not in text


def _assert_clean(text):
    for secret in (GEMINI_KEY, LIVEKIT_KEY, LIVEKIT_SECRET, RAW_BODY, "Bearer ", "eyJ"):
        assert secret not in text, secret


def test_health_has_a_voice_router_block_without_secrets(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, _Closed(RAW_BODY + " prepayment credits are depleted"))
    response = tiers.client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    block = body["voice_router"]
    assert block["order"] == ["gemini_live", "livekit", "local"]
    assert block["active"] == "livekit"
    assert block["tiers"]["gemini_live"]["state"] == "tripped"
    assert block["tiers"]["gemini_live"]["last_error"] == "billing_or_quota"
    assert block["tiers"]["livekit"]["state"] == "ready"
    assert set(block["tiers"]["gemini_live"]) == {"state", "last_error", "last_ok_at", "retry_in"}
    _assert_clean(response.text)
    _assert_clean(json.dumps(body))


def test_health_shows_not_configured_tiers(tiers, monkeypatch):
    monkeypatch.setattr(config, "GEMINI_API_KEY", "")
    monkeypatch.setattr(config, "LIVEKIT_URL", "")
    block = tiers.client.get("/health").json()["voice_router"]
    assert block["tiers"]["gemini_live"]["state"] == "not_configured"
    assert block["tiers"]["livekit"]["state"] == "not_configured"
    assert block["active"] == "local"


def test_health_after_recovery_is_ready_again(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, 429)
    tiers.clock.now += 61
    block = tiers.client.get("/health").json()["voice_router"]
    assert block["tiers"]["gemini_live"]["state"] == "ready"
    assert block["active"] == "gemini_live"


# LiveKit usability (server._livekit_usable)


def test_livekit_not_in_use_gives_local_and_leaves_its_breaker_alone(tiers):
    from voice_router import Tier

    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", lambda: None)
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    before = tiers.router.status()["tiers"]["livekit"]
    assert tiers.tier() == "local"
    assert tiers.router.status()["tiers"]["livekit"] == before
    assert before["state"] == "ready" and before["last_error"] is None


def test_livekit_configured_but_worker_down_records_an_outage_and_picks_local(tiers):
    from voice_router import Tier

    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", lambda: False)
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "local"
    status = tiers.router.status()["tiers"]["livekit"]
    assert status["state"] == "tripped" and status["last_error"] == "outage"


def test_a_worker_that_is_down_is_skipped_on_the_next_request_without_asking_again(tiers):
    from voice_router import Tier

    calls = []

    def usable():
        calls.append(1)
        return False

    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", usable)
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "local"
    assert tiers.tier() == "local"
    assert len(calls) == 1, "the tripped tier is not offered, so the worker is not asked again"


def test_livekit_recovers_after_the_window_when_the_worker_is_back(tiers):
    from voice_router import Tier

    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", lambda: False)
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "local"
    tiers.clock.now += 61
    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", lambda: True)
    tiers.router.report(Tier.GEMINI_LIVE, 402)
    assert tiers.tier() == "livekit"


def test_usable_true_and_gemini_failure_is_livekit(tiers):
    from voice_router import Tier

    tiers.router.report(Tier.GEMINI_LIVE, "Your prepayment credits are depleted")
    assert tiers.tier() == "livekit"


def test_a_healthy_gemini_never_asks_about_livekit(tiers):
    calls = []
    tiers.monkeypatch.setattr(tiers.server, "_livekit_usable", lambda: calls.append(1) or False)
    assert tiers.tier() == "gemini_live"
    assert calls == []
