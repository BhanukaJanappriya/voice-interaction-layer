"""The voice tier state machine, with an injected clock and no network."""

import json

import pytest

from voice_router.health import health_status
from voice_router.router import VoiceRouter
from voice_router.types import Failure, NotAFailure, Outcome, Tier


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make(configured=None, probes=None, seconds=60, clock=None):
    clock = clock or Clock()
    cfg = {Tier.GEMINI_LIVE: lambda: True, Tier.LIVEKIT: lambda: True}
    cfg.update(configured or {})
    router = VoiceRouter(breaker_seconds=seconds, clock=clock, wall_clock=lambda: 1.7e9, configured=cfg, probes=probes)
    return router, clock


def test_all_up_picks_gemini_live():
    router, _ = make()
    assert router.pick(None) is Tier.GEMINI_LIVE


@pytest.mark.parametrize(
    "failure",
    [
        "missing_key", 401, 403, 402, 429, 404, 500,
        "timeout", TimeoutError("connect"),
        1011,
        Exception("1011 Your prepayment credits are depleted"),
    ],
)
def test_each_genuine_failure_picks_next_tier(failure):
    router, _clock = make()
    router.report(Tier.GEMINI_LIVE, failure)
    assert router.pick(None) is Tier.LIVEKIT


def test_close_1011_with_prepayment_text():
    router, _ = make()

    class Closed(Exception):
        code = 1011

    router.report(Tier.GEMINI_LIVE, Closed("Your prepayment credits are depleted"))
    assert router.pick(None) is Tier.LIVEKIT
    assert router.status()["tiers"]["gemini_live"]["last_error"] == "billing_or_quota"


@pytest.mark.parametrize("neutral", ["safety_block", "refusal", "I don't know", "user_closed", 1000, 1001, "barge_in"])
def test_valid_outcomes_do_not_fall_back(neutral):
    router, _ = make()
    router.report(Tier.GEMINI_LIVE, neutral)
    assert router.pick(None) is Tier.GEMINI_LIVE
    assert router.status()["tiers"]["gemini_live"]["state"] == "ready"


def test_gemini_down_goes_to_livekit_both_down_goes_local():
    router, _ = make()
    router.report(Tier.GEMINI_LIVE, 429)
    assert router.pick(None) is Tier.LIVEKIT
    router.report(Tier.LIVEKIT, 503)
    assert router.pick(None) is Tier.LOCAL


def test_next_tier_only_after_previous_failed():
    router, _ = make()
    # livekit failing does nothing while gemini is healthy
    router.report(Tier.LIVEKIT, 500)
    assert router.pick(None) is Tier.GEMINI_LIVE
    router.report(Tier.GEMINI_LIVE, Outcome.success())
    assert router.pick(None) is Tier.GEMINI_LIVE


def test_unconfigured_tier_is_skipped():
    router, _ = make(configured={Tier.GEMINI_LIVE: lambda: False})
    assert router.pick(None) is Tier.LIVEKIT
    assert router.status()["tiers"]["gemini_live"]["state"] == "not_configured"
    router2, _ = make(configured={Tier.GEMINI_LIVE: lambda: False, Tier.LIVEKIT: lambda: False})
    assert router2.pick(None) is Tier.LOCAL


def test_local_never_trips():
    router, _ = make()
    router.report(Tier.GEMINI_LIVE, 500)
    router.report(Tier.LIVEKIT, 500)
    for _ in range(10):
        router.report(Tier.LOCAL, 500)
    assert router.pick(None) is Tier.LOCAL
    assert router.status()["tiers"]["local"]["state"] == "ready"


def test_upward_recovery_after_window():
    router, clock = make(seconds=60)
    router.report(Tier.GEMINI_LIVE, 429)
    router.report(Tier.LIVEKIT, 500)
    assert router.pick(None) is Tier.LOCAL
    clock.now += 61
    assert router.pick(None) is Tier.GEMINI_LIVE  # both windows ended, highest wins
    router.report(Tier.GEMINI_LIVE, 429)  # still broken: re-trips
    assert router.pick(None) is Tier.LIVEKIT


def test_success_closes_breaker_early():
    router, _ = make()
    router.report(Tier.GEMINI_LIVE, 500)
    router.report(Tier.GEMINI_LIVE, Outcome.success())
    assert router.pick(None) is Tier.GEMINI_LIVE
    assert router.status()["tiers"]["gemini_live"]["last_ok_at"] == 1.7e9


def test_retry_in_counts_down():
    router, clock = make(seconds=60)
    router.report(Tier.GEMINI_LIVE, 500)
    assert router.status()["tiers"]["gemini_live"]["retry_in"] == 60
    clock.now += 20
    assert router.status()["tiers"]["gemini_live"]["retry_in"] == 40


def test_repeated_unknown_failures_trip_after_three_in_five_minutes():
    router, clock = make()
    router.report(Tier.GEMINI_LIVE, "something odd")
    clock.now += 100
    router.report(Tier.GEMINI_LIVE, "something odd")
    assert router.pick(None) is Tier.GEMINI_LIVE
    clock.now += 100
    router.report(Tier.GEMINI_LIVE, "something odd")
    assert router.pick(None) is Tier.LIVEKIT
    assert router.status()["tiers"]["gemini_live"]["last_error"] == "repeated_failure"


def test_unknown_failures_spread_out_do_not_trip():
    router, clock = make()
    for _ in range(5):
        router.report(Tier.GEMINI_LIVE, "something odd")
        clock.now += 200
    assert router.pick(None) is Tier.GEMINI_LIVE


def test_report_accepts_typed_outcomes_and_never_raises():
    router, _ = make()
    router.report(Tier.GEMINI_LIVE, Outcome.neutral(NotAFailure("safety_block")))
    assert router.pick(None) is Tier.GEMINI_LIVE
    router.report(Tier.GEMINI_LIVE, Outcome.failed(Failure("billing_or_quota")))
    assert router.pick(None) is Tier.LIVEKIT
    router.report("not a tier", 500)  # must not raise


def test_probe_all_with_injected_probes():
    def gemini():
        raise Exception("429 RESOURCE_EXHAUSTED")

    calls = []

    def livekit():
        calls.append("livekit")

    router, _ = make(probes={Tier.GEMINI_LIVE: gemini, Tier.LIVEKIT: livekit, Tier.LOCAL: lambda: None})
    result = router.probe_all()
    assert result == {"gemini_live": False, "livekit": True, "local": True}
    assert router.pick(None) is Tier.LIVEKIT
    assert router.status()["tiers"]["gemini_live"]["last_error"] == "billing_or_quota"


def test_probe_skips_unconfigured_without_calling():
    def boom():
        raise AssertionError("must not be called")

    router, _ = make(configured={Tier.GEMINI_LIVE: lambda: False}, probes={Tier.GEMINI_LIVE: boom})
    assert router.probe(Tier.GEMINI_LIVE) is False


def test_probe_success_closes_a_tripped_tier():
    router, clock = make(probes={Tier.GEMINI_LIVE: lambda: None})
    router.report(Tier.GEMINI_LIVE, 500)
    assert router.probe(Tier.GEMINI_LIVE) is True
    assert router.pick(None) is Tier.GEMINI_LIVE


def test_custom_order_keeps_local_last():
    router = VoiceRouter(order=("livekit", "gemini_live"), configured={Tier.GEMINI_LIVE: lambda: True, Tier.LIVEKIT: lambda: True})
    assert router.order == (Tier.LIVEKIT, Tier.GEMINI_LIVE, Tier.LOCAL)
    assert router.pick() is Tier.LIVEKIT


def test_status_has_no_secrets_or_raw_bodies():
    router, _ = make()
    secret = "AIzaSyFAKEKEYFAKEKEYFAKEKEY1234567890abc"
    router.report(Tier.GEMINI_LIVE, Exception(f"401 invalid api key {secret} Bearer sk-live-abcdef https://x/?key={secret}"))
    status = router.status()
    blob = json.dumps(status)
    assert secret not in blob and "Bearer" not in blob and "http" not in blob and "sk-" not in blob
    assert set(status) == {"order", "active", "tiers"}
    assert set(status["tiers"]["gemini_live"]) == {"state", "last_error", "last_ok_at", "retry_in"}
    assert status["tiers"]["gemini_live"]["last_error"] == "invalid_key"
    assert status["active"] == "livekit"
    assert health_status(router)["tiers"]["gemini_live"]["state"] == "tripped"


def test_health_status_never_raises():
    class Broken:
        def status(self):
            raise RuntimeError("boom")

    assert health_status(Broken())["tiers"] == {}
